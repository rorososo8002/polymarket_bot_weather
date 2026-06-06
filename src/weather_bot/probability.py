from __future__ import annotations

import json
import math
import os
import re
import statistics
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .config import Settings
from .models import ParsedWeatherQuestion, WeatherSignal
from .nowcast import StationNowcastObservation
from .stations import STATION_MAP, TRADING_READY_STATION_MAP, StationMeta
from .weather_client import parse_weather_question, temperature_bucket_interval_bounds_f


# ---------------------------------------------------------------------------
# 1) Station mapping
# ---------------------------------------------------------------------------
# Do not trade a weather market until the exact Polymarket settlement source is
# in `stations.py`.  The active registry intentionally contains only verified
# cities from the current weather resolution rules.


# Default model set.  If Open-Meteo changes a model id, override with:
# OPEN_METEO_ENSEMBLE_MODELS=gfs_seamless,ecmwf_ifs025,gem_global
DEFAULT_ENSEMBLE_MODELS = "gfs_seamless,ecmwf_ifs025"
CONCURRENT_RATE_LIMIT_BACKOFF_SECONDS = 15 * 60
FORECAST_READ_TIMEOUT_BACKOFF_SECONDS = 30 * 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _safe_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text}"[:240]


# ---------------------------------------------------------------------------
# 2) Math helpers
# ---------------------------------------------------------------------------

def clamp_probability(x: float) -> float:
    return max(0.0, min(1.0, x))


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def probability_temperature_ge(threshold_f: float, forecast_max_f: float, sigma_f: float = 4.5) -> float:
    z = (threshold_f - forecast_max_f) / sigma_f
    return 1.0 - normal_cdf(z)


def _mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else float("nan")


def _stdev(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) >= 2 else 0.0


