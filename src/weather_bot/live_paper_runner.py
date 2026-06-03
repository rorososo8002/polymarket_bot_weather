from __future__ import annotations

import inspect
from datetime import datetime, timezone
import threading
import time
from typing import Any

from .config import Settings, load_settings
from .edge import (
    estimate_executable_net_return,
    executable_buy_price,
    fee_adjusted_entry_shares,
    no_net_edge,
    polymarket_taker_fee_per_share,
    yes_net_edge,
)
from .exit_policy import conservative_settlement_value, model_fair_price, target_exit_price
from .models import EdgeResult, MarketDecision, OrderBook, PaperPosition, RawMarket, WeatherSignal
from .nowcast import AviationWeatherMetarNowcastProvider
from .paper import PaperBroker, maybe_close_positions, maybe_settle_resolved_positions
from .polymarket_client import PolymarketClient
from .portfolio import (
    EntryBankrollSnapshot,
    EventPortfolioDecision,
    PortfolioCandidate,
    available_entry_bankroll,
    select_event_portfolio,
    websocket_pricing_block_reason,
)
from .probability import OpenMeteoEnsembleClient, estimate_weather_probability
from .realtime_orderbook import OrderBookMarketStream
from .risk import fractional_kelly_binary
from .runner_status import utc_now_iso, write_runner_status
from .weather_client import parse_weather_question


ENTRY_BANKROLL_FAIL_CLOSED_REASON = "기존 포지션을 안전하게 평가할 수 없어 신규 진입 차단"


def _call_probability_estimator(
    probability_estimator: Any,
    question: str,
    *,
    settings: Settings,
    ensemble_client: OpenMeteoEnsembleClient | None = None,
    observation_provider: Any | None = None,
) -> WeatherSignal:
    kwargs: dict[str, Any] = {"settings": settings}
    if ensemble_client is not None:
        kwargs["ensemble_client"] = ensemble_client
    if observation_provider is not None:
        signature = inspect.signature(probability_estimator)
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
        if accepts_kwargs or "observation_provider" in signature.parameters:
            kwargs["observation_provider"] = observation_provider
    return probability_estimator(question, **kwargs)


class StreamBackedPolymarketClient(PolymarketClient):
    def __init__(self, gamma_base: str, clob_base: str, stream: OrderBookMarketStream) -> None:
        super().__init__(gamma_base, clob_base)
        self.stream = stream

    def get_order_book(self, token_id: str) -> OrderBook:
        return self.stream.get_order_book(token_id)


def position_size_usd(
    side_probability: float,
    p_eff: float,
    settings: Settings,
    bankroll_usd: float,
    entry_fraction_override: float | None = None,
    net_edge: float = 0.0,
    min_edge: float | None = None,
) -> float:
    """Return target paper order size in USD."""
    if settings.size_mode.lower() == "kelly":
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
    return bankroll_usd * max(0.0, frac)


def _market_params(settings: Settings, market_type: str) -> tuple[float, float, float | None]:
    if market_type == "precipitation":
        return settings.precip_min_confidence, settings.precip_min_net_edge, settings.precip_entry_fraction
    return 0.50, settings.min_net_edge, None


def _bid_notional(book: OrderBook, min_price: float = 0.01) -> float:
    return sum(level.price * level.size for level in book.bids if level.price >= min_price)


def _side_liquidity_reason(side: str, book: OrderBook, market_type: str) -> str | None:
    ask = book.best_ask
    bid = book.best_bid
    if ask is None:
        return f"{side} liquidity filter: no ask [{market_type}]"
    if bid is None:
        return f"{side} liquidity filter: no bid [{market_type}]"
    spread = ask - bid
    if spread > 0.20:
        return f"{side} liquidity filter: spread too wide {spread:.2f} > 0.20 [{market_type}]"
    if ask >= 1.0 or ask <= 0.0:
        return f"{side} liquidity filter: invalid ask={ask:.3f} [{market_type}]"
    if ask < 0.08:
        return f"{side} liquidity filter: extreme low ask={ask:.3f} below 0.08 [{market_type}]"
    bid_value = _bid_notional(book)
    if bid_value < 10.0:
        return f"{side} liquidity filter: exit bid depth ${bid_value:.1f} < $10 [{market_type}]"
    return None


def _fetch_books(market: RawMarket, client: PolymarketClient) -> tuple[dict[str, OrderBook], str | None]:
    books: dict[str, OrderBook] = {}
    try:
        if market.yes_token_id:
            books["YES"] = client.get_order_book(market.yes_token_id)
        if market.no_token_id:
            books["NO"] = client.get_order_book(market.no_token_id)
    except Exception as exc:  # noqa: BLE001
        return books, f"order book error: {exc}"
    return books, None


