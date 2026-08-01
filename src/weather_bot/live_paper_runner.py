from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from dataclasses import replace
import inspect
import json
from datetime import date, datetime, timedelta, timezone
import math
from queue import Empty, SimpleQueue
import threading
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Settings, load_settings
from .edge import (
    ObservationSizingTier,
    estimate_executable_net_return,
    executable_buy_price,
    fee_adjusted_entry_shares,
    is_abnormal_price_opportunity,
    no_net_edge,
    observation_edge_entry_fraction,
    polymarket_taker_fee_per_share,
    yes_net_edge,
)
from .event_dates import event_date_window_from_hint
from .exit_policy import conservative_settlement_value, model_fair_price, target_exit_price
from .market_rules import (
    market_rule_mismatch_reason,
)
from .models import EdgeResult, MarketDecision, MarketTradability, OrderBook, OrderLevel, PaperPosition, RawMarket, WeatherSignal
from .nowcast import AviationWeatherMetarNowcastProvider
from .paper import PaperBroker, maybe_close_positions, maybe_settle_resolved_positions
from .polymarket_client import PolymarketClient
from .portfolio import (
    LOCK_EXACT_NO_STRATEGY_MODES,
    LOCK_ONLY_EXACT_NO_MAX_ENTRY_PRICE,
    LOCK_ONLY_EXACT_NO_MIN_NET_RETURN_PCT,
    LOCK_ONLY_EXACT_NO_TIER,
    UPSTREAM_LOCK_PAPER_EVENT_CAP_FRACTION,
    UPSTREAM_LOCK_PAPER_MODE,
    UPSTREAM_LOCK_PAPER_MAX_ENTRY_PRICE,
    UPSTREAM_LOCK_PAPER_NOWCAST_SOURCES,
    UPSTREAM_LOCK_PAPER_SETTLEMENT_UNCERTAINTY_FLOOR,
    UPSTREAM_LOCK_PAPER_SIGNAL_FAMILY,
    required_upstream_bucket_distance_c,
    EntryBankrollSnapshot,
    EventPortfolioDecision,
    PortfolioCandidate,
    available_entry_bankroll,
    direct_exact_no_entry_block_reason,
    exact_no_entry_block_reason_for_strategy,
    select_event_portfolio,
    websocket_pricing_block_reason,
)
from .realtime_orderbook import OrderBookMarketStream
from .residual_probability import ResidualProfileStore
from .risk import confidence_size_multiplier, drawdown_entry_block_reason, fractional_kelly_binary
from .runner_status import read_runner_status, update_runner_status_fields, utc_now_iso, write_runner_status
from .station_signal import estimate_station_signal
from .stations import TRADING_READY_STATION_MAP
from .upstream_lock_policy import upstream_lock_paper_exact_no_tier
from .weather_client import parse_weather_question, temperature_bucket_interval_bounds_f

estimate_station_probability = estimate_station_signal


ENTRY_BANKROLL_FAIL_CLOSED_REASON = "기존 포지션을 안전하게 평가할 수 없어 신규 진입 차단"

MAX_ENTRY_EXECUTION_PRICE = 0.90
YES_SIZE_CAP_95 = 0.20
YES_SIZE_CAP_93 = 0.10
YES_SIZE_CAP_DEFAULT = 0.05
ENTRY_DEPTH_AUDIT_TARGET_USD = 100.0
REALTIME_EVALUATION_QUEUE_MAX_EVENTS = 256
REALTIME_EVALUATION_BATCH_MAX_EVENTS = 64
REALTIME_NORMAL_EVALUATION_BATCH_MAX_EVENTS = 1
REALTIME_EVALUATION_COALESCE_SECONDS = 0.25
REALTIME_FINAL_CHECK_MAX_WORKERS = 8
REALTIME_FINAL_PREFETCH_DEADLINE_SECONDS = 1.5
REALTIME_FINAL_BOOK_PREFETCH_MAX_AGE_SECONDS = 5.0
FINAL_DIRECT_OBSERVATION_MAX_AGE_SECONDS = 5.0
REALTIME_LAST_EVALUATION_SIDE = "_LAST_EVALUATION"
REALTIME_STATION_REFRESH_PROBE_MAX_EVENTS = 4
NO_ONLY_NEW_ENTRY_REASON = (
    "SKIP_NO_ONLY_NEW_ENTRY: 신규 YES 진입 중단 정책; 기존 YES 포지션 청산은 계속 허용"
)
_station_refresh_probe_cursor = 0
_station_observation_refresh_cursor = 0


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_datetime(value: datetime | None) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat() if value is not None else ""


def _has_recent_direct_observation(
    signal: WeatherSignal,
    *,
    now: datetime,
) -> bool:
    payload = signal.nowcast if isinstance(signal.nowcast, dict) else {}
    if str(payload.get("source") or "") != "wunderground-history-direct":
        return False
    received_value = payload.get("bot_received_at")
    try:
        received_at = (
            received_value
            if isinstance(received_value, datetime)
            else datetime.fromisoformat(str(received_value).replace("Z", "+00:00"))
        )
        received_at = _utc_datetime(received_at)
    except (TypeError, ValueError):
        return False
    age_seconds = (_utc_datetime(now) - received_at).total_seconds()
    return 0.0 <= age_seconds <= FINAL_DIRECT_OBSERVATION_MAX_AGE_SECONDS


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_official_nowcast_lock(signal: WeatherSignal) -> bool:
    return (
        "official_nowcast_lock=" in signal.note
        or "official-nowcast-lock" in signal.source
        or "official-station-lock" in signal.source
    )


def _is_verified_settlement_lock_signal(signal: WeatherSignal) -> bool:
    nowcast = signal.nowcast if isinstance(signal.nowcast, dict) else {}
    return (
        signal.signal_family == "lock_only"
        and signal.source == "official-station-lock-strong_no"
        and nowcast.get("source") == "wunderground-history-direct"
        and nowcast.get("settlement_source_verified") is True
    )


def _is_intraday_observation_edge(signal: WeatherSignal) -> bool:
    return (
        "signal_family=intraday_observation_edge" in signal.note
        or "official-station-intraday-" in signal.source
        or "official-station-residual-" in signal.source
    )


def _is_official_station_entry_signal(signal: WeatherSignal) -> bool:
    return _is_official_nowcast_lock(signal) or _is_intraday_observation_edge(signal)


def _base_signal_family(signal: WeatherSignal) -> str:
    if signal.signal_family == UPSTREAM_LOCK_PAPER_SIGNAL_FAMILY:
        return UPSTREAM_LOCK_PAPER_SIGNAL_FAMILY
    if _is_intraday_observation_edge(signal):
        return "intraday_observation_edge"
    if _is_official_nowcast_lock(signal):
        return "lock_only"
    return ""


def _is_lock_only_exact_no(side: str, signal: WeatherSignal) -> bool:
    parsed = signal.parsed
    nowcast = signal.nowcast if isinstance(signal.nowcast, dict) else {}
    station_id = str(nowcast.get("station_id") or "").upper()
    p_true = _finite_float(signal.p_true)
    return (
        side == "NO"
        and parsed is not None
        and parsed.variable == "temperature"
        and parsed.temperature_metric in {"max", "min"}
        and parsed.temperature_bucket == "exact"
        and signal.signal_family == "lock_only"
        and signal.source == "official-station-lock-strong_no"
        and p_true is not None
        and 0.0 <= p_true <= 1e-12
        and signal.settlement_precision_confidence == "verified"
        and str(nowcast.get("source") or "") == "wunderground-history-direct"
        and station_id != "HKO"
        and not str(nowcast.get("data_block_reason") or "")
    )


def _is_upstream_lock_paper_exact_no(side: str, signal: WeatherSignal) -> bool:
    parsed = signal.parsed
    nowcast = signal.nowcast if isinstance(signal.nowcast, dict) else {}
    p_true = _finite_float(signal.p_true)
    conservative_yes_probability = _finite_float(signal.conservative_yes_probability)
    conservative_no_probability = _finite_float(signal.conservative_no_probability)
    upstream_distance_c = _finite_float(nowcast.get("upstream_bucket_distance_c"))
    upstream_required_c = _finite_float(nowcast.get("upstream_min_bucket_distance_c"))
    station_id = str(nowcast.get("station_id") or "").upper()
    expected_station = (
        TRADING_READY_STATION_MAP.get((parsed.city or "").casefold())
        if parsed is not None
        else None
    )
    expected_required_c = required_upstream_bucket_distance_c(
        source=str(nowcast.get("source") or ""),
        station_id=station_id,
        temperature_metric=parsed.temperature_metric if parsed is not None else None,
        temperature_bucket=parsed.temperature_bucket if parsed is not None else None,
        threshold_unit=parsed.threshold_unit if parsed is not None else None,
    )
    target_date = str(nowcast.get("target_date_local") or "")
    station_date = str(nowcast.get("station_local_date") or "")
    return (
        side == "NO"
        and parsed is not None
        and parsed.variable == "temperature"
        and parsed.temperature_metric in {"max", "min"}
        and parsed.temperature_bucket == "exact"
        and parsed.threshold_unit == "C"
        and signal.strategy_mode == UPSTREAM_LOCK_PAPER_MODE
        and signal.signal_family == UPSTREAM_LOCK_PAPER_SIGNAL_FAMILY
        and signal.source == "official-station-lock-strong_no"
        and p_true is not None
        and 0.0 <= p_true <= 1e-12
        and conservative_yes_probability is not None
        and conservative_no_probability is not None
        and UPSTREAM_LOCK_PAPER_SETTLEMENT_UNCERTAINTY_FLOOR
        <= conservative_yes_probability
        < 0.5
        and 0.5
        < conservative_no_probability
        <= 1.0 - UPSTREAM_LOCK_PAPER_SETTLEMENT_UNCERTAINTY_FLOOR
        and abs(conservative_yes_probability + conservative_no_probability - 1.0) <= 1e-9
        and signal.settlement_precision_confidence == "verified"
        and nowcast.get("source") in UPSTREAM_LOCK_PAPER_NOWCAST_SOURCES
        and expected_station is not None
        and station_id == expected_station.station_id.upper()
        and station_id != "HKO"
        and nowcast.get("daily_extremes_complete") is True
        and nowcast.get("entry_evidence_mode") == "upstream_same_station_paper"
        and nowcast.get("settlement_source_verified") is False
        and upstream_distance_c is not None
        and upstream_required_c is not None
        and abs(upstream_required_c - expected_required_c) <= 1e-12
        and upstream_distance_c >= expected_required_c - 1e-12
        and bool(target_date)
        and target_date == station_date
        and not str(nowcast.get("data_block_reason") or "")
    )


def _is_exact_no_lock(side: str, signal: WeatherSignal) -> bool:
    return _is_lock_only_exact_no(side, signal) or _is_upstream_lock_paper_exact_no(
        side,
        signal,
    )


def _lock_only_exact_no_entry_skip_reason(
    market: RawMarket,
    signal: WeatherSignal,
    side: str | None,
) -> str | None:
    parsed = signal.parsed
    nowcast = signal.nowcast if isinstance(signal.nowcast, dict) else {}
    bucket = parsed.temperature_bucket if parsed is not None else None
    metric = parsed.temperature_metric if parsed is not None else None
    nowcast_source = str(nowcast.get("source") or "")
    precision = str(signal.settlement_precision_confidence or "")
    p_true = _finite_float(signal.p_true)
    p_true_label = "UNKNOWN" if p_true is None else f"{p_true:.6f}"

    blocked_reason = direct_exact_no_entry_block_reason(market, signal, side or "") or ""
    if not blocked_reason:
        return None
    return (
        "SKIP_LOCK_ONLY_EXACT_NO_REQUIRED: strategy_mode=lock_only permits new entry only "
        "for a verified Wunderground-settled exact temperature bucket whose direct "
        "observation makes YES impossible; "
        f"blocked_reason={blocked_reason}; side={side or 'UNKNOWN'}; metric={metric or 'UNKNOWN'}; "
        f"bucket={bucket or 'UNKNOWN'}; p_true={p_true_label}; "
        f"signal_source={signal.source or 'UNKNOWN'}; "
        f"nowcast_source={nowcast_source or 'UNKNOWN'}; precision={precision or 'UNKNOWN'}"
    )


def _exact_no_entry_skip_reason(
    market: RawMarket,
    signal: WeatherSignal,
    side: str | None,
    settings: Settings,
) -> str | None:
    blocked_reason = exact_no_entry_block_reason_for_strategy(
        market,
        signal,
        side or "",
        strategy_mode=settings.strategy_mode,
    )
    if blocked_reason is None:
        return None
    if settings.strategy_mode == "lock_only":
        return _lock_only_exact_no_entry_skip_reason(market, signal, side)
    parsed = signal.parsed
    nowcast = signal.nowcast if isinstance(signal.nowcast, dict) else {}
    p_true = _finite_float(signal.p_true)
    p_true_label = "UNKNOWN" if p_true is None else f"{p_true:.6f}"
    return (
        "SKIP_UPSTREAM_LOCK_PAPER_EXACT_NO_REQUIRED: upstream_lock_paper permits new paper "
        "entry only for an exact temperature NO made physically impossible by a complete, "
        "fresh AWC/KMA same-station day; it does not claim Wunderground settlement confirmation; "
        f"blocked_reason={blocked_reason}; side={side or 'UNKNOWN'}; "
        f"metric={(parsed.temperature_metric if parsed is not None else None) or 'UNKNOWN'}; "
        f"bucket={(parsed.temperature_bucket if parsed is not None else None) or 'UNKNOWN'}; "
        f"p_true={p_true_label}; signal_source={signal.source or 'UNKNOWN'}; "
        f"nowcast_source={str(nowcast.get('source') or 'UNKNOWN')}; "
        f"precision={signal.settlement_precision_confidence or 'UNKNOWN'}"
    )


def _entry_price_cap(side: str, signal: WeatherSignal) -> float:
    if _is_lock_only_exact_no(side, signal):
        return LOCK_ONLY_EXACT_NO_MAX_ENTRY_PRICE
    if _is_upstream_lock_paper_exact_no(side, signal):
        return UPSTREAM_LOCK_PAPER_MAX_ENTRY_PRICE
    return MAX_ENTRY_EXECUTION_PRICE


def _upstream_lock_paper_probability_tier(signal: WeatherSignal) -> str:
    parsed = signal.parsed
    nowcast = signal.nowcast if isinstance(signal.nowcast, dict) else {}
    return upstream_lock_paper_exact_no_tier(
        source=str(nowcast.get("source") or ""),
        station_id=str(nowcast.get("station_id") or ""),
        temperature_metric=parsed.temperature_metric if parsed is not None else None,
        temperature_bucket=parsed.temperature_bucket if parsed is not None else None,
        threshold_unit=parsed.threshold_unit if parsed is not None else None,
    )


def _entry_min_return_pct(side: str, signal: WeatherSignal, settings: Settings) -> float:
    if _is_exact_no_lock(side, signal):
        return max(settings.entry_min_expected_net_return_pct, LOCK_ONLY_EXACT_NO_MIN_NET_RETURN_PCT)
    return settings.entry_min_expected_net_return_pct


def _yes_probability_size_cap(side_probability: float) -> float:
    if side_probability >= 0.95:
        return YES_SIZE_CAP_95
    if side_probability >= 0.93:
        return YES_SIZE_CAP_93
    return YES_SIZE_CAP_DEFAULT


def _cap_yes_observation_tier(
    side: str,
    side_probability: float,
    observation_tier: ObservationSizingTier | None,
    size_fraction_override: float | None,
) -> tuple[ObservationSizingTier | None, float | None]:
    if side != "YES":
        return observation_tier, size_fraction_override
    cap = _yes_probability_size_cap(side_probability)
    capped_fraction = min(size_fraction_override, cap) if size_fraction_override is not None else cap
    if observation_tier is None:
        return observation_tier, capped_fraction
    capped_event_cap = (
        min(observation_tier.event_cap_override_fraction, cap)
        if observation_tier.event_cap_override_fraction is not None
        else None
    )
    return (
        replace(
            observation_tier,
            entry_fraction=capped_fraction,
            event_cap_override_fraction=capped_event_cap,
        ),
        capped_fraction,
    )


def _observation_edge_fraction(
    signal: WeatherSignal,
    settings: Settings,
    side_probability: float,
    net_edge: float,
) -> ObservationSizingTier | None:
    if settings.strategy_mode not in {"intraday_observation_edge", "hybrid_observation_edge"}:
        return None
    if not _is_official_station_entry_signal(signal):
        return None
    if signal.source.startswith("official-station-residual-"):
        if not signal.probability_tier:
            return None
        return ObservationSizingTier(
            signal.probability_tier,
            signal.entry_size_fraction_override,
            signal.event_cap_override_fraction,
        )
    tier = observation_edge_entry_fraction(
        side_probability,
        tier_80_probability=settings.observation_tier_80_probability,
        tier_90_probability=settings.observation_tier_90_probability,
        tier_95_probability=settings.observation_tier_95_probability,
        tier_80_fraction=settings.observation_tier_80_fraction,
        tier_90_fraction=settings.observation_tier_90_fraction,
        tier_95_fraction=settings.observation_tier_95_fraction,
    )
    if tier is None:
        return None
    return replace(tier, event_cap_override_fraction=None)


def _side_probability(side: str, p_true_yes: float) -> float:
    p_yes = max(0.0, min(1.0, p_true_yes))
    return p_yes if side == "YES" else 1.0 - p_yes


def _explicit_conservative_side_probability(side: str, signal: WeatherSignal) -> float | None:
    probability = (
        signal.conservative_yes_probability
        if side == "YES"
        else signal.conservative_no_probability
    )
    if probability is None:
        return None
    return max(0.0, min(1.0, probability))


def _raw_side_probability(side: str, signal: WeatherSignal) -> float | None:
    if signal.raw_probability is None:
        return None
    return _side_probability(side, signal.raw_probability)


def _edge_error_margins(signal: WeatherSignal, settings: Settings) -> tuple[float, float]:
    if _is_verified_settlement_lock_signal(signal):
        return 0.0, 0.0
    if (
        signal.strategy_mode == UPSTREAM_LOCK_PAPER_MODE
        and signal.conservative_yes_probability is not None
        and signal.conservative_no_probability is not None
    ):
        return 0.0, 0.0
    return settings.model_error_margin, settings.resolution_error_margin


def _conservative_settlement_value_for_signal(side: str, signal: WeatherSignal, settings: Settings) -> float:
    explicit_probability = _explicit_conservative_side_probability(side, signal)
    if explicit_probability is not None:
        return explicit_probability
    if _is_verified_settlement_lock_signal(signal):
        return _side_probability(side, signal.p_true)
    return conservative_settlement_value(side, signal.p_true, settings)


def _model_fair_price_for_signal(side: str, signal: WeatherSignal, settings: Settings) -> float:
    explicit_probability = _explicit_conservative_side_probability(side, signal)
    if explicit_probability is None and not _is_verified_settlement_lock_signal(signal):
        return model_fair_price(side, signal.p_true, settings)
    settlement_value = _conservative_settlement_value_for_signal(side, signal, settings)
    fair = settlement_value - polymarket_taker_fee_per_share(
        settlement_value,
        settings.weather_taker_fee_rate,
    )
    return max(0.01, min(0.99, fair))


def _nowcast_bucket_lock_exit_signal(side: str, signal: WeatherSignal) -> tuple[str, str] | None:
    parsed = signal.parsed
    if side != "NO" or parsed is None or signal.nowcast is None:
        return None
    if parsed.variable != "temperature" or parsed.temperature_bucket not in {"exact", "range"}:
        return None

    bounds = temperature_bucket_interval_bounds_f(parsed)
    if bounds is None:
        return None

    if parsed.temperature_metric == "min":
        observed_f = _finite_float(signal.nowcast.get("observed_low_f"))
        observed_c = signal.nowcast.get("observed_low_c")
        observed_label = "observed_low_c"
    else:
        observed_f = _finite_float(signal.nowcast.get("observed_high_f"))
        observed_c = signal.nowcast.get("observed_high_c")
        observed_label = "observed_high_c"

    if observed_f is None or not bounds.contains_f(observed_f):
        return None

    return (
        "nowcast_bucket_lock_risk",
        (
            f"nowcast bucket lock risk: NO held while {observed_label}={observed_c} "
            f"is inside {parsed.temperature_bucket} bucket; p_true={signal.p_true:.3f}"
        ),
    )


def _with_exit_signal(side: str, signal: WeatherSignal, result: EdgeResult) -> EdgeResult:
    risk = _nowcast_bucket_lock_exit_signal(side, signal)
    if risk is None:
        return result
    exit_signal, exit_signal_reason = risk
    return replace(
        result,
        reason=f"{result.reason}; exit_signal={exit_signal}; {exit_signal_reason}",
        exit_signal=exit_signal,
        exit_signal_reason=exit_signal_reason,
    )


def _call_probability_estimator(
    probability_estimator: Any,
    question: str,
    *,
    settings: Settings,
    observation_provider: Any | None = None,
    residual_profile_store: Any | None = None,
    concentrated_sizing_eligible_by_station: Mapping[str, bool] | None = None,
    now: datetime | None = None,
) -> WeatherSignal:
    kwargs: dict[str, Any] = {"settings": settings}
    signature = inspect.signature(probability_estimator)
    accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
    if observation_provider is not None:
        if accepts_kwargs or "observation_provider" in signature.parameters:
            kwargs["observation_provider"] = observation_provider
    if residual_profile_store is not None:
        if accepts_kwargs or "residual_profile_store" in signature.parameters:
            kwargs["residual_profile_store"] = residual_profile_store
    eligibility = concentrated_sizing_eligible_by_station
    if eligibility is None and residual_profile_store is not None:
        eligibility = getattr(
            residual_profile_store,
            "concentrated_sizing_eligible_by_station",
            None,
        )
    if eligibility is not None:
        if accepts_kwargs or "concentrated_sizing_eligible_by_station" in signature.parameters:
            kwargs["concentrated_sizing_eligible_by_station"] = eligibility
    if now is not None and (accepts_kwargs or "now" in signature.parameters):
        kwargs["now"] = now
    return probability_estimator(question, **kwargs)


def _load_residual_profile_store(settings: Settings) -> ResidualProfileStore | None:
    if not settings.station_residual_probability_enabled:
        return None
    store = ResidualProfileStore.from_path(settings.station_residual_profile_path)
    if store.min_sample_days != settings.station_residual_min_sample_days:
        store = replace(store, min_sample_days=settings.station_residual_min_sample_days)
    return store


def _compact_status_text(value: Any, max_chars: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:max_chars]


def _safe_error_text(exc: BaseException) -> str:
    return _compact_status_text(str(exc) or exc.__class__.__name__)


def _status_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _market_error_status(settings: Settings) -> tuple[int, dict[str, Any] | None]:
    status = read_runner_status(settings)
    last_error = status.get("last_market_error")
    return _status_int(status.get("market_error_count")), last_error if isinstance(last_error, dict) else None


def _market_error_status_fields(count: int, last_error: dict[str, Any] | None) -> dict[str, Any]:
    fields: dict[str, Any] = {"market_error_count": count}
    if last_error is not None:
        fields["last_market_error"] = last_error
    return fields


def _fallback_error_signal(market: RawMarket, error_message: str) -> WeatherSignal:
    try:
        parsed = parse_weather_question(market.question)
    except Exception:  # noqa: BLE001
        parsed = None
    return WeatherSignal(
        p_true=0.5,
        confidence=0.0,
        source="market-evaluation-error",
        note=f"SKIP_ERROR: market evaluation failed; {error_message}",
        parsed=parsed,
    )


def _record_market_evaluation_error(
    broker: PaperBroker,
    market: RawMarket,
    exc: BaseException,
    market_type: str,
    *,
    signal: WeatherSignal | None = None,
    context: str,
) -> tuple[WeatherSignal, EdgeResult, dict[str, Any], int]:
    error_message = _safe_error_text(exc)
    signal = signal or _fallback_error_signal(market, error_message)
    result = EdgeResult(
        "SKIP_ERROR",
        signal.p_true,
        None,
        -999.0,
        0.0,
        0.0,
        f"SKIP_ERROR: market evaluation failed during {context}: {exc.__class__.__name__}: {error_message}",
    )
    current_count, _last_error = _market_error_status(broker.settings)
    error_count = current_count + 1
    error_info = {
        "at": utc_now_iso(),
        "count": error_count,
        "context": context,
        "market_id": market.market_id,
        "slug": market.slug or "",
        "question": _compact_status_text(market.question, 240),
        "error_type": exc.__class__.__name__,
        "message": error_message,
    }
    broker.log_decision(market, result, signal.note, market_type, signal=signal)
    broker.log_raw_snapshot(
        "market_evaluation_error",
        market,
        {
            "status": "error",
            "context": context,
            "error_type": exc.__class__.__name__,
            "error": error_message,
            "market_raw": market.raw,
            "signal": {
                "p_true": signal.p_true,
                "confidence": signal.confidence,
                "source": signal.source,
                "note": signal.note,
                "nowcast": signal.nowcast,
            },
        },
    )
    update_runner_status_fields(
        broker.settings,
        market_error_count=error_count,
        last_market_error=error_info,
    )
    return signal, result, error_info, error_count


