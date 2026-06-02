from __future__ import annotations

import csv
import json

import pytest

from weather_bot import dashboard as dashboard_module
from weather_bot.config import Settings
from weather_bot.dashboard import HTML, _read_csv, build_dashboard_payload


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_read_csv_uses_tail_without_loading_entire_file(tmp_path):
    path = tmp_path / "large.csv"
    rows = [{"ts": f"2026-05-24T10:{idx:02d}:00+00:00", "side": "SKIP", "note": str(idx)} for idx in range(120)]
    write_csv(path, rows)

    tail = _read_csv(path, limit=3)

    assert [row["note"] for row in tail] == ["117", "118", "119"]


def test_dashboard_payload_exposes_latest_event_portfolio_explanation(tmp_path):
    state_path = tmp_path / "state.json"
    portfolios_path = tmp_path / "portfolios.jsonl"
    state_path.write_text(json.dumps({"cash_usd": 100.0, "positions": []}), encoding="utf-8")
    portfolios_path.write_text(
        json.dumps(
            {
                "ts": "2026-06-01T01:00:00+00:00",
                "event_key": "seoul-may-25",
                "entry_bankroll_usd": 100.0,
                "event_cap_fraction": 0.10,
                "event_cap_usd": 10.0,
                "existing_event_exposure_usd": 0.0,
                "selected_exposure_usd": 10.0,
                "expected_net_profit_usd": 2.0,
                "selected_legs": [
                    {"market_id": "seoul-26", "side": "YES", "size_usd": 5.0},
                    {"market_id": "seoul-27", "side": "YES", "size_usd": 5.0},
                ],
                "rejected_legs": [{"market_id": "seoul-28", "side": "NO", "reason": "same-direction concentration"}],
                "scenario_pnl_usd": {"none_selected_legs_win": -10.0},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_dashboard_payload(
        Settings(
            bankroll_usd=100.0,
            state_path=str(state_path),
            portfolio_decisions_jsonl_path=str(portfolios_path),
        )
    )

    latest = payload["scanner"]["latest_event_portfolio"]
    assert latest["event_key"] == "seoul-may-25"
    assert latest["entry_bankroll_usd"] == 100.0
    assert latest["event_cap_fraction"] == 0.10
    assert [leg["market_id"] for leg in latest["selected_legs"]] == ["seoul-26", "seoul-27"]
    assert latest["rejected_legs"][0]["market_id"] == "seoul-28"


def test_dashboard_html_explains_adaptive_event_portfolio_budget():
    assert "Event Portfolio" in HTML
    assert "Reference bankroll" in HTML
    assert "$1,000" in HTML
    assert "max 2 legs" in HTML
    assert "Minimum $10" in HTML
    assert "city total 20%" in HTML
    assert "total open 90%" in HTML
    assert "YES+NO" in HTML
    assert "NO+NO" in HTML
    assert "expected log growth" in HTML


def test_dashboard_payload_summarizes_state_trades_and_decisions(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    raw_path = tmp_path / "raw.jsonl"
    state_path.write_text(
        json.dumps(
            {
                "cash_usd": 950.0,
                "realized_pnl_usd": 0.0,
                "positions": [
                    {
                        "position_id": "p1",
                        "market_id": "m1",
                        "question": "Seoul 2025.05.24 21C or higher?",
                        "token_id": "yes",
                        "side": "YES",
                        "entry_price": 0.5,
                        "shares": 100.0,
                        "cost_usd": 50.0,
                        "opened_at": "2026-05-24T10:00:00+00:00",
                        "last_mark_price": 0.6,
                        "metadata": {"city": "seoul", "date_hint": "may 24"},
                    }
                ],
                "stats": {"temperature": {"wins": 1, "losses": 1, "pnl": 4.0}},
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-24T10:00:00+00:00",
                "action": "OPEN",
                "market_id": "m1",
                "slug": "seoul-21c",
                "question": "Seoul 2025.05.24 21C or higher?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "100",
                "price": "0.5",
                "cash_delta_or_pnl": "-50",
                "reason": "entry",
            },
            {
                "ts": "2026-05-24T11:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m2",
                "slug": "nyc-90f",
                "question": "NYC 90F?",
                "market_type": "temperature",
                "side": "NO",
                "token_id": "no",
                "shares": "20",
                "price": "0.7",
                "cash_delta_or_pnl": "4",
                "reason": "take profit",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-24T10:00:00+00:00",
                "market_id": "m1",
                "slug": "seoul-21c",
                "question": "Seoul 2025.05.24 21C or higher?",
                "market_type": "temperature",
                "side": "YES",
                "p_true": "0.7",
                "p_exec": "0.5",
                "net_edge": "0.1",
                "size_usd": "50",
                "size_shares": "100",
                "entry_fraction": "0.05",
                "probability_stop_threshold": "0.60",
                "model_fair_price": "0.64",
                "target_exit_price": "0.60",
                "market_heat_score": "-0.1",
                "reason": "edge ok",
                "note": "station",
            },
            {
                "ts": "2026-05-24T10:01:00+00:00",
                "market_id": "m3",
                "slug": "skip",
                "question": "Bad market",
                "market_type": "temperature",
                "side": "SKIP",
                "p_true": "0.5",
                "p_exec": "",
                "net_edge": "-999",
                "size_usd": "0",
                "size_shares": "0",
                "entry_fraction": "",
                "probability_stop_threshold": "",
                "model_fair_price": "",
                "target_exit_price": "",
                "market_heat_score": "",
                "reason": "confidence too low",
                "note": "Ensemble forecast unavailable: rate limited",
            },
        ],
    )
    settings = Settings(
        bankroll_usd=1000.0,
        state_path=str(state_path),
        trades_csv_path=str(trades_path),
        decisions_csv_path=str(decisions_path),
        raw_snapshots_path=str(raw_path),
    )

    payload = build_dashboard_payload(settings, auth_required=True)

    assert payload["security"]["auth_required"] is True
    assert payload["summary"]["cash"] == 950.0
    assert payload["summary"]["market_value"] == pytest.approx(58.8)
    assert payload["summary"]["equity"] == pytest.approx(1008.8)
    assert payload["summary"]["total_pnl"] == pytest.approx(8.8)
    assert payload["summary"]["wins"] == 1
    assert payload["summary"]["losses"] == 1
    assert payload["scanner"]["decisions"] == 2
    assert payload["scanner"]["forecast_unavailable"] == 1
    assert payload["scanner"]["skips"] == 1
    assert payload["scanner"]["entries"] == 1
    assert payload["bot"]["last_event_at"] == "2026-05-24T11:00:00+00:00"
    assert payload["bot"]["scan_interval_seconds"] == 1800
    assert payload["bot"]["orderbook_mode"] == "websocket"
    assert payload["positions"][0]["unrealized_pnl"] == pytest.approx(8.8)
    assert "events" not in payload
    assert "recent_decisions" not in payload
    assert "pressure" not in payload
    assert "realized_results" in payload


def test_dashboard_scanner_counts_all_decisions_not_just_recent_tail(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    raw_path = tmp_path / "raw.jsonl"
    state_path.write_text(json.dumps({"cash_usd": 1000.0, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-24T10:00:00+00:00",
                "action": "OPEN",
                "market_id": "m1",
                "slug": "seed",
                "question": "Seed trade",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "1",
                "price": "0.5",
                "cash_delta_or_pnl": "-0.5",
                "reason": "seed",
            },
        ],
    )
    rows = []
    for idx in range(805):
        rows.append(
            {
                "ts": f"2026-05-24T10:{idx % 60:02d}:00+00:00",
                "market_id": f"skip-{idx}",
                "slug": "skip",
                "question": "Skipped market",
                "market_type": "temperature",
                "side": "SKIP",
                "p_true": "0.5",
                "p_exec": "",
                "net_edge": "-999",
                "size_usd": "0",
                "size_shares": "0",
                "entry_fraction": "",
                "probability_stop_threshold": "",
                "model_fair_price": "",
                "target_exit_price": "",
                "market_heat_score": "",
                "reason": "edge below",
                "note": "",
            }
        )
    rows.append(
        {
            "ts": "2026-05-24T11:00:00+00:00",
            "market_id": "entry-1",
            "slug": "yes",
            "question": "Entry market",
            "market_type": "temperature",
            "side": "YES",
            "p_true": "0.7",
            "p_exec": "0.5",
            "net_edge": "0.1",
            "size_usd": "50",
            "size_shares": "100",
            "entry_fraction": "0.05",
            "probability_stop_threshold": "0.6",
            "model_fair_price": "0.64",
            "target_exit_price": "0.60",
            "market_heat_score": "-0.1",
            "reason": "edge ok",
            "note": "",
        }
    )
    write_csv(decisions_path, rows)
    settings = Settings(
        state_path=str(state_path),
        trades_csv_path=str(trades_path),
        decisions_csv_path=str(decisions_path),
        raw_snapshots_path=str(raw_path),
    )

    payload = build_dashboard_payload(settings)

    assert payload["scanner"]["decisions"] == 806
    assert payload["scanner"]["skips"] == 805
    assert payload["scanner"]["entries"] == 1
    assert "recent_decisions" not in payload


def test_dashboard_scanner_totals_include_appended_decisions(tmp_path):
    state_path = tmp_path / "state.json"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 1000.0, "positions": []}), encoding="utf-8")
    rows = [
        {
            "ts": "2026-05-24T10:00:00+00:00",
            "side": "SKIP",
            "reason": "edge below",
            "note": "",
        }
    ]
    write_csv(decisions_path, rows)
    settings = Settings(state_path=str(state_path), decisions_csv_path=str(decisions_path))

    first_payload = build_dashboard_payload(settings)

    with decisions_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writerow(
            {
                "ts": "2026-05-24T10:01:00+00:00",
                "side": "YES",
                "reason": "edge ok",
                "note": "no forecast fallback available",
            }
        )
    second_payload = build_dashboard_payload(settings)

    assert first_payload["scanner"]["decisions"] == 1
    assert second_payload["scanner"]["decisions"] == 2
    assert second_payload["scanner"]["skips"] == 1
    assert second_payload["scanner"]["entries"] == 1
    assert second_payload["scanner"]["forecast_unavailable"] == 1


def test_dashboard_large_decision_file_skips_initial_full_scan(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 1000.0, "positions": []}), encoding="utf-8")
    write_csv(
        decisions_path,
        [
            {"ts": "2026-05-24T10:00:00+00:00", "side": "SKIP", "reason": "edge below", "note": ""},
            {"ts": "2026-05-24T10:01:00+00:00", "side": "YES", "reason": "edge ok", "note": ""},
        ],
    )

    def fail_full_scan(*_args, **_kwargs):
        raise AssertionError("large decision files should not block the dashboard with a full scan")

    monkeypatch.setattr(dashboard_module, "MAX_INITIAL_DECISION_TOTAL_SCAN_BYTES", 1)
    monkeypatch.setattr(dashboard_module, "_scan_decision_totals", fail_full_scan)

    payload = build_dashboard_payload(Settings(state_path=str(state_path), decisions_csv_path=str(decisions_path)))

    assert payload["scanner"]["decisions"] == 2
    assert payload["scanner"]["skips"] == 1
    assert payload["scanner"]["entries"] == 1


