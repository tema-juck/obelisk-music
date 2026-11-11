"""One supervised mpv process. Only checked audio bytes cross its stdin pipe."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx

from .radio_http import RadioError, RadioNetworkError, open_stream
from .radio_models import RadioPlayback, RadioStatus, Station, plain


class AudioBackend(Protocol):
    snapshot: RadioPlayback
    on_change: Callable[[RadioPlayback], None]

    async def play(self, station: Station) -> None: ...
    async def stop(self) -> None: ...
    async def set_volume(self, volume: int) -> None: ...


def audio_format(first: bytes) -> str:
    if first.startswith(b"ID3"):
        return "mp3"
    if len(first) >= 2 and first[0] == 255:
        if first[1] & 0xF6 == 0xF0:
            return "aac"
        if first[1] & 0xE0 == 0xE0 and first[1] & 0x06 != 0:
            return "mp3"
    raise RadioError(
        "Not a direct MP3/AAC stream; playlists, HTML and HLS are unsupported"
    )


def mpv_arguments(executable: str, socket: Path, codec: str, volume: int) -> list[str]:
    return [
        executable,
        "--no-config",
        "--load-scripts=no",
        "--ytdl=no",
        "--video=no",
        "--audio-display=no",
        "--terminal=no",
        "--input-terminal=no",
        "--access-references=no",
        "--autoload-files=no",
        "--audio-file-auto=no",
        "--sub-auto=no",
        "--demuxer=lavf",
        f"--demuxer-lavf-format={codec}",
        "--demuxer-lavf-o=protocol_whitelist=pipe",
        "--cache=yes",
        "--cache-secs=5",
        "--demuxer-max-bytes=2MiB",
        "--demuxer-max-back-bytes=0",
        f"--volume={volume}",
        f"--input-ipc-server={socket}",
        "--",
        "fd://0",
    ]


def mpv_environment() -> dict[str, str]:
    """A frozen Python's bundled libraries must not leak into the system mpv."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SPOTIFY_")
    }
    if getattr(sys, "frozen", False):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
        bundled = str(getattr(sys, "_MEIPASS", ""))
        if bundled:
            for key in ("PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
                if key in env:
                    env[key] = os.pathsep.join(
                        part
                        for part in env[key].split(os.pathsep)
                        if part and not Path(part).is_relative_to(bundled)
                    )
    return env


class MpvBackend:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        spawn=asyncio.create_subprocess_exec,
        connect=asyncio.open_unix_connection,
        find=shutil.which,
    ):
        self.http, self.spawn, self.connect, self.find = http, spawn, connect, find
        self.snapshot = RadioPlayback()
        self.on_change: Callable[[RadioPlayback], None] = lambda snapshot: None
        self._task: asyncio.Task | None = None
        self._control = None
        self._lock = asyncio.Lock()

    def _update(self, **changes) -> None:
        self.snapshot = self.snapshot.model_copy(update=changes)
        self.on_change(self.snapshot)

    async def play(self, station: Station) -> None:
        async with self._lock:
            await self._stop()
            self._task = asyncio.current_task()
        try:
            executable = self.find("mpv")
            if not executable:
                raise RadioError(
                    "mpv is required for radio; install it from mpv.io/installation, then retry"
                )
            self._update(
                station=station, metadata="", message="", status=RadioStatus.BUFFERING
            )
            for attempt in range(3):
                try:
                    await self._attempt(executable, station)
                except (
                    httpx.HTTPError,
                    RadioNetworkError,
                    OSError,
                    TimeoutError,
                    EOFError,
                ) as exc:
                    if attempt == 2:
                        raise RadioError(
                            "Radio connection lost or mpv failed; check the stream and audio output, then Enter retries"
                        ) from exc
                    self._update(
                        status=RadioStatus.RECONNECTING,
                        message=f"Connection interrupted; retry {attempt + 1}/2",
                    )
                    await asyncio.sleep(2**attempt)
                    self._update(status=RadioStatus.BUFFERING, metadata="", message="")
        except RadioError as exc:
            self._update(station=station, status=RadioStatus.ERROR, message=str(exc))
        except asyncio.CancelledError:
            self._update(
                status=RadioStatus.STOPPED,
                metadata="",
                message="Stopped · Space resumes at the live edge",
            )
            raise
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def _stop(self) -> None:
        task = self._task
        if task is not None and task is not asyncio.current_task():
            if not task.cancelling():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._update(
            status=RadioStatus.STOPPED,
            metadata="",
            message="Stopped · Space resumes at the live edge",
        )

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    async def set_volume(self, volume: int) -> None:
        volume = max(0, min(100, volume))
        if self._control is not None:
            try:
                self._control.write(
                    (
                        json.dumps({"command": ["set_property", "volume", volume]})
                        + "\n"
                    ).encode()
                )
                await asyncio.wait_for(self._control.drain(), 2)
            except (OSError, TimeoutError):
                raise RadioError(
                    "mpv volume control failed; stop and retry playback"
                ) from None
        self._update(volume=volume)

    async def _attempt(self, executable: str, station: Station) -> None:
        async with open_stream(self.http, station.url) as response:
            if (
                response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                raise RadioError(
                    "Compressed HTTP radio responses are unsupported; use a direct audio stream"
                )
            content_type = response.headers.get("content-type", "").lower()
            if any(
                t in content_type
                for t in ("mpegurl", "scpls", "html", "xml", "json", "text/")
            ):
                raise RadioError(
                    "Station returned a playlist or document; use its direct MP3/AAC URL"
                )
            chunks = self._audio_chunks(response)
            first = bytearray()
            while len(first) < 3:
                first.extend(await asyncio.wait_for(anext(chunks), 20))
            codec = audio_format(bytes(first))
            # Short canonical /tmp path avoids macOS's 104-byte Unix socket limit.
            with tempfile.TemporaryDirectory(
                prefix="obelisk-", dir="/tmp"
            ) as temporary:
                socket = Path(temporary) / "mpv.sock"
                process = None
                tasks = []
                control = None
                try:
                    # Shield process creation: cancellation must still acquire and reap the child.
                    creation = asyncio.create_task(
                        self.spawn(
                            *mpv_arguments(
                                executable, socket, codec, self.snapshot.volume
                            ),
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                            env=mpv_environment(),
                        )
                    )
                    try:
                        process = await asyncio.shield(creation)
                    except asyncio.CancelledError:
                        process = await creation
                        raise
                    await asyncio.wait_for(self._connect_ipc(socket, process), 5)
                    reader, control = self._control_pair
                    self._control = control
                    control.write(
                        b'{"command":["observe_property",1,"paused-for-cache"]}\n'
                    )
                    await asyncio.wait_for(control.drain(), 2)
                    await self.set_volume(self.snapshot.volume)
                    tasks = [
                        asyncio.create_task(
                            self._feed(process.stdin, bytes(first), chunks)
                        ),
                        asyncio.create_task(self._events(reader)),
                        asyncio.create_task(process.wait()),
                        asyncio.create_task(self._startup_timeout()),
                    ]
                    done, _ = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in done:
                        task.result()
                    raise EOFError("Radio stream or player ended")
                finally:
                    self._control = None
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if control is not None:
                        control.close()
                    if process is not None:
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
                                process.kill()
                                await asyncio.wait_for(process.wait(), 2)

    async def _connect_ipc(self, socket: Path, process) -> None:
        while process.returncode is None:
            try:
                self._control_pair = await self.connect(str(socket), limit=65_536)
                return
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(0.05)
        raise OSError("mpv exited during startup")

    async def _startup_timeout(self) -> None:
        await asyncio.sleep(20)
        if self.snapshot.status != RadioStatus.PLAYING:
            raise TimeoutError("mpv did not start audio")
        await asyncio.Future()

    async def _events(self, reader) -> None:
        while True:
            try:
                line = await reader.readline()
            except ValueError:
                raise RadioError(
                    "mpv returned oversized IPC metadata; stop and retry"
                ) from None
            if not line:
                raise EOFError("mpv IPC disconnected")
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    continue
                if event.get("event") == "playback-restart":
                    self._update(status=RadioStatus.PLAYING, message="")
                elif (
                    event.get("event") == "property-change"
                    and event.get("name") == "paused-for-cache"
                ):
                    if event.get("data") is True:
                        self._update(status=RadioStatus.BUFFERING)
                elif event.get("event") == "end-file":
                    raise EOFError("mpv stopped decoding")
            except (json.JSONDecodeError, UnicodeError):
                continue

    async def _feed(self, writer, first: bytes, chunks) -> None:
        writer.write(first)
        await asyncio.wait_for(writer.drain(), 20)
        async for chunk in chunks:
            writer.write(chunk)
            await asyncio.wait_for(writer.drain(), 20)

    async def _audio_chunks(self, response):
        """Strip bounded ICY metadata before decoding; never guess a track title."""
        raw = response.headers.get("icy-metaint")
        try:
            interval = int(raw) if raw else 0
            if not 0 <= interval <= 1_048_576:
                raise ValueError
        except ValueError:
            raise RadioError("Station returned invalid metadata framing") from None
        remaining = interval
        metadata_size = None
        buffer = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=4096):
            if not interval:
                yield chunk
                continue
            buffer.extend(chunk)
            while buffer:
                if remaining:
                    count = min(remaining, len(buffer))
                    yield bytes(buffer[:count])
                    del buffer[:count]
                    remaining -= count
                elif metadata_size is None:
                    metadata_size = buffer.pop(0) * 16
                elif len(buffer) >= metadata_size:
                    text = bytes(buffer[:metadata_size]).decode("utf-8", "replace")
                    title = re.search(r"StreamTitle='(.*?)';", text)
                    if title:
                        self._update(metadata=plain(title.group(1)))
                    del buffer[:metadata_size]
                    metadata_size = None
                    remaining = interval
                else:
                    break
        raise EOFError("Radio stream ended")
