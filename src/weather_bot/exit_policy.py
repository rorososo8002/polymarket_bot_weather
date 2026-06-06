from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import Settings
from .edge import clamp_probability, polymarket_taker_fee_per_share, polymarket_taker_fee_usdc
from .models import EdgeResult, PaperPosition


@dataclass(frozen=True)
class EntryPlan:
    bankroll_before: float
    entry_fraction: float
    entry_usd: float
    probability_stop_threshold: float
    model_fair_price: float
    target_exit_price: float
    market_heat_score: float
    rationale: str


@dataclass(frozen=True)
class ExitAssessment:
    should_close: bool
    reason: str
    model_fair_price: float
    target_exit_price: float
    market_heat_score: float
    trigger: str = "hold"


@dataclass(frozen=True)
class _LiquidationPnl:
    net_usd: float
    net_pct: float
    raw_pct: float
    exit_fee_usdc: float


def side_true_probability(side: Literal["YES", "NO"] | str, p_true_yes: float) -> float:
    p_yes = clamp_probability(p_true_yes)
    return p_yes if side == "YES" else 1.0 - p_yes


def model_fair_price(side: Literal["YES", "NO"] | str, p_true_yes: float, settings: Settings) -> float:
    """Conservative model fair price for the token side."""
    settlement_value = conservative_settlement_value(side, p_true_yes, settings)
    fair = settlement_value - polymarket_taker_fee_per_share(
        settlement_value,
        settings.weather_taker_fee_rate,
    )
    return max(0.01, min(0.99, fair))


def conservative_settlement_value(side: Literal["YES", "NO"] | str, p_true_yes: float, settings: Settings) -> float:
    """Return conservative expected settlement payout without order-book exit costs."""
    raw = side_true_probability(side, p_true_yes)
    value = raw - settings.model_error_margin - settings.resolution_error_margin
    return max(0.0, min(1.0, value))


def target_exit_price(entry_price: float, fair_price: float, settings: Settings) -> float:
    """Dynamic take-profit target based on model fair value."""
    if fair_price <= entry_price:
        return entry_price
    target = entry_price + settings.take_profit_to_fair_ratio * (fair_price - entry_price)
    return max(0.01, min(0.99, target))


def market_heat_score(mark_price: float, fair_price: float) -> float:
    """Positive means market is expensive versus our model; negative means cheap."""
    if fair_price <= 0:
        return 0.0
    return (mark_price - fair_price) / fair_price


def probability_stop_threshold(side: Literal["YES", "NO"] | str, p_true_yes: float, settings: Settings) -> float:
    side_probability = side_true_probability(side, p_true_yes)
    return max(0.0, side_probability - settings.probability_stop_drop_threshold)


