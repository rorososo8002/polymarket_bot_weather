from datetime import datetime, timezone

from weather_bot.config import Settings
from weather_bot.risk import (
    confidence_size_multiplier,
    drawdown_entry_block_reason,
    fractional_kelly_binary,
    shrink_probability,
)


def test_shrink_probability_toward_half():
    assert abs(shrink_probability(0.80, gamma=0.5) - 0.65) < 1e-9
    assert abs(shrink_probability(0.20, gamma=0.5) - 0.35) < 1e-9


def test_fractional_kelly_positive_edge():
    f = fractional_kelly_binary(
        p_true=0.70,
        p_eff=0.50,
        fractional_kelly=0.10,
        max_fraction=0.03,
        gamma=1.0,
    )
    assert 0 < f <= 0.03


def test_fractional_kelly_negative_edge_zero():
    f = fractional_kelly_binary(
        p_true=0.45,
        p_eff=0.50,
        fractional_kelly=0.10,
        max_fraction=0.03,
        gamma=1.0,
    )
    assert f == 0.0


def test_confidence_size_multiplier_scales_from_floor_to_full_size():
    assert confidence_size_multiplier(1.0, min_confidence=0.50, floor=0.25) == 1.0
    assert confidence_size_multiplier(0.50, min_confidence=0.50, floor=0.25) == 0.25
    assert confidence_size_multiplier(0.75, min_confidence=0.50, floor=0.25) == 0.625
    assert confidence_size_multiplier(0.49, min_confidence=0.50, floor=0.25) == 0.0


def test_drawdown_entry_block_reason_uses_today_realized_loss(tmp_path):
    trades_path = tmp_path / "trades.csv"
    trades_path.write_text(
        "\n".join(
            [
                "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason,city,event_date_local",
                "2026-06-14T00:00:00+00:00,CLOSE,m1,s,q,temperature,YES,yes,10,0.40,-1.00,loss,seoul,2026-06-14",
            ]
        ),
        encoding="utf-8",
    )
    settings = Settings(
        bankroll_usd=100.0,
        trades_csv_path=str(trades_path),
        daily_realized_loss_limit_fraction=0.005,
    )

    reason = drawdown_entry_block_reason(settings, [], now=datetime(2026, 6, 14, tzinfo=timezone.utc))

    assert "DAILY_LOSS_LIMIT_HIT" in reason
    assert "realized_loss=$1.00" in reason
