from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .config import Settings
from .models import EdgeResult, PaperPosition, PaperState, RawMarket
from .edge import executable_sell_price, max_absorbable_shares
from .exit_policy import assess_exit, build_entry_plan, side_true_probability
from .polymarket_client import PolymarketClient
from .portfolio import adaptive_event_cap_fraction, is_complementary_with_positions


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


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
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        # stats 역직렬화: 기존 state 파일에 없어도 빈 dict로 초기화
        raw_stats = raw.get("stats", {})
        stats: dict[str, Any] = {
            mt: {
                "wins": int(st.get("wins", 0)),
                "losses": int(st.get("losses", 0)),
                "pnl": float(st.get("pnl", 0.0)),
            }
            for mt, st in raw_stats.items()
        }
        return PaperState(
            cash_usd=float(raw.get("cash_usd", self.settings.bankroll_usd)),
            realized_pnl_usd=float(raw.get("realized_pnl_usd", 0.0)),
            positions=[PaperPosition(**p) for p in raw.get("positions", [])],
            stats=stats,
        )

    def save_state(self) -> None:
        payload = {
            "cash_usd": self.state.cash_usd,
            "realized_pnl_usd": self.state.realized_pnl_usd,
            "positions": [asdict(p) for p in self.state.positions],
            "stats": self.state.stats,
        }
        self.state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

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
        """마켓 ID에 해당하는 포지션이 side에 관계없이 존재하면 True.

        YES/NO 동시 진입(헷징) 차단에 사용.
        같은 마켓에 YES + NO를 동시 보유하면 수수료 손실만 발생하므로
        이미 한 쪽 포지션이 열려 있으면 반대 쪽 신호도 무시한다.
        """
        return any(p.market_id == market_id for p in self.state.positions)

    def city_exposure(self, city: str) -> float:
        """특정 도시의 현재 총 포지션 cost_usd 합계를 반환한다.

        같은 도시의 파생 마켓(기온 70F/72F/75F 등)에 중복 진입하면
        단일 날씨 이벤트에 과도하게 노출된다. 이를 제한하기 위해 사용한다.
        """
        city_lower = city.lower()
        return sum(
            p.cost_usd
            for p in self.state.positions
            if str(p.metadata.get("city", "")).lower() == city_lower
        )

    def event_date_exposure(self, city: str, date_hint: str) -> float:
        """같은 도시 + 날짜 조합의 현재 총 포지션 cost_usd 합계를 반환한다.

        예: NYC + 'jun 15'에 기온 70F, 72F 두 포지션을 동시에 보유하면
        같은 하루 날씨 이벤트에 이중으로 노출된다.
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

        # ── 도시별 중복 노출 한도 체크 ──────────────────────────────────────────
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

        # ── 도시+날짜 조합 중복 노출 한도 체크 ──────────────────────────────────
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
        adjusted_result = EdgeResult(
            result.side,
            result.p_true,
            result.p_exec,
            result.net_edge,
            spend,
            spend / result.p_exec,
            result.reason,
            expected_profit,
        )
        entry_plan = build_entry_plan(adjusted_result, risk_bankroll, self.settings)
        shares = spend / result.p_exec
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
                "reason": result.reason,
                "slug": market.slug,
                "market_type": market_type,
                "city": city,           # 도시별 중복 노출 한도 추적용
                "date_hint": date_hint, # 도시+날짜 조합 중복 노출 한도 추적용
            },
        )
        self.state.cash_usd -= spend
        self.state.positions.append(pos)
        self.save_state()
        self.log_trade("OPEN", market, result.side, token_id, shares, result.p_exec, -spend, entry_plan.rationale, market_type)
        return pos

    def close_position(self, pos: PaperPosition, market: RawMarket | None, exit_price: float, reason: str) -> float:
        proceeds = pos.shares * exit_price
        pnl = proceeds - pos.cost_usd
        self.state.cash_usd += proceeds
        self.state.realized_pnl_usd += pnl
        # ── 마켓 타입별 승률 추적 ──────────────────────────────────
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
        self.log_trade("CLOSE", dummy, pos.side, pos.token_id, pos.shares, exit_price, pnl, reason, market_type)
        return pnl

    def partial_close_position(
        self,
        pos: PaperPosition,
        shares_to_close: float,
        exit_price: float,
        reason: str,
    ) -> float:
        """보유 물량의 일부만 청산한다. 잔여 물량은 포지션에 남는다.

        Bid 호가창 유동성이 전체 보유 물량을 소화하지 못할 때 사용한다.
        shares_to_close만큼만 실현 손익을 계산하고, 잔여 shares/cost는
        다음 사이클에서 재청산 시도 대상으로 유지된다.

        - 부분 청산은 승/패 카운트에 포함하지 않는다 (최종 청산 시 판단).
        - PnL은 마켓 타입별 누적 합산에는 포함한다.

        Returns:
            실현 PnL (부분 체결분)
        """
        if shares_to_close <= 0 or exit_price <= 0:
            return 0.0
        # 보유 물량 전체를 초과하면 full close로 위임
        if shares_to_close >= pos.shares:
            dummy = RawMarket(pos.market_id, pos.question, pos.metadata.get("slug"), True, False)
            return self.close_position(pos, dummy, exit_price, reason)

        fraction = shares_to_close / pos.shares
        proceeds = shares_to_close * exit_price
        cost_basis_closed = pos.cost_usd * fraction
        pnl = proceeds - cost_basis_closed

        self.state.cash_usd += proceeds
        self.state.realized_pnl_usd += pnl

        # 마켓 타입별 PnL 누적 (부분 청산은 승/패 카운트 제외)
        market_type = str(pos.metadata.get("market_type", "temperature"))
        st = self.state.stats.setdefault(market_type, {"wins": 0, "losses": 0, "pnl": 0.0})
        st["pnl"] = round(st["pnl"] + pnl, 6)

        # 포지션 잔여분 업데이트 (in-place)
        pos.shares = round(pos.shares - shares_to_close, 6)
        pos.cost_usd = round(pos.cost_usd - cost_basis_closed, 6)

        dummy = RawMarket(pos.market_id, pos.question, pos.metadata.get("slug"), True, False)
        self.log_trade("PARTIAL_CLOSE", dummy, pos.side, pos.token_id, shares_to_close, exit_price, pnl, reason, market_type)
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
        """온도/강수 마켓별 승률 요약 반환."""
        lines = ["[타입별 승률 통계]"]
        for mt, st in sorted(self.state.stats.items()):
            total = st["wins"] + st["losses"]
            wr = st["wins"] / total if total > 0 else 0.0
            lines.append(
                f"  {mt:15s}: {st['wins']}승/{st['losses']}패  "
                f"승률 {wr:.1%}  누적PnL ${st['pnl']:.2f}"
            )
        if not self.state.stats:
            lines.append("  아직 청산된 거래 없음")
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


def maybe_close_positions(
    broker: PaperBroker,
    client: PolymarketClient,
    market_by_id: dict[str, RawMarket],
    latest_edges: dict[tuple[str, str], EdgeResult],
) -> list[str]:
    """열린 포지션 청산 여부를 평가하고, 조건 충족 시 청산을 실행한다.

    청산 가능 수량에 따라 세 가지 경로로 분기한다:

    1. 전량 청산 (can_fully_close=True)
       Bid 호가창이 보유 물량 전체를 소화할 수 있을 때.
       VWAP 가격으로 전체 청산 → CLOSE 로그.

    2. 부분 청산 (0 < absorbable < pos.shares)
       호가창이 일부만 소화 가능할 때.
       흡수 가능 수량만 청산, 잔여 물량은 다음 사이클에서 재시도 → PARTIAL_CLOSE 로그.

    3. 유동성 없음 (absorbable ≈ 0)
       실질적으로 체결 불가. 청산 보류 → HOLD_NO_LIQUIDITY 로그.
       이전 코드처럼 best_bid로 전량 청산 기록하지 않음 (가짜 체결 방지).
    """
    messages: list[str] = []
    now = datetime.now(timezone.utc)
    for pos in list(broker.state.positions):
        try:
            book = client.get_order_book(pos.token_id)
            best_bid = book.best_bid
            if best_bid is None:
                continue

            # ── 1단계: 소화 가능 수량 파악 ────────────────────────────────────
            vwap_exit, exit_slippage = executable_sell_price(book, pos.shares)
            absorbable = max_absorbable_shares(book.bids, min_price=0.01)
            can_fully_close = vwap_exit is not None

            if can_fully_close:
                # 전량 소화 가능: VWAP 가격이 mark
                mark = vwap_exit
            elif absorbable >= 0.001:
                # 부분 소화: 흡수 가능 수량 기준 VWAP 재계산
                partial_vwap, partial_slip = executable_sell_price(book, absorbable)
                if partial_vwap is not None:
                    mark = partial_vwap
                    exit_slippage = partial_slip
                else:
                    mark = best_bid
                    exit_slippage = 0.0
            else:
                # 유동성 사실상 없음: mark는 best_bid로 표시만 (청산 미실행)
                mark = best_bid
                exit_slippage = 0.0

            # ── 2단계: 미실현 PnL 및 메타데이터 갱신 ──────────────────────────
            pos.last_mark_price = mark
            pos.last_unrealized_pnl = pos.shares * mark - pos.cost_usd
            pos.metadata["exit_slippage"] = round(exit_slippage, 6)
            pos.metadata["best_bid"] = round(best_bid, 6)
            pos.metadata["absorbable_shares"] = round(absorbable, 4)
            pos.metadata["can_fully_close"] = can_fully_close

            # ── 3단계: 청산 조건 평가 ─────────────────────────────────────────
            hours = (now - parse_iso(pos.opened_at)).total_seconds() / 3600.0
            edge = latest_edges.get((pos.market_id, pos.side))
            assessment = assess_exit(pos, mark, edge, broker.settings, hours)
            pos.metadata["last_model_fair_price"] = assessment.model_fair_price
            pos.metadata["last_target_exit_price"] = assessment.target_exit_price
            pos.metadata["last_market_heat_score"] = assessment.market_heat_score
            pos.metadata["last_exit_assessment"] = assessment.reason

            if not assessment.should_close:
                continue

            # ── 4단계: 실제 청산 실행 ─────────────────────────────────────────
            if can_fully_close:
                pnl = broker.close_position(
                    pos, market_by_id.get(pos.market_id), mark, assessment.reason
                )
                messages.append(
                    f"CLOSE {pos.side} pnl=${pnl:.2f} "
                    f"exit_vwap={mark:.4f} best_bid={best_bid:.4f} "
                    f"slippage={exit_slippage:.4f} reason={assessment.reason}"
                )
            elif absorbable >= 0.001:
                # 부분 청산: 흡수 가능 수량만 체결, 잔여는 다음 사이클
                original_shares = pos.shares
                pnl = broker.partial_close_position(
                    pos, absorbable, mark,
                    f"PARTIAL({absorbable:.2f}/{original_shares:.2f}shares): {assessment.reason}",
                )
                messages.append(
                    f"PARTIAL_CLOSE {pos.side} "
                    f"closed={absorbable:.2f} remain={pos.shares:.2f} "
                    f"pnl=${pnl:.2f} price={mark:.4f} best_bid={best_bid:.4f} "
                    f"reason={assessment.reason}"
                )
            else:
                # 유동성 부족 → 청산 보류, 누적 사이클 수 기록
                no_liq = pos.metadata.get("no_liquidity_cycles", 0) + 1
                pos.metadata["no_liquidity_cycles"] = no_liq
                messages.append(
                    f"HOLD_NO_LIQUIDITY {pos.side} shares={pos.shares:.2f} "
                    f"best_bid={best_bid:.4f} cycles={no_liq} "
                    f"reason={assessment.reason}"
                )

        except Exception as exc:  # noqa: BLE001
            messages.append(f"MARK ERROR {pos.question[:60]}: {exc}")
    broker.save_state()
    return messages
