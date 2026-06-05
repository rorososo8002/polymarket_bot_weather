from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from itertools import combinations
from math import ceil, floor, inf, isinf, log
from typing import TYPE_CHECKING, Any

from .config import Settings
from .edge import clamp_probability, executable_sell_price, polymarket_taker_fee_usdc
from .exit_policy import side_true_probability
from .models import EdgeResult, PaperPosition, RawMarket, WeatherSignal
from .weather_client import (
    TemperatureBucketInterval,
    parse_weather_question,
    rounded_temperature_bucket_interval_f,
    temperature_bucket_interval_bounds_f,
)

if TYPE_CHECKING:
    from .paper import PaperBroker
    from .polymarket_client import PolymarketClient


@dataclass(frozen=True)
class EntryBankrollSnapshot:
    usable: bool
    cost_basis_bankroll: float
    liquidation_bankroll: float
    entry_bankroll: float
    reason: str


@dataclass(frozen=True)
class PortfolioCandidate:
    market: RawMarket
    signal: WeatherSignal
    result: EdgeResult
    market_type: str
    decision_ts: str = ""
    add_to_existing_position_id: str | None = None


@dataclass(frozen=True)
class RejectedPortfolioLeg:
    market_id: str
    side: str
    reason: str


@dataclass
class EventPortfolioDecision:
    event_key: str
    city: str
    date_hint: str
    entry_bankroll: EntryBankrollSnapshot
    event_cap_fraction: float
    event_cap_usd: float
    existing_event_exposure_usd: float
    selected: list[PortfolioCandidate]
    rejected: list[RejectedPortfolioLeg]
    selected_exposure_usd: float
    expected_net_profit_usd: float
    expected_log_growth: float
    scenario_probabilities: dict[str, float]
    scenario_pnl_usd: dict[str, float]

    def to_log_payload(self) -> dict[str, Any]:
        rejected_reason_counts = Counter(leg.reason for leg in self.rejected)
        rejected_sample_limit = 10
        worst_scenario_pnl_usd = min(self.scenario_pnl_usd.values()) if self.scenario_pnl_usd else 0.0
        return {
            "event_key": self.event_key,
            "city": self.city,
            "date_hint": self.date_hint,
            "entry_bankroll_usable": self.entry_bankroll.usable,
            "entry_bankroll_reason": self.entry_bankroll.reason,
            "cost_basis_bankroll_usd": round(self.entry_bankroll.cost_basis_bankroll, 6),
            "liquidation_bankroll_usd": round(self.entry_bankroll.liquidation_bankroll, 6),
            "entry_bankroll_usd": round(self.entry_bankroll.entry_bankroll, 6),
            "event_cap_fraction": round(self.event_cap_fraction, 6),
            "event_cap_usd": round(self.event_cap_usd, 6),
            "existing_event_exposure_usd": round(self.existing_event_exposure_usd, 6),
            "selected_exposure_usd": round(self.selected_exposure_usd, 6),
            "total_event_exposure_usd": round(self.existing_event_exposure_usd + self.selected_exposure_usd, 6),
            "expected_net_profit_usd": round(self.expected_net_profit_usd, 6),
            "expected_log_growth": round(self.expected_log_growth, 9),
            "selected_count": len(self.selected),
            "rejected_count": len(self.rejected),
            "selected_legs": [
                {
                    "market_id": leg.market.market_id,
                    "side": leg.result.side,
                    "size_usd": round(leg.result.size_usd, 6),
                    "size_shares": round(leg.result.size_shares, 6),
                    "p_exec": leg.result.p_exec,
                    "decision_ts": leg.decision_ts,
                    "add_to_existing_position_id": leg.add_to_existing_position_id,
                    "expected_net_profit_usd": round(leg.result.expected_net_profit_usd, 6),
                }
                for leg in self.selected
            ],
            "rejected_legs_sample": [
                {"market_id": leg.market_id, "side": leg.side, "reason": leg.reason}
                for leg in self.rejected[:rejected_sample_limit]
            ],
            "rejected_reason_counts": dict(sorted(rejected_reason_counts.items())),
            "worst_scenario_pnl_usd": round(worst_scenario_pnl_usd, 6),
        }


@dataclass(frozen=True)
class _PortfolioPlan:
    selected: tuple[PortfolioCandidate, ...]
    expected_net_profit_usd: float
    expected_log_growth: float
    scenario_pnl_usd: dict[str, float]


