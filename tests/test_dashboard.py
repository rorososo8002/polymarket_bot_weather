from __future__ import annotations

import csv
import json

from weather_bot.config import Settings
from weather_bot.dashboard import build_dashboard_payload


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


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
                "stop_loss_price": "0.45",
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
                "stop_loss_price": "",
                "model_fair_price": "",
                "target_exit_price": "",
                "market_heat_score": "",
                "reason": "confidence too low",
                "note": "",
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
    assert payload["summary"]["market_value"] == 60.0
    assert payload["summary"]["equity"] == 1010.0
    assert payload["summary"]["total_pnl"] == 10.0
    assert payload["summary"]["wins"] == 1
    assert payload["summary"]["losses"] == 1
    assert payload["scanner"]["decisions"] == 2
    assert payload["scanner"]["skips"] == 1
    assert payload["scanner"]["entries"] == 1
    assert payload["bot"]["last_event_at"] == "2026-05-24T11:00:00+00:00"
    assert payload["bot"]["scan_interval_seconds"] == 1800
    assert payload["bot"]["orderbook_mode"] == "websocket"
    assert payload["positions"][0]["unrealized_pnl"] == 10.0
    assert any(event["label"].startswith("DECISION") for event in payload["events"])


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
                "stop_loss_price": "",
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
