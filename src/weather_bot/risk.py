from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .edge import clamp_probability

REALIZED_PNL_ACTIONS = {"CLOSE", "PARTIAL_CLOSE", "SETTLED"}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(value: Any) -> datetime | None:
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


def _realized_trade_rows(path: str) -> list[dict[str, Any]]:
    csv_path = Path(path)
    if not csv_path.exists() or csv_path.stat().st_size <= 0:
        return []
    try:
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            return [
                row
                for row in csv.DictReader(f)
                if str(row.get("action") or "").upper() in REALIZED_PNL_ACTIONS
            ]
    except (OSError, csv.Error):
        return []


def _position_unrealized_pnl(position: Any) -> float:
    if isinstance(position, dict):
        return _float(position.get("last_unrealized_pnl"))
    return _float(getattr(position, "last_unrealized_pnl", 0.0))


def drawdown_entry_block_reason(
    settings: Any,
    open_positions: Iterable[Any],
    *,
    now: datetime | None = None,
    city: str = "",
    date_hint: str = "",
) -> str:
    """Return a reason when drawdown rules should block new entries only."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows = _realized_trade_rows(str(getattr(settings, "trades_csv_path", "")))
    bankroll = max(0.0, _float(getattr(settings, "bankroll_usd", 0.0)))
    today = current.date()
    today_pnl = 0.0
    consecutive_losses = 0
    large_loss_cutoff_hours = max(0.0, _float(getattr(settings, "large_loss_cooldown_hours", 0.0)))
    large_loss_threshold = bankroll * max(0.0, _float(getattr(settings, "large_loss_threshold_fraction", 0.0)))
    city_cooldown_hours = max(0.0, _float(getattr(settings, "city_loss_cooldown_hours", 0.0)))
    normalized_city = city.strip().lower()
    normalized_date = date_hint.strip().lower()

    for row in rows:
        ts = _parse_ts(row.get("ts"))
        pnl = _float(row.get("cash_delta_or_pnl"))
        if ts is not None and ts.date() == today:
            today_pnl += pnl

    for row in reversed(rows):
        pnl = _float(row.get("cash_delta_or_pnl"))
        if pnl < 0:
            consecutive_losses += 1
        elif pnl > 0:
            break

    realized_loss = max(0.0, -today_pnl)
    realized_limit = bankroll * max(0.0, _float(getattr(settings, "daily_realized_loss_limit_fraction", 0.0)))
    if realized_limit > 0 and realized_loss >= realized_limit:
        return f"SKIP_DRAWDOWN: DAILY_LOSS_LIMIT_HIT realized_loss=${realized_loss:.2f} limit=${realized_limit:.2f}"

    unrealized_loss = max(0.0, -sum(_position_unrealized_pnl(pos) for pos in open_positions))
    unrealized_limit = bankroll * max(0.0, _float(getattr(settings, "daily_unrealized_loss_limit_fraction", 0.0)))
    if unrealized_limit > 0 and unrealized_loss >= unrealized_limit:
        return (
            f"SKIP_DRAWDOWN: UNREALIZED_LOSS_LIMIT_HIT "
            f"unrealized_loss=${unrealized_loss:.2f} limit=${unrealized_limit:.2f}"
        )

    max_consecutive = int(_float(getattr(settings, "max_consecutive_losses", 0)))
    if max_consecutive > 0 and consecutive_losses >= max_consecutive:
        return f"SKIP_DRAWDOWN: MAX_CONSECUTIVE_LOSSES_HIT losses={consecutive_losses} limit={max_consecutive}"

    for row in reversed(rows):
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            continue
        age_hours = (current - ts).total_seconds() / 3600.0
        pnl = _float(row.get("cash_delta_or_pnl"))
        if large_loss_cutoff_hours > 0 and large_loss_threshold > 0 and pnl <= -large_loss_threshold and age_hours <= large_loss_cutoff_hours:
            return (
                f"SKIP_DRAWDOWN: LARGE_LOSS_COOLDOWN "
                f"loss=${abs(pnl):.2f} threshold=${large_loss_threshold:.2f}"
            )
        if not normalized_city or city_cooldown_hours <= 0 or pnl >= 0 or age_hours > city_cooldown_hours:
            continue
        row_city = str(row.get("city") or "").strip().lower()
        row_date = str(row.get("event_date_local") or row.get("date_hint") or "").strip().lower()
        city_matches = bool(row_city) and row_city == normalized_city
        date_matches = not normalized_date or not row_date or row_date == normalized_date
        if city_matches and date_matches:
            return f"SKIP_DRAWDOWN: CITY_LOSS_COOLDOWN city={city} date={date_hint}"

    return ""


def shrink_probability(p_true: float, gamma: float = 0.65) -> float:
    """Shrink model probability toward 0.5 to reduce overconfidence."""
    p = clamp_probability(p_true)
    if not 0 <= gamma <= 1:
        raise ValueError("gamma must be between 0 and 1")
    return 0.5 + gamma * (p - 0.5)


def confidence_size_multiplier(confidence: float, min_confidence: float = 0.50, floor: float = 0.25) -> float:
    """Return an entry-size multiplier from signal confidence.

    At the minimum tradable confidence, use `floor`; at confidence 1.0, use full
    size. Below the minimum, return zero so sizing fails closed.
    """
    c = clamp_probability(confidence)
    min_c = clamp_probability(min_confidence)
    floor_c = clamp_probability(floor)
    if c + 1e-12 < min_c:
        return 0.0
    if min_c >= 1.0:
        return 1.0 if c >= 1.0 else 0.0
    progress = (c - min_c) / (1.0 - min_c)
    return floor_c + (1.0 - floor_c) * progress


def fractional_kelly_binary(
    p_true: float,
    p_eff: float,
    fractional_kelly: float,
    max_fraction: float,
    gamma: float = 0.65,
) -> float:
    """Fractional Kelly for binary YES-like share.

    Full Kelly for a share bought at p_eff with win probability p is:
        f* = (p - p_eff) / (1 - p_eff)

    Returns bankroll fraction, clipped to [0, max_fraction].
    """
    if not 0 < p_eff < 1:
        return 0.0
    if fractional_kelly <= 0:
        return 0.0
    if max_fraction <= 0:
        return 0.0
    p_adj = shrink_probability(p_true, gamma=gamma)
    f_raw = (p_adj - p_eff) / (1.0 - p_eff)
    f = fractional_kelly * f_raw
    return max(0.0, min(max_fraction, f))