def _yes_no_sum_reason(books: dict[str, OrderBook], market_type: str) -> str | None:
    yes_book = books.get("YES")
    no_book = books.get("NO")
    if not yes_book or not no_book or yes_book.best_ask is None or no_book.best_ask is None:
        return None
    yes_no_sum = yes_book.best_ask + no_book.best_ask
    if abs(yes_no_sum - 1.0) > 0.05:
        return f"YES+NO ask sum abnormal {yes_no_sum:.3f} outside 1±0.05 [{market_type}]"
    return None


def _side_result(
    side: str,
    book: OrderBook,
    signal: WeatherSignal,
    settings: Settings,
    bankroll_before_entry: float,
    min_edge: float,
    entry_fraction_override: float | None,
    market_type: str,
) -> EdgeResult:
    liquidity_reason = _side_liquidity_reason(side, book, market_type)
    if liquidity_reason:
        return EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, liquidity_reason)

    target_usd = max(settings.min_order_usd, bankroll_before_entry * settings.max_single_market_fraction)
    p_exec, _shares, slip = executable_buy_price(book, target_usd)
    if p_exec is None:
        return EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, f"{side} liquidity filter: insufficient ask depth [{market_type}]")

    entry_fee_per_share = polymarket_taker_fee_per_share(p_exec, settings.weather_taker_fee_rate)
    if side == "YES":
        edge = yes_net_edge(
            signal.p_true,
            p_exec,
            entry_fee_per_share,
            settings.model_error_margin,
            settings.resolution_error_margin,
        )
        side_probability = signal.p_true
    else:
        edge = no_net_edge(
            signal.p_true,
            p_exec,
            entry_fee_per_share,
            settings.model_error_margin,
            settings.resolution_error_margin,
        )
        side_probability = 1.0 - signal.p_true

    p_eff = p_exec + entry_fee_per_share
    size_usd = position_size_usd(
        side_probability,
        p_eff,
        settings,
        bankroll_before_entry,
        entry_fraction_override,
        net_edge=edge,
        min_edge=min_edge,
    )
    if size_usd < settings.min_order_usd:
        reason = (
            f"{side} calculated order ${size_usd:.2f} below minimum order "
            f"${settings.min_order_usd:.2f}; skipping before expected-return estimate [{market_type}]"
        )
        return EdgeResult("SKIP", signal.p_true, p_exec, edge, 0.0, 0.0, reason)

    estimate_shares = fee_adjusted_entry_shares(size_usd, p_exec, settings.weather_taker_fee_rate)
    spread = max(0.0, (book.best_ask or p_exec) - (book.best_bid or p_exec))
    fair = model_fair_price(side, signal.p_true, settings)
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
        expected_exit_price=conservative_settlement_value(side, signal.p_true, settings),
        fee_rate=settings.weather_taker_fee_rate,
        hold_to_settlement=True,
    )
    return_estimate = max(
        (expected_exit_estimate, settlement_estimate),
        key=lambda estimate: estimate.expected_net_return_pct,
    )
    return_ok = return_estimate.expected_net_return_pct >= settings.entry_min_expected_net_return_pct
    is_trade = edge > min_edge and size_usd >= settings.min_order_usd and return_ok
    rejection = ""
    if not return_ok:
        rejection = f", reject=expected net return below {settings.entry_min_expected_net_return_pct:.2%}"
    reason = (
        f"{side} edge={edge:.4f}, p_exec_vwap={p_exec:.4f}, route={return_estimate.route}, "
        f"expected_exit={return_estimate.expected_exit_price:.4f}, "
        f"expected_gross=${return_estimate.expected_gross_profit_usdc:.4f}, "
        f"estimated_cost=${return_estimate.estimated_cost_usdc:.4f}, "
        f"expected_net_return={return_estimate.expected_net_return_pct:.2%}, "
        f"entry_fee=${return_estimate.entry_fee_usdc:.4f}, "
        f"exit_fee=${return_estimate.exit_fee_usdc:.4f}, "
        f"exit_market_cost=${return_estimate.exit_market_cost_usdc:.4f}, "
        f"spread_audit={spread:.4f}, slip_audit={slip:.4f}{rejection} [{market_type}]"
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


def evaluate_market(
    market: RawMarket,
    signal: WeatherSignal,
    client: PolymarketClient,
    settings: Settings,
    bankroll_before_entry: float,
    market_type: str = "temperature",
    entry_bankroll_reason: str | None = None,
) -> tuple[EdgeResult, dict[str, EdgeResult]]:
    """Evaluate live YES/NO books and return the best executable paper result."""
    min_confidence, min_edge, entry_fraction_override = _market_params(settings, market_type)

    if market_type == "precipitation" and not settings.enable_precipitation_markets:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, "precipitation/snow markets disabled by config")
        return result, {}

    if settings.require_parse_for_trade and signal.confidence < min_confidence:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, f"confidence too low: {signal.confidence:.2f} < {min_confidence:.2f} [{market_type}]")
        return result, {}

    if settings.require_date_hint_for_trade and signal.parsed is not None and signal.parsed.date_hint is None:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, f"date_hint=None: refusing undated market [{market_type}]")
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

    books, fetch_error = _fetch_books(market, client)
    if fetch_error:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, fetch_error)
        return result, {}

    sum_reason = _yes_no_sum_reason(books, market_type)
    if sum_reason:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, sum_reason)
        return result, {side: result for side in books}

    best_result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, "No valid side evaluated.")
    per_side: dict[str, EdgeResult] = {}
    for side, book in books.items():
        result = _side_result(
            side,
            book,
            signal,
            settings,
            bankroll_before_entry,
            min_edge,
            entry_fraction_override,
            market_type,
        )
        per_side[side] = result
        if result.net_edge > best_result.net_edge:
            best_result = result

    if best_result.side == "SKIP":
        prefix = "trade blocked" if best_result.net_edge > min_edge else f"edge below {min_edge:.2%}"
        reason = best_result.reason
        if best_result.net_edge <= -999.0:
            reason = _no_valid_side_reason(reason, per_side)
        best_result = EdgeResult(
            side="SKIP",
            p_true=signal.p_true,
            p_exec=best_result.p_exec,
            net_edge=best_result.net_edge,
            size_usd=0.0,
            size_shares=0.0,
            reason=f"{prefix} [{market_type}]. {reason}",
        )
    elif best_result.net_edge <= min_edge:
        best_result = EdgeResult(
            side="SKIP",
            p_true=signal.p_true,
            p_exec=best_result.p_exec,
            net_edge=best_result.net_edge,
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
) -> list[PortfolioCandidate]:
    executable = [
        PortfolioCandidate(market, signal, edge_result, market_type)
        for edge_result in per_side.values()
        if edge_result.side in {"YES", "NO"}
    ]
    return executable or [PortfolioCandidate(market, signal, result, market_type)]


