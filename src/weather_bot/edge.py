from __future__ import annotations

from dataclasses import dataclass

from .models import OrderBook, OrderLevel


WEATHER_TAKER_FEE_RATE = 0.05


@dataclass(frozen=True)
class ExecutableNetReturnEstimate:
    route: str
    shares: float
    entry_vwap: float
    expected_exit_price: float
    executable_exit_price: float
    expected_gross_profit_usdc: float
    entry_fee_usdc: float
    exit_fee_usdc: float
    exit_market_cost_usdc: float
    estimated_cost_usdc: float
    expected_net_profit_usdc: float
    expected_net_return_pct: float


def clamp_probability(x: float) -> float:
    return max(0.0, min(1.0, x))


def polymarket_taker_fee_usdc(shares: float, price: float, fee_rate: float = WEATHER_TAKER_FEE_RATE) -> float:
    """Calculate the Polymarket taker fee for one matched trade.

    Official formula: shares * fee_rate * price * (1 - price).
    Polymarket rounds the USDC fee to five decimal places.
    """
    if shares < 0:
        raise ValueError("shares must be non-negative")
    if not 0.0 <= price <= 1.0:
        raise ValueError("price must be between 0 and 1")
    if fee_rate < 0:
        raise ValueError("fee_rate must be non-negative")
    return round(shares * fee_rate * price * (1.0 - price), 5)


def polymarket_taker_fee_per_share(price: float, fee_rate: float = WEATHER_TAKER_FEE_RATE) -> float:
    """Return the unrounded per-share fee for edge calculations."""
    if not 0.0 <= price <= 1.0:
        raise ValueError("price must be between 0 and 1")
    if fee_rate < 0:
        raise ValueError("fee_rate must be non-negative")
    return fee_rate * price * (1.0 - price)


def fee_adjusted_entry_shares(size_usd: float, price: float, fee_rate: float = WEATHER_TAKER_FEE_RATE) -> float:
    """Return shares bought when `size_usd` is the all-in entry budget."""
    if size_usd < 0:
        raise ValueError("size_usd must be non-negative")
    fee_per_share = polymarket_taker_fee_per_share(price, fee_rate)
    unit_cost = price + fee_per_share
    if size_usd == 0 or unit_cost <= 0:
        return 0.0
    return size_usd / unit_cost


def estimate_executable_net_return(
    *,
    shares: float,
    entry_vwap: float,
    expected_exit_price: float,
    expected_exit_spread: float = 0.0,
    expected_exit_slippage: float = 0.0,
    fee_rate: float = WEATHER_TAKER_FEE_RATE,
    hold_to_settlement: bool = False,
) -> ExecutableNetReturnEstimate:
    """Estimate net return after executable entry and conservative exit costs.

    `entry_vwap` already includes entry spread and slippage. For an early exit,
    the current spread and observed slippage are used as a conservative future
    exit haircut. Settlement has no order-book exit or exit taker fee.
    """
    if shares <= 0:
        raise ValueError("shares must be positive")
    if not 0.0 <= entry_vwap <= 1.0:
        raise ValueError("entry_vwap must be between 0 and 1")
    if not 0.0 <= expected_exit_price <= 1.0:
        raise ValueError("expected_exit_price must be between 0 and 1")
    if expected_exit_spread < 0 or expected_exit_slippage < 0:
        raise ValueError("expected exit costs must be non-negative")

    if hold_to_settlement:
        route = "settlement"
        executable_exit_price = expected_exit_price
        exit_market_cost_usdc = 0.0
        exit_fee_usdc = 0.0
    else:
        route = "expected-exit"
        exit_haircut = expected_exit_spread + expected_exit_slippage
        executable_exit_price = max(0.0, expected_exit_price - exit_haircut)
        exit_market_cost_usdc = shares * (expected_exit_price - executable_exit_price)
        exit_fee_usdc = polymarket_taker_fee_usdc(shares, executable_exit_price, fee_rate)

    expected_gross_profit_usdc = shares * (expected_exit_price - entry_vwap)
    entry_fee_usdc = polymarket_taker_fee_usdc(shares, entry_vwap, fee_rate)
    estimated_cost_usdc = entry_fee_usdc + exit_fee_usdc + exit_market_cost_usdc
    expected_net_profit_usdc = expected_gross_profit_usdc - estimated_cost_usdc
    invested_usdc = shares * entry_vwap + entry_fee_usdc
    expected_net_return_pct = expected_net_profit_usdc / invested_usdc if invested_usdc > 0 else -1.0

    return ExecutableNetReturnEstimate(
        route=route,
        shares=shares,
        entry_vwap=entry_vwap,
        expected_exit_price=expected_exit_price,
        executable_exit_price=executable_exit_price,
        expected_gross_profit_usdc=round(expected_gross_profit_usdc, 5),
        entry_fee_usdc=entry_fee_usdc,
        exit_fee_usdc=exit_fee_usdc,
        exit_market_cost_usdc=round(exit_market_cost_usdc, 5),
        estimated_cost_usdc=round(estimated_cost_usdc, 5),
        expected_net_profit_usdc=round(expected_net_profit_usdc, 5),
        expected_net_return_pct=expected_net_return_pct,
    )


