"""Own radio I/O and worker lifetimes without coupling audio to Spotify."""

from __future__ import annotations

import asyncio

from .radio_audio import AudioBackend, MpvBackend
from .radio_directory import RadioDirectory
from .radio_http import RadioError, radio_http
from .radio_models import RadioState, RadioStatus, Station
from .radio_store import RadioStore
from .radio_ui import RadioScreen


class RadioController:
    def __init__(
        self, app, *, backend: AudioBackend | None = None, directory=None, store=None
    ):
        self.app = app
        self.http = (
            None if backend is not None and directory is not None else radio_http()
        )
        self.backend = backend if backend is not None else MpvBackend(self.http)
        self.directory = (
            directory if directory is not None else RadioDirectory(self.http)
        )
        self.store = store
        self.state = RadioState()
        self.loaded = False
        self.overlay: RadioScreen | None = None
        self.reader = None
        self.audio = None
        self.next_offset: int | None = None
        self._actions = asyncio.Lock()
        self._storage = asyncio.Lock()
        self.backend.on_change = self._playback

    def open(self) -> None:
        self.overlay = RadioScreen(vim_keys=self.app.settings.interface.vim_keys)
        self.app.push_screen(self.overlay)

    def _playback(self, snapshot) -> None:
        if (
            self.overlay
            and self.overlay.is_mounted
            and len(self.overlay.query("#radio-state"))
        ):
            self.overlay.show_playback(snapshot)

    def invalidate(self) -> None:
        if self.reader is not None:
            self.reader.cancel()
        if self.overlay:
            self.overlay.loading = False

    def intent(self, event: RadioScreen.Intent) -> None:
        if event.overlay is not self.overlay or self.app.source != "radio":
            return
        screen = event.overlay
        if event.action == "invalidate":
            self.invalidate()
        elif event.action in ("load", "more"):
            if event.action == "more" and screen.loading:
                return
            self.invalidate()
            screen.loading = True
            screen.message("Loading stations…")
            self.reader = self.app.run_worker(
                self._load(screen, screen.revision, event.action == "more"),
                group="radio-read",
            )
        else:
            self.app.run_worker(
                self._action(event.action, event.station, event.overlay),
                group="radio-command",
            )

    async def _ensure_state(self) -> None:
        async with self._storage:
            if self.loaded:
                return
            try:
                if self.store is None:
                    self.store = RadioStore()
                self.state = await asyncio.to_thread(self.store.load)
            except RadioError as exc:
                if self.overlay:
                    self.overlay.message(str(exc))
                self.store = None
                self.storage_error = str(exc)
            else:
                self.storage_error = ""
            self.loaded = True
            await self.backend.set_volume(self.state.volume)

    async def _load(self, screen: RadioScreen, revision: int, more: bool) -> None:
        def current():
            return (
                screen.is_mounted
                and screen is self.overlay
                and screen.revision == revision
                and self.app.source == "radio"
            )

        try:
            await self._ensure_state()
            if not current():
                return
            screen.favorites = {s.id for s in self.state.favorites}
            screen.last_id = self.state.last.id if self.state.last else None
            screen.show_playback(self.backend.snapshot)
            if screen.view in ("favorites", "custom"):
                entries = list(getattr(self.state, screen.view))
                self.next_offset = None
                message = (
                    f"{len(entries)} saved stations · f toggles favorite · a adds custom"
                    if entries
                    else "No saved stations · f saves a result · a adds a custom URL"
                )
            else:
                if more and self.next_offset is None:
                    return
                page = await self.directory.search(
                    **screen.filters(), offset=self.next_offset if more else 0
                )
                if not current():
                    return
                entries = screen.entries + page.stations if more else page.stations
                # A directory may repeat records across changing offset pages.
                entries = list({s.id: s for s in entries}.values())
                self.next_offset = page.next_offset
                message = (
                    f"{len(entries)} stations · {page.excluded} unsupported/unavailable excluded · Enter play"
                    if entries
                    else "No supported stations on this page · Load more or change filters"
                )
            if current():
                screen.show_stations(
                    entries, self.next_offset is not None, reset=not more
                )
                screen.message(self.storage_error or message)
        except RadioError as exc:
            if current():
                screen.message(str(exc))
        finally:
            if current():
                screen.loading = False

    async def _save(self, state: RadioState) -> bool:
        try:
            if self.store is None:
                raise RadioError(
                    self.storage_error
                    or "Radio data is unavailable; restart after repairing it"
                )
            async with self._storage:
                # Shield the filesystem write so shutdown waits for its completion.
                write = asyncio.create_task(asyncio.to_thread(self.store.save, state))
                try:
                    await asyncio.shield(write)
                except asyncio.CancelledError:
                    await write
                    raise
                self.state = state
            return True
        except RadioError as exc:
            if self.overlay:
                self.overlay.message(str(exc))
            return False

    async def _action(
        self, action: str, station: Station | None, origin: RadioScreen
    ) -> None:
        async with self._actions:
            if self.app.source != "radio" or origin is not self.overlay:
                return
            screen = self.overlay
            try:
                await self._ensure_state()
                if (
                    action in ("back", "quit")
                    and (selected := screen.selected())
                    and selected != self.state.last
                ):
                    await self._save(self.state.model_copy(update={"last": selected}))
                if action == "quit":
                    await self.backend.stop()
                    self.app.exit()
                elif action == "back":
                    self.invalidate()
                    await self.backend.stop()
                    self.overlay = None
                    self.app.source = "spotify"
                    if screen is self.app.screen:
                        self.app.pop_screen()
                    self.app._start_refresh()
                elif action in ("stop", "toggle"):
                    if action == "stop" or self.backend.snapshot.status in (
                        RadioStatus.PLAYING,
                        RadioStatus.BUFFERING,
                        RadioStatus.RECONNECTING,
                    ):
                        await self.backend.stop()
                    elif self.backend.snapshot.station or self.state.last:
                        await self._play(
                            self.backend.snapshot.station or self.state.last
                        )
                    else:
                        screen.message("Choose a station and press Enter")
                elif action == "play" and station:
                    await self._play(station)
                elif action in ("louder", "quieter"):
                    volume = max(
                        0,
                        min(
                            100,
                            self.backend.snapshot.volume
                            + (5 if action == "louder" else -5),
                        ),
                    )
                    await self.backend.set_volume(volume)
                    await self._save(self.state.model_copy(update={"volume": volume}))
                elif action in ("favorite", "add", "remove") and station:
                    data = self.state.model_dump()
                    key = "favorites" if action == "favorite" else "custom"
                    entries = list(getattr(self.state, key))
                    found = any(s.id == station.id for s in entries)
                    if action == "remove" or (action == "favorite" and found):
                        entries = [s for s in entries if s.id != station.id]
                    elif not found:
                        if len(entries) >= 100:
                            raise RadioError(
                                "At most 100 stations per saved list; remove one first"
                            )
                        entries.append(station)
                    data[key] = entries
                    if action == "remove":
                        data["favorites"] = [
                            s for s in self.state.favorites if s.id != station.id
                        ]
                        if self.state.last and self.state.last.id == station.id:
                            data["last"] = None
                    if await self._save(RadioState.model_validate(data)):
                        screen.favorites = {s.id for s in self.state.favorites}
                        if action == "add":
                            screen.view = "custom"
                        if screen.view in ("favorites", "custom"):
                            screen.action_refresh()
                        else:
                            screen.show_stations(screen.entries, screen.more)
                        screen.message(
                            "Removed · a adds it again"
                            if action == "remove"
                            else "Saved radio preferences"
                        )
            except RadioError as exc:
                screen.message(str(exc))

    async def _play(self, station: Station) -> None:
        if self.app.is_playing:
            raise RadioError(
                "Pause Spotify before starting radio: Esc, then Space; R returns here"
            )
        await self.backend.stop()
        await self._save(self.state.model_copy(update={"last": station}))
        self.audio = self.app.run_worker(
            self.backend.play(station), group="radio-audio"
        )

    async def close(self) -> None:
        self.invalidate()
        self.overlay = None
        self.backend.on_change = lambda snapshot: None
        await self.backend.stop()
        if self.http is not None:
            await self.http.aclose()
