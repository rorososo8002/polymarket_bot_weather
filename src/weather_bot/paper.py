from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .config import Settings
from .models import EdgeResult, PaperPosition, PaperState, RawMarket
from .edge import (
    executable_sell_price,
    fee_adjusted_entry_shares,
    max_absorbable_shares,
    polymarket_taker_fee_per_share,
    polymarket_taker_fee_usdc,
)
from .exit_policy import assess_exit, build_entry_plan, conservative_settlement_value, side_true_probability
from .polymarket_client import PolymarketClient
from .portfolio import adaptive_event_cap_fraction, is_complementary_with_positions, websocket_pricing_block_reason


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


@dataclass(frozen=True)
class SettlementRunnerDecision:
    keep_runner: bool
    shares_to_close: float
    runner_shares: float
    max_runner_shares: float
    principal_recovery_shares: float
    net_sell_price: float
    sell_now_net_usdc: float
    settlement_ev_usdc: float
    reason: str


PROFIT_RUNNER_TRIGGERS = {"take_profit", "overheated_take_profit"}


class PaperStateLoadError(RuntimeError):
    """Raised when paper accounting state is unsafe to trade from."""


def _required_number(raw: dict[str, Any], field: str, index: int) -> float:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"positions[{index}].{field} must be a number")
    value = float(value)
    if not isfinite(value):
        raise ValueError(f"positions[{index}].{field} must be finite")
    return value


def _required_nonempty_string(raw: dict[str, Any], field: str, index: int) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"positions[{index}].{field} must be a non-empty string")
    return value


def _position_from_state(raw: dict[str, Any], index: int) -> PaperPosition:
    item = dict(raw)

    _required_nonempty_string(item, "market_id", index)
    _required_nonempty_string(item, "token_id", index)

    side = item.get("side")
    if not isinstance(side, str) or side not in {"YES", "NO"}:
        raise ValueError(f"positions[{index}].side must be YES or NO")

    shares = _required_number(item, "shares", index)
    if shares <= 0:
        raise ValueError(f"positions[{index}].shares must be positive")
    item["shares"] = shares

    entry_price = _required_number(item, "entry_price", index)
    if not 0.0 <= entry_price <= 1.0:
        raise ValueError(f"positions[{index}].entry_price must be between 0 and 1")
    item["entry_price"] = entry_price

    cost_usd = _required_number(item, "cost_usd", index)
    if cost_usd < 0:
        raise ValueError(f"positions[{index}].cost_usd must be non-negative")
    item["cost_usd"] = cost_usd

    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError(f"positions[{index}].metadata must be a JSON object")
    item["metadata"] = metadata

    return PaperPosition(**item)


