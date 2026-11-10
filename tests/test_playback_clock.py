import asyncio
from types import SimpleNamespace

import httpx
import pytest

from obelisk_music import widgets
from obelisk_music.app import ObeliskApp
from obelisk_music.models import Playback
from obelisk_music.widgets import NowPlaying, PlainText, PlaybackProgress


def clock(monkeypatch, playback):
    now = [100.0]
    monkeypatch.setattr(widgets, "time", SimpleNamespace(monotonic=lambda: now[0]))
    widget = NowPlaying()
    widget.playback = Playback.model_validate(playback)
    return widget, now


def test_elapsed_time_pause_resume_seek_and_duration_clamp(monkeypatch):
    widget, now = clock(
        monkeypatch,
        {"is_playing": True, "progress_ms": 1000, "item": {"duration_ms": 10000}},
    )
    now[0] += 2
    assert widget.position_ms == 3000
    widget.set_playing(False)
    now[0] += 30
    assert widget.position_ms == 3000
    widget.set_playing(True)
    now[0] += 2
    assert widget.position_ms == 5000
    now[0] += 60
    assert widget.position_ms == 10000
    widget.playback = widget.playback.model_copy(update={"progress_ms": 100})
    widget._observed = now[0]
    assert widget.position_ms == 100


@pytest.mark.parametrize(
    "playback,expected",
    [
        ({}, 0),
        ({"is_playing": True, "progress_ms": 5000}, 0),
        ({"is_playing": True, "item": {"duration_ms": 0}, "progress_ms": 5000}, 0),
        ({"is_playing": True, "item": {"duration_ms": 10000}}, 0),
        (
            {"is_playing": False, "item": {"duration_ms": 10000}, "progress_ms": 1500},
            1500,
        ),
        (
            {"is_playing": True, "item": {"duration_ms": 10000}, "progress_ms": 50000},
            10000,
        ),
    ],
)
def test_unknown_and_paused_progress_stays_safe(monkeypatch, playback, expected):
    widget, now = clock(monkeypatch, playback)
    now[0] += 5
    assert widget.position_ms == expected


def test_clock_tick_updates_widgets_without_spotify_polling(monkeypatch):
    async def run():
        now = [100.0]
        monkeypatch.setattr(widgets, "time", SimpleNamespace(monotonic=lambda: now[0]))
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(204)

        app = ObeliskApp(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        async with app.run_test() as pilot:
            strip = app.query_one(NowPlaying)
            strip.show_playback(
                Playback.model_validate(
                    {
                        "is_playing": True,
                        "progress_ms": 1000,
                        "item": {
                            "name": "[red]Title[/red]",
                            "duration_ms": 10000,
                            "album": {"name": "Album"},
                        },
                        "device": {"name": "Speaker"},
                    }
                )
            )
            now[0] += 3
            await pilot.pause(1.1)
            assert str(strip.query_one("#elapsed", PlainText).render()) == "0:04"
            assert strip.query_one(PlaybackProgress)._fraction == 0.4
            assert not calls
            strip.set_playing(False)
            now[0] += 5
            strip._draw_time()
            assert strip.position_ms == 4000
            assert (
                str(strip.query_one("#track", PlainText).render()) == "[red]Title[/red]"
            )
            assert str(strip.query_one("#device", PlainText).render()) == "Speaker"
            strip.show_playback(Playback())
            assert (
                strip.position_ms == 0
                and strip.query_one(PlaybackProgress)._fraction == 0
            )

    asyncio.run(run())
