from __future__ import annotations

import json

import pytest

from weather_bot.config import Settings
from weather_bot.edge import polymarket_taker_fee_per_share
from weather_bot.live_paper_runner import _apply_event_portfolio, _evaluate_realtime_update, evaluate_market, run_cycle
from weather_bot.models import EdgeResult, OrderBook, OrderLevel, PaperPosition, PaperState, RawMarket, WeatherSignal
from weather_bot.paper import PaperBroker
from weather_bot.portfolio import (
    EntryBankrollSnapshot,
    PortfolioCandidate,
    adaptive_event_cap_fraction,
    available_entry_bankroll,
    select_event_portfolio,
)
from weather_bot.weather_client import parse_weather_question


class FakeBook:
    def __init__(self, bid: float, ask: float, bid_size: float = 100.0, ask_size: float = 100.0) -> None:
        self.value = OrderBook(
            token_id="token",
            bids=[OrderLevel(bid, bid_size)],
            asks=[OrderLevel(ask, ask_size)],
        )


class FakeClient:
    def __init__(self, books=None) -> None:
        self.books = books or {}

    def get_order_book(self, token_id: str):
        return self.books[token_id]


def settings(tmp_path, **overrides) -> Settings:
    values = {
        "state_path": str(tmp_path / "state.json"),
        "trades_csv_path": str(tmp_path / "trades.csv"),
        "decisions_csv_path": str(tmp_path / "decisions.csv"),
        "portfolio_decisions_jsonl_path": str(tmp_path / "portfolio.jsonl"),
        "raw_snapshots_path": str(tmp_path / "raw.jsonl"),
        "bankroll_usd": 100.0,
        **overrides,
    }
    return Settings(
        **values,
    )


def market(market_id: str, bucket: str) -> RawMarket:
    return RawMarket(
        market_id=market_id,
        question=f"Will the highest temperature in Seoul be {bucket} on May 25?",
        slug=market_id,
        active=True,
        closed=False,
        yes_token_id=f"{market_id}-yes",
        no_token_id=f"{market_id}-no",
        event_id="seoul-may-25",
    )


def candidate(
    market_id: str,
    bucket: str,
    *,
    side: str = "YES",
    size_usd: float = 10.0,
    p_true: float = 0.6,
    p_exec: float = 0.40,
    expected_net_profit_usd: float = 1.0,
) -> PortfolioCandidate:
    raw_market = market(market_id, bucket)
    parsed = parse_weather_question(raw_market.question)
    signal = WeatherSignal(p_true, 0.9, "test", "test", parsed)
    return PortfolioCandidate(
        market=raw_market,
        signal=signal,
        result=EdgeResult(
            side=side,
            p_true=p_true,
            p_exec=p_exec,
            net_edge=0.15,
            size_usd=size_usd,
            size_shares=size_usd / p_exec,
            reason="cost-adjusted candidate",
            expected_net_profit_usd=expected_net_profit_usd,
        ),
        market_type="temperature",
    )


def usable_snapshot(entry_bankroll: float = 100.0) -> EntryBankrollSnapshot:
    return EntryBankrollSnapshot(
        usable=True,
        cost_basis_bankroll=entry_bankroll,
        liquidation_bankroll=entry_bankroll,
        entry_bankroll=entry_bankroll,
        reason="priced",
    )


def orderbook(token_id: str, bid: float, ask: float) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        bids=[OrderLevel(bid, 1000.0)],
        asks=[OrderLevel(ask, 1000.0)],
    )


def test_adaptive_city_date_cap_drops_from_ten_to_five_percent_at_one_thousand(tmp_path):
    cfg = settings(tmp_path)

    assert adaptive_event_cap_fraction(999.99, cfg) == 0.10
    assert adaptive_event_cap_fraction(1000.0, cfg) == 0.05
    assert adaptive_event_cap_fraction(1500.0, cfg) == 0.05


def test_entry_bankroll_uses_lower_executable_liquidation_value(tmp_path):
    broker = PaperBroker(settings(tmp_path))
    broker.state = PaperState(
        cash_usd=90.0,
        positions=[
            PaperPosition(
                position_id="held",
                market_id="held",
                question="Will NYC reach 90°F on May 25?",
                token_id="held-token",
                side="YES",
                entry_price=0.50,
                shares=20.0,
                cost_usd=10.0,
                opened_at="2026-06-01T00:00:00+00:00",
            )
        ],
    )
    client = FakeClient({"held-token": FakeBook(0.40, 0.41).value})

    snapshot = available_entry_bankroll(broker, client)

    assert snapshot.usable is True
    assert snapshot.cost_basis_bankroll == 100.0
    assert snapshot.liquidation_bankroll == pytest.approx(97.76)
    assert snapshot.entry_bankroll == pytest.approx(97.76)