@dataclass(frozen=True)
class _ScenarioProbabilityAssessment:
    probabilities: dict[str, float]
    fail_closed_reason: str | None = None


_SCENARIO_PROBABILITY_EPSILON = 1e-9
_MAX_ALLOCATION_SIZE_CANDIDATES = 50
_ALLOCATION_SIZE_ROUND_DIGITS = 6


def adaptive_event_cap_fraction(entry_bankroll: float, settings: Settings) -> float:
    if entry_bankroll >= settings.event_date_exposure_transition_usd:
        return settings.large_bankroll_event_date_exposure_fraction
    return settings.max_event_date_exposure_fraction


def websocket_pricing_block_reason(health: dict[str, Any]) -> str | None:
    if not health:
        return None
    thread_alive = bool(health.get("thread_alive"))
    stale = bool(health.get("stale"))
    if thread_alive and not stale:
        return None

    if health.get("status_reason"):
        detail = str(health["status_reason"])
    elif not thread_alive:
        detail = "websocket receiver thread is not running"
    else:
        detail = "websocket executable order book depth is stale"

    reconnect_count = health.get("reconnect_count")
    if reconnect_count not in (None, "", 0, "0") and "reconnects=" not in detail:
        detail = f"{detail}; reconnects={reconnect_count}"
    last_error = str(health.get("last_error") or "")
    if last_error and "last_error=" not in detail:
        detail = f"{detail}; last_error={last_error}"
    return (
        f"websocket order book stream unhealthy: {detail}; "
        "new entries blocked and held-position exit evaluation paused until executable WebSocket depth resumes"
    )


def available_entry_bankroll(broker: PaperBroker, client: PolymarketClient) -> EntryBankrollSnapshot:
    cost_basis_bankroll = broker.current_bankroll_before_entry()
    stream = getattr(client, "stream", None)
    if stream is not None and hasattr(stream, "health_snapshot"):
        health = stream.health_snapshot()
        block_reason = websocket_pricing_block_reason(health)
        if block_reason:
            return EntryBankrollSnapshot(
                False,
                cost_basis_bankroll,
                0.0,
                0.0,
                block_reason,
            )

    liquidation_bankroll = broker.state.cash_usd
    for pos in broker.state.positions:
        try:
            book = client.get_order_book(pos.token_id)
            exit_price, _slippage = executable_sell_price(book, pos.shares)
        except Exception as exc:  # noqa: BLE001
            return EntryBankrollSnapshot(
                False,
                cost_basis_bankroll,
                0.0,
                0.0,
                f"cannot price held token {pos.token_id}: {type(exc).__name__}",
            )
        if exit_price is None:
            return EntryBankrollSnapshot(
                False,
                cost_basis_bankroll,
                0.0,
                0.0,
                f"cannot price held token {pos.token_id}: insufficient executable bid depth",
            )
        exit_fee_usdc = polymarket_taker_fee_usdc(pos.shares, exit_price, broker.settings.weather_taker_fee_rate)
        liquidation_bankroll += pos.shares * exit_price - exit_fee_usdc

    entry_bankroll = min(cost_basis_bankroll, liquidation_bankroll)
    return EntryBankrollSnapshot(
        True,
        cost_basis_bankroll,
        liquidation_bankroll,
        entry_bankroll,
        "priced from cash and executable held-position liquidation value",
    )


def _candidate_city_and_date(candidate: PortfolioCandidate) -> tuple[str, str]:
    parsed = candidate.signal.parsed or parse_weather_question(candidate.market.question)
    return parsed.city or "", parsed.date_hint or ""


def _event_key(candidate: PortfolioCandidate, city: str, date_hint: str) -> str:
    return candidate.market.event_id or "|".join([city or "unknown-city", date_hint or "unknown-date"])


def _event_positions(broker: PaperBroker, city: str, date_hint: str) -> list[PaperPosition]:
    return [
        pos
        for pos in broker.state.positions
        if (
            str(pos.metadata.get("city", "")).lower() == city.lower()
            and str(pos.metadata.get("date_hint", "")).lower() == date_hint.lower()
        )
    ]


def _market_positions(broker: PaperBroker, market_id: str) -> list[PaperPosition]:
    return [pos for pos in broker.state.positions if pos.market_id == market_id]


def _same_side_position(positions: list[PaperPosition], side: str) -> PaperPosition | None:
    return next((pos for pos in positions if pos.side == side), None)


