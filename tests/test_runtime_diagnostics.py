import json
from pathlib import Path

from weather_bot.runtime_diagnostics import build_runtime_report


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_runtime_report_separates_stream_and_token_stale_blocks(tmp_path):
    write(
        tmp_path / "paper_runner_status.json",
        json.dumps(
            {
                "updated_at": "2026-06-16T04:57:41+00:00",
                "phase": "streaming",
                "message": "websocket streaming 4 tokens across 2 markets",
                "open_positions": 2,
                "cash_usd": 151.03,
                "exposure_usd": 50.0,
                "websocket": {
                    "thread_alive": True,
                    "stale": False,
                    "last_message_age_seconds": 0,
                    "stale_book_age_seconds": 0,
                    "reconnect_count": 2,
                    "status_reason": "executable order book depth fresh; age=0s; reconnects=2",
                    "last_error": "",
                },
                "forecast_worker": {"thread_alive": True, "queue_depth": 0, "error_count": 0},
                "realtime_evaluator": {
                    "thread_alive": True,
                    "queue_depth": 25,
                    "processed_event_count": 1695,
                    "error_count": 0,
                },
            }
        ),
    )
    write(
        tmp_path / "paper_state.json",
        json.dumps(
            {
                "cash_usd": 151.03,
                "realized_pnl_usd": 1.03,
                "positions": [
                    {
                        "market_id": "2539304",
                        "question": "Will the highest temperature in Amsterdam be 23°C on June 16?",
                        "token_id": "token-amsterdam",
                        "side": "NO",
                        "cost_usd": 10.0,
                        "last_mark_price": 0.58,
                        "metadata": {"city": "amsterdam"},
                    },
                    {
                        "market_id": "2549700",
                        "question": "Will the highest temperature in Jeddah be 39°C on June 17?",
                        "token_id": "token-jeddah",
                        "side": "NO",
                        "cost_usd": 20.0,
                        "last_mark_price": 0.67,
                        "metadata": {"city": "jeddah"},
                    },
                ],
            }
        ),
    )
    write(
        tmp_path / "paper_trades.csv",
        "\n".join(
            [
                "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason,reason_code,city",
                "2026-06-16T04:31:13+00:00,HOLD_STREAM_UNHEALTHY,2539304,s,Will the highest temperature in Amsterdam be 23°C on June 16?,temperature,NO,token-amsterdam,18.10,0.57,0.0,websocket order book stream unhealthy: websocket receiver thread is not running; new entries blocked,HOLD_STREAM_UNHEALTHY,amsterdam",
                "2026-06-16T04:34:15+00:00,OPEN,2549700,s,Will the highest temperature in Jeddah be 39°C on June 17?,temperature,NO,token-jeddah,28.91,0.68,-20.0,entry,HOLD_RUNNER,jeddah",
                "2026-06-16T04:45:22+00:00,HOLD_STREAM_UNHEALTHY,2539304,s,Will the highest temperature in Amsterdam be 23°C on June 16?,temperature,NO,token-amsterdam,18.10,0.57,0.0,websocket order book stream unhealthy: token token-amsterdam executable order book depth age 66s exceeds 60s; reconnects=2,HOLD_STREAM_UNHEALTHY,amsterdam",
                "2026-06-16T04:53:49+00:00,HOLD_STREAM_UNHEALTHY,2539304,s,Will the highest temperature in Amsterdam be 23°C on June 16?,temperature,NO,token-amsterdam,18.10,0.57,0.0,websocket order book stream unhealthy: token token-amsterdam executable order book depth age 63s exceeds 60s; reconnects=2,HOLD_STREAM_UNHEALTHY,amsterdam",
            ]
        )
        + "\n",
    )

    report = build_runtime_report(tmp_path, tail=20)

    assert "runtime_status: phase=streaming open_positions=2 exposure=$50.00" in report
    assert "websocket: fresh thread_alive=True stale=False last_message_age=0s stale_book_age=0s reconnects=2" in report
    assert "- HOLD_STREAM_UNHEALTHY: 3" in report
    assert "- whole_stream_blocks=1" in report
    assert "- token_stale_blocks=2" in report
    assert "- amsterdam market_id=2539304 token=token-amsterdam count=2 latest_age=63s" in report
    assert "recommendation: investigate held-token liquidity/subscription before loosening global stale limits" in report


def test_runtime_report_warns_when_status_file_is_missing(tmp_path):
    write(
        tmp_path / "paper_trades.csv",
        "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason\n",
    )

    report = build_runtime_report(tmp_path, tail=10)

    assert "warnings:" in report
    assert "- missing paper_runner_status.json" in report


def test_runtime_report_summarizes_zero_selection_portfolio_rejections(tmp_path):
    write(
        tmp_path / "paper_runner_status.json",
        json.dumps(
            {
                "updated_at": "2026-06-18T00:00:00+00:00",
                "phase": "streaming",
                "open_positions": 0,
                "exposure_usd": 0,
                "websocket": {"thread_alive": True, "stale": False},
                "forecast_worker": {"thread_alive": True, "queue_depth": 0, "error_count": 0},
                "realtime_evaluator": {"thread_alive": True, "queue_depth": 0, "error_count": 0},
            }
        ),
    )
    write(tmp_path / "paper_state.json", json.dumps({"cash_usd": 172.51, "positions": []}))
    write(
        tmp_path / "paper_trades.csv",
        "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason\n",
    )
    rows = [
        {
            "event_key": "seoul|jun-18",
            "city": "seoul",
            "date_hint": "jun-18",
            "selected_count": 0,
            "rejected_count": 4,
            "entry_bankroll_usable": True,
            "entry_bankroll_reason": "",
            "rejected_reason_counts": {
                "scenario probabilities overlap (overlap_ratio=1.000)": 2,
                "not selected by event portfolio optimizer": 2,
            },
        },
        {
            "event_key": "tokyo|jun-18",
            "city": "tokyo",
            "date_hint": "jun-18",
            "selected_count": 1,
            "rejected_count": 1,
            "entry_bankroll_usable": True,
            "rejected_reason_counts": {"not selected by event portfolio optimizer": 1},
        },
    ]
    write(
        tmp_path / "paper_event_portfolios.jsonl",
        "\n".join(json.dumps(row) for row in rows) + "\n",
    )

    report = build_runtime_report(tmp_path, tail=20)

    assert "portfolio_rejections:" in report
    assert "- inspected_rows=2 zero_selection_rows=1 selected_rows=1" in report
    assert "- scenario probabilities overlap (overlap_ratio=1.000): 2" in report
    assert "- not selected by event portfolio optimizer: 2" in report
    assert "latest_zero_selection: event_key=seoul|jun-18 city=seoul date_hint=jun-18 rejected_count=4 entry_bankroll_usable=True" in report