def _market_type_from_signal(signal: WeatherSignal) -> str:
    if signal.parsed is not None and signal.parsed.variable in {"precipitation", "snow"}:
        return "precipitation"
    return "temperature"


def _market_from_position(pos: PaperPosition) -> RawMarket:
    return RawMarket(
        market_id=pos.market_id,
        question=pos.question,
        slug=pos.metadata.get("slug"),
        active=True,
        closed=False,
        yes_token_id=pos.token_id if pos.side == "YES" else None,
        no_token_id=pos.token_id if pos.side == "NO" else None,
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
    probability_estimator=estimate_weather_probability,
    ensemble_client: OpenMeteoEnsembleClient | None = None,
    observation_provider: Any | None = None,
) -> None:
    """Refresh model probability and edge for held positions missing from the scan."""
    for pos in broker.state.positions:
        key = (pos.market_id, pos.side)
        if key in latest_edges:
            continue
        market = market_by_id.get(pos.market_id) or _market_from_position(pos)
        signal = _call_probability_estimator(
            probability_estimator,
            pos.question,
            settings=settings,
            ensemble_client=ensemble_client,
            observation_provider=observation_provider,
        )
        market_type = str(pos.metadata.get("market_type") or _market_type_from_signal(signal))
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


def _discovery_coverage(markets: list[RawMarket]) -> dict[str, int]:
    groups = _group_weather_markets_by_event(markets)
    cities = {
        parsed.city
        for market in markets
        if (parsed := parse_weather_question(market.question)).city
    }
    return {"events": len(groups), "cities": len(cities), "markets": len(markets)}