def _position_stop_threshold(pos: PaperPosition, candidate: PortfolioCandidate, settings: Settings) -> float:
    entry_p_true = float(pos.metadata.get("entry_p_true", candidate.result.p_true))
    entry_side_probability = float(
        pos.metadata.get("entry_side_probability", side_true_probability(pos.side, entry_p_true))
    )
    return float(
        pos.metadata.get(
            "probability_stop_threshold",
            max(0.0, entry_side_probability - settings.probability_stop_drop_threshold),
        )
    )


def _add_to_position_block_reason(
    candidate: PortfolioCandidate,
    pos: PaperPosition,
    settings: Settings,
) -> str | None:
    result = candidate.result
    if result.side != pos.side:
        return "same-market position already open on opposite side"
    if result.p_exec is None:
        return "not an executable add-on candidate"

    add_trigger_price = pos.entry_price * (1.0 - settings.add_to_position_drop_pct)
    if result.p_exec > add_trigger_price + 1e-12:
        return (
            f"add-on price has not fallen {settings.add_to_position_drop_pct:.0%}: "
            f"p_exec={result.p_exec:.4f} > trigger={add_trigger_price:.4f}"
        )

    current_side_probability = side_true_probability(result.side, result.p_true)
    stop_threshold = _position_stop_threshold(pos, candidate, settings)
    if current_side_probability <= stop_threshold:
        return (
            f"add-on blocked by probability stop: side_probability={current_side_probability:.3f} "
            f"<= threshold={stop_threshold:.3f}"
        )
    return None


def _temperature_interval(question: str) -> tuple[float, float] | None:
    parsed = parse_weather_question(question)
    return rounded_temperature_bucket_interval_f(parsed)


def _temperature_interval_bounds(question: str) -> TemperatureBucketInterval | None:
    parsed = parse_weather_question(question)
    return temperature_bucket_interval_bounds_f(parsed)


def _bucket_intervals_do_not_overlap(left: TemperatureBucketInterval, right: TemperatureBucketInterval) -> bool:
    epsilon = 1e-9
    if left.upper_f < right.lower_f - epsilon:
        return True
    if right.upper_f < left.lower_f - epsilon:
        return True
    if abs(left.upper_f - right.lower_f) <= epsilon:
        return not (left.upper_inclusive and right.lower_inclusive)
    if abs(right.upper_f - left.lower_f) <= epsilon:
        return not (right.upper_inclusive and left.lower_inclusive)
    return False


def _is_complementary(candidate: PortfolioCandidate, selected: list[PortfolioCandidate], held: list[PaperPosition]) -> bool:
    if candidate.market_type != "temperature":
        return False
    candidate_interval = _temperature_interval_bounds(candidate.market.question)
    if candidate_interval is None:
        return False

    for leg in selected:
        if leg.market_type != "temperature":
            return False
        leg_interval = _temperature_interval_bounds(leg.market.question)
        if leg_interval is None or not _bucket_intervals_do_not_overlap(candidate_interval, leg_interval):
            return False
    return is_complementary_with_positions(candidate.market.question, candidate.result.side, held)


def is_complementary_with_positions(question: str, side: str, held: list[PaperPosition]) -> bool:
    if not held:
        return True
    candidate_interval = _temperature_interval_bounds(question)
    if candidate_interval is None:
        return False
    for pos in held:
        held_interval = _temperature_interval_bounds(pos.question)
        if held_interval is None or not _bucket_intervals_do_not_overlap(candidate_interval, held_interval):
            return False
    return True


def _bucket_intervals_overlap(intervals: list[TemperatureBucketInterval]) -> bool:
    ordered = sorted(intervals, key=lambda interval: (interval.lower_f, interval.upper_f))
    return any(
        not _bucket_intervals_do_not_overlap(left, right)
        for left, right in zip(ordered, ordered[1:])
    )


def _bucket_intervals_cover_all_outcomes(intervals: list[TemperatureBucketInterval]) -> bool:
    if not intervals:
        return False
    ordered = sorted(intervals, key=lambda interval: (interval.lower_f, interval.upper_f))
    first = ordered[0]
    last = ordered[-1]
    if not (isinf(first.lower_f) and first.lower_f < 0):
        return False
    if not (isinf(last.upper_f) and last.upper_f > 0):
        return False

    for left, right in zip(ordered, ordered[1:]):
        if not _bucket_intervals_do_not_overlap(left, right):
            return False
        if abs(left.upper_f - right.lower_f) > _SCENARIO_PROBABILITY_EPSILON:
            return False
        if not (left.upper_inclusive or right.lower_inclusive):
            return False
    return True


