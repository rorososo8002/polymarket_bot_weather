from __future__ import annotations

import time

from .config import Settings, load_settings
from .edge import executable_buy_price, max_absorbable_shares, no_net_edge, yes_net_edge
from .models import EdgeResult, MarketDecision, OrderBook, PaperPosition, RawMarket, WeatherSignal
from .paper import PaperBroker, maybe_close_positions, maybe_settle_resolved_positions
from .polymarket_client import PolymarketClient
from .probability import estimate_weather_probability
from .risk import fractional_kelly_binary


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
    if ask > 0.92 or ask < 0.08:
        return f"{side} liquidity filter: extreme ask={ask:.3f} outside 0.08~0.92 [{market_type}]"
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

    if side == "YES":
        edge = yes_net_edge(
            signal.p_true,
            p_exec,
            settings.estimated_fee_per_share,
            slip,
            settings.model_error_margin,
            settings.resolution_error_margin,
        )
        side_probability = signal.p_true
    else:
        edge = no_net_edge(
            signal.p_true,
            p_exec,
            settings.estimated_fee_per_share,
            slip,
            settings.model_error_margin,
            settings.resolution_error_margin,
        )
        side_probability = 1.0 - signal.p_true

    p_eff = p_exec + settings.estimated_fee_per_share
    size_usd = position_size_usd(
        side_probability,
        p_eff,
        settings,
        bankroll_before_entry,
        entry_fraction_override,
        net_edge=edge,
        min_edge=min_edge,
    )
    is_trade = edge > min_edge and size_usd >= settings.min_order_usd
    return EdgeResult(
        side=side if is_trade else "SKIP",
        p_true=signal.p_true,
        p_exec=p_exec,
        net_edge=edge,
        size_usd=size_usd if edge > min_edge else 0.0,
        size_shares=(size_usd / p_exec) if edge > min_edge and p_exec > 0 else 0.0,
        reason=f"{side} edge={edge:.4f}, p_exec_vwap={p_exec:.4f}, slip_audit={slip:.4f} [{market_type}]",
    )


def evaluate_market(
    market: RawMarket,
    signal: WeatherSignal,
    client: PolymarketClient,
    settings: Settings,
    bankroll_before_entry: float,
    market_type: str = "temperature",
) -> tuple[EdgeResult, dict[str, EdgeResult]]:
    """Evaluate live YES/NO books and return the best executable paper result."""
    min_confidence, min_edge, entry_fraction_override = _market_params(settings, market_type)

    if settings.require_parse_for_trade and signal.confidence < min_confidence:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, f"confidence too low: {signal.confidence:.2f} < {min_confidence:.2f} [{market_type}]")
        return result, {}

    if settings.require_date_hint_for_trade and signal.parsed is not None and signal.parsed.date_hint is None:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, f"date_hint=None: refusing undated market [{market_type}]")
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

    if best_result.net_edge <= min_edge or best_result.side == "SKIP":
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


def _market_type_from_signal(signal: WeatherSignal) -> str:
    if signal.parsed is not None and signal.parsed.variable == "precipitation":
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


def refresh_open_position_edges(
    broker: PaperBroker,
    client: PolymarketClient,
    settings: Settings,
    latest_edges: dict[tuple[str, str], EdgeResult],
    market_by_id: dict[str, RawMarket],
    probability_estimator=estimate_weather_probability,
) -> None:
    """Refresh model probability and edge for held positions missing from the scan."""
    for pos in broker.state.positions:
        key = (pos.market_id, pos.side)
        if key in latest_edges:
            continue
        market = market_by_id.get(pos.market_id) or _market_from_position(pos)
        signal = probability_estimator(pos.question, settings=settings)
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


def run_cycle(settings: Settings | None = None) -> list[MarketDecision]:
    settings = settings or load_settings()
    client = PolymarketClient(settings.gamma_base, settings.clob_base)
    broker = PaperBroker(settings)
    try:
        markets = client.discover_weather_markets(limit=settings.max_markets)
    except Exception as exc:  # noqa: BLE001
        print(f"DATA ERROR: could not fetch live Polymarket markets: {exc}")
        print("Check internet/DNS/VPN, then run live-paper-bot again.")
        return []

    market_by_id = {m.market_id: m for m in markets}
    decisions: list[MarketDecision] = []
    latest_edges: dict[tuple[str, str], EdgeResult] = {}

    print(
        f"\nLIVE PAPER CYCLE | markets={len(markets)} | "
        f"cash=${broker.state.cash_usd:.2f} | exposure=${broker.total_exposure():.2f} | "
        f"bankroll=${broker.current_bankroll_before_entry():.2f} | open_positions={len(broker.state.positions)}"
    )
    for market in markets:
        try:
            signal = estimate_weather_probability(market.question, settings=settings)
            bankroll_before = broker.current_bankroll_before_entry()
            market_type = _market_type_from_signal(signal)
            result, per_side = evaluate_market(market, signal, client, settings, bankroll_before, market_type)
            for side, edge_result in per_side.items():
                latest_edges[(market.market_id, side)] = edge_result
            decisions.append(MarketDecision(market=market, signal=signal, result=result))
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
            if result.side in {"YES", "NO"}:
                token_id = market.yes_token_id if result.side == "YES" else market.no_token_id
                if broker.has_any_position(market.market_id):
                    existing = [p.side for p in broker.state.positions if p.market_id == market.market_id]
                    print(f"SKIP hedge block: already holding {existing} in this market")
                elif token_id:
                    city = signal.parsed.city if signal.parsed is not None else ""
                    date_hint = signal.parsed.date_hint if signal.parsed is not None else ""
                    pos = broker.open_position(market, token_id, result, market_type, city=city or "", date_hint=date_hint or "")
                    if pos:
                        city_info = f" [{city}/{date_hint}]" if city else ""
                        print(f"PAPER OPENED {pos.side}: ${pos.cost_usd:.2f} at {pos.entry_price:.4f}, shares={pos.shares:.4f}{city_info}")
        except Exception as exc:  # noqa: BLE001
            print("-" * 100)
            print(f"Q: {market.question}")
            print(f"ERROR: {exc}")

    _hydrate_open_position_markets(client, broker, market_by_id)
    settlement_msgs = maybe_settle_resolved_positions(broker, market_by_id)
    for msg in settlement_msgs:
        print(msg)

    refresh_open_position_edges(broker, client, settings, latest_edges, market_by_id)
    close_msgs = maybe_close_positions(broker, client, market_by_id, latest_edges)
    for msg in close_msgs:
        print(msg)
    print(
        f"SUMMARY cash=${broker.state.cash_usd:.2f} realized_pnl=${broker.state.realized_pnl_usd:.2f} "
        f"exposure=${broker.total_exposure():.2f} bankroll=${broker.current_bankroll_before_entry():.2f}"
    )
    print(broker.stats_summary())
    return decisions


def run_forever(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    while True:
        run_cycle(settings)
        print(f"Sleeping {settings.scan_interval_seconds}s. Ctrl+C to stop.")
        time.sleep(settings.scan_interval_seconds)


def main() -> None:
    run_forever(load_settings())


if __name__ == "__main__":
    main()