def test_dashboard_scanner_distinguishes_entry_signals_from_actual_opens(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 950.0, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-24T10:00:00+00:00",
                "action": "OPEN",
                "market_id": "m1",
                "slug": "held",
                "question": "Held market",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "100",
                "price": "0.5",
                "cash_delta_or_pnl": "-50",
                "reason": "entry",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": f"2026-05-24T10:0{idx}:00+00:00",
                "market_id": "m1",
                "slug": "held",
                "question": "Held market",
                "market_type": "temperature",
                "side": "YES",
                "p_true": "0.7",
                "p_exec": "0.5",
                "net_edge": "0.1",
                "size_usd": "50",
                "size_shares": "100",
                "entry_fraction": "0.05",
                "probability_stop_threshold": "0.6",
                "model_fair_price": "0.64",
                "target_exit_price": "0.60",
                "market_heat_score": "-0.1",
                "reason": "edge ok",
                "note": "",
            }
            for idx in range(3)
        ],
    )
    settings = Settings(
        state_path=str(state_path),
        trades_csv_path=str(trades_path),
        decisions_csv_path=str(decisions_path),
    )

    payload = build_dashboard_payload(settings)

    assert payload["scanner"]["entries"] == 3
    assert payload["scanner"]["entry_signals"] == 3
    assert payload["scanner"]["actual_opens"] == 1


