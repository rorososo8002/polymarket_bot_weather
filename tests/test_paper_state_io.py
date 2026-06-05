from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import weather_bot.paper as paper
from weather_bot.config import Settings
from weather_bot.models import EdgeResult, RawMarket
from weather_bot.paper import PaperBroker, PaperStateLoadError


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        state_path=str(tmp_path / "paper_state.json"),
        trades_csv_path=str(tmp_path / "paper_trades.csv"),
        decisions_csv_path=str(tmp_path / "paper_decisions.csv"),
        portfolio_decisions_jsonl_path=str(tmp_path / "paper_event_portfolios.jsonl"),
        raw_snapshots_path=str(tmp_path / "paper_raw_snapshots.jsonl"),
    )


def accounting_journal_path(settings: Settings) -> Path:
    state_path = Path(settings.state_path)
    return state_path.with_name(f"{state_path.name}.journal")


def trade_market(market_id: str = "market-1") -> RawMarket:
    return RawMarket(
        market_id=market_id,
        question="Will NYC high temperature exceed 80F?",
        slug=f"{market_id}-slug",
        active=True,
        closed=False,
        yes_token_id=f"{market_id}-yes",
        no_token_id=f"{market_id}-no",
        event_slug=f"{market_id}-event",
    )


def entry_result(side: str = "YES", p_exec: float = 0.50) -> EdgeResult:
    return EdgeResult(
        side=side,  # type: ignore[arg-type]
        p_true=0.75 if side == "YES" else 0.25,
        p_exec=p_exec,
        net_edge=0.15,
        size_usd=10.0,
        size_shares=20.0,
        reason="edge ok",
        expected_net_profit_usd=2.0,
    )