class StreamBackedPolymarketClient(PolymarketClient):
    def __init__(self, gamma_base: str, clob_base: str, stream: OrderBookMarketStream) -> None:
        super().__init__(gamma_base, clob_base)
        self.stream = stream
        self._final_prefetch_lock = threading.Lock()
        self._final_book_prefetched_at: dict[str, float] = {}
        self._final_prefetch_generation: dict[str, int] = {}
        self._failed_prefetch_conditions: set[str] = set()
        self._failed_prefetch_tokens: set[str] = set()
        self._candidate_book_prefetch_audit: dict[str, dict[str, Any]] = {}

    def get_clob_market_tradability(self, condition_id: str) -> MarketTradability:
        condition = str(condition_id)
        with self._final_prefetch_lock:
            if condition in self._failed_prefetch_conditions:
                self._failed_prefetch_conditions.discard(condition)
                raise RuntimeError("concurrent final check exceeded its deadline or failed")
        return super().get_clob_market_tradability(condition)

    def get_order_book(self, token_id: str) -> OrderBook:
        return self.stream.get_order_book(token_id)

    def get_candidate_order_book(self, token_id: str) -> OrderBook:
        return self.stream.cache.get_order_book(token_id)

    def get_candidate_order_book_audit(self, token_id: str) -> dict[str, Any]:
        with self._final_prefetch_lock:
            return dict(self._candidate_book_prefetch_audit.get(str(token_id), {}))

    def refresh_order_book(self, token_id: str) -> OrderBook:
        token = str(token_id)
        with self._final_prefetch_lock:
            if token in self._failed_prefetch_tokens:
                self._failed_prefetch_tokens.discard(token)
                raise RuntimeError("concurrent final check exceeded its deadline or failed")
            prefetched_at = self._final_book_prefetched_at.pop(token, None)
            use_prefetched = (
                prefetched_at is not None
                and time.monotonic() - prefetched_at <= REALTIME_FINAL_BOOK_PREFETCH_MAX_AGE_SECONDS
            )
            if not use_prefetched:
                self._final_prefetch_generation[token] = self._final_prefetch_generation.get(token, 0) + 1
        if use_prefetched:
            try:
                return self.stream.cache.get_order_book(token)
            except KeyError:
                with self._final_prefetch_lock:
                    self._final_prefetch_generation[token] = self._final_prefetch_generation.get(token, 0) + 1
        return self.stream.refresh_order_book(token_id)

    def prefetch_candidate_order_books(self, token_ids: list[str]) -> dict[str, int]:
        """Fetch candidate books quickly, then isolate only failed batch members."""
        unique_tokens = list(dict.fromkeys(str(token_id) for token_id in token_ids if str(token_id)))
        if not unique_tokens:
            return {"requested": 0, "book_ready": 0, "failed": 0, "deferred": 0}

        started = time.monotonic()
        deadline = started + REALTIME_FINAL_PREFETCH_DEADLINE_SECONDS
        requested_at = utc_now_iso()
        audits = {
            token_id: {
                "requested_at": requested_at,
                "received_at": "",
                "checked_at": "",
                "status": "not_observed",
                "best_bid": None,
                "best_ask": None,
            }
            for token_id in unique_tokens
        }
        ready_tokens: set[str] = set()

        def error_status(exc: Exception) -> str:
            error_name = exc.__class__.__name__.lower()
            if "timeout" in error_name:
                return "timeout"
            if getattr(getattr(exc, "response", None), "status_code", None) is not None:
                return "http_error"
            return "request_error"

        def fetch_batch(batch: list[str], timeout: float) -> tuple[list[OrderBook], str, Exception | None]:
            try:
                return self.get_order_books(batch, timeout=timeout), utc_now_iso(), None
            except Exception as exc:  # noqa: BLE001
                return [], "", exc

        def apply_batch(
            batch: list[str],
            books: list[OrderBook],
            received_at: str,
            exc: Exception | None,
        ) -> set[str]:
            retry_tokens = set(batch)
            if exc is not None:
                status = error_status(exc)
                for token_id in batch:
                    audits[token_id]["status"] = status
                return retry_tokens
            returned: set[str] = set()
            for book in books:
                token_id = str(book.token_id)
                if token_id not in retry_tokens or token_id in returned:
                    continue
                returned.add(token_id)
                audits[token_id].update(
                    received_at=received_at,
                    checked_at=utc_now_iso(),
                    best_bid=book.best_bid,
                    best_ask=book.best_ask,
                )
                if _book_is_crossed(book):
                    audits[token_id]["status"] = "crossed"
                    continue
                try:
                    self.stream.apply_rest_snapshot(book, notify=False)
                except Exception:  # noqa: BLE001
                    audits[token_id]["status"] = "apply_error"
                    continue
                audits[token_id]["status"] = "ready"
                ready_tokens.add(token_id)
                retry_tokens.discard(token_id)
            for token_id in retry_tokens - returned:
                audits[token_id].update(received_at=received_at, status="missing_response")
            return retry_tokens

        primary_timeout = min(0.75, REALTIME_FINAL_PREFETCH_DEADLINE_SECONDS)
        primary_books, primary_received_at, primary_error = fetch_batch(
            unique_tokens,
            primary_timeout,
        )
        retry_tokens = apply_batch(
            unique_tokens,
            primary_books,
            primary_received_at,
            primary_error,
        )

        remaining = deadline - time.monotonic()
        if retry_tokens and remaining > 0:
            batches = [
                sorted(retry_tokens)[index:index + 4]
                for index in range(0, len(retry_tokens), 4)
            ]
            executor = ThreadPoolExecutor(
                max_workers=min(8, len(batches)),
                thread_name_prefix="candidate-book-retry",
            )
            futures = {
                executor.submit(fetch_batch, batch, max(0.05, remaining)): batch
                for batch in batches
            }
            completed, unfinished = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
            for future in completed:
                batch = futures[future]
                books, received_at, exc = future.result()
                apply_batch(batch, books, received_at, exc)
            for future in unfinished:
                for token_id in futures[future]:
                    audits[token_id]["status"] = "deadline"
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

        with self._final_prefetch_lock:
            self._candidate_book_prefetch_audit.update(audits)
        deferred = sum(1 for audit in audits.values() if audit["status"] == "deadline")
        return {
            "requested": len(unique_tokens),
            "book_ready": len(ready_tokens),
            "failed": len(unique_tokens) - len(ready_tokens) - deferred,
            "deferred": deferred,
        }

    def prefetch_final_entry_checks(self, checks: list[tuple[str, str]]) -> dict[str, int]:
        """Warm independent final REST checks concurrently; ledger writes remain serialized."""
        unique_checks = list(dict.fromkeys(
            (str(condition_id), str(token_id))
            for condition_id, token_id in checks
            if str(condition_id) and str(token_id)
        ))
        if not unique_checks:
            return {"requested": 0, "book_ready": 0, "failed": 0, "deferred": 0}

        scheduled: list[tuple[str, str, int]] = []
        cached_ready = 0
        with self._final_prefetch_lock:
            for condition_id, token_id in unique_checks:
                self._failed_prefetch_conditions.discard(condition_id)
                self._failed_prefetch_tokens.discard(token_id)
                current = time.monotonic()
                cached_tradability = self._tradability_cache.get(condition_id)
                cached_book_at = self._final_book_prefetched_at.get(token_id)
                tradability_is_fresh = (
                    cached_tradability is not None
                    and current - cached_tradability[0] < self.tradability_cache_ttl_seconds
                )
                book_is_fresh = (
                    cached_book_at is not None
                    and current - cached_book_at <= REALTIME_FINAL_BOOK_PREFETCH_MAX_AGE_SECONDS
                )
                if tradability_is_fresh and book_is_fresh:
                    try:
                        self.stream.cache.get_order_book(token_id)
                    except KeyError:
                        pass
                    else:
                        cached_ready += 1
                        continue
                generation = self._final_prefetch_generation.get(token_id, 0) + 1
                self._final_prefetch_generation[token_id] = generation
                scheduled.append((condition_id, token_id, generation))

        if not scheduled:
            return {
                "requested": len(unique_checks),
                "book_ready": cached_ready,
                "failed": 0,
                "deferred": 0,
            }

        def prefetch_one(check: tuple[str, str, int]) -> tuple[bool, bool]:
            condition_id, token_id, generation = check
            try:
                tradability = self._fetch_clob_market_tradability_uncached(condition_id)
            except Exception:  # The final serialized check retries and records the exact skip reason.
                return False, False
            with self._final_prefetch_lock:
                self._tradability_cache[condition_id] = (time.monotonic(), tradability)
            try:
                book = self.stream.fetch_order_book_snapshot(token_id)
            except Exception:
                return True, False
            with self._final_prefetch_lock:
                if self._final_prefetch_generation.get(token_id) != generation:
                    return True, False
                try:
                    self.stream.apply_rest_snapshot(book, notify=False)
                except Exception:
                    return True, False
                self._final_book_prefetched_at[token_id] = time.monotonic()
            return True, True

        max_workers = min(REALTIME_FINAL_CHECK_MAX_WORKERS, len(unique_checks))
        executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="final-entry-check")
        future_context = {
            executor.submit(prefetch_one, check): check
            for check in scheduled
        }
        futures = list(future_context)
        completed, unfinished = wait(
            futures,
            timeout=REALTIME_FINAL_PREFETCH_DEADLINE_SECONDS,
        )
        ready = cached_ready
        scheduled_ready = 0
        successful_conditions: set[str] = set()
        failed_conditions: set[str] = set()
        with self._final_prefetch_lock:
            for future in completed:
                condition_id, token_id, _generation = future_context[future]
                tradability_ready, book_ready = future.result()
                if tradability_ready:
                    successful_conditions.add(condition_id)
                else:
                    failed_conditions.add(condition_id)
                if book_ready:
                    ready += 1
                    scheduled_ready += 1
                else:
                    self._failed_prefetch_tokens.add(token_id)
            for future in unfinished:
                condition_id, token_id, generation = future_context[future]
                if self._final_prefetch_generation.get(token_id) == generation:
                    self._final_prefetch_generation[token_id] = generation + 1
                failed_conditions.add(condition_id)
                self._failed_prefetch_tokens.add(token_id)
            self._failed_prefetch_conditions.update(
                failed_conditions - successful_conditions
            )
        for future in unfinished:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        return {
            "requested": len(unique_checks),
            "book_ready": ready,
            "failed": len(completed) - scheduled_ready,
            "deferred": len(unfinished),
        }


