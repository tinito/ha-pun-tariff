"""PUN Index GME prices from the Gestore dei Mercati Energetici (GME).

Two sources, both returning the same normalized `PricePoint` list:

- `GmeApiClient`: the official API (https://api.mercatoelettrico.org, service active since
  2025-10-15). Free for personal, informational use; credentials are requested from GME.
  Data: `ME_ZonalPrices`, segment MGP, zone "PUN", granularity PT60 or PT15.
- `GmeWebClient`: the ZIP of daily XML files behind the public download page of the GME site,
  used by other integrations. No credentials, but not a documented interface.

`PriceClient` puts them together: the API when credentials are configured, the GME site without
them or whenever the API fails.

Hours (1-25) and quarter periods (1-100) count from local midnight (Europe/Rome), so a
daylight-saving day has 23/25 hours and 92/100 quarters. Prices are published in EUR/MWh and
returned in EUR/kWh. The parse functions are pure and tested; the clients only do the I/O.
"""
from __future__ import annotations

import base64
import io
import json
import logging
from collections.abc import Callable
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

ROME = ZoneInfo("Europe/Rome")
API_URL = "https://api.mercatoelettrico.org/request/api/v1"
WEB_URL = (
    "https://gme.mercatoelettrico.org/DesktopModules/GmeDownload/API/ExcelDownload/downloadzipfile"
    "?DataInizio={start}&DataFine={end}&Date={end}&Mercato=MGP&Settore=Prezzi&FiltroDate=InizioFine"
)
WEB_HEADERS = {
    "moduleid": "12103",
    "referer": "https://gme.mercatoelettrico.org/en-us/Home/Results/Electricity/MGP/Download?valore=Prezzi",
    "tabid": "1749",
    "userid": "-1",
}
SOURCE_NAME = "Gestore dei Mercati Energetici S.p.A."
SOURCE_API = "api"
SOURCE_WEB = "web"


class GmeError(Exception):
    """GME data could not be retrieved or parsed."""


class GmeAuthError(GmeError):
    """GME API credentials were rejected."""


@dataclass(frozen=True, slots=True)
class PricePoint:
    """PUN for an interval: `start` is timezone-aware UTC, `minutes` is 60 or 15."""

    start: datetime
    minutes: int
    price: float  # EUR/kWh

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.minutes)

    @property
    def local_start(self) -> datetime:
        return self.start.astimezone(ROME)


def interval_start(day: date, index: int, minutes: int) -> datetime:
    """UTC start of the `index`-th (1-based) interval of `minutes` of a local day."""
    midnight = datetime.combine(day, time(0), ROME).astimezone(timezone.utc)
    return midnight + timedelta(minutes=minutes * (index - 1))


def _number(text: str | None) -> float:
    if text is None:
        raise GmeError("missing price")
    return float(text.strip().replace(".", "").replace(",", ".")) if "," in text else float(text)


def parse_web_zip(data: bytes) -> list[PricePoint]:
    """Parse the GME site ZIP: `<day>MGPPrezzi.xml` (hourly) and `<day>MGPPrezzi15.xml` (quarters)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise GmeError("the GME site did not return a ZIP archive") from err
    points: list[PricePoint] = []
    with archive:
        for name in sorted(archive.namelist()):
            root = ElementTree.parse(archive.open(name)).getroot()
            for row in root:
                if row.tag == "Prezzi":
                    minutes, index = 60, row.findtext("Ora")
                elif row.tag == "Prezzi15":
                    minutes, index = 15, row.findtext("Periodo")
                else:
                    continue
                day = datetime.strptime(row.findtext("Data") or "", "%Y%m%d").date()
                points.append(PricePoint(interval_start(day, int(index or 0), minutes), minutes,
                                         _number(row.findtext("PUN")) / 1000))
    return points


def parse_api_content(content: str, minutes: int) -> list[PricePoint]:
    """Parse `ContentResponse` of RequestData: base64 of a ZIP holding one JSON array."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(content)))
    except (ValueError, zipfile.BadZipFile) as err:
        raise GmeError("unexpected GME API content") from err
    with archive:
        rows = []
        for name in archive.namelist():
            loaded = json.loads(archive.read(name).decode("utf-8-sig"))
            rows.extend(loaded if isinstance(loaded, list) else [loaded])
    return parse_api_rows(rows, minutes)


