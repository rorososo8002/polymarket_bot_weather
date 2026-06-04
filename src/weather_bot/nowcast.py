from __future__ import annotations

import math
import re
import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from .stations import STATION_MAP, StationMeta

AVIATIONWEATHER_METAR_SOURCE_URL = "https://aviationweather.gov/api/data/metar"
HKO_MAXMIN_SOURCE_URL = "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/latest_since_midnight_maxmin.csv"
SEOUL_SETTLEMENT_SOURCE_URL = "https://www.wunderground.com/history/daily/kr/incheon/RKSI"
AWC_METAR_UPDATE_CADENCE = (
    "Aviation Weather Center current METAR cache is updated once per minute; station METARs are "
    "normally hourly with special updates when conditions change."
)
HKO_MAXMIN_UPDATE_CADENCE = (
    "Hong Kong Observatory regional maximum/minimum air temperature since midnight updates every 10 minutes."
)


@dataclass(frozen=True)
class StationNowcastSource:
    station_id: str
    source: str
    source_url: str
    settlement_source_url: str
    update_cadence: str
    note: str
    provider_station_name: str = ""


@dataclass(frozen=True)
class StationNowcastObservation:
    station_id: str
    station_name: str
    observed_high_c: float | None
    observed_at: datetime | None
    high_observed_at: datetime | None
    source: str
    source_url: str
    settlement_source_url: str
    freshness_seconds: int | None
    unavailable_reason: str
    raw_observation_count: int = 0
    update_cadence: str = ""
    observed_low_c: float | None = None
    low_observed_at: datetime | None = None

    @property
    def usable(self) -> bool:
        return (
            not self.unavailable_reason
            and self.observed_at is not None
            and (self.observed_high_c is not None or self.observed_low_c is not None)
        )

    @property
    def observed_high_f(self) -> float | None:
        if self.observed_high_c is None:
            return None
        return self.observed_high_c * 9.0 / 5.0 + 32.0

    @property
    def observed_low_f(self) -> float | None:
        if self.observed_low_c is None:
            return None
        return self.observed_low_c * 9.0 / 5.0 + 32.0

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "station_id": self.station_id,
            "station_name": self.station_name,
            "observed_high_c": self.observed_high_c,
            "observed_high_f": self.observed_high_f,
            "observed_low_c": self.observed_low_c,
            "observed_low_f": self.observed_low_f,
            "observed_at": _iso_or_empty(self.observed_at),
            "high_observed_at": _iso_or_empty(self.high_observed_at),
            "low_observed_at": _iso_or_empty(self.low_observed_at),
            "source": self.source,
            "source_url": self.source_url,
            "settlement_source_url": self.settlement_source_url,
            "freshness_seconds": self.freshness_seconds,
            "unavailable_reason": self.unavailable_reason,
            "raw_observation_count": self.raw_observation_count,
            "update_cadence": self.update_cadence,
        }


@dataclass(frozen=True)
class _MetarBulkCacheEntry:
    cached_at: datetime
    payload: Any | None
    unavailable_reason: str = ""


def _default_nowcast_sources() -> dict[str, StationNowcastSource]:
    sources: dict[str, StationNowcastSource] = {}
    for station in STATION_MAP.values():
        if station.nowcast_provider_status != "provider_enabled":
            continue

        if station.nowcast_source_type == "metar":
            sources[station.station_id] = StationNowcastSource(
                station_id=station.station_id,
                source="aviationweather-metar",
                source_url=AVIATIONWEATHER_METAR_SOURCE_URL,
                settlement_source_url=SEOUL_SETTLEMENT_SOURCE_URL if station.station_id == "RKSI" else station.polymarket_rule_url,
                update_cadence=AWC_METAR_UPDATE_CADENCE,
                note="Same ICAO airport station as the settlement-station registry.",
            )
        elif station.nowcast_source_type == "hko_maxmin_since_midnight":
            sources[station.station_id] = StationNowcastSource(
                station_id=station.station_id,
                source="hko-maxmin-since-midnight",
                source_url=HKO_MAXMIN_SOURCE_URL,
                settlement_source_url=station.polymarket_rule_url,
                update_cadence=HKO_MAXMIN_UPDATE_CADENCE,
                note="HKO official regional max/min temperature since midnight for the HK Observatory row.",
                provider_station_name="HK Observatory",
            )

    return sources