def _today_for_timezone(timezone_name: str = "auto", now: datetime | None = None) -> date:
    if timezone_name and timezone_name != "auto":
        try:
            zone = ZoneInfo(timezone_name)
            current = now or datetime.now(zone)
            if current.tzinfo is None:
                current = current.replace(tzinfo=zone)
            else:
                current = current.astimezone(zone)
            return current.date()
        except Exception:
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            offset = _fallback_offset_hours(timezone_name, current)
            if offset is not None:
                zone = timezone(timedelta(hours=offset))
                return current.astimezone(zone).date()
    current = now or datetime.now()
    return current.date()


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    days_ahead = (weekday - first.weekday()) % 7
    return first + timedelta(days=days_ahead + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    days_back = (last.weekday() - weekday) % 7
    return last - timedelta(days=days_back)


def _fallback_offset_hours(timezone_name: str, now: datetime) -> int | None:
    current = now.astimezone(timezone.utc)
    current_date = current.date()
    us_dst_zones = {
        "America/New_York": (-5, -4),
        "America/Chicago": (-6, -5),
        "America/Los_Angeles": (-8, -7),
        "America/Denver": (-7, -6),
        "America/Toronto": (-5, -4),
    }
    if timezone_name in us_dst_zones:
        standard, daylight = us_dst_zones[timezone_name]
        starts = _nth_weekday(current.year, 3, 6, 2)
        ends = _nth_weekday(current.year, 11, 6, 1)
        return daylight if starts <= current_date < ends else standard

    european_dst_zones = {
        "Europe/London": (0, 1),
        "Europe/Paris": (1, 2),
        "Europe/Amsterdam": (1, 2),
        "Europe/Berlin": (1, 2),
        "Europe/Helsinki": (2, 3),
        "Europe/Madrid": (1, 2),
        "Europe/Rome": (1, 2),
        "Europe/Warsaw": (1, 2),
    }
    if timezone_name in european_dst_zones:
        standard, daylight = european_dst_zones[timezone_name]
        starts = _last_weekday(current.year, 3, 6)
        ends = _last_weekday(current.year, 10, 6)
        return daylight if starts <= current_date < ends else standard

    fixed_offsets = {
        "Africa/Johannesburg": 2,
        "America/Argentina/Buenos_Aires": -3,
        "America/Panama": -5,
        "America/Phoenix": -7,
        "Asia/Hong_Kong": 8,
        "Asia/Jerusalem": 2,
        "Asia/Karachi": 5,
        "Asia/Manila": 8,
        "Asia/Riyadh": 3,
        "Asia/Seoul": 9,
        "Asia/Shanghai": 8,
        "Asia/Singapore": 8,
        "Asia/Taipei": 8,
        "Asia/Tokyo": 9,
        "Europe/Istanbul": 3,
        "Europe/Moscow": 3,
        "Pacific/Auckland": 12,
    }
    return fixed_offsets.get(timezone_name)


def _lead_time_days(target: date, timezone_name: str = "auto", now: datetime | None = None) -> float:
    return max(0.0, (target - _today_for_timezone(timezone_name, now)).days)


def dynamic_sigma_f(member_values_f: list[float], lead_days: float, floor_f: float = 1.25, cap_f: float = 8.0) -> float:
    """Blend ensemble spread and time-to-event error.

    The floor prevents fake certainty when all members cluster tightly.
    The lead component acknowledges that day-5 forecasts deserve more error
    budget than same-day forecasts.
    """
    spread = _stdev(member_values_f)
    lead_component = 0.85 + 0.45 * math.sqrt(max(0.0, lead_days))
    sigma = math.sqrt(spread * spread + lead_component * lead_component)
    return max(floor_f, min(cap_f, sigma))


def blend_empirical_and_cdf(empirical_p: float, mean_f: float, threshold_f: float, sigma_f: float, operator: str) -> float:
    """Blend raw ensemble vote with a smooth CDF around the threshold.

    Raw vote is robust when there are many members; CDF reduces jumpiness when
    the threshold is near the ensemble mean or when only a few members are returned.
    """
    if operator == ">=":
        cdf_p = probability_temperature_ge(threshold_f, mean_f, sigma_f)
    else:
        cdf_p = normal_cdf((threshold_f - mean_f) / sigma_f)
    return clamp_probability(0.70 * empirical_p + 0.30 * cdf_p)


def _temperature_bucket_probability(
    parsed: ParsedWeatherQuestion,
    member_values_f: list[float],
    mean_f: float,
    sigma_f: float,
) -> tuple[float, float]:
    """Return a probability and raw ensemble vote for one temperature bucket."""
    if parsed.threshold_f is None or parsed.operator is None:
        raise ValueError("Temperature bucket is missing a parsed threshold.")

    threshold_f = parsed.threshold_f

    if parsed.temperature_bucket in {"exact", "range"}:
        bounds = temperature_bucket_interval_bounds_f(parsed)
        if bounds is None:
            raise ValueError("Temperature bucket is missing a parsed interval.")
        lower_f, upper_f = bounds.as_tuple()
        votes = [bounds.contains_f(value) for value in member_values_f]
        cdf_p = normal_cdf((upper_f - mean_f) / sigma_f) - normal_cdf((lower_f - mean_f) / sigma_f)
    elif parsed.temperature_bucket == "lower_tail":
        bounds = temperature_bucket_interval_bounds_f(parsed)
        if bounds is None:
            raise ValueError("Lower-tail temperature bucket is missing a parsed interval.")
        _lower_f, upper_f = bounds.as_tuple()
        votes = [bounds.contains_f(value) for value in member_values_f]
        cdf_p = normal_cdf((upper_f - mean_f) / sigma_f)
    elif parsed.temperature_bucket == "upper_tail":
        bounds = temperature_bucket_interval_bounds_f(parsed)
        if bounds is None:
            raise ValueError("Upper-tail temperature bucket is missing a parsed interval.")
        lower_f, _upper_f = bounds.as_tuple()
        votes = [bounds.contains_f(value) for value in member_values_f]
        cdf_p = 1.0 - normal_cdf((lower_f - mean_f) / sigma_f)
    elif parsed.operator == ">=":
        votes = [value >= threshold_f for value in member_values_f]
        cdf_p = probability_temperature_ge(threshold_f, mean_f, sigma_f)
    else:
        votes = [value <= threshold_f for value in member_values_f]
        cdf_p = normal_cdf((threshold_f - mean_f) / sigma_f)

    empirical_p = sum(votes) / len(votes)
    return clamp_probability(0.70 * empirical_p + 0.30 * cdf_p), empirical_p


# ---------------------------------------------------------------------------
# 3) Bias correction
# ---------------------------------------------------------------------------
# Bias is additive in Fahrenheit: corrected_forecast = raw_forecast - bias.
# Example: if GFS/ECMWF at RKSI usually over-forecasts high temp by +1.2F,
# set {"RKSI": {"temperature_2m_max": 1.2}}.

DEFAULT_BIAS_F: dict[str, dict[str, float]] = {
    # Keep defaults conservative/neutral until you have backtest evidence.
    "KLGA": {"temperature_2m_max": 0.0, "temperature_2m_min": 0.0},
    "RKSI": {"temperature_2m_max": 0.0, "temperature_2m_min": 0.0},
    "EGLC": {"temperature_2m_max": 0.0, "temperature_2m_min": 0.0},
}


class WeatherBiasLoadError(ValueError):
    """Raised when an explicit WEATHER_BIAS_JSON file cannot be trusted."""


class OpenMeteoRateLimitCooldown(RuntimeError):
    """Raised when a persisted Open-Meteo daily limit block prevents a request."""


class OpenMeteoTemporaryForecastCooldown(RuntimeError):
    """Raised when a recent transient forecast failure prevents a duplicate request."""


def _read_explicit_bias_json(path: str) -> dict[str, dict[str, float]]:
    try:
        raw_text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise WeatherBiasLoadError(
            f"WEATHER_BIAS_JSON points to unreadable bias file {path}: {_safe_error(exc)}"
        ) from exc

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise WeatherBiasLoadError(
            f"WEATHER_BIAS_JSON contains invalid JSON in {path}: {_safe_error(exc)}"
        ) from exc

    if not isinstance(raw, dict):
        raise WeatherBiasLoadError(
            f"WEATHER_BIAS_JSON file {path} must contain a JSON object mapping station IDs to variable bias values."
        )

    parsed: dict[str, dict[str, float]] = {}
    for station_id, variables in raw.items():
        if not isinstance(station_id, str) or not station_id.strip():
            raise WeatherBiasLoadError(
                f"WEATHER_BIAS_JSON file {path!r} contains an invalid station ID {station_id!r}."
            )
        if not isinstance(variables, dict):
            raise WeatherBiasLoadError(
                f"WEATHER_BIAS_JSON station {station_id!r} must map to a JSON object of variable bias values."
            )
        parsed_variables: dict[str, float] = {}
        for variable, value in variables.items():
            if not isinstance(variable, str) or not variable.strip():
                raise WeatherBiasLoadError(
                    f"WEATHER_BIAS_JSON station {station_id!r} contains an invalid variable name {variable!r}."
                )
            try:
                numeric_value = float(value)
            except (TypeError, ValueError) as exc:
                raise WeatherBiasLoadError(
                    f"WEATHER_BIAS_JSON station {station_id!r} variable {variable!r} must be numeric; got {value!r}."
                ) from exc
            if not math.isfinite(numeric_value):
                raise WeatherBiasLoadError(
                    f"WEATHER_BIAS_JSON station {station_id!r} variable {variable!r} must be finite; got {value!r}."
                )
            parsed_variables[variable] = numeric_value
        parsed[station_id] = parsed_variables
    return parsed


def load_bias_table(path: str | Path | None = None) -> dict[str, dict[str, float]]:
    table = {k: dict(v) for k, v in DEFAULT_BIAS_F.items()}
    bias_path = os.getenv("WEATHER_BIAS_JSON", "").strip() if path is None else str(path).strip()
    if not bias_path:
        return table
    raw = _read_explicit_bias_json(bias_path)
    for station_id, variables in raw.items():
        table.setdefault(station_id, {}).update(variables)
    return table


def bias_for(station: StationMeta, variable: str) -> float:
    return float(load_bias_table().get(station.station_id, {}).get(variable, 0.0))


# ---------------------------------------------------------------------------
# 4) Open-Meteo Ensemble client and parser
# ---------------------------------------------------------------------------

class OpenMeteoEnsembleClient:
    _request_throttle_lock = threading.Lock()
    _request_throttle_last_finished_at: datetime | None = None

    def __init__(
        self,
        timeout: float = 20.0,
        cache_path: str | Path | None = None,
        cache_ttl_seconds: int = 2400,
        request_log_path: str | Path | None = None,
        rate_limit_state_path: str | Path | None = None,
        request_min_interval_seconds: int = 0,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.timeout = timeout
        self.base_url = os.getenv("OPEN_METEO_ENSEMBLE_BASE", "https://ensemble-api.open-meteo.com/v1/ensemble")
        self.models = os.getenv("OPEN_METEO_ENSEMBLE_MODELS", DEFAULT_ENSEMBLE_MODELS)
        self.cache_path = Path(cache_path) if cache_path else None
        self.request_log_path = Path(request_log_path) if request_log_path else None
        self.rate_limit_state_path = Path(rate_limit_state_path) if rate_limit_state_path else None
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.request_min_interval_seconds = max(0, int(request_min_interval_seconds))
        self._sleep = sleep or time.sleep
        self.disabled_reason = ""
        self._cache: dict[str, dict[str, Any]] = {}
        self._last_attempt_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_failure_reason = ""
        self._persistence_error = ""
        self._request_log_error = ""
        self._last_cache_miss_reason = ""
        self._latest_cache_created_at: datetime | None = None
        self._rate_limit_state_error = ""
        self._rate_limit_blocked_until: datetime | None = None
        self._rate_limit_kind = ""
        self._temporary_failure_blocked_until_by_cache_key: dict[str, datetime] = {}
        self._temporary_failure_reason_by_cache_key: dict[str, str] = {}
        self._temporary_failure_last_reason = ""
        self._request_throttle_last_wait_seconds = 0.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenMeteoEnsembleClient":
        cache_path = settings.forecast_cache_path or str(Path(settings.state_path).with_name("forecast_cache.json"))
        request_log_path = settings.forecast_request_log_path or str(
            Path(settings.state_path).with_name("forecast_request_log.jsonl")
        )
        rate_limit_state_path = settings.forecast_rate_limit_state_path or str(
            Path(settings.state_path).with_name("forecast_rate_limit_state.json")
        )
        return cls(
            cache_path=cache_path,
            cache_ttl_seconds=settings.forecast_cache_ttl_seconds,
            request_log_path=request_log_path,
            rate_limit_state_path=rate_limit_state_path,
            request_min_interval_seconds=settings.forecast_request_min_interval_seconds,
        )

    @classmethod
    def reset_request_throttle_for_tests(cls) -> None:
        with cls._request_throttle_lock:
            cls._request_throttle_last_finished_at = None

    def _cache_key(self, latitude: float, longitude: float, timezone: str, forecast_days: int) -> str:
        return "|".join([
            f"{round(latitude, 4):.4f}",
            f"{round(longitude, 4):.4f}",
            timezone,
            str(int(forecast_days)),
            self.models,
        ])

    @staticmethod
    def _created_at(entry: dict[str, Any]) -> datetime | None:
        try:
            created_at = datetime.fromisoformat(str(entry.get("created_at", "")).replace("Z", "+00:00"))
        except ValueError:
            return None
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return created_at.astimezone(timezone.utc)

    def _remember_cached_data(self, cache_key: str, data: dict[str, Any], created_at: datetime) -> None:
        self._cache[cache_key] = {
            "created_at": _utc_iso(created_at),
            "data": data,
        }
        if self._latest_cache_created_at is None or created_at > self._latest_cache_created_at:
            self._latest_cache_created_at = created_at
        if self._last_success_at is None or created_at > self._last_success_at:
            self._last_success_at = created_at

    def _fresh_entry_data(self, entry: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        if self.cache_ttl_seconds <= 0:
            return None
        created_at = self._created_at(entry)
        if created_at is None:
            return None
        age_seconds = (now - created_at).total_seconds()
        if age_seconds > self.cache_ttl_seconds:
            return None
        data = entry.get("data")
        return data if isinstance(data, dict) else None

    def _fresh_cached_data(self, cache_key: str) -> dict[str, Any] | None:
        now = _utc_now()
        memory_miss_reason = ""
        memory_entry = self._cache.get(cache_key)
        if isinstance(memory_entry, dict):
            data = self._fresh_entry_data(memory_entry, now)
            if data is not None:
                self._last_cache_miss_reason = ""
                return data
            self._cache.pop(cache_key, None)
            memory_miss_reason = "memory-cache-stale-or-invalid"
        if self.cache_ttl_seconds <= 0:
            self._last_cache_miss_reason = "cache-disabled"
            return None
        if self.cache_path is None:
            self._last_cache_miss_reason = memory_miss_reason or "cache-not-configured"
            return None
        if not self.cache_path.exists():
            self._last_cache_miss_reason = memory_miss_reason or "disk-cache-missing"
            return None
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                self._last_cache_miss_reason = "disk-cache-invalid"
                return None
            entry = raw.get(cache_key)
            if not isinstance(entry, dict):
                self._last_cache_miss_reason = memory_miss_reason or "disk-entry-missing"
                return None
            data = self._fresh_entry_data(entry, now)
            created_at = self._created_at(entry)
            if data is not None and created_at is not None:
                self._remember_cached_data(cache_key, data, created_at)
                self._last_cache_miss_reason = ""
                return data
            self._last_cache_miss_reason = memory_miss_reason or "disk-entry-stale-or-invalid"
        except Exception as exc:
            self._persistence_error = _safe_error(exc)
            self._last_cache_miss_reason = "disk-cache-read-error"
            return None
        return None

    def _clear_temporary_failure(self, cache_key: str) -> None:
        self._temporary_failure_blocked_until_by_cache_key.pop(cache_key, None)
        self._temporary_failure_reason_by_cache_key.pop(cache_key, None)
        if not self._temporary_failure_blocked_until_by_cache_key:
            self._temporary_failure_last_reason = ""

    def _remember_temporary_failure(self, cache_key: str, reason: str, attempted_at: datetime) -> datetime:
        blocked_until = attempted_at.astimezone(timezone.utc) + timedelta(seconds=FORECAST_READ_TIMEOUT_BACKOFF_SECONDS)
        self._temporary_failure_blocked_until_by_cache_key[cache_key] = blocked_until
        self._temporary_failure_reason_by_cache_key[cache_key] = reason
        self._temporary_failure_last_reason = (
            f"Open-Meteo forecast temporarily unavailable until {_utc_iso(blocked_until)}: {reason[:160]}"
        )
        return blocked_until

    def _temporary_failure_cooldown_reason(self, cache_key: str) -> str:
        blocked_until = self._temporary_failure_blocked_until_by_cache_key.get(cache_key)
        if blocked_until is None:
            return ""
        if blocked_until <= _utc_now():
            self._clear_temporary_failure(cache_key)
            return ""
        reason = self._temporary_failure_reason_by_cache_key.get(cache_key, "recent forecast request failed")
        self._temporary_failure_last_reason = (
            f"Open-Meteo forecast temporarily unavailable until {_utc_iso(blocked_until)}: {reason[:160]}"
        )
        return self._temporary_failure_last_reason

    def _active_temporary_failure_count(self) -> int:
        current = _utc_now()
        for cache_key, blocked_until in list(self._temporary_failure_blocked_until_by_cache_key.items()):
            if blocked_until <= current:
                self._clear_temporary_failure(cache_key)
        return len(self._temporary_failure_blocked_until_by_cache_key)

    def _store_cached_data(self, cache_key: str, data: dict[str, Any]) -> None:
        created_at = _utc_now()
        self._remember_cached_data(cache_key, data, created_at)
        self._clear_temporary_failure(cache_key)
        self._last_failure_reason = ""
        if self.cache_ttl_seconds <= 0 or self.cache_path is None:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            raw: dict[str, Any] = {}
            if self.cache_path.exists():
                try:
                    loaded = json.loads(self.cache_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        raw = loaded
                except Exception as exc:
                    self._persistence_error = _safe_error(exc)
                    raw = {}
            raw[cache_key] = {
                "created_at": _utc_iso(created_at),
                "data": data,
            }
            self.cache_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            self._persistence_error = ""
        except Exception as exc:
            self._persistence_error = _safe_error(exc)

    def _append_request_log(self, row: dict[str, Any]) -> None:
        if self.request_log_path is None:
            return
        try:
            self.request_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.request_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
            self._request_log_error = ""
        except Exception as exc:
            self._request_log_error = _safe_error(exc)

    def _wait_for_request_slot_locked(self) -> None:
        self._request_throttle_last_wait_seconds = 0.0
        if self.request_min_interval_seconds <= 0:
            return
        last_finished_at = type(self)._request_throttle_last_finished_at
        if last_finished_at is None:
            return
        elapsed_seconds = (_utc_now() - last_finished_at).total_seconds()
        wait_seconds = self.request_min_interval_seconds - elapsed_seconds
        if wait_seconds <= 0:
            return
        self._request_throttle_last_wait_seconds = wait_seconds
        self._sleep(wait_seconds)

    def _get_with_request_throttle(self, *, params: dict[str, Any]) -> tuple[datetime, requests.Response]:
        with type(self)._request_throttle_lock:
            self._wait_for_request_slot_locked()
            attempted_at = _utc_now()
            self._last_attempt_at = attempted_at
            try:
                resp = requests.get(self.base_url, params=params, timeout=self.timeout)
            finally:
                type(self)._request_throttle_last_finished_at = _utc_now()
            return attempted_at, resp

    def _parse_rate_limit_blocked_until(self, value: Any) -> datetime:
        try:
            blocked_until = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid blocked_until {value!r}") from exc
        if blocked_until.tzinfo is None:
            blocked_until = blocked_until.replace(tzinfo=timezone.utc)
        return blocked_until.astimezone(timezone.utc)

    @staticmethod
    def _next_rate_limit_reset(now: datetime) -> datetime:
        current = now.astimezone(timezone.utc)
        next_day = current.date() + timedelta(days=1)
        return datetime(next_day.year, next_day.month, next_day.day, 0, 15, tzinfo=timezone.utc)

    @staticmethod
    def _rate_limit_kind_from_body(body: str) -> str:
        lowered = body.lower()
        if "too many concurrent requests" in lowered or "concurrent" in lowered:
            return "concurrent"
        return "daily"

    def _rate_limit_blocked_until_for_kind(self, kind: str, attempted_at: datetime) -> datetime:
        if kind == "concurrent":
            return attempted_at.astimezone(timezone.utc) + timedelta(seconds=CONCURRENT_RATE_LIMIT_BACKOFF_SECONDS)
        return self._next_rate_limit_reset(attempted_at)

    def _blocked_until_from_rate_limit_state(self, raw: dict[str, Any]) -> tuple[datetime, str]:
        reason = str(raw.get("reason") or "").strip()
        kind = str(raw.get("kind") or "").strip() or self._rate_limit_kind_from_body(reason)
        blocked_until = self._parse_rate_limit_blocked_until(raw.get("blocked_until"))
        if kind == "concurrent" and not raw.get("kind") and raw.get("last_rate_limited_at"):
            attempted_at = self._parse_rate_limit_blocked_until(raw.get("last_rate_limited_at"))
            blocked_until = min(blocked_until, self._rate_limit_blocked_until_for_kind(kind, attempted_at))
        return blocked_until, kind

    def _persist_rate_limit_cooldown(self, reason: str, attempted_at: datetime, kind: str = "daily") -> datetime | None:
        blocked_until = self._rate_limit_blocked_until_for_kind(kind, attempted_at)
        self._rate_limit_blocked_until = blocked_until
        self._rate_limit_kind = kind
        if self.rate_limit_state_path is None:
            return blocked_until
        cooldown_seconds = max(0, int((blocked_until - attempted_at.astimezone(timezone.utc)).total_seconds()))
        row = {
            "blocked_until": _utc_iso(blocked_until),
            "cooldown_seconds": cooldown_seconds,
            "kind": kind,
            "last_rate_limited_at": _utc_iso(attempted_at),
            "reason": reason,
            "status_code": 429,
        }
        try:
            self.rate_limit_state_path.parent.mkdir(parents=True, exist_ok=True)
            self.rate_limit_state_path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
            self._rate_limit_state_error = ""
        except Exception as exc:
            self._rate_limit_state_error = _safe_error(exc)
        return blocked_until

    def _rate_limit_cooldown_reason(self) -> str:
        if self.rate_limit_state_path is None or not self.rate_limit_state_path.exists():
            if self._rate_limit_blocked_until is not None and self._rate_limit_blocked_until > _utc_now():
                reason = self._last_failure_reason or "Open-Meteo rate limited"
                if self._rate_limit_kind == "concurrent":
                    return (
                        f"Open-Meteo concurrent request cooldown until "
                        f"{_utc_iso(self._rate_limit_blocked_until)}: {reason[:160]}"
                    )
                return f"Open-Meteo rate limited until {_utc_iso(self._rate_limit_blocked_until)}: {reason[:160]}"
            self._rate_limit_blocked_until = None
            self._rate_limit_kind = ""
            return ""
        try:
            raw = json.loads(self.rate_limit_state_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("rate limit state must be a JSON object")
            blocked_until, kind = self._blocked_until_from_rate_limit_state(raw)
        except Exception as exc:
            self._rate_limit_state_error = _safe_error(exc)
            return f"Open-Meteo rate-limit state is invalid: {self._rate_limit_state_error}"

        self._rate_limit_state_error = ""
        self._rate_limit_blocked_until = blocked_until
        self._rate_limit_kind = kind
        if blocked_until <= _utc_now():
            self._rate_limit_blocked_until = None
            self._rate_limit_kind = ""
            return ""
        reason = str(raw.get("reason") or "Open-Meteo rate limited").strip()
        if kind == "concurrent":
            return f"Open-Meteo concurrent request cooldown until {_utc_iso(blocked_until)}: {reason[:160]}"
        return f"Open-Meteo rate limited until {_utc_iso(blocked_until)}: {reason[:160]}"

    def _request_log_row(
        self,
        *,
        attempted_at: datetime,
        cache_key: str,
        latitude: float,
        longitude: float,
        timezone_name: str,
        forecast_days: int,
        status: str,
        status_code: int | None = None,
        error: str = "",
        cache_miss_reason: str = "",
        city: str = "",
        station_id: str = "",
        station_name: str = "",
        rate_limit_blocked_until: datetime | None = None,
        rate_limit_kind: str = "",
        temporary_failure_blocked_until: datetime | None = None,
        temporary_failure_kind: str = "",
    ) -> dict[str, Any]:
        return {
            "attempted_at": _utc_iso(attempted_at),
            "base_url": self.base_url,
            "cache_miss_reason": cache_miss_reason,
            "cache_key": cache_key,
            "city": city,
            "forecast_days": int(forecast_days),
            "latitude": round(float(latitude), 4),
            "longitude": round(float(longitude), 4),
            "models": self.models,
            "station_id": station_id,
            "station_name": station_name,
            "status": status,
            "status_code": status_code,
            "timezone": timezone_name,
            "error": error,
            "rate_limit_blocked_until": _utc_iso(rate_limit_blocked_until) if rate_limit_blocked_until else "",
            "rate_limit_kind": rate_limit_kind,
            "temporary_failure_blocked_until": (
                _utc_iso(temporary_failure_blocked_until) if temporary_failure_blocked_until else ""
            ),
            "temporary_failure_kind": temporary_failure_kind,
        }

    def health_snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        current = (now or _utc_now()).astimezone(timezone.utc)
        temporary_failure_count = self._active_temporary_failure_count()
        cache_age_seconds: int | None = None
        if self._latest_cache_created_at is not None:
            cache_age_seconds = max(0, int((current - self._latest_cache_created_at).total_seconds()))
        stale = self._last_success_at is None
        if cache_age_seconds is not None and self.cache_ttl_seconds > 0:
            stale = cache_age_seconds > self.cache_ttl_seconds
        return {
            "last_attempt_at": _utc_iso(self._last_attempt_at) if self._last_attempt_at is not None else "",
            "last_success_at": _utc_iso(self._last_success_at) if self._last_success_at is not None else "",
            "last_failure_reason": self._last_failure_reason,
            "cache_age_seconds": cache_age_seconds,
            "stale": stale,
            "persistence_error": self._persistence_error,
            "request_log_path": str(self.request_log_path or ""),
            "request_log_error": self._request_log_error,
            "last_cache_miss_reason": self._last_cache_miss_reason,
            "rate_limit_state_path": str(self.rate_limit_state_path or ""),
            "rate_limit_state_error": self._rate_limit_state_error,
            "rate_limit_blocked_until": (
                _utc_iso(self._rate_limit_blocked_until) if self._rate_limit_blocked_until is not None else ""
            ),
            "rate_limit_kind": self._rate_limit_kind,
            "temporary_failure_count": temporary_failure_count,
            "temporary_failure_last_reason": self._temporary_failure_last_reason if temporary_failure_count else "",
            "request_min_interval_seconds": self.request_min_interval_seconds,
            "request_throttle_last_finished_at": (
                _utc_iso(type(self)._request_throttle_last_finished_at)
                if type(self)._request_throttle_last_finished_at is not None
                else ""
            ),
            "request_throttle_last_wait_seconds": int(round(self._request_throttle_last_wait_seconds)),
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception(lambda exc: _should_retry_forecast_exception(exc)),
        reraise=True,
    )
    def forecast_daily_ensemble(
        self,
        latitude: float,
        longitude: float,
        timezone: str = "auto",
        forecast_days: int = 7,
        *,
        city: str = "",
        station_id: str = "",
        station_name: str = "",
    ) -> dict[str, Any]:
        cache_key = self._cache_key(latitude, longitude, timezone, forecast_days)
        cached = self._fresh_cached_data(cache_key)
        if cached is not None:
            return cached
        cache_miss_reason = self._last_cache_miss_reason
        if self.disabled_reason:
            self._last_failure_reason = self.disabled_reason
            raise OpenMeteoRateLimitCooldown(f"ensemble disabled for cycle: {self.disabled_reason}")
        rate_limit_cooldown_reason = self._rate_limit_cooldown_reason()
        if rate_limit_cooldown_reason:
            self._last_failure_reason = rate_limit_cooldown_reason
            if self._rate_limit_kind != "concurrent":
                self.disabled_reason = rate_limit_cooldown_reason
            raise OpenMeteoRateLimitCooldown(rate_limit_cooldown_reason)
        temporary_failure_reason = self._temporary_failure_cooldown_reason(cache_key)
        if temporary_failure_reason:
            self._last_failure_reason = temporary_failure_reason
            raise OpenMeteoTemporaryForecastCooldown(temporary_failure_reason)
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "daily": "temperature_2m_max,temperature_2m_min",
            "models": self.models,
            "temperature_unit": "fahrenheit",
            "timezone": timezone,
            "forecast_days": forecast_days,
        }
        attempted_at = _utc_now()
        resp: requests.Response | None = None
        try:
            attempted_at, resp = self._get_with_request_throttle(params=params)
            resp.raise_for_status()
        except requests.HTTPError as exc:
            attempted_at = self._last_attempt_at or attempted_at
            if _is_rate_limited(exc):
                body = getattr(resp, "text", "") if resp is not None else ""
                body = body or str(exc)
                rate_limit_reason = f"Open-Meteo rate limited: {body[:160]}"
                rate_limit_kind = self._rate_limit_kind_from_body(body)
                blocked_until = self._persist_rate_limit_cooldown(rate_limit_reason, attempted_at, rate_limit_kind)
                reset_text = f" until {_utc_iso(blocked_until)}" if blocked_until is not None else ""
                self._last_failure_reason = f"{rate_limit_reason}{reset_text}"
                if rate_limit_kind != "concurrent":
                    self.disabled_reason = self._last_failure_reason
            else:
                blocked_until = None
                rate_limit_kind = ""
                self._last_failure_reason = _safe_error(exc)
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", getattr(resp, "status_code", None))
            self._append_request_log(
                self._request_log_row(
                    attempted_at=attempted_at,
                    cache_key=cache_key,
                    latitude=latitude,
                    longitude=longitude,
                    timezone_name=timezone,
                    forecast_days=forecast_days,
                    status="http_error",
                    status_code=status_code,
                    error=self._last_failure_reason,
                    cache_miss_reason=cache_miss_reason,
                    city=city,
                    station_id=station_id,
                    station_name=station_name,
                    rate_limit_blocked_until=blocked_until,
                    rate_limit_kind=rate_limit_kind,
                )
            )
            raise
        except requests.ReadTimeout as exc:
            attempted_at = self._last_attempt_at or attempted_at
            timeout_reason = _safe_error(exc)
            blocked_until = self._remember_temporary_failure(cache_key, timeout_reason, attempted_at)
            self._last_failure_reason = self._temporary_failure_last_reason
            self._append_request_log(
                self._request_log_row(
                    attempted_at=attempted_at,
                    cache_key=cache_key,
                    latitude=latitude,
                    longitude=longitude,
                    timezone_name=timezone,
                    forecast_days=forecast_days,
                    status="error",
                    error=self._last_failure_reason,
                    cache_miss_reason=cache_miss_reason,
                    city=city,
                    station_id=station_id,
                    station_name=station_name,
                    temporary_failure_blocked_until=blocked_until,
                    temporary_failure_kind="read_timeout",
                )
            )
            raise
        except Exception as exc:
            attempted_at = self._last_attempt_at or attempted_at
            self._last_failure_reason = _safe_error(exc)
            self._append_request_log(
                self._request_log_row(
                    attempted_at=attempted_at,
                    cache_key=cache_key,
                    latitude=latitude,
                    longitude=longitude,
                    timezone_name=timezone,
                    forecast_days=forecast_days,
                    status="error",
                    error=self._last_failure_reason,
                    cache_miss_reason=cache_miss_reason,
                    city=city,
                    station_id=station_id,
                    station_name=station_name,
                )
            )
            raise
        try:
            data = resp.json()
        except Exception as exc:
            self._last_failure_reason = _safe_error(exc)
            self._append_request_log(
                self._request_log_row(
                    attempted_at=attempted_at,
                    cache_key=cache_key,
                    latitude=latitude,
                    longitude=longitude,
                    timezone_name=timezone,
                    forecast_days=forecast_days,
                    status="json_error",
                    status_code=getattr(resp, "status_code", None),
                    error=self._last_failure_reason,
                    cache_miss_reason=cache_miss_reason,
                    city=city,
                    station_id=station_id,
                    station_name=station_name,
                )
            )
            raise
        self._append_request_log(
            self._request_log_row(
                attempted_at=attempted_at,
                cache_key=cache_key,
                latitude=latitude,
                longitude=longitude,
                timezone_name=timezone,
                forecast_days=forecast_days,
                status="success",
                status_code=getattr(resp, "status_code", None),
                cache_miss_reason=cache_miss_reason,
                city=city,
                station_id=station_id,
                station_name=station_name,
            )
        )
        self._store_cached_data(cache_key, data)
        return data


def _is_rate_limited(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    return isinstance(exc, requests.HTTPError) and getattr(response, "status_code", None) == 429


def _is_rate_limit_cooldown(exc: BaseException) -> bool:
    return isinstance(exc, OpenMeteoRateLimitCooldown)


def _is_temporary_forecast_cooldown(exc: BaseException) -> bool:
    return isinstance(exc, OpenMeteoTemporaryForecastCooldown)


def _is_read_timeout(exc: BaseException) -> bool:
    return isinstance(exc, requests.ReadTimeout)


def _should_retry_forecast_exception(exc: BaseException) -> bool:
    return not (
        _is_rate_limited(exc)
        or _is_rate_limit_cooldown(exc)
        or _is_temporary_forecast_cooldown(exc)
        or _is_read_timeout(exc)
    )


WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _target_date_from_hint(
    parsed: ParsedWeatherQuestion,
    timezone_name: str = "auto",
    now: datetime | None = None,
) -> date:
    today = _today_for_timezone(timezone_name, now)
    hint = (parsed.date_hint or "").lower().strip()
    if hint in {"today", "\uc624\ub298"}:
        return today
    if hint in {"tomorrow", "\ub0b4\uc77c"}:
        return today + timedelta(days=1)
    if hint in WEEKDAY_INDEX:
        days_ahead = (WEEKDAY_INDEX[hint] - today.weekday()) % 7
        return today + timedelta(days=days_ahead)

    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", hint)
    if m:
        month_names = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        month = month_names.index(m.group(1)[:3]) + 1
        day = int(m.group(2))
        candidate = date(today.year, month, day)
        # Weather markets are near-term; if the date already passed, assume next year.
        if candidate < today - timedelta(days=3):
            candidate = date(today.year + 1, month, day)
        return candidate

    return today


def _date_index(daily: dict[str, Any], target: date) -> int:
    times = daily.get("time") or []
    target_s = target.isoformat()
    for idx, value in enumerate(times):
        if str(value) == target_s:
            return idx
    raise ValueError(f"missing exact forecast date for target_date={target_s}")


def _extract_member_values(daily: dict[str, Any], variable: str, idx: int, bias_f: float = 0.0) -> list[float]:
    values: list[float] = []
    for key, series in daily.items():
        if key == "time" or not isinstance(series, list):
            continue
        # Open-Meteo may return the base name and/or model/member-suffixed names.
        # Accept variable, variable_member01, variable_gfs_member02, etc.
        if key == variable or key.startswith(variable + "_") or variable in key:
            if idx < len(series) and series[idx] is not None:
                try:
                    values.append(float(series[idx]) - bias_f)
                except (TypeError, ValueError):
                    continue
    return values


def _station_for(parsed: ParsedWeatherQuestion) -> StationMeta | None:
    if parsed.city:
        return TRADING_READY_STATION_MAP.get(parsed.city.lower())
    return None


def _format_threshold(parsed: ParsedWeatherQuestion) -> str:
    if parsed.threshold_f is None:
        return "unknown"
    if parsed.temperature_bucket == "range":
        if parsed.temperature_range_lower_f is None or parsed.temperature_range_upper_f is None:
            return "unknown-range"
        if (
            parsed.threshold_unit == "C"
            and parsed.temperature_range_lower_original is not None
            and parsed.temperature_range_upper_original is not None
        ):
            return (
                f"{parsed.temperature_range_lower_original:.1f}-{parsed.temperature_range_upper_original:.1f}C/"
                f"{parsed.temperature_range_lower_f:.1f}-{parsed.temperature_range_upper_f:.1f}F"
            )
        return f"{parsed.temperature_range_lower_f:.1f}-{parsed.temperature_range_upper_f:.1f}F"
    if parsed.threshold_unit == "C" and parsed.threshold_original is not None:
        return f"{parsed.threshold_original:.1f}C/{parsed.threshold_f:.1f}F"
    return f"{parsed.threshold_f:.1f}F"


def _temperature_daily_variable(parsed: ParsedWeatherQuestion) -> str:
    return "temperature_2m_min" if parsed.temperature_metric == "min" else "temperature_2m_max"


def _nowcast_threshold_adjustment(
    parsed: ParsedWeatherQuestion,
    forecast_probability: float,
    observed_high_f: float,
) -> tuple[float, str]:
    if parsed.threshold_f is None or parsed.operator is None:
        return forecast_probability, "no-threshold"

    threshold_f = parsed.threshold_f
    if parsed.temperature_bucket == "exact":
        bounds = temperature_bucket_interval_bounds_f(parsed)
        if bounds is None:
            return forecast_probability, "no-threshold"
        _lower_f, upper_f = bounds.as_tuple()
        if observed_high_f >= upper_f:
            return 0.0, "observed-high-above-exact-bucket"
        return forecast_probability, "observed-high-not-decisive"

    if parsed.temperature_bucket == "range":
        bounds = temperature_bucket_interval_bounds_f(parsed)
        if bounds is None:
            return forecast_probability, "no-threshold"
        _lower_f, upper_f = bounds.as_tuple()
        if observed_high_f > upper_f:
            return 0.0, "observed-high-above-range-bucket"
        return forecast_probability, "observed-high-not-decisive"

    if parsed.temperature_bucket == "lower_tail":
        bounds = temperature_bucket_interval_bounds_f(parsed)
        if bounds is None:
            return forecast_probability, "no-threshold"
        _lower_f, upper_f = bounds.as_tuple()
        if observed_high_f >= upper_f:
            return 0.0, "observed-high-above-lower-tail"
        return forecast_probability, "observed-high-not-decisive"

    if parsed.temperature_bucket == "upper_tail":
        bounds = temperature_bucket_interval_bounds_f(parsed)
        if bounds is None:
            return forecast_probability, "no-threshold"
        lower_f, _upper_f = bounds.as_tuple()
        if observed_high_f >= lower_f:
            return 1.0, "observed-high-reached-upper-tail"
        return forecast_probability, "observed-high-not-decisive"

    if parsed.operator == ">=" and observed_high_f >= threshold_f:
        return 1.0, "observed-high-reached-threshold"
    if parsed.operator == "<=" and observed_high_f > threshold_f:
        return 0.0, "observed-high-above-threshold"
    return forecast_probability, "observed-high-not-decisive"


def _nowcast_low_threshold_adjustment(
    parsed: ParsedWeatherQuestion,
    forecast_probability: float,
    observed_low_f: float,
) -> tuple[float, str]:
    if parsed.threshold_f is None or parsed.operator is None:
        return forecast_probability, "no-threshold"

    threshold_f = parsed.threshold_f
    if parsed.temperature_bucket == "exact":
        bounds = temperature_bucket_interval_bounds_f(parsed)
        if bounds is None:
            return forecast_probability, "no-threshold"
        lower_f, _upper_f = bounds.as_tuple()
        if observed_low_f < lower_f:
            return 0.0, "observed-low-below-exact-bucket"
        return forecast_probability, "observed-low-not-decisive"

    if parsed.temperature_bucket == "range":
        bounds = temperature_bucket_interval_bounds_f(parsed)
        if bounds is None:
            return forecast_probability, "no-threshold"
        lower_f, _upper_f = bounds.as_tuple()
        if observed_low_f < lower_f:
            return 0.0, "observed-low-below-range-bucket"
        return forecast_probability, "observed-low-not-decisive"

    if parsed.temperature_bucket == "lower_tail":
        bounds = temperature_bucket_interval_bounds_f(parsed)
        if bounds is None:
            return forecast_probability, "no-threshold"
        _lower_f, upper_f = bounds.as_tuple()
        if observed_low_f <= upper_f:
            return 1.0, "observed-low-reached-lower-tail"
        return forecast_probability, "observed-low-not-decisive"

    if parsed.temperature_bucket == "upper_tail":
        bounds = temperature_bucket_interval_bounds_f(parsed)
        if bounds is None:
            return forecast_probability, "no-threshold"
        lower_f, _upper_f = bounds.as_tuple()
        if observed_low_f < lower_f:
            return 0.0, "observed-low-below-upper-tail"
        return forecast_probability, "observed-low-not-decisive"

    if parsed.operator == "<=" and observed_low_f <= threshold_f:
        return 1.0, "observed-low-reached-threshold"
    if parsed.operator == ">=" and observed_low_f < threshold_f:
        return 0.0, "observed-low-below-threshold"
    return forecast_probability, "observed-low-not-decisive"


def _observed_temperature_extremes(
    observation_provider: Any,
    station: StationMeta,
    *,
    target: date,
    metric: str,
) -> StationNowcastObservation | None:
    if hasattr(observation_provider, "observed_temperature_extremes_so_far"):
        return observation_provider.observed_temperature_extremes_so_far(station, target_date=target)
    if metric == "min" and hasattr(observation_provider, "observed_low_so_far"):
        return observation_provider.observed_low_so_far(station, target_date=target)
    if metric != "min" and hasattr(observation_provider, "observed_high_so_far"):
        return observation_provider.observed_high_so_far(station, target_date=target)
    return None


def _with_temperature_nowcast(
    signal: WeatherSignal,
    parsed: ParsedWeatherQuestion,
    station: StationMeta,
    target: date,
    settings: Settings,
    observation_provider: Any | None,
) -> WeatherSignal:
    if not settings.station_nowcast_enabled:
        return replace(signal, note=f"{signal.note}; evidence=forecast-only; nowcast_unavailable=disabled")
    if observation_provider is None:
        return replace(signal, note=f"{signal.note}; evidence=forecast-only; nowcast_unavailable=provider-not-supplied")

    try:
        observation = _observed_temperature_extremes(
            observation_provider,
            station,
            target=target,
            metric=parsed.temperature_metric,
        )
    except Exception as exc:  # noqa: BLE001
        note = f"{signal.note}; evidence=forecast-only; nowcast_unavailable=provider-error:{type(exc).__name__}"
        return replace(signal, note=note)
    if observation is None:
        reason = "observed-low-provider-not-supplied" if parsed.temperature_metric == "min" else "provider-not-supplied"
        return replace(signal, note=f"{signal.note}; evidence=forecast-only; nowcast_unavailable={reason}")

    payload = observation.to_log_payload()
    observed_value_f = observation.observed_low_f if parsed.temperature_metric == "min" else observation.observed_high_f
    if not observation.usable or observed_value_f is None:
        reason = observation.unavailable_reason or "unknown"
        value_label = "observed_low_c" if parsed.temperature_metric == "min" else "observed_high_c"
        observed_value_c = observation.observed_low_c if parsed.temperature_metric == "min" else observation.observed_high_c
        note = (
            f"{signal.note}; evidence=forecast-only; nowcast_unavailable={reason}; "
            f"nowcast_source={observation.source or 'unmapped'}; "
            f"{value_label}={observed_value_c}"
        )
        return replace(signal, note=note, nowcast=payload)

    if parsed.temperature_metric == "min":
        adjusted_p, adjustment = _nowcast_low_threshold_adjustment(parsed, signal.p_true, observed_value_f)
        observed_label = "observed_low_c"
        observed_value_c = observation.observed_low_c
    else:
        adjusted_p, adjustment = _nowcast_threshold_adjustment(parsed, signal.p_true, observed_value_f)
        observed_label = "observed_high_c"
        observed_value_c = observation.observed_high_c
    confidence = max(signal.confidence, 0.95) if adjusted_p != signal.p_true else signal.confidence
    note = (
        f"{signal.note}; evidence=forecast-plus-nowcast; nowcast_adjustment={adjustment}; "
        f"{observed_label}={observed_value_c:.1f}; "
        f"observed_at={_utc_iso(observation.observed_at)}; "
        f"freshness_seconds={observation.freshness_seconds}; "
        f"nowcast_source={observation.source}"
    )
    return replace(
        signal,
        p_true=clamp_probability(adjusted_p),
        confidence=confidence,
        source=f"{signal.source}+nowcast",
        note=note,
        nowcast=payload,
    )

# ---------------------------------------------------------------------------
# 5) Main probability function used by live_paper_runner.py
# ---------------------------------------------------------------------------

def estimate_weather_probability(
    question: str,
    settings: Settings | None = None,
    client: Any | None = None,
    ensemble_client: OpenMeteoEnsembleClient | None = None,
    observation_provider: Any | None = None,
) -> WeatherSignal:
    """Estimate P(YES) for a Polymarket weather question.

    Temperature model:
    P_yes = 0.70 * ensemble_vote + 0.30 * NormalCDF(mean, dynamic_sigma)
    where each ensemble member is treated as one scenario and station/model bias
    is subtracted before threshold comparison.
    """
    settings = settings or Settings()
    parsed = parse_weather_question(question)
    if parsed.variable != "temperature":
        return WeatherSignal(
            0.5,
            0.0,
            "unsupported-weather-market",
            "Unsupported non-temperature weather market under the temperature-only paper strategy.",
            parsed,
        )
    if parsed.city is None or parsed.latitude is None or parsed.longitude is None:
        return WeatherSignal(0.5, 0.0, "fallback", f"Could not parse city. {parsed.note}", parsed)

    station = _station_for(parsed)
    if station is None:
        return WeatherSignal(
            0.5,
            0.0,
            "unsupported-station",
            f"{parsed.city} is not in the trading-ready Polymarket settlement-station allowlist with stored rule evidence.",
            parsed,
        )

    if parsed.variable == "temperature" and parsed.threshold_f is not None and parsed.operator is not None:
        target = _target_date_from_hint(parsed, timezone_name=station.timezone)
        variable = _temperature_daily_variable(parsed)

        try:
            bias_f = bias_for(station, variable)
            ensemble_client = ensemble_client or OpenMeteoEnsembleClient.from_settings(settings)
            data = ensemble_client.forecast_daily_ensemble(
                station.latitude,
                station.longitude,
                timezone=station.timezone,
                forecast_days=max(3, min(16, int(_lead_time_days(target, timezone_name=station.timezone)) + 2)),
                city=parsed.city or "",
                station_id=station.station_id,
                station_name=station.station_name,
            )
            daily = data.get("daily") or {}
            idx = _date_index(daily, target)
            member_values = _extract_member_values(daily, variable, idx, bias_f=bias_f)
            if len(member_values) < 4:
                raise ValueError(f"Too few ensemble members parsed for {variable}: {len(member_values)}")

            mean_f = _mean(member_values)
            spread_f = _stdev(member_values)
            sigma_f = dynamic_sigma_f(member_values, _lead_time_days(target, timezone_name=station.timezone))
            p, empirical_p = _temperature_bucket_probability(parsed, member_values, mean_f, sigma_f)

            # Confidence: station mapped + enough members + low ambiguity.  Wider spread lowers confidence.
            station_bonus = 0.12
            member_bonus = min(0.18, len(member_values) / 250.0)
            spread_penalty = min(0.20, max(0.0, spread_f - 3.0) / 20.0)
            confidence = clamp_probability(parsed.confidence + station_bonus + member_bonus - spread_penalty)

            date_used = (daily.get("time") or [target.isoformat()])[min(idx, max(0, len(daily.get("time") or []) - 1))]
            note = (
                f"{station.station_name} [{station.station_id}] target_date={date_used}; "
                f"bucket={parsed.temperature_bucket}; {parsed.operator}{_format_threshold(parsed)}; "
                f"members={len(member_values)}; "
                f"vote={empirical_p:.3f}; mean={mean_f:.1f}F; spread={spread_f:.2f}F; "
                f"dynamic_sigma={sigma_f:.2f}F; bias={bias_f:+.2f}F; "
                f"models={ensemble_client.models}. {station.note}"
            )
            signal = WeatherSignal(
                p_true=clamp_probability(p),
                confidence=confidence,
                source="open-meteo-ensemble-station",
                note=note,
                parsed=parsed,
            )
            return _with_temperature_nowcast(signal, parsed, station, target, settings, observation_provider)
        except Exception as exc:  # noqa: BLE001
            return WeatherSignal(
                p_true=0.5,
                confidence=0.0,
                source="forecast-unavailable",
                note=f"Ensemble forecast unavailable: {exc}",
                parsed=parsed,
            )

    return WeatherSignal(0.5, 0.0, "fallback", "Unsupported or weakly parsed weather market.", parsed)