def trade_actions(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return [row["action"] for row in csv.DictReader(f)]


def write_open_trade_for_valid_position(settings: Settings) -> None:
    path = Path(settings.trades_csv_path)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=paper.TRADE_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerow(
            {
                "ts": "2026-06-03T00:00:00+00:00",
                "action": "OPEN",
                "market_id": "market-1",
                "slug": "market-1-slug",
                "question": "Will NYC high temperature exceed 80F?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "token-yes-1",
                "shares": "12.500000",
                "price": "0.420000",
                "cash_delta_or_pnl": "-5.250000",
                "reason": "legacy fixture",
            }
        )


def write_trade_rows(settings: Settings, rows: list[dict[str, str]]) -> None:
    path = Path(settings.trades_csv_path)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=paper.TRADE_CSV_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            payload = {field: "" for field in paper.TRADE_CSV_FIELDNAMES}
            payload.update(row)
            writer.writerow(payload)


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
    write_open_trade_for_valid_position(settings)

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


def test_state_with_open_positions_and_missing_trade_ledger_fails_closed(tmp_path):
    settings = settings_for(tmp_path)
    write_state(Path(settings.state_path), valid_position_state())

    with pytest.raises(PaperStateLoadError, match="paper_trades.csv"):
        PaperBroker(settings)


def test_missing_state_with_executed_trade_ledger_fails_closed(tmp_path):
    settings = settings_for(tmp_path)
    write_trade_rows(
        settings,
        [
            {
                "ts": "2026-06-03T00:00:00+00:00",
                "action": "OPEN",
                "market_id": "market-1",
                "side": "YES",
                "token_id": "token-yes-1",
                "shares": "12.500000",
                "price": "0.420000",
                "cash_delta_or_pnl": "-5.250000",
            }
        ],
    )

    with pytest.raises(PaperStateLoadError, match="paper_state.json is missing"):
        PaperBroker(settings)


def test_open_position_without_matching_open_trade_fails_closed(tmp_path):
    settings = settings_for(tmp_path)
    write_state(Path(settings.state_path), valid_position_state())
    write_trade_rows(
        settings,
        [
            {
                "ts": "2026-06-03T00:00:00+00:00",
                "action": "OPEN",
                "market_id": "other-market",
                "side": "YES",
                "token_id": "other-token",
                "shares": "12.500000",
                "price": "0.420000",
                "cash_delta_or_pnl": "-5.250000",
            }
        ],
    )

    with pytest.raises(PaperStateLoadError, match="no matching OPEN trade"):
        PaperBroker(settings)


def test_unresolved_accounting_journal_fails_closed_on_startup(tmp_path):
    settings = settings_for(tmp_path)
    accounting_journal_path(settings).write_text(
        json.dumps({"action": "OPEN", "phase": "state_saved"}),
        encoding="utf-8",
    )

    with pytest.raises(PaperStateLoadError, match="unresolved paper accounting transaction"):
        PaperBroker(settings)


def test_open_position_save_failure_rolls_back_memory_and_leaves_journal(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    broker = PaperBroker(settings)
    before_cash = broker.state.cash_usd

    def fail_save_state():
        raise OSError("state disk full")

    monkeypatch.setattr(broker, "save_state", fail_save_state)

    with pytest.raises(RuntimeError, match="paper accounting transaction failed"):
        broker.open_position(trade_market(), "market-1-yes", entry_result())

    assert broker.state.cash_usd == before_cash
    assert broker.state.positions == []
    assert trade_actions(Path(settings.trades_csv_path)) == []
    assert accounting_journal_path(settings).exists()
    with pytest.raises(PaperStateLoadError, match="unresolved paper accounting transaction"):
        PaperBroker(settings)


@pytest.mark.parametrize("action", ["OPEN", "ADD", "CLOSE", "PARTIAL_CLOSE"])
def test_executed_trade_log_failure_leaves_startup_halt_journal(tmp_path, monkeypatch, action):
    settings = settings_for(tmp_path)
    broker = PaperBroker(settings)
    market = trade_market()
    position = None
    if action != "OPEN":
        position = broker.open_position(market, "market-1-yes", entry_result())
        assert position is not None

    real_log_trade = broker.log_trade

    def fail_selected_log_trade(log_action, *args, **kwargs):
        if log_action == action:
            raise OSError(f"{action} trade log unavailable")
        return real_log_trade(log_action, *args, **kwargs)

    monkeypatch.setattr(broker, "log_trade", fail_selected_log_trade)

    with pytest.raises(RuntimeError, match="paper accounting transaction failed"):
        if action == "OPEN":
            broker.open_position(market, "market-1-yes", entry_result())
        elif action == "ADD":
            broker.open_position(
                market,
                "market-1-yes",
                entry_result(p_exec=0.44),
                allow_same_side_add=True,
            )
        elif action == "CLOSE":
            assert position is not None
            broker.close_position(position, market, 0.60, "test close")
        else:
            assert position is not None
            broker.partial_close_position(position, 2.0, 0.60, "test partial close")

    assert accounting_journal_path(settings).exists()
    with pytest.raises(PaperStateLoadError, match="unresolved paper accounting transaction"):
        PaperBroker(settings)


def test_partial_close_save_failure_does_not_append_partial_trade(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    broker = PaperBroker(settings)
    market = trade_market()
    position = broker.open_position(market, "market-1-yes", entry_result())
    assert position is not None
    original_cash = broker.state.cash_usd
    original_shares = position.shares

    def fail_save_state():
        raise OSError("state replace denied")

    monkeypatch.setattr(broker, "save_state", fail_save_state)

    with pytest.raises(RuntimeError, match="paper accounting transaction failed"):
        broker.partial_close_position(position, 2.0, 0.60, "test partial close")

    assert broker.state.cash_usd == original_cash
    assert broker.state.positions[0].shares == original_shares
    assert trade_actions(Path(settings.trades_csv_path)) == ["OPEN"]
    assert accounting_journal_path(settings).exists()


def test_log_trade_appends_to_legacy_trade_csv_without_rewriting_header(tmp_path):
    settings = settings_for(tmp_path)
    write_state(Path(settings.state_path), {"cash_usd": 100.0, "positions": []})
    legacy_header = "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason"
    Path(settings.trades_csv_path).write_text(
        legacy_header
        + "\n"
        + "2026-01-01T00:00:00+00:00,OPEN,old,old,q,temperature,YES,yes,1.000000,0.500000,-0.500000,legacy\n",
        encoding="utf-8",
    )
    broker = PaperBroker(settings)

    broker.log_trade(
        "SKIP_TEST",
        trade_market("skip-market"),
        "YES",
        "skip-market-yes",
        0.0,
        0.50,
        0.0,
        "skip fixture",
        "temperature",
        entry_metadata={"entry_p_true": 0.75},
    )

    lines = Path(settings.trades_csv_path).read_text(encoding="utf-8").splitlines()
    assert lines[0] == legacy_header
    assert "entry_p_true" not in lines[0]
    assert lines[-1].split(",")[1] == "SKIP_TEST"


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
