"""Radio contracts use mock HTTP, fake DNS and private temporary state only."""

import asyncio
import json
import os
import socket

import httpx
import pytest
from pydantic import ValidationError

from obelisk_music.config import ConfigError
from obelisk_music.radio_directory import PAGE_SIZE, RadioDirectory
from obelisk_music.radio_http import (
    PublicTransport,
    RadioError,
    open_stream,
    resolve_public,
    validate_url,
)
from obelisk_music.radio_models import RadioState, Station
from obelisk_music.radio_store import RadioStore, radio_state_path


def station(number=1):
    return Station.from_custom(f"Station {number}", f"https://radio.example/{number}")


def record(number=1, **changes):
    return {
        "stationuuid": str(number),
        "name": f"[red]Radio {number}[/red]",
        "url": f"https://radio.example/{number}",
        "codec": "MP3",
        "bitrate": 128,
        "hls": 0,
        "lastcheckok": 1,
        **changes,
    }


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "/tmp/audio.mp3",
        "ftp://example.com/a",
        "http://127.0.0.1/a",
        "http://[::1]/a",
        "http://192.168.1.1",
        "http://169.254.169.254",
        "http://localhost/a",
        "http://host.local/a",
        "https://user:password@example.com",
        "https://example.com/#x",
        "https://example.com/\nhello",
        "http://[::ffff:8.8.8.8]",
        "https://example.com:0/a",
        "http://[fe80::1%25en0]/a",
    ],
)
def test_reject_unsafe_urls(url):
    with pytest.raises(RadioError):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://radio.example/live",
        "http://8.8.8.8:8000/live",
        "https://[2606:4700:4700::1111]/stream",
    ],
)
def test_public_urls(url):
    assert validate_url(url) == url


def test_station_parsing_and_plain_metadata():
    parsed = Station.from_directory(
        record(
            name="Rock\x1b\n[red]FM[/red]",
            url_resolved="https://radio.example/resolved",
        )
    )
    assert parsed.url.endswith("resolved")
    assert "\x1b" not in parsed.name and "[red]" in parsed.name
    assert "https://" not in repr(parsed)


@pytest.mark.parametrize(
    "changes",
    [
        {"hls": 1},
        {"lastcheckok": 0},
        {"codec": "OGG"},
        {"url": "file:///x"},
        {"name": ""},
        {"bitrate": -1},
    ],
)
def test_reject_unsupported_directory_entries(changes):
    with pytest.raises((ValueError, ValidationError)):
        Station.from_directory(record(**changes))


def test_dns_pinning_preserves_tls_host_and_validates_all_answers(monkeypatch):
    async def run():
        async def resolver(host, port):
            assert (host, port) == ("radio.example", 443)
            return "8.8.8.8"

        seen = []

        def handler(request):
            seen.append(request)
            assert request.url.host == "8.8.8.8"
            assert request.headers["host"] == "radio.example"
            assert request.extensions["sni_hostname"] == "radio.example"
            return httpx.Response(200, content=b"ok")

        async with httpx.AsyncClient(
            transport=PublicTransport(
                transport=httpx.MockTransport(handler), resolver=resolver
            )
        ) as http:
            assert (await http.get("https://radio.example/audio")).text == "ok"
        assert len(seen) == 1
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **kw: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
                for ip in ("8.8.8.8", "127.0.0.1")
            ],
        )
        with pytest.raises(RadioError, match="private"):
            await resolve_public("radio.example", 443)

    asyncio.run(run())


def test_redirects_are_revalidated_and_bounded():
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(RadioError, match="public"):
                async with open_stream(http, "https://radio.example/audio"):
                    pytest.fail("unsafe response yielded")
        assert len(calls) == 1
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(302, headers={"location": "/loop"})
            )
        ) as http:
            with pytest.raises(RadioError, match="too many"):
                async with open_stream(http, "https://radio.example/audio"):
                    pass

    asyncio.run(run())