def test_entry_bankroll_fails_closed_when_held_position_cannot_be_priced(tmp_path):
    broker = PaperBroker(settings(tmp_path))
    broker.state = PaperState(
        cash_usd=90.0,
        positions=[
            PaperPosition(
                position_id="held",
                market_id="held",
                question="Will NYC reach 90°F on May 25?",
                token_id="missing-token",
                side="YES",
                entry_price=0.50,
                shares=20.0,
                cost_usd=10.0,
                opened_at="2026-06-01T00:00:00+00:00",
            )
        ],
    )

    snapshot = available_entry_bankroll(broker, FakeClient())

    assert snapshot.usable is False
    assert snapshot.entry_bankroll == 0.0
    assert "missing-token" in snapshot.reason


def test_evaluate_market_skips_when_entry_bankroll_is_zero(tmp_path):
    cfg = settings(
        tmp_path,
        min_net_edge=0.01,
        entry_min_expected_net_return_pct=0.01,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
    )
    raw_market = market("seoul-26", "26\u00b0C")
    signal = WeatherSignal(0.80, 0.90, "test", "test", parse_weather_question(raw_market.question))
    client = FakeClient(
        {
            "seoul-26-yes": orderbook("seoul-26-yes", 0.39, 0.40),
            "seoul-26-no": orderbook("seoul-26-no", 0.59, 0.60),
        }
    )

    result, per_side = evaluate_market(raw_market, signal, client, cfg, 0.0, "temperature")

    assert result.side == "SKIP"
    assert per_side == {}
    assert result.size_usd == 0.0
    assert result.size_shares == 0.0
    assert "기존 포지션을 안전하게 평가할 수 없어 신규 진입 차단" in result.reason


def test_evaluate_market_skips_when_calculated_order_is_below_minimum(tmp_path):
    cfg = settings(
        tmp_path,
        bankroll_usd=50.0,
        min_net_edge=0.01,
        min_order_usd=10.0,
        entry_fraction=0.10,
        max_single_market_fraction=0.10,
        entry_min_expected_net_return_pct=0.01,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
    )
    raw_market = market("seoul-26", "26\u00b0C")
    signal = WeatherSignal(0.80, 0.90, "test", "test", parse_weather_question(raw_market.question))
    client = FakeClient(
        {
            "seoul-26-yes": orderbook("seoul-26-yes", 0.39, 0.40),
            "seoul-26-no": orderbook("seoul-26-no", 0.59, 0.60),
        }
    )

    result, per_side = evaluate_market(raw_market, signal, client, cfg, 50.0, "temperature")

    assert result.side == "SKIP"
    assert result.size_usd == 0.0
    assert result.size_shares == 0.0
    assert "minimum order" in result.reason
    assert per_side["YES"].size_usd == 0.0


def test_event_portfolio_selects_one_profitable_leg(tmp_path):
    broker = PaperBroker(settings(tmp_path))

    decision = select_event_portfolio(
        broker,
        [candidate("seoul-26", "26°C", expected_net_profit_usd=1.25)],
        usable_snapshot(),
    )

    assert [(leg.market.market_id, leg.result.side, leg.result.size_usd) for leg in decision.selected] == [
        ("seoul-26", "YES", 10.0)
    ]
    assert decision.event_cap_fraction == 0.10
    assert decision.event_cap_usd == 10.0
    assert decision.expected_net_profit_usd == 1.25


