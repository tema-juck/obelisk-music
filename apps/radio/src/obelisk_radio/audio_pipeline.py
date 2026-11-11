"""Optional decoded Radio PCM fan-out, with the original mpv engine unchanged."""

from __future__ import annotations

import asyncio
import json
import math
import time

from .audio_analysis import (
    MODES,
    PCM_FORMAT,
    SAMPLE_BYTES,
    SAMPLE_RATE,
    Analyzer,
    AudioFrame,
    PcmRing,
)
from .config import VisualizerSettings
from .radio_audio import MpvBackend, _finish


class VisualizerState:
    def __init__(self, settings: VisualizerSettings):
        self.settings = settings
        self.enabled = settings.enabled
        self.mode = settings.mode
        self.playing = False
        self.frame: AudioFrame | None = None
        self.updated = 0.0
        self.revision = 0
        self.pipeline = False

    @property
    def active(self) -> bool:
        return self.enabled and self.mode != "off"

    def change(self, *, cycle: bool = False) -> None:
        if cycle:
            self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
            self.enabled = self.mode != "off"
        else:
            self.enabled = not self.active
            if self.enabled and self.mode == "off":
                self.mode = "spectrum"
        self.revision += 1
        self.frame = None

    def current(self) -> AudioFrame | None:
        if self.active and self.playing and time.monotonic() - self.updated < 0.3:
            return self.frame
        return None


class AnalysisSession:
    """One ring and one serial worker per playback attempt; no queued DSP jobs."""

    def __init__(self, state: VisualizerState):
        self.state = state
        self.ring = PcmRing()
        self.clock: float | None = None
        self.clock_updated = 0.0
        self.control = None
        self._clock_ready = asyncio.Event()

    def observe(self, line: bytes) -> None:
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            return
        if isinstance(event, dict) and (
            (
                event.get("name") == "audio-pts"
                and event.get("event") == "property-change"
            )
            or (event.get("request_id") == 42 and event.get("error") == "success")
        ):
            value = event.get("data")
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                self.clock = value
                self.clock_updated = time.monotonic()
                self._clock_ready.set()

    async def run(self) -> None:
        analyzer = None
        revision = -1
        previous = -1
        next_frame = time.monotonic()
        while True:
            await asyncio.sleep(max(0, next_frame - time.monotonic()))
            next_frame = max(next_frame + 1 / self.state.settings.fps, time.monotonic())
            if not self.state.active or not self.state.playing:
                analyzer = None
                self.state.frame = None
                continue
            if analyzer is None or revision != self.state.revision:
                analyzer = Analyzer(self.state.settings.sensitivity)
                revision = self.state.revision
                previous = -1
            if self.control is not None:
                self._clock_ready.clear()
                self.control.write(
                    b'{"command":["get_property","audio-pts"],"request_id":42}\n'
                )
                try:
                    await asyncio.wait_for(self.control.drain(), 0.2)
                    await asyncio.wait_for(self._clock_ready.wait(), 0.2)
                except (OSError, TimeoutError):
                    self.state.frame = None
                    continue
            if self.clock is None or time.monotonic() - self.clock_updated > 0.25:
                self.state.frame = None
                continue
            end = int(self.clock * SAMPLE_RATE)
            if end <= previous:
                continue
            pcm = self.ring.window(end)
            if pcm is None:
                self.state.frame = None
                continue
            work = asyncio.create_task(
                asyncio.to_thread(analyzer.analyze, pcm, end / SAMPLE_RATE)
            )
            frame, interrupted = await _finish(work)
            if interrupted:
                raise asyncio.CancelledError
            previous = end
            if (
                revision == self.state.revision
                and self.state.active
                and self.state.playing
            ):
                self.state.frame = frame
                self.state.updated = time.monotonic()


class ClockReader:
    def __init__(self, reader, session: AnalysisSession):
        self.reader, self.session = reader, session

    async def readline(self):
        line = await self.reader.readline()
        self.session.observe(line)
        return line


async def reap(process) -> None:
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 2)
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await asyncio.wait_for(process.wait(), 2)


