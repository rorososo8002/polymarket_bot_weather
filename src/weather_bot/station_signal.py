from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .event_dates import event_date_window_from_hint
from .models import ParsedWeatherQuestion, WeatherSignal
from .nowcast import StationNowcastObservation
from .stations import StationMeta, TRADING_READY_STATION_MAP
from .weather_client import parse_weather_question


@dataclass(frozen=True)
class _OfficialStationLock:
    p_true: float
    lock_name: str
    adjustment: str
    entry_fraction: float
    size_reason: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name if timezone_name and timezone_name != "auto" else "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _today_for_timezone(timezone_name: str = "auto", now: datetime | None = None) -> date:
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(_zone(timezone_name)).date()


def _target_date_from_hint(
    parsed: ParsedWeatherQuestion,
    *,
    timezone_name: str = "auto",
    now: datetime | None = None,
) -> date:
    window = event_date_window_from_hint(parsed.date_hint, timezone_name, now=now)
    return window.event_date_local if window is not None else _today_for_timezone(timezone_name, now)


def _station_for(parsed: ParsedWeatherQuestion) -> StationMeta | None:
    if parsed.city:
        return TRADING_READY_STATION_MAP.get(parsed.city.lower())
    return None


def _event_end_utc(target: date, timezone_name: str) -> datetime:
    local_end = datetime.combine(target + timedelta(days=1), datetime_time.min, tzinfo=_zone(timezone_name))
    return local_end.astimezone(timezone.utc)


def _hours_until_event_end(target: date, timezone_name: str, now: datetime | None) -> float:
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (_event_end_utc(target, timezone_name) - current.astimezone(timezone.utc)).total_seconds() / 3600.0


def _whole_celsius_exact_bucket(parsed: ParsedWeatherQuestion) -> int | None:
    if parsed.variable != "temperature":
        return None
    if parsed.temperature_bucket != "exact" or parsed.threshold_unit != "C":
        return None
    if parsed.threshold_original is None:
        return None
    rounded = round(float(parsed.threshold_original))
    if abs(float(parsed.threshold_original) - rounded) > 1e-9:
        return None
    return int(rounded)


def _official_station_exact_lock(
    parsed: ParsedWeatherQuestion,
    observed_value_c: float | None,
    *,
    target: date,
    timezone_name: str,
    settings: Settings,
    now: datetime | None,
) -> _OfficialStationLock | None:
    if not settings.official_nowcast_lock_enabled or observed_value_c is None:
        return None
    bucket_c = _whole_celsius_exact_bucket(parsed)
    if bucket_c is None:
        return None

    lower_c = float(bucket_c)
    upper_c = float(bucket_c + 1)
    hours_to_close = _hours_until_event_end(target, timezone_name, now)
    near_close = 0.0 <= hours_to_close <= settings.official_nowcast_lock_near_close_hours

    if parsed.temperature_metric == "min":
        if observed_value_c < lower_c:
            return _OfficialStationLock(
                p_true=0.0,
                lock_name="strong_no",
                adjustment="official-lock-observed-low-below-source-display-integer",
                entry_fraction=settings.official_nowcast_lock_strong_entry_fraction,
                size_reason=(
                    f"official_nowcast_lock=strong_no; observed_low_c={observed_value_c:.1f} "
                    f"< displayed_bucket_lower_c={lower_c:.1f}"
                ),
            )
        if near_close and lower_c <= observed_value_c < upper_c:
            buffer_c = observed_value_c - lower_c
            return _yes_lock_from_buffer(
                observed_label="observed_low_c",
                observed_value_c=observed_value_c,
                buffer_label="buffer_to_lower_c",
                buffer_c=buffer_c,
                hours_to_close=hours_to_close,
                settings=settings,
            )
        return None

    if observed_value_c >= upper_c:
        return _OfficialStationLock(
            p_true=0.0,
            lock_name="strong_no",
            adjustment="official-lock-observed-high-reached-next-source-display-integer",
            entry_fraction=settings.official_nowcast_lock_strong_entry_fraction,
            size_reason=(
                f"official_nowcast_lock=strong_no; observed_high_c={observed_value_c:.1f} "
                f">= next_displayed_integer_c={upper_c:.1f}"
            ),
        )
    if near_close and lower_c <= observed_value_c < upper_c:
        buffer_c = upper_c - observed_value_c
        return _yes_lock_from_buffer(
            observed_label="observed_high_c",
            observed_value_c=observed_value_c,
            buffer_label="buffer_to_next_integer_c",
            buffer_c=buffer_c,
            hours_to_close=hours_to_close,
            settings=settings,
        )
    return None


