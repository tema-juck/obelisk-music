import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from textual import events

from obelisk_music.app import ObeliskApp
from obelisk_music.auth import AuthError
from obelisk_music.models import Playback, Token
from obelisk_music.spotify import SpotifyError
from obelisk_music.widgets import FeedbackBar, PlainText


def make_app(monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(204))
    )
    return ObeliskApp(http=http)


def authorize(app):
    app.session._install(Token(access_token="test-access", expires_in=3600))


def content(app, selector):
    return str(app.query_one(selector, PlainText).render())


async def settle(app):
    async with asyncio.timeout(2):
        while any(not worker.is_finished for worker in app.workers):
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("operation", ["refresh", "command", "login", "restore"])
def test_quit_remains_responsive_and_cancels_slow_operation(monkeypatch, operation):
    async def run():
        app = make_app(monkeypatch)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async with app.run_test(size=(80, 24)):
            authorize(app)
            if operation == "refresh":
                monkeypatch.setattr(app.spotify, "playback", slow)
                key = "r"
            elif operation == "command":
                monkeypatch.setattr(app.spotify, "next", slow)
                key = "n"
            else:
                app.client_id = "test-client"
                monkeypatch.setattr(
                    app.session, "login" if operation == "login" else "restore", slow
                )
                if operation == "restore":
                    app.session._token = None
                key = "l" if operation == "login" else "r"
            app.post_message(events.Key(key, key))
            await asyncio.wait_for(started.wait(), 1)
            app.post_message(events.Key("q", "q"))
            async with asyncio.timeout(1):
                while app.is_running:
                    await asyncio.sleep(0.01)
        assert cancelled.is_set()
        assert app.http.is_closed

    asyncio.run(run())


def test_repeated_refresh_is_coalesced(monkeypatch):
    async def run():
        app = make_app(monkeypatch)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def slow():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return Playback()

        async with app.run_test() as pilot:
            authorize(app)
            monkeypatch.setattr(app.spotify, "playback", slow)
            await pilot.press("r")
            await started.wait()
            await pilot.press("r", "r", "r")
            app._poll()
            assert calls == 1
            release.set()
            await settle(app)
            assert calls == 1

    asyncio.run(run())


def test_old_response_cannot_replace_state_after_command(monkeypatch):
    async def run():
        app = make_app(monkeypatch)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def playback():
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                # A delayed transport can finish even after cancellation was requested.
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return Playback.model_validate({"item": {"name": "OLD"}})
            return Playback.model_validate({"item": {"name": "NEW"}})

        async with app.run_test() as pilot:
            authorize(app)
            monkeypatch.setattr(app.spotify, "playback", playback)
            monkeypatch.setattr(app.spotify, "next", AsyncMock())
            await pilot.press("r")
            await started.wait()
            await pilot.press("n")
            release.set()
            await settle(app)
            assert content(app, "#track") == "NEW"
            assert calls == 2

    asyncio.run(run())


def test_refresh_calls_cannot_run_concurrently(monkeypatch):
    async def run():
        app = make_app(monkeypatch)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def playback():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return Playback()

        async with app.run_test():
            authorize(app)
            monkeypatch.setattr(app.spotify, "playback", playback)
            task = asyncio.create_task(app.refresh_player())
            await started.wait()
            await app.refresh_player()
            assert calls == 1
            release.set()
            await task

    asyncio.run(run())


def test_command_feedback_survives_successful_poll_and_expires(monkeypatch):
    async def run():
        app = make_app(monkeypatch)
        async with app.run_test() as pilot:
            authorize(app)
            monkeypatch.setattr(
                app.spotify,
                "pause",
                AsyncMock(side_effect=SpotifyError("Denied pause", status_code=403)),
            )
            monkeypatch.setattr(
                app.spotify,
                "playback",
                AsyncMock(
                    return_value=Playback.model_validate(
                        {"device": {"name": "Desktop"}, "is_playing": True}
                    )
                ),
            )
            app.is_playing = True
            await pilot.press("space")
            await settle(app)
            assert "Denied pause" in content(app, "#status")
            assert app.connection_state == "CONNECTED"
            await app.refresh_player()
            assert "Denied pause" in content(app, "#status")
            app.query_one(FeedbackBar).clear_message()
            assert content(app, "#status") == FeedbackBar.HINT

    asyncio.run(run())


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (httpx.ReadTimeout("test"), "OFFLINE"),
        (AuthError("unlock keychain"), "LOGIN NEEDED"),
        (SpotifyError("rate limited", status_code=429), "RATE LIMITED"),
    ],
)
def test_poll_failure_updates_connection_and_preserves_command_feedback(
    monkeypatch, error, state
):
    async def run():
        app = make_app(monkeypatch)
        async with app.run_test():
            authorize(app)
            app._connection("CONNECTED")
            app.status("Previous command failed", command=True)
            monkeypatch.setattr(app.spotify, "playback", AsyncMock(side_effect=error))
            await app.refresh_player()
            assert app.connection_state == state
            assert content(app, "#status") == "Previous command failed"

    asyncio.run(run())