def test_dashboard_payload_builds_realized_trade_rows_for_operator_table(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 1035.0, "realized_pnl_usd": 14.0, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-29T10:00:00+00:00",
                "action": "OPEN",
                "market_id": "m-seoul",
                "slug": "seoul-27c",
                "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "100",
                "price": "0.21",
                "cash_delta_or_pnl": "-21",
                "reason": "target_exit=0.320",
            },
            {
                "ts": "2026-05-29T11:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m-seoul",
                "slug": "seoul-27c",
                "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "100",
                "price": "0.35",
                "cash_delta_or_pnl": "14",
                "reason": "take profit",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-29T10:00:01+00:00",
                "market_id": "m-seoul",
                "slug": "seoul-27c",
                "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "p_true": "0.70",
                "p_exec": "0.21",
                "net_edge": "0.20",
                "size_usd": "21",
                "size_shares": "100",
                "entry_fraction": "0.02",
                "probability_stop_threshold": "0.60",
                "model_fair_price": "0.40",
                "target_exit_price": "0.32",
                "market_heat_score": "0.1",
                "reason": "edge ok",
                "note": "station target_date=2026-05-29; >=27.0C/80.6F; members=82; vote=0.70; mean=86.0F; spread=2.0F",
            }
        ],
    )

    payload = build_dashboard_payload(
        Settings(state_path=str(state_path), trades_csv_path=str(trades_path), decisions_csv_path=str(decisions_path))
    )

    realized = payload["realized_results"][0]
    assert realized["date_hint"] == "may 29"
    assert realized["city"] == "seoul"
    assert realized["forecast_c"] == 30.0
    assert realized["threshold_c"] == 27.0
    assert realized["condition_label"] == "or higher"
    assert realized["expected_exit_price"] == 0.32
    assert realized["entry_price"] == 0.21
    assert realized["exit_price"] == 0.35
    assert realized["pnl"] == 14.0
    assert round(realized["roi"], 4) == round(14.0 / 21.0, 4)


