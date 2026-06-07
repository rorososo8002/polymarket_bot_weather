from pathlib import Path

from weather_bot import analyze_paper
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
                "2026-01-01T00:01:00+00:00,m2,s,q2,temperature,SKIP_ERROR,0.520000,,0.010000,0,0,,,,,,SKIP_ERROR: market evaluation failed,",
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
    assert "SKIP_ERROR: 1" in report
    assert "edge >= 10%: count=1 avg_p_true=0.700" in report
    assert "resolved_brier=0.0900 n=1" in report


def test_build_report_tolerates_missing_side_column(tmp_path):
    decisions = tmp_path / "paper_decisions.csv"
    trades = tmp_path / "paper_trades.csv"
    write(
        decisions,
        "\n".join(
            [
                "ts,market_id,slug,question,market_type,p_true,p_exec,net_edge,size_usd,size_shares,entry_fraction,probability_stop_threshold,model_fair_price,target_exit_price,market_heat_score,reason,note",
                "2026-01-01T00:00:00+00:00,m1,s,q1,temperature,0.520000,,-999,0,0,,,,,,legacy row without side,",
                "2026-01-01T00:01:00+00:00,m2,s,q2,temperature,0.500000,,-999,0,0,,,,,,legacy row without side 2,",
            ]
        )
        + "\n",
    )
    write(
        trades,
        "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason\n",
    )

    report = build_report(decisions, trades)

    assert "decisions=2 entries=0 skips=0" in report


def test_resolved_brier_prefers_open_entry_probability_over_later_decision(tmp_path):
    decisions = tmp_path / "paper_decisions.csv"
    trades = tmp_path / "paper_trades.csv"
    write(
        decisions,
        "\n".join(
            [
                "ts,market_id,slug,question,market_type,side,p_true,p_exec,net_edge,size_usd,size_shares,entry_fraction,probability_stop_threshold,model_fair_price,target_exit_price,market_heat_score,reason,note",
                "2026-01-01T00:00:00+00:00,m1,s,q1,temperature,YES,0.700000,0.500000,0.120000,50,100,,,,,,YES edge,",
                "2026-01-01T01:00:00+00:00,m1,s,q1,temperature,YES,0.900000,0.500000,0.200000,50,100,,,,,,YES edge later,",
            ]
        )
        + "\n",
    )
    write(
        trades,
        "\n".join(
            [
                "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason,entry_p_true,entry_side_probability,entry_net_edge,decision_ts",
                "2026-01-01T00:00:02+00:00,OPEN,m1,s,q1,temperature,YES,yes,100,0.5,-50,entry,0.700000,0.700000,0.120000,2026-01-01T00:00:00+00:00",
                "2026-01-02T00:00:00+00:00,CLOSE,m1,s,q1,temperature,YES,yes,100,1.0,50,resolved winner=YES,,,,",
            ]
        )
        + "\n",
    )

    report = build_report(decisions, trades)

    assert "resolved_brier=0.0900 n=1" in report


def test_resolved_brier_falls_back_to_decision_probability_for_legacy_trades(tmp_path):
    decisions = tmp_path / "paper_decisions.csv"
    trades = tmp_path / "paper_trades.csv"
    write(
        decisions,
        "\n".join(
            [
                "ts,market_id,slug,question,market_type,side,p_true,p_exec,net_edge,size_usd,size_shares,entry_fraction,probability_stop_threshold,model_fair_price,target_exit_price,market_heat_score,reason,note",
                "2026-01-01T00:00:00+00:00,m1,s,q1,temperature,YES,0.700000,0.500000,0.120000,50,100,,,,,,YES edge,",
                "2026-01-01T01:00:00+00:00,m1,s,q1,temperature,YES,0.900000,0.500000,0.200000,50,100,,,,,,YES edge later,",
            ]
        )
        + "\n",
    )
    write(
        trades,
        "\n".join(
            [
                "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason",
                "2026-01-01T00:00:02+00:00,OPEN,m1,s,q1,temperature,YES,yes,100,0.5,-50,entry",
                "2026-01-02T00:00:00+00:00,CLOSE,m1,s,q1,temperature,YES,yes,100,1.0,50,resolved winner=YES",
            ]
        )
        + "\n",
    )

    report = build_report(decisions, trades)

    assert "resolved_brier=0.0100 n=1" in report