class RealtimeEvaluationCoalescer:
    """Coalesce WebSocket token updates before running strategy evaluation."""

    def __init__(
        self,
        *,
        event_key_by_token: dict[str, str],
        evaluator: Callable[[set[str]], None],
        max_pending_events: int = REALTIME_EVALUATION_QUEUE_MAX_EVENTS,
        max_batch_events: int = REALTIME_EVALUATION_BATCH_MAX_EVENTS,
        max_normal_batch_events: int = REALTIME_NORMAL_EVALUATION_BATCH_MAX_EVENTS,
        coalesce_seconds: float = REALTIME_EVALUATION_COALESCE_SECONDS,
        event_priority: Callable[[str], tuple] | None = None,
        status_update: Callable[[dict[str, object]], None] | None = None,
        max_event_retries: int = 1,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.event_key_by_token = {str(token): str(event_key) for token, event_key in event_key_by_token.items()}
        self.evaluator = evaluator
        self.max_pending_events = max(1, int(max_pending_events))
        self.max_batch_events = max(1, int(max_batch_events))
        self.max_normal_batch_events = max(1, min(int(max_normal_batch_events), self.max_batch_events))
        self.coalesce_seconds = max(0.0, float(coalesce_seconds))
        self.event_priority = event_priority
        self.status_update = status_update
        self.max_event_retries = max(0, int(max_event_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self._condition = threading.Condition()
        self._pending_tokens_by_event: dict[str, set[str]] = {}
        self._urgent_event_keys: set[str] = set()
        self._urgent_enqueued_monotonic_by_event: dict[str, float] = {}
        self._retry_count_by_event: dict[str, int] = {}
        self._inflight_urgent_event_keys: set[str] = set()
        self._stop_requested = False
        self._drain_on_stop = True
        self._thread: threading.Thread | None = None
        self._enqueued_update_count = 0
        self._coalesced_update_count = 0
        self._dropped_update_count = 0
        self._dropped_urgent_update_count = 0
        self._processed_batch_count = 0
        self._processed_event_count = 0
        self._urgent_processed_event_count = 0
        self._error_count = 0
        self._inflight_event_count = 0
        self._last_error = ""
        self._last_error_at: str | None = None
        self._last_evaluated_at: str | None = None
        self._last_evaluation_duration_seconds: float | None = None
        self._last_urgent_enqueued_at: str | None = None
        self._last_urgent_evaluated_at: str | None = None
        self._last_urgent_queue_wait_seconds: float | None = None
        self._last_urgent_evaluation_duration_seconds: float | None = None
        self._last_urgent_evaluation_lag_seconds: float | None = None

    def start(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_requested = False
            self._drain_on_stop = True
            self._thread = threading.Thread(
                target=self._run,
                name="polymarket-realtime-evaluator",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> None:
        with self._condition:
            self._stop_requested = True
            self._drain_on_stop = drain
            if not drain:
                self._pending_tokens_by_event.clear()
                self._urgent_event_keys.clear()
                self._urgent_enqueued_monotonic_by_event.clear()
                self._retry_count_by_event.clear()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))

    def enqueue_tokens(self, updated_token_ids: set[str], *, urgent: bool = False) -> int:
        tokens_by_event: dict[str, set[str]] = {}
        for token_id in updated_token_ids:
            token = str(token_id)
            event_key = self.event_key_by_token.get(token)
            if event_key:
                tokens_by_event.setdefault(event_key, set()).add(token)
        if not tokens_by_event:
            return 0

        accepted = 0
        with self._condition:
            if self._stop_requested:
                self._dropped_update_count += len(tokens_by_event)
                if urgent:
                    self._dropped_urgent_update_count += len(tokens_by_event)
                return 0
            for event_key, tokens in tokens_by_event.items():
                pending = self._pending_tokens_by_event.get(event_key)
                if pending is not None:
                    pending.update(tokens)
                    if urgent:
                        self._urgent_event_keys.add(event_key)
                        self._urgent_enqueued_monotonic_by_event.setdefault(
                            event_key,
                            time.monotonic(),
                        )
                    self._coalesced_update_count += 1
                    accepted += 1
                    continue
                if len(self._pending_tokens_by_event) >= self.max_pending_events:
                    if urgent:
                        normal_event_key = next(
                            (
                                key
                                for key in reversed(self._pending_tokens_by_event)
                                if key not in self._urgent_event_keys
                            ),
                            None,
                        )
                        if normal_event_key is not None:
                            self._pending_tokens_by_event.pop(normal_event_key, None)
                            self._dropped_update_count += 1
                        else:
                            self._dropped_update_count += 1
                            self._dropped_urgent_update_count += 1
                            continue
                    else:
                        self._dropped_update_count += 1
                        continue
                self._pending_tokens_by_event[event_key] = set(tokens)
                if urgent:
                    self._urgent_event_keys.add(event_key)
                    self._urgent_enqueued_monotonic_by_event.setdefault(
                        event_key,
                        time.monotonic(),
                    )
                self._enqueued_update_count += 1
                accepted += 1
            if accepted:
                if urgent:
                    self._last_urgent_enqueued_at = utc_now_iso()
                self._condition.notify_all()
        return accepted

    def status_snapshot(self) -> dict[str, object]:
        with self._condition:
            return {
                "thread_alive": bool(self._thread is not None and self._thread.is_alive()),
                "queue_depth": len(self._pending_tokens_by_event),
                "urgent_queue_depth": len(
                    self._urgent_event_keys.intersection(self._pending_tokens_by_event)
                ),
                "max_pending_events": self.max_pending_events,
                "max_batch_events": self.max_batch_events,
                "max_normal_batch_events": self.max_normal_batch_events,
                "coalesce_seconds": self.coalesce_seconds,
                "inflight_event_count": self._inflight_event_count,
                "enqueued_update_count": self._enqueued_update_count,
                "coalesced_update_count": self._coalesced_update_count,
                "dropped_update_count": self._dropped_update_count,
                "dropped_urgent_update_count": self._dropped_urgent_update_count,
                "processed_batch_count": self._processed_batch_count,
                "processed_event_count": self._processed_event_count,
                "urgent_processed_event_count": self._urgent_processed_event_count,
                "error_count": self._error_count,
                "last_error": self._last_error,
                "last_error_at": self._last_error_at,
                "last_evaluated_at": self._last_evaluated_at,
                "last_evaluation_duration_seconds": self._last_evaluation_duration_seconds,
                "last_urgent_enqueued_at": self._last_urgent_enqueued_at,
                "last_urgent_evaluated_at": self._last_urgent_evaluated_at,
                "last_urgent_queue_wait_seconds": self._last_urgent_queue_wait_seconds,
                "last_urgent_evaluation_duration_seconds": self._last_urgent_evaluation_duration_seconds,
                "last_urgent_evaluation_lag_seconds": self._last_urgent_evaluation_lag_seconds,
            }

    def _run(self) -> None:
        coalesce_next_batch = True
        while True:
            with self._condition:
                while not self._pending_tokens_by_event and not self._stop_requested:
                    self._condition.wait()
                    coalesce_next_batch = True
                if self._stop_requested and (not self._drain_on_stop or not self._pending_tokens_by_event):
                    return

                if coalesce_next_batch:
                    deadline = time.monotonic() + self.coalesce_seconds
                    while True:
                        if self._stop_requested and not self._drain_on_stop:
                            return
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self._condition.wait(timeout=remaining)

                pending = self._pop_next_pending_batch_locked()
                coalesce_next_batch = not self._pending_tokens_by_event

            self._evaluate_pending_batch(pending)

    def _pop_next_pending_batch_locked(self) -> dict[str, set[str]]:
        event_keys = list(self._pending_tokens_by_event)
        def priority(event_key: str) -> tuple:
            base = self.event_priority(event_key) if self.event_priority is not None else ()
            if not isinstance(base, tuple):
                base = (base,)
            return (0 if event_key in self._urgent_event_keys else 1, *base)

        event_keys = sorted(event_keys, key=priority)
        urgent_event_keys = [
            event_key for event_key in event_keys if event_key in self._urgent_event_keys
        ]
        if urgent_event_keys:
            event_keys = urgent_event_keys[: self.max_batch_events]
        else:
            event_keys = event_keys[: self.max_normal_batch_events]
        pending = {
            event_key: self._pending_tokens_by_event.pop(event_key)
            for event_key in event_keys
        }
        self._inflight_urgent_event_keys = self._urgent_event_keys.intersection(event_keys)
        self._urgent_event_keys.difference_update(event_keys)
        self._inflight_event_count = len(pending)
        return pending

    def _run_pending_batch_once(self) -> bool:
        with self._condition:
            if not self._pending_tokens_by_event:
                return False
            pending = self._pop_next_pending_batch_locked()
        self._evaluate_pending_batch(pending)
        return True

    def _evaluate_pending_batch(self, pending: dict[str, set[str]]) -> None:
        updated_token_ids = {token for tokens in pending.values() for token in tokens}
        urgent_event_keys = set(self._inflight_urgent_event_keys)
        started_at = time.monotonic()
        with self._condition:
            urgent_enqueued_at = [
                self._urgent_enqueued_monotonic_by_event[event_key]
                for event_key in urgent_event_keys.intersection(pending)
                if event_key in self._urgent_enqueued_monotonic_by_event
            ]
        try:
            self.evaluator(updated_token_ids)
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            self._retry_failed_batch(pending, urgent_event_keys)
        else:
            with self._condition:
                urgent_completed = urgent_event_keys.intersection(pending)
                if urgent_completed:
                    completed_at = time.monotonic()
                    self._urgent_processed_event_count += len(urgent_completed)
                    self._last_urgent_evaluated_at = utc_now_iso()
                    self._last_urgent_queue_wait_seconds = (
                        round(max(started_at - enqueued for enqueued in urgent_enqueued_at), 3)
                        if urgent_enqueued_at
                        else None
                    )
                    self._last_urgent_evaluation_duration_seconds = round(
                        completed_at - started_at,
                        3,
                    )
                    self._last_urgent_evaluation_lag_seconds = (
                        round(max(completed_at - enqueued for enqueued in urgent_enqueued_at), 3)
                        if urgent_enqueued_at
                        else None
                    )
                for event_key in pending:
                    self._retry_count_by_event.pop(event_key, None)
                    self._urgent_enqueued_monotonic_by_event.pop(event_key, None)
        finally:
            duration = time.monotonic() - started_at
            with self._condition:
                self._processed_batch_count += 1
                self._processed_event_count += len(pending)
                self._inflight_event_count = 0
                self._inflight_urgent_event_keys.clear()
                self._last_evaluated_at = utc_now_iso()
                self._last_evaluation_duration_seconds = round(duration, 3)

    def _retry_failed_batch(
        self,
        pending: dict[str, set[str]],
        urgent_event_keys: set[str],
    ) -> None:
        retryable: dict[str, set[str]] = {}
        with self._condition:
            for event_key, tokens in pending.items():
                retry_count = self._retry_count_by_event.get(event_key, 0)
                if retry_count >= self.max_event_retries:
                    self._retry_count_by_event.pop(event_key, None)
                    self._urgent_enqueued_monotonic_by_event.pop(event_key, None)
                    continue
                self._retry_count_by_event[event_key] = retry_count + 1
                retryable[event_key] = tokens
        if not retryable:
            return
        if self.retry_backoff_seconds:
            time.sleep(self.retry_backoff_seconds)
        normal_tokens = {
            token
            for event_key, tokens in retryable.items()
            if event_key not in urgent_event_keys
            for token in tokens
        }
        urgent_tokens = {
            token
            for event_key, tokens in retryable.items()
            if event_key in urgent_event_keys
            for token in tokens
        }
        self.enqueue_tokens(normal_tokens)
        self.enqueue_tokens(urgent_tokens, urgent=True)

    def _record_error(self, exc: BaseException) -> None:
        safe_error = " ".join(str(exc).split())[:240]
        if not safe_error:
            safe_error = exc.__class__.__name__
        with self._condition:
            self._error_count += 1
            self._last_error = f"{exc.__class__.__name__}: {safe_error}"
            self._last_error_at = utc_now_iso()
            snapshot = self.status_snapshot()
        if self.status_update is not None:
            try:
                self.status_update(snapshot)
            except Exception:
                pass


def _enqueue_realtime_update(
    evaluator_worker: RealtimeEvaluationCoalescer | None,
    updated_token_ids: set[str],
    *,
    urgent: bool = False,
) -> int:
    if evaluator_worker is None:
        return 0
    return evaluator_worker.enqueue_tokens(updated_token_ids, urgent=urgent)


def _enqueue_due_candidate_book_retries(
    evaluator_worker: RealtimeEvaluationCoalescer | None,
    retries_by_token: dict[str, tuple[float, str, bool]],
    *,
    now_monotonic: float | None = None,
) -> int:
    """Recheck quiet candidate books without waiting forever for WebSocket traffic."""
    current = time.monotonic() if now_monotonic is None else now_monotonic
    due = sorted(
        (
            (token_id, status, urgent)
            for token_id, (due_at, status, urgent) in retries_by_token.items()
            if due_at <= current
        ),
        key=lambda item: (not item[2], item[0]),
    )[:64]
    enqueued = 0
    for urgent in (True, False):
        selected = {token_id for token_id, _status, is_urgent in due if is_urgent == urgent}
        if not selected:
            continue
        for token_id, status, is_urgent in due:
            if token_id in selected:
                delay = 5.0 if status == "empty_ask" else 2.0
                retries_by_token[token_id] = (current + delay, status, is_urgent)
        accepted = _enqueue_realtime_update(evaluator_worker, selected, urgent=urgent)
        enqueued += accepted
        if accepted == 0:
            for token_id in selected:
                _due_at, status, is_urgent = retries_by_token[token_id]
                retries_by_token[token_id] = (current + 1.0, status, is_urgent)
    return enqueued


def _datetime_state_text(value: Any) -> str:
    if isinstance(value, datetime):
        return _iso_datetime(value)
    return str(value or "")


def _station_observation_state_key(observation: Any) -> tuple[Any, ...]:
    return (
        str(getattr(observation, "station_id", "") or "").upper(),
        str(getattr(observation, "source", "") or ""),
        _datetime_state_text(getattr(observation, "observed_at", None)),
        _datetime_state_text(getattr(observation, "high_observed_at", None)),
        _datetime_state_text(getattr(observation, "high_last_observed_at", None)),
        _datetime_state_text(getattr(observation, "high_drop_observed_at", None)),
        _datetime_state_text(getattr(observation, "low_observed_at", None)),
        _datetime_state_text(getattr(observation, "low_last_observed_at", None)),
        _datetime_state_text(getattr(observation, "low_rise_observed_at", None)),
        _finite_float(getattr(observation, "observed_high_c", None)),
        _finite_float(getattr(observation, "observed_low_c", None)),
        _finite_float(getattr(observation, "latest_temp_c", None)),
        int(getattr(observation, "high_bucket_confirmations", 0) or 0),
        str(getattr(observation, "data_block_reason", "") or ""),
        str(getattr(observation, "unavailable_reason", "") or ""),
        str(getattr(observation, "observation_due_status", "") or ""),
        bool(getattr(observation, "daily_extremes_complete", True)),
        str(getattr(observation, "midnight_reset_status", "") or ""),
        str(getattr(observation, "fast_shadow_state_key", "") or ""),
    )


def _market_station_id(market: RawMarket, parsed: Any) -> str:
    provenance_station_id = (
        market.rule_provenance.station_id
        if market.rule_provenance is not None
        else ""
    )
    if provenance_station_id:
        return str(provenance_station_id).upper()
    station = TRADING_READY_STATION_MAP.get(str(parsed.city or "").lower())
    return station.station_id.upper() if station is not None else ""


def _is_realtime_no_candidate(parsed: Any) -> bool:
    return parsed.variable == "temperature" and (
        (
            parsed.temperature_metric == "max"
            and parsed.temperature_bucket in {"exact", "lower_tail", "upper_tail"}
        )
        or (
            parsed.temperature_metric == "min"
            and parsed.temperature_bucket == "exact"
        )
    )


def _station_refresh_high_exact_no_probe_candidates(
    markets: list[RawMarket],
    *,
    station_ids: set[str] | None = None,
) -> list[tuple[str, set[str], set[str]]]:
    candidates: list[tuple[str, set[str], set[str]]] = []
    event_index: dict[str, int] = {}
    restrict_to_station_ids = station_ids is not None
    allowed_station_ids = {str(station_id).upper() for station_id in station_ids or set()}
    for market in markets:
        try:
            parsed = parse_weather_question(market.question)
        except Exception:  # noqa: BLE001
            continue
        if restrict_to_station_ids and _market_station_id(market, parsed) not in allowed_station_ids:
            continue
        if not _is_realtime_no_candidate(parsed):
            continue
        event_key = _market_event_key(market)
        if event_key in event_index:
            index = event_index[event_key]
            if market.no_token_id:
                candidates[index][1].add(str(market.no_token_id))
            candidates[index][2].add(market.market_id)
            continue
        if not market.no_token_id:
            continue
        event_index[event_key] = len(candidates)
        candidates.append((event_key, {str(market.no_token_id)}, {market.market_id}))
    return candidates


def _station_refresh_high_exact_no_probe_tokens(
    markets: list[RawMarket],
    *,
    max_events: int = REALTIME_STATION_REFRESH_PROBE_MAX_EVENTS,
    station_ids: set[str] | None = None,
) -> tuple[set[str], set[str]]:
    global _station_refresh_probe_cursor
    candidates = _station_refresh_high_exact_no_probe_candidates(markets, station_ids=station_ids)
    if not candidates:
        return set(), set()

    if station_ids is not None:
        limit = len(candidates)
    else:
        limit = min(len(candidates), max(1, int(max_events)))
    start = _station_refresh_probe_cursor % len(candidates)
    selected = [candidates[(start + offset) % len(candidates)] for offset in range(limit)]
    _station_refresh_probe_cursor = (start + limit) % len(candidates)
    token_ids = {
        token_id
        for _event_key, event_token_ids, _market_ids in selected
        for token_id in event_token_ids
    }
    market_ids_to_expire = {
        market_id
        for _event_key, _event_token_ids, market_ids in selected
        for market_id in market_ids
    }
    return token_ids, market_ids_to_expire


def _realtime_evaluation_trigger_tokens(
    stream_markets: list[RawMarket],
    broker: PaperBroker,
) -> dict[str, str]:
    """Return candidate and held tokens allowed to wake strategy evaluation."""
    trigger_tokens: dict[str, str] = {}
    for market in stream_markets:
        try:
            parsed = parse_weather_question(market.question)
        except Exception:  # noqa: BLE001
            continue
        if _is_realtime_no_candidate(parsed) and market.no_token_id:
            event_key = _market_event_key(market)
            trigger_tokens[str(market.no_token_id)] = event_key
            if market.yes_token_id:
                trigger_tokens[str(market.yes_token_id)] = event_key
    market_by_id = {market.market_id: market for market in stream_markets}
    for pos in broker.state.positions:
        if not pos.token_id:
            continue
        market = market_by_id.get(pos.market_id) or _market_from_position(pos)
        trigger_tokens[str(pos.token_id)] = _market_event_key(market)
    return trigger_tokens


def _orderbook_update_tokens_for_realtime_evaluation(
    updated_token_ids: set[str],
    broker: PaperBroker,
    watched_token_ids: set[str] | None = None,
) -> set[str]:
    held_tokens = {str(pos.token_id) for pos in broker.state.positions if pos.token_id}
    watched_tokens = {str(token_id) for token_id in watched_token_ids or set() if str(token_id)}
    return {
        str(token_id)
        for token_id in updated_token_ids
        if str(token_id) in held_tokens or str(token_id) in watched_tokens
    }


def _realtime_price_watch_token_ids(
    markets: list[RawMarket],
    broker: PaperBroker,
    signals_by_market: dict[str, WeatherSignal],
    settings: Settings,
) -> set[str]:
    watched = {str(pos.token_id) for pos in broker.state.positions if pos.token_id}
    for market in markets:
        if not market.no_token_id:
            continue
        signal = signals_by_market.get(market.market_id)
        if signal is None or not _realtime_signal_allows_new_entry(signal, settings, market):
            continue
        watched.add(str(market.no_token_id))
        if market.yes_token_id:
            watched.add(str(market.yes_token_id))
    return watched


def _realtime_signal_allows_new_entry(
    signal: WeatherSignal,
    settings: Settings,
    market: RawMarket | None = None,
) -> bool:
    min_confidence, _min_edge, _entry_fraction = _market_params(settings, "temperature")
    if signal.confidence < min_confidence:
        return False
    if settings.strategy_mode in LOCK_EXACT_NO_STRATEGY_MODES:
        side = _preferred_entry_side(signal)
        if market is None:
            return _is_exact_no_lock(side or "", signal)
        return _exact_no_entry_skip_reason(market, signal, side, settings) is None
    if settings.official_nowcast_entry_only and not _is_official_station_entry_signal(signal):
        return False
    if settings.no_only_new_entries and _preferred_entry_side(signal) != "NO":
        return False
    return True


def _enqueue_station_refresh_high_exact_no_probes(
    evaluator_worker: RealtimeEvaluationCoalescer | None,
    markets: list[RawMarket],
    signal_refreshed_at_by_market: dict[str, datetime] | None,
    *,
    pending_signal_invalidations: SimpleQueue[str] | None = None,
    station_ids: set[str] | None = None,
) -> int:
    token_ids, market_ids_to_expire = _station_refresh_high_exact_no_probe_tokens(markets, station_ids=station_ids)
    if pending_signal_invalidations is not None:
        for market_id in market_ids_to_expire:
            pending_signal_invalidations.put(market_id)
    elif signal_refreshed_at_by_market is not None:
        for market_id in market_ids_to_expire:
            signal_refreshed_at_by_market.pop(market_id, None)
    return _enqueue_realtime_update(evaluator_worker, token_ids, urgent=True)


def _drain_pending_signal_invalidations(
    pending_signal_invalidations: SimpleQueue[str],
    signal_refreshed_at_by_market: dict[str, datetime],
) -> None:
    while True:
        try:
            market_id = pending_signal_invalidations.get_nowait()
        except Empty:
            return
        signal_refreshed_at_by_market.pop(market_id, None)


def position_size_usd(
    side_probability: float,
    p_eff: float,
    settings: Settings,
    bankroll_usd: float,
    entry_fraction_override: float | None = None,
    net_edge: float = 0.0,
    min_edge: float | None = None,
    confidence: float = 1.0,
    min_confidence: float = 0.50,
) -> float:
    """Return target paper order size in USD."""
    if entry_fraction_override is not None:
        frac = min(max(0.0, entry_fraction_override), settings.max_single_market_fraction)
    elif settings.size_mode.lower() == "kelly":
        frac = fractional_kelly_binary(
            side_probability,
            p_eff,
            settings.fractional_kelly,
            settings.max_single_market_fraction,
            gamma=settings.probability_shrink_gamma,
        )
    else:
        base = entry_fraction_override if entry_fraction_override is not None else settings.entry_fraction
        edge_floor = min_edge if min_edge is not None else settings.min_net_edge
        edge_scale = 0.5 if (net_edge > 0 and net_edge < edge_floor * 2.0) else 1.0
        frac = min(base * edge_scale, settings.max_single_market_fraction)
    confidence_multiplier = confidence_size_multiplier(
        confidence,
        min_confidence=min_confidence,
        floor=settings.confidence_size_floor,
    )
    frac *= confidence_multiplier
    return bankroll_usd * max(0.0, frac)


def _market_params(settings: Settings, market_type: str) -> tuple[float, float, float | None]:
    return 0.50, settings.min_net_edge, None


def _bid_notional(book: OrderBook, min_price: float = 0.01) -> float:
    return sum(level.price * level.size for level in book.bids if level.price >= min_price)


def _skip_entry_result(result: EdgeResult, reason: str, p_exec: float | None = None, net_edge: float | None = None) -> EdgeResult:
    return replace(
        result,
        side="SKIP",
        p_exec=result.p_exec if p_exec is None else p_exec,
        net_edge=result.net_edge if net_edge is None else net_edge,
        size_usd=0.0,
        size_shares=0.0,
        reason=reason,
        expected_net_profit_usd=0.0,
        price_anomaly=False,
    )


def _market_tradability_skip_reason(
    market: RawMarket,
    client: PolymarketClient | None,
    _settings: Settings,
    *,
    final_pre_trade: bool,
    market_type: str,
    signal: WeatherSignal | None = None,
) -> str | None:
    suffix = f"final_pre_trade={str(final_pre_trade).lower()} [{market_type}]"
    if not market.active:
        return f"SKIP_MARKET_INACTIVE: market.active is not true; {suffix}"
    if market.closed:
        return f"SKIP_MARKET_CLOSED: market.closed is true; {suffix}"
    if market.archived is True:
        return f"SKIP_MARKET_ARCHIVED: market.archived is true; {suffix}"
    if market.accepting_orders is False:
        return f"SKIP_NOT_ACCEPTING_ORDERS: market is not accepting orders; {suffix}"
    if market.enable_order_book is False:
        return f"SKIP_ORDERBOOK_DISABLED: market order book is disabled; {suffix}"
    if not final_pre_trade:
        return None
    if not market.condition_id:
        return f"SKIP_NO_CONDITION_ID: CLOB tradability cannot be checked; {suffix}"
    if client is None:
        return f"SKIP_TRADABILITY_UNKNOWN: CLOB client is unavailable; {suffix}"

    try:
        tradability = client.get_clob_market_tradability(market.condition_id)
    except Exception as exc:  # noqa: BLE001
        return (
            "SKIP_TRADABILITY_UNKNOWN: CLOB tradability lookup failed: "
            f"{exc.__class__.__name__}: {exc}; {suffix}"
        )

    returned_condition_id = str(getattr(tradability, "condition_id", "") or "").strip()
    if returned_condition_id and returned_condition_id != market.condition_id:
        return (
            "SKIP_TRADABILITY_UNKNOWN: CLOB condition_id mismatch "
            f"expected={market.condition_id} actual={returned_condition_id}; {suffix}"
        )

    active = getattr(tradability, "active", None)
    closed = getattr(tradability, "closed", None)
    archived = getattr(tradability, "archived", None)
    accepting_orders = getattr(tradability, "accepting_orders", None)
    enable_order_book = getattr(tradability, "enable_order_book", None)
    active = market.active if active is None else active
    closed = market.closed if closed is None else closed
    archived = market.archived if archived is None else archived
    accepting_orders = market.accepting_orders if accepting_orders is None else accepting_orders
    enable_order_book = market.enable_order_book if enable_order_book is None else enable_order_book

    if active is False:
        return f"SKIP_MARKET_INACTIVE: CLOB market is inactive; {suffix}"
    if closed is True:
        return f"SKIP_MARKET_CLOSED: CLOB market is closed; {suffix}"
    if archived is True:
        return f"SKIP_MARKET_ARCHIVED: CLOB market is archived; {suffix}"
    if accepting_orders is False:
        return f"SKIP_NOT_ACCEPTING_ORDERS: CLOB market is not accepting orders; {suffix}"
    if enable_order_book is False:
        return f"SKIP_ORDERBOOK_DISABLED: CLOB market order book is disabled; {suffix}"
    if active is not True or closed is not False or accepting_orders is not True or enable_order_book is not True:
        return f"SKIP_TRADABILITY_UNKNOWN: required CLOB tradability fields are unknown; {suffix}"
    if signal is not None:
        if isinstance(signal.nowcast, dict):
            signal.nowcast.update(
                {
                    "clob_accepting_orders": accepting_orders,
                    "clob_enable_order_book": enable_order_book,
                    "clob_active": active,
                    "clob_closed": closed,
                    "clob_end_date_iso": getattr(tradability, "end_date_iso", None),
                }
            )
        feasibility_reason = _high_formation_clob_close_reason(signal, tradability, market_type)
        if feasibility_reason:
            return feasibility_reason
    return None


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _high_formation_clob_close_reason(
    signal: WeatherSignal,
    tradability: MarketTradability,
    market_type: str,
) -> str | None:
    if not str(signal.source or "").startswith("official-station-residual-high-"):
        return None
    nowcast = signal.nowcast if isinstance(signal.nowcast, dict) else {}
    timezone_name = str(nowcast.get("station_timezone") or "")
    target_date_text = str(nowcast.get("target_date_local") or "")
    close_at = _parse_iso_datetime(getattr(tradability, "end_date_iso", None))
    try:
        target_date = date.fromisoformat(target_date_text)
        first_final_q25 = int(float(nowcast.get("first_final_high_local_minute_q25")))
        zone = ZoneInfo(timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return None
    if close_at is None:
        return None
    close_local = close_at.astimezone(zone)
    close_local_minute = close_local.hour * 60 + close_local.minute
    if close_local.date() < target_date or (
        close_local.date() == target_date and close_local_minute < first_final_q25
    ):
        nowcast["data_block_reason"] = "clob-closes-before-high-formation"
        nowcast["strategy_allowed_reason"] = "blocked because CLOB closes before high formation"
        return (
            "SKIP_HIGH_FORMATION_AFTER_CLOB_CLOSE: verified CLOB close precedes the "
            f"historical high-formation window; clob_close_local={close_local.isoformat()}; "
            f"first_final_high_q25_minute={first_final_q25}; final_pre_trade=true [{market_type}]"
        )
    return None


def _spread_guard_reason(side: str, ask: float, bid: float, settings: Settings, market_type: str) -> str | None:
    spread_abs = max(0.0, ask - bid)
    if spread_abs > settings.max_entry_spread_abs + 1e-12:
        return (
            f"SKIP_WIDE_SPREAD: {side} liquidity filter: "
            f"spread_abs={spread_abs:.4f} > max_abs={settings.max_entry_spread_abs:.4f} "
            f"[{market_type}]"
        )
    spread_pct = spread_abs / ask if ask > 0 else math.inf
    if spread_pct > settings.max_entry_spread_pct + 1e-12:
        return (
            f"SKIP_WIDE_SPREAD: {side} liquidity filter: "
            f"spread_pct={spread_pct:.2%} > max_pct={settings.max_entry_spread_pct:.2%}; "
            f"spread_abs={spread_abs:.4f}, ask={ask:.4f} [{market_type}]"
        )
    return None


def _price_impact_guard_reason(
    side: str,
    book: OrderBook,
    p_exec: float,
    slippage: float,
    settings: Settings,
    market_type: str,
) -> str | None:
    best_ask = book.best_ask or p_exec
    impact_pct = slippage / best_ask if best_ask > 0 else math.inf
    if (
        slippage <= settings.max_entry_spread_abs
        and impact_pct <= settings.max_entry_spread_pct
    ):
        return None
    return (
        f"SKIP_EXCESSIVE_PRICE_IMPACT: {side} VWAP impact={slippage:.4f} "
        f"({impact_pct:.1%}) exceeds limits "
        f"abs={settings.max_entry_spread_abs:.4f}, "
        f"pct={settings.max_entry_spread_pct:.1%} [{market_type}]"
    )


def _side_liquidity_reason(side: str, book: OrderBook, settings: Settings, market_type: str) -> str | None:
    ask = book.best_ask
    bid = book.best_bid
    if ask is None:
        return f"SKIP_NO_EXECUTABLE_DEPTH: {side} liquidity filter: no ask [{market_type}]"
    if bid is None:
        return f"SKIP_NO_EXECUTABLE_DEPTH: {side} liquidity filter: no bid [{market_type}]"
    if ask >= 1.0 or ask <= 0.0:
        return f"SKIP_NO_EXECUTABLE_DEPTH: {side} liquidity filter: invalid ask={ask:.3f} [{market_type}]"
    if bid >= ask - 1e-12:
        return (
            f"SKIP_CROSSED_ORDER_BOOK: {side} liquidity filter: "
            f"best_bid={bid:.4f} >= best_ask={ask:.4f}; refresh required [{market_type}]"
        )
    spread_reason = _spread_guard_reason(side, ask, bid, settings, market_type)
    if spread_reason:
        return spread_reason
    if ask < 0.08:
        return f"{side} liquidity filter: extreme low ask={ask:.3f} below 0.08 [{market_type}]"
    bid_value = _bid_notional(book)
    if bid_value < 10.0:
        return (
            f"SKIP_NO_EXECUTABLE_DEPTH: {side} liquidity filter: "
            f"exit bid depth ${bid_value:.1f} < $10 [{market_type}]"
        )
    return None


def _book_is_crossed(book: OrderBook) -> bool:
    return (
        book.best_bid is not None
        and book.best_ask is not None
        and book.best_bid >= book.best_ask - 1e-12
    )


def _opposite_side(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def _market_token_for_side(market: RawMarket, side: str) -> str | None:
    return market.yes_token_id if side == "YES" else market.no_token_id


def _combined_side_order_book(
    market: RawMarket,
    side: str,
    source_books: dict[str, OrderBook],
) -> OrderBook | None:
    """Combine direct depth with the executable 1-opposite complementary route."""
    direct = source_books.get(side)
    if side != "NO":
        return direct
    opposite_side = _opposite_side(side)
    complement = source_books.get(opposite_side)
    token_id = _market_token_for_side(market, side)
    if not token_id or (direct is None and complement is None):
        return None

    direct_crossed = direct is not None and _book_is_crossed(direct)
    complement_crossed = complement is not None and _book_is_crossed(complement)
    ask_entries: list[tuple[OrderLevel, str]] = []
    bid_entries: list[tuple[OrderLevel, str]] = []
    ignored_routes: list[str] = []

    if direct is not None and not direct_crossed:
        ask_entries.extend(
            (level, f"direct:{side}_ask")
            for level in direct.asks
            if level.size > 0
        )
        bid_entries.extend(
            (level, f"direct:{side}_bid")
            for level in direct.bids
            if level.size > 0
        )
    elif direct_crossed:
        ignored_routes.append(f"direct:{side}_crossed")

    direct_best_bid = direct.best_bid if direct is not None and not direct_crossed else None
    direct_best_ask = direct.best_ask if direct is not None and not direct_crossed else None
    if complement is not None and not complement_crossed:
        for level in complement.bids:
            price = round(1.0 - level.price, 12)
            if level.size <= 0 or not 0.0 < price < 1.0:
                continue
            if direct_best_bid is not None and price <= direct_best_bid + 1e-12:
                ignored_routes.append(f"complement:{opposite_side}_bid_crossed")
                continue
            ask_entries.append(
                (OrderLevel(price, level.size), f"complement:{opposite_side}_bid")
            )
        for level in complement.asks:
            price = round(1.0 - level.price, 12)
            if level.size <= 0 or not 0.0 < price < 1.0:
                continue
            if direct_best_ask is not None and price >= direct_best_ask - 1e-12:
                ignored_routes.append(f"complement:{opposite_side}_ask_crossed")
                continue
            bid_entries.append(
                (OrderLevel(price, level.size), f"complement:{opposite_side}_ask")
            )
    elif complement_crossed:
        ignored_routes.append(f"complement:{opposite_side}_crossed")

    ask_entries.sort(key=lambda item: item[0].price)
    bid_entries.sort(key=lambda item: item[0].price, reverse=True)
    route_payload = {
        "side": side,
        "direct_token_id": _market_token_for_side(market, side),
        "complement_token_id": _market_token_for_side(market, opposite_side),
        "ask_routes": [route for _level, route in ask_entries],
        "bid_routes": [route for _level, route in bid_entries],
        "ignored_routes": ignored_routes,
        "direct_available": direct is not None,
        "complement_available": complement is not None,
    }
    return OrderBook(
        str(token_id),
        bids=[level for level, _route in bid_entries],
        asks=[level for level, _route in ask_entries],
        market=(direct.market if direct is not None else complement.market),
        timestamp=(direct.timestamp if direct is not None else complement.timestamp),
        min_order_size=(direct.min_order_size if direct is not None else complement.min_order_size),
        tick_size=(direct.tick_size if direct is not None else complement.tick_size),
        neg_risk=(direct.neg_risk if direct is not None else complement.neg_risk),
        raw={"complementary_liquidity": route_payload},
    )


def _book_ask_routes(book: OrderBook) -> list[str]:
    raw = book.raw if isinstance(book.raw, dict) else {}
    payload = raw.get("complementary_liquidity")
    if not isinstance(payload, dict):
        return []
    routes = payload.get("ask_routes")
    return [str(route) for route in routes] if isinstance(routes, list) else []


def _preferred_entry_side(signal: WeatherSignal) -> str | None:
    yes_probability = (
        signal.conservative_yes_probability
        if signal.conservative_yes_probability is not None
        else signal.p_true
    )
    no_probability = (
        signal.conservative_no_probability
        if signal.conservative_no_probability is not None
        else 1.0 - signal.p_true
    )
    if abs(yes_probability - no_probability) <= 1e-12:
        return None
    return "YES" if yes_probability > no_probability else "NO"


def _fetch_books(
    market: RawMarket,
    client: PolymarketClient,
    *,
    preferred_side: str | None = None,
    allowed_sides: set[str] | None = None,
) -> tuple[dict[str, OrderBook], str | None]:
    books: dict[str, OrderBook] = {}
    source_books: dict[str, OrderBook] = {}
    errors: list[str] = []
    fetch_book = getattr(client, "get_candidate_order_book", None)
    if not callable(fetch_book):
        fetch_book = client.get_order_book
    refresh_book = getattr(client, "refresh_order_book", None)
    requested_sides = allowed_sides or {"YES", "NO"}
    source_sides = requested_sides | {_opposite_side(side) for side in requested_sides}
    for side, token_id in (("YES", market.yes_token_id), ("NO", market.no_token_id)):
        if not token_id or side not in source_sides:
            continue
        try:
            source_books[side] = fetch_book(token_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{side}: {exc}")

    preferred_book = (
        _combined_side_order_book(market, preferred_side, source_books)
        if preferred_side in requested_sides
        else None
    )
    if callable(refresh_book) and preferred_side in requested_sides and (
        preferred_book is None
        or preferred_book.best_ask is None
        or _book_is_crossed(preferred_book)
    ):
        for side in (preferred_side, _opposite_side(preferred_side)):
            token_id = _market_token_for_side(market, side)
            if not token_id:
                continue
            try:
                source_books[side] = refresh_book(token_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{side} REST refresh: {exc}")

    for side in ("YES", "NO"):
        if side not in requested_sides:
            continue
        book = _combined_side_order_book(market, side, source_books)
        if book is not None:
            books[side] = book
    if not books and errors:
        return books, f"order book error: {'; '.join(errors)}"
    return books, None


def _side_edge_metrics(
    side: str,
    signal: WeatherSignal,
    p_exec: float,
    settings: Settings,
) -> tuple[float, float, float]:
    entry_fee_per_share = polymarket_taker_fee_per_share(p_exec, settings.weather_taker_fee_rate)
    explicit_probability = _explicit_conservative_side_probability(side, signal)
    if explicit_probability is not None:
        model_error_margin, resolution_error_margin = _edge_error_margins(signal, settings)
        edge = (
            explicit_probability
            - p_exec
            - entry_fee_per_share
            - model_error_margin
            - resolution_error_margin
        )
        return entry_fee_per_share, edge, explicit_probability
    if side == "YES":
        model_error_margin, resolution_error_margin = _edge_error_margins(signal, settings)
        edge = yes_net_edge(
            signal.p_true,
            p_exec,
            entry_fee_per_share,
            model_error_margin,
            resolution_error_margin,
        )
        side_probability = signal.p_true
    else:
        model_error_margin, resolution_error_margin = _edge_error_margins(signal, settings)
        edge = no_net_edge(
            signal.p_true,
            p_exec,
            entry_fee_per_share,
            model_error_margin,
            resolution_error_margin,
        )
        side_probability = 1.0 - signal.p_true
    return entry_fee_per_share, edge, side_probability


def _max_executable_buy_target_usd(book: OrderBook, fee_rate: float) -> float:
    total = 0.0
    for level in book.asks:
        if level.size <= 0:
            continue
        total += level.size * (level.price + polymarket_taker_fee_per_share(level.price, fee_rate))
    return total


def _max_executable_budget_at_price_cap(
    book: OrderBook,
    *,
    requested_size_usd: float,
    minimum_size_usd: float,
    price_cap: float,
    fee_rate: float,
) -> float | None:
    """Return the largest real ask-side budget whose VWAP clears the cap."""
    shares = 0.0
    notional = 0.0
    for level in book.asks:
        if level.size <= 0:
            continue
        take = level.size
        if level.price > price_cap:
            headroom = price_cap * shares - notional
            if headroom <= 0:
                break
            take = min(take, headroom / (level.price - price_cap))
        shares += take
        notional += take * level.price
        if take + 1e-12 < level.size:
            break

    if shares <= 0:
        return None
    p_exec = notional / shares
    safe_budget = shares * (
        p_exec + polymarket_taker_fee_per_share(p_exec, fee_rate)
    )
    limit = min(requested_size_usd, safe_budget)
    return limit if limit + 1e-9 >= minimum_size_usd else None


def _settlement_return_pct_for_budget(
    book: OrderBook,
    size_usd: float,
    settings: Settings,
) -> tuple[float, float] | None:
    p_exec, shares, _slip = executable_buy_price(
        book,
        size_usd,
        fee_rate=settings.weather_taker_fee_rate,
    )
    if p_exec is None or shares <= 0:
        return None
    estimate = estimate_executable_net_return(
        shares=shares,
        entry_vwap=p_exec,
        expected_exit_price=1.0,
        fee_rate=settings.weather_taker_fee_rate,
        hold_to_settlement=True,
    )
    return p_exec, estimate.expected_net_return_pct


def _lock_only_exact_no_budget(
    side: str,
    book: OrderBook,
    signal: WeatherSignal,
    settings: Settings,
    bankroll_before_entry: float,
) -> float | None:
    if not _is_lock_only_exact_no(side, signal):
        return None
    limit = min(
        bankroll_before_entry,
        _max_executable_buy_target_usd(book, settings.weather_taker_fee_rate),
    )
    if limit < settings.min_order_usd:
        return None

    def clears(size_usd: float) -> bool:
        result = _settlement_return_pct_for_budget(book, size_usd, settings)
        if result is None:
            return False
        p_exec, return_pct = result
        required_return_pct = max(
            settings.entry_min_expected_net_return_pct,
            LOCK_ONLY_EXACT_NO_MIN_NET_RETURN_PCT,
        )
        return (
            p_exec <= LOCK_ONLY_EXACT_NO_MAX_ENTRY_PRICE + 1e-12
            and return_pct >= required_return_pct - 1e-12
        )

    if not clears(settings.min_order_usd):
        return None
    if clears(limit):
        return limit

    low = settings.min_order_usd
    high = limit
    for _ in range(40):
        mid = (low + high) / 2.0
        if clears(mid):
            low = mid
        else:
            high = mid
    return low if low >= settings.min_order_usd else None


def _entry_price_cap_skip_result(
    side: str,
    signal: WeatherSignal,
    settings: Settings,
    p_exec: float,
    market_type: str,
    book: OrderBook | None = None,
) -> EdgeResult | None:
    cap = _entry_price_cap(side, signal)
    if p_exec <= cap + 1e-12:
        return None
    _entry_fee_per_share, edge, _side_probability = _side_edge_metrics(side, signal, p_exec, settings)
    depth_json = ""
    if book is not None:
        _checked_price, checked_shares, _checked_slip = executable_buy_price(
            book,
            settings.min_order_usd,
            fee_rate=settings.weather_taker_fee_rate,
        )
        depth_json = _entry_ask_depth_top5_json(
            book,
            entry_size_usd=settings.min_order_usd,
            entry_vwap=p_exec,
            entry_shares=checked_shares,
            fee_rate=settings.weather_taker_fee_rate,
        )
    return EdgeResult(
        "SKIP",
        signal.p_true,
        p_exec,
        edge,
        0.0,
        0.0,
        (
            f"SKIP_ENTRY_PRICE_TOO_HIGH: {side} p_exec_vwap={p_exec:.4f} "
            f"> max_entry_price={cap:.4f}; edge={edge:.4f} "
            f"[{market_type}]"
        ),
        entry_ask_depth_top5_json=depth_json,
    )


def _entry_ask_depth_top5_json(
    book: OrderBook,
    *,
    entry_size_usd: float,
    entry_vwap: float,
    entry_shares: float,
    fee_rate: float,
) -> str:
    levels: list[dict[str, Any]] = []
    ask_routes = _book_ask_routes(book)
    cumulative_shares = 0.0
    cumulative_notional = 0.0
    cumulative_all_in = 0.0
    for index, level in enumerate(book.asks):
        if level.size <= 0:
            continue
        cumulative_shares += level.size
        notional = level.price * level.size
        all_in = level.size * (level.price + polymarket_taker_fee_per_share(level.price, fee_rate))
        cumulative_notional += notional
        cumulative_all_in += all_in
        level_payload: dict[str, Any] = {
                "price": round(level.price, 6),
                "size": round(level.size, 6),
                "notional_usd": round(notional, 6),
                "all_in_usd": round(all_in, 6),
                "cumulative_size": round(cumulative_shares, 6),
                "cumulative_notional_usd": round(cumulative_notional, 6),
                "cumulative_all_in_usd": round(cumulative_all_in, 6),
        }
        if index < len(ask_routes):
            level_payload["route"] = ask_routes[index]
        levels.append(level_payload)
        if len(levels) >= 5:
            break
    target_vwap, target_shares, target_slip = executable_buy_price(
        book,
        ENTRY_DEPTH_AUDIT_TARGET_USD,
        fee_rate=fee_rate,
    )
    payload = {
        "entry_size_usd": round(entry_size_usd, 6),
        "entry_vwap": round(entry_vwap, 6),
        "entry_shares": round(entry_shares, 6),
        "target_usd": ENTRY_DEPTH_AUDIT_TARGET_USD,
        "target_executable": target_vwap is not None and target_shares > 0,
        "target_vwap": None if target_vwap is None else round(target_vwap, 6),
        "target_shares": round(target_shares, 6),
        "target_slippage": round(target_slip, 6),
        "routes": list(dict.fromkeys(
            str(level["route"])
            for level in levels
            if "route" in level
        )),
        "levels": levels,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _refresh_selected_order_book_before_entry(
    client: PolymarketClient,
    market: RawMarket,
    token_id: str,
    side: str,
    signal: WeatherSignal,
    market_type: str,
) -> tuple[OrderBook | None, str | None, str]:
    refresh = getattr(client, "refresh_order_book", None)
    fetch = refresh if callable(refresh) else client.get_order_book
    source = "rest_helper" if callable(refresh) else "stream"
    source_books: dict[str, OrderBook] = {}
    errors: list[str] = []
    source_sides = (side, _opposite_side(side)) if side == "NO" else (side,)
    for source_side in source_sides:
        source_token = (
            token_id
            if source_side == side
            else _market_token_for_side(market, source_side)
        )
        if not source_token:
            continue
        try:
            source_books[source_side] = fetch(source_token)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source_side}: {exc.__class__.__name__}: {exc}")
    book = _combined_side_order_book(market, side, source_books)
    if book is None:
        return (
            None,
            "SKIP_TRADABILITY_UNKNOWN: pre-trade order book refresh "
            f"failed for selected {side}: {'; '.join(errors) or 'no token book returned'} "
            f"[{market_type}]",
            f"{source}_failed",
        )
    routes = list(dict.fromkeys(_book_ask_routes(book)))
    route_source = "direct"
    if routes and all(route.startswith("complement:") for route in routes):
        route_source = "complement"
    elif any(route.startswith("complement:") for route in routes):
        route_source = "direct+complement"
    return book, None, f"{source}:{route_source}"


def _side_result(
    side: str,
    book: OrderBook,
    signal: WeatherSignal,
    settings: Settings,
    bankroll_before_entry: float,
    min_confidence: float,
    min_edge: float,
    entry_fraction_override: float | None,
    market_type: str,
) -> EdgeResult:
    liquidity_reason = _side_liquidity_reason(side, book, settings, market_type)
    if liquidity_reason:
        return EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, liquidity_reason)

    p_exec, _shares, slip = executable_buy_price(
        book,
        settings.min_order_usd,
        fee_rate=settings.weather_taker_fee_rate,
    )
    if p_exec is None:
        return EdgeResult(
            "SKIP",
            signal.p_true,
            None,
            -999.0,
            0.0,
            0.0,
            f"SKIP_NO_EXECUTABLE_DEPTH: {side} liquidity filter: insufficient ask depth [{market_type}]",
        )
    price_cap_result = _entry_price_cap_skip_result(
        side,
        signal,
        settings,
        p_exec,
        market_type,
        book,
    )
    if price_cap_result is not None:
        return price_cap_result

    lock_only_exact_no = _is_lock_only_exact_no(side, signal)
    upstream_exact_no = _is_upstream_lock_paper_exact_no(side, signal)
    lock_budget = (
        _lock_only_exact_no_budget(
            side,
            book,
            signal,
            settings,
            bankroll_before_entry,
        )
        if lock_only_exact_no
        else None
    )
    entry_fee_per_share = 0.0
    edge = -999.0
    size_usd = 0.0
    requested_size_usd = 0.0
    partial_fill_reason = ""
    observation_tier: ObservationSizingTier | None = None
    effective_entry_fraction = (
        signal.entry_size_fraction_override
        if signal.entry_size_fraction_override is not None
        else entry_fraction_override
    )
    for _attempt in range(4):
        entry_fee_per_share, edge, side_probability = _side_edge_metrics(side, signal, p_exec, settings)
        p_eff = p_exec + entry_fee_per_share
        observation_tier = _observation_edge_fraction(
            signal,
            settings,
            side_probability,
            edge,
        )
        size_fraction_override = (
            observation_tier.entry_fraction
            if observation_tier is not None
            else effective_entry_fraction
        )
        observation_tier, size_fraction_override = _cap_yes_observation_tier(
            side,
            side_probability,
            observation_tier,
            size_fraction_override,
        )
        effective_entry_fraction = size_fraction_override
        if lock_only_exact_no:
            if lock_budget is None:
                return EdgeResult(
                    "SKIP",
                    signal.p_true,
                    p_exec,
                    edge,
                    0.0,
                    0.0,
                    (
                        "SKIP_LOCK_ONLY_FULL_BANKROLL_NO_DEPTH: "
                        f"{side} exact temperature lock has no executable ask budget clearing "
                        f"max_entry_price={LOCK_ONLY_EXACT_NO_MAX_ENTRY_PRICE:.4f} "
                        f"and min_settlement_net_return={LOCK_ONLY_EXACT_NO_MIN_NET_RETURN_PCT:.2%} "
                        f"[{market_type}]"
                    ),
                )
            requested_size_usd = lock_budget
            effective_entry_fraction = min(1.0, requested_size_usd / bankroll_before_entry) if bankroll_before_entry > 0 else 0.0
        else:
            requested_size_usd = position_size_usd(
                side_probability,
                p_eff,
                settings,
                bankroll_before_entry,
                size_fraction_override,
                net_edge=edge,
                min_edge=min_edge,
                confidence=signal.confidence,
                min_confidence=min_confidence,
            )
            if upstream_exact_no:
                requested_size_usd = min(
                    requested_size_usd,
                    bankroll_before_entry * UPSTREAM_LOCK_PAPER_EVENT_CAP_FRACTION,
                )
        size_usd = requested_size_usd
        if size_usd < settings.min_order_usd:
            break
        checked_p_exec, _checked_shares, checked_slip = executable_buy_price(
            book,
            size_usd,
            fee_rate=settings.weather_taker_fee_rate,
        )
        if checked_p_exec is None:
            capped_size_usd = min(
                requested_size_usd,
                _max_executable_buy_target_usd(book, settings.weather_taker_fee_rate),
            )
            if capped_size_usd + 1e-9 < settings.min_order_usd:
                return EdgeResult(
                    "SKIP",
                    signal.p_true,
                    p_exec,
                    edge,
                    0.0,
                    0.0,
                    f"SKIP_NO_EXECUTABLE_DEPTH: {side} liquidity filter: "
                    f"insufficient ask depth for minimum order "
                    f"${settings.min_order_usd:.2f}; available=${max(0.0, capped_size_usd):.2f} [{market_type}]",
                )
            checked_p_exec, _checked_shares, checked_slip = executable_buy_price(
                book,
                capped_size_usd,
                fee_rate=settings.weather_taker_fee_rate,
            )
            if checked_p_exec is None:
                return EdgeResult(
                    "SKIP",
                    signal.p_true,
                    p_exec,
                    edge,
                    0.0,
                    0.0,
                    f"SKIP_NO_EXECUTABLE_DEPTH: {side} liquidity filter: "
                    f"insufficient ask depth for capped order "
                    f"${capped_size_usd:.2f} [{market_type}]",
                )
            size_usd = capped_size_usd
            partial_fill_reason = (
                f", partial_fill=${capped_size_usd:.2f}/${requested_size_usd:.2f}"
            )
        price_cap_result = _entry_price_cap_skip_result(side, signal, settings, checked_p_exec, market_type)
        if price_cap_result is not None and upstream_exact_no:
            capped_size_usd = _max_executable_budget_at_price_cap(
                book,
                requested_size_usd=requested_size_usd,
                minimum_size_usd=settings.min_order_usd,
                price_cap=_entry_price_cap(side, signal),
                fee_rate=settings.weather_taker_fee_rate,
            )
            if capped_size_usd is not None:
                checked_p_exec, _checked_shares, checked_slip = executable_buy_price(
                    book,
                    capped_size_usd,
                    fee_rate=settings.weather_taker_fee_rate,
                )
                size_usd = capped_size_usd
                partial_fill_reason = (
                    f", price_capped_fill=${capped_size_usd:.2f}/${requested_size_usd:.2f}"
                )
                if checked_p_exec is not None:
                    price_cap_result = _entry_price_cap_skip_result(
                        side,
                        signal,
                        settings,
                        checked_p_exec,
                        market_type,
                        book,
                    )
        if price_cap_result is not None:
            return price_cap_result
        if abs(checked_p_exec - p_exec) <= 1e-12 and abs(checked_slip - slip) <= 1e-12:
            break
        p_exec = checked_p_exec
        slip = checked_slip
    else:
        return EdgeResult(
            "SKIP",
            signal.p_true,
            p_exec,
            edge,
            0.0,
            0.0,
            f"{side} liquidity filter: execution price did not stabilize [{market_type}]",
        )

    if size_usd < settings.min_order_usd:
        reason = (
            f"{side} calculated order ${size_usd:.2f} below minimum order "
            f"${settings.min_order_usd:.2f}; skipping before expected-return estimate [{market_type}]"
        )
        return EdgeResult("SKIP", signal.p_true, p_exec, edge, 0.0, 0.0, reason)

    if not lock_only_exact_no:
        price_impact_reason = _price_impact_guard_reason(
            side,
            book,
            p_exec,
            slip,
            settings,
            market_type,
        )
        if price_impact_reason:
            return EdgeResult(
                "SKIP",
                signal.p_true,
                p_exec,
                edge,
                0.0,
                0.0,
                price_impact_reason,
            )

    estimate_shares = fee_adjusted_entry_shares(size_usd, p_exec, settings.weather_taker_fee_rate)
    spread = max(0.0, (book.best_ask or p_exec) - (book.best_bid or p_exec))
    fair = _model_fair_price_for_signal(side, signal, settings)
    expected_exit = target_exit_price(p_exec, fair, settings)
    expected_exit_estimate = estimate_executable_net_return(
        shares=estimate_shares,
        entry_vwap=p_exec,
        expected_exit_price=expected_exit,
        expected_exit_spread=spread,
        expected_exit_slippage=slip,
        fee_rate=settings.weather_taker_fee_rate,
    )
    settlement_estimate = estimate_executable_net_return(
        shares=estimate_shares,
        entry_vwap=p_exec,
        expected_exit_price=_conservative_settlement_value_for_signal(side, signal, settings),
        fee_rate=settings.weather_taker_fee_rate,
        hold_to_settlement=True,
    )
    return_estimate = max(
        (expected_exit_estimate, settlement_estimate),
        key=lambda estimate: estimate.expected_net_return_pct,
    )
    min_return_pct = _entry_min_return_pct(side, signal, settings)
    return_ok = return_estimate.expected_net_return_pct >= min_return_pct - 1e-12
    is_trade = edge > min_edge and size_usd >= settings.min_order_usd and return_ok
    rejection = ""
    if not return_ok:
        rejection = f", reject=expected net return below {min_return_pct:.2%}"
    confidence_multiplier = confidence_size_multiplier(
        signal.confidence,
        min_confidence=min_confidence,
        floor=settings.confidence_size_floor,
    )
    official_lock_note = ""
    if _is_official_nowcast_lock(signal):
        official_lock_note = (
            f", official_nowcast_lock=true, entry_size_reason={signal.entry_size_reason}, "
            f"entry_size_fraction_override={(signal.entry_size_fraction_override or 0.0):.2f}"
        )
        if lock_only_exact_no:
            official_lock_note += (
                ", lock_only_exact_no_full_bankroll=true, "
                f"max_entry_price={LOCK_ONLY_EXACT_NO_MAX_ENTRY_PRICE:.4f}, "
                f"min_settlement_net_return={LOCK_ONLY_EXACT_NO_MIN_NET_RETURN_PCT:.2%}"
            )
    elif _is_intraday_observation_edge(signal):
        official_lock_note = (
            f", intraday_observation_edge=true, entry_size_reason={signal.entry_size_reason}, "
            f"entry_size_fraction_override={(signal.entry_size_fraction_override or 0.0):.4f}"
        )
    ask_routes = list(dict.fromkeys(_book_ask_routes(book)))
    liquidity_route_note = (
        f", liquidity_routes={'+'.join(ask_routes)}" if ask_routes else ""
    )
    reason = (
        f"{side} edge={edge:.4f}, p_exec_vwap={p_exec:.4f}, route={return_estimate.route}, "
        f"expected_exit={return_estimate.expected_exit_price:.4f}, "
        f"expected_gross=${return_estimate.expected_gross_profit_usdc:.4f}, "
        f"estimated_cost=${return_estimate.estimated_cost_usdc:.4f}, "
        f"expected_net_return={return_estimate.expected_net_return_pct:.2%}, "
        f"entry_fee=${return_estimate.entry_fee_usdc:.4f}, "
        f"exit_fee=${return_estimate.exit_fee_usdc:.4f}, "
        f"exit_market_cost=${return_estimate.exit_market_cost_usdc:.4f}, "
        f"best_bid={(book.best_bid or 0.0):.4f}, best_ask={(book.best_ask or 0.0):.4f}, "
        f"spread_audit={spread:.4f}, slip_audit={slip:.4f}, "
        f"confidence_size_multiplier={confidence_multiplier:.3f}"
        f"{official_lock_note}{liquidity_route_note}{partial_fill_reason}{rejection} [{market_type}]"
    )
    return EdgeResult(
        side=side if is_trade else "SKIP",
        p_true=signal.p_true,
        p_exec=p_exec,
        net_edge=edge,
        size_usd=size_usd if edge > min_edge and return_ok else 0.0,
        size_shares=estimate_shares if edge > min_edge and return_ok else 0.0,
        reason=reason,
        expected_net_profit_usd=return_estimate.expected_net_profit_usdc if edge > min_edge and return_ok else 0.0,
        strategy_mode=settings.strategy_mode if _is_official_station_entry_signal(signal) else "",
        signal_family=_base_signal_family(signal),
        entry_size_fraction_override=effective_entry_fraction,
        raw_probability=signal.raw_probability,
        conservative_yes_probability=signal.conservative_yes_probability,
        conservative_no_probability=signal.conservative_no_probability,
        raw_selected_side_probability=_raw_side_probability(side, signal)
        if signal.raw_probability is not None
        else signal.raw_selected_side_probability,
        selected_side_probability=side_probability
        if _explicit_conservative_side_probability(side, signal) is not None
        else signal.selected_side_probability,
        calibration_sample_days=signal.calibration_sample_days,
        calibration_profile_key=signal.calibration_profile_key,
        calibration_status=signal.calibration_status,
        probability_tier=(
            LOCK_ONLY_EXACT_NO_TIER
            if lock_only_exact_no
            else _upstream_lock_paper_probability_tier(signal)
            if upstream_exact_no
            else
            observation_tier.probability_tier
            if observation_tier is not None
            else signal.probability_tier
        ),
        event_cap_override_fraction=(
            1.0
            if lock_only_exact_no
            else
            observation_tier.event_cap_override_fraction
            if observation_tier is not None
            else signal.event_cap_override_fraction
        ),
        requested_size_usd=requested_size_usd,
        executable_size_usd=size_usd,
    )


def _final_pre_trade_entry_result(
    market: RawMarket,
    signal: WeatherSignal,
    result: EdgeResult,
    token_id: str,
    client: PolymarketClient | None,
    settings: Settings,
    market_type: str,
) -> EdgeResult:
    tradability_reason = _market_tradability_skip_reason(
        market,
        client,
        settings,
        final_pre_trade=True,
        market_type=market_type,
        signal=signal,
    )
    if tradability_reason:
        return _skip_entry_result(result, tradability_reason)
    assert client is not None

    rule_mismatch = market_rule_mismatch_reason(market)
    if rule_mismatch:
        return _skip_entry_result(
            result,
            f"SKIP_RULE_MISMATCH: final pre-trade check failed: {rule_mismatch}",
        )
    if settings.strategy_mode in LOCK_EXACT_NO_STRATEGY_MODES:
        strategy_reason = _exact_no_entry_skip_reason(
            market,
            signal,
            result.side,
            settings,
        )
        if strategy_reason is not None:
            return _skip_entry_result(result, strategy_reason)
    refreshed_book, refresh_error, final_book_source = _refresh_selected_order_book_before_entry(
        client,
        market,
        token_id,
        result.side,
        signal,
        market_type,
    )
    if refresh_error:
        return _skip_entry_result(result, refresh_error)
    try:
        book = refreshed_book if refreshed_book is not None else client.get_order_book(token_id)
    except Exception as exc:  # noqa: BLE001
        return _skip_entry_result(
            result,
            f"SKIP_TRADABILITY_UNKNOWN: final pre-trade book fetch failed for "
            f"{result.side}: {exc} [{market_type}]",
        )

    liquidity_reason = _side_liquidity_reason(result.side, book, settings, market_type)
    if liquidity_reason:
        return _skip_entry_result(
            result,
            f"{liquidity_reason}; final_pre_trade=true",
        )

    lock_only_exact_no = _is_lock_only_exact_no(result.side, signal)
    upstream_exact_no = _is_upstream_lock_paper_exact_no(result.side, signal)
    final_size_usd = result.size_usd
    final_size_note = ""
    if lock_only_exact_no:
        safe_budget = _lock_only_exact_no_budget(
            result.side,
            book,
            signal,
            settings,
            result.size_usd,
        )
        if safe_budget is None:
            return _skip_entry_result(
                result,
                "SKIP_FINAL_LOCK_ONLY_BUDGET: final pre-trade book has no executable "
                f"exact-NO amount of at least ${settings.min_order_usd:.2f} clearing "
                f"max_entry_price={LOCK_ONLY_EXACT_NO_MAX_ENTRY_PRICE:.4f} and "
                f"min_settlement_net_return={LOCK_ONLY_EXACT_NO_MIN_NET_RETURN_PCT:.2%} "
                f"[{market_type}]",
            )
        final_size_usd = min(result.size_usd, safe_budget)
        if final_size_usd + 1e-9 < result.size_usd:
            final_size_note = (
                f", final_size_reduced=${final_size_usd:.2f}/${result.size_usd:.2f}"
            )
    elif upstream_exact_no:
        safe_budget = _max_executable_budget_at_price_cap(
            book,
            requested_size_usd=result.size_usd,
            minimum_size_usd=settings.min_order_usd,
            price_cap=_entry_price_cap(result.side, signal),
            fee_rate=settings.weather_taker_fee_rate,
        )
        if safe_budget is None:
            return _skip_entry_result(
                result,
                "SKIP_FINAL_UPSTREAM_BUDGET: final pre-trade book has no executable "
                f"exact-NO amount of at least ${settings.min_order_usd:.2f} clearing "
                f"max_entry_price={_entry_price_cap(result.side, signal):.4f} "
                f"[{market_type}]",
            )
        final_size_usd = min(result.size_usd, safe_budget)
        if final_size_usd + 1e-9 < result.size_usd:
            final_size_note = (
                f", final_size_reduced=${final_size_usd:.2f}/${result.size_usd:.2f}"
            )

    checked_p_exec, checked_shares, checked_slip = executable_buy_price(
        book,
        final_size_usd,
        fee_rate=settings.weather_taker_fee_rate,
    )
    if checked_p_exec is None or checked_shares <= 0:
        return _skip_entry_result(
            result,
            f"SKIP_NO_EXECUTABLE_DEPTH: final pre-trade check failed: "
            f"{result.side} insufficient ask depth "
            f"for ${final_size_usd:.2f} [{market_type}]",
        )
    price_cap_result = _entry_price_cap_skip_result(
        result.side,
        signal,
        settings,
        checked_p_exec,
        market_type,
        book,
    )
    if price_cap_result is not None:
        blocked = _skip_entry_result(
            result,
            f"final pre-trade check failed: {price_cap_result.reason}",
            p_exec=checked_p_exec,
            net_edge=price_cap_result.net_edge,
        )
        return replace(
            blocked,
            entry_ask_depth_top5_json=price_cap_result.entry_ask_depth_top5_json,
        )

    if not lock_only_exact_no:
        price_impact_reason = _price_impact_guard_reason(
            result.side,
            book,
            checked_p_exec,
            checked_slip,
            settings,
            market_type,
        )
        if price_impact_reason:
            return _skip_entry_result(result, f"{price_impact_reason}; final_pre_trade=true")

    _entry_fee_per_share, edge, final_side_probability = _side_edge_metrics(
        result.side,
        signal,
        checked_p_exec,
        settings,
    )
    _unused, min_edge, _unused_entry_fraction = _market_params(settings, market_type)
    spread = max(0.0, (book.best_ask or checked_p_exec) - (book.best_bid or checked_p_exec))
    fair = _model_fair_price_for_signal(result.side, signal, settings)
    expected_exit = target_exit_price(checked_p_exec, fair, settings)
    expected_exit_estimate = estimate_executable_net_return(
        shares=checked_shares,
        entry_vwap=checked_p_exec,
        expected_exit_price=expected_exit,
        expected_exit_spread=spread,
        expected_exit_slippage=checked_slip,
        fee_rate=settings.weather_taker_fee_rate,
    )
    settlement_estimate = estimate_executable_net_return(
        shares=checked_shares,
        entry_vwap=checked_p_exec,
        expected_exit_price=_conservative_settlement_value_for_signal(result.side, signal, settings),
        fee_rate=settings.weather_taker_fee_rate,
        hold_to_settlement=True,
    )
    return_estimate = max(
        (expected_exit_estimate, settlement_estimate),
        key=lambda estimate: estimate.expected_net_return_pct,
    )
    min_return_pct = _entry_min_return_pct(result.side, signal, settings)
    return_ok = return_estimate.expected_net_return_pct >= min_return_pct - 1e-12
    if edge <= min_edge or not return_ok:
        return _skip_entry_result(
            result,
            f"SKIP_FINAL_EDGE: final pre-trade check failed: edge={edge:.4f} "
            f"threshold={min_edge:.4f}, expected_net_return={return_estimate.expected_net_return_pct:.2%} "
            f"threshold={min_return_pct:.2%} [{market_type}]",
            p_exec=checked_p_exec,
            net_edge=edge,
        )

    price_anomaly = (
        settings.strategy_mode in {"intraday_observation_edge", "hybrid_observation_edge"}
        and _is_official_station_entry_signal(signal)
        and is_abnormal_price_opportunity(
            final_side_probability,
            edge,
            return_estimate.expected_net_return_pct,
            min_side_probability=settings.intraday_min_side_probability,
            abnormal_min_net_edge=settings.intraday_abnormal_min_net_edge,
            min_expected_net_return_pct=settings.entry_min_expected_net_return_pct,
        )
    )
    signal_family = (
        "abnormal_official_station_mispricing"
        if price_anomaly
        else (result.signal_family or _base_signal_family(signal))
    )
    reason = (
        f"{result.reason}; final_pre_trade=true, p_exec_vwap={checked_p_exec:.4f}, "
        f"edge={edge:.4f}, route={return_estimate.route}, "
        f"expected_net_return={return_estimate.expected_net_return_pct:.2%}, "
        f"best_bid={(book.best_bid or 0.0):.4f}, best_ask={(book.best_ask or 0.0):.4f}, "
        f"spread_audit={spread:.4f}, slip_audit={checked_slip:.4f}, "
        f"final_book_source={final_book_source}, "
        f"liquidity_routes={'+'.join(dict.fromkeys(_book_ask_routes(book))) or 'none'}, "
        f"price_anomaly={str(price_anomaly).lower()}, strategy_mode={settings.strategy_mode}, "
        f"signal_family={signal_family}{final_size_note}"
    )
    entry_ask_depth_top5_json = _entry_ask_depth_top5_json(
        book,
        entry_size_usd=final_size_usd,
        entry_vwap=checked_p_exec,
        entry_shares=checked_shares,
        fee_rate=settings.weather_taker_fee_rate,
    )
    reason = f"{reason}, entry_ask_depth_top5={entry_ask_depth_top5_json}"
    return replace(
        result,
        p_exec=checked_p_exec,
        net_edge=edge,
        size_usd=final_size_usd,
        size_shares=checked_shares,
        reason=reason,
        expected_net_profit_usd=return_estimate.expected_net_profit_usdc,
        price_anomaly=price_anomaly,
        strategy_mode=settings.strategy_mode if _is_official_station_entry_signal(signal) else "",
        signal_family=signal_family,
        executable_size_usd=final_size_usd,
        entry_ask_depth_top5_json=entry_ask_depth_top5_json,
    )


def _no_valid_side_reason(base_reason: str, per_side: dict[str, EdgeResult]) -> str:
    details = [
        result.reason
        for side in ("YES", "NO")
        if (result := per_side.get(side)) is not None and result.reason
    ]
    if not details:
        return base_reason
    return f"{base_reason} {' | '.join(details)}"


def _entry_bankroll_skip_reason(bankroll_before_entry: float, reason: str | None = None) -> str:
    detail = f"; {reason}" if reason else ""
    return f"{ENTRY_BANKROLL_FAIL_CLOSED_REASON}; entry_bankroll=${bankroll_before_entry:.2f}{detail}"


def pre_station_tradeability_gate(
    market: RawMarket,
    settings: Settings,
    market_type: str = "temperature",
) -> tuple[WeatherSignal, EdgeResult] | None:
    """Return a SKIP decision when a market should not reach station evaluation."""
    parsed = parse_weather_question(market.question)

    def skip(source: str, note: str, reason: str) -> tuple[WeatherSignal, EdgeResult]:
        signal = WeatherSignal(
            p_true=0.5,
            confidence=0.0,
            source=source,
            note=note,
            parsed=parsed,
        )
        result = EdgeResult(
            side="SKIP",
            p_true=signal.p_true,
            p_exec=None,
            net_edge=-999.0,
            size_usd=0.0,
            size_shares=0.0,
            reason=reason,
        )
        return signal, result

    tradability_reason = _market_tradability_skip_reason(
        market,
        None,
        settings,
        final_pre_trade=False,
        market_type=market_type,
    )
    if tradability_reason:
        return skip("market-tradability", tradability_reason, tradability_reason)

    if parsed.variable != "temperature" or parsed.threshold_f is None or parsed.operator is None:
        note = "Unsupported weather market skipped before station evaluation. " + parsed.note
        return skip(
            "unsupported-weather-market",
            note,
            f"unsupported-weather-market: refusing non-temperature or weakly parsed market before station evaluation [{market_type}]",
        )

    if parsed.city is None:
        return skip(
            "fallback",
            f"Could not parse city before station evaluation. {parsed.note}",
            f"city not parsed: refusing market before station evaluation [{market_type}]",
        )

    if rule_mismatch := market_rule_mismatch_reason(market):
        return skip(
            "rule-mismatch",
            f"SKIP_RULE_MISMATCH: market title and rule text disagree before station evaluation. {rule_mismatch}",
            f"SKIP_RULE_MISMATCH: {rule_mismatch} [{market_type}]",
        )

    if parsed.city.lower() not in TRADING_READY_STATION_MAP:
        return skip(
            "unsupported-station",
            f"{parsed.city} is not in the trading-ready Polymarket settlement-station allowlist with stored rule evidence.",
            f"unsupported-station: refusing market before station evaluation [{market_type}]",
        )

    if parsed.date_hint is None:
        return skip(
            "pre-station-skip",
            f"date_hint=None: station evaluation skipped before request. {parsed.note}",
            f"date_hint=None: refusing undated market before station evaluation [{market_type}]",
        )

    return None


def _drawdown_entry_gate(
    market: RawMarket,
    broker: PaperBroker,
    settings: Settings,
    now: datetime,
    market_type: str = "temperature",
) -> tuple[WeatherSignal, EdgeResult] | None:
    parsed = parse_weather_question(market.question)
    reason = drawdown_entry_block_reason(
        settings,
        broker.state.positions,
        now=now,
        city=parsed.city or "",
        date_hint=parsed.date_hint or "",
    )
    if not reason:
        return None
    signal = WeatherSignal(
        p_true=0.5,
        confidence=0.0,
        source="drawdown-circuit-breaker",
        note=reason,
        parsed=parsed,
    )
    result = EdgeResult(
        side="SKIP",
        p_true=signal.p_true,
        p_exec=None,
        net_edge=-999.0,
        size_usd=0.0,
        size_shares=0.0,
        reason=f"{reason} [{market_type}]",
    )
    return signal, result


def evaluate_market(
    market: RawMarket,
    signal: WeatherSignal,
    client: PolymarketClient,
    settings: Settings,
    bankroll_before_entry: float,
    market_type: str = "temperature",
    entry_bankroll_reason: str | None = None,
    *,
    allowed_sides: set[str] | None = None,
) -> tuple[EdgeResult, dict[str, EdgeResult]]:
    """Evaluate live YES/NO books and return the best executable paper result."""
    min_confidence, min_edge, entry_fraction_override = _market_params(settings, market_type)

    tradability_reason = _market_tradability_skip_reason(
        market,
        client,
        settings,
        final_pre_trade=False,
        market_type=market_type,
    )
    if tradability_reason:
        return EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, tradability_reason), {}

    if signal.parsed is not None and signal.parsed.date_hint is None:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, f"date_hint=None: refusing undated market [{market_type}]")
        return result, {}

    if settings.official_nowcast_entry_only and not _is_official_station_entry_signal(signal):
        result = EdgeResult(
            "SKIP",
            signal.p_true,
            None,
            -999.0,
            0.0,
            0.0,
            (
                "official-station-entry-only: non-lock entry blocked; "
                "waiting for same-station settlement-lock evidence "
                f"[{market_type}]"
            ),
        )
        return result, {}

    if settings.require_parse_for_trade and signal.confidence < min_confidence:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, f"confidence too low: {signal.confidence:.2f} < {min_confidence:.2f} [{market_type}]")
        return result, {}

    if rule_mismatch := market_rule_mismatch_reason(market):
        reason_code = (
            "SKIP_WRH_TIMESERIES_UNVERIFIED"
            if "WRH timeseries" in rule_mismatch
            else "SKIP_RULE_MISMATCH"
        )
        result = EdgeResult(
            "SKIP",
            signal.p_true,
            None,
            -999.0,
            0.0,
            0.0,
            f"{reason_code}: {rule_mismatch} [{market_type}]",
        )
        return result, {}

    if bankroll_before_entry <= 0:
        result = EdgeResult(
            "SKIP",
            signal.p_true,
            None,
            -999.0,
            0.0,
            0.0,
            _entry_bankroll_skip_reason(bankroll_before_entry, entry_bankroll_reason),
        )
        return result, {}

    books, fetch_error = _fetch_books(
        market,
        client,
        preferred_side=(
            _preferred_entry_side(signal)
            if allowed_sides is None or len(allowed_sides) != 1
            else next(iter(allowed_sides))
        ),
        allowed_sides=allowed_sides,
    )
    if fetch_error:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, fetch_error)
        return result, {}
    best_result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, "No valid side evaluated.")
    per_side: dict[str, EdgeResult] = {}
    for side, book in books.items():
        result = _side_result(
            side,
            book,
            signal,
            settings,
            bankroll_before_entry,
            min_confidence,
            min_edge,
            entry_fraction_override,
            market_type,
        )
        result = _with_exit_signal(side, signal, result)
        # Pre-entry nowcast gate: if the exit signal would fire immediately after
        # entry (e.g. nowcast bucket-lock risk), block the entry entirely rather
        # than opening and closing in the same cycle.
        if result.exit_signal and result.side != "SKIP":
            result = replace(
                result,
                side="SKIP",
                size_usd=0.0,
                size_shares=0.0,
                reason=(
                    f"pre-entry nowcast block: {result.exit_signal_reason}; "
                    f"entry blocked to prevent immediate exit [{market_type}]"
                ),
            )
        per_side[side] = result
        if result.net_edge > best_result.net_edge:
            best_result = result

    if best_result.side == "SKIP":
        prefix = "trade blocked" if best_result.net_edge > min_edge else f"edge below {min_edge:.2%}"
        reason = best_result.reason
        if best_result.net_edge <= -999.0:
            reason = _no_valid_side_reason(reason, per_side)
        best_result = replace(
            best_result,
            side="SKIP",
            p_true=signal.p_true,
            size_usd=0.0,
            size_shares=0.0,
            reason=f"{prefix} [{market_type}]. {reason}",
        )
    elif best_result.net_edge <= min_edge:
        best_result = replace(
            best_result,
            side="SKIP",
            p_true=signal.p_true,
            size_usd=0.0,
            size_shares=0.0,
            reason=f"edge below {min_edge:.2%} [{market_type}]. {best_result.reason}",
        )
    return best_result, per_side


def _event_portfolio_candidates(
    market: RawMarket,
    signal: WeatherSignal,
    result: EdgeResult,
    per_side: dict[str, EdgeResult],
    market_type: str,
    decision_ts: str = "",
) -> list[PortfolioCandidate]:
    executable = [
        PortfolioCandidate(market, signal, edge_result, market_type, decision_ts)
        for edge_result in per_side.values()
        if edge_result.side in {"YES", "NO"}
    ]
    return executable or [PortfolioCandidate(market, signal, result, market_type, decision_ts)]


def _new_entry_candidates_for_strategy(
    candidates: list[PortfolioCandidate],
    settings: Settings,
) -> list[PortfolioCandidate]:
    eligible: list[PortfolioCandidate] = []
    for candidate in candidates:
        if candidate.result.side not in {"YES", "NO"}:
            eligible.append(candidate)
            continue
        if (
            settings.strategy_mode in LOCK_EXACT_NO_STRATEGY_MODES
            and _exact_no_entry_skip_reason(
                candidate.market,
                candidate.signal,
                candidate.result.side,
                settings,
            )
        ):
            continue
        if settings.no_only_new_entries and candidate.result.side == "YES":
            continue
        eligible.append(candidate)
    return eligible


def _refresh_held_exit_edges_from_signal(
    broker: PaperBroker,
    market: RawMarket,
    signal: WeatherSignal,
    latest_edges: dict[tuple[str, str], EdgeResult],
    entry_bankroll_reason: str | None,
) -> dict[str, EdgeResult]:
    refreshed: dict[str, EdgeResult] = {}
    reason = "held exit evidence refreshed from latest signal while new entries are blocked"
    if entry_bankroll_reason:
        reason = f"{reason}; entry_bankroll_reason={entry_bankroll_reason}"
    for pos in broker.state.positions:
        if pos.market_id != market.market_id:
            continue
        edge = EdgeResult(
            pos.side,
            signal.p_true,
            None,
            -999.0,
            0.0,
            0.0,
            reason,
        )
        edge = _with_exit_signal(pos.side, signal, edge)
        if not edge.exit_signal and signal.confidence <= 0.0:
            continue
        latest_edges[(pos.market_id, pos.side)] = edge
        refreshed[pos.side] = edge
    return refreshed


def _is_temperature_market(market: RawMarket) -> bool:
    parsed = parse_weather_question(market.question)
    return parsed.variable == "temperature" and parsed.threshold_f is not None and parsed.operator is not None


def _temperature_markets_only(markets: list[RawMarket]) -> list[RawMarket]:
    return [market for market in markets if _is_temperature_market(market)]


def _market_from_position(pos: PaperPosition) -> RawMarket:
    return RawMarket(
        market_id=pos.market_id,
        question=pos.question,
        slug=pos.metadata.get("slug"),
        active=True,
        closed=False,
        yes_token_id=pos.token_id if pos.side == "YES" else None,
        no_token_id=pos.token_id if pos.side == "NO" else None,
        event_slug=pos.metadata.get("event_slug"),
    )


def _sleep_seconds_until_next_cycle(started_at: datetime, interval_seconds: int, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    elapsed = (now - started_at).total_seconds()
    return max(0.0, float(interval_seconds) - elapsed)


def refresh_open_position_edges(
    broker: PaperBroker,
    client: PolymarketClient,
    settings: Settings,
    latest_edges: dict[tuple[str, str], EdgeResult],
    market_by_id: dict[str, RawMarket],
    probability_estimator=estimate_station_probability,
    observation_provider: Any | None = None,
    residual_profile_store: ResidualProfileStore | None = None,
) -> None:
    """Refresh station signal and edge for held positions missing from the scan."""
    for pos in broker.state.positions:
        key = (pos.market_id, pos.side)
        if key in latest_edges:
            continue
        market = market_by_id.get(pos.market_id) or _market_from_position(pos)
        if not _is_temperature_market(market):
            continue
        gated = pre_station_tradeability_gate(market, settings)
        if gated is not None:
            _signal, result = gated
            latest_edges[key] = result
            market_by_id.setdefault(pos.market_id, market)
            continue
        signal = _call_probability_estimator(
            probability_estimator,
            pos.question,
            settings=settings,
            observation_provider=observation_provider,
            residual_profile_store=residual_profile_store,
            now=datetime.now(timezone.utc),
        )
        market_type = "temperature"
        _best, per_side = evaluate_market(
            market,
            signal,
            client,
            settings,
            broker.current_bankroll_before_entry(),
            market_type,
        )
        if pos.side in per_side:
            latest_edges[key] = per_side[pos.side]
        market_by_id.setdefault(pos.market_id, market)


def _hydrate_open_position_markets(client: PolymarketClient, broker: PaperBroker, market_by_id: dict[str, RawMarket]) -> None:
    for pos in broker.state.positions:
        if pos.market_id in market_by_id:
            continue
        try:
            market_by_id[pos.market_id] = client.get_market(pos.market_id)
        except Exception:
            market_by_id[pos.market_id] = _market_from_position(pos)


def _stream_market_registry(
    client: PolymarketClient,
    broker: PaperBroker,
    markets: list[RawMarket],
) -> dict[str, RawMarket]:
    market_by_id = {market.market_id: market for market in markets}
    _hydrate_open_position_markets(client, broker, market_by_id)
    return market_by_id


def _ensure_open_position_stream_tokens(
    stream_markets: list[RawMarket],
    broker: PaperBroker,
) -> list[RawMarket]:
    market_by_id = {market.market_id: market for market in stream_markets}
    order = [market.market_id for market in stream_markets]
    for pos in broker.state.positions:
        market = market_by_id.get(pos.market_id) or _market_from_position(pos)
        if pos.side == "YES" and market.yes_token_id != pos.token_id:
            market = replace(market, yes_token_id=pos.token_id)
        elif pos.side == "NO" and market.no_token_id != pos.token_id:
            market = replace(market, no_token_id=pos.token_id)
        if pos.market_id not in market_by_id:
            order.append(pos.market_id)
        market_by_id[pos.market_id] = market
    return [market_by_id[market_id] for market_id in order]


def _market_by_token_with_held_positions_first(
    stream_markets: list[RawMarket],
    broker: PaperBroker,
) -> dict[str, RawMarket]:
    market_by_id = {market.market_id: market for market in stream_markets}
    market_by_token: dict[str, RawMarket] = {}
    for pos in broker.state.positions:
        if not pos.token_id:
            continue
        market_by_token[pos.token_id] = market_by_id.get(pos.market_id) or _market_from_position(pos)
    for market in stream_markets:
        for token_id in _market_token_ids(market):
            market_by_token.setdefault(token_id, market)
    return market_by_token


def _market_is_current_local_measurement_day(market: RawMarket, now: datetime) -> bool:
    event_date, local_today = _market_event_date_for_priority(market, now)
    return event_date is not None and event_date == local_today


def _select_realtime_stream_markets(
    stream_markets: list[RawMarket],
    broker: PaperBroker,
    *,
    now: datetime,
) -> list[RawMarket]:
    open_market_ids = {pos.market_id for pos in broker.state.positions}
    selected: list[RawMarket] = []
    seen_market_ids: set[str] = set()
    for market in stream_markets:
        should_stream = (
            market.market_id in open_market_ids
            or _market_is_current_local_measurement_day(market, now)
        )
        if not should_stream:
            continue
        if market.market_id not in seen_market_ids:
            selected.append(market)
            seen_market_ids.add(market.market_id)
    return _ensure_open_position_stream_tokens(selected, broker)


def _settle_resolved_positions_before_streaming(
    broker: PaperBroker,
    market_by_id: dict[str, RawMarket],
) -> list[str]:
    open_market_ids_before = {pos.market_id for pos in broker.state.positions}
    messages = maybe_settle_resolved_positions(broker, market_by_id)
    if messages:
        open_market_ids_after = {pos.market_id for pos in broker.state.positions}
        for market_id in open_market_ids_before - open_market_ids_after:
            market_by_id.pop(market_id, None)
    return messages


def _market_event_key(market: RawMarket) -> str:
    if market.event_id:
        return market.event_id
    parsed = parse_weather_question(market.question)
    return "|".join([
        parsed.city or "unknown-city",
        parsed.date_hint or "unknown-date",
        parsed.variable,
        parsed.temperature_metric,
    ])


def _group_weather_markets_by_event(markets: list[RawMarket]) -> list[list[RawMarket]]:
    groups: dict[str, list[RawMarket]] = {}
    for market in markets:
        groups.setdefault(_market_event_key(market), []).append(market)
    return list(groups.values())


def _market_is_active_for_realtime_priority(market: RawMarket) -> bool:
    if not market.active or market.closed or market.archived is True:
        return False
    if market.accepting_orders is False or market.enable_order_book is False:
        return False
    return bool(market.yes_token_id or market.no_token_id)


def _market_event_date_for_priority(market: RawMarket, now: datetime) -> tuple[date | None, date]:
    provenance = market.rule_provenance
    timezone_name = (
        provenance.event_timezone
        if provenance is not None and provenance.event_timezone
        else "UTC"
    )
    try:
        local_today = now.astimezone(ZoneInfo(timezone_name)).date()
    except ZoneInfoNotFoundError:
        timezone_name = "UTC"
        local_today = now.astimezone(timezone.utc).date()
    if provenance is not None and provenance.event_date_local:
        try:
            return date.fromisoformat(provenance.event_date_local), local_today
        except ValueError:
            pass
    parsed = parse_weather_question(market.question)
    window = event_date_window_from_hint(
        parsed.date_hint,
        timezone_name,
        now=now,
        source_texts=(market.question, market.slug or "", market.event_slug or ""),
    )
    return (window.event_date_local if window is not None else None), local_today


def _realtime_event_priorities(
    markets: list[RawMarket],
    *,
    open_market_ids: set[str],
    now: datetime,
) -> dict[str, tuple[int, int]]:
    priorities: dict[str, tuple[int, int]] = {}
    groups: dict[str, list[RawMarket]] = {}
    for market in markets:
        groups.setdefault(_market_event_key(market), []).append(market)
    for event_key, group in groups.items():
        if any(market.market_id in open_market_ids for market in group):
            priorities[event_key] = (0, 0)
            continue
        live = [market for market in group if _market_is_active_for_realtime_priority(market)]
        if not live:
            priorities[event_key] = (9, 9999)
            continue
        date_deltas: list[int] = []
        same_day = False
        for market in live:
            event_date, local_today = _market_event_date_for_priority(market, now)
            if event_date is None:
                continue
            delta = (event_date - local_today).days
            date_deltas.append(abs(delta))
            same_day = same_day or delta == 0
        nearest_day_distance = min(date_deltas) if date_deltas else 9999
        realtime_no_candidate = any(
            _is_realtime_no_candidate(parse_weather_question(market.question))
            for market in live
        )
        if same_day and realtime_no_candidate:
            tier = 1
        elif same_day:
            tier = 2
        elif realtime_no_candidate:
            tier = 3
        else:
            tier = 4
        priorities[event_key] = (tier, nearest_day_distance)
    return priorities


def _discovery_coverage(markets: list[RawMarket]) -> dict[str, int]:
    groups = _group_weather_markets_by_event(markets)
    cities = {
        parsed.city
        for market in markets
        if (parsed := parse_weather_question(market.question)).city
    }
    return {"events": len(groups), "cities": len(cities), "markets": len(markets)}


def _pre_station_skip_reason(signal: WeatherSignal, result: EdgeResult) -> str:
    return (result.reason or signal.note or "unknown_pre_station_skip").strip()


def run_cycle(settings: Settings | None = None) -> list[MarketDecision]:
    settings = settings or load_settings()
    cycle_started_at = utc_now_iso()
    market_error_count = 0
    last_market_error: dict[str, Any] | None = None
    write_runner_status(
        settings,
        "starting",
        message="starting cycle",
        cycle_started_at=cycle_started_at,
        **_market_error_status_fields(market_error_count, last_market_error),
    )
    client = PolymarketClient(settings.gamma_base, settings.clob_base)
    observation_provider = AviationWeatherMetarNowcastProvider.from_settings(settings)
    residual_profile_store = _load_residual_profile_store(settings)
    broker = PaperBroker(settings)
    try:
        write_runner_status(
            settings,
            "discovering",
            message="discovering markets",
            cycle_started_at=cycle_started_at,
            **_market_error_status_fields(market_error_count, last_market_error),
        )
        discovered_markets = client.discover_weather_markets(
            max_pages=settings.discovery_max_pages,
            page_size=settings.discovery_page_size,
        )
    except Exception as exc:  # noqa: BLE001
        write_runner_status(
            settings,
            "error",
            message=f"market discovery failed: {exc}",
            cycle_started_at=cycle_started_at,
            **_market_error_status_fields(market_error_count, last_market_error),
        )
        print(f"DATA ERROR: could not fetch live Polymarket markets: {exc}")
        print("Check internet/DNS/VPN, then run live-paper-bot again.")
        return []

    discovered_markets = _temperature_markets_only(discovered_markets)
    event_groups = _group_weather_markets_by_event(discovered_markets)
    markets = [market for group in event_groups for market in group]
    coverage = _discovery_coverage(markets)
    market_by_id = {m.market_id: m for m in markets}
    decisions: list[MarketDecision] = []
    latest_edges: dict[tuple[str, str], EdgeResult] = {}

    print(
        f"\nLIVE PAPER CYCLE | markets={len(markets)} | events={coverage['events']} | "
        f"cities={coverage['cities']} | "
        f"cash=${broker.state.cash_usd:.2f} | exposure=${broker.total_exposure():.2f} | "
        f"bankroll=${broker.current_bankroll_before_entry():.2f} | open_positions={len(broker.state.positions)}"
    )
    write_runner_status(
        settings,
        "evaluating",
        message=f"evaluating 0/{len(markets)} markets across {coverage['events']} events, {coverage['cities']} cities",
        cycle_started_at=cycle_started_at,
        markets_done=0,
        markets_total=len(markets),
        events_total=coverage["events"],
        cities_total=coverage["cities"],
        cash_usd=round(broker.state.cash_usd, 2),
        exposure_usd=round(broker.total_exposure(), 2),
        open_positions=len(broker.state.positions),
        **_market_error_status_fields(market_error_count, last_market_error),
    )
    markets_done = 0
    for event_markets in event_groups:
        entry_bankroll = available_entry_bankroll(broker, client)
        candidates: list[PortfolioCandidate] = []
        for market in event_markets:
            markets_done += 1
            try:
                market_type = "temperature"
                gated = pre_station_tradeability_gate(market, settings, market_type)
                if gated is not None:
                    signal, result = gated
                    per_side: dict[str, EdgeResult] = {}
                else:
                    drawdown_gate = _drawdown_entry_gate(market, broker, settings, datetime.now(timezone.utc), market_type)
                    if drawdown_gate is not None:
                        signal, result = drawdown_gate
                        per_side = {}
                    else:
                        signal = _call_probability_estimator(
                            estimate_station_probability,
                            market.question,
                            settings=settings,
                            observation_provider=observation_provider,
                            residual_profile_store=residual_profile_store,
                            now=datetime.now(timezone.utc),
                        )
                        result, per_side = evaluate_market(
                            market,
                            signal,
                            client,
                            settings,
                            entry_bankroll.entry_bankroll,
                            market_type,
                            entry_bankroll.reason,
                        )
                for side, edge_result in per_side.items():
                    latest_edges[(market.market_id, side)] = edge_result
                decisions.append(MarketDecision(market=market, signal=signal, result=result))
                decision_ts = broker.log_decision(market, result, signal.note, market_type, signal=signal)
                candidates.extend(_event_portfolio_candidates(market, signal, result, per_side, market_type, decision_ts))
                broker.log_raw_snapshot(
                    "decision",
                    market,
                    {
                        "market_raw": market.raw,
                        "signal": {
                            "p_true": signal.p_true,
                            "confidence": signal.confidence,
                            "source": signal.source,
                            "note": signal.note,
                            "nowcast": signal.nowcast,
                        },
                        "per_side": {side: edge.__dict__ for side, edge in per_side.items()},
                    },
                )
                print("-" * 100)
                print(f"Q: {market.question}")
                print(f"P_true={signal.p_true:.3f} confidence={signal.confidence:.2f} source={signal.source}")
                print(f"Decision={result.side} edge={result.net_edge:.3f} p_exec={result.p_exec} size=${result.size_usd:.2f}")
                print(f"Reason: {result.reason}")
                print(f"Note: {signal.note}")
            except Exception as exc:  # noqa: BLE001
                signal, result, last_market_error, market_error_count = _record_market_evaluation_error(
                    broker,
                    market,
                    exc,
                    "temperature",
                    context="cycle",
                )
                decisions.append(MarketDecision(market=market, signal=signal, result=result))
                candidates.extend(_event_portfolio_candidates(market, signal, result, {}, "temperature"))
                print("-" * 100)
                print(f"Q: {market.question}")
                print(f"ERROR: {exc}")
            write_runner_status(
                settings,
                "evaluating",
                message=f"evaluating {markets_done}/{len(markets)}",
                cycle_started_at=cycle_started_at,
                markets_done=markets_done,
                markets_total=len(markets),
                events_total=coverage["events"],
                cities_total=coverage["cities"],
                last_market=market.question,
                cash_usd=round(broker.state.cash_usd, 2),
                exposure_usd=round(broker.total_exposure(), 2),
                open_positions=len(broker.state.positions),
                **_market_error_status_fields(market_error_count, last_market_error),
            )
        portfolio = _apply_event_portfolio(
            broker,
            candidates,
            entry_bankroll,
            client=client,
            observation_provider=observation_provider,
            residual_profile_store=residual_profile_store,
        )
        print(
            f"EVENT PORTFOLIO {portfolio.event_key}: selected={len(portfolio.selected)} "
            f"exposure=${portfolio.selected_exposure_usd:.2f} cap=${portfolio.event_cap_usd:.2f} "
            f"expected_net_profit=${portfolio.expected_net_profit_usd:.2f}"
        )

    write_runner_status(
        settings,
        "closing",
        message="checking settlements and exits",
        cycle_started_at=cycle_started_at,
        markets_done=len(markets),
        markets_total=len(markets),
        events_total=coverage["events"],
        cities_total=coverage["cities"],
        **_market_error_status_fields(market_error_count, last_market_error),
    )
    _hydrate_open_position_markets(client, broker, market_by_id)
    settlement_msgs = maybe_settle_resolved_positions(broker, market_by_id)
    for msg in settlement_msgs:
        print(msg)

    refresh_open_position_edges(
        broker,
        client,
        settings,
        latest_edges,
        market_by_id,
        observation_provider=observation_provider,
        residual_profile_store=residual_profile_store,
    )
    close_msgs = maybe_close_positions(broker, client, market_by_id, latest_edges)
    for msg in close_msgs:
        print(msg)
    print(
        f"SUMMARY cash=${broker.state.cash_usd:.2f} realized_pnl=${broker.state.realized_pnl_usd:.2f} "
        f"exposure=${broker.total_exposure():.2f} bankroll=${broker.current_bankroll_before_entry():.2f}"
    )
    print(broker.stats_summary())
    write_runner_status(
        settings,
        "cycle_complete",
        message=f"cycle complete {len(decisions)}/{len(markets)}",
        cycle_started_at=cycle_started_at,
        markets_done=len(markets),
        markets_total=len(markets),
        events_total=coverage["events"],
        cities_total=coverage["cities"],
        cash_usd=round(broker.state.cash_usd, 2),
        exposure_usd=round(broker.total_exposure(), 2),
        open_positions=len(broker.state.positions),
        **_market_error_status_fields(market_error_count, last_market_error),
    )
    return decisions


def _market_token_ids(market: RawMarket) -> list[str]:
    return [token_id for token_id in (market.yes_token_id, market.no_token_id) if token_id]


def _record_pre_trade_skip(
    broker: PaperBroker,
    market: RawMarket,
    original_result: EdgeResult,
    skip_result: EdgeResult,
    token_id: str,
    market_type: str,
    *,
    signal: WeatherSignal | None = None,
) -> EdgeResult:
    action = skip_result.reason.split(":", 1)[0]
    if not action.startswith("SKIP_"):
        action = "SKIP_PRE_TRADE"
    broker.log_trade(
        action,
        market,
        original_result.side,
        token_id,
        0.0,
        skip_result.p_exec if skip_result.p_exec is not None else (original_result.p_exec or 0.0),
        0.0,
        skip_result.reason,
        market_type,
    )
    if signal is not None:
        broker.log_decision(
            market,
            skip_result,
            skip_result.reason,
            market_type,
            signal=signal,
        )
    return skip_result


def _open_position_if_needed(
    broker: PaperBroker,
    market: RawMarket,
    signal: WeatherSignal,
    result: EdgeResult,
    market_type: str,
    entry_bankroll_usd: float | None = None,
    decision_ts: str = "",
    add_to_existing_position_id: str | None = None,
    client: PolymarketClient | None = None,
    probability_estimator: Any | None = None,
    observation_provider: Any | None = None,
    residual_profile_store: ResidualProfileStore | None = None,
) -> EdgeResult | None:
    if result.side not in {"YES", "NO"}:
        return result
    token_id = market.yes_token_id if result.side == "YES" else market.no_token_id
    if broker.settings.strategy_mode in LOCK_EXACT_NO_STRATEGY_MODES:
        strategy_reason = _exact_no_entry_skip_reason(
            market,
            signal,
            result.side,
            broker.settings,
        )
        if strategy_reason is not None:
            blocked = _skip_entry_result(result, strategy_reason)
            return _record_pre_trade_skip(
                broker,
                market,
                result,
                blocked,
                token_id or "",
                market_type,
                signal=signal,
            )
    if result.side == "YES" and broker.settings.no_only_new_entries:
        blocked = _skip_entry_result(
            result,
            NO_ONLY_NEW_ENTRY_REASON,
        )
        return _record_pre_trade_skip(
            broker,
            market,
            result,
            blocked,
            token_id or "",
            market_type,
            signal=signal,
        )
    initial_reason = _market_tradability_skip_reason(
        market,
        client,
        broker.settings,
        final_pre_trade=False,
        market_type=market_type,
    )
    if initial_reason:
        final_result = _skip_entry_result(result, initial_reason)
    elif not token_id:
        final_result = _skip_entry_result(
            result,
            f"SKIP_TRADABILITY_UNKNOWN: selected {result.side} token ID is missing; "
            f"final_pre_trade=false [{market_type}]",
        )
    else:
        final_result = result

    if final_result.side == "SKIP":
        return _record_pre_trade_skip(
            broker,
            market,
            result,
            final_result,
            token_id or "",
            market_type,
            signal=signal,
        )

    allow_same_side_add = (
        add_to_existing_position_id is not None
        and broker.has_position(market.market_id, result.side)
    )
    if broker.has_any_position(market.market_id) and not allow_same_side_add:
        return None
    final_signal = signal
    revalidated_result = result
    if _is_official_station_entry_signal(signal) and (
        probability_estimator is not None
        or observation_provider is not None
        or residual_profile_store is not None
    ):
        current = datetime.now(timezone.utc)
        try:
            final_signal = _call_probability_estimator(
                probability_estimator or estimate_station_probability,
                market.question,
                settings=broker.settings,
                observation_provider=observation_provider,
                residual_profile_store=residual_profile_store,
                now=current,
            )
            final_best, final_per_side = evaluate_market(
                market,
                final_signal,
                client,
                broker.settings,
                entry_bankroll_usd
                if entry_bankroll_usd is not None
                else broker.current_bankroll_before_entry(),
                market_type,
            )
        except Exception as exc:  # noqa: BLE001
            final_best = EdgeResult(
                "SKIP",
                signal.p_true,
                None,
                -999.0,
                0.0,
                0.0,
                f"SKIP_FINAL_STATION_SIGNAL: final station signal revalidation failed: {exc}",
            )
            final_per_side = {}
        final_side = final_per_side.get(result.side)
        if final_side is None or final_side.side != result.side:
            final_result = _skip_entry_result(
                result,
                (
                    "SKIP_FINAL_STATION_SIGNAL: final station signal revalidation "
                    f"blocked {result.side}: {final_best.reason}"
                ),
            )
            return _record_pre_trade_skip(
                broker,
                market,
                result,
                final_result,
                token_id,
                market_type,
                # Keep the exact-NO evidence that reached this final gate.  A
                # neutral/unavailable recheck explains the rejection but must
                # not erase the candidate from the audit ledger.
                signal=signal,
            )
        selected_size_usd = min(result.size_usd, final_side.size_usd)
        selected_size_scale = (
            selected_size_usd / final_side.size_usd
            if final_side.size_usd > 0
            else 0.0
        )
        revalidated_result = replace(
            final_side,
            size_usd=selected_size_usd,
            size_shares=final_side.size_shares * selected_size_scale,
            executable_size_usd=selected_size_usd,
            expected_net_profit_usd=final_side.expected_net_profit_usd * selected_size_scale,
            reason=f"{result.reason}; final_station_revalidation=refetched; {final_side.reason}",
        )
    final_result = _final_pre_trade_entry_result(
        market,
        final_signal,
        revalidated_result,
        token_id,
        client,
        broker.settings,
        market_type,
    )
    if final_result.side == "SKIP":
        return _record_pre_trade_skip(
            broker,
            market,
            result,
            final_result,
            token_id,
            market_type,
            signal=final_signal,
        )
    city = final_signal.parsed.city if final_signal.parsed is not None else ""
    date_hint = final_signal.parsed.date_hint if final_signal.parsed is not None else ""
    opened_position = broker.open_position(
        market,
        token_id,
        final_result,
        market_type,
        city=city or "",
        date_hint=date_hint or "",
        entry_bankroll_usd=entry_bankroll_usd,
        decision_ts=decision_ts,
        allow_same_side_add=allow_same_side_add,
        signal=final_signal,
    )
    if opened_position is None:
        ledger_rejection = getattr(broker, "last_open_rejection_result", None)
        if isinstance(ledger_rejection, EdgeResult):
            return ledger_rejection
        return _skip_entry_result(
            final_result,
            "SKIP_PAPER_LEDGER_REJECTED: final paper ledger did not open the selected entry",
        )
    return final_result


def _apply_event_portfolio(
    broker: PaperBroker,
    candidates: list[PortfolioCandidate],
    entry_bankroll: EntryBankrollSnapshot,
    client: PolymarketClient | None = None,
    probability_estimator: Any | None = None,
    observation_provider: Any | None = None,
    residual_profile_store: ResidualProfileStore | None = None,
) -> EventPortfolioDecision:
    for candidate in candidates:
        if candidate.result.side not in {"YES", "NO"}:
            continue
        strategy_reason = None
        if broker.settings.strategy_mode in LOCK_EXACT_NO_STRATEGY_MODES:
            strategy_reason = _exact_no_entry_skip_reason(
                candidate.market,
                candidate.signal,
                candidate.result.side,
                broker.settings,
            )
        if strategy_reason is None and broker.settings.no_only_new_entries and candidate.result.side == "YES":
            strategy_reason = NO_ONLY_NEW_ENTRY_REASON
        if strategy_reason is None:
            continue
        blocked = _skip_entry_result(candidate.result, strategy_reason)
        broker.log_decision(
            candidate.market,
            blocked,
            blocked.reason,
            candidate.market_type,
            signal=candidate.signal,
        )
    candidates = _new_entry_candidates_for_strategy(candidates, broker.settings)
    decision = select_event_portfolio(broker, candidates, entry_bankroll)
    executable_candidate_reached_portfolio = any(
        candidate.result.side in {"YES", "NO"}
        and candidate.result.p_exec is not None
        and candidate.result.size_usd > 0
        for candidate in candidates
    )
    broker.log_event_portfolio_decision(
        decision.to_log_payload(),
        has_selected=bool(decision.selected) or executable_candidate_reached_portfolio,
    )
    refresh_error = ""
    official_station_candidates = [
        candidate
        for candidate in decision.selected
        if _is_official_station_entry_signal(candidate.signal)
    ]
    refresh_started_at = datetime.now(timezone.utc)
    candidates_requiring_forced_refresh = [
        candidate
        for candidate in official_station_candidates
        if not _has_recent_direct_observation(candidate.signal, now=refresh_started_at)
    ]
    if observation_provider is not None and candidates_requiring_forced_refresh:
        discard = getattr(observation_provider, "discard_cached_observations_before_entry", None)
        if not callable(discard):
            refresh_error = "SKIP_FINAL_STATION_REFRESH: observation provider cannot force a fresh official request"
        else:
            try:
                selected_station_ids = {
                    station_id
                    for candidate in candidates_requiring_forced_refresh
                    if (
                        station_id := _market_station_id(
                            candidate.market,
                            candidate.signal.parsed
                            or parse_weather_question(candidate.market.question),
                        )
                    )
                }
                if not selected_station_ids:
                    raise ValueError("selected official station could not be mapped")
                discard(station_ids=selected_station_ids)
            except Exception as exc:  # noqa: BLE001
                refresh_error = (
                    "SKIP_FINAL_STATION_REFRESH: fresh official observation request could not start: "
                    f"{type(exc).__name__}: {exc}"
                )
    for candidate in decision.selected:
        if refresh_error and _is_official_station_entry_signal(candidate.signal):
            token_id = (
                candidate.market.yes_token_id
                if candidate.result.side == "YES"
                else candidate.market.no_token_id
            ) or ""
            blocked = _skip_entry_result(candidate.result, refresh_error)
            _record_pre_trade_skip(
                broker,
                candidate.market,
                candidate.result,
                blocked,
                token_id,
                candidate.market_type,
                signal=candidate.signal,
            )
            continue
        _open_position_if_needed(
            broker,
            candidate.market,
            candidate.signal,
            candidate.result,
            candidate.market_type,
            entry_bankroll_usd=entry_bankroll.entry_bankroll,
            decision_ts=candidate.decision_ts,
            add_to_existing_position_id=candidate.add_to_existing_position_id,
            client=client,
            probability_estimator=probability_estimator,
            observation_provider=observation_provider,
            residual_profile_store=residual_profile_store,
        )
    return decision


def _realtime_signal_is_stale(
    market: RawMarket,
    settings: Settings,
    signal_refreshed_at_by_market: dict[str, datetime] | None,
    *,
    now: datetime,
) -> bool:
    if signal_refreshed_at_by_market is None:
        return False
    last_refreshed_at = signal_refreshed_at_by_market.get(market.market_id)
    if last_refreshed_at is None:
        return True
    ttl_seconds = settings.station_nowcast_cache_ttl_seconds
    return (now - last_refreshed_at.astimezone(timezone.utc)).total_seconds() >= ttl_seconds


def _record_station_signal_pending(
    broker: PaperBroker,
    market: RawMarket,
    market_type: str,
    reason: str,
) -> None:
    try:
        parsed = parse_weather_question(market.question)
    except Exception:  # noqa: BLE001
        parsed = None
    signal = WeatherSignal(
        p_true=0.5,
        confidence=0.0,
        source="official-station-pending",
        note=reason,
        parsed=parsed,
    )
    result = EdgeResult(
        "SKIP",
        signal.p_true,
        None,
        -999.0,
        0.0,
        0.0,
        reason,
    )
    broker.log_decision(market, result, signal.note, market_type, signal=signal)
    broker.log_raw_snapshot(
        "station_signal_pending",
        market,
        {
            "status": "pending",
            "reason": reason,
            "signal": {
                "p_true": signal.p_true,
                "confidence": signal.confidence,
                "source": signal.source,
                "note": signal.note,
            },
        },
    )


def _record_realtime_prefilter_skip(
    broker: PaperBroker,
    market: RawMarket,
    market_type: str,
    reason: str,
    *,
    signal: WeatherSignal | None = None,
    prefilter_skip_state_by_market: dict[str, str] | None = None,
    orderbook_audit: dict[str, Any] | None = None,
) -> None:
    reason_code = reason.split(":", 1)[0]
    if signal is None:
        try:
            parsed = parse_weather_question(market.question)
        except Exception:  # noqa: BLE001
            parsed = None
        signal = WeatherSignal(
            p_true=0.5,
            confidence=0.0,
            source="realtime-prefilter",
            note=reason,
            parsed=parsed,
        )
    state_key = reason_code
    if _is_exact_no_lock("NO", signal):
        nowcast = signal.nowcast if isinstance(signal.nowcast, dict) else {}
        evidence_at = str(
            nowcast.get("observed_at")
            or nowcast.get("high_observed_at")
            or nowcast.get("low_observed_at")
            or nowcast.get("bot_received_at")
            or "unknown"
        )
        state_key = f"{reason_code}|{evidence_at}"
    if prefilter_skip_state_by_market is not None:
        if prefilter_skip_state_by_market.get(market.market_id) == state_key:
            return
        prefilter_skip_state_by_market[market.market_id] = state_key
    result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, reason)
    broker.log_decision(
        market,
        result,
        signal.note,
        market_type,
        signal=signal,
        token_id_override=(
            market.no_token_id if _is_exact_no_lock("NO", signal) else None
        ),
        orderbook_audit=orderbook_audit,
    )


def _record_fast_shadow_order_book_probes(
    broker: PaperBroker,
    markets: list[RawMarket],
    signals_by_market: dict[str, WeatherSignal],
    candidate_book: Callable[[str], OrderBook] | None,
    fast_shadow_book_state_by_market: dict[str, str] | None,
) -> None:
    if candidate_book is None or fast_shadow_book_state_by_market is None:
        return
    for market in markets:
        signal = signals_by_market.get(market.market_id)
        nowcast = signal.nowcast if signal is not None and isinstance(signal.nowcast, dict) else {}
        state_key = str(nowcast.get("fast_shadow_state_key") or "")
        if (
            not state_key
            or str(nowcast.get("fast_shadow_match_status") or "") != "pending"
            or not market.no_token_id
            or fast_shadow_book_state_by_market.get(market.market_id) == state_key
        ):
            continue
        try:
            book = candidate_book(str(market.no_token_id))
        except Exception:  # noqa: BLE001
            continue
        fast_shadow_book_state_by_market[market.market_id] = state_key
        broker.log_raw_snapshot(
            "wunderground_fast_shadow_book",
            market,
            {
                "trade_evidence": False,
                "fast_shadow_state_key": state_key,
                "fast_shadow_match_status": "pending",
                "fast_shadow_first_seen_at": nowcast.get("fast_shadow_first_seen_at"),
                "fast_shadow_observed_at": nowcast.get("fast_shadow_observed_at"),
                "fast_shadow_temp_c": nowcast.get("fast_shadow_temp_c"),
                "no_order_book": {
                    "token_id": book.token_id,
                    "timestamp": book.timestamp,
                    "best_bid": book.best_bid,
                    "best_ask": book.best_ask,
                    "bids_top5": [
                        {"price": level.price, "size": level.size}
                        for level in book.bids[:5]
                    ],
                    "asks_top5": [
                        {"price": level.price, "size": level.size}
                        for level in book.asks[:5]
                    ],
                },
            },
        )


def _evaluate_realtime_update(
    updated_token_ids: set[str],
    client: StreamBackedPolymarketClient,
    broker: PaperBroker,
    settings: Settings,
    market_by_token: dict[str, RawMarket],
    signals_by_market: dict[str, WeatherSignal],
    market_types: dict[str, str],
    latest_edges: dict[tuple[str, str], EdgeResult],
    *,
    signal_refreshed_at_by_market: dict[str, datetime] | None = None,
    probability_estimator: Any = estimate_station_probability,
    observation_provider: Any | None = None,
    residual_profile_store: ResidualProfileStore | None = None,
    now: datetime | None = None,
    wake_when_book_returns: set[str] | None = None,
    candidate_book_retry_by_token: dict[str, tuple[float, str, bool]] | None = None,
    prefilter_skip_state_by_market: dict[str, str] | None = None,
    fast_shadow_book_state_by_market: dict[str, str] | None = None,
) -> dict[str, object]:
    evaluation_started_at = time.monotonic()
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    websocket_health: dict[str, object] = {}
    stream = getattr(client, "stream", None)
    if stream is not None and hasattr(stream, "health_snapshot"):
        websocket_health = stream.health_snapshot()
    touched_events = {
        _market_event_key(market_by_token[token_id])
        for token_id in updated_token_ids
        if token_id in market_by_token
    }
    updated_market_ids_by_event: dict[str, set[str]] = {}
    for token_id in updated_token_ids:
        market = market_by_token.get(token_id)
        if market is None:
            continue
        updated_market_ids_by_event.setdefault(_market_event_key(market), set()).add(market.market_id)
    market_by_id = {market.market_id: market for market in market_by_token.values()}
    event_groups: dict[str, list[RawMarket]] = {}
    for market in market_by_id.values():
        event_groups.setdefault(_market_event_key(market), []).append(market)
    held_sides_by_market: dict[str, set[str]] = {}
    for position in broker.state.positions:
        held_sides_by_market.setdefault(position.market_id, set()).add(position.side)
    held_market_ids = set(held_sides_by_market)
    markets_to_prefetch = [
        market
        for event_key in sorted(touched_events)
        for market in event_groups[event_key]
        if (
            market.market_id in updated_market_ids_by_event.get(event_key, set())
            or market.market_id in held_market_ids
        )
    ]
    allowed_sides_by_market: dict[str, set[str]] = {}
    for market in markets_to_prefetch:
        allowed_sides = {"NO"} if settings.no_only_new_entries else {"YES", "NO"}
        allowed_sides.update(held_sides_by_market.get(market.market_id, set()))
        allowed_sides_by_market[market.market_id] = allowed_sides

    candidate_prefetch_started_at = time.monotonic()
    candidate_prefetch_status = {
        "requested": 0,
        "book_ready": 0,
        "failed": 0,
        "deferred": 0,
    }
    prefetch = getattr(client, "prefetch_final_entry_checks", None)
    prefetch_candidate_books = getattr(client, "prefetch_candidate_order_books", None)
    candidate_book = getattr(client, "get_candidate_order_book", None)
    candidate_book_audit = getattr(client, "get_candidate_order_book_audit", None)

    def candidate_source_books(market: RawMarket) -> dict[str, OrderBook]:
        source_books: dict[str, OrderBook] = {}
        if not callable(candidate_book):
            return source_books
        for side in ("YES", "NO"):
            token_id = _market_token_for_side(market, side)
            if not token_id:
                continue
            try:
                source_books[side] = candidate_book(token_id)
            except Exception:  # noqa: BLE001
                continue
        return source_books

    if callable(prefetch_candidate_books) and callable(candidate_book):
        candidate_token_ids: list[str] = []
        for market in markets_to_prefetch:
            source_books = candidate_source_books(market)
            effective_books = [
                _combined_side_order_book(market, side, source_books)
                for side in allowed_sides_by_market[market.market_id]
            ]
            effective_depth_missing = not any(
                book is not None
                and book.best_ask is not None
                and not _book_is_crossed(book)
                for book in effective_books
            )
            for side in ("YES", "NO"):
                token_id = _market_token_for_side(market, side)
                if not token_id:
                    continue
                book = source_books.get(side)
                if book is None or _book_is_crossed(book) or effective_depth_missing:
                    candidate_token_ids.append(token_id)
        candidate_prefetch_status = prefetch_candidate_books(
            list(dict.fromkeys(candidate_token_ids))
        )
    candidate_prefetch_duration_seconds = time.monotonic() - candidate_prefetch_started_at

    ready_market_ids: set[str] = set()
    book_unavailable_market_ids: set[str] = set()
    missing_book_tokens: set[str] = set()
    book_statuses_by_market: dict[str, dict[str, str]] = {}
    for market in markets_to_prefetch:
        if market.market_id in held_market_ids or not callable(candidate_book):
            ready_market_ids.add(market.market_id)
            continue
        source_books = candidate_source_books(market)
        token_statuses: dict[str, str] = {}
        for side in ("YES", "NO"):
            token_id = _market_token_for_side(market, side)
            if not token_id:
                continue
            source_book = source_books.get(side)
            prefetched_audit = (
                candidate_book_audit(str(token_id))
                if callable(candidate_book_audit)
                else {}
            )
            if source_book is None:
                token_statuses[str(token_id)] = str(
                    prefetched_audit.get("status") or "missing_response"
                )
            elif _book_is_crossed(source_book):
                token_statuses[str(token_id)] = "crossed"
            else:
                token_statuses[str(token_id)] = "ready"
            if token_id and (source_book is None or _book_is_crossed(source_book)):
                missing_book_tokens.add(str(token_id))
        executable_ask_found = any(
            book is not None
            and book.best_ask is not None
            and not _book_is_crossed(book)
            for side in allowed_sides_by_market[market.market_id]
            if (book := _combined_side_order_book(market, side, source_books)) is not None
        )
        if executable_ask_found:
            ready_market_ids.add(market.market_id)
        else:
            book_unavailable_market_ids.add(market.market_id)
            for token_id, status in list(token_statuses.items()):
                if status == "ready":
                    token_statuses[token_id] = "empty_ask"
            missing_book_tokens.update(
                str(token_id)
                for token_id in (market.yes_token_id, market.no_token_id)
                if token_id
            )
        book_statuses_by_market[market.market_id] = token_statuses

    book_unavailable_markets = [
        market
        for market in markets_to_prefetch
        if market.market_id in book_unavailable_market_ids
    ]
    book_unavailable_signal_errors: dict[str, Exception] = {}
    if settings.strategy_mode in LOCK_EXACT_NO_STRATEGY_MODES:
        # An urgent weather change can prove an exact-NO even while CLOB depth
        # is temporarily unavailable.  Compute that evidence before recording
        # the book rejection so a zero-fill day still has an auditable candidate.
        book_unavailable_signal_errors = _prefetch_realtime_signals(
            book_unavailable_markets,
            settings,
            signals_by_market,
            signal_refreshed_at_by_market,
            probability_estimator=probability_estimator,
            observation_provider=observation_provider,
            residual_profile_store=residual_profile_store,
            now=current,
        )

    for market in book_unavailable_markets:
        signal = None
        if market.market_id not in book_unavailable_signal_errors:
            candidate_signal = signals_by_market.get(market.market_id)
            if candidate_signal is not None and _is_exact_no_lock("NO", candidate_signal):
                signal = candidate_signal
        source_books = candidate_source_books(market)
        statuses = book_statuses_by_market.get(market.market_id, {})
        direct_book = source_books.get("NO")
        complement_book = source_books.get("YES")
        source_audits = [
            candidate_book_audit(str(token_id))
            for token_id in (market.no_token_id, market.yes_token_id)
            if token_id and callable(candidate_book_audit)
        ]
        requested_times = [
            str(audit.get("requested_at"))
            for audit in source_audits
            if audit.get("requested_at")
        ]
        received_times = [
            str(audit.get("received_at"))
            for audit in source_audits
            if audit.get("received_at")
        ]
        status_values = set(statuses.values())
        if status_values & {"timeout", "deadline"}:
            reason_code = "SKIP_BOOK_TIMEOUT"
        elif "http_error" in status_values:
            reason_code = "SKIP_BOOK_HTTP_ERROR"
        elif status_values & {"request_error", "missing_response", "apply_error", "not_observed"}:
            reason_code = "SKIP_BOOK_MISSING_RESPONSE"
        elif "crossed" in status_values:
            reason_code = "SKIP_CROSSED_BOOK"
        else:
            reason_code = "SKIP_NO_EXECUTABLE_ASK"
        orderbook_audit = {
            "book_request_started_at": min(requested_times) if requested_times else "",
            "book_received_at": max(received_times) if received_times else "",
            "book_checked_at": utc_now_iso(),
            "book_status_detail": ";".join(
                f"{token_id}={status}" for token_id, status in sorted(statuses.items())
            ),
            "book_route": "none",
            "direct_best_bid": direct_book.best_bid if direct_book is not None else None,
            "direct_best_ask": direct_book.best_ask if direct_book is not None else None,
            "complement_best_bid": complement_book.best_bid if complement_book is not None else None,
            "complement_best_ask": complement_book.best_ask if complement_book is not None else None,
        }
        _record_realtime_prefilter_skip(
            broker,
            market,
            market_types.get(market.market_id, "temperature"),
            f"{reason_code}: realtime NO entry has no executable direct or complementary ask",
            signal=signal,
            prefilter_skip_state_by_market=prefilter_skip_state_by_market,
            orderbook_audit=orderbook_audit,
        )
        if candidate_book_retry_by_token is not None:
            retry_now = time.monotonic()
            urgent = signal is not None and _is_exact_no_lock("NO", signal)
            for token_id, status in statuses.items():
                delay = 5.0 if status == "empty_ask" else 2.0
                existing = candidate_book_retry_by_token.get(token_id)
                due_at = retry_now + delay
                if existing is not None:
                    due_at = min(due_at, existing[0])
                    urgent = urgent or existing[2]
                candidate_book_retry_by_token[token_id] = (due_at, status, urgent)

    touched_candidate_tokens = {
        str(token_id)
        for market in markets_to_prefetch
        for token_id in (market.yes_token_id, market.no_token_id)
        if token_id
    }
    if wake_when_book_returns is not None:
        wake_when_book_returns.difference_update(touched_candidate_tokens)
        wake_when_book_returns.update(missing_book_tokens)
    if candidate_book_retry_by_token is not None:
        for market in markets_to_prefetch:
            if market.market_id not in ready_market_ids:
                continue
            for token_id in (market.yes_token_id, market.no_token_id):
                if token_id:
                    candidate_book_retry_by_token.pop(str(token_id), None)
    markets_ready_for_evaluation = [
        market for market in markets_to_prefetch if market.market_id in ready_market_ids
    ]

    signal_prefetch_started_at = time.monotonic()
    signal_prefetch_errors = _prefetch_realtime_signals(
        markets_ready_for_evaluation,
        settings,
        signals_by_market,
        signal_refreshed_at_by_market,
        probability_estimator=probability_estimator,
        observation_provider=observation_provider,
        residual_profile_store=residual_profile_store,
        now=current,
    )
    signal_prefetch_duration_seconds = time.monotonic() - signal_prefetch_started_at
    _record_fast_shadow_order_book_probes(
        broker,
        markets_ready_for_evaluation,
        signals_by_market,
        candidate_book if callable(candidate_book) else None,
        fast_shadow_book_state_by_market,
    )
    signal_ineligible_reasons: dict[str, str] = {}
    for market in markets_ready_for_evaluation:
        if (
            not callable(candidate_book)
            or market.market_id in held_market_ids
            or market.market_id in signal_prefetch_errors
            or market.market_id not in signals_by_market
        ):
            continue
        signal = signals_by_market[market.market_id]
        if _realtime_signal_allows_new_entry(signal, settings, market):
            continue
        reason = "SKIP_SIGNAL_INELIGIBLE: realtime signal is not eligible for a new NO entry"
        if settings.strategy_mode in LOCK_EXACT_NO_STRATEGY_MODES:
            reason = _exact_no_entry_skip_reason(
                market,
                signal,
                _preferred_entry_side(signal),
                settings,
            ) or reason
        signal_ineligible_reasons[market.market_id] = reason
    signal_ineligible_market_ids = set(signal_ineligible_reasons)
    for market in markets_ready_for_evaluation:
        if market.market_id in signal_ineligible_market_ids:
            _record_realtime_prefilter_skip(
                broker,
                market,
                market_types.get(market.market_id, "temperature"),
                signal_ineligible_reasons[market.market_id],
                signal=signals_by_market[market.market_id],
                prefilter_skip_state_by_market=prefilter_skip_state_by_market,
            )
    if prefilter_skip_state_by_market is not None:
        for market in markets_ready_for_evaluation:
            if market.market_id not in signal_ineligible_market_ids:
                prefilter_skip_state_by_market.pop(market.market_id, None)
    evaluation_market_ids = ready_market_ids - signal_ineligible_market_ids
    markets_selected_for_evaluation = [
        market for market in markets_ready_for_evaluation if market.market_id in evaluation_market_ids
    ]
    market_evaluation_started_at = time.monotonic()
    pending_event_candidates: list[list[PortfolioCandidate]] = []
    for event_key in sorted(touched_events):
        entry_bankroll = available_entry_bankroll(broker, client)
        candidates: list[PortfolioCandidate] = []
        updated_market_ids = updated_market_ids_by_event.get(event_key, set())
        markets_to_evaluate = [
            market
            for market in event_groups[event_key]
            if (
                market.market_id in updated_market_ids
                or market.market_id in held_market_ids
            )
            and market.market_id in evaluation_market_ids
        ]
        for market in markets_to_evaluate:
            market_type = market_types.get(market.market_id, "temperature")
            try:
                drawdown_gate = _drawdown_entry_gate(market, broker, settings, current, market_type)
                if drawdown_gate is not None:
                    signal, result = drawdown_gate
                    per_side: dict[str, EdgeResult] = {}
                    existing_signal = signals_by_market.get(market.market_id)
                    if existing_signal is not None:
                        _refresh_held_exit_edges_from_signal(
                            broker,
                            market,
                            existing_signal,
                            latest_edges,
                            result.reason,
                        )
                    latest_edges[(market.market_id, REALTIME_LAST_EVALUATION_SIDE)] = result
                    decision_ts = broker.log_decision(market, result, signal.note, market_type, signal=signal)
                    candidates.extend(_event_portfolio_candidates(market, signal, result, per_side, market_type, decision_ts))
                    continue
                if market.market_id in signal_prefetch_errors:
                    raise signal_prefetch_errors[market.market_id]
                if market.market_id not in signals_by_market or _realtime_signal_is_stale(
                    market,
                    settings,
                    signal_refreshed_at_by_market,
                    now=current,
                ):
                    _refresh_realtime_signal_if_needed(
                        market,
                        settings,
                        signals_by_market,
                        signal_refreshed_at_by_market,
                        probability_estimator=probability_estimator,
                        observation_provider=observation_provider,
                        residual_profile_store=residual_profile_store,
                        now=current,
                    )
                if market.market_id not in signals_by_market:
                    _record_station_signal_pending(
                        broker,
                        market,
                        market_type,
                        "official station signal pending; new entry blocked until station evidence is available",
                    )
                    continue
                _refresh_realtime_signal_if_needed(
                    market,
                    settings,
                    signals_by_market,
                    signal_refreshed_at_by_market,
                    probability_estimator=probability_estimator,
                    observation_provider=observation_provider,
                    residual_profile_store=residual_profile_store,
                    now=current,
                )
                signal = signals_by_market[market.market_id]
                result, per_side = evaluate_market(
                    market,
                    signal,
                    client,
                    settings,
                    entry_bankroll.entry_bankroll,
                    market_type,
                    entry_bankroll.reason,
                    allowed_sides=allowed_sides_by_market.get(market.market_id),
                )
                for side, edge_result in per_side.items():
                    latest_edges[(market.market_id, side)] = edge_result
                latest_edges[(market.market_id, REALTIME_LAST_EVALUATION_SIDE)] = result
                held_exit_edges: dict[str, EdgeResult] = {}
                if not per_side and "entry_bankroll=$" in result.reason:
                    held_exit_edges = _refresh_held_exit_edges_from_signal(
                        broker,
                        market,
                        signal,
                        latest_edges,
                        entry_bankroll.reason,
                    )
                decision_ts = broker.log_decision(market, result, signal.note, market_type, signal=signal)
                candidates.extend(_event_portfolio_candidates(market, signal, result, per_side, market_type, decision_ts))
                broker.log_raw_snapshot(
                    "realtime_decision",
                    market,
                    {
                        "updated_token_ids": sorted(updated_token_ids),
                        "signal": {
                            "p_true": signal.p_true,
                            "confidence": signal.confidence,
                            "source": signal.source,
                            "note": signal.note,
                            "nowcast": signal.nowcast,
                        },
                        "entry_bankroll": entry_bankroll.__dict__,
                        "websocket": websocket_health,
                        "per_side": {side: edge.__dict__ for side, edge in per_side.items()},
                        "held_exit_edges": {side: edge.__dict__ for side, edge in held_exit_edges.items()},
                    },
                )
            except Exception as exc:  # noqa: BLE001
                signal, result, _last_market_error, _market_error_count = _record_market_evaluation_error(
                    broker,
                    market,
                    exc,
                    market_type,
                    signal=signals_by_market.get(market.market_id),
                    context="realtime_update",
                )
                candidates.extend(_event_portfolio_candidates(market, signal, result, {}, market_type))
        pending_event_candidates.append(candidates)

    market_evaluation_duration_seconds = time.monotonic() - market_evaluation_started_at
    final_prefetch_started_at = time.monotonic()
    final_checks: list[tuple[str, str]] = []
    for candidates in pending_event_candidates:
        for candidate in _new_entry_candidates_for_strategy(candidates, settings):
            if candidate.result.side not in {"YES", "NO"}:
                continue
            token_id = (
                candidate.market.yes_token_id
                if candidate.result.side == "YES"
                else candidate.market.no_token_id
            )
            if candidate.market.condition_id and token_id:
                final_checks.append((candidate.market.condition_id, token_id))
                complement_token_id = (
                    _market_token_for_side(candidate.market, "YES")
                    if candidate.result.side == "NO"
                    else None
                )
                if complement_token_id:
                    final_checks.append(
                        (candidate.market.condition_id, complement_token_id)
                    )
    prefetch_status = (
        prefetch(final_checks)
        if callable(prefetch)
        else {"requested": 0, "book_ready": 0, "failed": 0, "deferred": 0}
    )
    update_runner_status_fields(
        settings,
        realtime_final_prefetch={
            **prefetch_status,
            "updated_at": utc_now_iso(),
        },
    )
    final_prefetch_duration_seconds = time.monotonic() - final_prefetch_started_at

    portfolio_apply_started_at = time.monotonic()
    for candidates in pending_event_candidates:
        entry_bankroll = available_entry_bankroll(broker, client)
        _apply_event_portfolio(
            broker,
            candidates,
            entry_bankroll,
            client=client,
            observation_provider=observation_provider,
            residual_profile_store=residual_profile_store,
        )
    for message in maybe_close_positions(broker, client, market_by_id, latest_edges):
        print(message)
    portfolio_apply_duration_seconds = time.monotonic() - portfolio_apply_started_at
    breakdown = {
        "event_count": len(touched_events),
        "market_count": len(markets_selected_for_evaluation),
        "book_unavailable_market_count": len(book_unavailable_market_ids),
        "book_unavailable_market_ids_sample": sorted(book_unavailable_market_ids)[:10],
        "signal_ineligible_market_count": len(signal_ineligible_market_ids),
        "signal_ineligible_market_ids_sample": sorted(signal_ineligible_market_ids)[:10],
        "candidate_book_prefetch": candidate_prefetch_status,
        "candidate_book_prefetch_seconds": round(candidate_prefetch_duration_seconds, 3),
        "signal_prefetch_seconds": round(signal_prefetch_duration_seconds, 3),
        "market_evaluation_seconds": round(market_evaluation_duration_seconds, 3),
        "final_prefetch_seconds": round(final_prefetch_duration_seconds, 3),
        "portfolio_apply_seconds": round(portfolio_apply_duration_seconds, 3),
        "total_seconds": round(time.monotonic() - evaluation_started_at, 3),
        "completed_at": utc_now_iso(),
    }
    update_runner_status_fields(
        settings,
        realtime_evaluation_breakdown=breakdown,
    )
    return breakdown


def _refresh_realtime_signal_if_needed(
    market: RawMarket,
    settings: Settings,
    signals_by_market: dict[str, WeatherSignal],
    signal_refreshed_at_by_market: dict[str, datetime] | None,
    *,
    probability_estimator: Any = estimate_station_probability,
    observation_provider: Any | None = None,
    residual_profile_store: ResidualProfileStore | None = None,
    now: datetime | None = None,
) -> None:
    if signal_refreshed_at_by_market is None:
        return
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    last_refreshed_at = signal_refreshed_at_by_market.get(market.market_id)
    if last_refreshed_at is not None:
        last_refreshed_at = last_refreshed_at.astimezone(timezone.utc)
    if (
        last_refreshed_at is not None
        and (current - last_refreshed_at).total_seconds() < settings.station_nowcast_cache_ttl_seconds
    ):
        return

    signal = _compute_realtime_signal(
        market,
        settings,
        probability_estimator=probability_estimator,
        observation_provider=observation_provider,
        residual_profile_store=residual_profile_store,
        now=current,
    )
    signals_by_market[market.market_id] = signal
    signal_refreshed_at_by_market[market.market_id] = current


def _compute_realtime_signal(
    market: RawMarket,
    settings: Settings,
    *,
    probability_estimator: Any = estimate_station_probability,
    observation_provider: Any | None = None,
    residual_profile_store: ResidualProfileStore | None = None,
    now: datetime,
) -> WeatherSignal:
    gated = pre_station_tradeability_gate(market, settings, "temperature")
    if gated is not None:
        signal, _result = gated
        return signal
    return _call_probability_estimator(
        probability_estimator,
        market.question,
        settings=settings,
        observation_provider=observation_provider,
        residual_profile_store=residual_profile_store,
        now=now,
    )


def _prefetch_realtime_signals(
    markets: list[RawMarket],
    settings: Settings,
    signals_by_market: dict[str, WeatherSignal],
    signal_refreshed_at_by_market: dict[str, datetime] | None,
    *,
    probability_estimator: Any = estimate_station_probability,
    observation_provider: Any | None = None,
    residual_profile_store: ResidualProfileStore | None = None,
    now: datetime,
    max_workers: int = REALTIME_FINAL_CHECK_MAX_WORKERS,
) -> dict[str, Exception]:
    if signal_refreshed_at_by_market is None:
        return {}
    current = _utc_datetime(now)
    unique_markets = list({market.market_id: market for market in markets}.values())
    stale_markets = [
        market
        for market in unique_markets
        if market.market_id not in signals_by_market
        or _realtime_signal_is_stale(
            market,
            settings,
            signal_refreshed_at_by_market,
            now=current,
        )
    ]
    if not stale_markets:
        return {}

    def compute(market: RawMarket) -> WeatherSignal:
        return _compute_realtime_signal(
            market,
            settings,
            probability_estimator=probability_estimator,
            observation_provider=observation_provider,
            residual_profile_store=residual_profile_store,
            now=current,
        )

    worker_count = min(max(1, int(max_workers)), len(stale_markets))
    if worker_count == 1:
        completed = []
        errors: dict[str, Exception] = {}
        for market in stale_markets:
            try:
                completed.append((market, compute(market)))
            except Exception as exc:  # noqa: BLE001
                errors[market.market_id] = exc
    else:
        completed = []
        errors = {}
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="station-signal") as executor:
            future_by_market = {executor.submit(compute, market): market for market in stale_markets}
            for future, market in future_by_market.items():
                try:
                    completed.append((market, future.result()))
                except Exception as exc:  # noqa: BLE001
                    errors[market.market_id] = exc
    for market, signal in completed:
        signals_by_market[market.market_id] = signal
        signal_refreshed_at_by_market[market.market_id] = current
    return errors