def run_cycle(settings: Settings | None = None) -> list[MarketDecision]:
    settings = settings or load_settings()
    cycle_started_at = utc_now_iso()
    write_runner_status(settings, "starting", message="starting cycle", cycle_started_at=cycle_started_at)
    client = PolymarketClient(settings.gamma_base, settings.clob_base)
    ensemble_client = OpenMeteoEnsembleClient.from_settings(settings)
    observation_provider = AviationWeatherMetarNowcastProvider.from_settings(settings)
    broker = PaperBroker(settings)
    try:
        write_runner_status(settings, "discovering", message="discovering markets", cycle_started_at=cycle_started_at)
        discovered_markets = client.discover_weather_markets(
            max_pages=settings.discovery_max_pages,
            page_size=settings.discovery_page_size,
        )
    except Exception as exc:  # noqa: BLE001
        write_runner_status(settings, "error", message=f"market discovery failed: {exc}", cycle_started_at=cycle_started_at)
        print(f"DATA ERROR: could not fetch live Polymarket markets: {exc}")
        print("Check internet/DNS/VPN, then run live-paper-bot again.")
        return []

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
    )
    markets_done = 0
    for event_markets in event_groups:
        entry_bankroll = available_entry_bankroll(broker, client)
        candidates: list[PortfolioCandidate] = []
        for market in event_markets:
            markets_done += 1
            try:
                signal = _call_probability_estimator(
                    estimate_weather_probability,
                    market.question,
                    settings=settings,
                    ensemble_client=ensemble_client,
                    observation_provider=observation_provider,
                )
                market_type = _market_type_from_signal(signal)
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
                candidates.extend(_event_portfolio_candidates(market, signal, result, per_side, market_type))
                broker.log_decision(market, result, signal.note, market_type)
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
            )
        portfolio = _apply_event_portfolio(broker, candidates, entry_bankroll)
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
        ensemble_client=ensemble_client,
        observation_provider=observation_provider,
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
    )
    return decisions


def _market_token_ids(market: RawMarket) -> list[str]:
    return [token_id for token_id in (market.yes_token_id, market.no_token_id) if token_id]


def _open_position_if_needed(
    broker: PaperBroker,
    market: RawMarket,
    signal: WeatherSignal,
    result: EdgeResult,
    market_type: str,
    entry_bankroll_usd: float | None = None,
) -> None:
    if result.side not in {"YES", "NO"}:
        return
    token_id = market.yes_token_id if result.side == "YES" else market.no_token_id
    if broker.has_any_position(market.market_id) or not token_id:
        return
    city = signal.parsed.city if signal.parsed is not None else ""
    date_hint = signal.parsed.date_hint if signal.parsed is not None else ""
    broker.open_position(
        market,
        token_id,
        result,
        market_type,
        city=city or "",
        date_hint=date_hint or "",
        entry_bankroll_usd=entry_bankroll_usd,
    )


def _apply_event_portfolio(
    broker: PaperBroker,
    candidates: list[PortfolioCandidate],
    entry_bankroll: EntryBankrollSnapshot,
) -> EventPortfolioDecision:
    decision = select_event_portfolio(broker, candidates, entry_bankroll)
    broker.log_event_portfolio_decision(decision.to_log_payload())
    for candidate in decision.selected:
        _open_position_if_needed(
            broker,
            candidate.market,
            candidate.signal,
            candidate.result,
            candidate.market_type,
            entry_bankroll_usd=entry_bankroll.entry_bankroll,
        )
    return decision


def _evaluate_realtime_update(
    updated_token_ids: set[str],
    client: StreamBackedPolymarketClient,
    broker: PaperBroker,
    settings: Settings,
    market_by_token: dict[str, RawMarket],
    signals_by_market: dict[str, WeatherSignal],
    market_types: dict[str, str],
    latest_edges: dict[tuple[str, str], EdgeResult],
) -> None:
    websocket_health: dict[str, object] = {}
    stream = getattr(client, "stream", None)
    if stream is not None and hasattr(stream, "health_snapshot"):
        websocket_health = stream.health_snapshot()
    touched_events = {
        _market_event_key(market_by_token[token_id])
        for token_id in updated_token_ids
        if token_id in market_by_token
    }
    market_by_id = {market.market_id: market for market in market_by_token.values()}
    event_groups: dict[str, list[RawMarket]] = {}
    for market in market_by_id.values():
        event_groups.setdefault(_market_event_key(market), []).append(market)
    for event_key in touched_events:
        entry_bankroll = available_entry_bankroll(broker, client)
        candidates: list[PortfolioCandidate] = []
        for market in event_groups[event_key]:
            signal = signals_by_market[market.market_id]
            market_type = market_types[market.market_id]
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
            candidates.extend(_event_portfolio_candidates(market, signal, result, per_side, market_type))
            broker.log_decision(market, result, signal.note, market_type)
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
                },
            )
        _apply_event_portfolio(broker, candidates, entry_bankroll)
    for message in maybe_close_positions(broker, client, market_by_id, latest_edges):
        print(message)