def test_fee_adjusted_shares_drive_portfolio_scenario_and_open_position(tmp_path):
    cfg = settings(
        tmp_path,
        min_net_edge=0.01,
        entry_min_expected_net_return_pct=0.01,
        weather_taker_fee_rate=0.05,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
    )
    raw_market = market("seoul-26", "26\u00b0C")
    signal = WeatherSignal(0.80, 0.90, "test", "test", parse_weather_question(raw_market.question))
    client = FakeClient(
        {
            "seoul-26-yes": orderbook("seoul-26-yes", 0.39, 0.40),
            "seoul-26-no": orderbook("seoul-26-no", 0.59, 0.60),
        }
    )

    result, _per_side = evaluate_market(raw_market, signal, client, cfg, 100.0, "temperature")

    assert result.side == "YES"
    assert result.p_exec is not None
    entry_fee_per_share = polymarket_taker_fee_per_share(result.p_exec, cfg.weather_taker_fee_rate)
    gross_shares = result.size_usd / result.p_exec
    fee_adjusted_shares = result.size_usd / (result.p_exec + entry_fee_per_share)
    assert result.size_shares == pytest.approx(fee_adjusted_shares)
    assert result.size_shares < gross_shares

    broker = PaperBroker(cfg)
    decision = _apply_event_portfolio(
        broker,
        [PortfolioCandidate(raw_market, signal, result, "temperature")],
        usable_snapshot(),
    )

    assert len(broker.state.positions) == 1
    position = broker.state.positions[0]
    selected = decision.selected[0]
    assert position.shares == pytest.approx(fee_adjusted_shares)
    assert selected.result.size_shares == pytest.approx(position.shares)
    assert decision.scenario_pnl_usd["seoul-26"] == pytest.approx(position.shares - position.cost_usd, abs=1e-6)


def test_event_portfolio_allows_two_profitable_no_legs_when_growth_improves(tmp_path):
    broker = PaperBroker(settings(tmp_path, bankroll_usd=200.0))

    decision = select_event_portfolio(
        broker,
        [
            candidate("seoul-26", "26°C", side="NO", size_usd=20.0, p_true=0.10, p_exec=0.70, expected_net_profit_usd=3.0),
            candidate("seoul-27", "27°C", side="NO", size_usd=20.0, p_true=0.10, p_exec=0.70, expected_net_profit_usd=3.0),
        ],
        usable_snapshot(200.0),
    )

    assert [leg.market.market_id for leg in decision.selected] == ["seoul-26", "seoul-27"]
    assert [leg.result.side for leg in decision.selected] == ["NO", "NO"]
    assert [leg.result.size_usd for leg in decision.selected] == [10.0, 10.0]
    assert decision.selected_exposure_usd == decision.event_cap_usd == 20.0
    assert decision.scenario_pnl_usd["seoul-26"] > -20.0
    assert decision.scenario_pnl_usd["seoul-27"] > -20.0
    assert decision.scenario_pnl_usd["other"] > 0.0


def test_event_portfolio_allows_yes_no_combination_from_different_buckets(tmp_path):
    broker = PaperBroker(settings(tmp_path, bankroll_usd=200.0))

    decision = select_event_portfolio(
        broker,
        [
            candidate("seoul-25", "25°C", side="YES", size_usd=20.0, p_true=0.31, p_exec=0.21, expected_net_profit_usd=4.0),
            candidate("seoul-27", "27°C", side="NO", size_usd=20.0, p_true=0.06, p_exec=0.76, expected_net_profit_usd=3.0),
        ],
        usable_snapshot(200.0),
    )

    assert [(leg.market.market_id, leg.result.side) for leg in decision.selected] == [
        ("seoul-25", "YES"),
        ("seoul-27", "NO"),
    ]
    assert decision.selected_exposure_usd == 20.0


def test_event_portfolio_blocks_opposite_position_in_same_market(tmp_path):
    broker = PaperBroker(settings(tmp_path))
    broker.state.positions = [
        PaperPosition(
            position_id="held",
            market_id="seoul-26",
            question=market("seoul-26", "26°C").question,
            token_id="seoul-26-yes",
            side="YES",
            entry_price=0.40,
            shares=12.5,
            cost_usd=5.0,
            opened_at="2026-06-01T00:00:00+00:00",
            metadata={"city": "seoul", "date_hint": "may 25"},
        )
    ]
    opposite = candidate("seoul-26", "26°C", side="NO")

    decision = select_event_portfolio(broker, [opposite], usable_snapshot())

    assert decision.selected == []
    assert any("same-market position already open" in item.reason for item in decision.rejected)


