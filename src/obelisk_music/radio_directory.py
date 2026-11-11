"""Bounded, cancellable Radio Browser discovery with independent cooldowns."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from pydantic import ValidationError

from .radio_http import RadioError, RadioNetworkError
from .radio_models import Station

GENRES = (
    ("Lo-Fi / Chill", "chill"),
    ("House / Electronic", "electronic"),
    ("Rock / Metal", "rock"),
    ("Jazz / Blues", "jazz"),
    ("Ambient", "ambient"),
    ("Classical", "classical"),
)
MIRRORS = ("https://de1.api.radio-browser.info", "https://nl1.api.radio-browser.info")
PAGE_SIZE = 30


@dataclass(frozen=True)
class StationPage:
    stations: list[Station]
    next_offset: int | None
    excluded: int = 0


def retry_delay(value: str) -> float:
    try:
        delay = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            delay = (date - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            delay = 30
    return max(1, min(3600, delay))


class RadioDirectory:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self.cooldown = 0.0
        self._lock = asyncio.Lock()
        self.mirror = 0

    async def search(
        self,
        *,
        name: str = "",
        tag: str = "",
        country: str = "",
        language: str = "",
        offset: int = 0,
    ) -> StationPage:
        if not 0 <= offset <= 10000 or any(
            len(s) > 200 for s in (name, tag, country, language)
        ):
            raise RadioError(
                "Radio search is too long or pagination limit reached; refine the filters"
            )
        if country and (
            len(country) != 2 or not country.isascii() or not country.isalpha()
        ):
            raise RadioError("Country must be a two-letter code, for example DE or US")
        params = {
            "name": name,
            "tag": tag,
            "countrycode": country.upper(),
            "language": language,
            "offset": offset,
            "limit": PAGE_SIZE,
            "hidebroken": "true",
            "order": "clickcount",
            "reverse": "true",
        }
        async with self._lock:
            remaining = self.cooldown - time.monotonic()
            if remaining > 0:
                raise RadioError(
                    f"Radio directory cooling down; retry in {remaining:.0f}s"
                )
            for attempt in range(len(MIRRORS)):
                index = (self.mirror + attempt) % len(MIRRORS)
                try:
                    async with self.http.stream(
                        "GET", MIRRORS[index] + "/json/stations/search", params=params
                    ) as response:
                        if response.status_code == 429:
                            self.cooldown = time.monotonic() + retry_delay(
                                response.headers.get("Retry-After", "30")
                            )
                            raise RadioError(
                                "Radio directory rate limited; retry after its cooldown"
                            )
                        if response.status_code >= 500:
                            continue
                        if response.status_code != 200:
                            raise RadioError(
                                f"Radio directory unavailable (HTTP {response.status_code}); r retries"
                            )
                        if (
                            response.headers.get("content-encoding", "identity").lower()
                            != "identity"
                        ):
                            raise RadioError(
                                "Radio directory returned unsupported HTTP compression"
                            )
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 1_048_576:
                                raise RadioError(
                                    "Radio directory response exceeded its safety limit"
                                )
                    data = json.loads(body)
                    if not isinstance(data, list) or len(data) > PAGE_SIZE:
                        raise RadioError("Radio directory returned an invalid page")
                    stations = []
                    for item in data:
                        try:
                            stations.append(Station.from_directory(item))
                        except (
                            ValueError,
                            KeyError,
                            TypeError,
                            AttributeError,
                            ValidationError,
                        ):
                            pass
                    self.mirror = index
                    more = (
                        offset + len(data)
                        if len(data) == PAGE_SIZE and offset + len(data) <= 10000
                        else None
                    )
                    return StationPage(stations, more, len(data) - len(stations))
                except (
                    httpx.HTTPError,
                    RadioNetworkError,
                    json.JSONDecodeError,
                    UnicodeError,
                    RecursionError,
                ):
                    continue
        raise RadioError(
            "Radio directory mirrors could not be reached; r retries, or use favorites/custom streams"
        )