def vwap_for_size(levels: list[OrderLevel], shares: float) -> float | None:
    """Return average fill price for buying `shares` against ask levels.

    If available liquidity is insufficient, returns None.
    """
    if shares <= 0:
        raise ValueError("shares must be positive")
    remaining = shares
    notional = 0.0
    for level in levels:
        take = min(remaining, level.size)
        notional += take * level.price
        remaining -= take
        if remaining <= 1e-12:
            return notional / shares
    return None


def _best_executable_price(levels: list[OrderLevel]) -> float | None:
    for level in levels:
        if level.size > 0:
            return level.price
    return None


def _all_in_buy_cost(shares: float, levels: list[OrderLevel], fee_rate: float) -> tuple[float, float] | None:
    p_exec = vwap_for_size(levels, shares)
    if p_exec is None:
        return None
    return shares * (p_exec + polymarket_taker_fee_per_share(p_exec, fee_rate)), p_exec


def executable_buy_price(book: OrderBook, target_usd: float, fee_rate: float = 0.0) -> tuple[float | None, float, float]:
    """Estimate VWAP execution price, shares, and slippage for a target USD order.

    When `fee_rate` is provided, `target_usd` is treated as the all-in paper
    budget, so the initial share estimate includes expected entry fees.
    """
    if target_usd <= 0:
        raise ValueError("target_usd must be positive")
    if fee_rate < 0:
        raise ValueError("fee_rate must be non-negative")
    best_ask = _best_executable_price(book.asks)
    if best_ask is None or best_ask <= 0:
        return None, 0.0, 0.0
    available_shares = sum(level.size for level in book.asks if level.size > 0)
    if available_shares <= 0:
        return None, 0.0, 0.0
    max_cost = _all_in_buy_cost(available_shares, book.asks, fee_rate)
    if max_cost is None or max_cost[0] + 1e-9 < target_usd:
        return None, 0.0, 0.0
    low = 0.0
    high = available_shares
    p_exec = max_cost[1]
    for _ in range(64):
        mid = (low + high) / 2.0
        if mid <= 0:
            break
        cost = _all_in_buy_cost(mid, book.asks, fee_rate)
        if cost is None:
            high = mid
            continue
        if cost[0] <= target_usd:
            low = mid
            p_exec = cost[1]
        else:
            high = mid
    shares = low
    if shares <= 0:
        return None, 0.0, 0.0
    slippage = max(0.0, p_exec - best_ask)
    return p_exec, shares, slippage


def executable_sell_price(book: OrderBook, shares: float) -> tuple[float | None, float]:
    """Estimate VWAP exit price and slippage for selling `shares` against bid levels.

    This is the exit-side mirror of executable_buy_price. It calculates VWAP
    across the available bid-depth. If the held shares exceed the best-bid
    depth, the resulting slippage is reflected even in paper trading.

    Returns:
        (vwap_price, slippage). vwap_price is None when liquidity is insufficient.
    """
    if shares <= 0:
        raise ValueError("shares must be positive")
    best_bid = _best_executable_price(book.bids)
    if best_bid is None or best_bid <= 0:
        return None, 0.0
    p_exec = vwap_for_size(book.bids, shares)
    if p_exec is None:
        # The bid book cannot absorb the full share amount.
        return None, 0.0
    slippage = max(0.0, best_bid - p_exec)
    return p_exec, slippage


def max_absorbable_shares(levels: list[OrderLevel], min_price: float = 0.01) -> float:
    """Return total bid-side shares available at or above `min_price`.

    If a position's shares exceed this value, a full close is not executable.
    maybe_close_positions uses this to decide whether a partial close is needed.

    Args:
        levels: Bid book levels, preferably sorted by descending price.
        min_price: Ignore bids below this price. The default excludes dust bids
            at one cent or below.

    Returns:
        Total absorbable shares. Zero means there is effectively no liquidity.
    """
    return sum(level.size for level in levels if level.price >= min_price)


def yes_net_edge(
    p_true: float,
    p_exec: float,
    fee_per_share: float,
    model_error_margin: float,
    resolution_error_margin: float,
) -> float:
    return (
        clamp_probability(p_true)
        - p_exec
        - fee_per_share
        - model_error_margin
        - resolution_error_margin
    )


def no_net_edge(
    p_true: float,
    p_exec_no: float,
    fee_per_share: float,
    model_error_margin: float,
    resolution_error_margin: float,
) -> float:
    return (
        1.0
        - clamp_probability(p_true)
        - p_exec_no
        - fee_per_share
        - model_error_margin
        - resolution_error_margin
    )
