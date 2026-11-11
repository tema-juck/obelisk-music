import asyncio
import json

import httpx
import pytest

from obelisk_radio.radio_audio import MpvBackend, audio_format
from obelisk_radio.radio_http import RadioError
from obelisk_radio.radio_models import RadioStatus, Station


def station(number=1):
    return Station.from_custom(f"Station {number}", f"https://radio.example/{number}")


class Writer:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        await asyncio.sleep(0)

    def close(self):
        self.closed = True


class Process:
    def __init__(self, *, ignore_term=False):
        self.stdin = Writer()
        self.returncode = None
        self.done = asyncio.Event()
        self.ignore_term = ignore_term
        self.terminated = self.killed = False

    async def wait(self):
        await self.done.wait()
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.ignore_term:
            self.returncode = 0
            self.done.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.done.set()


class Audio(httpx.AsyncByteStream):
    async def __aiter__(self):
        while True:
            yield b"\xff\xfb" + b"\0" * 4094
            await asyncio.sleep(0.01)


class Harness:
    def __init__(self):
        self.processes = []
        self.args = []
        self.readers = []
        self.controls = []
        self.http = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, headers={"content-type": "audio/mpeg"}, stream=Audio()
                )
            )
        )
        self.backend = MpvBackend(
            self.http,
            spawn=self.spawn,
            connect=self.connect,
            find=lambda n: "/usr/bin/mpv",
        )
        self.states = []
        self.backend.on_change = lambda s: self.states.append(s.status)

    async def spawn(self, *args, **kwargs):
        self.args.append(args)
        process = Process()
        self.processes.append(process)
        return process

    async def connect(self, *args, **kwargs):
        reader = asyncio.StreamReader(limit=65536)
        reader.feed_data(b'{"event":"playback-restart"}\n')
        control = Writer()
        self.readers.append(reader)
        self.controls.append(control)
        return reader, control

    async def until_playing(self):
        for _ in range(100):
            await asyncio.sleep(0.01)
            if self.backend.snapshot.status == RadioStatus.PLAYING:
                return
        raise AssertionError("backend did not play")

    async def close(self):
        await self.backend.stop()
        await self.http.aclose()


def test_start_stop_volume_switch_and_cleanup():
    async def run():
        harness = Harness()
        first = asyncio.create_task(harness.backend.play(station()))
        try:
            await harness.until_playing()
            assert RadioStatus.BUFFERING in harness.states
            await harness.backend.set_volume(200)
            assert harness.backend.snapshot.volume == 100
            command = json.loads(harness.controls[0].writes[-1])
            assert command == {"command": ["set_property", "volume", 100]}
            second = asyncio.create_task(harness.backend.play(station(2)))
            for _ in range(100):
                await asyncio.sleep(0.01)
                if (
                    len(harness.processes) == 2
                    and harness.backend.snapshot.status == RadioStatus.PLAYING
                ):
                    break
            assert len(harness.processes) == 2
            assert harness.processes[0].terminated and harness.processes[0].stdin.closed
            assert first.cancelled()
            args = harness.args[1]
            assert args[-1] == "fd://0" and "--access-references=no" in args
            assert not any("https://" in arg for arg in args)
            assert "--no-config" in args and "--load-scripts=no" in args
            harness.readers[1].feed_data(
                b'{"event":"property-change","name":"paused-for-cache","data":true}\n'
            )
            await asyncio.sleep(0.03)
            assert harness.backend.snapshot.status == RadioStatus.BUFFERING
            harness.readers[1].feed_data(b'{"event":"playback-restart"}\n')
            await harness.until_playing()
            await harness.backend.stop()
            assert second.cancelled() and harness.processes[1].terminated
            assert harness.backend.snapshot.status == RadioStatus.STOPPED
            assert all(control.closed for control in harness.controls)
        finally:
            await harness.close()

    asyncio.run(run())


def test_missing_mpv_is_actionable():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: pytest.fail("No request without mpv")
            )
        ) as http:
            backend = MpvBackend(http, find=lambda n: None)
            await backend.play(station())
            assert backend.snapshot.status == RadioStatus.ERROR
            assert (
                "install" in backend.snapshot.message
                and "mpv" in backend.snapshot.message
            )

    asyncio.run(run())


