import inspect

import pytest

import weather_bot.edge as edge_module
from weather_bot.edge import no_net_edge, vwap_for_size, yes_net_edge
from weather_bot.models import OrderBook, OrderLevel


def test_vwap_for_size_single_level():
    levels = [OrderLevel(price=0.40, size=100)]
    assert vwap_for_size(levels, 10) == 0.40


def test_vwap_for_size_multi_level():
    levels = [OrderLevel(price=0.40, size=10), OrderLevel(price=0.45, size=10)]
    assert abs(vwap_for_size(levels, 20) - 0.425) < 1e-9


def test_vwap_insufficient_liquidity():
    levels = [OrderLevel(price=0.40, size=10)]
    assert vwap_for_size(levels, 11) is None


def test_yes_net_edge():
    edge = yes_net_edge(
        p_true=0.60,
        p_exec=0.50,
        fee_per_share=0.01,
        model_error_margin=0.02,
        resolution_error_margin=0.01,
    )
    assert abs(edge - 0.06) < 1e-9


def test_no_net_edge():
    edge = no_net_edge(
        p_true=0.40,
        p_exec_no=0.50,
        fee_per_share=0.01,
        model_error_margin=0.02,
        resolution_error_margin=0.01,
    )
    assert abs(edge - 0.06) < 1e-9


def test_edge_functions_do_not_accept_separate_slippage_parameter():
    assert "slippage" not in inspect.signature(yes_net_edge).parameters
    assert "slippage" not in inspect.signature(no_net_edge).parameters


def test_polymarket_weather_taker_fee_uses_official_curve():
    fee = edge_module.polymarket_taker_fee_usdc(shares=100, price=0.90, fee_rate=0.05)

    assert fee == 0.45


def test_polymarket_weather_taker_fee_rounds_usdc_to_five_places():
    fee = edge_module.polymarket_taker_fee_usdc(shares=1, price=0.333333, fee_rate=0.05)

    assert fee == 0.01111


def test_executable_net_return_rejects_thin_088_to_092_round_trip():
    estimate = edge_module.estimate_executable_net_return(
        shares=100,
        entry_vwap=0.88,
        expected_exit_price=0.92,
        expected_exit_spread=0.01,
        expected_exit_slippage=0.0,
        fee_rate=0.05,
    )

    assert estimate.expected_gross_profit_usdc == 4.0
    assert estimate.estimated_cost_usdc > 0
    assert estimate.expected_net_return_pct < 0.06


def test_executable_buy_price_can_use_fee_adjusted_all_in_budget():
    book = OrderBook(token_id="yes", bids=[], asks=[OrderLevel(price=0.50, size=19.6)])

    p_exec, shares, slippage = edge_module.executable_buy_price(book, 10.0, fee_rate=0.05)

    assert p_exec == 0.50
    assert shares == pytest.approx(10.0 / (0.50 + 0.05 * 0.50 * 0.50))
    assert slippage == 0.0


def test_executable_buy_price_without_fee_still_uses_gross_notional_budget():
    book = OrderBook(token_id="yes", bids=[], asks=[OrderLevel(price=0.50, size=19.2)])

    p_exec, shares, slippage = edge_module.executable_buy_price(book, 10.0)

    assert p_exec is None
    assert shares == 0.0
    assert slippage == 0.0


def test_executable_buy_price_solves_fee_budget_across_multiple_ask_levels():
    book = OrderBook(
        token_id="yes",
        bids=[],
        asks=[
            OrderLevel(price=0.50, size=10),
            OrderLevel(price=0.60, size=20),
        ],
    )

    p_exec, shares, slippage = edge_module.executable_buy_price(book, 10.0, fee_rate=0.05)

    assert p_exec is not None
    all_in_cost = shares * (p_exec + edge_module.polymarket_taker_fee_per_share(p_exec, 0.05))
    best_ask_only_shares = edge_module.fee_adjusted_entry_shares(10.0, 0.50, 0.05)
    assert all_in_cost == pytest.approx(10.0)
    assert shares < best_ask_only_shares
    assert p_exec > 0.50
    assert slippage == pytest.approx(p_exec - 0.50)
