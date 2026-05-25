from pathlib import Path

from weather_bot.analyze_paper import build_report


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_build_report_summarizes_decisions_edge_buckets_and_resolved_brier(tmp_path):
    decisions = tmp_path / "paper_decisions.csv"
    trades = tmp_path / "paper_trades.csv"
    write(
        decisions,
        "\n".join(
            [
                "ts,market_id,slug,question,market_type,side,p_true,p_exec,net_edge,size_usd,size_shares,entry_fraction,probability_stop_threshold,model_fair_price,target_exit_price,market_heat_score,reason,note",
                "2026-01-01T00:00:00+00:00,m1,s,q1,temperature,YES,0.700000,0.500000,0.120000,50,100,,,,,,YES edge,",
                "2026-01-01T00:01:00+00:00,m2,s,q2,temperature,SKIP,0.520000,,0.010000,0,0,,,,,,confidence too low,",
            ]
        )
        + "\n",
    )
    write(
        trades,
        "\n".join(
            [
                "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason",
                "2026-01-02T00:00:00+00:00,CLOSE,m1,s,q1,temperature,YES,yes,100,1.0,50,resolved winner=YES",
            ]
        )
        + "\n",
    )

    report = build_report(decisions, trades)

    assert "decisions=2 entries=1 skips=1" in report
    assert "confidence too low: 1" in report
    assert "edge >= 10%: count=1 avg_p_true=0.700" in report
    assert "resolved_brier=0.0900 n=1" in report