DEFAULT_NOWCAST_SOURCES = _default_nowcast_sources()
PILOT_NOWCAST_SOURCES = DEFAULT_NOWCAST_SOURCES


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_or_empty(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_observation_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    try:
        return _as_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _parse_raw_metar_temp_c(raw: str) -> float | None:
    precise = re.search(r"\bT([01])(\d{3})([01])(\d{3})\b", raw)
    if precise:
        sign = -1.0 if precise.group(1) == "1" else 1.0
        return sign * int(precise.group(2)) / 10.0

    standard = re.search(r"\b(M?\d{2})/(?:M?\d{2}|//)\b", raw)
    if not standard:
        return None
    token = standard.group(1)
    sign = -1.0 if token.startswith("M") else 1.0
    digits = token[1:] if token.startswith("M") else token
    return sign * float(digits)


def _parse_hko_report_time(value: Any, timezone_name: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        local = datetime.strptime(text, "%Y%m%d%H%M").replace(tzinfo=_zone(timezone_name))
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def _parse_hko_temperature_c(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text.upper() == "N/A" or text.endswith("*"):
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _extract_temperature_c(record: dict[str, Any]) -> float | None:
    for key in ("temp", "temp_c", "temperature"):
        value = record.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    raw = record.get("rawOb") or record.get("raw_text") or record.get("raw")
    if raw is None:
        return None
    return _parse_raw_metar_temp_c(str(raw))


def _record_observed_at(record: dict[str, Any]) -> datetime | None:
    for key in ("obsTime", "reportTime", "receiptTime", "time", "valid_time"):
        observed_at = _parse_observation_time(record.get(key))
        if observed_at is not None:
            return observed_at
    return None


class AviationWeatherMetarNowcastProvider:
    """Pilot nowcast provider for explicitly mapped ICAO settlement stations."""

    def __init__(
        self,
        *,
        http_get: Callable[..., Any] = requests.get,
        timeout: float = 20.0,
        freshness_seconds: int = 5400,
        cache_ttl_seconds: int = 900,
        request_log_path: str | Path | None = None,
        sources: dict[str, StationNowcastSource] | None = None,
    ) -> None:
        self.http_get = http_get
        self.timeout = timeout
        self.freshness_seconds = max(0, int(freshness_seconds))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.request_log_path = Path(request_log_path) if request_log_path else None
        self._request_log_error = ""
        self.sources = sources or PILOT_NOWCAST_SOURCES
        self._cache: dict[tuple[str, str], tuple[datetime, StationNowcastObservation]] = {}
        self._awc_metar_bulk_cache: _MetarBulkCacheEntry | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> "AviationWeatherMetarNowcastProvider":
        request_log_path = settings.station_nowcast_request_log_path or str(
            Path(settings.state_path).with_name("station_nowcast_request_log.jsonl")
        )
        return cls(
            freshness_seconds=settings.station_nowcast_freshness_seconds,
            cache_ttl_seconds=settings.station_nowcast_cache_ttl_seconds,
            request_log_path=request_log_path,
        )

    def observed_high_so_far(
        self,
        station: StationMeta,
        *,
        target_date: date,
        now: datetime | None = None,
    ) -> StationNowcastObservation:
        return self.observed_temperature_extremes_so_far(station, target_date=target_date, now=now)

    def observed_low_so_far(
        self,
        station: StationMeta,
        *,
        target_date: date,
        now: datetime | None = None,
    ) -> StationNowcastObservation:
        return self.observed_temperature_extremes_so_far(station, target_date=target_date, now=now)

    def observed_temperature_extremes_so_far(
        self,
        station: StationMeta,
        *,
        target_date: date,
        now: datetime | None = None,
    ) -> StationNowcastObservation:
        current = _as_utc(now or _utc_now())
        source = self.sources.get(station.station_id)
        if source is None:
            return self._unavailable(station, "nowcast-source-unmapped")

        if target_date != _local_date(station.timezone, current):
            return self._unavailable(station, "target-date-not-today", source)

        cache_key = (station.station_id, target_date.isoformat())
        cached = self._cache.get(cache_key)
        cache_miss_reason = "empty-cache"
        if cached is not None and self.cache_ttl_seconds > 0:
            cached_at, observation = cached
            if (current - cached_at).total_seconds() <= self.cache_ttl_seconds:
                return observation
            cache_miss_reason = "expired-cache"
        elif cached is not None:
            cache_miss_reason = "cache-disabled"

        if source.source == "aviationweather-metar":
            observation = self._fetch_aviationweather(
                station,
                target_date,
                current,
                source,
                cache_miss_reason=cache_miss_reason,
            )
        elif source.source == "hko-maxmin-since-midnight":
            observation = self._fetch_hko_maxmin(
                station,
                target_date,
                current,
                source,
                cache_miss_reason=cache_miss_reason,
            )
        else:
            observation = self._unavailable(station, "unsupported-nowcast-source", source)

        self._cache[cache_key] = (current, observation)
        return observation

    def _fetch_aviationweather(
        self,
        station: StationMeta,
        target_date: date,
        now: datetime,
        source: StationNowcastSource,
        *,
        cache_miss_reason: str,
    ) -> StationNowcastObservation:
        bulk_entry = self._fetch_awc_metar_bulk_cache(
            station,
            target_date,
            now,
            source,
            cache_miss_reason=cache_miss_reason,
        )
        if bulk_entry.unavailable_reason:
            return self._unavailable(station, bulk_entry.unavailable_reason, source)

        return self._parse_payload(bulk_entry.payload, station, target_date, now, source)

    def _fetch_awc_metar_bulk_cache(
        self,
        station: StationMeta,
        target_date: date,
        now: datetime,
        source: StationNowcastSource,
        *,
        cache_miss_reason: str,
    ) -> _MetarBulkCacheEntry:
        cached = self._fresh_awc_metar_bulk_cache(now)
        if cached is not None:
            return cached

        station_ids = self._awc_metar_bulk_station_ids()
        response: Any | None = None
        try:
            response = self.http_get(
                source.source_url,
                params={
                    "ids": ",".join(station_ids),
                    "format": "json",
                    "hoursBeforeNow": self._awc_metar_bulk_hours_before_now(now, fallback_station=station),
                },
                timeout=self.timeout,
                headers={"User-Agent": "polymarket-weather-bot/nowcast"},
            )
            if getattr(response, "status_code", 200) == 204:
                entry = _MetarBulkCacheEntry(
                    cached_at=now,
                    payload=None,
                    unavailable_reason="no-observations-returned",
                )
                self._awc_metar_bulk_cache = entry
                self._append_request_log(
                    self._request_log_bulk_row(
                        requested_at=now,
                        trigger_station=station,
                        target_date=target_date,
                        source=source,
                        station_ids=station_ids,
                        cache_miss_reason=cache_miss_reason,
                        status="no_observations",
                        status_code=204,
                    )
                )
                return entry
            response.raise_for_status()
            payload = response.json()
            entry = _MetarBulkCacheEntry(cached_at=now, payload=payload)
            self._awc_metar_bulk_cache = entry
            self._append_request_log(
                self._request_log_bulk_row(
                    requested_at=now,
                    trigger_station=station,
                    target_date=target_date,
                    source=source,
                    station_ids=station_ids,
                    cache_miss_reason=cache_miss_reason,
                    status="success",
                    status_code=getattr(response, "status_code", None),
                )
            )
            return entry
        except Exception as exc:  # noqa: BLE001
            entry = _MetarBulkCacheEntry(
                cached_at=now,
                payload=None,
                unavailable_reason=f"nowcast-fetch-error:{type(exc).__name__}",
            )
            self._awc_metar_bulk_cache = entry
            self._append_request_log(
                self._request_log_bulk_row(
                    requested_at=now,
                    trigger_station=station,
                    target_date=target_date,
                    source=source,
                    station_ids=station_ids,
                    cache_miss_reason=cache_miss_reason,
                    status="error",
                    status_code=getattr(response, "status_code", None),
                    error=type(exc).__name__,
                )
            )
            return entry

    def _fresh_awc_metar_bulk_cache(self, now: datetime) -> _MetarBulkCacheEntry | None:
        cached = self._awc_metar_bulk_cache
        if cached is None or self.cache_ttl_seconds <= 0:
            return None
        if (now - cached.cached_at).total_seconds() <= self.cache_ttl_seconds:
            return cached
        return None

    def _awc_metar_bulk_station_ids(self) -> list[str]:
        station_ids = {
            source.station_id.upper()
            for source in self.sources.values()
            if source.source == "aviationweather-metar"
        }
        return sorted(station_ids)

    def _awc_metar_bulk_hours_before_now(self, now: datetime, *, fallback_station: StationMeta) -> int:
        station_ids = set(self._awc_metar_bulk_station_ids())
        hours = [
            _hours_since_local_midnight(station.timezone, now)
            for station in STATION_MAP.values()
            if station.station_id.upper() in station_ids
        ]
        if not hours:
            return _hours_since_local_midnight(fallback_station.timezone, now)
        return max(hours)

    def _fetch_hko_maxmin(
        self,
        station: StationMeta,
        target_date: date,
        now: datetime,
        source: StationNowcastSource,
        *,
        cache_miss_reason: str,
    ) -> StationNowcastObservation:
        response: Any | None = None
        try:
            response = self.http_get(
                source.source_url,
                params={},
                timeout=self.timeout,
                headers={"User-Agent": "polymarket-weather-bot/nowcast"},
            )
            if getattr(response, "status_code", 200) == 204:
                self._append_request_log(
                    self._request_log_row(
                        requested_at=now,
                        station=station,
                        target_date=target_date,
                        source=source,
                        cache_miss_reason=cache_miss_reason,
                        status="no_observations",
                        status_code=204,
                    )
                )
                return self._unavailable(station, "no-observations-returned", source)
            response.raise_for_status()
            observation = self._parse_hko_payload(response.text, station, target_date, now, source)
            self._append_request_log(
                self._request_log_row(
                    requested_at=now,
                    station=station,
                    target_date=target_date,
                    source=source,
                    cache_miss_reason=cache_miss_reason,
                    status="success",
                    status_code=getattr(response, "status_code", None),
                    unavailable_reason=observation.unavailable_reason,
                )
            )
            return observation
        except Exception as exc:  # noqa: BLE001
            self._append_request_log(
                self._request_log_row(
                    requested_at=now,
                    station=station,
                    target_date=target_date,
                    source=source,
                    cache_miss_reason=cache_miss_reason,
                    status="error",
                    status_code=getattr(response, "status_code", None),
                    error=type(exc).__name__,
                )
            )
            return self._unavailable(station, f"nowcast-fetch-error:{type(exc).__name__}", source)

    def _append_request_log(self, row: dict[str, Any]) -> None:
        if self.request_log_path is None:
            return
        try:
            self.request_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.request_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
            self._request_log_error = ""
        except Exception as exc:  # noqa: BLE001
            self._request_log_error = type(exc).__name__

    def _request_log_row(
        self,
        *,
        requested_at: datetime,
        station: StationMeta,
        target_date: date,
        source: StationNowcastSource,
        cache_miss_reason: str,
        status: str,
        status_code: int | None = None,
        error: str = "",
        unavailable_reason: str = "",
    ) -> dict[str, Any]:
        return {
            "cache_miss_reason": cache_miss_reason,
            "city": station.city,
            "error": error,
            "requested_at": _iso_or_empty(requested_at),
            "source": source.source,
            "source_url": source.source_url,
            "station_id": station.station_id,
            "station_name": station.station_name,
            "status": status,
            "status_code": status_code,
            "target_date": target_date.isoformat(),
            "timezone": station.timezone,
            "unavailable_reason": unavailable_reason,
        }

    def _request_log_bulk_row(
        self,
        *,
        requested_at: datetime,
        trigger_station: StationMeta,
        target_date: date,
        source: StationNowcastSource,
        station_ids: list[str],
        cache_miss_reason: str,
        status: str,
        status_code: int | None = None,
        error: str = "",
        unavailable_reason: str = "",
    ) -> dict[str, Any]:
        return {
            "cache_miss_reason": cache_miss_reason,
            "city": "bulk-metar",
            "error": error,
            "request_mode": "awc_metar_bulk_cache",
            "requested_at": _iso_or_empty(requested_at),
            "requested_station_count": len(station_ids),
            "requested_station_ids": station_ids,
            "source": source.source,
            "source_url": source.source_url,
            "station_id": "METAR_BULK",
            "station_name": "Aviation Weather Center METAR bulk prefetch",
            "status": status,
            "status_code": status_code,
            "target_date": target_date.isoformat(),
            "timezone": "bulk",
            "trigger_city": trigger_station.city,
            "trigger_station_id": trigger_station.station_id,
            "trigger_timezone": trigger_station.timezone,
            "unavailable_reason": unavailable_reason,
        }

    def _parse_payload(
        self,
        payload: Any,
        station: StationMeta,
        target_date: date,
        now: datetime,
        source: StationNowcastSource,
    ) -> StationNowcastObservation:
        if not isinstance(payload, list):
            return self._unavailable(station, "malformed-observation-payload", source)

        zone = _zone(station.timezone)
        observations: list[tuple[datetime, float]] = []
        for record in payload:
            if not isinstance(record, dict):
                continue
            if str(record.get("icaoId") or record.get("station_id") or station.station_id).upper() != station.station_id:
                continue
            observed_at = _record_observed_at(record)
            temp_c = _extract_temperature_c(record)
            if observed_at is None or temp_c is None:
                continue
            if observed_at.astimezone(zone).date() == target_date:
                observations.append((observed_at, temp_c))

        if not observations:
            return self._unavailable(station, "malformed-observation-payload", source, raw_count=len(payload))

        latest_at = max(observed_at for observed_at, _temp in observations)
        high_at, high_c = max(observations, key=lambda item: item[1])
        low_at, low_c = min(observations, key=lambda item: item[1])
        freshness_seconds = max(0, int((now - latest_at).total_seconds()))
        reason = "stale-observation" if freshness_seconds > self.freshness_seconds else ""
        return StationNowcastObservation(
            station_id=station.station_id,
            station_name=station.station_name,
            observed_high_c=round(high_c, 3),
            observed_at=latest_at,
            high_observed_at=high_at,
            source=source.source,
            source_url=source.source_url,
            settlement_source_url=source.settlement_source_url,
            freshness_seconds=freshness_seconds,
            unavailable_reason=reason,
            raw_observation_count=len(payload),
            update_cadence=source.update_cadence,
            observed_low_c=round(low_c, 3),
            low_observed_at=low_at,
        )

    def _parse_hko_payload(
        self,
        payload: str,
        station: StationMeta,
        target_date: date,
        now: datetime,
        source: StationNowcastSource,
    ) -> StationNowcastObservation:
        reader = csv.DictReader(io.StringIO(payload.strip()))
        if not reader.fieldnames:
            return self._unavailable(station, "malformed-observation-payload", source)

        rows = list(reader)
        station_name = source.provider_station_name or station.station_name
        for row in rows:
            if str(row.get("Automatic Weather Station") or "").strip().casefold() != station_name.casefold():
                continue

            observed_at = _parse_hko_report_time(row.get("Date time"), station.timezone)
            high_c = _parse_hko_temperature_c(row.get("Maximum Air Temperature Since Midnight(degree Celsius)"))
            low_c = _parse_hko_temperature_c(row.get("Minimum Air Temperature Since Midnight(degree Celsius)"))
            if observed_at is None or high_c is None or low_c is None:
                return self._unavailable(station, "malformed-observation-payload", source, raw_count=len(rows))

            freshness_seconds = max(0, int((now - observed_at).total_seconds()))
            reason = "stale-observation" if (
                observed_at.astimezone(_zone(station.timezone)).date() != target_date
                or freshness_seconds > self.freshness_seconds
            ) else ""
            return StationNowcastObservation(
                station_id=station.station_id,
                station_name=station.station_name,
                observed_high_c=round(high_c, 3),
                observed_at=observed_at,
                high_observed_at=None,
                source=source.source,
                source_url=source.source_url,
                settlement_source_url=source.settlement_source_url,
                freshness_seconds=freshness_seconds,
                unavailable_reason=reason,
                raw_observation_count=len(rows),
                update_cadence=source.update_cadence,
                observed_low_c=round(low_c, 3),
                low_observed_at=None,
            )

        return self._unavailable(station, "malformed-observation-payload", source, raw_count=len(rows))

    def _unavailable(
        self,
        station: StationMeta,
        reason: str,
        source: StationNowcastSource | None = None,
        *,
        raw_count: int = 0,
    ) -> StationNowcastObservation:
        return StationNowcastObservation(
            station_id=station.station_id,
            station_name=station.station_name,
            observed_high_c=None,
            observed_at=None,
            high_observed_at=None,
            source=source.source if source is not None else "",
            source_url=source.source_url if source is not None else "",
            settlement_source_url=source.settlement_source_url if source is not None else "",
            freshness_seconds=None,
            unavailable_reason=reason,
            raw_observation_count=raw_count,
            update_cadence=source.update_cadence if source is not None else "",
        )


def _zone(timezone_name: str) -> ZoneInfo:
    return ZoneInfo(timezone_name if timezone_name and timezone_name != "auto" else "UTC")


def _local_date(timezone_name: str, now: datetime) -> date:
    return now.astimezone(_zone(timezone_name)).date()


def _hours_since_local_midnight(timezone_name: str, now: datetime) -> int:
    zone = _zone(timezone_name)
    local_now = now.astimezone(zone)
    local_midnight = datetime.combine(local_now.date(), time.min, tzinfo=zone)
    elapsed = local_now - local_midnight
    return max(2, min(36, int(math.ceil(elapsed / timedelta(hours=1))) + 1))
