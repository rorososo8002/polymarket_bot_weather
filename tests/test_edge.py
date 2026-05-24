from weather_bot.edge import no_net_edge, vwap_for_size, yes_net_edge
from weather_bot.models import OrderLevel


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
        slippage=0.01,
        model_error_margin=0.02,
        resolution_error_margin=0.01,
    )
    assert abs(edge - 0.06) < 1e-9


def test_no_net_edge():
    edge = no_net_edge(
        p_true=0.40,
        p_exec_no=0.50,
        fee_per_share=0.01,
        slippage=0.01,
        model_error_margin=0.02,
        resolution_error_margin=0.01,
    )
    assert abs(edge - 0.06) < 1e-9
