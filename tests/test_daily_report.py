from __future__ import annotations

import csv
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_daily_report_module():
    script_path = ROOT / "scripts" / "daily_report.py"
    spec = importlib.util.spec_from_file_location("daily_report_under_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_trades(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_daily_report_uses_cash_delta_or_pnl_from_trade_ledger(tmp_path):
    module = _load_daily_report_module()
    module.TRADES_CSV = tmp_path / "paper_trades.csv"
    module.STATE_JSON = tmp_path / "paper_state.json"

    _write_trades(
        module.TRADES_CSV,
        [
            {
                "ts": "2026-06-10T01:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m-win",
                "slug": "seoul-27c",
                "question": "Will the highest temperature in Seoul be 27C or higher on June 10?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "10",
                "price": "0.70",
                "cash_delta_or_pnl": "12.50",
                "reason": "take profit",
            },
            {
                "ts": "2026-06-10T02:00:00+00:00",
                "action": "SETTLED",
                "market_id": "m-loss",
                "slug": "tokyo-31c",
                "question": "Will the highest temperature in Tokyo be 31C or higher on June 10?",
                "market_type": "temperature",
                "side": "NO",
                "token_id": "no",
                "shares": "10",
                "price": "0.00",
                "cash_delta_or_pnl": "-5.00",
                "reason": "resolved winner=YES",
            },
        ],
    )
    module.STATE_JSON.write_text(json.dumps({"cash_usd": 107.5, "positions": []}), encoding="utf-8")

    report = module.build_report(
        datetime(2026, 6, 10, tzinfo=timezone.utc),
        datetime(2026, 6, 11, tzinfo=timezone.utc),
    )

    assert "$+7.50" in report
    assert "승리 / 패배        : 1 / 1" in report
    assert "Seoul" in report
    assert "Tokyo" in report


def test_daily_report_falls_back_to_legacy_realized_pnl_columns(tmp_path):
    module = _load_daily_report_module()
    module.TRADES_CSV = tmp_path / "paper_trades.csv"
    module.STATE_JSON = tmp_path / "paper_state.json"
    module.TRADES_CSV.write_text(
        "\n".join(
            [
                "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,realized_pnl_usd,reason",
                (
                    "2026-06-02T00:00:00+00:00,CLOSE,seoul,seoul,"
                    "Will the highest temperature in Seoul be 27C or higher on June 1?,"
                    "temperature,YES,yes,10,0.55,3.25,legacy close"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    module.STATE_JSON.write_text(
        json.dumps({"cash_usd": 103.25, "positions": []}),
        encoding="utf-8",
    )

    report = module.build_report(
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 8, tzinfo=timezone.utc),
    )

    assert "$+3.25" in report