def _scenario_probabilities(candidates: list[PortfolioCandidate]) -> _ScenarioProbabilityAssessment:
    unique: dict[str, PortfolioCandidate] = {}
    for candidate in candidates:
        unique.setdefault(candidate.market.market_id, candidate)
    if not unique:
        return _ScenarioProbabilityAssessment({"other": 1.0})

    bucket_probabilities = {
        market_id: clamp_probability(candidate.signal.p_true)
        for market_id, candidate in unique.items()
    }
    total = sum(bucket_probabilities.values())
    rounded_probabilities = {
        market_id: round(probability, 12)
        for market_id, probability in bucket_probabilities.items()
    }
    bucket_intervals = [
        interval
        for candidate in unique.values()
        if (interval := _temperature_interval_bounds(candidate.market.question)) is not None
    ]
    if _bucket_intervals_overlap(bucket_intervals):
        return _ScenarioProbabilityAssessment(
            rounded_probabilities,
            "scenario probabilities overlap",
        )

    if total <= 0:
        return _ScenarioProbabilityAssessment({"other": 1.0})

    exhaustive = (
        len(bucket_intervals) == len(unique)
        and _bucket_intervals_cover_all_outcomes(bucket_intervals)
    )
    if exhaustive:
        return _ScenarioProbabilityAssessment(
            {
                market_id: round(probability / total, 12)
                for market_id, probability in bucket_probabilities.items()
            }
        )

    if total > 1.0 + _SCENARIO_PROBABILITY_EPSILON:
        return _ScenarioProbabilityAssessment(
            rounded_probabilities,
            "scenario probabilities exceed one without exhaustive intervals",
        )

    probabilities = dict(rounded_probabilities)
    if total < 1.0 - _SCENARIO_PROBABILITY_EPSILON:
        probabilities["other"] = round(1.0 - total, 12)
    return _ScenarioProbabilityAssessment(probabilities)


def _leg_wins(side: str, market_id: str, outcome: str) -> bool:
    if side == "YES":
        return outcome == market_id
    return side == "NO" and outcome != market_id


def _scenario_pnl(
    selected: tuple[PortfolioCandidate, ...],
    held: list[PaperPosition],
    probabilities: dict[str, float],
    settings: Settings,
) -> dict[str, float]:
    selected_cost = sum(leg.result.size_usd for leg in selected)
    total_cost = selected_cost + sum(pos.cost_usd for pos in held)
    scenarios: dict[str, float] = {}
    for outcome in probabilities:
        payout = sum(
            leg.result.size_shares
            for leg in selected
            if _leg_wins(leg.result.side, leg.market.market_id, outcome)
        )
        payout += sum(
            pos.shares
            for pos in held
            if _leg_wins(pos.side, pos.market_id, outcome)
        )
        scenarios[outcome] = round(payout - total_cost, 6)
    return scenarios


def _dedupe_allocation_sizes(values: list[float], minimum_usd: float, limit_usd: float) -> list[float]:
    seen: set[float] = set()
    for value in values:
        rounded = round(float(value), _ALLOCATION_SIZE_ROUND_DIGITS)
        if rounded + 1e-9 < minimum_usd or rounded - 1e-9 > limit_usd:
            continue
        seen.add(rounded)
    return sorted(seen)


def _allocation_sizes(limit_usd: float, minimum_usd: float, preferred_usd: float | None = None) -> list[float]:
    if limit_usd + 1e-9 < minimum_usd:
        return []
    first_whole_dollar = ceil(minimum_usd)
    last_whole_dollar = floor(limit_usd)
    dense_count = max(0, last_whole_dollar - first_whole_dollar + 1)
    anchors = [minimum_usd, limit_usd]
    if preferred_usd is not None:
        anchors.append(preferred_usd)
    if dense_count <= _MAX_ALLOCATION_SIZE_CANDIDATES:
        dense_values = [float(value) for value in range(first_whole_dollar, last_whole_dollar + 1)]
        dense_candidates = _dedupe_allocation_sizes([*anchors, *dense_values], minimum_usd, limit_usd)
        if len(dense_candidates) <= _MAX_ALLOCATION_SIZE_CANDIDATES:
            return dense_candidates

    anchor_values = _dedupe_allocation_sizes(anchors, minimum_usd, limit_usd)
    interior_slots = max(0, _MAX_ALLOCATION_SIZE_CANDIDATES - len(anchor_values))
    interval = limit_usd - minimum_usd
    spaced_values = [
        minimum_usd + (interval * (index + 1) / (interior_slots + 1))
        for index in range(interior_slots)
    ]
    return _dedupe_allocation_sizes([*anchor_values, *spaced_values], minimum_usd, limit_usd)