def test_cancellation_during_process_creation_reaps_child():
    async def run():
        harness = Harness()
        spawning, release = asyncio.Event(), asyncio.Event()
        process = Process()

        async def spawn(*args, **kwargs):
            spawning.set()
            await release.wait()
            return process

        harness.backend.spawn = spawn
        task = asyncio.create_task(harness.backend.play(station()))
        await asyncio.wait_for(spawning.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert process.terminated and process.stdin.closed
        assert harness.backend.snapshot.status == RadioStatus.STOPPED
        await harness.close()

    asyncio.run(run())


def test_bounded_reconnect_and_cancel_backoff(monkeypatch):
    async def run():
        harness = Harness()
        attempts = []
        real_sleep = asyncio.sleep

        async def fail(*args):
            attempts.append(1)
            raise httpx.ReadTimeout("offline")

        async def quick_sleep(delay):
            await real_sleep(0)

        harness.backend._attempt = fail
        monkeypatch.setattr(asyncio, "sleep", quick_sleep)
        await harness.backend.play(station())
        assert len(attempts) == 3
        assert harness.states.count(RadioStatus.RECONNECTING) == 2
        assert harness.backend.snapshot.status == RadioStatus.ERROR
        monkeypatch.setattr(asyncio, "sleep", real_sleep)
        attempts.clear()
        task = asyncio.create_task(harness.backend.play(station()))
        for _ in range(20):
            await real_sleep(0.01)
            if harness.backend.snapshot.status == RadioStatus.RECONNECTING:
                break
        await harness.backend.stop()
        assert task.cancelled() and len(attempts) == 1
        await harness.close()

    asyncio.run(run())


def test_invalid_stream_is_not_retried_or_spawned():
    async def run():
        for body, content_type in [
            (b"<html>bad</html>", "text/html"),
            (b"#EXTM3U\nhttp://127.0.0.1", "audio/mpeg"),
            (b"[playlist]", "audio/mpeg"),
        ]:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r, body=body, content_type=content_type: httpx.Response(
                        200, headers={"content-type": content_type}, content=body
                    )
                )
            ) as http:
                backend = MpvBackend(
                    http,
                    find=lambda n: "/usr/bin/mpv",
                    spawn=lambda *a, **kw: pytest.fail("invalid stream spawned mpv"),
                )
                await backend.play(station())
                assert backend.snapshot.status == RadioStatus.ERROR
                assert (
                    "direct" in backend.snapshot.message
                    or "playlist" in backend.snapshot.message
                )

    asyncio.run(run())


def test_icy_metadata_is_stripped_bounded_and_plain():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200))
        ) as http:
            backend = MpvBackend(http)
            title = b"StreamTitle='[red]Actual title[/red]';"
            size = (len(title) + 15) // 16
            body = (
                b"\xff\xfbxx" + bytes([size]) + title.ljust(size * 16, b"\0") + b"abcd"
            )
            response = httpx.Response(200, headers={"icy-metaint": "4"}, content=body)
            audio = bytearray()
            with pytest.raises(EOFError):
                async for chunk in backend._audio_chunks(response):
                    audio.extend(chunk)
            assert bytes(audio) == b"\xff\xfbxxabcd"
            assert backend.snapshot.metadata == "[red]Actual title[/red]"
            with pytest.raises(RadioError, match="framing"):
                await anext(
                    backend._audio_chunks(
                        httpx.Response(
                            200, headers={"icy-metaint": "999999999999"}, content=b"x"
                        )
                    )
                )

    asyncio.run(run())


@pytest.mark.parametrize(
    ("data", "codec"),
    [(b"\xff\xfb", "mp3"), (b"ID3", "mp3"), (b"\xff\xf1", "aac"), (b"\xff\xf9", "aac")],
)
def test_codec_sniffing(data, codec):
    assert audio_format(data) == codec


def test_stubborn_process_is_killed_and_waited():
    async def run():
        harness = Harness()
        task = asyncio.create_task(harness.backend.play(station()))
        await harness.until_playing()
        harness.processes[0].ignore_term = True
        await harness.backend.stop()
        process = harness.processes[0]
        assert process.terminated and process.killed and process.returncode == -9
        assert task.cancelled() and process.stdin.closed
        await harness.close()

    asyncio.run(run())


def test_cancel_while_connecting_ipc_cleans_up():
    async def run():
        harness = Harness()
        connecting = asyncio.Event()

        async def connect(*args, **kwargs):
            connecting.set()
            await asyncio.Future()

        harness.backend.connect = connect
        task = asyncio.create_task(harness.backend.play(station()))
        await asyncio.wait_for(connecting.wait(), 1)
        await harness.backend.stop()
        assert task.cancelled() and harness.processes[0].terminated
        await harness.close()

    asyncio.run(run())


def test_oversized_ipc_is_a_recoverable_error():
    async def run():
        harness = Harness()
        reader = asyncio.StreamReader(limit=8)
        reader.feed_data(b"12345678901234567890\n")
        with pytest.raises(RadioError, match="oversized"):
            await harness.backend._events(reader)
        await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_stream_http_rejection_never_retries_or_spawns(status):
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(status)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            backend = MpvBackend(
                http,
                find=lambda n: "/usr/bin/mpv",
                spawn=lambda *a, **kw: pytest.fail("HTTP error spawned mpv"),
            )
            await backend.play(station())
            assert backend.snapshot.status == RadioStatus.ERROR
            assert f"HTTP {status}" in backend.snapshot.message
            assert len(calls) == 1

    asyncio.run(run())


def test_frozen_external_mpv_environment_keeps_system_libraries(monkeypatch):
    import sys

    from obelisk_radio.radio_audio import mpv_environment

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/private/tmp/obelisk-bundle", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/private/tmp/obelisk-bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    monkeypatch.setenv(
        "DYLD_LIBRARY_PATH", "/private/tmp/obelisk-bundle/lib:/opt/user/lib"
    )
    monkeypatch.setenv("SPOTIFY_ACCESS_TOKEN", "test-only-never-forward")
    env = mpv_environment()
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert env["DYLD_LIBRARY_PATH"] == "/opt/user/lib"
    assert "SPOTIFY_ACCESS_TOKEN" not in env
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    assert "LD_LIBRARY_PATH" not in mpv_environment()


def test_repeated_cancellation_cannot_interrupt_process_reaping():
    async def run():
        harness = Harness()
        task = asyncio.create_task(harness.backend.play(station()))
        await harness.until_playing()
        process = harness.processes[0]
        process.ignore_term = True
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 4)
        assert process.terminated and process.killed and process.stdin.closed
        await harness.close()

    asyncio.run(run())
