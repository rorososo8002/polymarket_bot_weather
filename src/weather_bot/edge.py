from __future__ import annotations

from .models import OrderBook, OrderLevel


def clamp_probability(x: float) -> float:
    return max(0.0, min(1.0, x))


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


def executable_buy_price(book: OrderBook, target_usd: float) -> tuple[float | None, float, float]:
    """Estimate VWAP execution price, shares, and slippage for a target USD order.

    Uses best ask to estimate shares first, then computes VWAP over ask levels.
    """
    if target_usd <= 0:
        raise ValueError("target_usd must be positive")
    best_ask = book.best_ask
    if best_ask is None or best_ask <= 0:
        return None, 0.0, 0.0
    shares = target_usd / best_ask
    p_exec = vwap_for_size(book.asks, shares)
    if p_exec is None:
        return None, 0.0, 0.0
    slippage = max(0.0, p_exec - best_ask)
    return p_exec, shares, slippage


def executable_sell_price(book: OrderBook, shares: float) -> tuple[float | None, float]:
    """Estimate VWAP exit price and slippage for selling `shares` against bid levels.

    진입(executable_buy_price)과 대칭 구조: bid 호가창 전체 깊이를 VWAP으로 계산.
    보유 물량이 최우선 매수 호가 잔량보다 클 경우 슬리피지가 발생하며,
    이를 가상매매 단계에서도 현실적으로 반영한다.

    Returns:
        (vwap_price, slippage): 유동성 부족 시 vwap_price는 None.
    """
    if shares <= 0:
        raise ValueError("shares must be positive")
    best_bid = book.best_bid
    if best_bid is None or best_bid <= 0:
        return None, 0.0
    p_exec = vwap_for_size(book.bids, shares)
    if p_exec is None:
        # Bid 호가창 유동성이 전체 물량을 소화 못할 경우
        return None, 0.0
    slippage = max(0.0, best_bid - p_exec)
    return p_exec, slippage


def max_absorbable_shares(levels: list[OrderLevel], min_price: float = 0.01) -> float:
    """Bid 호가창에서 min_price 이상 가격대의 총 소화 가능 수량을 반환한다.

    보유 물량(pos.shares)이 이 값을 초과하면 전량 청산이 불가능하다.
    maybe_close_positions에서 부분 청산(Partial Close) 여부를 판단할 때 사용한다.

    Args:
        levels: 호가창 Bid 레벨 목록 (price 내림차순 정렬 권장)
        min_price: 이 가격 미만의 호가는 무시 (기본값 0.01 = 1센트 이하 쓰레기 호가 제외)

    Returns:
        float: 소화 가능한 총 수량 (0이면 사실상 유동성 없음)
    """
    return sum(level.size for level in levels if level.price >= min_price)


def yes_net_edge(
    p_true: float,
    p_exec: float,
    fee_per_share: float,
    slippage: float,
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
    slippage: float,
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
