import asyncio
import json
import time

import httpx
import pytest
from test_audio_analysis import tone
from test_radio_audio import Harness, Process, station

from obelisk_radio.audio_analysis import WINDOW
from obelisk_radio.audio_pipeline import (
    AnalysisSession,
    PcmProcess,
    VisualizerBackend,
    VisualizerState,
)
from obelisk_radio.config import VisualizerSettings
from obelisk_radio.radio_models import RadioStatus


def state():
    result = VisualizerState(VisualizerSettings(enabled=True))
    result.playing = True
    return result


def test_analysis_clock_not_decoder_progress_drives_frames_and_missing_samples_expire():
    async def run():
        view = state()
        session = AnalysisSession(view)
        session.ring.append(tone(1500) * 20)
        task = asyncio.create_task(session.run())
        try:
            await asyncio.sleep(0.05)
            assert view.current() is None
            session.observe(
                json.dumps(
                    {
                        "event": "property-change",
                        "name": "audio-pts",
                        "data": WINDOW / 48000,
                    }
                ).encode()
            )
            await asyncio.sleep(0.05)
            assert view.current().rms > 0.3
            assert view.current().timestamp == pytest.approx(WINDOW / 48000)
            await asyncio.sleep(0.3)
            assert view.current() is None
            for bad in (
                b"bad",
                b"[]",
                b'{"event":"property-change","name":"audio-pts","data":NaN}',
                b'{"event":"property-change","name":"audio-pts","data":true}',
            ):
                session.observe(bad)
            assert session.clock == WINDOW / 48000
            session.clock = 100
            session.clock_updated = time.monotonic()
            await asyncio.sleep(0.05)
            assert view.current() is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_disabled_and_stopped_do_not_analyze_and_resume_resets(monkeypatch):
    async def run():
        from obelisk_radio.audio_analysis import Analyzer

        calls = []
        analyze = Analyzer.analyze

        def record(self, *args):
            calls.append(args[1])
            return analyze(self, *args)

        monkeypatch.setattr(Analyzer, "analyze", record)
        view = state()
        view.enabled = False
        session = AnalysisSession(view)
        session.ring.append(tone(1500) * 20)
        session.clock = 0.1
        session.clock_updated = time.monotonic()
        task = asyncio.create_task(session.run())
        try:
            await asyncio.sleep(0.05)
            assert calls == []
            view.change()
            await asyncio.sleep(0.05)
            assert len(calls) == 1 and view.current() is not None
            view.playing = False
            await asyncio.sleep(0.05)
            assert len(calls) == 1 and view.current() is None
            view.playing = True
            session.clock = 0.15
            session.clock_updated = time.monotonic()
            await asyncio.sleep(0.05)
            assert len(calls) == 2
            view.change(cycle=True)
            assert view.mode == "beat-pulse" and view.current() is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_mode_cycle_toggle_and_expired_frames():
    view = state()
    for expected in ("beat-pulse", "waveform", "off", "spectrum"):
        view.change(cycle=True)
        assert view.mode == expected
        assert view.active == (expected != "off")
    view.change()
    assert not view.active
    view.change()
    assert view.active and view.mode == "spectrum"
    view.frame = object()
    view.updated = time.monotonic() - 1
    assert view.current() is None


class Transport:
    def set_write_buffer_limits(self, **kwargs):
        self.limits = kwargs


def children():
    decoder, sink = Process(), Process()
    decoder.stdout = asyncio.StreamReader(limit=8192)
    sink.stdin.transport = Transport()
    return decoder, sink


def test_fanout_audio_bytes_equal_analysis_and_immediate_cleanup():
    async def run():
        decoder, sink = children()
        session = AnalysisSession(state())
        process = PcmProcess(decoder, sink, session)
        audio = tone(1500)
        decoder.stdout.feed_data(audio)
        await asyncio.sleep(0.02)
        assert b"".join(sink.stdin.writes) == audio
        assert bytes(session.ring.data) == audio
        process.terminate()
        await asyncio.wait_for(process.wait(), 1)
        assert decoder.terminated and sink.terminated
        assert decoder.stdin.closed and sink.stdin.closed
        assert process.returncode == 0
        decoder, sink = children()
        process = PcmProcess(decoder, sink, AnalysisSession(state()))
        process.terminate()
        await asyncio.wait_for(process.wait(), 1)
        assert decoder.terminated and sink.terminated

    asyncio.run(run())


@pytest.mark.parametrize("failed", ["decoder", "sink", "pipe"])
def test_either_child_exit_or_pipe_eof_reaps_both(failed):
    async def run():
        decoder, sink = children()
        process = PcmProcess(decoder, sink, AnalysisSession(state()))
        if failed == "pipe":
            decoder.stdout.feed_eof()
        else:
            child = decoder if failed == "decoder" else sink
            child.returncode = 2
            child.done.set()
        await asyncio.wait_for(process.wait(), 1)
        assert decoder.returncode is not None and sink.returncode is not None
        assert decoder.stdin.closed and sink.stdin.closed

    asyncio.run(run())