def _yes_lock_from_buffer(
    *,
    observed_label: str,
    observed_value_c: float,
    buffer_label: str,
    buffer_c: float,
    hours_to_close: float,
    settings: Settings,
) -> _OfficialStationLock | None:
    if buffer_c >= settings.official_nowcast_lock_yes_strong_buffer_c:
        return _OfficialStationLock(
            p_true=0.985,
            lock_name="strong_yes",
            adjustment="official-lock-near-close-inside-strong-buffer",
            entry_fraction=settings.official_nowcast_lock_strong_entry_fraction,
            size_reason=(
                f"official_nowcast_lock=strong_yes; {observed_label}={observed_value_c:.1f}; "
                f"{buffer_label}={buffer_c:.1f}; hours_to_close={hours_to_close:.2f}"
            ),
        )
    if buffer_c >= settings.official_nowcast_lock_yes_base_buffer_c:
        return _OfficialStationLock(
            p_true=0.94,
            lock_name="base_yes",
            adjustment="official-lock-near-close-inside-base-buffer",
            entry_fraction=settings.official_nowcast_lock_base_entry_fraction,
            size_reason=(
                f"official_nowcast_lock=base_yes; {observed_label}={observed_value_c:.1f}; "
                f"{buffer_label}={buffer_c:.1f}; hours_to_close={hours_to_close:.2f}"
            ),
        )
    return None


def _observed_temperature_extremes(
    observation_provider: Any,
    station: StationMeta,
    *,
    target: date,
    metric: str,
    now: datetime | None,
) -> StationNowcastObservation | None:
    if hasattr(observation_provider, "observed_temperature_extremes_so_far"):
        return observation_provider.observed_temperature_extremes_so_far(station, target_date=target, now=now)
    if metric == "min" and hasattr(observation_provider, "observed_low_so_far"):
        return observation_provider.observed_low_so_far(station, target_date=target, now=now)
    if metric != "min" and hasattr(observation_provider, "observed_high_so_far"):
        return observation_provider.observed_high_so_far(station, target_date=target, now=now)
    return None


def _neutral_signal(parsed: ParsedWeatherQuestion, source: str, note: str) -> WeatherSignal:
    return WeatherSignal(p_true=0.5, confidence=0.0, source=source, note=note, parsed=parsed)


def estimate_station_signal(
    question: str,
    settings: Settings | None = None,
    *,
    observation_provider: Any | None = None,
    now: datetime | None = None,
    **_unused: Any,
) -> WeatherSignal:
    """Build a paper-trading signal from official settlement-station observations only."""

    settings = settings or Settings()
    current = now or _utc_now()
    parsed = parse_weather_question(question)
    if parsed.variable != "temperature":
        return _neutral_signal(
            parsed,
            "unsupported-weather-market",
            "Unsupported non-temperature weather market under the station-only paper strategy.",
        )
    if parsed.city is None:
        return _neutral_signal(parsed, "unsupported-station", f"Could not parse city. {parsed.note}")
    station = _station_for(parsed)
    if station is None:
        return _neutral_signal(
            parsed,
            "unsupported-station",
            f"{parsed.city} is not in the trading-ready Polymarket settlement-station allowlist.",
        )
    if not settings.station_nowcast_enabled:
        return _neutral_signal(parsed, "official-station-disabled", "station nowcast disabled")
    if observation_provider is None:
        return _neutral_signal(
            parsed,
            "official-station-unavailable",
            "official station observation provider not supplied",
        )

    target = _target_date_from_hint(parsed, timezone_name=station.timezone, now=current)
    try:
        observation = _observed_temperature_extremes(
            observation_provider,
            station,
            target=target,
            metric=parsed.temperature_metric,
            now=current,
        )
    except Exception as exc:  # noqa: BLE001
        return _neutral_signal(
            parsed,
            "official-station-unavailable",
            f"official station observation unavailable: {exc.__class__.__name__}",
        )
    if observation is None:
        return _neutral_signal(parsed, "official-station-unavailable", "official station observation missing")

    payload = observation.to_log_payload()
    observed_value_c = observation.observed_low_c if parsed.temperature_metric == "min" else observation.observed_high_c
    observed_label = "observed_low_c" if parsed.temperature_metric == "min" else "observed_high_c"
    base_note = (
        f"{station.station_name} [{station.station_id}] target_date={target.isoformat()}; "
        f"evidence=official-station; {observed_label}={observed_value_c}; "
        f"observed_at={payload.get('observed_at')}; freshness_seconds={observation.freshness_seconds}; "
        f"nowcast_source={observation.source}"
    )
    if not observation.usable or observed_value_c is None:
        reason = observation.unavailable_reason or "missing-observed-temperature"
        return replace(
            _neutral_signal(parsed, "official-station-unavailable", f"{base_note}; nowcast_unavailable={reason}"),
            nowcast=payload,
        )

    lock = _official_station_exact_lock(
        parsed,
        observed_value_c,
        target=target,
        timezone_name=station.timezone,
        settings=settings,
        now=current,
    )
    if lock is None:
        return WeatherSignal(
            p_true=0.5,
            confidence=0.0,
            source="official-station-neutral",
            note=f"{base_note}; official_station_lock=none",
            parsed=parsed,
            nowcast=payload,
        )

    return WeatherSignal(
        p_true=lock.p_true,
        confidence=1.0,
        source=f"official-station-lock-{lock.lock_name}",
        note=(
            f"{base_note}; station_adjustment={lock.adjustment}; "
            f"{lock.size_reason}; entry_size_fraction_override={lock.entry_fraction:.2f}"
        ),
        parsed=parsed,
        nowcast=payload,
        entry_size_fraction_override=lock.entry_fraction,
        entry_size_reason=lock.size_reason,
    )
