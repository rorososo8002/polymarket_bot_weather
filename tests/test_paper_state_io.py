from __future__ import annotations

import json
from pathlib import Path

import pytest

import weather_bot.paper as paper
from weather_bot.config import Settings
from weather_bot.paper import PaperBroker


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        state_path=str(tmp_path / "paper_state.json"),
        trades_csv_path=str(tmp_path / "paper_trades.csv"),
        decisions_csv_path=str(tmp_path / "paper_decisions.csv"),
        portfolio_decisions_jsonl_path=str(tmp_path / "paper_event_portfolios.jsonl"),
        raw_snapshots_path=str(tmp_path / "paper_raw_snapshots.jsonl"),
    )


def test_save_state_writes_temp_file_before_atomic_replace(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    state_path = Path(settings.state_path)
    state_path.write_text(json.dumps({"cash_usd": 100.0, "positions": []}), encoding="utf-8")
    broker = PaperBroker(settings)
    broker.state.cash_usd = 88.0
    replace_calls: list[tuple[Path, Path]] = []
    real_replace = paper.os.replace

    def replace_spy(src, dst):
        src_path = Path(src)
        dst_path = Path(dst)
        replace_calls.append((src_path, dst_path))
        assert dst_path == state_path
        assert src_path != state_path
        assert src_path.parent == state_path.parent
        assert json.loads(src_path.read_text(encoding="utf-8"))["cash_usd"] == 88.0
        assert json.loads(state_path.read_text(encoding="utf-8"))["cash_usd"] == 100.0
        real_replace(src, dst)

    monkeypatch.setattr(paper.os, "replace", replace_spy)

    broker.save_state()

    assert replace_calls
    assert json.loads(state_path.read_text(encoding="utf-8"))["cash_usd"] == 88.0
    assert not replace_calls[0][0].exists()


def test_corrupt_paper_state_fails_closed_instead_of_starting_new_book(tmp_path):
    settings = settings_for(tmp_path)
    state_path = Path(settings.state_path)
    state_path.write_text('{"cash_usd": 42.0, "positions": [', encoding="utf-8")

    with pytest.raises(RuntimeError, match="refusing to start"):
        PaperBroker(settings)

    assert state_path.read_text(encoding="utf-8") == '{"cash_usd": 42.0, "positions": ['