def _stream_status_phase(
    websocket_health: dict[str, object],
    *,
    token_count: int,
    market_count: int,
    event_count: int,
    city_count: int,
) -> tuple[str, str]:
    coverage = f"{token_count} tokens across {market_count} markets, {event_count} events, {city_count} cities"
    if token_count <= 0:
        return "stream_waiting", f"no streamable temperature markets discovered; websocket waiting; {coverage}"
    block_reason = websocket_pricing_block_reason(websocket_health)
    recovery = ""
    if block_reason:
        recovery = f": {block_reason}"
    if not websocket_health.get("thread_alive"):
        return "stream_error", f"websocket thread stopped{recovery}; {coverage}"
    if websocket_health.get("stale"):
        return "stream_stale", f"websocket order book stale{recovery}; {coverage}"
    return "streaming", f"websocket streaming {coverage}"


def _stream_should_rebuild(websocket_health: dict[str, object], *, token_count: int) -> bool:
    return token_count > 0 and (
        not bool(websocket_health.get("thread_alive"))
        or bool(websocket_health.get("stale"))
    )


def _refresh_official_station_observations(
    observation_provider: Any,
    *,
    now: datetime,
    station_state_by_id: dict[str, tuple[Any, ...]] | None = None,
    on_shared_metar_refreshed: Callable[[set[str]], None] | None = None,
    station_ids: set[str] | None = None,
) -> set[str]:
    global _station_observation_refresh_cursor
    current = _utc_datetime(now)
    allowed_station_ids = (
        None
        if station_ids is None
        else {str(station_id).upper() for station_id in station_ids}
    )
    prepare_daily_extremes = getattr(
        observation_provider,
        "prepare_daily_extremes",
        None,
    )
    if callable(prepare_daily_extremes):
        try:
            prepare_daily_extremes(
                now=current,
                station_ids=allowed_station_ids,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                "STATION HISTORY PREPARE ERROR: "
                f"error_type={type(exc).__name__}"
            )
    stations = [
        station
        for station in TRADING_READY_STATION_MAP.values()
        if allowed_station_ids is None or station.station_id.upper() in allowed_station_ids
    ]
    if stations:
        offset = _station_observation_refresh_cursor % len(stations)
        stations = stations[offset:] + stations[:offset]
        _station_observation_refresh_cursor = (offset + 1) % len(stations)

    def refresh(
        group: list[Any],
        on_changed: Callable[[set[str]], None] | None = None,
    ) -> set[str]:
        def fetch(station: Any) -> tuple[Any, Any]:
            target_date = current.astimezone(ZoneInfo(station.timezone)).date()
            observation = observation_provider.observed_temperature_extremes_so_far(
                station,
                target_date=target_date,
                now=current,
            )
            return station, observation

        changed_station_ids: set[str] = set()
        released_station_ids: set[str] = set()

        def record(station: Any, observation: Any) -> None:
            if station_state_by_id is None:
                return
            station_id = str(
                getattr(observation, "station_id", "") or station.station_id
            ).upper()
            state_key = _station_observation_state_key(observation)
            initial = station_id not in station_state_by_id
            changed = (
                not initial
                and station_state_by_id[station_id] != state_key
            )
            station_state_by_id[station_id] = state_key
            if initial or changed:
                released_station_ids.add(station_id)
                if on_changed is not None and parallel:
                    on_changed({station_id})
            if not changed:
                return
            changed_station_ids.add(station_id)

        parallel = (
            getattr(observation_provider, "supports_parallel_station_refresh", False)
            and len(group) > 1
        )
        if parallel:
            with ThreadPoolExecutor(
                max_workers=len(group),
                thread_name_prefix="station-refresh",
            ) as executor:
                futures = {executor.submit(fetch, station): station for station in group}
                for future in as_completed(futures):
                    station = futures[future]
                    try:
                        refreshed_station, observation = future.result()
                    except Exception as exc:  # noqa: BLE001
                        print(
                            "STATION REFRESH ERROR: "
                            f"station_id={station.station_id}; error_type={type(exc).__name__}"
                        )
                        continue
                    record(refreshed_station, observation)
        else:
            refreshed = [fetch(station) for station in group]
            for station, observation in refreshed:
                record(station, observation)
            if on_changed is not None and released_station_ids:
                on_changed(set(released_station_ids))
        return changed_station_ids

    metar_stations = [station for station in stations if station.nowcast_source_type == "metar"]
    other_stations = [station for station in stations if station.nowcast_source_type != "metar"]
    changed_station_ids = refresh(metar_stations, on_shared_metar_refreshed)
    changed_station_ids.update(refresh(other_stations))
    return changed_station_ids