def test_stalled_sink_is_bounded_and_cancellation_reaps_children():
    async def run():
        decoder, sink = children()

        async def stalled():
            await asyncio.Future()

        sink.stdin.drain = stalled
        session = AnalysisSession(state())
        process = PcmProcess(decoder, sink, session)
        decoder.stdout.feed_data(bytes(4096))
        await asyncio.sleep(0.02)
        assert len(sink.stdin.writes) == 1
        assert session.ring.end == 0
        process.terminate()
        await asyncio.wait_for(process.wait(), 1)
        assert sink.terminated and decoder.terminated

    asyncio.run(run())


def test_stubborn_decoder_and_sink_are_both_killed_and_reaped():
    async def run():
        decoder, sink = children()
        decoder.ignore_term = sink.ignore_term = True
        process = PcmProcess(decoder, sink, AnalysisSession(state()))
        process.terminate()
        await asyncio.wait_for(process.wait(), 3)
        assert decoder.killed and sink.killed
        assert decoder.returncode == sink.returncode == -9

    asyncio.run(run())


def test_cancel_waits_for_single_inflight_dsp_job_and_discards_result(monkeypatch):
    import threading

    from obelisk_radio.audio_analysis import Analyzer

    async def run():
        entered, release = threading.Event(), threading.Event()
        analyze = Analyzer.analyze
        calls = []

        def slow(self, *args):
            calls.append(1)
            entered.set()
            assert release.wait(2)
            return analyze(self, *args)

        monkeypatch.setattr(Analyzer, "analyze", slow)
        view = state()
        session = AnalysisSession(view)
        session.ring.append(tone(1500) * 4)
        session.clock = 0.1
        session.clock_updated = time.monotonic()
        task = asyncio.create_task(session.run())
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done() and calls == [1]
        finally:
            release.set()
            task.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
        assert view.frame is None

    asyncio.run(run())


def test_backend_preserves_original_when_disabled_and_splits_when_enabled():
    async def run():
        harness = Harness()
        view = state()
        spawned = []

        async def spawn(*args, **kwargs):
            decoder, sink = children()
            child = decoder if len(spawned) % 2 == 0 else sink
            spawned.append((args, kwargs, child))
            return child

        backend = VisualizerBackend(
            harness.http,
            view,
            spawn=spawn,
            connect=harness.connect,
            find=lambda n: "/usr/bin/mpv",
        )
        first = asyncio.create_task(backend.play(station()))
        try:
            for _ in range(100):
                if backend.snapshot.status == RadioStatus.PLAYING:
                    break
                await asyncio.sleep(0.01)
            assert len(spawned) == 2 and view.pipeline
            assert "--ao=pcm" in spawned[0][0] and "--volume=100" in spawned[0][0]
            assert not any(
                arg.startswith("--input-ipc-server=") for arg in spawned[0][0]
            )
            assert "--demuxer=rawaudio" in spawned[1][0]
            assert "--ao=pcm" not in spawned[1][0]
            await backend.set_volume(25)
            assert json.loads(harness.controls[0].writes[-1])["command"][-1] == 25
            session = backend.session
            second = asyncio.create_task(backend.play(station(2)))
            for _ in range(100):
                if len(spawned) == 4:
                    break
                await asyncio.sleep(0.01)
            assert len(spawned) == 4 and first.cancelled()
            assert backend.session is not session and backend.session.ring.end == 0
            await backend.stop()
            assert second.cancelled()
            assert all(
                p.stdin.closed and p.returncode is not None for _, _, p in spawned
            )
            view.enabled = False
            backend.spawn = backend._spawn_audio
            process = await backend.spawn(
                "mpv", "--", "fd://0", stdin=asyncio.subprocess.PIPE
            )
            assert spawned[-1][0] == ("mpv", "--", "fd://0")
            process.terminate()
        finally:
            await backend.stop()
            await harness.close()

    asyncio.run(run())


def test_sink_spawn_failure_and_repeated_creation_cancellation_reap_decoder():
    async def run():
        async with httpx.AsyncClient() as http:
            decoder, _ = children()
            calls = []

            async def fail(*args, **kwargs):
                calls.append(args)
                if len(calls) == 1:
                    return decoder
                raise OSError("no audio output")

            backend = VisualizerBackend(http, state(), spawn=fail)
            with pytest.raises(OSError):
                await backend._spawn_audio(
                    "mpv", "--", "fd://0", stdin=asyncio.subprocess.PIPE
                )
            assert decoder.terminated and decoder.stdin.closed

        harness = Harness()
        decoder, sink = children()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def slow(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                return decoder
            entered.set()
            await release.wait()
            return sink

        backend = VisualizerBackend(
            harness.http,
            state(),
            spawn=slow,
            connect=harness.connect,
            find=lambda n: "/usr/bin/mpv",
        )
        task = asyncio.create_task(backend.play(station()))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
        assert decoder.terminated and sink.terminated
        await harness.close()

    asyncio.run(run())
