"""Opt-in real-mpv audio/analysis check. Plays sound; never stores audio.

Default: original generated tone bursts encoded in memory by external ffmpeg.
Use --url for an explicitly chosen public MP3/AAC stream. No human synchronization
claim follows from this automated check. Run outside pytest's network sandbox.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import subprocess
import time

import httpx
import numpy as np

from obelisk_radio.audio_analysis import SAMPLE_RATE, WINDOW, Analyzer
from obelisk_radio.audio_pipeline import VisualizerBackend, VisualizerState
from obelisk_radio.config import VisualizerSettings
from obelisk_radio.radio_http import radio_http
from obelisk_radio.radio_models import RadioStatus, Station


def generated_audio(seconds: int) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("Generated-audio check requires external ffmpeg, or use --url")
    t = np.arange((seconds + 15) * SAMPLE_RATE) / SAMPLE_RATE
    envelope = np.where(t % 0.5 < 0.08, 0.35, 0.01)
    mono = envelope * np.sin(2 * np.pi * 94 * t)
    mono += 0.05 * np.sin(2 * np.pi * np.where(t % 4 < 2, 1500, 9000) * t)
    pcm = np.repeat((mono * 32767).astype(np.int16)[:, None], 2, axis=1)
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-i",
            "pipe:0",
            "-f",
            "mp3",
            "pipe:1",
        ],
        input=pcm.astype("<i2").tobytes(),
        capture_output=True,
        check=True,
        timeout=30,
    )
    return result.stdout


class GeneratedStream(httpx.AsyncByteStream):
    def __init__(self, data: bytes):
        self.data = data

    async def __aiter__(self):
        for offset in range(0, len(self.data), 4096):
            yield self.data[offset : offset + 4096]
            await asyncio.sleep(0.005)
        await asyncio.Future()


def process_metrics(pids: list[int]) -> list[dict]:
    output = subprocess.check_output(
        ["ps", "-o", "pid=,pcpu=,rss=", "-p", ",".join(map(str, pids))],
        text=True,
    )
    return [
        {"pid": int(pid), "cpu_percent": float(cpu), "rss_kib": int(rss)}
        for pid, cpu, rss in (line.split() for line in output.splitlines())
    ]


async def check(seconds: int, url: str | None) -> None:
    data = await asyncio.to_thread(generated_audio, seconds) if url is None else None
    http = (
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "audio/mpeg"},
                    stream=GeneratedStream(data),
                )
            )
        )
        if data is not None
        else radio_http()
    )
    state = VisualizerState(VisualizerSettings(enabled=True))
    processes = []

    async def spawn(*args, **kwargs):
        process = await asyncio.create_subprocess_exec(*args, **kwargs)
        processes.append(process)
        return process

    backend = VisualizerBackend(http, state, spawn=spawn)

    def changed(snapshot):
        state.playing = snapshot.status == RadioStatus.PLAYING

    backend.on_change = changed
    station = Station.from_custom(
        "Live acceptance" if url else "Original generated pulses",
        url or "https://generated.example/test.mp3",
    )
    playback = asyncio.create_task(backend.play(station))
    frames, gaps, offsets = [], [], []
    metrics = []
    last_time = None
    try:
        deadline = time.monotonic() + 25
        while not state.playing and time.monotonic() < deadline:
            if playback.done():
                raise AssertionError(f"Playback failed: {backend.snapshot.message}")
            await asyncio.sleep(0.05)
        assert state.playing, "System sink did not reach PLAYING"
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            frame = state.current()
            if frame is not None and (
                not frames or frame.timestamp != frames[-1].timestamp
            ):
                now = time.monotonic()
                if last_time is not None:
                    gaps.append(now - last_time)
                last_time = now
                frames.append(frame)
                offsets.append(backend.session.clock - frame.timestamp)
            await asyncio.sleep(0.005)
        metrics = process_metrics(
            [os.getpid(), *(p.pid for p in processes if p.returncode is None)]
        )
        assert len(frames) > seconds * 5, "No sustained real PCM analysis"
        assert max(frame.rms for frame in frames) > 0.001
        assert len({tuple(round(v, 3) for v in frame.spectrum) for frame in frames}) > 5
        assert len({tuple(round(v, 3) for v in frame.waveform) for frame in frames}) > 5
        await backend.set_volume(20)
        assert backend.snapshot.volume == 20
        state.change()
        await asyncio.sleep(0.15)
        assert not state.active and state.current() is None and state.playing
    finally:
        await backend.stop()
        await asyncio.gather(playback, return_exceptions=True)
        await http.aclose()
    assert all(p.returncode is not None for p in processes), "Orphan mpv child"
    assert not state.pipeline and state.current() is None
    analyzer = Analyzer()
    pcm = bytes(WINDOW * 4)
    started = time.perf_counter()
    for i in range(1000):
        analyzer.analyze(pcm, i / 30)
    print(
        json.dumps(
            {
                "source": "explicit live Radio stream"
                if url
                else "original generated tone bursts",
                "status": "PASS",
                "frames": len(frames),
                "onsets": sum(f.onset for f in frames),
                "observed_fps": round(1 / statistics.mean(gaps), 2),
                "frame_gap_p95_ms": round(
                    sorted(gaps)[int(len(gaps) * 0.95)] * 1000, 2
                ),
                "sink_clock_minus_window_end_p95_ms": round(
                    sorted(offsets)[int(len(offsets) * 0.95)] * 1000, 2
                ),
                "analysis_window_ms": round(WINDOW / SAMPLE_RATE * 1000, 2),
                "dsp_mean_ms": round((time.perf_counter() - started), 3),
                "processes": metrics,
                "children_reaped": len(processes),
                "human_audiovisual_confirmation": False,
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=12, choices=range(5, 61))
    parser.add_argument("--url")
    args = parser.parse_args()
    asyncio.run(check(args.seconds, args.url))


if __name__ == "__main__":
    main()