def _station_refresh_is_due(
    refreshed_at: datetime,
    settings: Settings,
    *,
    now: datetime,
) -> bool:
    return (now - refreshed_at).total_seconds() >= max(
        1,
        int(settings.station_refresh_poll_seconds),
    )


def _scheduled_realtime_probe_tokens(
    markets: list[RawMarket],
    signals_by_market: dict[str, WeatherSignal],
    timer_bucket_by_market: dict[str, str],
    *,
    now: datetime,
) -> tuple[set[str], set[str], set[str]]:
    """Wake quiet markets when a local-time gate or residual 30-minute bin changes."""
    urgent_tokens: set[str] = set()
    normal_tokens: set[str] = set()
    market_ids_to_expire: set[str] = set()
    current = _utc_datetime(now)
    for market in markets:
        if not market.no_token_id:
            continue
        signal = signals_by_market.get(market.market_id)
        if signal is None:
            continue
        try:
            parsed = signal.parsed or parse_weather_question(market.question)
        except Exception:  # noqa: BLE001
            continue
        if not _is_realtime_no_candidate(parsed):
            continue
        payload = signal.nowcast if isinstance(signal.nowcast, dict) else {}
        timezone_name = str(payload.get("station_timezone") or "")
        if not timezone_name:
            station = TRADING_READY_STATION_MAP.get(str(parsed.city or "").lower())
            timezone_name = station.timezone if station is not None else ""
        try:
            local = current.astimezone(ZoneInfo(timezone_name))
        except (ValueError, ZoneInfoNotFoundError):
            continue
        target_date = str(payload.get("target_date_local") or "")
        if target_date and target_date != local.date().isoformat():
            continue

        local_minute = local.hour * 60 + local.minute
        direction = "low" if parsed.temperature_metric == "min" else "high"
        reason = str(payload.get("data_block_reason") or "")
        threshold: float | None = None
        if reason == "formation-monitoring-not-started":
            threshold = _finite_float(payload.get("monitoring_start_local_minute"))
        elif reason == "formation-q75-not-reached":
            threshold = _finite_float(payload.get(f"first_final_{direction}_local_minute_q75"))
        elif reason == "high-exact-before-16-local":
            threshold = 16 * 60
        if threshold is not None and local_minute >= threshold:
            urgent_tokens.add(str(market.no_token_id))
            market_ids_to_expire.add(market.market_id)

        timer_bucket = f"{local.date().isoformat()}:{local_minute // 30}"
        previous_bucket = timer_bucket_by_market.get(market.market_id)
        timer_bucket_by_market[market.market_id] = timer_bucket
        if (
            previous_bucket is not None
            and previous_bucket != timer_bucket
            and signal.signal_family == "intraday_observation_edge"
            and str(market.no_token_id) not in urgent_tokens
        ):
            normal_tokens.add(str(market.no_token_id))
            market_ids_to_expire.add(market.market_id)
    return urgent_tokens, normal_tokens, market_ids_to_expire