class PcmProcess:
    """Expose the engine's process contract while supervising both mpv children."""

    def __init__(self, decoder, sink, session: AnalysisSession):
        self.decoder, self.sink, self.session = decoder, sink, session
        self.stdin = decoder.stdin
        self.returncode = None
        self._stopping = asyncio.Event()
        sink.stdin.transport.set_write_buffer_limits(high=8192, low=4096)
        self._task = asyncio.create_task(self._supervise())

    async def _fanout(self) -> None:
        while True:
            try:
                pcm = await self.decoder.stdout.readexactly(1024 * SAMPLE_BYTES)
            except asyncio.IncompleteReadError as exc:
                pcm = exc.partial
                if pcm:
                    self.sink.stdin.write(pcm)
                    await asyncio.wait_for(self.sink.stdin.drain(), 5)
                return
            self.sink.stdin.write(pcm)
            await asyncio.wait_for(self.sink.stdin.drain(), 5)
            # Audio owns backpressure. Analysis only copies a bounded memory block.
            self.session.ring.append(pcm)

    async def _supervise(self) -> int:
        tasks = [
            asyncio.create_task(self._fanout()),
            asyncio.create_task(self.decoder.wait()),
            asyncio.create_task(self.sink.wait()),
            asyncio.create_task(self._stopping.wait()),
        ]
        analysis = asyncio.create_task(self.session.run())
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            return 0
        except (OSError, TimeoutError):
            return 1
        except asyncio.CancelledError:
            return 0
        finally:
            for task in [*tasks, analysis]:
                task.cancel()
            await asyncio.gather(*tasks, analysis, return_exceptions=True)
            await asyncio.gather(reap(self.decoder), reap(self.sink))
            self.session.state.frame = None
            self.session.state.pipeline = False
            self.returncode = 0

    async def wait(self) -> int:
        return await asyncio.shield(self._task)

    def terminate(self) -> None:
        self._stopping.set()

    def kill(self) -> None:
        for process in (self.decoder, self.sink):
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass


class VisualizerBackend(MpvBackend):
    def __init__(
        self,
        http,
        state: VisualizerState,
        *,
        spawn=asyncio.create_subprocess_exec,
        **kwargs,
    ):
        super().__init__(http, spawn=self._spawn_audio, **kwargs)
        self.state = state
        self._spawn = spawn
        self.session: AnalysisSession | None = None

    async def _spawn_audio(self, *args, **kwargs):
        self.state.frame = None
        self.session = None
        if not self.state.active:
            return await self._spawn(*args, **kwargs)
        base = list(args[:-2])
        decoder_args = [
            arg
            for arg in base
            if not arg.startswith(("--volume=", "--input-ipc-server="))
        ]
        decoder_args += [
            "--ao=pcm",
            "--ao-pcm-file=/dev/stdout",
            "--ao-pcm-waveheader=no",
            "--audio-format=s16",
            "--audio-samplerate=48000",
            "--audio-channels=stereo",
            "--volume=100",
            "--",
            "fd://0",
        ]
        sink_args = [
            arg for arg in base if not arg.startswith(("--demuxer", "--cache"))
        ]
        sink_args += [
            "--demuxer=rawaudio",
            f"--demuxer-rawaudio-format={PCM_FORMAT}",
            "--demuxer-rawaudio-rate=48000",
            "--demuxer-rawaudio-channels=stereo",
            "--cache=no",
            "--demuxer-readahead-secs=0",
            "--demuxer-max-bytes=128KiB",
            "--audio-buffer=0.1",
            "--",
            "fd://0",
        ]
        decoder = await self._spawn(
            *decoder_args,
            **{**kwargs, "stdout": asyncio.subprocess.PIPE, "limit": 8192},
        )
        sink = None
        try:
            sink = await self._spawn(*sink_args, **kwargs)
            self.session = AnalysisSession(self.state)
            process = PcmProcess(decoder, sink, self.session)
            self.state.pipeline = True
            return process
        except BaseException:
            await reap(decoder)
            if sink is not None:
                await reap(sink)
            raise

    async def _connect_ipc(self, socket, process) -> None:
        await super()._connect_ipc(socket, process)
        if self.session is not None:
            reader, control = self._control_pair
            self.session.control = control
            self._control_pair = ClockReader(reader, self.session), control