def _resize_candidate(candidate: PortfolioCandidate, size_usd: float) -> PortfolioCandidate:
    result = candidate.result
    if result.p_exec is None or result.size_usd <= 0:
        return candidate
    scale = size_usd / result.size_usd
    return replace(
        candidate,
        result=replace(
            result,
            size_usd=size_usd,
            size_shares=result.size_shares * scale,
            expected_net_profit_usd=result.expected_net_profit_usd * scale,
        ),
    )


def _build_plan(
    selected: tuple[PortfolioCandidate, ...],
    held: list[PaperPosition],
    entry_bankroll: float,
    probabilities: dict[str, float],
    settings: Settings,
) -> _PortfolioPlan | None:
    expected_net_profit = sum(leg.result.expected_net_profit_usd for leg in selected)
    if expected_net_profit <= 0:
        return None
    scenario_pnl = _scenario_pnl(selected, held, probabilities, settings)
    expected_log_growth = 0.0
    for outcome, probability in probabilities.items():
        wealth_after = entry_bankroll + scenario_pnl[outcome]
        if wealth_after <= 0:
            return None
        expected_log_growth += probability * log(wealth_after / entry_bankroll)
    if expected_log_growth <= 0:
        return None
    return _PortfolioPlan(selected, expected_net_profit, expected_log_growth, scenario_pnl)