@pytest.mark.parametrize("size", [(120, 40), (80, 24), (60, 18), (40, 12)])
def test_layout_and_metadata_remain_visible_and_plain(monkeypatch, size):
    async def run():
        app = make_app(monkeypatch)
        playback = Playback.model_validate(
            {
                "item": {
                    "name": "[red]Title[/red]",
                    "artists": [{"name": "[bold]Artist[/bold]"}],
                    "album": {"name": "[i]Album[/i]"},
                    "duration_ms": 10000,
                },
                "device": {"name": "[red]Desktop[/red]"},
                "progress_ms": 5000,
            }
        )
        async with app.run_test(size=size) as pilot:
            authorize(app)
            monkeypatch.setattr(
                app.spotify, "playback", AsyncMock(return_value=playback)
            )
            await app.refresh_player()
            await pilot.pause()
            assert content(app, "#track") == "[red]Title[/red]"
            assert content(app, "#artist") == "[bold]Artist[/bold]"
            assert content(app, "#album") == "[i]Album[/i]"
            assert content(app, "#device") == "[red]Desktop[/red]"
            assert content(app, "#sidebar-device") == "[red]Desktop[/red]"
            for selector in [
                "#track",
                "#artist",
                "#progress",
                "#elapsed",
                "#duration",
                "#controls",
                "#device",
                "#status",
                "#connection",
            ]:
                widget = app.query_one(selector)
                assert widget.region.width > 0, selector
                assert widget.region.height > 0, selector
                assert widget.region.x >= 0 and widget.region.y >= 0, selector
                assert widget.region.right <= size[0], selector
                assert widget.region.bottom <= size[1], selector
            assert (
                len(content(app, "#progress"))
                == app.query_one("#progress").content_size.width
            )
            assert content(app, "#elapsed") == "0:05"
            assert content(app, "#duration") == "0:10"

    asyncio.run(run())


def test_resize_reflows_progress_and_preserves_metadata(monkeypatch):
    async def run():
        app = make_app(monkeypatch)
        async with app.run_test(size=(120, 40)) as pilot:
            authorize(app)
            monkeypatch.setattr(
                app.spotify,
                "playback",
                AsyncMock(
                    return_value=Playback.model_validate({"item": {"name": "Track"}})
                ),
            )
            await app.refresh_player()
            await pilot.resize_terminal(40, 12)
            await pilot.pause()
            assert content(app, "#track") == "Track"
            assert app.query_one("#sidebar").display is False
            assert len(content(app, "#progress")) == 38
            await pilot.resize_terminal(120, 40)
            await pilot.pause()
            assert app.query_one("#sidebar").display is True
            assert (
                len(content(app, "#progress"))
                == app.query_one("#progress").content_size.width
            )

    asyncio.run(run())


def test_commands_are_not_duplicated_or_cancelled_by_repeat_keys(monkeypatch):
    async def run():
        app = make_app(monkeypatch)
        started = asyncio.Event()
        release = asyncio.Event()

        async def next_track():
            started.set()
            await release.wait()

        command = AsyncMock(side_effect=next_track)
        async with app.run_test() as pilot:
            authorize(app)
            monkeypatch.setattr(app.spotify, "next", command)
            await pilot.press("n")
            await started.wait()
            await pilot.press("n", "n", "r")
            app._poll()
            assert command.await_count == 1
            release.set()
            await settle(app)

    asyncio.run(run())


def test_login_cancels_startup_restore_and_blocks_polling(monkeypatch):
    async def run():
        app = make_app(monkeypatch)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def restore():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def login(uri):
            authorize(app)

        async with app.run_test() as pilot:
            app.client_id = "test-client"
            monkeypatch.setattr(app.session, "restore", restore)
            monkeypatch.setattr(app.session, "login", login)
            app._start_session(interactive=False)
            await started.wait()
            app._poll()
            assert app._refresh_worker is None
            await pilot.press("l")
            await settle(app)
            assert cancelled.is_set()
            assert app.session.authorized

    asyncio.run(run())


def test_headless_http_401_recovery_and_shared_command_cooldown(monkeypatch):
    from unittest.mock import Mock

    from obelisk_music import auth

    async def run():
        monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
        monkeypatch.setattr(
            auth.keyring, "get_password", Mock(return_value="test-refresh")
        )
        calls = []

        def handler(request):
            calls.append(request)
            if request.url == auth.TOKEN_URL:
                return httpx.Response(
                    200, json={"access_token": "new-test-access", "expires_in": 3600}
                )
            if request.headers["Authorization"] == "Bearer test-access":
                return httpx.Response(401)
            if request.method == "PUT":
                return httpx.Response(429, headers={"Retry-After": "60"})
            return httpx.Response(
                200,
                json={
                    "is_playing": True,
                    "device": {"name": "Desktop"},
                    "item": {"name": "Recovered track"},
                },
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        app = ObeliskApp(http=http)
        async with app.run_test() as pilot:
            authorize(app)
            await pilot.press("r")
            await settle(app)
            assert content(app, "#track") == "Recovered track"
            assert app.connection_state == "CONNECTED"
            assert len(calls) == 3
            await pilot.press("space")
            await settle(app)
            assert app.connection_state == "RATE LIMITED"
            assert "60s" in content(app, "#status")
            assert len(calls) == 4
            await pilot.press("r")
            await settle(app)
            assert len(calls) == 4
            app.spotify._retry_at = 0
            await pilot.press("r")
            await settle(app)
            assert len(calls) == 5
            assert app.connection_state == "CONNECTED"
            assert "60s" in content(app, "#status")
        assert http.is_closed

    asyncio.run(run())