def test_dashboard_realized_rows_are_latest_first_and_numeric_when_history_is_sparse(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 1008.0, "realized_pnl_usd": 8.0, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-29T11:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m-old",
                "slug": "old",
                "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "10",
                "price": "0.60",
                "cash_delta_or_pnl": "3",
                "reason": "take profit",
            },
            {
                "ts": "2026-05-30T11:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m-new",
                "slug": "new",
                "question": "Will the highest temperature in Seoul be 29°C or higher on May 30?",
                "market_type": "temperature",
                "side": "NO",
                "token_id": "no",
                "shares": "10",
                "price": "0.40",
                "cash_delta_or_pnl": "5",
                "reason": "settled",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-30T10:00:00+00:00",
                "market_id": "m-new",
                "slug": "new",
                "question": "Will the highest temperature in Seoul be 29°C or higher on May 30?",
                "market_type": "temperature",
                "side": "NO",
                "p_true": "0.30",
                "p_exec": "",
                "net_edge": "0.10",
                "size_usd": "4",
                "size_shares": "10",
                "entry_fraction": "",
                "probability_stop_threshold": "",
                "model_fair_price": "",
                "target_exit_price": "",
                "market_heat_score": "",
                "reason": "edge ok",
                "note": "",
            }
        ],
    )

    payload = build_dashboard_payload(
        Settings(state_path=str(state_path), trades_csv_path=str(trades_path), decisions_csv_path=str(decisions_path))
    )

    first = payload["realized_results"][0]
    assert first["market_id"] == "m-new"
    assert first["forecast_c"] == 29.0
    assert first["expected_exit_price"] == 0.4
    assert first["entry_price"] == 0.4
    assert first["exit_price"] == 0.4
    assert first["roi"] == 1.25