def select_event_portfolio(
    broker: PaperBroker,
    candidates: list[PortfolioCandidate],
    entry_bankroll: EntryBankrollSnapshot,
) -> EventPortfolioDecision:
    first = candidates[0] if candidates else None
    city, date_hint = _candidate_city_and_date(first) if first is not None else ("", "")
    settings = broker.settings
    event_cap_fraction = adaptive_event_cap_fraction(entry_bankroll.entry_bankroll, settings)
    event_cap_usd = entry_bankroll.entry_bankroll * event_cap_fraction
    existing_event_exposure = broker.event_date_exposure(city, date_hint) if city and date_hint else 0.0
    held = _event_positions(broker, city, date_hint)
    rejected: list[RejectedPortfolioLeg] = []
    probability_assessment = _scenario_probabilities(candidates)
    probabilities = probability_assessment.probabilities
    if probability_assessment.fail_closed_reason is not None:
        for candidate in candidates:
            rejected.append(
                RejectedPortfolioLeg(
                    candidate.market.market_id,
                    candidate.result.side,
                    probability_assessment.fail_closed_reason,
                )
            )
        return EventPortfolioDecision(
            event_key=_event_key(first, city, date_hint) if first is not None else "unknown-event",
            city=city,
            date_hint=date_hint,
            entry_bankroll=entry_bankroll,
            event_cap_fraction=event_cap_fraction,
            event_cap_usd=event_cap_usd,
            existing_event_exposure_usd=existing_event_exposure,
            selected=[],
            rejected=rejected,
            selected_exposure_usd=0.0,
            expected_net_profit_usd=0.0,
            expected_log_growth=0.0,
            scenario_probabilities=probabilities,
            scenario_pnl_usd=_scenario_pnl(tuple(), held, probabilities, settings),
        )
    eligible: list[PortfolioCandidate] = []
    for candidate in candidates:
        result = candidate.result
        side = result.side
        if side not in {"YES", "NO"} or result.p_exec is None or result.size_usd <= 0:
            rejected.append(RejectedPortfolioLeg(candidate.market.market_id, side, "not an executable entry candidate"))
            continue
        if not entry_bankroll.usable:
            rejected.append(RejectedPortfolioLeg(candidate.market.market_id, side, entry_bankroll.reason))
            continue
        if result.expected_net_profit_usd <= 0:
            rejected.append(RejectedPortfolioLeg(candidate.market.market_id, side, "portfolio EV does not improve after costs"))
            continue
        market_positions = _market_positions(broker, candidate.market.market_id)
        same_side_position = _same_side_position(market_positions, side)
        is_add_to_existing = False
        if market_positions:
            if same_side_position is None:
                rejected.append(RejectedPortfolioLeg(candidate.market.market_id, side, "same-market position already open on opposite side"))
                continue
            block_reason = _add_to_position_block_reason(candidate, same_side_position, settings)
            if block_reason is not None:
                rejected.append(RejectedPortfolioLeg(candidate.market.market_id, side, block_reason))
                continue
            candidate = replace(candidate, add_to_existing_position_id=same_side_position.position_id)
            is_add_to_existing = True
        if len(held) >= settings.max_event_portfolio_legs and not is_add_to_existing:
            rejected.append(RejectedPortfolioLeg(candidate.market.market_id, side, "event leg cap reached"))
            continue
        if held and not is_add_to_existing and not _is_complementary(candidate, [], held):
            rejected.append(RejectedPortfolioLeg(candidate.market.market_id, side, "event legs are not complementary"))
            continue
        eligible.append(candidate)

    available_budget = min(
        event_cap_usd - existing_event_exposure,
        entry_bankroll.entry_bankroll * settings.max_city_exposure_fraction - broker.city_exposure(city),
        entry_bankroll.entry_bankroll * settings.max_total_exposure_fraction - broker.total_exposure(),
        broker.state.cash_usd,
    )
    single_limit = min(
        available_budget,
        entry_bankroll.entry_bankroll * settings.max_single_market_fraction,
    )
    plans: list[_PortfolioPlan] = []
    remaining_slots = settings.max_event_portfolio_legs - len(held)
    if entry_bankroll.usable:
        for candidate in eligible:
            if candidate.add_to_existing_position_id is None and remaining_slots <= 0:
                continue
            for size_usd in _allocation_sizes(
                min(single_limit, candidate.result.size_usd),
                settings.min_order_usd,
                preferred_usd=candidate.result.size_usd,
            ):
                plan = _build_plan(
                    (_resize_candidate(candidate, size_usd),),
                    held,
                    entry_bankroll.entry_bankroll,
                    probabilities,
                    settings,
                )
                if plan is not None:
                    plans.append(plan)
    if entry_bankroll.usable and remaining_slots > 1:
        for left, right in combinations(eligible, 2):
            if left.market.market_id == right.market.market_id:
                continue
            if not _is_complementary(right, [left], held):
                continue
            left_sizes = _allocation_sizes(
                min(single_limit, left.result.size_usd),
                settings.min_order_usd,
                preferred_usd=left.result.size_usd,
            )
            right_sizes = _allocation_sizes(
                min(single_limit, right.result.size_usd),
                settings.min_order_usd,
                preferred_usd=right.result.size_usd,
            )
            for left_size in left_sizes:
                for right_size in right_sizes:
                    if left_size + right_size > available_budget + 1e-9:
                        continue
                    plan = _build_plan(
                        (
                            _resize_candidate(left, left_size),
                            _resize_candidate(right, right_size),
                        ),
                        held,
                        entry_bankroll.entry_bankroll,
                        probabilities,
                        settings,
                    )
                    if plan is not None:
                        plans.append(plan)

    best_plan = max(
        plans,
        key=lambda plan: (
            plan.expected_log_growth,
            plan.expected_net_profit_usd,
            -len(plan.selected),
        ),
        default=None,
    )
    selected = list(best_plan.selected) if best_plan is not None else []
    selected_keys = {
        (leg.market.market_id, leg.result.side)
        for leg in selected
    }
    for candidate in eligible:
        key = (candidate.market.market_id, candidate.result.side)
        if key not in selected_keys:
            rejected.append(RejectedPortfolioLeg(candidate.market.market_id, candidate.result.side, "not selected by event portfolio optimizer"))
    selected_exposure = sum(leg.result.size_usd for leg in selected)
    return EventPortfolioDecision(
        event_key=_event_key(first, city, date_hint) if first is not None else "unknown-event",
        city=city,
        date_hint=date_hint,
        entry_bankroll=entry_bankroll,
        event_cap_fraction=event_cap_fraction,
        event_cap_usd=event_cap_usd,
        existing_event_exposure_usd=existing_event_exposure,
        selected=selected,
        rejected=rejected,
        selected_exposure_usd=selected_exposure,
        expected_net_profit_usd=best_plan.expected_net_profit_usd if best_plan is not None else 0.0,
        expected_log_growth=best_plan.expected_log_growth if best_plan is not None else 0.0,
        scenario_probabilities=probabilities,
        scenario_pnl_usd=best_plan.scenario_pnl_usd if best_plan is not None else _scenario_pnl(tuple(), held, probabilities, settings),
    )