class PaperBroker:
    """Small local paper broker.

    Buys at live ask/VWAP from CLOB data and marks positions to the current bid.
    It never sends orders to Polymarket.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state_path = Path(settings.state_path)
        self.trades_csv_path = Path(settings.trades_csv_path)
        self.decisions_csv_path = Path(settings.decisions_csv_path)
        self.portfolio_decisions_jsonl_path = Path(settings.portfolio_decisions_jsonl_path)
        self.raw_snapshots_path = Path(settings.raw_snapshots_path)
        self.state = self.load_state()

    def load_state(self) -> PaperState:
        if not self.state_path.exists():
            return PaperState(cash_usd=self.settings.bankroll_usd)
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PaperStateLoadError(
                f"Invalid paper state at {self.state_path}: JSON is corrupt; refusing to start paper trading"
            ) from exc
        except OSError as exc:
            raise PaperStateLoadError(
                f"Invalid paper state at {self.state_path}: cannot read state file; refusing to start paper trading"
            ) from exc
        if not isinstance(raw, dict):
            raise PaperStateLoadError(
                f"Invalid paper state at {self.state_path}: root must be a JSON object; refusing to start paper trading"
            )
        try:
            cash_usd = float(raw["cash_usd"])
            realized_pnl_usd = float(raw.get("realized_pnl_usd", 0.0))
            if not isfinite(cash_usd) or not isfinite(realized_pnl_usd):
                raise ValueError("cash and realized PnL must be finite")

            raw_positions = raw.get("positions", [])
            if not isinstance(raw_positions, list):
                raise ValueError("positions must be a list")
            positions: list[PaperPosition] = []
            for index, item in enumerate(raw_positions):
                if not isinstance(item, dict):
                    raise ValueError("each position must be a JSON object")
                positions.append(_position_from_state(item, index))

            # Older state files may not have stats, but if present it must be readable.
            raw_stats = raw.get("stats", {})
            if not isinstance(raw_stats, dict):
                raise ValueError("stats must be a JSON object")
            stats: dict[str, Any] = {}
            for mt, st in raw_stats.items():
                if not isinstance(st, dict):
                    raise ValueError("each stats entry must be a JSON object")
                stats[str(mt)] = {
                    "wins": int(st.get("wins", 0)),
                    "losses": int(st.get("losses", 0)),
                    "pnl": float(st.get("pnl", 0.0)),
                }
        except (KeyError, TypeError, ValueError) as exc:
            raise PaperStateLoadError(
                f"Invalid paper state at {self.state_path}: {exc}; refusing to start paper trading"
            ) from exc
        return PaperState(
            cash_usd=cash_usd,
            realized_pnl_usd=realized_pnl_usd,
            positions=positions,
            stats=stats,
        )

    def save_state(self) -> None:
        payload = {
            "cash_usd": self.state.cash_usd,
            "realized_pnl_usd": self.state.realized_pnl_usd,
            "positions": [asdict(p) for p in self.state.positions],
            "stats": self.state.stats,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_name(f"{self.state_path.name}.{uuid4().hex}.tmp")
        try:
            tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, self.state_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def total_exposure(self) -> float:
        return sum(p.cost_usd for p in self.state.positions)

    def current_bankroll_before_entry(self) -> float:
        """Cash plus open position cost basis.

        Do not add realized_pnl_usd here because realized PnL is already reflected
        in cash after positions close. This is the denominator for the 5% entry rule.
        """
        return self.state.cash_usd + self.total_exposure()

    def has_position(self, market_id: str, side: str) -> bool:
        return any(p.market_id == market_id and p.side == side for p in self.state.positions)

    def has_any_position(self, market_id: str) -> bool:
        """Return True when any position exists for the market, regardless of side.

        This blocks same-market YES/NO hedging. Holding both sides of one binary
        market only burns fees, so a signal for the opposite side is ignored
        when either side is already open.
        """
        return any(p.market_id == market_id for p in self.state.positions)

    def city_exposure(self, city: str) -> float:
        """Return total current position cost for one city.

        Multiple derived markets for one city, such as 70F, 72F, and 75F
        temperature buckets, can overexpose the account to one weather event.
        This value lets the broker enforce the city cap.
        """
        city_lower = city.lower()
        return sum(
            p.cost_usd
            for p in self.state.positions
            if str(p.metadata.get("city", "")).lower() == city_lower
        )

    def event_date_exposure(self, city: str, date_hint: str) -> float:
        """Return total current position cost for one city-date pair.

        Example: NYC + 'jun 15' with both 70F and 72F positions is double
        exposure to the same day's weather outcome.
        """
        city_lower = city.lower()
        date_lower = date_hint.lower()
        return sum(
            p.cost_usd
            for p in self.state.positions
            if (
                str(p.metadata.get("city", "")).lower() == city_lower
                and str(p.metadata.get("date_hint", "")).lower() == date_lower
            )
        )

    def event_date_position_count(self, city: str, date_hint: str) -> int:
        return len(self.event_date_positions(city, date_hint))

    def event_date_positions(self, city: str, date_hint: str) -> list[PaperPosition]:
        city_lower = city.lower()
        date_lower = date_hint.lower()
        return [
            p
            for p in self.state.positions
            if (
                str(p.metadata.get("city", "")).lower() == city_lower
                and str(p.metadata.get("date_hint", "")).lower() == date_lower
            )
        ]

    def open_position(
        self,
        market: RawMarket,
        token_id: str,
        result: EdgeResult,
        market_type: str = "temperature",
        city: str = "",
        date_hint: str = "",
        entry_bankroll_usd: float | None = None,
    ) -> PaperPosition | None:
        if result.side not in {"YES", "NO"} or result.p_exec is None or result.size_usd <= 0:
            return None
        if self.has_any_position(market.market_id):
            self.log_trade("SKIP_SAME_MARKET", market, result.side, token_id, 0, result.p_exec, 0, "same-market position already open")
            return None
        bankroll_before = self.current_bankroll_before_entry()
        risk_bankroll = min(bankroll_before, entry_bankroll_usd) if entry_bankroll_usd is not None else bankroll_before
        allowed_exposure = risk_bankroll * self.settings.max_total_exposure_fraction
        if self.total_exposure() + result.size_usd > allowed_exposure:
            self.log_trade("SKIP_EXPOSURE_CAP", market, result.side, token_id, 0, result.p_exec, 0, "total exposure cap")
            return None

        # City exposure cap.
        if city:
            city_exp = self.city_exposure(city)
            city_limit = risk_bankroll * self.settings.max_city_exposure_fraction
            if city_exp + result.size_usd > city_limit:
                reason = (
                    f"SKIP_CITY_CAP: {city} exposure={city_exp:.2f}+{result.size_usd:.2f} "
                    f"> limit={city_limit:.2f} ({self.settings.max_city_exposure_fraction:.0%} bankroll)"
                )
                self.log_trade("SKIP_CITY_CAP", market, result.side, token_id, 0, result.p_exec, 0, reason)
                return None

        # City-date exposure cap.
        if city and date_hint:
            event_positions = self.event_date_positions(city, date_hint)
            event_leg_count = len(event_positions)
            if event_leg_count >= self.settings.max_event_portfolio_legs:
                reason = (
                    f"SKIP_EVENT_DATE_LEG_CAP: {city}/{date_hint} legs={event_leg_count} "
                    f">= limit={self.settings.max_event_portfolio_legs}"
                )
                self.log_trade("SKIP_EVENT_DATE_LEG_CAP", market, result.side, token_id, 0, result.p_exec, 0, reason)
                return None
            if not is_complementary_with_positions(market.question, result.side, event_positions):
                reason = f"SKIP_EVENT_DATE_CONCENTRATION: {city}/{date_hint} new leg is not complementary"
                self.log_trade("SKIP_EVENT_DATE_CONCENTRATION", market, result.side, token_id, 0, result.p_exec, 0, reason)
                return None
            event_exp = self.event_date_exposure(city, date_hint)
            event_fraction = adaptive_event_cap_fraction(risk_bankroll, self.settings)
            event_limit = risk_bankroll * event_fraction
            if event_exp + result.size_usd > event_limit:
                reason = (
                    f"SKIP_EVENT_DATE_CAP: {city}/{date_hint} exposure={event_exp:.2f}+{result.size_usd:.2f} "
                    f"> limit={event_limit:.2f} ({event_fraction:.0%} bankroll)"
                )
                self.log_trade("SKIP_EVENT_DATE_CAP", market, result.side, token_id, 0, result.p_exec, 0, reason)
                return None

        spend = min(result.size_usd, self.state.cash_usd)
        if spend < self.settings.min_order_usd:
            self.log_trade("SKIP_CASH", market, result.side, token_id, 0, result.p_exec, 0, "not enough cash")
            return None
        expected_profit = result.expected_net_profit_usd * spend / result.size_usd
        shares = fee_adjusted_entry_shares(spend, result.p_exec, self.settings.weather_taker_fee_rate)
        entry_fee_usdc = polymarket_taker_fee_usdc(shares, result.p_exec, self.settings.weather_taker_fee_rate)
        adjusted_result = EdgeResult(
            result.side,
            result.p_true,
            result.p_exec,
            result.net_edge,
            spend,
            shares,
            result.reason,
            expected_profit,
        )
        entry_plan = build_entry_plan(adjusted_result, risk_bankroll, self.settings)
        pos = PaperPosition(
            position_id=str(uuid4()),
            market_id=market.market_id,
            question=market.question,
            token_id=token_id,
            side=result.side,  # type: ignore[arg-type]
            entry_price=result.p_exec,
            shares=shares,
            cost_usd=spend,
            opened_at=utc_now_iso(),
            last_mark_price=result.p_exec,
            metadata={
                "entry_edge": result.net_edge,
                "entry_p_true": result.p_true,
                "entry_side_probability": side_true_probability(result.side, result.p_true),
                "bankroll_before": entry_plan.bankroll_before,
                "entry_bankroll_usd": risk_bankroll,
                "entry_fraction": entry_plan.entry_fraction,
                "probability_stop_threshold": entry_plan.probability_stop_threshold,
                "model_fair_price": entry_plan.model_fair_price,
                "target_exit_price": entry_plan.target_exit_price,
                "market_heat_score": entry_plan.market_heat_score,
                "entry_rationale": entry_plan.rationale,
                "entry_notional_usdc": round(shares * result.p_exec, 6),
                "entry_fee_usdc": entry_fee_usdc,
                "reason": result.reason,
                "slug": market.slug,
                "event_slug": market.event_slug,
                "market_type": market_type,
                "city": city,           # Tracks city-level exposure caps.
                "date_hint": date_hint, # Tracks city-date exposure caps.
            },
        )
        self.state.cash_usd -= spend
        self.state.positions.append(pos)
        self.save_state()
        open_reason = f"{entry_plan.rationale}; entry_fee=${entry_fee_usdc:.5f}"
        self.log_trade("OPEN", market, result.side, token_id, shares, result.p_exec, -spend, open_reason, market_type)
        return pos

    def close_position(self, pos: PaperPosition, market: RawMarket | None, exit_price: float, reason: str) -> float:
        gross_proceeds = pos.shares * exit_price
        exit_fee_usdc = polymarket_taker_fee_usdc(pos.shares, exit_price, self.settings.weather_taker_fee_rate)
        proceeds = gross_proceeds - exit_fee_usdc
        pnl = proceeds - pos.cost_usd
        self.state.cash_usd += proceeds
        self.state.realized_pnl_usd += pnl
        # Track win rate by market type.
        market_type = str(pos.metadata.get("market_type", "temperature"))
        st = self.state.stats.setdefault(market_type, {"wins": 0, "losses": 0, "pnl": 0.0})
        if pnl > 0:
            st["wins"] += 1
        else:
            st["losses"] += 1
        st["pnl"] = round(st["pnl"] + pnl, 6)
        self.state.positions = [p for p in self.state.positions if p.position_id != pos.position_id]
        self.save_state()
        dummy = market or RawMarket(pos.market_id, pos.question, None, True, False)
        close_reason = f"{reason}; exit_fee=${exit_fee_usdc:.5f} gross=${gross_proceeds:.5f} net=${proceeds:.5f}"
        self.log_trade("CLOSE", dummy, pos.side, pos.token_id, pos.shares, exit_price, pnl, close_reason, market_type)
        return pnl

    def partial_close_position(
        self,
        pos: PaperPosition,
        shares_to_close: float,
        exit_price: float,
        reason: str,
    ) -> float:
        """Close only part of a position and leave the remainder open.

        This is used when bid-side liquidity cannot absorb the full held size.
        The broker realizes PnL only for shares_to_close, then keeps the
        remaining shares and cost basis for a later close attempt.

        - Partial closes do not count as wins or losses; the final close does.
        - Partial-close PnL still contributes to market-type cumulative PnL.

        Returns:
            Realized PnL for the partially executed close.
        """
        if shares_to_close <= 0 or exit_price <= 0:
            return 0.0
        # Delegate to a full close when the requested size covers the position.
        if shares_to_close >= pos.shares:
            dummy = RawMarket(pos.market_id, pos.question, pos.metadata.get("slug"), True, False)
            return self.close_position(pos, dummy, exit_price, reason)

        fraction = shares_to_close / pos.shares
        gross_proceeds = shares_to_close * exit_price
        exit_fee_usdc = polymarket_taker_fee_usdc(shares_to_close, exit_price, self.settings.weather_taker_fee_rate)
        proceeds = gross_proceeds - exit_fee_usdc
        cost_basis_closed = pos.cost_usd * fraction
        pnl = proceeds - cost_basis_closed

        self.state.cash_usd += proceeds
        self.state.realized_pnl_usd += pnl

        # Add PnL by market type, but do not count a partial close as a win/loss.
        market_type = str(pos.metadata.get("market_type", "temperature"))
        st = self.state.stats.setdefault(market_type, {"wins": 0, "losses": 0, "pnl": 0.0})
        st["pnl"] = round(st["pnl"] + pnl, 6)

        # Update the remaining position in place.
        pos.shares = round(pos.shares - shares_to_close, 6)
        pos.cost_usd = round(pos.cost_usd - cost_basis_closed, 6)

        dummy = RawMarket(pos.market_id, pos.question, pos.metadata.get("slug"), True, False)
        close_reason = f"{reason}; exit_fee=${exit_fee_usdc:.5f} gross=${gross_proceeds:.5f} net=${proceeds:.5f}"
        self.log_trade("PARTIAL_CLOSE", dummy, pos.side, pos.token_id, shares_to_close, exit_price, pnl, close_reason, market_type)
        self.save_state()
        return pnl

    def log_decision(self, market: RawMarket, result: EdgeResult, note: str, market_type: str = "temperature") -> None:
        exists = self.decisions_csv_path.exists()
        with self.decisions_csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "ts", "market_id", "slug", "question", "market_type", "side", "p_true", "p_exec",
                    "net_edge", "size_usd", "size_shares", "entry_fraction",
                    "probability_stop_threshold", "model_fair_price", "target_exit_price",
                    "market_heat_score", "reason", "note",
                ],
            )
            if not exists:
                writer.writeheader()
            entry_fraction = probability_stop = model_fair = target_exit = heat_score = ""
            if result.side in {"YES", "NO"} and result.p_exec is not None and result.size_usd > 0:
                bankroll_before = self.current_bankroll_before_entry()
                plan = build_entry_plan(result, bankroll_before, self.settings)
                entry_fraction = f"{plan.entry_fraction:.6f}"
                probability_stop = f"{plan.probability_stop_threshold:.6f}"
                model_fair = f"{plan.model_fair_price:.6f}"
                target_exit = f"{plan.target_exit_price:.6f}"
                heat_score = f"{plan.market_heat_score:.6f}"
            writer.writerow({
                "ts": utc_now_iso(),
                "market_id": market.market_id,
                "slug": market.slug or "",
                "question": market.question,
                "market_type": market_type,
                "side": result.side,
                "p_true": f"{result.p_true:.6f}",
                "p_exec": "" if result.p_exec is None else f"{result.p_exec:.6f}",
                "net_edge": f"{result.net_edge:.6f}",
                "size_usd": f"{result.size_usd:.2f}",
                "size_shares": f"{result.size_shares:.6f}",
                "entry_fraction": entry_fraction,
                "probability_stop_threshold": probability_stop,
                "model_fair_price": model_fair,
                "target_exit_price": target_exit,
                "market_heat_score": heat_score,
                "reason": result.reason,
                "note": note,
            })

    def log_trade(
        self,
        action: str,
        market: RawMarket,
        side: str,
        token_id: str,
        shares: float,
        price: float,
        cash_delta_or_pnl: float,
        reason: str,
        market_type: str = "",
    ) -> None:
        exists = self.trades_csv_path.exists()
        with self.trades_csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["ts", "action", "market_id", "slug", "question", "market_type", "side", "token_id", "shares", "price", "cash_delta_or_pnl", "reason"],
            )
            if not exists:
                writer.writeheader()
            writer.writerow({
                "ts": utc_now_iso(),
                "action": action,
                "market_id": market.market_id,
                "slug": market.slug or "",
                "question": market.question,
                "market_type": market_type,
                "side": side,
                "token_id": token_id,
                "shares": f"{shares:.6f}",
                "price": f"{price:.6f}",
                "cash_delta_or_pnl": f"{cash_delta_or_pnl:.6f}",
                "reason": reason,
            })

    def log_raw_snapshot(self, event: str, market: RawMarket, payload: dict[str, Any]) -> None:
        row = {
            "ts": utc_now_iso(),
            "event": event,
            "market_id": market.market_id,
            "slug": market.slug or "",
            "question": market.question,
            "payload": payload,
        }
        with self.raw_snapshots_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def log_event_portfolio_decision(self, payload: dict[str, Any]) -> None:
        row = {"ts": utc_now_iso(), **payload}
        with self.portfolio_decisions_jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


    def stats_summary(self) -> str:
        """Return win-rate summary by market type."""
        lines = ["[Win-Rate Stats By Market Type]"]
        for mt, st in sorted(self.state.stats.items()):
            total = st["wins"] + st["losses"]
            wr = st["wins"] / total if total > 0 else 0.0
            lines.append(
                f"  {mt:15s}: {st['wins']} wins/{st['losses']} losses  "
                f"win_rate {wr:.1%}  cumulative_pnl ${st['pnl']:.2f}"
            )
        if not self.state.stats:
            lines.append("  no closed trades yet")
        return "\n".join(lines)


def resolved_winning_side(market: RawMarket) -> Literal["YES", "NO"] | None:
    raw = market.raw or {}
    if not (market.closed or raw.get("closed") or raw.get("resolved")):
        return None
    candidates = [
        raw.get("winningOutcome"),
        raw.get("winning_outcome"),
        raw.get("winner"),
        raw.get("outcome"),
        raw.get("resolvedOutcome"),
    ]
    for candidate in candidates:
        text = str(candidate or "").strip().upper()
        if text in {"YES", "NO"}:
            return text  # type: ignore[return-value]
    token_id = raw.get("winningTokenId") or raw.get("winning_token_id")
    if token_id is not None:
        token_id_s = str(token_id)
        if market.yes_token_id and token_id_s == market.yes_token_id:
            return "YES"
        if market.no_token_id and token_id_s == market.no_token_id:
            return "NO"
    outcome_price_winner = _resolved_side_from_outcome_prices(raw)
    if outcome_price_winner is not None:
        return outcome_price_winner
    return None


def _raw_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def _resolved_side_from_outcome_prices(raw: dict[str, Any]) -> Literal["YES", "NO"] | None:
    outcomes = _raw_list(raw.get("outcomes") or raw.get("outcome_names"))
    prices = _raw_list(raw.get("outcomePrices") or raw.get("outcome_prices"))
    if len(outcomes) != len(prices):
        return None
    price_by_side: dict[str, float] = {}
    for outcome, price in zip(outcomes, prices):
        side = str(outcome or "").strip().upper()
        if side not in {"YES", "NO"}:
            continue
        try:
            price_value = float(price)
        except (TypeError, ValueError):
            return None
        price_by_side[side] = price_value
    yes_price = price_by_side.get("YES")
    no_price = price_by_side.get("NO")
    if yes_price is None or no_price is None:
        return None
    epsilon = 1e-9
    if abs(yes_price - 1.0) <= epsilon and abs(no_price) <= epsilon:
        return "YES"
    if abs(no_price - 1.0) <= epsilon and abs(yes_price) <= epsilon:
        return "NO"
    return None


def maybe_settle_resolved_positions(
    broker: PaperBroker,
    market_by_id: dict[str, RawMarket],
) -> list[str]:
    messages: list[str] = []
    for pos in list(broker.state.positions):
        market = market_by_id.get(pos.market_id)
        if market is None:
            continue
        winner = resolved_winning_side(market)
        if winner is None:
            continue
        payout = 1.0 if pos.side == winner else 0.0
        pnl = broker.close_position(pos, market, payout, f"resolved winner={winner}")
        messages.append(f"SETTLED {pos.side} pnl=${pnl:.2f} payout={payout:.4f} reason=resolved winner={winner}")
    return messages


def _market_for_position(pos: PaperPosition, market: RawMarket | None = None) -> RawMarket:
    return market or RawMarket(pos.market_id, pos.question, pos.metadata.get("slug"), True, False)


def _runner_decision(
    pos: PaperPosition,
    mark_price: float,
    latest_edge: EdgeResult | None,
    settings: Settings,
) -> SettlementRunnerDecision:
    if not settings.settlement_runner_enabled:
        return SettlementRunnerDecision(False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "settlement runner blocked: disabled")
    if latest_edge is None:
        return SettlementRunnerDecision(False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "settlement runner blocked: missing latest probability")

    max_fraction = max(0.0, min(1.0, settings.settlement_runner_max_fraction))
    if max_fraction <= 0.0:
        return SettlementRunnerDecision(False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "settlement runner blocked: max fraction is zero")

    fee_per_share = polymarket_taker_fee_per_share(mark_price, settings.weather_taker_fee_rate)
    net_sell_price = max(0.0, mark_price - fee_per_share)
    if net_sell_price <= 0.0:
        return SettlementRunnerDecision(False, 0.0, 0.0, 0.0, 0.0, net_sell_price, 0.0, 0.0, "settlement runner blocked: net sell price is zero")

    settlement_value = conservative_settlement_value(pos.side, latest_edge.p_true, settings)
    sell_now_net = pos.shares * net_sell_price
    settlement_ev = pos.shares * settlement_value
    ev_margin = settlement_ev - sell_now_net
    required_margin = settings.settlement_runner_min_ev_margin_usd
    if ev_margin < required_margin:
        reason = (
            f"settlement runner blocked: sell_now_net=${sell_now_net:.2f} "
            f"settlement_ev=${settlement_ev:.2f} margin=${ev_margin:.2f} "
            f"< required=${required_margin:.2f}"
        )
        return SettlementRunnerDecision(False, 0.0, 0.0, 0.0, 0.0, net_sell_price, sell_now_net, settlement_ev, reason)

    max_runner_shares = pos.shares * max_fraction
    principal_recovery_shares = min(pos.shares, pos.cost_usd / net_sell_price)
    cap_recovery_shares = max(0.0, pos.shares - max_runner_shares)
    shares_to_close = min(pos.shares, max(principal_recovery_shares, cap_recovery_shares))
    runner_shares = max(0.0, pos.shares - shares_to_close)
    if shares_to_close <= 0.0 or runner_shares <= 0.000001:
        reason = (
            f"settlement runner blocked: no bounded runner after principal recovery "
            f"shares_to_close={shares_to_close:.4f} runner={runner_shares:.4f}"
        )
        return SettlementRunnerDecision(False, shares_to_close, runner_shares, max_runner_shares, principal_recovery_shares, net_sell_price, sell_now_net, settlement_ev, reason)

    reason = (
        f"settlement runner ok: sell_now_net=${sell_now_net:.2f} "
        f"settlement_ev=${settlement_ev:.2f} net_sell_price={net_sell_price:.4f} "
        f"principal_recovery_shares={principal_recovery_shares:.4f} "
        f"max_runner_shares={max_runner_shares:.4f}"
    )
    return SettlementRunnerDecision(
        True,
        shares_to_close,
        runner_shares,
        max_runner_shares,
        principal_recovery_shares,
        net_sell_price,
        sell_now_net,
        settlement_ev,
        reason,
    )


def _runner_hold_reason(
    decision: SettlementRunnerDecision,
    *,
    status: str,
    held_shares: float,
    assessment_reason: str,
    low_liquidity: bool = False,
) -> str:
    low_liquidity_text = " low_liquidity" if low_liquidity else ""
    return (
        f"tranche=settlement_runner action=hold status={status}{low_liquidity_text} "
        f"held_shares={held_shares:.4f} target_runner_shares={decision.runner_shares:.4f} "
        f"max_runner_shares={decision.max_runner_shares:.4f} "
        f"sell_now_net=${decision.sell_now_net_usdc:.2f} settlement_ev=${decision.settlement_ev_usdc:.2f} "
        f"assessment={assessment_reason}"
    )


def maybe_close_positions(
    broker: PaperBroker,
    client: PolymarketClient,
    market_by_id: dict[str, RawMarket],
    latest_edges: dict[tuple[str, str], EdgeResult],
) -> list[str]:
    """Evaluate open positions and close them when an exit rule fires.

    The close path depends on executable bid-side liquidity:

    1. Full close (can_fully_close=True)
       The bid book can absorb the whole position.
       The broker closes the full position at VWAP and logs CLOSE.

    2. Partial close (0 < absorbable < pos.shares)
       The bid book can absorb only part of the position.
       The broker closes only the absorbable shares, keeps the remainder for
       the next cycle, and logs PARTIAL_CLOSE.

    3. No liquidity (absorbable approximately 0)
       The close is effectively not executable. The broker holds the position
       and logs HOLD_NO_LIQUIDITY instead of pretending a full best-bid fill
       happened.
    """
    messages: list[str] = []
    stream = getattr(client, "stream", None)
    if stream is not None and hasattr(stream, "health_snapshot"):
        health = stream.health_snapshot()
        stream_block_reason = websocket_pricing_block_reason(health)
        if stream_block_reason:
            for pos in list(broker.state.positions):
                market = _market_for_position(pos, market_by_id.get(pos.market_id))
                market_type = str(pos.metadata.get("market_type", "temperature"))
                mark = pos.last_mark_price if pos.last_mark_price is not None else pos.entry_price
                pos.metadata["last_exit_assessment"] = stream_block_reason
                pos.metadata["last_websocket_health"] = health
                broker.log_trade(
                    "HOLD_STREAM_UNHEALTHY",
                    market,
                    pos.side,
                    pos.token_id,
                    pos.shares,
                    mark,
                    0.0,
                    stream_block_reason,
                    market_type,
                )
                messages.append(
                    f"HOLD_STREAM_UNHEALTHY {pos.side} shares={pos.shares:.2f} "
                    f"mark={mark:.4f} reason={stream_block_reason}"
                )
            broker.save_state()
            return messages
    now = datetime.now(timezone.utc)
    for pos in list(broker.state.positions):
        try:
            book = client.get_order_book(pos.token_id)
            best_bid = book.best_bid
            if best_bid is None:
                market = _market_for_position(pos, market_by_id.get(pos.market_id))
                market_type = str(pos.metadata.get("market_type", "temperature"))
                mark = pos.last_mark_price if pos.last_mark_price is not None else pos.entry_price
                no_liq = pos.metadata.get("no_liquidity_cycles", 0) + 1
                pos.metadata["no_liquidity_cycles"] = no_liq
                pos.metadata["absorbable_shares"] = 0.0
                pos.metadata["can_fully_close"] = False
                pos.metadata["last_exit_assessment"] = "no executable bid depth; indicative best_bid_ask ignored"
                if book.indicative_best_bid is not None:
                    pos.metadata["indicative_best_bid"] = round(book.indicative_best_bid, 6)
                broker.log_trade(
                    "HOLD_NO_LIQUIDITY",
                    market,
                    pos.side,
                    pos.token_id,
                    pos.shares,
                    mark,
                    0.0,
                    "no executable bid depth; indicative best_bid_ask ignored",
                    market_type,
                )
                messages.append(
                    f"HOLD_NO_LIQUIDITY {pos.side} shares={pos.shares:.2f} "
                    f"mark={mark:.4f} cycles={no_liq} reason=no executable bid depth; indicative best_bid_ask ignored"
                )
                continue

            # Step 1: measure executable absorbable size.
            vwap_exit, exit_slippage = executable_sell_price(book, pos.shares)
            absorbable = max_absorbable_shares(book.bids, min_price=0.01)
            can_fully_close = vwap_exit is not None

            if can_fully_close:
                # Full close is executable, so VWAP becomes the mark.
                mark = vwap_exit
            elif absorbable >= 0.001:
                # Partial close is executable, so recalculate VWAP for that size.
                partial_vwap, partial_slip = executable_sell_price(book, absorbable)
                if partial_vwap is not None:
                    mark = partial_vwap
                    exit_slippage = partial_slip
                else:
                    mark = best_bid
                    exit_slippage = 0.0
            else:
                # No practical liquidity. Mark at best bid, but do not close.
                mark = best_bid
                exit_slippage = 0.0

            # Step 2: update unrealized PnL and diagnostic metadata.
            pos.last_mark_price = mark
            exit_fee_usdc = polymarket_taker_fee_usdc(pos.shares, mark, broker.settings.weather_taker_fee_rate)
            pos.last_unrealized_pnl = pos.shares * mark - exit_fee_usdc - pos.cost_usd
            pos.metadata["exit_slippage"] = round(exit_slippage, 6)
            pos.metadata["exit_fee_usdc"] = exit_fee_usdc
            pos.metadata["best_bid"] = round(best_bid, 6)
            pos.metadata["absorbable_shares"] = round(absorbable, 4)
            pos.metadata["can_fully_close"] = can_fully_close

            # Step 3: evaluate exit conditions.
            hours = (now - parse_iso(pos.opened_at)).total_seconds() / 3600.0
            edge = latest_edges.get((pos.market_id, pos.side))
            assessment = assess_exit(pos, mark, edge, broker.settings, hours)
            pos.metadata["last_model_fair_price"] = assessment.model_fair_price
            pos.metadata["last_target_exit_price"] = assessment.target_exit_price
            pos.metadata["last_market_heat_score"] = assessment.market_heat_score
            pos.metadata["last_exit_assessment"] = assessment.reason

            if not assessment.should_close:
                continue

            market = _market_for_position(pos, market_by_id.get(pos.market_id))
            market_type = str(pos.metadata.get("market_type", "temperature"))
            runner_decision: SettlementRunnerDecision | None = None
            close_reason = assessment.reason
            if pos.metadata.get("settlement_runner_active") and assessment.trigger in PROFIT_RUNNER_TRIGGERS:
                runner_decision = _runner_decision(pos, mark, edge, broker.settings)
                if runner_decision.keep_runner:
                    reason = _runner_hold_reason(
                        runner_decision,
                        status="active",
                        held_shares=pos.shares,
                        assessment_reason=assessment.reason,
                    )
                    pos.metadata["last_settlement_runner_decision"] = reason
                    broker.log_trade("HOLD_RUNNER", market, pos.side, pos.token_id, pos.shares, mark, 0.0, reason, market_type)
                    messages.append(f"HOLD_RUNNER {pos.side} shares={pos.shares:.2f} price={mark:.4f} reason={reason}")
                    continue
                close_reason = f"{assessment.reason}; {runner_decision.reason}"

            if runner_decision is None and assessment.trigger in PROFIT_RUNNER_TRIGGERS:
                runner_decision = _runner_decision(pos, mark, edge, broker.settings)
                pos.metadata["last_settlement_runner_decision"] = runner_decision.reason
                if runner_decision.keep_runner:
                    desired_close = runner_decision.shares_to_close
                    shares_to_close = min(desired_close, absorbable if not can_fully_close else desired_close)
                    if shares_to_close < 0.001:
                        no_liq = pos.metadata.get("no_liquidity_cycles", 0) + 1
                        pos.metadata["no_liquidity_cycles"] = no_liq
                        reason = (
                            f"tranche=principal_recovery action=hold low_liquidity "
                            f"desired_shares={desired_close:.4f} absorbable={absorbable:.4f}; "
                            f"{runner_decision.reason}; assessment={assessment.reason}"
                        )
                        broker.log_trade("HOLD_NO_LIQUIDITY", market, pos.side, pos.token_id, pos.shares, mark, 0.0, reason, market_type)
                        messages.append(
                            f"HOLD_NO_LIQUIDITY {pos.side} shares={pos.shares:.2f} "
                            f"best_bid={best_bid:.4f} cycles={no_liq} reason={reason}"
                        )
                        continue

                    tranche_vwap, tranche_slippage = executable_sell_price(book, shares_to_close)
                    if tranche_vwap is None:
                        tranche_vwap = mark
                        tranche_slippage = exit_slippage
                    low_liquidity = shares_to_close + 0.000001 < desired_close
                    principal_reason = (
                        f"tranche=principal_recovery action=partial_close "
                        f"desired_shares={desired_close:.4f} actual_shares={shares_to_close:.4f} "
                        f"runner_target_shares={runner_decision.runner_shares:.4f} "
                        f"exit_vwap={tranche_vwap:.4f} slippage={tranche_slippage:.4f} "
                        f"{'low_liquidity ' if low_liquidity else ''}"
                        f"{runner_decision.reason}; assessment={assessment.reason}"
                    )
                    pnl = broker.partial_close_position(pos, shares_to_close, tranche_vwap, principal_reason)
                    if not low_liquidity and pos.shares <= runner_decision.max_runner_shares + 0.000001:
                        pos.metadata["settlement_runner_active"] = True
                        runner_status = "active"
                    else:
                        pos.metadata["settlement_runner_pending"] = True
                        runner_status = "pending"
                    hold_reason = _runner_hold_reason(
                        runner_decision,
                        status=runner_status,
                        held_shares=pos.shares,
                        assessment_reason=assessment.reason,
                        low_liquidity=low_liquidity,
                    )
                    pos.metadata["last_settlement_runner_decision"] = hold_reason
                    broker.log_trade("HOLD_RUNNER", market, pos.side, pos.token_id, pos.shares, tranche_vwap, 0.0, hold_reason, market_type)
                    messages.append(
                        f"PARTIAL_CLOSE {pos.side} closed={shares_to_close:.2f} remain={pos.shares:.2f} "
                        f"pnl=${pnl:.2f} price={tranche_vwap:.4f} best_bid={best_bid:.4f} "
                        f"{'low_liquidity ' if low_liquidity else ''}reason={principal_reason}"
                    )
                    messages.append(
                        f"HOLD_RUNNER {pos.side} shares={pos.shares:.2f} "
                        f"target_runner={runner_decision.runner_shares:.2f} status={runner_status} reason={hold_reason}"
                    )
                    continue
                close_reason = f"{assessment.reason}; {runner_decision.reason}"

            # Step 4: execute the selected close path.
            if can_fully_close:
                pnl = broker.close_position(pos, market, mark, close_reason)
                messages.append(
                    f"CLOSE {pos.side} pnl=${pnl:.2f} "
                    f"exit_vwap={mark:.4f} best_bid={best_bid:.4f} "
                    f"slippage={exit_slippage:.4f} reason={close_reason}"
                )
            elif absorbable >= 0.001:
                # Partial close: execute only the absorbable shares and keep the rest.
                original_shares = pos.shares
                pnl = broker.partial_close_position(
                    pos, absorbable, mark,
                    f"PARTIAL({absorbable:.2f}/{original_shares:.2f}shares): {close_reason}",
                )
                messages.append(
                    f"PARTIAL_CLOSE {pos.side} "
                    f"closed={absorbable:.2f} remain={pos.shares:.2f} "
                    f"pnl=${pnl:.2f} price={mark:.4f} best_bid={best_bid:.4f} "
                    f"reason={close_reason}"
                )
            else:
                # Insufficient liquidity: hold and count consecutive no-liquidity cycles.
                no_liq = pos.metadata.get("no_liquidity_cycles", 0) + 1
                pos.metadata["no_liquidity_cycles"] = no_liq
                broker.log_trade("HOLD_NO_LIQUIDITY", market, pos.side, pos.token_id, pos.shares, mark, 0.0, close_reason, market_type)
                messages.append(
                    f"HOLD_NO_LIQUIDITY {pos.side} shares={pos.shares:.2f} "
                    f"best_bid={best_bid:.4f} cycles={no_liq} "
                    f"reason={close_reason}"
                )

        except Exception as exc:  # noqa: BLE001
            messages.append(f"MARK ERROR {pos.question[:60]}: {exc}")
    broker.save_state()
    return messages