def test_event_portfolio_splits_shared_budget_instead_of_multiplying_it(tmp_path):
    broker = PaperBroker(settings(tmp_path, bankroll_usd=200.0))

    decision = select_event_portfolio(
        broker,
        [
            candidate("seoul-26", "26°C", side="NO", size_usd=20.0, p_true=0.10, p_exec=0.70, expected_net_profit_usd=3.0),
            candidate("seoul-27", "27°C", side="NO", size_usd=20.0, p_true=0.10, p_exec=0.70, expected_net_profit_usd=3.0),
        ],
        usable_snapshot(200.0),
    )

    assert [leg.result.size_usd for leg in decision.selected] == [10.0, 10.0]
    assert decision.selected_exposure_usd == decision.event_cap_usd == 20.0


def test_event_portfolio_small_account_uses_one_ten_dollar_leg_instead_of_five_plus_five(tmp_path):
    broker = PaperBroker(settings(tmp_path))

    decision = select_event_portfolio(
        broker,
        [
            candidate("seoul-26", "26°C", side="NO", p_true=0.10, p_exec=0.70, expected_net_profit_usd=1.5),
            candidate("seoul-27", "27°C", side="NO", p_true=0.10, p_exec=0.70, expected_net_profit_usd=1.5),
        ],
        usable_snapshot(),
    )

    assert len(decision.selected) == 1
    assert decision.selected[0].result.size_usd == 10.0
    assert decision.selected_exposure_usd == 10.0


def test_event_portfolio_normalizes_exhaustive_bucket_probabilities_to_one(tmp_path):
    broker = PaperBroker(settings(tmp_path))

    decision = select_event_portfolio(
        broker,
        [
            candidate("seoul-low", "25°C or below", p_true=0.35, p_exec=0.20, expected_net_profit_usd=2.0),
            candidate("seoul-26", "26°C", p_true=0.40, p_exec=0.25, expected_net_profit_usd=2.0),
            candidate("seoul-high", "27°C or higher", p_true=0.28, p_exec=0.20, expected_net_profit_usd=2.0),
        ],
        usable_snapshot(),
    )

    assert sum(decision.scenario_probabilities.values()) == 1.0
    assert decision.scenario_probabilities == {
        "seoul-low": 0.339805825243,
        "seoul-26": 0.388349514563,
        "seoul-high": 0.271844660194,
    }