def _stream_status_phase(
    websocket_health: dict[str, object],
    *,
    token_count: int,
    market_count: int,
    event_count: int,
    city_count: int,
) -> tuple[str, str]:
    coverage = f"{token_count} tokens across {market_count} markets, {event_count} events, {city_count} cities"
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
    return token_count > 0 and not bool(websocket_health.get("thread_alive"))


def run_realtime_forever(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    while True:
        refresh_started_at = datetime.now(timezone.utc)
        cycle_started_at = utc_now_iso()
        discovery_client = PolymarketClient(settings.gamma_base, settings.clob_base)
        ensemble_client = OpenMeteoEnsembleClient.from_settings(settings)
        observation_provider = AviationWeatherMetarNowcastProvider.from_settings(settings)
        broker = PaperBroker(settings)
        write_runner_status(settings, "discovering", message="discovering markets for websocket stream", cycle_started_at=cycle_started_at)
        discovered_markets = discovery_client.discover_weather_markets(
            max_pages=settings.discovery_max_pages,
            page_size=settings.discovery_page_size,
        )
        event_groups = _group_weather_markets_by_event(discovered_markets)
        markets = [market for group in event_groups for market in group]
        market_by_id = _stream_market_registry(discovery_client, broker, markets)
        for msg in _settle_resolved_positions_before_streaming(broker, market_by_id):
            print(msg)
        stream_markets = list(market_by_id.values())
        coverage = _discovery_coverage(stream_markets)
        market_by_token = {
            token_id: market
            for market in stream_markets
            for token_id in _market_token_ids(market)
        }
        signals_by_market: dict[str, WeatherSignal] = {}
        market_types: dict[str, str] = {}
        for market in stream_markets:
            signal = _call_probability_estimator(
                estimate_weather_probability,
                market.question,
                settings=settings,
                ensemble_client=ensemble_client,
                observation_provider=observation_provider,
            )
            signals_by_market[market.market_id] = signal
            market_types[market.market_id] = _market_type_from_signal(signal)

        latest_edges: dict[tuple[str, str], EdgeResult] = {}
        update_lock = threading.RLock()
        stream_holder: dict[str, StreamBackedPolymarketClient] = {}

        def on_update(updated_token_ids: set[str]) -> None:
            with update_lock:
                stream_client = stream_holder.get("client")
                if stream_client is not None:
                    _evaluate_realtime_update(
                        updated_token_ids,
                        stream_client,
                        broker,
                        settings,
                        market_by_token,
                        signals_by_market,
                        market_types,
                        latest_edges,
                    )

        def build_stream() -> OrderBookMarketStream:
            return OrderBookMarketStream(
                settings.orderbook_stream_url,
                on_update=on_update,
                heartbeat_seconds=settings.orderbook_stream_heartbeat_seconds,
                reconnect_seconds=settings.orderbook_stream_reconnect_seconds,
                stale_seconds=settings.orderbook_stream_stale_seconds,
            )

        stream = build_stream()
        stream_holder["client"] = StreamBackedPolymarketClient(settings.gamma_base, settings.clob_base, stream)
        stream.start(market_by_token.keys())

        def write_stream_status(websocket_health: dict[str, object] | None = None) -> None:
            websocket_health = websocket_health or stream.health_snapshot()
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
                forecast=ensemble_client.health_snapshot(),
                websocket=websocket_health,
            )

        write_stream_status()
        status_updated_at = datetime.now(timezone.utc)
        try:
            while True:
                now = datetime.now(timezone.utc)
                elapsed = (now - refresh_started_at).total_seconds()
                if elapsed >= settings.forecast_refresh_interval_seconds:
                    break
                websocket_health = stream.health_snapshot()
                if _stream_should_rebuild(websocket_health, token_count=len(market_by_token)):
                    write_stream_status(websocket_health)
                    print(f"STREAM REBUILD {websocket_health.get('status_reason') or 'websocket thread stopped'}")
                    with update_lock:
                        stream.stop()
                        stream = build_stream()
                        stream_holder["client"] = StreamBackedPolymarketClient(settings.gamma_base, settings.clob_base, stream)
                        stream.start(market_by_token.keys())
                    status_updated_at = now
                if (now - status_updated_at).total_seconds() >= settings.runner_health_status_interval_seconds:
                    write_stream_status()
                    status_updated_at = now
                time.sleep(1)
        finally:
            stream.stop()


def run_forever(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    if not settings.orderbook_stream_enabled:
        raise RuntimeError("ORDERBOOK_STREAM_ENABLED=false disables the required real-time order-book stream.")
    run_realtime_forever(settings)


def main() -> None:
    run_forever(load_settings())


if __name__ == "__main__":
    main()
