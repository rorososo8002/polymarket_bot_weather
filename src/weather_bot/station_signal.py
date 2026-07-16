from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time as datetime_time, timedelta, timezone
import math
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .edge import ObservationSizingTier, observation_edge_entry_fraction
from .event_dates import event_date_window_from_hint
from .models import ParsedWeatherQuestion, WeatherSignal
from .nowcast import StationNowcastObservation
from .residual_probability import ResidualProbabilityEstimate
from .settlement_precision import SettlementPrecisionProfile, settlement_precision_profile_for_station
from .stations import StationMeta, TRADING_READY_STATION_MAP
from .weather_client import parse_weather_question


UNSTABLE_HIGH_BUCKET_CITY_IDS = frozenset({"ZUUU", "ZUCK", "KBKF"})
MIN_HIGH_BUCKET_CONFIRMATIONS = 2
HIGH_EXACT_ENTRY_MIN_LOCAL_MINUTE = 16 * 60
LOW_EXACT_NO_WEATHER_BUFFER_C = 1.0
LOW_EXACT_NO_WEATHER_PENALTY = 0.08
LOW_EXACT_NO_WEATHER_MAX_FRACTION = 0.05
PRECIPITATION_TOKENS = frozenset({"RA", "DZ", "SHRA", "TSRA", "FZRA", "SN", "SHSN", "TSSN"})


@dataclass(frozen=True)
class _OfficialStationLock:
    p_true: float
    lock_name: str
    adjustment: str
    entry_fraction: float
    size_reason: str


@dataclass(frozen=True)
class _IntradayObservationEdge:
    p_true: float
    signal_name: str
    adjustment: str
    entry_fraction: float
    size_reason: str


@dataclass(frozen=True)
class _LowWeatherRisk:
    selected_probability: float
    conservative_yes_probability: float
    conservative_no_probability: float
    block_reason: str
    note: str


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
    if (
        parsed.temperature_metric == "max"
        and parsed.temperature_bucket == "lower_tail"
        and parsed.threshold_unit == "C"
        and parsed.threshold_original is not None
    ):
        upper_c = float(parsed.threshold_original)
        if observed_value_c > upper_c:
            return _OfficialStationLock(
                p_true=0.0,
                lock_name="strong_no",
                adjustment="official-lock-observed-high-above-lower-tail-threshold",
                entry_fraction=settings.official_nowcast_lock_strong_entry_fraction,
                size_reason=(
                    f"official_nowcast_lock=strong_no; observed_high_c={observed_value_c:.1f} "
                    f"> lower_tail_upper_c={upper_c:.1f}"
                ),
            )
        return None

    bucket_c = _whole_celsius_exact_bucket(parsed)
    if bucket_c is None:
        return None

    lower_c = float(bucket_c)
    upper_c = float(bucket_c + 1)
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
    return None

def _observed_value_in_source_unit(observed_value_c: float, unit: str) -> float | None:
    if unit == "C":
        return observed_value_c
    if unit == "F":
        return observed_value_c * 9.0 / 5.0 + 32.0
    return None


def _exact_source_bucket(
    parsed: ParsedWeatherQuestion,
    precision_profile: SettlementPrecisionProfile,
) -> tuple[float, float] | None:
    if parsed.variable != "temperature" or parsed.temperature_bucket != "exact":
        return None
    if parsed.threshold_original is None or parsed.threshold_unit != precision_profile.unit:
        return None
    rounded = round(float(parsed.threshold_original))
    if abs(float(parsed.threshold_original) - rounded) > 1e-9:
        return None
    return float(rounded), float(rounded + 1)


def _source_threshold(
    parsed: ParsedWeatherQuestion,
    precision_profile: SettlementPrecisionProfile,
) -> float | None:
    if parsed.threshold_original is None or parsed.threshold_unit != precision_profile.unit:
        return None
    return float(parsed.threshold_original)