def test_build_report_streams_large_csv_readers_without_materializing_them(tmp_path, monkeypatch):
    decisions = tmp_path / "paper_decisions.csv"
    trades = tmp_path / "paper_trades.csv"
    decision_rows = [
        "ts,market_id,slug,question,market_type,side,p_true,p_exec,net_edge,size_usd,size_shares,entry_fraction,probability_stop_threshold,model_fair_price,target_exit_price,market_heat_score,reason,note"
    ]
    trade_rows = [
        "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason"
    ]
    for index in range(1_000):
        side = "YES" if index == 999 else "SKIP"
        p_true = "0.700000" if side == "YES" else "0.510000"
        reason = "YES edge" if side == "YES" else "confidence too low"
        decision_rows.append(
            f"2026-01-01T00:{index % 60:02d}:00+00:00,m{index},slug-{index},q,temperature,{side},{p_true},0.500000,0.120000,10,20,,,,,,{reason},"
        )
    trade_rows.append(
        "2026-01-02T00:00:00+00:00,CLOSE,m999,slug-999,q,temperature,YES,yes,20,1.0,10,resolved winner=YES"
    )
    write(decisions, "\n".join(decision_rows) + "\n")
    write(trades, "\n".join(trade_rows) + "\n")

    original_dict_reader = analyze_paper.csv.DictReader

    class GuardedDictReader:
        def __init__(self, *args, **kwargs):
            self._reader = original_dict_reader(*args, **kwargs)

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._reader)

        def __length_hint__(self):
            raise AssertionError("CSV readers must be streamed, not materialized with list().")

    monkeypatch.setattr(analyze_paper.csv, "DictReader", GuardedDictReader)

    report = build_report(decisions, trades)

    assert "decisions=1000 entries=1 skips=999 trades=1" in report
    assert "confidence too low: 999" in report
    assert "resolved_brier=0.0900 n=1" in report


def test_build_report_warns_on_close_without_open(tmp_path):
    decisions = tmp_path / "paper_decisions.csv"
    trades = tmp_path / "paper_trades.csv"
    write(
        decisions,
        "\n".join(
            [
                "ts,market_id,slug,question,market_type,side,p_true,p_exec,net_edge,size_usd,size_shares,entry_fraction,probability_stop_threshold,model_fair_price,target_exit_price,market_heat_score,reason,note",
                "2026-01-01T00:00:00+00:00,m1,s,q1,temperature,YES,0.700000,0.500000,0.120000,50,100,,,,,,YES edge,",
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

    assert "warnings:" in report
    assert "CLOSE without OPEN market_id=m1 side=YES token_id=yes" in report


def test_build_report_warns_when_decisions_file_is_missing(tmp_path):
    trades = tmp_path / "paper_trades.csv"
    write(
        trades,
        "\n".join(
            [
                "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason",
                "2026-01-01T00:00:00+00:00,OPEN,m1,s,q1,temperature,YES,yes,100,0.5,-50,entry",
            ]
        )
        + "\n",
    )

    report = build_report(tmp_path / "missing_decisions.csv", trades)

    assert "warnings:" in report
    assert "paper_decisions.csv is missing" in report


def test_build_report_warns_on_duplicate_open_trade(tmp_path):
    decisions = tmp_path / "paper_decisions.csv"
    trades = tmp_path / "paper_trades.csv"
    write(
        decisions,
        "ts,market_id,slug,question,market_type,side,p_true,p_exec,net_edge,size_usd,size_shares,entry_fraction,probability_stop_threshold,model_fair_price,target_exit_price,market_heat_score,reason,note\n",
    )
    write(
        trades,
        "\n".join(
            [
                "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason",
                "2026-01-01T00:00:00+00:00,OPEN,m1,s,q1,temperature,YES,yes,100,0.5,-50,entry",
                "2026-01-01T00:00:01+00:00,OPEN,m1,s,q1,temperature,YES,yes,100,0.5,-50,entry duplicate",
            ]
        )
        + "\n",
    )

    report = build_report(decisions, trades)

    assert "warnings:" in report
    assert "duplicate OPEN market_id=m1 side=YES token_id=yes" in report
