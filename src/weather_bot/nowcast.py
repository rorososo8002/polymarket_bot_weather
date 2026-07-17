from __future__ import annotations

import math
import re
import csv
import io
import json
import os
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from .stations import STATION_MAP, StationMeta

AVIATIONWEATHER_METAR_SOURCE_URL = "https://aviationweather.gov/api/data/metar"
KMA_METAR_SOURCE_URL = "https://apis.data.go.kr/1360000/AmmService/getMetar"
HKO_MAXMIN_SOURCE_URL = "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/latest_since_midnight_maxmin.csv"
SEOUL_SETTLEMENT_SOURCE_URL = "https://www.wunderground.com/history/daily/kr/incheon/RKSI"
AWC_METAR_UPDATE_CADENCE = (
    "Aviation Weather Center METAR API requests are floored at one real request per minute; "
    "station METARs are normally hourly with special updates when conditions change."
)
HKO_MAXMIN_UPDATE_CADENCE = (
    "Hong Kong Observatory regional maximum/minimum air temperature since midnight updates every 10 minutes."
)
AWC_METAR_MIN_REAL_REQUEST_INTERVAL_SECONDS = 60
HKO_MAXMIN_MIN_REAL_REQUEST_INTERVAL_SECONDS = 10 * 60
AWC_METAR_MAX_CONTINUITY_GAP_SECONDS = 90 * 60
AWC_METAR_RECOVERY_LOOKBACK_HOURS = 4
AWC_METAR_MAX_RESPONSE_ROWS = 400
HKO_MAXMIN_MAX_OBSERVATION_AGE_SECONDS = 2 * HKO_MAXMIN_MIN_REAL_REQUEST_INTERVAL_SECONDS


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
    low_last_observed_at: datetime | None = None
    low_rise_observed_at: datetime | None = None
    high_bucket_confirmations: int = 0
    high_last_observed_at: datetime | None = None
    high_drop_observed_at: datetime | None = None
    station_local_date: str = ""
    station_local_time: str = ""
    midnight_reset_status: str = ""
    data_block_reason: str = ""
    daily_extremes_complete: bool = True
    daily_extremes_status: str = ""
    latest_temp_c: float | None = None
    latest_dewpoint_c: float | None = None
    latest_weather: str = ""
    latest_raw_observation: str = ""
    learned_observation_interval_seconds: int | None = None
    next_observation_due_at: datetime | None = None
    observation_due_status: str = ""
    request_started_at: datetime | None = None
    source_received_at: datetime | None = None
    bot_received_at: datetime | None = None
    source_latency_seconds: int | None = None
    source_latency_status: str = ""
    bot_detection_latency_seconds: int | None = None

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
            "high_last_observed_at": _iso_or_empty(self.high_last_observed_at),
            "high_drop_observed_at": _iso_or_empty(self.high_drop_observed_at),
            "low_observed_at": _iso_or_empty(self.low_observed_at),
            "low_last_observed_at": _iso_or_empty(self.low_last_observed_at),
            "low_rise_observed_at": _iso_or_empty(self.low_rise_observed_at),
            "source": self.source,
            "source_url": self.source_url,
            "settlement_source_url": self.settlement_source_url,
            "freshness_seconds": self.freshness_seconds,
            "unavailable_reason": self.unavailable_reason,
            "raw_observation_count": self.raw_observation_count,
            "update_cadence": self.update_cadence,
            "high_bucket_confirmations": self.high_bucket_confirmations,
            "station_local_date": self.station_local_date,
            "station_local_time": self.station_local_time,
            "midnight_reset_status": self.midnight_reset_status,
            "data_block_reason": self.data_block_reason,
            "daily_extremes_complete": self.daily_extremes_complete,
            "daily_extremes_status": self.daily_extremes_status,
            "latest_temp_c": self.latest_temp_c,
            "latest_dewpoint_c": self.latest_dewpoint_c,
            "latest_weather": self.latest_weather,
            "latest_raw_observation": self.latest_raw_observation,
            "learned_observation_interval_seconds": self.learned_observation_interval_seconds,
            "next_observation_due_at": _iso_or_empty(self.next_observation_due_at),
            "observation_due_status": self.observation_due_status,
            "request_started_at": _iso_or_empty(self.request_started_at),
            "source_received_at": _iso_or_empty(self.source_received_at),
            "bot_received_at": _iso_or_empty(self.bot_received_at),
            "source_latency_seconds": self.source_latency_seconds,
            "source_latency_status": self.source_latency_status,
            "bot_detection_latency_seconds": self.bot_detection_latency_seconds,
        }


@dataclass(frozen=True)
class _MetarBulkCacheEntry:
    cached_at: datetime
    payload: Any | None
    unavailable_reason: str = ""
    hours_before_now: int = 0
    requested_at: datetime | None = None
    received_at: datetime | None = None


@dataclass(frozen=True)
class _KmaMetarFetch:
    source: StationNowcastSource
    rows: list[dict[str, Any]]
    requested_at: datetime
    received_at: datetime | None
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


def _parse_raw_metar_dewpoint_c(raw: str) -> float | None:
    precise = re.search(r"\bT[01]\d{3}([01])(\d{3})\b", raw)
    if precise:
        sign = -1.0 if precise.group(1) == "1" else 1.0
        return sign * int(precise.group(2)) / 10.0

    standard = re.search(r"\bM?\d{2}/(M?\d{2}|//)\b", raw)
    if not standard or standard.group(1) == "//":
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


def _extract_dewpoint_c(record: dict[str, Any]) -> float | None:
    for key in ("dewp", "dewpoint", "dewpoint_c"):
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
    return _parse_raw_metar_dewpoint_c(str(raw))


def _raw_observation_text(record: dict[str, Any]) -> str:
    return str(record.get("rawOb") or record.get("raw_text") or record.get("raw") or "")


def _record_observed_at(record: dict[str, Any]) -> datetime | None:
    for key in ("obsTime", "reportTime", "receiptTime", "time", "valid_time"):
        observed_at = _parse_observation_time(record.get(key))
        if observed_at is not None:
            return observed_at
    return None


def _record_source_received_at(record: dict[str, Any]) -> datetime | None:
    for key in ("receiptTime", "source_received_at", "published_at", "issueTime"):
        received_at = _parse_observation_time(record.get(key))
        if received_at is not None:
            return received_at
    return None