def _enqueue_official_station_refresh_updates(
    evaluator_worker: RealtimeEvaluationCoalescer | None,
    markets: list[RawMarket],
    signals_by_market: dict[str, WeatherSignal],
    timer_bucket_by_market: dict[str, str],
    signal_refreshed_at_by_market: dict[str, datetime],
    *,
    station_ids: set[str],
    now: datetime,
) -> None:
    urgent_timer_tokens, normal_timer_tokens, timer_market_ids = (
        _scheduled_realtime_probe_tokens(
            markets,
            signals_by_market,
            timer_bucket_by_market,
            now=now,
        )
    )
    for market_id in timer_market_ids:
        signal_refreshed_at_by_market.pop(market_id, None)
    _enqueue_station_refresh_high_exact_no_probes(
        evaluator_worker,
        markets,
        signal_refreshed_at_by_market,
        station_ids=station_ids,
    )
    _enqueue_realtime_update(evaluator_worker, urgent_timer_tokens, urgent=True)
    _enqueue_realtime_update(evaluator_worker, normal_timer_tokens)


def _realtime_error_backoff_seconds(settings: Settings) -> float:
    return min(max(float(settings.runner_health_status_interval_seconds), 5.0), 60.0)


def _validate_runtime_strategy_dependencies(settings: Settings) -> None:
    if settings.strategy_mode == "lock_only" and not settings.wunderground_api_key.strip():
        raise RuntimeError(
            "WUNDERGROUND_API_KEY is required when STRATEGY_MODE=lock_only; "
            "the secret value is never written to logs"
        )


