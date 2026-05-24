from __future__ import annotations

import json
from datetime import datetime, timezone

import requests

from weather_bot.config import Settings
from weather_bot.edge import no_net_edge, yes_net_edge
from weather_bot.live_paper_runner import evaluate_market, refresh_open_position_edges
from weather_bot.models import EdgeResult, OrderBook, OrderLevel, PaperPosition, PaperState, RawMarket, WeatherSignal
from weather_bot.paper import PaperBroker, maybe_settle_resolved_positions
from weather_bot.polymarket_client import PolymarketClient
from weather_bot.probability import _target_date_from_hint
from weather_bot.weather_client import parse_weather_question


class FakePolymarketClient(PolymarketClient):
    def __init__(self, pages: dict[int, list[dict]] | None = None, books: dict[str, OrderBook] | None = None) -> None:
        super().__init__("https://gamma.example", "https://clob.example")
        self.pages = pages or {}
        self.books = books or {}

    def _get(self, url: str, params: dict | None = None):
        offset = int((params or {}).get("offset", 0))
        return self.pages.get(offset, [])

    def get_order_book(self, token_id: str) -> OrderBook:
        return self.books[token_id]


def book(token_id: str, bid: float, ask: float, bid_size: float = 100.0, ask_size: float = 100.0) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        bids=[OrderLevel(bid, bid_size)],
        asks=[OrderLevel(ask, ask_size)],
    )


def temp_signal(p_true: float = 0.2) -> WeatherSignal:
    parsed = parse_weather_question("Will NYC reach 90°F on May 25?")
    return WeatherSignal(p_true=p_true, confidence=0.9, source="test", note="", parsed=parsed)


def temp_market() -> RawMarket:
    return RawMarket(
        market_id="m1",
        question="Will NYC reach 90°F on May 25?",
        slug="nyc-90f-may-25",
        active=True,
        closed=False,
        yes_token_id="yes",
        no_token_id="no",
        condition_id="cond1",
    )


def test_discovery_uses_weather_question_shape_and_paginates():
    client = FakePolymarketClient(
        pages={
            0: [{"id": "x", "question": "Will unrelated thing happen?", "clobTokenIds": json.dumps(["x_yes", "x_no"])}],
            50: [{"id": "m1", "question": "Will NYC reach 90°F on May 25?", "clobTokenIds": json.dumps(["yes", "no"])}],
            100: [],
        }
    )

    markets = client.discover_weather_markets(limit=1)

    assert [m.market_id for m in markets] == ["m1"]


def test_discovery_rejects_non_weather_questions_with_ambiguous_words_and_dates():
    false_positives = [
        "Will the Carolina Hurricanes win the 2026 NHL Stanley Cup?",
        "Zelenskyy out as Ukraine president by end of 2026?",
        "Will Mamdani freeze NYC rents before 2027?",
        "Will Waymo launch in Washington DC by June 30 2026?",
        "Will Waymo operate in 6 cities on June 30 2026?",
        "Will the Democrats win the New York governor race in 2026?",
        "Will Boston Breach finish in the top 4 of the CDL Regular Season?",
        "Will the Chicago White Sox win the 2026 World Series?",
        "Will Seattle Sounders FC win the 2026 MLS Cup?",
    ]

    for question in false_positives:
        assert not PolymarketClient._is_weather_market({"question": question})


def test_discovery_keeps_supported_weather_question_shapes():
    true_weather_questions = [
        "Will NYC reach 90 F on May 25?",
        "Will it rain in NYC on Friday?",
        "Will Chicago get more than 0.5 inches of rain on May 25?",
    ]

    for question in true_weather_questions:
        assert PolymarketClient._is_weather_market({"question": question})


def test_discovery_stops_at_page_limit_without_fetching_deep_offsets():
    seen_offsets: list[int] = []

    class PagingClient(FakePolymarketClient):
        def _get(self, url: str, params: dict | None = None):
            offset = int((params or {}).get("offset", 0))
            seen_offsets.append(offset)
            if offset >= 100:
                raise AssertionError("deep page should not be fetched")
            return [{"id": str(offset), "question": "Will unrelated thing happen?", "clobTokenIds": json.dumps(["yes", "no"])}]

    markets = PagingClient().discover_weather_markets(limit=1, max_pages=2)

    assert markets == []
    assert seen_offsets == [0, 50]


def test_discovery_returns_partial_results_when_later_gamma_page_errors():
    class FlakyClient(FakePolymarketClient):
        def _get(self, url: str, params: dict | None = None):
            offset = int((params or {}).get("offset", 0))
            if offset == 0:
                return [{"id": "m1", "question": "Will NYC reach 90 F on May 25?", "clobTokenIds": json.dumps(["yes", "no"])}]
            raise requests.HTTPError("later page failed")

    markets = FlakyClient().discover_weather_markets(limit=2)

    assert [market.market_id for market in markets] == ["m1"]