def _liquidation_pnl(pos: PaperPosition, mark_price: float, settings: Settings) -> _LiquidationPnl | None:
    if pos.cost_usd <= 0 or pos.shares <= 0:
        return None
    raw_pct = (mark_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0.0
    exit_fee_usdc = polymarket_taker_fee_usdc(pos.shares, mark_price, settings.weather_taker_fee_rate)
    net_usd = pos.shares * mark_price - exit_fee_usdc - pos.cost_usd
    return _LiquidationPnl(
        net_usd=net_usd,
        net_pct=net_usd / pos.cost_usd,
        raw_pct=raw_pct,
        exit_fee_usdc=exit_fee_usdc,
    )


def _pnl_reason(pnl: _LiquidationPnl) -> str:
    return f"net_pnl={pnl.net_pct:.1%}, raw_pnl={pnl.raw_pct:.1%}, exit_fee=${pnl.exit_fee_usdc:.5f}"


def build_entry_plan(
    result: EdgeResult,
    bankroll_before: float,
    settings: Settings,
) -> EntryPlan:
    if result.side not in {"YES", "NO"} or result.p_exec is None:
        raise ValueError("entry plan requires YES/NO result with p_exec")
    fair = model_fair_price(result.side, result.p_true, settings)
    target = target_exit_price(result.p_exec, fair, settings)
    heat = market_heat_score(result.p_exec, fair)
    fraction = result.size_usd / bankroll_before if bankroll_before > 0 else 0.0
    stop_threshold = probability_stop_threshold(result.side, result.p_true, settings)
    rationale = (
        f"entry: model_p={result.p_true:.3f}, side={result.side}, p_exec={result.p_exec:.4f}, "
        f"net_edge={result.net_edge:.4f}, bankroll=${bankroll_before:.2f}, "
        f"entry_fraction={fraction:.2%}, probability_stop={stop_threshold:.3f}, "
        f"model_fair={fair:.4f}, target_exit={target:.4f}, heat={heat:.2%}"
    )
    return EntryPlan(
        bankroll_before=bankroll_before,
        entry_fraction=fraction,
        entry_usd=result.size_usd,
        probability_stop_threshold=stop_threshold,
        model_fair_price=fair,
        target_exit_price=target,
        market_heat_score=heat,
        rationale=rationale,
    )


def assess_exit(
    pos: PaperPosition,
    mark_price: float,
    latest_edge: EdgeResult | None,
    settings: Settings,
    holding_hours: float,
) -> ExitAssessment:
    p_true = latest_edge.p_true if latest_edge is not None else float(pos.metadata.get("entry_p_true", 0.5))
    fair = model_fair_price(pos.side, p_true, settings)
    target = target_exit_price(pos.entry_price, fair, settings)
    heat = market_heat_score(mark_price, fair)
    pnl = _liquidation_pnl(pos, mark_price, settings)
    if pnl is None:
        return ExitAssessment(
            False,
            f"hold: invalid position cost or shares for liquidation PnL, cost=${pos.cost_usd:.5f}, shares={pos.shares:.5f}",
            fair,
            target,
            heat,
            "invalid_position_cost",
        )

    entry_p_true = float(pos.metadata.get("entry_p_true", p_true))
    entry_side_probability = float(
        pos.metadata.get("entry_side_probability", side_true_probability(pos.side, entry_p_true))
    )
    current_side_probability = side_true_probability(pos.side, p_true)
    stop_threshold = float(
        pos.metadata.get(
            "probability_stop_threshold",
            max(0.0, entry_side_probability - settings.probability_stop_drop_threshold),
        )
    )
    probability_drop = entry_side_probability - current_side_probability
    if latest_edge is not None and latest_edge.exit_signal == "nowcast_bucket_lock_risk":
        reason = latest_edge.exit_signal_reason or latest_edge.reason
        return ExitAssessment(
            True,
            reason,
            fair,
            target,
            heat,
            "nowcast_bucket_lock_risk",
        )

    if current_side_probability <= stop_threshold:
        return ExitAssessment(
            True,
            f"probability stop: side_probability {entry_side_probability:.3f}->{current_side_probability:.3f} "
            f"<= threshold={stop_threshold:.3f} (drop={probability_drop:.3f})",
            fair,
            target,
            heat,
            "probability_stop",
        )

    if mark_price >= target and pnl.net_pct >= settings.min_profit_pct:
        return ExitAssessment(
            True,
            f"take profit: market reached model target {target:.4f} ({_pnl_reason(pnl)})",
            fair,
            target,
            heat,
            "take_profit",
        )

    if mark_price >= fair + settings.overheat_margin and pnl.net_pct > 0:
        return ExitAssessment(
            True,
            f"take profit: overheated vs model fair {fair:.4f}, heat={heat:.1%}, {_pnl_reason(pnl)}",
            fair,
            target,
            heat,
            "overheated_take_profit",
        )

    if (
        latest_edge is not None
        and latest_edge.p_exec is not None
        and latest_edge.net_edge <= settings.exit_net_edge
        and pnl.net_pct >= -settings.edge_fade_max_loss_pct
    ):
        return ExitAssessment(
            True,
            f"edge faded: latest_edge={latest_edge.net_edge:.4f}, {_pnl_reason(pnl)}",
            fair,
            target,
            heat,
            "edge_faded",
        )

    if holding_hours >= settings.max_holding_hours:
        return ExitAssessment(True, f"max holding hours {holding_hours:.1f}", fair, target, heat, "max_holding")

    return ExitAssessment(
        False,
        f"hold: mark={mark_price:.4f}, target={target:.4f}, fair={fair:.4f}, heat={heat:.1%}, {_pnl_reason(pnl)}",
        fair,
        target,
        heat,
    )
