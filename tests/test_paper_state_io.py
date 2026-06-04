from __future__ import annotations

import json
from pathlib import Path

import pytest

import weather_bot.paper as paper
from weather_bot.config import Settings
from weather_bot.paper import PaperBroker, PaperStateLoadError


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


def valid_position_state() -> dict:
    return {
        "cash_usd": 90.0,
        "realized_pnl_usd": 1.25,
        "positions": [
            {
                "position_id": "pos-1",
                "market_id": "market-1",
                "question": "Will NYC high temperature exceed 80F?",
                "token_id": "token-yes-1",
                "side": "YES",
                "entry_price": 0.42,
                "shares": 12.5,
                "cost_usd": 5.25,
                "opened_at": "2026-06-03T00:00:00+00:00",
                "last_mark_price": 0.45,
                "last_unrealized_pnl": 0.375,
                "metadata": {"city": "NYC", "date_hint": "jun 3"},
            }
        ],
        "stats": {"temperature": {"wins": 1, "losses": 0, "pnl": 1.25}},
    }


def write_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state), encoding="utf-8")


def test_valid_paper_state_with_position_still_loads(tmp_path):
    settings = settings_for(tmp_path)
    write_state(Path(settings.state_path), valid_position_state())

    broker = PaperBroker(settings)

    assert broker.state.cash_usd == 90.0
    assert broker.state.realized_pnl_usd == 1.25
    assert len(broker.state.positions) == 1
    position = broker.state.positions[0]
    assert position.market_id == "market-1"
    assert position.token_id == "token-yes-1"
    assert position.side == "YES"
    assert position.shares == pytest.approx(12.5)
    assert position.entry_price == pytest.approx(0.42)
    assert position.cost_usd == pytest.approx(5.25)
    assert position.metadata == {"city": "NYC", "date_hint": "jun 3"}
    assert broker.state.stats == {"temperature": {"wins": 1, "losses": 0, "pnl": 1.25}}


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("cash_usd", -0.01),
        ("cash_usd", "90.0"),
        ("cash_usd", True),
        ("realized_pnl_usd", float("nan")),
        ("realized_pnl_usd", "1.25"),
        ("realized_pnl_usd", False),
    ],
)
def test_invalid_account_numbers_fail_closed_instead_of_loading_book(tmp_path, field, bad_value):
    settings = settings_for(tmp_path)
    state = valid_position_state()
    state[field] = bad_value
    write_state(Path(settings.state_path), state)

    with pytest.raises(PaperStateLoadError, match="refusing to start"):
        PaperBroker(settings)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("wins", -1),
        ("wins", 1.5),
        ("wins", "1"),
        ("wins", True),
        ("losses", -1),
        ("losses", 0.25),
        ("losses", "0"),
        ("losses", False),
        ("pnl", float("nan")),
        ("pnl", "1.25"),
        ("pnl", True),
    ],
)
def test_invalid_stats_values_fail_closed_instead_of_loading_book(tmp_path, field, bad_value):
    settings = settings_for(tmp_path)
    state = valid_position_state()
    state["stats"]["temperature"][field] = bad_value
    write_state(Path(settings.state_path), state)

    with pytest.raises(PaperStateLoadError, match="refusing to start"):
        PaperBroker(settings)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("side", "MAYBE"),
        ("shares", "12.5"),
        ("shares", float("nan")),
        ("shares", 0.0),
        ("entry_price", "0.42"),
        ("entry_price", -0.01),
        ("entry_price", 1.01),
        ("cost_usd", "5.25"),
        ("cost_usd", -0.01),
        ("market_id", ""),
        ("market_id", None),
        ("token_id", ""),
        ("token_id", "   "),
        ("metadata", ["not", "a", "dict"]),
    ],
)
def test_invalid_position_fields_fail_closed_instead_of_loading_book(tmp_path, field, bad_value):
    settings = settings_for(tmp_path)
    state = valid_position_state()
    state["positions"][0][field] = bad_value
    write_state(Path(settings.state_path), state)

    with pytest.raises(PaperStateLoadError, match="refusing to start"):
        PaperBroker(settings)