def test_dashboard_open_positions_include_polymarket_link_and_forecast_weather(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(
        json.dumps(
            {
                "cash_usd": 979.0,
                "positions": [
                    {
                        "position_id": "p1",
                        "market_id": "m-seoul",
                        "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                        "token_id": "yes",
                        "side": "YES",
                        "entry_price": 0.21,
                        "shares": 100.0,
                        "cost_usd": 21.0,
                        "opened_at": "2026-05-29T10:00:00+00:00",
                        "last_mark_price": 0.30,
                        "metadata": {"city": "seoul", "date_hint": "may 29", "slug": "seoul-27c"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-29T10:00:00+00:00",
                "action": "OPEN",
                "market_id": "m-seoul",
                "slug": "seoul-27c",
                "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "100",
                "price": "0.21",
                "cash_delta_or_pnl": "-21",
                "reason": "entry",
            }
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-29T10:00:01+00:00",
                "market_id": "m-seoul",
                "slug": "seoul-27c",
                "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "p_true": "0.70",
                "p_exec": "0.21",
                "net_edge": "0.20",
                "size_usd": "21",
                "size_shares": "100",
                "entry_fraction": "0.02",
                "probability_stop_threshold": "0.60",
                "model_fair_price": "0.40",
                "target_exit_price": "0.32",
                "market_heat_score": "0.1",
                "reason": "edge ok",
                "note": "station target_date=2026-05-29; >=27.0C/80.6F; members=82; vote=0.70; mean=86.0F; spread=2.0F",
            }
        ],
    )

    payload = build_dashboard_payload(
        Settings(state_path=str(state_path), trades_csv_path=str(trades_path), decisions_csv_path=str(decisions_path))
    )

    position = payload["positions"][0]
    assert position["market_url"] == "https://polymarket.com/event/seoul-27c"
    assert position["forecast_c"] == 30.0


def test_dashboard_summary_reports_latest_forecast_cache_time_and_profit_loss_totals(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    forecast_cache_path = tmp_path / "forecast_cache.json"
    state_path.write_text(json.dumps({"cash_usd": 1008.0, "realized_pnl_usd": 8.0, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-29T11:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m-win",
                "slug": "win",
                "question": "Win",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "10",
                "price": "0.70",
                "cash_delta_or_pnl": "12",
                "reason": "take profit",
            },
            {
                "ts": "2026-05-30T11:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m-loss",
                "slug": "loss",
                "question": "Loss",
                "market_type": "temperature",
                "side": "NO",
                "token_id": "no",
                "shares": "10",
                "price": "0.20",
                "cash_delta_or_pnl": "-4",
                "reason": "stop",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-29T10:00:00+00:00",
                "market_id": "m1",
                "slug": "m1",
                "question": "Skipped",
                "market_type": "temperature",
                "side": "SKIP",
                "p_true": "0.5",
                "p_exec": "",
                "net_edge": "-999",
                "size_usd": "0",
                "size_shares": "0",
                "entry_fraction": "",
                "probability_stop_threshold": "",
                "model_fair_price": "",
                "target_exit_price": "",
                "market_heat_score": "",
                "reason": "edge below",
                "note": "",
            }
        ],
    )
    forecast_cache_path.write_text(
        json.dumps(
            {
                "old": {"created_at": "2026-05-29T08:00:00+00:00", "data": {}},
                "new": {"created_at": "2026-05-30T09:30:00+00:00", "data": {}},
            }
        ),
        encoding="utf-8",
    )

    payload = build_dashboard_payload(
        Settings(
            state_path=str(state_path),
            trades_csv_path=str(trades_path),
            decisions_csv_path=str(decisions_path),
            forecast_cache_path=str(forecast_cache_path),
        )
    )

    assert payload["summary"]["realized_profit_usd"] == 12.0
    assert payload["summary"]["realized_loss_usd"] == 4.0
    assert payload["scanner"]["latest_forecast_at"] == "2026-05-30T09:30:00+00:00"


def test_dashboard_uses_clear_english_scanner_labels():
    assert "Cumulative candidate decisions" not in HTML
    assert "Forecast unavailable" not in HTML
    assert "Actual entries" not in HTML
    assert "YES/NO decisions" not in HTML
    assert "Open Positions" in HTML
    assert "Total Open Entry Cost" in HTML
    assert "Latest Open-Meteo Forecast" in HTML
    assert "Total Profit" in HTML
    assert "Total Loss" in HTML
    assert "Forecast" in HTML
    assert "Cumulative skips" not in HTML
    assert "Remaining Cash" in HTML
    assert "NO FORECAST" not in HTML
    assert "Total Exposure" not in HTML
    assert "Recent Candidates" not in HTML
    assert "Event Stream" not in HTML
    assert 'data-range="1D"' in HTML
    assert 'id="chart-tooltip"' in HTML
    assert '"¢"' in HTML


def test_dashboard_payload_uses_runner_status_as_bot_heartbeat(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    raw_path = tmp_path / "raw.jsonl"
    runner_status_path = tmp_path / "paper_runner_status.json"
    state_path.write_text(json.dumps({"cash_usd": 1000.0, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-24T10:00:00+00:00",
                "action": "OPEN",
                "market_id": "m1",
                "slug": "old",
                "question": "Old trade",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "10",
                "price": "0.5",
                "cash_delta_or_pnl": "-5",
                "reason": "entry",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-24T10:01:00+00:00",
                "market_id": "m2",
                "slug": "old-decision",
                "question": "Old decision",
                "market_type": "temperature",
                "side": "SKIP",
                "p_true": "0.5",
                "p_exec": "",
                "net_edge": "-999",
                "size_usd": "0",
                "size_shares": "0",
                "entry_fraction": "",
                "probability_stop_threshold": "",
                "model_fair_price": "",
                "target_exit_price": "",
                "market_heat_score": "",
                "reason": "old",
                "note": "",
            },
        ],
    )
    runner_status_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-05-24T10:05:00+00:00",
                "phase": "evaluating",
                "message": "evaluating 3/40",
                "markets_done": 3,
                "markets_total": 40,
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        bankroll_usd=1000.0,
        state_path=str(state_path),
        trades_csv_path=str(trades_path),
        decisions_csv_path=str(decisions_path),
        raw_snapshots_path=str(raw_path),
    )

    payload = build_dashboard_payload(settings)

    assert payload["bot"]["last_event_at"] == "2026-05-24T10:05:00+00:00"
    assert payload["bot"]["status"] == "STALE"
    assert payload["bot"]["phase"] == "evaluating"
    assert payload["bot"]["message"] == "evaluating 3/40"
    assert payload["bot"]["markets_done"] == 3
    assert payload["bot"]["markets_total"] == 40


def test_dashboard_payload_surfaces_forecast_and_websocket_health(tmp_path):
    state_path = tmp_path / "state.json"
    runner_status_path = tmp_path / "paper_runner_status.json"
    state_path.write_text(json.dumps({"cash_usd": 1000.0, "positions": []}), encoding="utf-8")
    runner_status_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-06-01T00:01:00+00:00",
                "phase": "stream_error",
                "message": "websocket thread stopped",
                "forecast": {
                    "last_attempt_at": "2026-06-01T00:00:00+00:00",
                    "last_success_at": "2026-06-01T00:00:00+00:00",
                    "last_failure_reason": "RuntimeError: rate limited",
                    "cache_age_seconds": 1801,
                    "stale": True,
                    "persistence_error": "OSError: disk full",
                },
                "websocket": {
                    "thread_alive": False,
                    "reconnect_count": 3,
                    "last_message_at": "2026-06-01T00:00:30+00:00",
                    "last_book_at": "2026-06-01T00:00:20+00:00",
                    "stale_book_age_seconds": 40,
                    "stale": True,
                    "last_error": "RuntimeError: websocket stopped",
                },
            }
        ),
        encoding="utf-8",
    )

    payload = build_dashboard_payload(Settings(state_path=str(state_path)))

    assert payload["health"]["forecast"]["status"] == "STALE"
    assert payload["health"]["forecast"]["cache_age_seconds"] >= 1801
    assert payload["health"]["forecast"]["persistence_error"] == "OSError: disk full"
    assert payload["health"]["websocket"]["status"] == "FAILED"
    assert payload["health"]["websocket"]["thread_alive"] is False
    assert payload["health"]["websocket"]["reconnect_count"] == 3
    assert payload["bot"]["status"] == "FAILED"


def test_dashboard_html_explains_health_warnings():
    assert "Forecast Health" in HTML
    assert "Last success" in HTML
    assert "WebSocket Health" in HTML
    assert "Reconnects" in HTML
    assert "Last order book" in HTML