def parse_api_rows(rows: list[dict], minutes: int) -> list[PricePoint]:
    """Keep the PUN rows (zone "PUN") of ME_ZonalPrices requested with granularity `minutes`.

    Hourly rows are indexed by `Hour` (1-25), quarter-hour rows by `Period` (1-100).
    """
    points = []
    for row in rows:
        if str(row.get("Zone", "")).upper() != "PUN":
            continue
        day = datetime.strptime(str(row["FlowDate"]), "%Y%m%d").date()
        index = int(row["Period"]) if minutes == 15 else int(row["Hour"])
        points.append(PricePoint(interval_start(day, index, minutes), minutes, _number(str(row["Price"])) / 1000))
    return points


class GmeWebClient:
    """Download from the public GME site (no credentials)."""

    def __init__(self, session, executor) -> None:
        self._session = session
        self._executor = executor  # async callable(func, *args) running func off the event loop

    async def fetch(self, start: date, end: date) -> list[PricePoint]:
        url = WEB_URL.format(start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
        async with self._session.get(url, headers=WEB_HEADERS, timeout=60) as response:
            if response.status != 200:
                raise GmeError(f"GME site answered {response.status}")
            data = await response.read()
        return await self._executor(parse_web_zip, data)


class GmeApiClient:
    """Official GME API (credentials from GME)."""

    def __init__(self, session, executor, username: str, password: str) -> None:
        self._session = session
        self._executor = executor
        self._username = username
        self._password = password
        self._token: str | None = None

    async def authenticate(self) -> None:
        async with self._session.post(f"{API_URL}/Auth", timeout=30,
                                      json={"Login": self._username, "Password": self._password}) as response:
            body = await response.json(content_type=None)
        if not body or not body.get("Success") or not body.get("token"):
            raise GmeAuthError((body or {}).get("Reason") or "authentication failed")
        self._token = body["token"]

    async def fetch(self, start: date, end: date, granularity: str = "PT15") -> list[PricePoint]:
        request = {"Platform": "PublicMarketResults", "Segment": "MGP", "DataName": "ME_ZonalPrices",
                   "IntervalStart": int(start.strftime("%Y%m%d")), "IntervalEnd": int(end.strftime("%Y%m%d")),
                   "Attributes": {"GranularityType": granularity}}
        for attempt in range(2):
            if self._token is None:
                await self.authenticate()
            async with self._session.post(f"{API_URL}/RequestData", json=request, timeout=60,
                                          headers={"Authorization": f"Bearer {self._token}"}) as response:
                if response.status == 401 and attempt == 0:
                    self._token = None
                    continue
                if response.status != 200:
                    raise GmeError(f"GME API answered {response.status}")
                body = await response.json(content_type=None)
            content = (body or {}).get("ContentResponse")
            if not content:
                raise GmeError((body or {}).get("ResultRequest") or "empty GME API response")
            return await self._executor(parse_api_content, content, 60 if granularity == "PT60" else 15)
        raise GmeAuthError("GME API token rejected")


class PriceClient:
    """The GME API when configured, the GME site without credentials or when the API fails.

    A rejected login calls `on_auth_error` (to ask the user for new credentials) and the prices
    still come from the site. `last_source` tells which source answered the last request.
    """

    def __init__(self, web: GmeWebClient, api: GmeApiClient | None = None,
                 on_auth_error: Callable[[GmeAuthError], None] | None = None) -> None:
        self._web = web
        self._api = api
        self._on_auth_error = on_auth_error
        self._warned = False
        self.last_source: str | None = None

    async def fetch(self, start: date, end: date, granularity: str = "PT60") -> list[PricePoint]:
        """PUN from `start` to `end`. The site returns hours and quarter hours in one download."""
        if self._api is not None:
            try:
                points = await self._api.fetch(start, end, granularity)
            except GmeAuthError as err:
                if self._on_auth_error is not None:
                    self._on_auth_error(err)
                self._fallback(f"credentials rejected ({err})")
            except (GmeError, TimeoutError, OSError) as err:
                self._fallback(str(err) or type(err).__name__)
            else:
                self._warned = False
                self.last_source = SOURCE_API
                return points
        points = await self._web.fetch(start, end)
        self.last_source = SOURCE_WEB
        return points

    def _fallback(self, reason: str) -> None:
        log = _LOGGER.debug if self._warned else _LOGGER.warning
        log("GME API not available, using the GME site: %s", reason)
        self._warned = True