def run_realtime_forever(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    _validate_runtime_strategy_dependencies(settings)
    while True:
        refresh_started_at = datetime.now(timezone.utc)
        cycle_started_at = utc_now_iso()
        failed_phase = "initializing"
        broker: PaperBroker | None = None
        stream: OrderBookMarketStream | None = None
        evaluator_worker: RealtimeEvaluationCoalescer | None = None
        try:
            discovery_client = PolymarketClient(settings.gamma_base, settings.clob_base)
            observation_provider = AviationWeatherMetarNowcastProvider.from_settings(settings)
            residual_profile_store = _load_residual_profile_store(settings)
            broker = PaperBroker(settings)
            failed_phase = "market_discovery"
            write_runner_status(
                settings,
                "discovering",
                message="discovering markets for websocket stream",
                cycle_started_at=cycle_started_at,
                **_market_error_status_fields(0, None),
            )
            discovered_markets = discovery_client.discover_weather_markets(
                max_pages=settings.discovery_max_pages,
                page_size=settings.discovery_page_size,
            )
            raw_discovered_count = len(discovered_markets)
            failed_phase = "market_preparation"
            discovered_markets = _temperature_markets_only(discovered_markets)
            event_groups = _group_weather_markets_by_event(discovered_markets)
            markets = [market for group in event_groups for market in group]
            temperature_coverage = _discovery_coverage(markets)
            market_by_id = _stream_market_registry(discovery_client, broker, markets)
            for msg in _settle_resolved_positions_before_streaming(broker, market_by_id):
                print(msg)
            open_market_ids = {pos.market_id for pos in broker.state.positions}
            stream_candidates = _temperature_markets_only(list(market_by_id.values()))
            stream_markets: list[RawMarket] = []
            precomputed_signals: dict[str, WeatherSignal] = {}
            pre_station_skip_counts: dict[str, int] = {}
            for market in stream_candidates:
                market_type = "temperature"
                gated = pre_station_tradeability_gate(market, settings, market_type)
                if gated is not None:
                    signal, result = gated
                    skip_reason = _pre_station_skip_reason(signal, result)
                    pre_station_skip_counts[skip_reason] = pre_station_skip_counts.get(skip_reason, 0) + 1
                    precomputed_signals[market.market_id] = signal
                    broker.log_decision(market, result, signal.note, market_type, signal=signal)
                    broker.log_raw_snapshot(
                        "pre_station_skip",
                        market,
                        {
                            "market_raw": market.raw,
                            "signal": {
                                "p_true": signal.p_true,
                                "confidence": signal.confidence,
                                "source": signal.source,
                                "note": signal.note,
                                "nowcast": signal.nowcast,
                            },
                            "per_side": {},
                        },
                    )
                    if market.market_id in open_market_ids:
                        stream_markets.append(market)
                    continue
                stream_markets.append(market)
            stream_markets = _select_realtime_stream_markets(
                stream_markets,
                broker,
                now=datetime.now(timezone.utc),
            )
            station_ids_for_refresh: set[str] = set()
            for market in stream_markets:
                try:
                    parsed = parse_weather_question(market.question)
                except Exception:  # noqa: BLE001
                    continue
                station_id = _market_station_id(market, parsed)
                if station_id:
                    station_ids_for_refresh.add(station_id)
            for market in stream_markets:
                market_by_id[market.market_id] = market
            coverage = _discovery_coverage(stream_markets)
            market_by_token = _market_by_token_with_held_positions_first(stream_markets, broker)
            discovery_status = {
                "raw_discovered_markets": raw_discovered_count,
                "temperature_markets": temperature_coverage["markets"],
                "temperature_events": temperature_coverage["events"],
                "temperature_cities": temperature_coverage["cities"],
                "stream_candidates": len(stream_candidates),
                "pre_station_skipped": sum(pre_station_skip_counts.values()),
                "pre_station_skip_reasons": pre_station_skip_counts,
                "stream_markets": len(stream_markets),
                "stream_events": coverage["events"],
                "stream_cities": coverage["cities"],
                "stream_tokens": len(market_by_token),
            }
            signals_by_market: dict[str, WeatherSignal] = {}
            signal_refreshed_at_by_market: dict[str, datetime] = {}
            market_types: dict[str, str] = {}
            for market in stream_markets:
                signal = precomputed_signals.get(market.market_id)
                if signal is not None:
                    signals_by_market[market.market_id] = signal
                    signal_refreshed_at_by_market[market.market_id] = datetime.now(timezone.utc)
                market_types[market.market_id] = "temperature"
            latest_edges: dict[tuple[str, str], EdgeResult] = {}
            update_lock = threading.RLock()
            pending_signal_invalidations: SimpleQueue[str] = SimpleQueue()
            stream_holder: dict[str, StreamBackedPolymarketClient] = {}
            latest_realtime_evaluation: dict[str, object] | None = None
            latest_official_station_refresh: dict[str, object] | None = None
            event_key_by_token = _realtime_evaluation_trigger_tokens(stream_markets, broker)
            price_watch_token_ids = _realtime_price_watch_token_ids(
                stream_markets,
                broker,
                signals_by_market,
                settings,
            )
            wake_when_book_returns: set[str] = set()
            candidate_book_retry_by_token: dict[str, tuple[float, str, bool]] = {}
            prefilter_skip_state_by_market: dict[str, str] = {}
            fast_shadow_book_state_by_market: dict[str, str] = {}
            event_priorities = _realtime_event_priorities(
                list(market_by_id.values()),
                open_market_ids=open_market_ids,
                now=datetime.now(timezone.utc),
            )

            def evaluate_queued_update(updated_token_ids: set[str]) -> None:
                nonlocal latest_realtime_evaluation, price_watch_token_ids
                with update_lock, broker.batch_skip_diagnostics():
                    _drain_pending_signal_invalidations(
                        pending_signal_invalidations,
                        signal_refreshed_at_by_market,
                    )
                    stream_client = stream_holder.get("client")
                    if stream_client is not None:
                        latest_realtime_evaluation = _evaluate_realtime_update(
                            updated_token_ids,
                            stream_client,
                            broker,
                            settings,
                            market_by_token,
                            signals_by_market,
                            market_types,
                            latest_edges,
                            signal_refreshed_at_by_market=signal_refreshed_at_by_market,
                            observation_provider=observation_provider,
                            residual_profile_store=residual_profile_store,
                            wake_when_book_returns=wake_when_book_returns,
                            candidate_book_retry_by_token=candidate_book_retry_by_token,
                            prefilter_skip_state_by_market=prefilter_skip_state_by_market,
                            fast_shadow_book_state_by_market=fast_shadow_book_state_by_market,
                        )
                        price_watch_token_ids = _realtime_price_watch_token_ids(
                            stream_markets,
                            broker,
                            signals_by_market,
                            settings,
                        ) | wake_when_book_returns

            def update_evaluator_status(status: dict[str, object]) -> None:
                update_runner_status_fields(settings, realtime_evaluator=status)

            evaluator_worker = RealtimeEvaluationCoalescer(
                event_key_by_token=event_key_by_token,
                evaluator=evaluate_queued_update,
                event_priority=lambda event_key: event_priorities.get(str(event_key), (9, 9999)),
                status_update=update_evaluator_status,
            )
            evaluator_worker.start()

            def on_update(updated_token_ids: set[str]) -> None:
                _enqueue_realtime_update(
                    evaluator_worker,
                    _orderbook_update_tokens_for_realtime_evaluation(
                        updated_token_ids,
                        broker,
                        price_watch_token_ids,
                    ),
                )

            def build_stream() -> OrderBookMarketStream:
                rest_snapshot_fetcher = getattr(discovery_client, "get_order_book", None)
                return OrderBookMarketStream(
                    settings.orderbook_stream_url,
                    on_update=on_update,
                    heartbeat_seconds=settings.orderbook_stream_heartbeat_seconds,
                    reconnect_seconds=settings.orderbook_stream_reconnect_seconds,
                    stale_seconds=settings.orderbook_stream_stale_seconds,
                    rest_snapshot_fetcher=rest_snapshot_fetcher,
                    rest_snapshot_enabled=settings.orderbook_rest_snapshot_enabled and callable(rest_snapshot_fetcher),
                    rest_snapshot_interval_seconds=settings.orderbook_rest_snapshot_interval_seconds,
                    background_rest_snapshot_enabled=False,
                )

            failed_phase = "websocket_start"
            stream = build_stream()
            stream_holder["client"] = StreamBackedPolymarketClient(settings.gamma_base, settings.clob_base, stream)
            stream.start(market_by_token.keys())

            def write_stream_status(websocket_health: dict[str, object] | None = None) -> None:
                websocket_health = websocket_health or stream.health_snapshot()
                market_error_count, last_market_error = _market_error_status(settings)
                phase, message = _stream_status_phase(
                    websocket_health,
                    token_count=len(market_by_token),
                    market_count=len(stream_markets),
                    event_count=coverage["events"],
                    city_count=coverage["cities"],
                )
                write_runner_status(
                    settings,
                    phase,
                    message=message,
                    cycle_started_at=cycle_started_at,
                    markets_done=0,
                    markets_total=len(stream_markets),
                    events_total=coverage["events"],
                    cities_total=coverage["cities"],
                    cash_usd=round(broker.state.cash_usd, 2),
                    exposure_usd=round(broker.total_exposure(), 2),
                    open_positions=len(broker.state.positions),
                    websocket=websocket_health,
                    realtime_evaluator=evaluator_worker.status_snapshot() if evaluator_worker is not None else None,
                    realtime_evaluation_breakdown=latest_realtime_evaluation,
                    official_station_refresh=latest_official_station_refresh,
                    strategy={
                        "mode": settings.strategy_mode,
                        "no_only_new_entries": settings.no_only_new_entries,
                    },
                    discovery=discovery_status,
                    **_market_error_status_fields(market_error_count, last_market_error),
                )

            failed_phase = "runner_status_update"
            write_stream_status()
            status_updated_at = datetime.now(timezone.utc)
            station_state_by_id: dict[str, tuple[Any, ...]] = {}
            timer_bucket_by_market: dict[str, str] = {}
            station_refreshed_at = status_updated_at - timedelta(
                seconds=max(1, int(settings.station_refresh_poll_seconds))
            )
            try:
                failed_phase = "websocket_monitoring"
                while True:
                    now = datetime.now(timezone.utc)
                    elapsed = (now - refresh_started_at).total_seconds()
                    if elapsed >= settings.stream_cycle_interval_seconds:
                        break
                    with update_lock:
                        _enqueue_due_candidate_book_retries(
                            evaluator_worker,
                            candidate_book_retry_by_token,
                        )
                    websocket_health = stream.health_snapshot()
                    if _stream_should_rebuild(websocket_health, token_count=len(market_by_token)):
                        failed_phase = "runner_status_update"
                        write_stream_status(websocket_health)
                        print(f"STREAM REBUILD {websocket_health.get('status_reason') or 'websocket thread stopped'}")
                        failed_phase = "websocket_rebuild"
                        with update_lock:
                            stream.stop()
                            stream = build_stream()
                            stream_holder["client"] = StreamBackedPolymarketClient(settings.gamma_base, settings.clob_base, stream)
                            stream.start(market_by_token.keys())
                        status_updated_at = now
                    if _station_refresh_is_due(station_refreshed_at, settings, now=now):
                        failed_phase = "station_observation_refresh"
                        station_refresh_started_at = time.monotonic()
                        metar_changed_station_ids: set[str] = set()
                        shared_metar_duration_seconds = 0.0

                        def release_shared_metar_changes(
                            changed_ids: set[str],
                        ) -> None:
                            nonlocal metar_changed_station_ids, shared_metar_duration_seconds
                            metar_changed_station_ids.update(changed_ids)
                            shared_metar_duration_seconds = round(
                                time.monotonic() - station_refresh_started_at,
                                3,
                            )
                            _enqueue_station_refresh_high_exact_no_probes(
                                evaluator_worker,
                                stream_markets,
                                signal_refreshed_at_by_market,
                                pending_signal_invalidations=pending_signal_invalidations,
                                station_ids=changed_ids,
                            )

                        changed_station_ids = _refresh_official_station_observations(
                            observation_provider,
                            now=now,
                            station_state_by_id=station_state_by_id,
                            on_shared_metar_refreshed=release_shared_metar_changes,
                            station_ids=station_ids_for_refresh,
                        )
                        station_refresh_duration_seconds = round(
                            time.monotonic() - station_refresh_started_at,
                            3,
                        )
                        _enqueue_station_refresh_high_exact_no_probes(
                            evaluator_worker,
                            stream_markets,
                            signal_refreshed_at_by_market,
                            pending_signal_invalidations=pending_signal_invalidations,
                            station_ids=(changed_station_ids - metar_changed_station_ids),
                        )
                        with update_lock:
                            _enqueue_official_station_refresh_updates(
                                evaluator_worker,
                                stream_markets,
                                signals_by_market,
                                timer_bucket_by_market,
                                signal_refreshed_at_by_market,
                                station_ids=set(),
                                now=now,
                            )
                        latest_official_station_refresh = {
                            "duration_seconds": station_refresh_duration_seconds,
                            "shared_metar_duration_seconds": shared_metar_duration_seconds,
                            "remaining_source_duration_seconds": round(
                                max(
                                    0.0,
                                    station_refresh_duration_seconds
                                    - shared_metar_duration_seconds,
                                ),
                                3,
                            ),
                            "changed_station_count": len(changed_station_ids),
                            "changed_station_ids": sorted(changed_station_ids),
                            "completed_at": utc_now_iso(),
                        }
                        request_log_health = getattr(
                            observation_provider,
                            "request_log_health",
                            None,
                        )
                        if callable(request_log_health):
                            latest_official_station_refresh["request_log"] = request_log_health()
                        update_runner_status_fields(
                            settings,
                            official_station_refresh=latest_official_station_refresh,
                        )
                        # Keep the five-second cadence measured start-to-start.
                        # Provider-specific caches enforce the external request
                        # floors, while completed city data is reconsidered at once.
                        station_refreshed_at = now
                    if (now - status_updated_at).total_seconds() >= settings.runner_health_status_interval_seconds:
                        failed_phase = "runner_status_update"
                        write_stream_status()
                        status_updated_at = now
                    failed_phase = "websocket_monitoring"
                    time.sleep(1)
            finally:
                if evaluator_worker is not None:
                    failed_phase = "realtime_evaluator_stop"
                    evaluator_worker.stop(drain=False)
                    evaluator_worker = None
                try:
                    failed_phase = "websocket_stop"
                    stream.stop()
                except Exception:  # noqa: BLE001
                    raise
                stream = None
        except Exception as exc:  # noqa: BLE001
            evaluator_status = evaluator_worker.status_snapshot() if evaluator_worker is not None else None
            websocket_health = None
            if stream is not None and hasattr(stream, "health_snapshot"):
                try:
                    websocket_health = stream.health_snapshot()
                except Exception as health_exc:  # noqa: BLE001
                    websocket_health = {
                        "thread_alive": False,
                        "stale": True,
                        "last_error": f"{health_exc.__class__.__name__}: {health_exc}",
                        "status_reason": "websocket health snapshot failed during realtime error handling",
                    }
            if evaluator_worker is not None:
                evaluator_worker.stop(drain=False)
                evaluator_worker = None
            if stream is not None:
                try:
                    stream.stop()
                except Exception as stop_exc:  # noqa: BLE001
                    print(f"STREAM STOP ERROR after realtime failure: {stop_exc}")
                stream = None
            message = f"realtime refresh cycle failed during {failed_phase}: {exc}"
            market_error_count, last_market_error = _market_error_status(settings)
            write_runner_status(
                settings,
                "error",
                message=message,
                failed_phase=failed_phase,
                error_type=exc.__class__.__name__,
                cycle_started_at=cycle_started_at,
                cash_usd=round(broker.state.cash_usd, 2) if broker is not None else None,
                exposure_usd=round(broker.total_exposure(), 2) if broker is not None else None,
                open_positions=len(broker.state.positions) if broker is not None else None,
                websocket=websocket_health,
                realtime_evaluator=evaluator_status,
                strategy={
                    "mode": settings.strategy_mode,
                    "no_only_new_entries": settings.no_only_new_entries,
                },
                **_market_error_status_fields(market_error_count, last_market_error),
            )
            print(f"REALTIME ERROR: {message}")
            backoff_seconds = _realtime_error_backoff_seconds(settings)
            print(f"Retrying realtime refresh cycle in {backoff_seconds:.0f}s.")
            time.sleep(backoff_seconds)


def run_forever(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    if not settings.orderbook_stream_enabled:
        raise RuntimeError("ORDERBOOK_STREAM_ENABLED=false disables the required real-time order-book stream.")
    run_realtime_forever(settings)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Paper-only realtime weather-market runner")
    parser.add_argument(
        "--dry-start",
        action="store_true",
        help="validate local settings and residual-profile assets without network access",
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.dry_start:
        if not settings.orderbook_stream_enabled:
            raise RuntimeError("ORDERBOOK_STREAM_ENABLED=false disables the required real-time order-book stream.")
        _validate_runtime_strategy_dependencies(settings)
        residual_store = _load_residual_profile_store(settings)
        profile_status = "loaded" if residual_store is not None else "disabled"
        print(
            "DRY START OK: "
            f"paper_only=true strategy_mode={settings.strategy_mode} "
            f"residual_profiles={profile_status}"
        )
        return
    run_forever(settings)


if __name__ == "__main__":
    main()