def test_event_portfolio_log_reconstructs_budget_legs_rejections_and_scenarios(tmp_path):
    broker = PaperBroker(settings(tmp_path, bankroll_usd=200.0))
    decision = select_event_portfolio(
        broker,
        [
            candidate("seoul-26", "26°C", side="NO", size_usd=20.0, p_true=0.10, p_exec=0.70, expected_net_profit_usd=3.0),
            candidate("seoul-27", "27°C", side="NO", size_usd=20.0, p_true=0.10, p_exec=0.70, expected_net_profit_usd=3.0),
            candidate("seoul-28", "28°C", side="YES", size_usd=20.0, p_true=0.01, p_exec=0.90, expected_net_profit_usd=0.10),
        ],
        usable_snapshot(200.0),
    )

    broker.log_event_portfolio_decision(decision.to_log_payload())

    row = json.loads((tmp_path / "portfolio.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["event_key"] == "seoul-may-25"
    assert row["entry_bankroll_usd"] == 200.0
    assert row["event_cap_usd"] == 20.0
    assert [leg["market_id"] for leg in row["selected_legs"]] == ["seoul-26", "seoul-27"]
    assert row["rejected_legs"][0]["market_id"] == "seoul-28"
    assert sum(row["scenario_probabilities"].values()) == 1.0
    assert row["expected_log_growth"] > 0.0
    assert row["scenario_pnl_usd"]["other"] > 0.0


def test_broker_small_account_allows_two_legs_inside_shared_ten_percent_cap(tmp_path):
    broker = PaperBroker(settings(tmp_path, bankroll_usd=200.0))
    first = candidate("seoul-26", "26°C", side="NO")
    second = candidate("seoul-27", "27°C", side="NO")
    third = candidate("seoul-28", "28°C", side="NO")

    first_pos = broker.open_position(
        first.market,
        first.market.no_token_id or "",
        first.result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=200.0,
    )
    second_pos = broker.open_position(
        second.market,
        second.market.no_token_id or "",
        second.result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=200.0,
    )
    third_pos = broker.open_position(
        third.market,
        third.market.no_token_id or "",
        third.result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=200.0,
    )

    assert first_pos is not None
    assert second_pos is not None
    assert third_pos is None
    assert broker.event_date_exposure("seoul", "may 25") == 20.0


def test_broker_thousand_dollar_account_uses_five_percent_city_date_cap(tmp_path):
    broker = PaperBroker(settings(tmp_path, bankroll_usd=1000.0))
    first = candidate("seoul-26", "26°C", size_usd=50.0)
    second = candidate("seoul-27", "27°C", size_usd=50.0)

    first_pos = broker.open_position(
        first.market,
        first.market.yes_token_id or "",
        first.result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
    )
    second_pos = broker.open_position(
        second.market,
        second.market.yes_token_id or "",
        second.result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=1000.0,
    )

    assert first_pos is not None
    assert second_pos is None
    assert broker.event_date_exposure("seoul", "may 25") == 50.0


def test_broker_blocks_third_city_date_leg_even_when_small_orders_fit_budget(tmp_path):
    broker = PaperBroker(settings(tmp_path, bankroll_usd=300.0))
    positions = []
    for market_id, bucket in [("seoul-26", "26°C"), ("seoul-27", "27°C"), ("seoul-28", "28°C")]:
        item = candidate(market_id, bucket, side="NO", size_usd=10.0)
        positions.append(
            broker.open_position(
                item.market,
                item.market.no_token_id or "",
                item.result,
                city="seoul",
                date_hint="may 25",
                entry_bankroll_usd=300.0,
            )
        )

    assert positions[0] is not None
    assert positions[1] is not None
    assert positions[2] is None
    assert broker.event_date_exposure("seoul", "may 25") == 20.0


def test_broker_blocks_direct_same_market_opposite_position(tmp_path):
    broker = PaperBroker(settings(tmp_path))
    yes = candidate("seoul-26", "26°C", side="YES")
    no = candidate("seoul-26", "26°C", side="NO")

    yes_pos = broker.open_position(
        yes.market,
        yes.market.yes_token_id or "",
        yes.result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=100.0,
    )
    no_pos = broker.open_position(
        no.market,
        no.market.no_token_id or "",
        no.result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=100.0,
    )

    assert yes_pos is not None
    assert no_pos is None
    assert broker.event_date_position_count("seoul", "may 25") == 1


def test_broker_allows_direct_repeated_no_city_date_positions_inside_shared_budget(tmp_path):
    broker = PaperBroker(settings(tmp_path, bankroll_usd=200.0))
    first = candidate("seoul-26", "26°C", side="NO")
    second = candidate("seoul-27", "27°C", side="NO")

    first_pos = broker.open_position(
        first.market,
        first.market.no_token_id or "",
        first.result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=200.0,
    )
    second_pos = broker.open_position(
        second.market,
        second.market.no_token_id or "",
        second.result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=200.0,
    )

    assert first_pos is not None
    assert second_pos is not None
    assert broker.event_date_position_count("seoul", "may 25") == 2


def test_broker_rejects_orders_below_ten_dollars(tmp_path):
    broker = PaperBroker(settings(tmp_path))
    too_small = candidate("seoul-26", "26°C", size_usd=9.99)

    pos = broker.open_position(
        too_small.market,
        too_small.market.yes_token_id or "",
        too_small.result,
        city="seoul",
        date_hint="may 25",
        entry_bankroll_usd=100.0,
    )

    assert pos is None


def test_broker_city_cap_allows_two_dates_but_blocks_third(tmp_path):
    broker = PaperBroker(settings(tmp_path))
    positions = []
    for idx, date_hint in enumerate(("may 25", "may 26", "may 27"), start=1):
        item = candidate(f"seoul-{idx}", f"{25 + idx}°C")
        positions.append(
            broker.open_position(
                item.market,
                item.market.yes_token_id or "",
                item.result,
                city="seoul",
                date_hint=date_hint,
                entry_bankroll_usd=100.0,
            )
        )

    assert positions[0] is not None
    assert positions[1] is not None
    assert positions[2] is None
    assert broker.city_exposure("seoul") == 20.0


def test_broker_total_open_exposure_cap_is_ninety_percent(tmp_path):
    broker = PaperBroker(settings(tmp_path))
    positions = []
    for idx in range(10):
        item = candidate(f"city-{idx}", f"{20 + idx}°C")
        positions.append(
            broker.open_position(
                item.market,
                item.market.yes_token_id or "",
                item.result,
                city=f"city-{idx}",
                date_hint=f"may {idx + 1}",
                entry_bankroll_usd=100.0,
            )
        )

    assert all(pos is not None for pos in positions[:9])
    assert positions[9] is None
    assert broker.total_exposure() == 90.0


def test_runner_applies_selected_event_portfolio_and_writes_one_event_log(tmp_path):
    broker = PaperBroker(settings(tmp_path, bankroll_usd=200.0))
    decision = _apply_event_portfolio(
        broker,
        [
            candidate("seoul-26", "26°C", side="NO", size_usd=20.0, p_true=0.10, p_exec=0.70, expected_net_profit_usd=3.0),
            candidate("seoul-27", "27°C", side="NO", size_usd=20.0, p_true=0.10, p_exec=0.70, expected_net_profit_usd=3.0),
        ],
        usable_snapshot(200.0),
    )

    assert [pos.market_id for pos in broker.state.positions] == ["seoul-26", "seoul-27"]
    assert [leg.market.market_id for leg in decision.selected] == ["seoul-26", "seoul-27"]
    rows = (tmp_path / "portfolio.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1


def test_run_cycle_opens_city_date_candidates_as_one_logged_portfolio(monkeypatch, tmp_path):
    cfg = settings(
        tmp_path,
        bankroll_usd=200.0,
        min_net_edge=0.01,
        entry_min_expected_net_return_pct=0.01,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
    )
    markets = [market("seoul-26", "26°C"), market("seoul-27", "27°C")]
    books = {
        "seoul-26-yes": orderbook("seoul-26-yes", 0.39, 0.40),
        "seoul-26-no": orderbook("seoul-26-no", 0.59, 0.60),
        "seoul-27-yes": orderbook("seoul-27-yes", 0.39, 0.40),
        "seoul-27-no": orderbook("seoul-27-no", 0.59, 0.60),
    }

    class CycleClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def discover_weather_markets(self, max_pages: int, page_size: int):
            return markets

        def get_order_book(self, token_id: str) -> OrderBook:
            return books[token_id]

        def get_market(self, market_id: str) -> RawMarket:
            return next(item for item in markets if item.market_id == market_id)

    def estimate(question, **_kwargs):
        parsed = parse_weather_question(question)
        return WeatherSignal(0.80, 0.90, "test", "test", parsed)

    monkeypatch.setattr("weather_bot.live_paper_runner.PolymarketClient", CycleClient)
    monkeypatch.setattr("weather_bot.live_paper_runner.estimate_weather_probability", estimate)

    run_cycle(cfg)

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    rows = (tmp_path / "portfolio.jsonl").read_text(encoding="utf-8").splitlines()
    assert [pos["market_id"] for pos in state["positions"]] == ["seoul-26", "seoul-27"]
    assert len(rows) == 1
    assert [leg["market_id"] for leg in json.loads(rows[0])["selected_legs"]] == ["seoul-26", "seoul-27"]


def test_run_cycle_logs_skip_when_entry_bankroll_cannot_price_held_position(monkeypatch, tmp_path):
    cfg = settings(
        tmp_path,
        bankroll_usd=100.0,
        min_net_edge=0.01,
        entry_min_expected_net_return_pct=0.01,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
    )
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "cash_usd": 90.0,
                "positions": [
                    {
                        "position_id": "held",
                        "market_id": "held",
                        "question": "Will the highest temperature in Seoul be 25\u00b0C on May 25?",
                        "token_id": "missing-token",
                        "side": "YES",
                        "entry_price": 0.50,
                        "shares": 20.0,
                        "cost_usd": 10.0,
                        "opened_at": "2026-06-01T00:00:00+00:00",
                        "metadata": {"city": "seoul", "date_hint": "may 25"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    markets = [market("seoul-26", "26\u00b0C")]
    books = {
        "seoul-26-yes": orderbook("seoul-26-yes", 0.39, 0.40),
        "seoul-26-no": orderbook("seoul-26-no", 0.59, 0.60),
    }

    class CycleClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def discover_weather_markets(self, max_pages: int, page_size: int):
            return markets

        def get_order_book(self, token_id: str) -> OrderBook:
            return books[token_id]

        def get_market(self, market_id: str) -> RawMarket:
            if market_id == "held":
                return RawMarket(
                    market_id="held",
                    question="Will the highest temperature in Seoul be 25\u00b0C on May 25?",
                    slug="held",
                    active=True,
                    closed=False,
                    yes_token_id="missing-token",
                    no_token_id=None,
                    event_id="seoul-may-25",
                )
            return next(item for item in markets if item.market_id == market_id)

    def estimate(question, **_kwargs):
        parsed = parse_weather_question(question)
        return WeatherSignal(0.80, 0.90, "test", "test", parsed)

    monkeypatch.setattr("weather_bot.live_paper_runner.PolymarketClient", CycleClient)
    monkeypatch.setattr("weather_bot.live_paper_runner.estimate_weather_probability", estimate)

    decisions = run_cycle(cfg)

    assert len(decisions) == 1
    assert decisions[0].result.side == "SKIP"
    assert "기존 포지션을 안전하게 평가할 수 없어 신규 진입 차단" in decisions[0].result.reason
    rows = (tmp_path / "portfolio.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(rows[0])["entry_bankroll_usable"] is False


def test_run_cycle_opens_two_profitable_no_legs_for_same_event(monkeypatch, tmp_path):
    cfg = settings(
        tmp_path,
        bankroll_usd=200.0,
        min_net_edge=0.01,
        entry_min_expected_net_return_pct=0.01,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
    )
    markets = [market("seoul-26", "26°C"), market("seoul-27", "27°C")]
    books = {
        "seoul-26-yes": orderbook("seoul-26-yes", 0.29, 0.30),
        "seoul-26-no": orderbook("seoul-26-no", 0.69, 0.70),
        "seoul-27-yes": orderbook("seoul-27-yes", 0.29, 0.30),
        "seoul-27-no": orderbook("seoul-27-no", 0.69, 0.70),
    }

    class CycleClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def discover_weather_markets(self, max_pages: int, page_size: int):
            return markets

        def get_order_book(self, token_id: str) -> OrderBook:
            return books[token_id]

        def get_market(self, market_id: str) -> RawMarket:
            return next(item for item in markets if item.market_id == market_id)

    def estimate(question, **_kwargs):
        parsed = parse_weather_question(question)
        return WeatherSignal(0.10, 0.90, "test", "test", parsed)

    monkeypatch.setattr("weather_bot.live_paper_runner.PolymarketClient", CycleClient)
    monkeypatch.setattr("weather_bot.live_paper_runner.estimate_weather_probability", estimate)

    run_cycle(cfg)

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    rows = (tmp_path / "portfolio.jsonl").read_text(encoding="utf-8").splitlines()
    assert [(pos["market_id"], pos["side"], pos["cost_usd"]) for pos in state["positions"]] == [
        ("seoul-26", "NO", 10.0),
        ("seoul-27", "NO", 10.0),
    ]
    assert [leg["side"] for leg in json.loads(rows[0])["selected_legs"]] == ["NO", "NO"]


def test_realtime_update_reselects_the_whole_city_date_event(monkeypatch, tmp_path):
    cfg = settings(
        tmp_path,
        bankroll_usd=200.0,
        min_net_edge=0.01,
        entry_min_expected_net_return_pct=0.01,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
    )
    broker = PaperBroker(cfg)
    markets = [market("seoul-26", "26°C"), market("seoul-27", "27°C")]
    books = {
        "seoul-26-yes": orderbook("seoul-26-yes", 0.39, 0.40),
        "seoul-26-no": orderbook("seoul-26-no", 0.59, 0.60),
        "seoul-27-yes": orderbook("seoul-27-yes", 0.39, 0.40),
        "seoul-27-no": orderbook("seoul-27-no", 0.59, 0.60),
    }
    client = FakeClient(books)
    signal = WeatherSignal(0.80, 0.90, "test", "test", parse_weather_question(markets[0].question))
    signals = {
        markets[0].market_id: signal,
        markets[1].market_id: WeatherSignal(
            0.80,
            0.90,
            "test",
            "test",
            parse_weather_question(markets[1].question),
        ),
    }
    market_by_token = {
        token_id: raw_market
        for raw_market in markets
        for token_id in (raw_market.yes_token_id, raw_market.no_token_id)
        if token_id
    }

    _evaluate_realtime_update(
        {"seoul-26-yes"},
        client,
        broker,
        cfg,
        market_by_token,
        signals,
        {raw_market.market_id: "temperature" for raw_market in markets},
        {},
    )

    rows = (tmp_path / "portfolio.jsonl").read_text(encoding="utf-8").splitlines()
    assert [pos.market_id for pos in broker.state.positions] == ["seoul-26", "seoul-27"]
    assert len(rows) == 1