def test_directory_filters_pagination_and_exclusions():
    async def run():
        offsets = []

        def handler(request):
            offsets.append(int(request.url.params["offset"]))
            assert request.url.params["countrycode"] == "DE"
            assert request.url.params["language"] == "english"
            assert request.url.params["tag"] == "jazz"
            assert request.url.params["name"] == "FM"
            data = [record(i) for i in range(PAGE_SIZE)] if len(offsets) == 1 else []
            if data:
                data[1] = record(1, codec="OGG")
            return httpx.Response(200, json=data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            directory = RadioDirectory(http)
            page = await directory.search(
                name="FM", tag="jazz", country="de", language="english"
            )
            assert len(page.stations) == PAGE_SIZE - 1 and page.excluded == 1
            assert page.next_offset == PAGE_SIZE
            page = await directory.search(
                name="FM",
                tag="jazz",
                country="de",
                language="english",
                offset=page.next_offset,
            )
            assert page.next_offset is None and page.stations == []
        assert offsets == [0, PAGE_SIZE]

    asyncio.run(run())


def test_directory_failover_cooldown_and_invalid_pages():
    async def run():
        calls = []

        def handler(request):
            calls.append(request.url.host)
            return httpx.Response(503 if len(calls) == 1 else 200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            assert not (await RadioDirectory(http).search()).stations
        assert len(set(calls)) == 2
        calls.clear()

        def limited(request):
            calls.append(request)
            return httpx.Response(429, headers={"Retry-After": "60"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(limited)) as http:
            directory = RadioDirectory(http)
            with pytest.raises(RadioError, match="rate limited"):
                await directory.search()
            with pytest.raises(RadioError, match="cooling down"):
                await directory.search(name="other")
        assert len(calls) == 1
        for body in ({"bad": True}, [record()] * (PAGE_SIZE + 1)):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r, body=body: httpx.Response(200, json=body)
                )
            ) as http:
                with pytest.raises(RadioError, match="invalid page"):
                    await RadioDirectory(http).search()

    asyncio.run(run())


def test_directory_network_failure_and_invalid_country():
    async def run():
        def handler(request):
            raise httpx.ConnectError("offline")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            directory = RadioDirectory(http)
            with pytest.raises(RadioError, match="mirrors"):
                await directory.search()
            with pytest.raises(RadioError, match="two-letter"):
                await directory.search(country="Germany")

    asyncio.run(run())


def test_store_discovery_atomic_permissions_and_reload(monkeypatch, tmp_path):
    path = radio_state_path()
    assert not path.parent.exists()
    store = RadioStore(path)
    assert store.load() == RadioState()
    assert not path.parent.exists()
    state = RadioState(
        favorites=[station()], custom=[station(2)], last=station(), volume=40
    )
    store.save(state)
    assert RadioStore(path).load() == state
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert list(path.parent.iterdir()) == [path]
    assert not any(
        word in path.read_text()
        for word in ("access_token", "refresh_token", "client_id")
    )
    monkeypatch.setenv("XDG_DATA_HOME", "../bad")
    with pytest.raises(RadioError):
        radio_state_path()


def test_corrupt_store_does_not_overwrite(tmp_path):
    path = tmp_path / "radio.json"
    for content in (b"{broken", b"", b'{"version":2}', b'{"volume":200}'):
        path.write_bytes(content)
        store = RadioStore(path)
        with pytest.raises(RadioError, match="repair"):
            store.load()
        with pytest.raises(RadioError, match="read-only"):
            store.save(RadioState())
        assert path.read_bytes() == content


def test_store_rejects_symlinks_and_write_failures(monkeypatch, tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    path = tmp_path / "radio.json"
    path.symlink_to(victim)
    store = RadioStore(path)
    with pytest.raises(RadioError):
        store.load()
    path.unlink()
    store.load()
    path.symlink_to(victim)
    with pytest.raises(RadioError, match="symlink"):
        store.save(RadioState())
    assert victim.read_text() == "keep"
    path.unlink()
    monkeypatch.setattr(
        os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("failure"))
    )
    with pytest.raises(RadioError, match="Cannot save"):
        store.save(RadioState())
    assert not path.exists() and not list(tmp_path.glob(".radio-*"))
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises((RadioError, ConfigError)):
        RadioStore(linked / "radio.json").load()


@pytest.mark.parametrize("status", [401, 403, 404])
def test_directory_denial_is_not_spotify_authentication(status):
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(status)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(RadioError, match=f"HTTP {status}"):
                await RadioDirectory(http).search()
        assert len(calls) == 1

    asyncio.run(run())


def test_directory_body_limit_and_compression_rejection():
    async def run():
        for response in [
            httpx.Response(200, content=b" " * 1_048_577),
            httpx.Response(
                200, headers={"content-encoding": "identity, identity"}, content=b"[]"
            ),
        ]:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r, response=response: response)
            ) as http:
                with pytest.raises(RadioError):
                    await RadioDirectory(http).search()

    asyncio.run(run())


def test_duplicate_saved_identifiers_are_corrupt_not_a_ui_crash(tmp_path):
    path = tmp_path / "radio.json"
    state = {"favorites": [station().model_dump(), station().model_dump()]}
    path.write_text(json.dumps(state))
    store = RadioStore(path)
    with pytest.raises(RadioError, match="repair"):
        store.load()
    assert json.loads(path.read_text()) == state