def _floor_local_minute_30m(now: datetime, timezone_name: str) -> int:
    current = now
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local = current.astimezone(_zone(timezone_name))
    return local.hour * 60 + (local.minute // 30) * 30


def _formation_audit_evidence(
    payload: dict[str, Any],
    *,
    parsed: ParsedWeatherQuestion,
    station: StationMeta,
    target: date,
    now: datetime,
    residual_profile_store: Any | None,
    precision_profile: SettlementPrecisionProfile,
) -> str:
    local = now.astimezone(_zone(station.timezone))
    direction = "low" if parsed.temperature_metric == "min" else "high"
    local_minute = local.hour * 60 + local.minute
    payload.update(
        {
            "station_timezone": station.timezone,
            "target_date_local": target.isoformat(),
            "station_local_date": local.date().isoformat(),
            "station_local_time": local.strftime("%H:%M"),
            "strategy_direction": direction,
            "current_local_minute": local_minute,
        }
    )
    formation_method = getattr(residual_profile_store, "formation_window", None)
    if residual_profile_store is None or not callable(formation_method):
        payload["formation_monitoring_status"] = "not_available"
        return (
            f"station_timezone={station.timezone}; target_date_local={target.isoformat()}; "
            f"station_local_date={local.date().isoformat()}; station_local_time={local.strftime('%H:%M')}; "
            f"strategy_direction={direction}; formation_monitoring_status=not_available"
        )

    window = formation_method(
        station_id=station.station_id,
        month=target.month,
        direction=direction,
    )
    if not isinstance(window, Mapping):
        payload["formation_monitoring_status"] = "missing"
        payload["data_block_reason"] = "formation-window-missing"
        payload["strategy_allowed_reason"] = "verified formation metadata is unavailable"
        return (
            f"station_timezone={station.timezone}; target_date_local={target.isoformat()}; "
            f"station_local_date={local.date().isoformat()}; station_local_time={local.strftime('%H:%M')}; "
            f"strategy_direction={direction}; formation_monitoring_status=missing; "
            "data_block_reason=formation-window-missing; "
            "strategy_allowed_reason=verified formation metadata is unavailable"
        )

    try:
        monitoring_start = int(window["monitoring_start_local_minute"])
    except (KeyError, TypeError, ValueError):
        payload["formation_monitoring_status"] = "missing"
        return "formation_monitoring_status=missing"
    status = "started" if local_minute >= monitoring_start else "before_start"
    high_stats = window.get("first_final_high_local_minute")
    low_stats = window.get("first_final_low_local_minute")
    high_stats = high_stats if isinstance(high_stats, Mapping) else {}
    low_stats = low_stats if isinstance(low_stats, Mapping) else {}
    payload.update(
        {
            "formation_monitoring_status": status,
            "monitoring_start_local_minute": monitoring_start,
            "first_final_high_local_minute_q25": high_stats.get("q25"),
            "first_final_high_local_minute_median": high_stats.get("median"),
            "first_final_high_local_minute_q75": high_stats.get("q75"),
            "first_final_low_local_minute_q25": low_stats.get("q25"),
            "first_final_low_local_minute_median": low_stats.get("median"),
            "first_final_low_local_minute_q75": low_stats.get("q75"),
            "formation_sample_days": window.get("occurrence_sample_days"),
        }
    )
    movement_method = getattr(residual_profile_store, "movement_probability", None)
    movement_probability = None
    if callable(movement_method):
        movement_probability = movement_method(
            station_id=station.station_id,
            month=target.month,
            local_minute=_floor_local_minute_30m(now, station.timezone),
            direction=direction,
            unit=precision_profile.unit,
        )
    payload["remaining_movement_probability"] = movement_probability
    if status == "before_start":
        payload["data_block_reason"] = "formation-monitoring-not-started"
        payload["strategy_allowed_reason"] = "station-local formation monitoring has not started"
    else:
        payload.setdefault("data_block_reason", "")
        payload["strategy_allowed_reason"] = "formation monitoring started; residual probability required"
    return "; ".join(
        [
            f"station_timezone={station.timezone}",
            f"target_date_local={target.isoformat()}",
            f"station_local_date={local.date().isoformat()}",
            f"station_local_time={local.strftime('%H:%M')}",
            f"strategy_direction={direction}",
            f"formation_monitoring_status={status}",
            f"monitoring_start_local_minute={monitoring_start}",
            f"first_final_high_local_minute_q25={high_stats.get('q25')}",
            f"first_final_high_local_minute_median={high_stats.get('median')}",
            f"first_final_high_local_minute_q75={high_stats.get('q75')}",
            f"first_final_low_local_minute_q25={low_stats.get('q25')}",
            f"first_final_low_local_minute_median={low_stats.get('median')}",
            f"first_final_low_local_minute_q75={low_stats.get('q75')}",
            f"remaining_movement_probability={movement_probability}",
            f"data_block_reason={payload.get('data_block_reason', '')}",
            f"strategy_allowed_reason={payload.get('strategy_allowed_reason', '')}",
        ]
    )


def _residual_bucket_request(
    parsed: ParsedWeatherQuestion,
    precision_profile: SettlementPrecisionProfile,
) -> tuple[str, float | None, float | None] | None:
    if parsed.temperature_bucket == "exact":
        bucket = _exact_source_bucket(parsed, precision_profile)
        if bucket is None:
            return None
        lower, upper = bucket
        return "exact", lower, upper

    threshold = _source_threshold(parsed, precision_profile)
    if threshold is None:
        return None
    if parsed.temperature_bucket == "lower_tail":
        return "lower_tail", None, threshold
    if parsed.temperature_bucket == "upper_tail":
        return "upper_tail", threshold, None
    return None


def _residual_tier(
    selected_probability: float,
    *,
    settings: Settings,
) -> ObservationSizingTier | None:
    return observation_edge_entry_fraction(
        selected_probability,
        tier_80_probability=settings.observation_tier_80_probability,
        tier_90_probability=settings.observation_tier_90_probability,
        tier_95_probability=settings.observation_tier_95_probability,
        tier_80_fraction=settings.observation_tier_80_fraction,
        tier_90_fraction=settings.observation_tier_90_fraction,
        tier_95_fraction=settings.observation_tier_95_fraction,
    )


def _selected_residual_side(estimate: ResidualProbabilityEstimate) -> tuple[str, float, float]:
    if estimate.conservative_yes_probability >= estimate.conservative_no_probability:
        return "YES", estimate.conservative_yes_probability, estimate.raw_probability
    return "NO", estimate.conservative_no_probability, 1.0 - estimate.raw_probability


def _payload_float(payload: Mapping[str, Any], key: str) -> float | None:
    try:
        value = float(payload.get(key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _has_precipitation(payload: Mapping[str, Any]) -> bool:
    text = f"{payload.get('latest_weather') or ''} {payload.get('latest_raw_observation') or ''}"
    for token in text.replace("+", " ").replace("-", " ").split():
        normalized = "".join(ch for ch in token.upper() if ch.isalpha())
        if normalized in PRECIPITATION_TOKENS:
            return True
    return False


def _low_exact_no_weather_risk(
    *,
    selected_side: str,
    selected_probability: float,
    conservative_yes_probability: float,
    conservative_no_probability: float,
    direction: str,
    bucket_type: str,
    bucket_lower: float | None,
    observed_value: float,
    payload: Mapping[str, Any],
) -> _LowWeatherRisk | None:
    if selected_side != "NO" or direction != "low" or bucket_type != "exact" or bucket_lower is None:
        return None
    gap_c = observed_value - bucket_lower
    if gap_c <= 0.0 or gap_c > LOW_EXACT_NO_WEATHER_BUFFER_C:
        return None

    dewpoint_c = _payload_float(payload, "latest_dewpoint_c")
    dewpoint_near = (
        dewpoint_c is not None
        and bucket_lower - LOW_EXACT_NO_WEATHER_BUFFER_C
        <= dewpoint_c
        <= bucket_lower + LOW_EXACT_NO_WEATHER_BUFFER_C
    )
    precip = _has_precipitation(payload)
    flags = [f"gap_c={gap_c:.2f}"]
    if dewpoint_near:
        flags.append(f"dewpoint_c={dewpoint_c:.2f}")
    if precip:
        flags.append("precipitation_observed=true")

    if dewpoint_near and precip:
        return _LowWeatherRisk(
            selected_probability=selected_probability,
            conservative_yes_probability=conservative_yes_probability,
            conservative_no_probability=conservative_no_probability,
            block_reason="low-exact-no-rain-dewpoint-risk",
            note="low_weather_risk=blocked; " + "; ".join(flags),
        )
    if not dewpoint_near and not precip:
        return None

    adjusted_no = max(0.0, conservative_no_probability - LOW_EXACT_NO_WEATHER_PENALTY)
    adjusted_yes = min(1.0, conservative_yes_probability + LOW_EXACT_NO_WEATHER_PENALTY)
    return _LowWeatherRisk(
        selected_probability=adjusted_no,
        conservative_yes_probability=adjusted_yes,
        conservative_no_probability=adjusted_no,
        block_reason="",
        note=(
            f"low_weather_risk=penalty; penalty={LOW_EXACT_NO_WEATHER_PENALTY:.2f}; "
            + "; ".join(flags)
        ),
    )


def _residual_neutral_signal(
    parsed: ParsedWeatherQuestion,
    *,
    source: str,
    note: str,
    payload: dict[str, Any],
    settings: Settings,
    precision_profile: SettlementPrecisionProfile,
    estimate: ResidualProbabilityEstimate | None = None,
    selected_probability: float | None = None,
    raw_selected_probability: float | None = None,
) -> WeatherSignal:
    return WeatherSignal(
        p_true=0.5,
        confidence=0.0,
        source=source,
        note=note,
        parsed=parsed,
        nowcast=payload,
        strategy_mode=settings.strategy_mode,
        signal_family="",
        settlement_precision_confidence=precision_profile.confidence,
        raw_probability=estimate.raw_probability if estimate is not None else None,
        conservative_yes_probability=(
            estimate.conservative_yes_probability if estimate is not None else None
        ),
        conservative_no_probability=(
            estimate.conservative_no_probability if estimate is not None else None
        ),
        raw_selected_side_probability=raw_selected_probability,
        selected_side_probability=selected_probability,
        calibration_sample_days=estimate.sample_days if estimate is not None else 0,
        calibration_profile_key=estimate.profile_key if estimate is not None else "",
        calibration_status=estimate.reason_code if estimate is not None else "",
    )


def _residual_observation_edge_signal(
    parsed: ParsedWeatherQuestion,
    observed_value_c: float,
    *,
    station: StationMeta,
    precision_profile: SettlementPrecisionProfile,
    settings: Settings,
    now: datetime,
    target: date,
    base_note: str,
    payload: dict[str, Any],
    residual_profile_store: Any | None,
) -> WeatherSignal | None:
    if not settings.station_residual_probability_enabled or residual_profile_store is None:
        return None
    if settings.strategy_mode not in {"intraday_observation_edge", "hybrid_observation_edge"}:
        return None
    if precision_profile.confidence != "verified":
        return _residual_neutral_signal(
            parsed,
            source="official-station-residual-unavailable",
            note=(
                f"{base_note}; signal_family=intraday_observation_edge; "
                "residual_probability=blocked; settlement precision is not verified"
            ),
            payload=payload,
            settings=settings,
            precision_profile=precision_profile,
        )

    formation_status = str(payload.get("formation_monitoring_status") or "")
    if formation_status in {"missing", "before_start"}:
        reason = (
            "verified formation window is missing"
            if formation_status == "missing"
            else "current station-local time is before the monitoring start"
        )
        return _residual_neutral_signal(
            parsed,
            source="official-station-formation-window",
            note=(
                f"{base_note}; signal_family=intraday_observation_edge; "
                f"formation_monitoring_status={formation_status}; {reason}"
            ),
            payload=payload,
            settings=settings,
            precision_profile=precision_profile,
        )

    bucket = _residual_bucket_request(parsed, precision_profile)
    if bucket is None:
        return None
    bucket_type, bucket_lower, bucket_upper = bucket
    observed_value = _observed_value_in_source_unit(observed_value_c, precision_profile.unit)
    if observed_value is None:
        return None

    direction = "low" if parsed.temperature_metric == "min" else "high"
    q75_key = f"first_final_{direction}_local_minute_q75"
    try:
        formation_q75 = float(payload[q75_key])
    except (KeyError, TypeError, ValueError):
        formation_q75 = None
    local = now.astimezone(_zone(station.timezone))
    local_minute = local.hour * 60 + local.minute
    exact_bucket_already_impossible = bool(
        bucket_type == "exact"
        and (
            (direction == "high" and bucket_upper is not None and observed_value >= bucket_upper)
            or (direction == "low" and bucket_lower is not None and observed_value < bucket_lower)
        )
    )
    if (
        bucket_type == "exact"
        and formation_q75 is not None
        and local_minute < formation_q75
        and not exact_bucket_already_impossible
    ):
        payload["data_block_reason"] = "formation-q75-not-reached"
        payload["strategy_allowed_reason"] = "exact bucket waits for city-month-direction q75"
        return _residual_neutral_signal(
            parsed,
            source="official-station-formation-q75",
            note=(
                f"{base_note}; signal_family=intraday_observation_edge; "
                f"formation_q75_local_minute={formation_q75:.0f}; "
                f"current_local_minute={local_minute}; exact bucket entry blocked"
            ),
            payload=payload,
            settings=settings,
            precision_profile=precision_profile,
        )
    if (
        bucket_type == "exact"
        and direction == "high"
        and local_minute < HIGH_EXACT_ENTRY_MIN_LOCAL_MINUTE
        and not exact_bucket_already_impossible
    ):
        payload["data_block_reason"] = "high-exact-before-16-local"
        payload["strategy_allowed_reason"] = "blocked until station-local 16:00 for high exact entries"
        return _residual_neutral_signal(
            parsed,
            source="official-station-high-exact-local-time",
            note=(
                f"{base_note}; signal_family=intraday_observation_edge; "
                f"current_local_minute={local_minute}; "
                f"required_local_minute={HIGH_EXACT_ENTRY_MIN_LOCAL_MINUTE}; "
                "exact high entry blocked before station-local 16:00"
            ),
            payload=payload,
            settings=settings,
            precision_profile=precision_profile,
        )
    if bucket_type == "exact" and direction == "high" and not exact_bucket_already_impossible:
        try:
            high_bucket_confirmations = int(payload.get("high_bucket_confirmations", 0))
        except (TypeError, ValueError):
            high_bucket_confirmations = 0
        if high_bucket_confirmations < MIN_HIGH_BUCKET_CONFIRMATIONS:
            payload["data_block_reason"] = "high-bucket-confirmation-pending"
            payload["strategy_allowed_reason"] = "blocked until the current high integer bucket is confirmed twice"
            return _residual_neutral_signal(
                parsed,
                source="official-station-high-bucket-confirmation",
                note=(
                    f"{base_note}; signal_family=intraday_observation_edge; "
                    f"high_bucket_confirmations={high_bucket_confirmations}; "
                    f"required_high_bucket_confirmations={MIN_HIGH_BUCKET_CONFIRMATIONS}; "
                    "exact high bucket entry blocked"
                ),
                payload=payload,
                settings=settings,
                precision_profile=precision_profile,
            )
        if not str(payload.get("high_drop_observed_at") or "").strip():
            payload["data_block_reason"] = "high-exact-drop-not-confirmed"
            payload["strategy_allowed_reason"] = "blocked until a lower observation confirms the high has rolled over"
            return _residual_neutral_signal(
                parsed,
                source="official-station-high-drop-confirmation",
                note=(
                    f"{base_note}; signal_family=intraday_observation_edge; "
                    "high_drop_observed_at missing; exact high bucket entry blocked"
                ),
                payload=payload,
                settings=settings,
                precision_profile=precision_profile,
            )
    if (
        bucket_type == "exact"
        and direction == "low"
        and not exact_bucket_already_impossible
        and not str(payload.get("low_rise_observed_at") or "").strip()
    ):
        payload["data_block_reason"] = "low-exact-rise-not-confirmed"
        payload["strategy_allowed_reason"] = "blocked until a higher observation confirms the low has rolled over"
        return _residual_neutral_signal(
            parsed,
            source="official-station-low-rise-confirmation",
            note=(
                f"{base_note}; signal_family=intraday_observation_edge; "
                "low_rise_observed_at missing; exact low bucket entry blocked"
            ),
            payload=payload,
            settings=settings,
            precision_profile=precision_profile,
        )

    estimate = residual_profile_store.estimate_bucket(
        station_id=station.station_id,
        month=target.month,
        local_minute=_floor_local_minute_30m(now, station.timezone),
        direction=direction,
        observed_extreme=observed_value,
        bucket_type=bucket_type,
        bucket_lower=bucket_lower,
        bucket_upper=bucket_upper,
        unit=precision_profile.unit,
    )
    if not estimate.usable:
        return _residual_neutral_signal(
            parsed,
            source="official-station-residual-unavailable",
            note=(
                f"{base_note}; signal_family=intraday_observation_edge; "
                f"residual_probability=unavailable; calibration_status={estimate.reason_code}; "
                f"{estimate.reason}"
            ),
            payload=payload,
            settings=settings,
            precision_profile=precision_profile,
            estimate=estimate,
        )

    conservative_yes_probability = estimate.conservative_yes_probability
    conservative_no_probability = estimate.conservative_no_probability
    selected_side, selected_probability, raw_selected_probability = _selected_residual_side(estimate)
    low_weather_risk = _low_exact_no_weather_risk(
        selected_side=selected_side,
        selected_probability=selected_probability,
        conservative_yes_probability=conservative_yes_probability,
        conservative_no_probability=conservative_no_probability,
        direction=direction,
        bucket_type=bucket_type,
        bucket_lower=bucket_lower,
        observed_value=observed_value,
        payload=payload,
    )
    if low_weather_risk is not None and low_weather_risk.block_reason:
        payload["data_block_reason"] = low_weather_risk.block_reason
        payload["strategy_allowed_reason"] = "blocked because low exact NO has rain/dewpoint downside risk"
        return _residual_neutral_signal(
            parsed,
            source="official-station-low-weather-risk",
            note=(
                f"{base_note}; signal_family=intraday_observation_edge; "
                f"selected_side={selected_side}; selected_side_probability={selected_probability:.4f}; "
                f"{low_weather_risk.note}"
            ),
            payload=payload,
            settings=settings,
            precision_profile=precision_profile,
            estimate=estimate,
            selected_probability=selected_probability,
            raw_selected_probability=raw_selected_probability,
        )
    if low_weather_risk is not None:
        conservative_yes_probability = low_weather_risk.conservative_yes_probability
        conservative_no_probability = low_weather_risk.conservative_no_probability
        selected_probability = low_weather_risk.selected_probability
        payload["low_weather_risk"] = low_weather_risk.note
    if (
        bucket_type == "exact"
        and direction == "high"
        and station.station_id in UNSTABLE_HIGH_BUCKET_CITY_IDS
        and raw_selected_probability < 1.0
    ):
        payload["data_block_reason"] = "unstable-high-bucket-city-requires-raw-100"
        payload["strategy_allowed_reason"] = "blocked because this city needs raw 100% high-bucket evidence"
        return _residual_neutral_signal(
            parsed,
            source="official-station-unstable-high-bucket-city",
            note=(
                f"{base_note}; signal_family=intraday_observation_edge; "
                f"station_id={station.station_id}; selected_side={selected_side}; "
                f"raw_selected_side_probability={raw_selected_probability:.4f}; "
                "unstable high-bucket city requires raw 100%"
            ),
            payload=payload,
            settings=settings,
            precision_profile=precision_profile,
            estimate=estimate,
            selected_probability=selected_probability,
            raw_selected_probability=raw_selected_probability,
        )
    tier = _residual_tier(
        selected_probability,
        settings=settings,
    )
    if tier is None:
        return _residual_neutral_signal(
            parsed,
            source="official-station-residual-below-tier",
            note=(
                f"{base_note}; signal_family=intraday_observation_edge; "
                f"residual_probability=below observation tier; selected_side={selected_side}; "
                f"selected_side_probability={selected_probability:.4f}; "
                f"calibration_status={estimate.reason_code}"
            ),
            payload=payload,
            settings=settings,
            precision_profile=precision_profile,
            estimate=estimate,
            selected_probability=selected_probability,
            raw_selected_probability=raw_selected_probability,
            )

    if low_weather_risk is not None and tier.entry_fraction is not None:
        tier = replace(
            tier,
            entry_fraction=min(tier.entry_fraction, LOW_EXACT_NO_WEATHER_MAX_FRACTION),
            event_cap_override_fraction=None,
        )

    unit = precision_profile.unit.lower()
    size_reason = (
        f"residual_probability={estimate.reason_code}; selected_side={selected_side}; "
        f"raw_probability={estimate.raw_probability:.4f}; "
        f"conservative_yes_probability={conservative_yes_probability:.4f}; "
        f"conservative_no_probability={conservative_no_probability:.4f}; "
        f"selected_side_probability={selected_probability:.4f}; "
        f"probability_tier={tier.probability_tier}; "
        f"calibration_sample_days={estimate.sample_days}; profile_key={estimate.profile_key}"
    )
    if low_weather_risk is not None:
        size_reason = f"{size_reason}; {low_weather_risk.note}"
    sizing_note = (
        "kelly"
        if tier.entry_fraction is None
        else f"{tier.entry_fraction:.4f}"
    )
    return WeatherSignal(
        p_true=estimate.raw_probability,
        confidence=1.0,
        source=f"official-station-residual-{direction}-{selected_side.lower()}",
        note=(
            f"{base_note}; strategy_mode={settings.strategy_mode}; "
            f"signal_family=intraday_observation_edge; station_adjustment=residual-{direction}; "
            f"observed_extreme_{unit}={observed_value:.2f}; bucket_type={bucket_type}; "
            f"{size_reason}; entry_size_fraction_override={sizing_note}"
        ),
        parsed=parsed,
        nowcast=payload,
        entry_size_fraction_override=tier.entry_fraction,
        entry_size_reason=size_reason,
        strategy_mode=settings.strategy_mode,
        signal_family="intraday_observation_edge",
        settlement_precision_confidence=precision_profile.confidence,
        raw_probability=estimate.raw_probability,
        conservative_yes_probability=conservative_yes_probability,
        conservative_no_probability=conservative_no_probability,
        raw_selected_side_probability=raw_selected_probability,
        selected_side_probability=selected_probability,
        calibration_sample_days=estimate.sample_days,
        calibration_profile_key=estimate.profile_key,
        calibration_status=estimate.reason_code,
        probability_tier=tier.probability_tier,
        event_cap_override_fraction=tier.event_cap_override_fraction,
    )


def _intraday_observation_edge(
    parsed: ParsedWeatherQuestion,
    observed_value_c: float,
    *,
    station: StationMeta,
    precision_profile: SettlementPrecisionProfile,
    settings: Settings,
    now: datetime,
) -> _IntradayObservationEdge | None:
    if not settings.intraday_observation_edge_enabled:
        return None
    if settings.strategy_mode not in {"intraday_observation_edge", "hybrid_observation_edge"}:
        return None

    observed_value = _observed_value_in_source_unit(observed_value_c, precision_profile.unit)
    if observed_value is None:
        return None
    local_hour = now.astimezone(_zone(station.timezone)).hour

    if parsed.temperature_metric == "min":
        edge = _intraday_low_edge(
            parsed,
            observed_value,
            precision_profile=precision_profile,
            settings=settings,
            local_hour=local_hour,
        )
    else:
        edge = _intraday_high_edge(
            parsed,
            observed_value,
            precision_profile=precision_profile,
            settings=settings,
            local_hour=local_hour,
        )
    if edge is None or precision_profile.confidence != "needs_audit":
        return edge

    multiplier = settings.intraday_hko_needs_audit_fraction_multiplier
    return replace(
        edge,
        entry_fraction=edge.entry_fraction * multiplier,
        size_reason=f"{edge.size_reason}; hko_needs_audit_multiplier={multiplier:.2f}",
    )


def _intraday_high_edge(
    parsed: ParsedWeatherQuestion,
    observed_value: float,
    *,
    precision_profile: SettlementPrecisionProfile,
    settings: Settings,
    local_hour: int,
) -> _IntradayObservationEdge | None:
    bucket = _exact_source_bucket(parsed, precision_profile)
    if bucket is None:
        return None
    lower, upper = bucket
    unit = precision_profile.unit

    if observed_value >= upper:
        return _IntradayObservationEdge(
            p_true=0.0,
            signal_name="high-strong-no",
            adjustment="intraday-observed-high-reached-next-source-display-integer",
            entry_fraction=settings.intraday_strong_entry_fraction,
            size_reason=(
                f"intraday_observation_edge=strong_no; observed_high_{unit.lower()}={observed_value:.2f}; "
                f"bucket_upper_{unit.lower()}={upper:.2f}; local_hour={local_hour}"
            ),
        )
    return None


def _intraday_low_edge(
    parsed: ParsedWeatherQuestion,
    observed_value: float,
    *,
    precision_profile: SettlementPrecisionProfile,
    settings: Settings,
    local_hour: int,
) -> _IntradayObservationEdge | None:
    unit = precision_profile.unit
    bucket = _exact_source_bucket(parsed, precision_profile)
    if bucket is not None:
        lower, upper = bucket
        if observed_value < lower:
            return _IntradayObservationEdge(
                p_true=0.0,
                signal_name="low-strong-no",
                adjustment="intraday-observed-low-below-source-display-integer",
                entry_fraction=settings.intraday_strong_entry_fraction,
                size_reason=(
                    f"intraday_observation_edge=strong_no; observed_low_{unit.lower()}={observed_value:.2f}; "
                    f"bucket_lower_{unit.lower()}={lower:.2f}; local_hour={local_hour}"
                ),
            )
        return None

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
    residual_profile_store: Any | None = None,
    concentrated_sizing_eligible_by_station: Mapping[str, bool] | None = None,
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
    precision_profile = settlement_precision_profile_for_station(station)
    precision_note = (
        f"settlement_precision_confidence={precision_profile.confidence}; "
        f"settlement_precision_bucket_model={precision_profile.bucket_model}; "
        f"reporting_precision={precision_profile.reporting_precision}"
    )
    if precision_profile.confidence == "blocked":
        return _neutral_signal(
            parsed,
            "unsupported-settlement-precision",
            f"{station.station_name} [{station.station_id}]; {precision_note}; {precision_profile.note}",
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
    payload.update(
        {
            "settlement_precision_confidence": precision_profile.confidence,
            "settlement_precision_bucket_model": precision_profile.bucket_model,
            "settlement_reporting_precision": precision_profile.reporting_precision,
            "settlement_source_type": precision_profile.source_type,
        }
    )
    observed_value_c = observation.observed_low_c if parsed.temperature_metric == "min" else observation.observed_high_c
    observed_label = "observed_low_c" if parsed.temperature_metric == "min" else "observed_high_c"
    base_note = (
        f"{station.station_name} [{station.station_id}] target_date={target.isoformat()}; "
        f"evidence=official-station; {observed_label}={observed_value_c}; "
        f"observed_at={payload.get('observed_at')}; freshness_seconds={observation.freshness_seconds}; "
        f"nowcast_source={observation.source}; {precision_note}"
    )
    if observation.midnight_reset_status:
        base_note += (
            f"; midnight_reset_status={observation.midnight_reset_status}; "
            f"data_block_reason={observation.data_block_reason}"
        )
    if not observation.station_id or observation.station_id.upper() != station.station_id.upper():
        return replace(
            _neutral_signal(
                parsed,
                "official-station-unavailable",
                f"{base_note}; station mismatch: expected {station.station_id}, got {observation.station_id}",
            ),
            nowcast=payload,
            strategy_mode=settings.strategy_mode,
            settlement_precision_confidence=precision_profile.confidence,
        )
    if (
        observation.freshness_seconds is None
        or observation.freshness_seconds > settings.station_nowcast_freshness_seconds
    ):
        return replace(
            _neutral_signal(
                parsed,
                "official-station-unavailable",
                (
                    f"{base_note}; stale station observation: freshness_seconds="
                    f"{observation.freshness_seconds}, limit={settings.station_nowcast_freshness_seconds}"
                ),
            ),
            nowcast=payload,
            strategy_mode=settings.strategy_mode,
            settlement_precision_confidence=precision_profile.confidence,
        )
    if not observation.usable or observed_value_c is None:
        reason = observation.unavailable_reason or "missing-observed-temperature"
        return replace(
            _neutral_signal(parsed, "official-station-unavailable", f"{base_note}; nowcast_unavailable={reason}"),
            nowcast=payload,
            strategy_mode=settings.strategy_mode,
            settlement_precision_confidence=precision_profile.confidence,
        )
    if observation.source == "aviationweather-metar" and not observation.daily_extremes_complete:
        reason = observation.data_block_reason or "metar-daily-extremes-incomplete"
        return replace(
            _neutral_signal(
                parsed,
                "official-station-unavailable",
                f"{base_note}; nowcast_unavailable={reason}",
            ),
            nowcast=payload,
            strategy_mode=settings.strategy_mode,
            settlement_precision_confidence=precision_profile.confidence,
        )
    formation_note = _formation_audit_evidence(
        payload,
        parsed=parsed,
        station=station,
        target=target,
        now=current,
        residual_profile_store=residual_profile_store,
        precision_profile=precision_profile,
    )
    base_note = f"{base_note}; {formation_note}"

    lock = None
    if settings.strategy_mode in {"lock_only", "intraday_observation_edge", "hybrid_observation_edge"}:
        lock = _official_station_exact_lock(
            parsed,
            observed_value_c,
            target=target,
            timezone_name=station.timezone,
            settings=settings,
            now=current,
        )
    if lock is not None:
        lock_probability = lock.p_true
        lock_fraction = lock.entry_fraction
        lock_size_reason = lock.size_reason
        if precision_profile.confidence == "needs_audit":
            strong_probability = settings.intraday_strong_side_probability
            lock_probability = 1.0 - strong_probability
            lock_fraction *= settings.intraday_hko_needs_audit_fraction_multiplier
            lock_size_reason += (
                f"; hko_needs_audit_probability_cap={strong_probability:.2f}; "
                f"hko_needs_audit_multiplier={settings.intraday_hko_needs_audit_fraction_multiplier:.2f}"
            )
        payload["data_block_reason"] = ""
        payload["strategy_allowed_reason"] = "verified same-day observation irreversibly broke the bucket"
        base_note += "; data_block_reason=; strategy_allowed_reason=verified bucket break"
        return WeatherSignal(
            p_true=lock_probability,
            confidence=1.0,
            source=f"official-station-lock-{lock.lock_name}",
            note=(
                f"{base_note}; strategy_mode={settings.strategy_mode}; signal_family=lock_only; "
                f"station_adjustment={lock.adjustment}; "
                f"{lock_size_reason}; entry_size_fraction_override={lock_fraction:.4f}"
            ),
            parsed=parsed,
            nowcast=payload,
            entry_size_fraction_override=lock_fraction,
            entry_size_reason=lock_size_reason,
            strategy_mode=settings.strategy_mode,
            signal_family="lock_only",
            settlement_precision_confidence=precision_profile.confidence,
        )

    if (
        observation.source == "aviationweather-metar"
        and observation.observation_due_status == "overdue"
        and observation.next_observation_due_at is not None
    ):
        payload["data_block_reason"] = "next-learned-observation-pending"
        payload["strategy_allowed_reason"] = (
            "ordinary probability entry waits for the next report inferred from recent station observations"
        )
        return replace(
            _neutral_signal(
                parsed,
                "official-station-observation-report-pending",
                (
                    f"{base_note}; signal_family=intraday_observation_edge; "
                    "observation_report=pending; "
                    f"learned_observation_interval_seconds="
                    f"{observation.learned_observation_interval_seconds}; "
                    f"next_observation_due_at={payload.get('next_observation_due_at')}; "
                    "ordinary entry paused until a newer official observation arrives"
                ),
            ),
            nowcast=payload,
            strategy_mode=settings.strategy_mode,
            settlement_precision_confidence=precision_profile.confidence,
        )

    residual = _residual_observation_edge_signal(
        parsed,
        observed_value_c,
        station=station,
        precision_profile=precision_profile,
        settings=settings,
        now=current,
        target=target,
        base_note=base_note,
        payload=payload,
        residual_profile_store=residual_profile_store,
    )
    if residual is not None:
        return residual

    intraday = _intraday_observation_edge(
        parsed,
        observed_value_c,
        station=station,
        precision_profile=precision_profile,
        settings=settings,
        now=current,
    )
    if intraday is not None and intraday.p_true == 0.0:
        return WeatherSignal(
            p_true=intraday.p_true,
            confidence=1.0,
            source=f"official-station-intraday-{intraday.signal_name}",
            note=(
                f"{base_note}; strategy_mode={settings.strategy_mode}; "
                f"signal_family=intraday_observation_edge; station_adjustment={intraday.adjustment}; "
                f"{intraday.size_reason}; entry_size_fraction_override={intraday.entry_fraction:.4f}"
            ),
            parsed=parsed,
            nowcast=payload,
            entry_size_fraction_override=intraday.entry_fraction,
            entry_size_reason=intraday.size_reason,
            strategy_mode=settings.strategy_mode,
            signal_family="intraday_observation_edge",
            settlement_precision_confidence=precision_profile.confidence,
        )

    return WeatherSignal(
        p_true=0.5,
        confidence=0.0,
        source="official-station-neutral",
        note=(
            f"{base_note}; strategy_mode={settings.strategy_mode}; "
            "official_station_lock=none; intraday_observation_edge=none"
        ),
        parsed=parsed,
        nowcast=payload,
        strategy_mode=settings.strategy_mode,
        settlement_precision_confidence=precision_profile.confidence,
    )