def test_vwap_slippage_is_not_subtracted_twice():
    assert abs(yes_net_edge(0.60, 0.55, 0.0, 0.05, 0.0, 0.0) - 0.05) < 1e-12
    assert abs(no_net_edge(0.40, 0.55, 0.0, 0.05, 0.0, 0.0) - 0.05) < 1e-12


def test_no_candidate_requires_no_side_exit_liquidity():
    settings = Settings(
        min_net_edge=0.01,
        min_order_usd=1.0,
        estimated_fee_per_share=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        require_date_hint_for_trade=True,
    )
    client = FakePolymarketClient(
        books={
            "yes": book("yes", bid=0.59, ask=0.60, bid_size=100.0, ask_size=100.0),
            "no": book("no", bid=0.39, ask=0.40, bid_size=1.0, ask_size=100.0),
        }
    )

    result, per_side = evaluate_market(temp_market(), temp_signal(p_true=0.2), client, settings, 1000.0, "temperature")

    assert result.side == "SKIP"
    assert per_side["NO"].side == "SKIP"
    assert "NO" in per_side["NO"].reason
    assert "liquidity" in per_side["NO"].reason.lower()


def test_today_date_uses_station_timezone_not_local_machine_timezone():
    parsed = parse_weather_question("Will NYC reach 90°F today?")
    now_utc = datetime(2026, 5, 25, 1, 0, tzinfo=timezone.utc)

    assert _target_date_from_hint(parsed, timezone_name="America/New_York", now=now_utc).isoformat() == "2026-05-24"


def test_weekday_date_hint_is_parsed():
    parsed = parse_weather_question("Will it rain in Chicago on Friday?")

    assert parsed.date_hint == "friday"


def test_held_positions_are_re_evaluated_even_when_not_in_scan_results():
    settings = Settings(
        min_net_edge=0.01,
        estimated_fee_per_share=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        require_date_hint_for_trade=True,
    )
    broker = PaperBroker(settings)
    broker.state = PaperState(
        cash_usd=950.0,
        positions=[
            PaperPosition(
                position_id="p1",
                market_id="held",
                question="Will NYC reach 90°F on May 25?",
                token_id="yes",
                side="YES",
                entry_price=0.50,
                shares=100.0,
                cost_usd=50.0,
                opened_at=datetime.now(timezone.utc).isoformat(),
                metadata={"entry_p_true": 0.8, "stop_loss_price": 0.45, "city": "nyc", "date_hint": "may 25"},
            )
        ],
    )
    client = FakePolymarketClient(books={"yes": book("yes", bid=0.49, ask=0.50, bid_size=200.0, ask_size=200.0)})

    latest_edges: dict[tuple[str, str], EdgeResult] = {}
    refresh_open_position_edges(
        broker,
        client,
        settings,
        latest_edges,
        {},
        probability_estimator=lambda question, settings=None: temp_signal(p_true=0.1),
    )

    assert latest_edges[("held", "YES")].p_true == 0.1


def test_resolved_market_settles_to_binary_payout():
    settings = Settings()
    broker = PaperBroker(settings)
    broker.state = PaperState(
        cash_usd=900.0,
        positions=[
            PaperPosition(
                position_id="p1",
                market_id="m1",
                question="Will NYC reach 90°F on May 25?",
                token_id="yes",
                side="YES",
                entry_price=0.40,
                shares=100.0,
                cost_usd=40.0,
                opened_at=datetime.now(timezone.utc).isoformat(),
            )
        ],
    )
    market = RawMarket(
        market_id="m1",
        question="Will NYC reach 90°F on May 25?",
        slug="nyc-90f-may-25",
        active=False,
        closed=True,
        yes_token_id="yes",
        no_token_id="no",
        raw={"resolved": True, "winningOutcome": "Yes"},
    )

    messages = maybe_settle_resolved_positions(broker, {"m1": market})

    assert messages == ["SETTLED YES pnl=$60.00 payout=1.0000 reason=resolved winner=YES"]
    assert broker.state.cash_usd == 1000.0
    assert broker.state.positions == []


def test_raw_snapshot_log_is_jsonl(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "snapshots.jsonl"),
    )
    broker = PaperBroker(settings)
    market = temp_market()

    broker.log_raw_snapshot("entry", market, {"orderbook": {"bids": [["0.49", "10"]]}})

    rows = (tmp_path / "snapshots.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(rows[0])
    assert payload["event"] == "entry"
    assert payload["market_id"] == "m1"
    assert payload["payload"]["orderbook"]["bids"] == [["0.49", "10"]]
