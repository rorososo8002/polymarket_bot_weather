from datetime import datetime, timezone

from weather_bot.config import Settings
from weather_bot.exit_policy import assess_exit, build_entry_plan, model_fair_price, target_exit_price
from weather_bot.models import EdgeResult, PaperPosition


def test_entry_plan_records_probability_stop_threshold():
    settings = Settings(bankroll_usd=100, entry_fraction=0.05, max_single_market_fraction=0.05)
    result = EdgeResult("YES", 0.68, 0.52, 0.11, 5.0, 9.615, "test")
    plan = build_entry_plan(result, 100.0, settings)
    assert round(plan.entry_fraction, 4) == 0.05
    assert round(plan.probability_stop_threshold, 4) == 0.58
    assert plan.model_fair_price > result.p_exec
    assert plan.target_exit_price > result.p_exec
    assert plan.target_exit_price < plan.model_fair_price


def test_take_profit_when_mark_reaches_model_target():
    settings = Settings(min_profit_pct=0.03)
    pos = PaperPosition(
        position_id="p1",
        market_id="m1",
        question="Will Seoul reach 21C?",
        token_id="t1",
        side="YES",
        entry_price=0.52,
        shares=10,
        cost_usd=5.2,
        opened_at=datetime.now(timezone.utc).isoformat(),
        metadata={"entry_p_true": 0.68, "probability_stop_threshold": 0.58},
    )
    edge = EdgeResult("YES", 0.68, 0.60, 0.03, 5, 10, "latest")
    fair = model_fair_price("YES", 0.68, settings)
    target = target_exit_price(0.52, fair, settings)
    assessment = assess_exit(pos, target, edge, settings, 1.0)
    assert assessment.should_close
    assert "model target" in assessment.reason


def test_probability_stop_closes_when_model_probability_drops():
    settings = Settings(probability_stop_drop_threshold=0.10)
    pos = PaperPosition(
        position_id="p1",
        market_id="m1",
        question="q",
        token_id="t1",
        side="YES",
        entry_price=0.50,
        shares=10,
        cost_usd=5,
        opened_at=datetime.now(timezone.utc).isoformat(),
        metadata={"entry_p_true": 0.70, "probability_stop_threshold": 0.60},
    )
    latest_edge = EdgeResult("YES", 0.59, 0.50, -0.01, 0, 0, "latest")
    assessment = assess_exit(pos, 0.50, latest_edge, settings, 1.0)
    assert assessment.should_close
    assert "probability stop" in assessment.reason


def test_invalid_edge_sentinel_does_not_trigger_edge_fade_exit():
    settings = Settings(exit_net_edge=0.0)
    pos = PaperPosition(
        position_id="p1",
        market_id="m1",
        question="Will Seoul reach 25C?",
        token_id="t1",
        side="NO",
        entry_price=0.50,
        shares=100,
        cost_usd=50,
        opened_at=datetime.now(timezone.utc).isoformat(),
        metadata={"entry_p_true": 0.20, "probability_stop_threshold": 0.70},
    )
    invalid_edge = EdgeResult("SKIP", 0.20, None, -999.0, 0, 0, "No valid side evaluated.")

    assessment = assess_exit(pos, 0.501, invalid_edge, settings, 1.0)

    assert not assessment.should_close
    assert "hold" in assessment.reason