def _parse_raw_metar_observed_at(raw: str, reference: datetime) -> datetime | None:
    match = re.search(r"\b(?:METAR|SPECI)\s+[A-Z]{4}\s+(?:COR\s+)?(\d{2})(\d{2})(\d{2})Z\b", raw)
    if not match:
        return None
    day, hour, minute = (int(value) for value in match.groups())
    current = _as_utc(reference)
    candidates: list[datetime] = []
    for month_offset in (-1, 0, 1):
        month_index = current.year * 12 + current.month - 1 + month_offset
        year, zero_based_month = divmod(month_index, 12)
        try:
            candidates.append(
                datetime(year, zero_based_month + 1, day, hour, minute, tzinfo=timezone.utc)
            )
        except ValueError:
            continue
    plausible = [candidate for candidate in candidates if candidate <= current + timedelta(minutes=5)]
    if not plausible:
        return None
    return min(plausible, key=lambda candidate: abs((current - candidate).total_seconds()))


def _raw_metar_station_id(raw: str) -> str:
    match = re.search(r"\b(?:METAR|SPECI)\s+(?:COR\s+)?([A-Z]{4})\b", raw.upper())
    return match.group(1) if match else ""


def _kma_metar_rows(payload: Any, *, reference: datetime) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    response = payload.get("response")
    if not isinstance(response, dict):
        return []
    header = response.get("header")
    if not isinstance(header, dict) or str(header.get("resultCode") or "") not in {"0", "00"}:
        return []
    body = response.get("body")
    if not isinstance(body, dict):
        return []
    items = body.get("items")
    if isinstance(items, dict):
        items = items.get("item")
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("metarMsg") or item.get("msgText") or item.get("rawOb") or "").strip()
        station_id = str(item.get("icaoCode") or item.get("icao") or item.get("stationId") or "").strip()
        observed_at = _record_observed_at(item) or _parse_raw_metar_observed_at(raw, reference)
        raw_station_id = _raw_metar_station_id(raw)
        if (
            not raw
            or not station_id
            or not raw_station_id
            or raw_station_id != station_id.upper()
            or observed_at is None
        ):
            continue
        rows.append(
            {
                "icaoId": station_id,
                "rawOb": raw.rstrip("="),
                "reportTime": observed_at.isoformat(),
                "source_received_at": item.get("issueTime") or item.get("announceTime"),
            }
        )
    return rows


def _learned_observation_interval_seconds(observed_times: list[datetime]) -> int | None:
    ordered = sorted(set(observed_times))
    gaps = sorted(
        int((current - previous).total_seconds())
        for previous, current in zip(ordered, ordered[1:])
        if 0 < (current - previous).total_seconds() <= AWC_METAR_MAX_CONTINUITY_GAP_SECONDS
    )
    if len(gaps) < 2:
        return None
    middle = len(gaps) // 2
    if len(gaps) % 2:
        return gaps[middle]
    return int(round((gaps[middle - 1] + gaps[middle]) / 2.0))


