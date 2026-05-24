from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .edge import clamp_probability
from .models import EdgeResult, PaperPosition
from .config import Settings


@dataclass(frozen=True)
class EntryPlan:
    bankroll_before: float
    entry_fraction: float
    entry_usd: float
    stop_loss_price: float
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


def side_true_probability(side: Literal["YES", "NO"] | str, p_true_yes: float) -> float:
    p_yes = clamp_probability(p_true_yes)
    return p_yes if side == "YES" else 1.0 - p_yes


def model_fair_price(side: Literal["YES", "NO"] | str, p_true_yes: float, settings: Settings) -> float:
    """Conservative model fair price for the token side.

    For YES, fair is P(event). For NO, fair is 1-P(event). We subtract model,
    resolution, and fee buffers so profit targets are not based on the raw model.
    """
    raw = side_true_probability(side, p_true_yes)
    fair = raw - settings.estimated_fee_per_share - settings.model_error_margin - settings.resolution_error_margin
    return max(0.01, min(0.99, fair))


def target_exit_price(entry_price: float, fair_price: float, settings: Settings) -> float:
    """Dynamic take-profit target based on model fair value, not fixed 5%/10%.

    If model fair is above entry, we target a configurable fraction of the gap.
    Example: entry 0.42, fair 0.60, ratio 0.70 -> target 0.546.
    """
    if fair_price <= entry_price:
        return entry_price
    target = entry_price + settings.take_profit_to_fair_ratio * (fair_price - entry_price)
    return max(0.01, min(0.99, target))


def market_heat_score(mark_price: float, fair_price: float) -> float:
    """Positive means market is expensive versus our model; negative means cheap.

    0.00 = near fair, +0.10 = roughly 10% overheated vs fair, -0.10 = cheap.
    """
    if fair_price <= 0:
        return 0.0
    return (mark_price - fair_price) / fair_price


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
    stop = max(0.01, result.p_exec * (1.0 - settings.stop_loss_pct))
    rationale = (
        f"entry: model_p={result.p_true:.3f}, side={result.side}, p_exec={result.p_exec:.4f}, "
        f"net_edge={result.net_edge:.4f}, bankroll=${bankroll_before:.2f}, "
        f"entry_fraction={fraction:.2%}, stop={stop:.4f}, model_fair={fair:.4f}, "
        f"target_exit={target:.4f}, heat={heat:.2%}"
    )
    return EntryPlan(
        bankroll_before=bankroll_before,
        entry_fraction=fraction,
        entry_usd=result.size_usd,
        stop_loss_price=stop,
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
    pnl_pct = (mark_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0.0

    stop_price = float(pos.metadata.get("stop_loss_price", pos.entry_price * (1.0 - settings.stop_loss_pct)))

    # ── 펀더멘탈 손절: p_true 급락 시 가격 손절보다 먼저 청산 ─────────────────
    # 날씨 마켓은 호가가 얇아 가격만 보면 whipsaw로 오발동이 잦다.
    # 모델 확률 자체가 크게 하락했을 때 먼저 나오는 것이 더 신뢰성이 높다.
    entry_p_true = float(pos.metadata.get("entry_p_true", p_true))
    p_true_drop = entry_p_true - p_true  # 양수이면 p_true가 하락한 것
    if p_true_drop >= settings.p_true_drop_threshold:
        return ExitAssessment(
            True,
            f"fundamental stop: p_true {entry_p_true:.3f}→{p_true:.3f} "
            f"(drop={p_true_drop:.3f} >= threshold={settings.p_true_drop_threshold:.3f})",
            fair, target, heat,
        )

    if mark_price <= stop_price:
        return ExitAssessment(True, f"stop loss: mark {mark_price:.4f} <= stop {stop_price:.4f} ({pnl_pct:.1%})", fair, target, heat)

    # Exit if the market has caught up to the model's conservative fair-value target.
    if mark_price >= target and pnl_pct >= settings.min_profit_pct:
        return ExitAssessment(True, f"take profit: market reached model target {target:.4f} ({pnl_pct:.1%})", fair, target, heat)

    # Exit faster if the market becomes expensive relative to our model.
    if mark_price >= fair + settings.overheat_margin and pnl_pct > 0:
        return ExitAssessment(True, f"take profit: overheated vs model fair {fair:.4f}, heat={heat:.1%}", fair, target, heat)

    # If edge disappears, exit only if the trade is not meaningfully underwater; stop handles that case.
    if latest_edge is not None and latest_edge.net_edge <= settings.exit_net_edge and pnl_pct >= -settings.edge_fade_max_loss_pct:
        return ExitAssessment(True, f"edge faded: latest_edge={latest_edge.net_edge:.4f}, pnl={pnl_pct:.1%}", fair, target, heat)

    if holding_hours >= settings.max_holding_hours:
        return ExitAssessment(True, f"max holding hours {holding_hours:.1f}", fair, target, heat)

    return ExitAssessment(False, f"hold: mark={mark_price:.4f}, target={target:.4f}, fair={fair:.4f}, heat={heat:.1%}, pnl={pnl_pct:.1%}", fair, target, heat)