class AviationWeatherMetarNowcastProvider:
    """Pilot nowcast provider for explicitly mapped ICAO settlement stations."""

    def __init__(
        self,
        *,
        http_get: Callable[..., Any] = requests.get,
        timeout: float = 20.0,
        freshness_seconds: int = 5400,
        cache_ttl_seconds: int = 60,
        request_log_path: str | Path | None = None,
        hko_rollover_state_path: str | Path | None = None,
        metar_daily_extremes_state_path: str | Path | None = None,
        sources: dict[str, StationNowcastSource] | None = None,
        kma_metar_service_key: str = "",
        kma_metar_poll_seconds: int = 30,
        kma_metar_timeout_seconds: float = 3.0,
        kma_metar_station_ids: set[str] | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.http_get = http_get
        self.clock = clock
        self.timeout = timeout
        self.freshness_seconds = max(0, int(freshness_seconds))
        self.kma_metar_service_key = str(kma_metar_service_key or "").strip()
        self.kma_metar_poll_seconds = max(5, int(kma_metar_poll_seconds))
        self.kma_metar_timeout_seconds = max(0.1, float(kma_metar_timeout_seconds))
        self.kma_metar_station_ids = {
            str(station_id).strip().upper()
            for station_id in (kma_metar_station_ids or {"RKSI", "RKPK"})
            if str(station_id).strip()
        }
        configured_cache_ttl = max(0, int(cache_ttl_seconds))
        self.cache_ttl_seconds = (
            min(configured_cache_ttl, self.kma_metar_poll_seconds)
            if self.kma_metar_service_key and configured_cache_ttl > 0
            else configured_cache_ttl
        )
        self.request_log_path = Path(request_log_path) if request_log_path else None
        self.hko_rollover_state_path = Path(hko_rollover_state_path) if hko_rollover_state_path else None
        self.metar_daily_extremes_state_path = (
            Path(metar_daily_extremes_state_path) if metar_daily_extremes_state_path else None
        )
        self._request_log_error = ""
        self._request_log_last_success_at: datetime | None = None
        self._kma_unavailable_until: datetime | None = None
        self._kma_station_unavailable_until: dict[str, datetime] = {}
        self.sources = sources or PILOT_NOWCAST_SOURCES
        self._cache: dict[tuple[str, str], tuple[datetime, StationNowcastObservation]] = {}
        self._awc_metar_bulk_cache: _MetarBulkCacheEntry | None = None
        self._logged_observation_delivery: dict[tuple[str, str], datetime] = {}
        self._cache_lock = threading.RLock()
        self._observation_lock = threading.RLock()
        self._hko_observation_lock = threading.RLock()
        self._request_log_lock = threading.RLock()
        self._hko_rollover_state = self._load_hko_rollover_state()
        self._metar_daily_extremes_state = self._load_metar_daily_extremes_state()

    @classmethod
    def from_settings(cls, settings: Any) -> "AviationWeatherMetarNowcastProvider":
        request_log_path = settings.station_nowcast_request_log_path or str(
            Path(settings.state_path).with_name("station_nowcast_request_log.jsonl")
        )
        hko_rollover_state_path = getattr(settings, "hko_rollover_state_path", "") or str(
            Path(settings.state_path).with_name("hko_rollover_state.json")
        )
        metar_daily_extremes_state_path = getattr(
            settings,
            "metar_daily_extremes_state_path",
            "",
        ) or str(Path(settings.state_path).with_name("metar_daily_extremes_state.json"))
        return cls(
            freshness_seconds=settings.station_nowcast_freshness_seconds,
            cache_ttl_seconds=settings.station_nowcast_cache_ttl_seconds,
            request_log_path=request_log_path,
            hko_rollover_state_path=hko_rollover_state_path,
            metar_daily_extremes_state_path=metar_daily_extremes_state_path,
            kma_metar_service_key=getattr(settings, "kma_metar_service_key", ""),
            kma_metar_poll_seconds=getattr(settings, "kma_metar_poll_seconds", 30),
            kma_metar_timeout_seconds=getattr(settings, "kma_metar_timeout_seconds", 3.0),
            kma_metar_station_ids={
                station_id.strip().upper()
                for station_id in str(getattr(settings, "kma_metar_station_ids", "RKSI,RKPK")).split(",")
                if station_id.strip()
            },
        )

    def _load_metar_daily_extremes_state(self) -> dict[str, Any]:
        path = self.metar_daily_extremes_state_path
        if path is None:
            return {"schema_version": 1, "stations": {}}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {"schema_version": 1, "stations": {}}
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("stations"), dict)
        ):
            return {"schema_version": 1, "stations": {}}
        return payload

    def _write_metar_daily_extremes_state(self) -> None:
        path = self.metar_daily_extremes_state_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(
                json.dumps(self._metar_daily_extremes_state, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def _accumulate_metar_daily_extremes(
        self,
        station: StationMeta,
        observations: list[tuple[datetime, float]],
        target_date: date,
    ) -> dict[str, Any] | None:
        stations = self._metar_daily_extremes_state.setdefault("stations", {})
        station_state = stations.setdefault(
            station.station_id,
            {"days": {}, "last_local_date": "", "last_observed_at": ""},
        )
        days = station_state.setdefault("days", {})
        zone = _zone(station.timezone)
        last_observed_at = _parse_observation_time(station_state.get("last_observed_at"))
        last_local_date_text = str(station_state.get("last_local_date") or "")
        try:
            last_local_date = date.fromisoformat(last_local_date_text) if last_local_date_text else None
        except ValueError:
            last_local_date = None

        for observed_at, temp_c in sorted(set(observations)):
            local_date = observed_at.astimezone(zone).date()
            local_date_text = local_date.isoformat()
            if last_observed_at is not None and observed_at <= last_observed_at:
                continue

            gap_seconds = (
                (observed_at - last_observed_at).total_seconds()
                if last_observed_at is not None
                else None
            )
            is_next_date = (
                last_local_date is not None
                and local_date - last_local_date == timedelta(days=1)
            )
            if local_date_text not in days:
                high_bucket = math.floor(temp_c)
                complete = bool(
                    is_next_date
                    and gap_seconds is not None
                    and gap_seconds <= AWC_METAR_MAX_CONTINUITY_GAP_SECONDS
                )
                days[local_date_text] = {
                    "high_c": temp_c,
                    "high_bucket_c": high_bucket,
                    "high_bucket_confirmations": 1,
                    "low_c": temp_c,
                    "high_observed_at": _iso_or_empty(observed_at),
                    "high_last_observed_at": _iso_or_empty(observed_at),
                    "high_drop_observed_at": "",
                    "low_observed_at": _iso_or_empty(observed_at),
                    "low_last_observed_at": _iso_or_empty(observed_at),
                    "low_rise_observed_at": "",
                    "latest_observed_at": _iso_or_empty(observed_at),
                    "complete": complete,
                    "blocked_reason": (
                        "" if complete else "metar-daily-extremes-baseline-missing"
                    ),
                }
            else:
                day = days[local_date_text]
                if (
                    bool(day.get("complete"))
                    and gap_seconds is not None
                    and gap_seconds > AWC_METAR_MAX_CONTINUITY_GAP_SECONDS
                ):
                    day["complete"] = False
                    day["blocked_reason"] = "metar-observation-gap"
                high_bucket = math.floor(float(day.get("high_c", temp_c)))
                try:
                    high_bucket_confirmations = int(day.get("high_bucket_confirmations", 1))
                except (TypeError, ValueError):
                    high_bucket_confirmations = 1
                observed_bucket = math.floor(temp_c)
                if temp_c > float(day["high_c"]):
                    day["high_c"] = temp_c
                    day["high_observed_at"] = _iso_or_empty(observed_at)
                    day["high_last_observed_at"] = _iso_or_empty(observed_at)
                    day["high_drop_observed_at"] = ""
                    day["high_bucket_c"] = observed_bucket
                    day["high_bucket_confirmations"] = (
                        high_bucket_confirmations + 1 if observed_bucket == high_bucket else 1
                    )
                elif temp_c == float(day["high_c"]):
                    day["high_last_observed_at"] = _iso_or_empty(observed_at)
                    day["high_drop_observed_at"] = ""
                    if observed_bucket == high_bucket:
                        day["high_bucket_confirmations"] = high_bucket_confirmations + 1
                elif observed_bucket == high_bucket:
                    day["high_bucket_confirmations"] = high_bucket_confirmations + 1
                elif temp_c < float(day["high_c"]) and not day.get("high_drop_observed_at"):
                    day["high_drop_observed_at"] = _iso_or_empty(observed_at)
                current_low = float(day["low_c"])
                if temp_c < current_low:
                    day["low_c"] = temp_c
                    day["low_observed_at"] = _iso_or_empty(observed_at)
                    day["low_last_observed_at"] = _iso_or_empty(observed_at)
                    day["low_rise_observed_at"] = ""
                elif temp_c == current_low:
                    day["low_last_observed_at"] = _iso_or_empty(observed_at)
                    day["low_rise_observed_at"] = ""
                elif temp_c > current_low and not day.get("low_rise_observed_at"):
                    day["low_rise_observed_at"] = _iso_or_empty(observed_at)
                day["latest_observed_at"] = _iso_or_empty(observed_at)

            last_observed_at = observed_at
            last_local_date = local_date
            station_state["last_observed_at"] = _iso_or_empty(observed_at)
            station_state["last_local_date"] = local_date_text

        for old_date in sorted(days)[:-2]:
            days.pop(old_date, None)
        self._write_metar_daily_extremes_state()
        day = days.get(target_date.isoformat())
        return day if isinstance(day, dict) else None

    def _load_hko_rollover_state(self) -> dict[str, Any]:
        if self.hko_rollover_state_path is None:
            return {}
        try:
            payload = json.loads(self.hko_rollover_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return {}
        return payload

    def _write_hko_rollover_state(self) -> None:
        path = self.hko_rollover_state_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(
                json.dumps(self._hko_rollover_state, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def _record_hko_daily_extremes(
        self,
        *,
        observed_at: datetime,
        observed_date: date,
        high_c: float,
        low_c: float,
    ) -> None:
        local_date = observed_date.isoformat()
        days = self._hko_rollover_state.setdefault("days", {})
        day = days.get(local_date)
        if not isinstance(day, dict):
            day = {
                "high_c": high_c,
                "high_first_observed_at": _iso_or_empty(observed_at),
                "low_c": low_c,
                "low_first_observed_at": _iso_or_empty(observed_at),
            }
            days[local_date] = day
        else:
            if high_c > float(day["high_c"]):
                day["high_c"] = high_c
                day["high_first_observed_at"] = _iso_or_empty(observed_at)
            if low_c < float(day["low_c"]):
                day["low_c"] = low_c
                day["low_first_observed_at"] = _iso_or_empty(observed_at)
        day["latest_observed_at"] = _iso_or_empty(observed_at)
        for old_date in sorted(days)[:-2]:
            days.pop(old_date, None)

    def _validate_hko_rollover(
        self,
        *,
        observed_at: datetime,
        observed_date: date,
        high_c: float,
        low_c: float,
    ) -> tuple[str, str]:
        if self.hko_rollover_state_path is None:
            return "not_configured", ""

        state = dict(self._hko_rollover_state)
        current_date_text = str(state.get("current_date") or "")
        try:
            current_date = date.fromisoformat(current_date_text) if current_date_text else None
        except ValueError:
            current_date = None

        if current_date is None:
            self._hko_rollover_state = {
                "schema_version": 1,
                "station_id": "HKO",
                "current_date": observed_date.isoformat(),
                "current_high_c": high_c,
                "current_low_c": low_c,
                "reset_verified": False,
                "blocked_reason": "hko-rollover-baseline-missing",
            }
            self._write_hko_rollover_state()
            return "blocked_baseline_missing", "hko-rollover-baseline-missing"

        if observed_date < current_date:
            return "blocked_date_regression", "hko-observation-date-regressed"

        if observed_date > current_date:
            previous_is_yesterday = observed_date - current_date == timedelta(days=1)
            state = {
                "schema_version": 1,
                "station_id": "HKO",
                "previous_date": current_date.isoformat(),
                "previous_high_c": state.get("current_high_c"),
                "previous_low_c": state.get("current_low_c"),
                "current_date": observed_date.isoformat(),
                "current_high_c": high_c,
                "current_low_c": low_c,
                "reset_verified": False,
                "blocked_reason": "",
                "days": state.get("days", {}),
            }
            if not previous_is_yesterday:
                state["blocked_reason"] = "hko-rollover-baseline-missing"
            self._hko_rollover_state = state

        state = self._hko_rollover_state
        if bool(state.get("reset_verified")):
            previous_high = _parse_hko_temperature_c(state.get("current_high_c"))
            previous_low = _parse_hko_temperature_c(state.get("current_low_c"))
            reason = str(state.get("blocked_reason") or "")
            if reason:
                return "blocked_same_day_monotonicity", reason
            if previous_high is not None and high_c < previous_high - 1e-9:
                reason = "hko-same-day-high-decreased"
            elif previous_low is not None and low_c > previous_low + 1e-9:
                reason = "hko-same-day-low-increased"
            if reason:
                state["blocked_reason"] = reason
                self._write_hko_rollover_state()
                return "blocked_same_day_monotonicity", reason
            state["current_high_c"] = max(high_c, previous_high if previous_high is not None else high_c)
            state["current_low_c"] = min(low_c, previous_low if previous_low is not None else low_c)
            self._record_hko_daily_extremes(
                observed_at=observed_at,
                observed_date=observed_date,
                high_c=high_c,
                low_c=low_c,
            )
            self._write_hko_rollover_state()
            return "verified", ""

        previous_high = _parse_hko_temperature_c(state.get("previous_high_c"))
        previous_low = _parse_hko_temperature_c(state.get("previous_low_c"))
        if previous_high is None or previous_low is None:
            state["current_high_c"] = high_c
            state["current_low_c"] = low_c
            state["blocked_reason"] = "hko-rollover-baseline-missing"
            self._write_hko_rollover_state()
            return "blocked_baseline_missing", "hko-rollover-baseline-missing"

        nested_range = high_c <= previous_high + 1e-9 and low_c >= previous_low - 1e-9
        strictly_reset = high_c < previous_high - 1e-9 or low_c > previous_low + 1e-9
        state["current_high_c"] = high_c
        state["current_low_c"] = low_c
        if nested_range and strictly_reset:
            state["reset_verified"] = True
            state["blocked_reason"] = ""
            self._record_hko_daily_extremes(
                observed_at=observed_at,
                observed_date=observed_date,
                high_c=high_c,
                low_c=low_c,
            )
            self._write_hko_rollover_state()
            return "verified", ""
        state["blocked_reason"] = "hko-midnight-reset-pending"
        self._write_hko_rollover_state()
        if abs(high_c - previous_high) <= 1e-9 and abs(low_c - previous_low) <= 1e-9:
            return "pending_previous_day_match", "hko-midnight-reset-pending"
        return "pending_reset_unproven", "hko-midnight-reset-pending"

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
        observation_lock = (
            self._hko_observation_lock
            if station.nowcast_source_type == "hko_maxmin_since_midnight"
            else self._observation_lock
        )
        with observation_lock:
            return self._observed_temperature_extremes_so_far_unlocked(
                station,
                target_date=target_date,
                now=now,
            )

    def discard_cached_observations_before_entry(
        self,
        *,
        now: datetime | None = None,
        station_ids: set[str] | None = None,
    ) -> None:
        current = _as_utc(now or _utc_now())
        selected_station_ids = (
            {str(station_id).upper() for station_id in station_ids}
            if station_ids is not None
            else None
        )
        selected_sources = [
            source
            for source in self.sources.values()
            if selected_station_ids is None
            or source.station_id.upper() in selected_station_ids
        ]
        include_hko = selected_station_ids is None or any(
            source.source == "hko-maxmin-since-midnight"
            for source in selected_sources
        )
        include_metar = selected_station_ids is None or any(
            source.source != "hko-maxmin-since-midnight"
            for source in selected_sources
        )

        def discard_selected() -> None:
            with self._cache_lock:
                retained: dict[
                    tuple[str, str],
                    tuple[datetime, StationNowcastObservation],
                ] = {}
                for cache_key, cached in self._cache.items():
                    if (
                        selected_station_ids is not None
                        and cache_key[0].upper() not in selected_station_ids
                    ):
                        retained[cache_key] = cached
                        continue
                    source = self.sources.get(cache_key[0])
                    floor_seconds = (
                        self._source_min_real_request_interval_seconds(source)
                        if source is not None
                        else 0
                    )
                    cached_at, _observation = cached
                    if (
                        floor_seconds > 0
                        and (current - cached_at).total_seconds() <= floor_seconds
                    ):
                        retained[cache_key] = cached
                self._cache = retained

            if include_metar:
                bulk_cached = self._awc_metar_bulk_cache
                if (
                    bulk_cached is not None
                    and (current - bulk_cached.cached_at).total_seconds()
                    > AWC_METAR_MIN_REAL_REQUEST_INTERVAL_SECONDS
                ):
                    self._awc_metar_bulk_cache = None

        if include_metar and include_hko:
            with self._observation_lock, self._hko_observation_lock:
                discard_selected()
        elif include_metar:
            with self._observation_lock:
                discard_selected()
        elif include_hko:
            with self._hko_observation_lock:
                discard_selected()

    def _observed_temperature_extremes_so_far_unlocked(
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

        target_date_blocker = _target_date_unavailable_reason(
            station.timezone,
            target_date,
            current,
            freshness_seconds=self._source_max_observation_age_seconds(source),
        )
        if target_date_blocker:
            return self._unavailable(station, target_date_blocker, source)

        cache_key = (station.station_id, target_date.isoformat())
        with self._cache_lock:
            cached = self._cache.get(cache_key)
        cache_miss_reason = "empty-cache"
        provider_floor_seconds = self._source_min_real_request_interval_seconds(source)
        effective_cache_ttl_seconds = max(self.cache_ttl_seconds, provider_floor_seconds)
        if cached is not None and self.cache_ttl_seconds > 0:
            cached_at, observation = cached
            if (current - cached_at).total_seconds() <= effective_cache_ttl_seconds:
                return observation
            cache_miss_reason = "expired-cache"
        elif cached is not None:
            cached_at, observation = cached
            if provider_floor_seconds > 0 and (current - cached_at).total_seconds() <= provider_floor_seconds:
                return observation
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

        with self._cache_lock:
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
        kma_fetch: _KmaMetarFetch | None = None
        awc_observation: StationNowcastObservation | None = None
        if self._kma_metar_enabled(station):
            kma_fetch = self._request_kma_metar(
                station,
                target_date,
                now,
                source,
                cache_miss_reason=cache_miss_reason,
            )
            if kma_fetch.rows and self._kma_recovery_required(station, target_date, kma_fetch.rows):
                bulk_entry = self._fetch_awc_metar_bulk_cache(
                    station,
                    target_date,
                    now,
                    source,
                    cache_miss_reason="kma-history-recovery",
                )
                if not bulk_entry.unavailable_reason:
                    awc_observation = self._parse_payload(
                        bulk_entry.payload,
                        station,
                        target_date,
                        now,
                        source,
                        request_started_at=bulk_entry.requested_at or bulk_entry.cached_at,
                        bot_received_at=bulk_entry.received_at,
                    )

            if kma_fetch.rows:
                kma_observation = self._parse_payload(
                    kma_fetch.rows,
                    station,
                    target_date,
                    now,
                    kma_fetch.source,
                    request_started_at=kma_fetch.requested_at,
                    bot_received_at=kma_fetch.received_at,
                )
                if kma_observation.usable:
                    return kma_observation
                if awc_observation is not None and awc_observation.usable:
                    return awc_observation

        bulk_entry = self._fetch_awc_metar_bulk_cache(
            station,
            target_date,
            now,
            source,
            cache_miss_reason=cache_miss_reason,
        )
        if bulk_entry.unavailable_reason:
            return self._unavailable(station, bulk_entry.unavailable_reason, source)

        return self._parse_payload(
            bulk_entry.payload,
            station,
            target_date,
            now,
            source,
            request_started_at=bulk_entry.requested_at or bulk_entry.cached_at,
            bot_received_at=bulk_entry.received_at,
        )

    def _kma_metar_enabled(self, station: StationMeta) -> bool:
        unavailable_until = self._kma_unavailable_until
        station_unavailable_until = self._kma_station_unavailable_until.get(
            station.station_id.upper()
        )
        checked_at = _as_utc(self.clock())
        return bool(
            self.kma_metar_service_key
            and station.station_id.upper() in self.kma_metar_station_ids
            and (
                unavailable_until is None
                or checked_at >= unavailable_until
            )
            and (
                station_unavailable_until is None
                or checked_at >= station_unavailable_until
            )
        )

    def _kma_recovery_required(
        self,
        station: StationMeta,
        target_date: date,
        rows: list[dict[str, Any]],
    ) -> bool:
        station_state = self._metar_daily_extremes_state.get("stations", {}).get(station.station_id)
        if not isinstance(station_state, dict):
            return True
        day = station_state.get("days", {}).get(target_date.isoformat())
        if not isinstance(day, dict) or not bool(day.get("complete")):
            return True
        last_observed_at = _parse_observation_time(station_state.get("last_observed_at"))
        if last_observed_at is None:
            return True
        new_times = sorted(
            observed_at
            for row in rows
            if str(row.get("icaoId") or "").upper() == station.station_id.upper()
            if (observed_at := _record_observed_at(row)) is not None
            and observed_at > last_observed_at
        )
        timeline = [last_observed_at, *new_times]
        return any(
            (current - previous).total_seconds() > AWC_METAR_MAX_CONTINUITY_GAP_SECONDS
            for previous, current in zip(timeline, timeline[1:])
        )

    def _request_kma_metar(
        self,
        station: StationMeta,
        target_date: date,
        now: datetime,
        fallback_source: StationNowcastSource,
        *,
        cache_miss_reason: str,
    ) -> _KmaMetarFetch:
        source = StationNowcastSource(
            station_id=station.station_id,
            source="kma-aviation-metar",
            source_url=KMA_METAR_SOURCE_URL,
            settlement_source_url=fallback_source.settlement_source_url,
            update_cadence="KMA domestic METAR/SPECI direct API; polled every configured fast interval.",
            note="Official Korean Aviation Meteorological Office METAR for the same ICAO station.",
        )
        response: Any | None = None
        response_received_at: datetime | None = None
        requested_at = _as_utc(self.clock())
        try:
            response = self.http_get(
                source.source_url,
                params={
                    "ServiceKey": self.kma_metar_service_key,
                    "pageNo": 1,
                    "numOfRows": 10,
                    "dataType": "JSON",
                    "icao": station.station_id,
                },
                timeout=min(self.timeout, self.kma_metar_timeout_seconds),
                headers={"User-Agent": "polymarket-weather-bot/nowcast"},
            )
            response_received_at = _as_utc(self.clock())
            response.raise_for_status()
            rows = _kma_metar_rows(response.json(), reference=now)
            if not rows:
                self._kma_station_unavailable_until[station.station_id.upper()] = (
                    response_received_at + timedelta(
                        seconds=self.kma_metar_poll_seconds
                    )
                )
                self._append_request_log(
                    self._request_log_row(
                        requested_at=requested_at,
                        response_received_at=response_received_at,
                        request_mode="kma_metar_fast",
                        station=station,
                        target_date=target_date,
                        source=source,
                        cache_miss_reason=cache_miss_reason,
                        status="malformed_response",
                        status_code=getattr(response, "status_code", None),
                        unavailable_reason="malformed-observation-payload",
                    )
                )
                return _KmaMetarFetch(
                    source,
                    [],
                    requested_at,
                    response_received_at,
                    "malformed-observation-payload",
                )
            self._kma_unavailable_until = None
            self._kma_station_unavailable_until.pop(station.station_id.upper(), None)
            self._append_request_log(
                self._request_log_row(
                    requested_at=requested_at,
                    response_received_at=response_received_at,
                    request_mode="kma_metar_fast",
                    station=station,
                    target_date=target_date,
                    source=source,
                    cache_miss_reason=cache_miss_reason,
                    status="success",
                    status_code=getattr(response, "status_code", None),
                )
            )
            return _KmaMetarFetch(source, rows, requested_at, response_received_at)
        except Exception as exc:  # noqa: BLE001
            response_received_at = response_received_at or _as_utc(self.clock())
            self._kma_unavailable_until = response_received_at + timedelta(
                seconds=self.kma_metar_poll_seconds
            )
            self._append_request_log(
                self._request_log_row(
                    requested_at=requested_at,
                    response_received_at=response_received_at,
                    request_mode="kma_metar_fast",
                    station=station,
                    target_date=target_date,
                    source=source,
                    cache_miss_reason=cache_miss_reason,
                    status="error",
                    status_code=getattr(response, "status_code", None),
                    error=type(exc).__name__,
                )
            )
            return _KmaMetarFetch(
                source,
                [],
                requested_at,
                response_received_at,
                f"nowcast-fetch-error:{type(exc).__name__}",
            )

    def _fetch_awc_metar_bulk_cache(
        self,
        station: StationMeta,
        target_date: date,
        now: datetime,
        source: StationNowcastSource,
        *,
        cache_miss_reason: str,
    ) -> _MetarBulkCacheEntry:
        station_ids = self._awc_metar_bulk_station_ids()
        hours_before_now = AWC_METAR_RECOVERY_LOOKBACK_HOURS
        cached = self._fresh_awc_metar_bulk_cache(now, min_hours_before_now=hours_before_now)
        if cached is not None:
            return cached

        response: Any | None = None
        response_received_at: datetime | None = None
        requested_at = _as_utc(self.clock())
        try:
            response = self.http_get(
                source.source_url,
                params={
                    "ids": ",".join(station_ids),
                    "format": "json",
                    "hours": hours_before_now,
                },
                timeout=self.timeout,
                headers={"User-Agent": "polymarket-weather-bot/nowcast"},
            )
            response_received_at = _as_utc(self.clock())
            if getattr(response, "status_code", 200) == 204:
                entry = _MetarBulkCacheEntry(
                    cached_at=now,
                    payload=None,
                    unavailable_reason="no-observations-returned",
                    hours_before_now=hours_before_now,
                    requested_at=requested_at,
                    received_at=response_received_at,
                )
                self._awc_metar_bulk_cache = entry
                self._append_request_log(
                    self._request_log_bulk_row(
                        requested_at=requested_at,
                        trigger_station=station,
                        target_date=target_date,
                        source=source,
                        station_ids=station_ids,
                        cache_miss_reason=cache_miss_reason,
                        status="no_observations",
                        status_code=204,
                        response_received_at=response_received_at,
                    )
                )
                return entry
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list) and len(payload) >= AWC_METAR_MAX_RESPONSE_ROWS:
                entry = _MetarBulkCacheEntry(
                    cached_at=now,
                    payload=None,
                    unavailable_reason="metar-response-row-limit",
                    hours_before_now=hours_before_now,
                    requested_at=requested_at,
                    received_at=response_received_at,
                )
                self._awc_metar_bulk_cache = entry
                self._append_request_log(
                    self._request_log_bulk_row(
                        requested_at=requested_at,
                        trigger_station=station,
                        target_date=target_date,
                        source=source,
                        station_ids=station_ids,
                        cache_miss_reason=cache_miss_reason,
                        status="truncated_response",
                        status_code=getattr(response, "status_code", None),
                        unavailable_reason=entry.unavailable_reason,
                        response_received_at=response_received_at,
                    )
                )
                return entry
            entry = _MetarBulkCacheEntry(
                cached_at=now,
                payload=payload,
                hours_before_now=hours_before_now,
                requested_at=requested_at,
                received_at=response_received_at,
            )
            self._awc_metar_bulk_cache = entry
            self._append_request_log(
                self._request_log_bulk_row(
                    requested_at=requested_at,
                    trigger_station=station,
                    target_date=target_date,
                    source=source,
                    station_ids=station_ids,
                    cache_miss_reason=cache_miss_reason,
                    status="success",
                    status_code=getattr(response, "status_code", None),
                    response_received_at=response_received_at,
                )
            )
            return entry
        except Exception as exc:  # noqa: BLE001
            response_received_at = response_received_at or _as_utc(self.clock())
            entry = _MetarBulkCacheEntry(
                cached_at=now,
                payload=None,
                unavailable_reason=f"nowcast-fetch-error:{type(exc).__name__}",
                hours_before_now=hours_before_now,
                requested_at=requested_at,
                received_at=response_received_at,
            )
            self._awc_metar_bulk_cache = entry
            self._append_request_log(
                self._request_log_bulk_row(
                    requested_at=requested_at,
                    trigger_station=station,
                    target_date=target_date,
                    source=source,
                    station_ids=station_ids,
                    cache_miss_reason=cache_miss_reason,
                    status="error",
                    status_code=getattr(response, "status_code", None),
                    error=type(exc).__name__,
                    response_received_at=response_received_at,
                )
            )
            return entry

    def _fresh_awc_metar_bulk_cache(self, now: datetime, *, min_hours_before_now: int) -> _MetarBulkCacheEntry | None:
        cached = self._awc_metar_bulk_cache
        effective_cache_ttl_seconds = max(self.cache_ttl_seconds, AWC_METAR_MIN_REAL_REQUEST_INTERVAL_SECONDS)
        if cached is None or effective_cache_ttl_seconds <= 0:
            return None
        if (now - cached.cached_at).total_seconds() <= effective_cache_ttl_seconds:
            if cached.hours_before_now >= min_hours_before_now:
                return cached
        return None

    def _source_min_real_request_interval_seconds(self, source: StationNowcastSource) -> int:
        if self.kma_metar_service_key and source.station_id.upper() in self.kma_metar_station_ids:
            return self.kma_metar_poll_seconds
        if source.source == "aviationweather-metar":
            return AWC_METAR_MIN_REAL_REQUEST_INTERVAL_SECONDS
        if source.source == "hko-maxmin-since-midnight":
            return HKO_MAXMIN_MIN_REAL_REQUEST_INTERVAL_SECONDS
        return 0

    def _source_max_observation_age_seconds(self, source: StationNowcastSource) -> int:
        if source.source == "hko-maxmin-since-midnight":
            return min(self.freshness_seconds, HKO_MAXMIN_MAX_OBSERVATION_AGE_SECONDS)
        return self.freshness_seconds

    def _awc_metar_bulk_station_ids(self) -> list[str]:
        station_ids = {
            source.station_id.upper()
            for source in self.sources.values()
            if source.source == "aviationweather-metar"
        }
        return sorted(station_ids)

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
        response_received_at: datetime | None = None
        requested_at = _as_utc(self.clock())
        try:
            response = self.http_get(
                source.source_url,
                params={},
                timeout=self.timeout,
                headers={"User-Agent": "polymarket-weather-bot/nowcast"},
            )
            response_received_at = _as_utc(self.clock())
            if getattr(response, "status_code", 200) == 204:
                self._append_request_log(
                    self._request_log_row(
                        requested_at=requested_at,
                        response_received_at=response_received_at,
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
            if observation.observed_at is not None:
                observation = replace(
                    observation,
                    request_started_at=requested_at,
                    bot_received_at=response_received_at,
                    source_received_at=None,
                    source_latency_seconds=None,
                    source_latency_status="provider-timestamp-unavailable",
                    bot_detection_latency_seconds=max(
                        0,
                        int((response_received_at - observation.observed_at).total_seconds()),
                    ),
                )
            self._append_request_log(
                self._request_log_row(
                    requested_at=requested_at,
                    response_received_at=response_received_at,
                    station=station,
                    target_date=target_date,
                    source=source,
                    cache_miss_reason=cache_miss_reason,
                    status="success",
                    status_code=getattr(response, "status_code", None),
                    unavailable_reason=observation.unavailable_reason,
                )
            )
            self._log_observation_delivery(station, observation)
            return observation
        except Exception as exc:  # noqa: BLE001
            response_received_at = response_received_at or _as_utc(self.clock())
            self._append_request_log(
                self._request_log_row(
                    requested_at=requested_at,
                    response_received_at=response_received_at,
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
        with self._request_log_lock:
            try:
                self.request_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.request_log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
                    handle.write("\n")
                self._request_log_error = ""
                self._request_log_last_success_at = _as_utc(self.clock())
            except Exception as exc:  # noqa: BLE001
                self._request_log_error = type(exc).__name__

    def request_log_health(self) -> dict[str, Any]:
        with self._request_log_lock:
            return {
                "status": "error" if self._request_log_error else "ok",
                "error": self._request_log_error,
                "path": str(self.request_log_path or ""),
                "last_success_at": _iso_or_empty(self._request_log_last_success_at),
            }

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
        request_mode: str = "station_fetch",
        response_received_at: datetime | None = None,
    ) -> dict[str, Any]:
        return {
            "cache_miss_reason": cache_miss_reason,
            "city": station.city,
            "error": error,
            "requested_at": _iso_or_empty(requested_at),
            "response_received_at": _iso_or_empty(response_received_at),
            "request_duration_seconds": (
                None
                if response_received_at is None
                else round((response_received_at - requested_at).total_seconds(), 3)
            ),
            "request_mode": request_mode,
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
        response_received_at: datetime | None = None,
    ) -> dict[str, Any]:
        return {
            "cache_miss_reason": cache_miss_reason,
            "city": "bulk-metar",
            "error": error,
            "request_mode": "awc_metar_bulk_cache",
            "requested_at": _iso_or_empty(requested_at),
            "response_received_at": _iso_or_empty(response_received_at),
            "request_duration_seconds": (
                None
                if response_received_at is None
                else round((response_received_at - requested_at).total_seconds(), 3)
            ),
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
        *,
        request_started_at: datetime | None = None,
        bot_received_at: datetime | None = None,
    ) -> StationNowcastObservation:
        if not isinstance(payload, list):
            return self._unavailable(station, "malformed-observation-payload", source)

        zone = _zone(station.timezone)
        observations: list[tuple[datetime, float]] = []
        cadence_observed_times: list[datetime] = []
        latest_record: dict[str, Any] | None = None
        latest_record_at: datetime | None = None
        for record in payload:
            if not isinstance(record, dict):
                continue
            record_station_id = str(record.get("icaoId") or record.get("station_id") or "").strip()
            if not record_station_id or record_station_id.upper() != station.station_id.upper():
                continue
            observed_at = _record_observed_at(record)
            temp_c = _extract_temperature_c(record)
            if observed_at is None:
                continue
            cadence_observed_times.append(observed_at)
            if temp_c is None:
                continue
            if observed_at.astimezone(zone).date() == target_date:
                observations.append((observed_at, temp_c))
                if latest_record_at is None or observed_at > latest_record_at:
                    latest_record = record
                    latest_record_at = observed_at

        if not observations:
            return self._unavailable(station, "malformed-observation-payload", source, raw_count=len(payload))

        if any(observed_at > now for observed_at, _temp in observations):
            return self._unavailable(station, "future-observation", source, raw_count=len(observations))

        day = self._accumulate_metar_daily_extremes(station, observations, target_date)
        if day is None:
            return self._unavailable(
                station,
                "metar-daily-extremes-baseline-missing",
                source,
                raw_count=len(observations),
            )
        latest_at = _parse_observation_time(day.get("latest_observed_at"))
        high_at = _parse_observation_time(day.get("high_observed_at"))
        high_last_at = _parse_observation_time(day.get("high_last_observed_at")) or high_at
        high_drop_at = _parse_observation_time(day.get("high_drop_observed_at"))
        low_at = _parse_observation_time(day.get("low_observed_at"))
        low_last_at = _parse_observation_time(day.get("low_last_observed_at")) or low_at
        low_rise_at = _parse_observation_time(day.get("low_rise_observed_at"))
        if latest_at is None or high_at is None or low_at is None:
            return self._unavailable(
                station,
                "malformed-observation-payload",
                source,
                raw_count=len(observations),
            )
        if latest_record_at is None or latest_record_at < latest_at:
            return self._unavailable(
                station,
                "observation-older-than-ledger",
                source,
                raw_count=len(observations),
            )
        high_c = float(day["high_c"])
        low_c = float(day["low_c"])
        latest_temp_c = _extract_temperature_c(latest_record or {})
        latest_dewpoint_c = _extract_dewpoint_c(latest_record or {})
        latest_weather = str((latest_record or {}).get("wxString") or "")
        latest_raw = _raw_observation_text(latest_record or {})
        source_received_at = _record_source_received_at(latest_record or {})
        source_latency_seconds = (
            max(0, int((source_received_at - latest_at).total_seconds()))
            if source_received_at is not None
            else None
        )
        source_latency_status = (
            "measured" if source_received_at is not None else "provider-timestamp-unavailable"
        )
        bot_detection_latency_seconds = (
            max(0, int((bot_received_at - latest_at).total_seconds()))
            if bot_received_at is not None
            else None
        )
        try:
            high_bucket_confirmations = int(day.get("high_bucket_confirmations", 0))
        except (TypeError, ValueError):
            high_bucket_confirmations = 0
        freshness_seconds = max(0, int((now - latest_at).total_seconds()))
        learned_interval_seconds = _learned_observation_interval_seconds(cadence_observed_times)
        next_observation_due_at = (
            latest_at + timedelta(seconds=learned_interval_seconds)
            if learned_interval_seconds is not None
            else None
        )
        observation_due_status = (
            "unknown"
            if next_observation_due_at is None
            else "overdue"
            if now >= next_observation_due_at
            else "current"
        )
        reason = (
            "stale-observation"
            if freshness_seconds > self._source_max_observation_age_seconds(source)
            else ""
        )
        complete = bool(day.get("complete"))
        blocked_reason = str(day.get("blocked_reason") or "")
        observation = StationNowcastObservation(
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
            raw_observation_count=len(observations),
            update_cadence=source.update_cadence,
            observed_low_c=round(low_c, 3),
            low_observed_at=low_at,
            low_last_observed_at=low_last_at,
            low_rise_observed_at=low_rise_at,
            high_bucket_confirmations=high_bucket_confirmations,
            high_last_observed_at=high_last_at,
            high_drop_observed_at=high_drop_at,
            station_local_date=latest_at.astimezone(zone).date().isoformat(),
            station_local_time=latest_at.astimezone(zone).strftime("%H:%M"),
            data_block_reason=blocked_reason,
            daily_extremes_complete=complete,
            daily_extremes_status="complete" if complete else "blocked",
            latest_temp_c=round(latest_temp_c, 3) if latest_temp_c is not None else None,
            latest_dewpoint_c=round(latest_dewpoint_c, 3) if latest_dewpoint_c is not None else None,
            latest_weather=latest_weather,
            latest_raw_observation=latest_raw,
            learned_observation_interval_seconds=learned_interval_seconds,
            next_observation_due_at=next_observation_due_at,
            observation_due_status=observation_due_status,
            request_started_at=request_started_at,
            source_received_at=source_received_at,
            bot_received_at=bot_received_at,
            source_latency_seconds=source_latency_seconds,
            source_latency_status=source_latency_status,
            bot_detection_latency_seconds=bot_detection_latency_seconds,
        )
        self._log_observation_delivery(station, observation)
        return observation

    def _log_observation_delivery(
        self,
        station: StationMeta,
        observation: StationNowcastObservation,
    ) -> None:
        observed_at = observation.observed_at
        if observed_at is None:
            return
        key = (station.station_id.upper(), observation.source)
        with self._request_log_lock:
            if self._logged_observation_delivery.get(key) == observed_at:
                return
            self._logged_observation_delivery[key] = observed_at
            self._append_request_log({
                "request_mode": "observation_delivery",
                "city": station.city,
                "station_id": station.station_id,
                "station_name": station.station_name,
                "timezone": station.timezone,
                "source": observation.source,
                "source_url": observation.source_url,
                "observation_observed_at": _iso_or_empty(observed_at),
                "request_started_at": _iso_or_empty(observation.request_started_at),
                "source_received_at": _iso_or_empty(observation.source_received_at),
                "bot_received_at": _iso_or_empty(observation.bot_received_at),
                "source_latency_seconds": observation.source_latency_seconds,
                "source_latency_status": observation.source_latency_status,
                "bot_detection_latency_seconds": observation.bot_detection_latency_seconds,
                "latest_temp_c": observation.latest_temp_c,
                "observed_high_c": observation.observed_high_c,
                "observed_low_c": observation.observed_low_c,
                "raw_observation": observation.latest_raw_observation,
                "logged_at": _iso_or_empty(_as_utc(self.clock())),
            })

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

            if observed_at > now:
                return self._unavailable(station, "future-observation", source, raw_count=len(rows))

            freshness_seconds = max(0, int((now - observed_at).total_seconds()))
            reason = "stale-observation" if (
                observed_at.astimezone(_zone(station.timezone)).date() != target_date
                or freshness_seconds > self._source_max_observation_age_seconds(source)
            ) else ""
            midnight_reset_status = ""
            data_block_reason = ""
            if not reason:
                midnight_reset_status, data_block_reason = self._validate_hko_rollover(
                    observed_at=observed_at,
                    observed_date=observed_at.astimezone(_zone(station.timezone)).date(),
                    high_c=high_c,
                    low_c=low_c,
                )
                reason = data_block_reason
            local_observed = observed_at.astimezone(_zone(station.timezone))
            hko_day = self._hko_rollover_state.get("days", {}).get(local_observed.date().isoformat(), {})
            return StationNowcastObservation(
                station_id=station.station_id,
                station_name=station.station_name,
                observed_high_c=round(high_c, 3),
                observed_at=observed_at,
                high_observed_at=_parse_observation_time(hko_day.get("high_first_observed_at")),
                source=source.source,
                source_url=source.source_url,
                settlement_source_url=source.settlement_source_url,
                freshness_seconds=freshness_seconds,
                unavailable_reason=reason,
                raw_observation_count=len(rows),
                update_cadence=source.update_cadence,
                observed_low_c=round(low_c, 3),
                low_observed_at=_parse_observation_time(hko_day.get("low_first_observed_at")),
                station_local_date=local_observed.date().isoformat(),
                station_local_time=local_observed.strftime("%H:%M"),
                midnight_reset_status=midnight_reset_status,
                data_block_reason=data_block_reason,
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
            daily_extremes_complete=False,
            daily_extremes_status="unavailable",
        )


def _zone(timezone_name: str) -> ZoneInfo:
    return ZoneInfo(timezone_name if timezone_name and timezone_name != "auto" else "UTC")


def _target_date_unavailable_reason(
    timezone_name: str,
    target_date: date,
    now: datetime,
    *,
    freshness_seconds: int,
) -> str:
    zone = _zone(timezone_name)
    local_now = now.astimezone(zone)
    local_today = local_now.date()
    if target_date == local_today:
        return ""
    if target_date == local_today - timedelta(days=1):
        target_end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=zone)
        if (local_now - target_end).total_seconds() <= freshness_seconds:
            return ""
        return "target-date-post-close-window-expired"
    return "target-date-not-today"
