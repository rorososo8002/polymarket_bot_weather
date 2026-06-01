from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import requests

from weather_bot.config import Settings
from weather_bot.edge import no_net_edge, yes_net_edge
from weather_bot.live_paper_runner import _sleep_seconds_until_next_cycle, evaluate_market, refresh_open_position_edges, run_cycle
from weather_bot.models import EdgeResult, OrderBook, OrderLevel, PaperPosition, PaperState, RawMarket, WeatherSignal
from weather_bot.paper import PaperBroker, maybe_close_positions, maybe_settle_resolved_positions
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

    def _get_web_text(self, path: str) -> str:
        return ""

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


def unavailable_forecast_signal() -> WeatherSignal:
    parsed = parse_weather_question("Will NYC reach 90°F on May 25?")
    return WeatherSignal(
        p_true=0.5,
        confidence=0.0,
        source="forecast-unavailable",
        note="ensemble forecast unavailable",
        parsed=parsed,
    )


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


def test_discovery_uses_polymarket_weather_category_event_slugs():
    class CategoryClient(FakePolymarketClient):
        def _get_web_text(self, path: str) -> str:
            if path == "/weather/temperature":
                return '<a href="/event/highest-temperature-in-seoul-on-may-25-2026">Seoul</a>'
            return ""

        def _get(self, url: str, params: dict | None = None):
            if "/events/slug/highest-temperature-in-seoul-on-may-25-2026" in url:
                return {
                    "markets": [
                        {
                            "id": "m1",
                            "question": "Will the highest temperature in Seoul be 27\u00b0C or higher on May 25?",
                            "clobTokenIds": json.dumps(["yes", "no"]),
                        },
                        {
                            "id": "m2",
                            "question": "Will the highest temperature in Seoul be 26\u00b0C on May 25?",
                            "clobTokenIds": json.dumps(["yes2", "no2"]),
                        },
                    ]
                }
            return []

    markets = CategoryClient().discover_weather_markets(limit=5)

    assert [market.market_id for market in markets] == ["m1"]


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
        "Will the highest temperature in Seoul be 27\u00b0C or higher on May 25?",
        "Will the highest temperature in London be 26\u00b0C or below on May 25?",
        "Will it rain in NYC on Friday?",
        "Will Chicago get more than 0.5 inches of rain on May 25?",
    ]

    for question in true_weather_questions:
        assert PolymarketClient._is_weather_market({"question": question})


def test_discovery_rejects_exact_temperature_bucket_until_model_supports_ranges():
    assert not PolymarketClient._is_weather_market(
        {"question": "Will the highest temperature in Seoul be 26\u00b0C on May 25?"}
    )


def test_discovery_rejects_weather_markets_outside_verified_station_set():
    assert not PolymarketClient._is_weather_market(
        {"question": "Will the highest temperature in Austin be 34\u00b0C or higher on May 25?"}
    )


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
    assert abs(yes_net_edge(0.60, 0.55, 0.0, 0.0, 0.0) - 0.05) < 1e-12
    assert abs(no_net_edge(0.40, 0.55, 0.0, 0.0, 0.0) - 0.05) < 1e-12


def test_no_candidate_requires_no_side_exit_liquidity():
    settings = Settings(
        min_net_edge=0.01,
        min_order_usd=1.0,
        weather_taker_fee_rate=0.0,
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


def test_no_valid_side_decision_explains_side_liquidity_filters():
    settings = Settings(
        min_net_edge=0.01,
        min_order_usd=1.0,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        require_date_hint_for_trade=True,
    )
    client = FakePolymarketClient(
        books={
            "yes": book("yes", bid=0.001, ask=0.0, bid_size=1000.0, ask_size=1000.0),
            "no": book("no", bid=0.98, ask=1.0, bid_size=1000.0, ask_size=1000.0),
        }
    )

    result, per_side = evaluate_market(temp_market(), temp_signal(p_true=0.01), client, settings, 1000.0, "temperature")

    assert result.side == "SKIP"
    assert "No valid side evaluated" in result.reason
    assert "YES liquidity filter: invalid ask=0.000" in result.reason
    assert "NO liquidity filter: invalid ask=1.000" in result.reason
    assert per_side["YES"].reason in result.reason
    assert per_side["NO"].reason in result.reason


def test_entry_does_not_use_fixed_price_drop_guard():
    settings = Settings(
        min_net_edge=0.01,
        min_order_usd=1.0,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        require_date_hint_for_trade=True,
    )
    client = FakePolymarketClient(
        books={
            "yes": book("yes", bid=0.08, ask=0.13, bid_size=1000.0, ask_size=1000.0),
            "no": book("no", bid=0.86, ask=0.87, bid_size=1000.0, ask_size=1000.0),
        }
    )

    result, per_side = evaluate_market(temp_market(), temp_signal(p_true=0.25), client, settings, 1000.0, "temperature")

    assert result.side == "YES"
    assert per_side["YES"].side == "YES"
    assert "YES edge=" in per_side["YES"].reason


def test_entry_net_return_filter_rejects_thin_high_price_trade(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        min_net_edge=0.01,
        entry_min_expected_net_return_pct=0.06,
        weather_taker_fee_rate=0.05,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        require_date_hint_for_trade=True,
    )
    client = FakePolymarketClient(
        books={
            "yes": book("yes", bid=0.87, ask=0.88, bid_size=1000.0, ask_size=1000.0),
            "no": book("no", bid=0.11, ask=0.12, bid_size=1000.0, ask_size=1000.0),
        }
    )

    result, per_side = evaluate_market(temp_market(), temp_signal(p_true=0.92), client, settings, 1000.0, "temperature")
    PaperBroker(settings).log_decision(temp_market(), result, "test", "temperature")

    assert result.side == "SKIP"
    assert per_side["YES"].net_edge > settings.min_net_edge
    assert "expected_gross=" in result.reason
    assert "estimated_cost=" in result.reason
    assert "expected_net_return=" in result.reason
    assert "reject=expected net return below 6.00%" in result.reason
    assert "reject=expected net return below 6.00%" in (tmp_path / "decisions.csv").read_text(encoding="utf-8")


def test_entry_net_return_filter_allows_high_price_settlement_candidate():
    settings = Settings(
        min_net_edge=0.01,
        entry_min_expected_net_return_pct=0.06,
        weather_taker_fee_rate=0.05,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        require_date_hint_for_trade=True,
    )
    client = FakePolymarketClient(
        books={
            "yes": book("yes", bid=0.92, ask=0.93, bid_size=1000.0, ask_size=1000.0),
            "no": book("no", bid=0.06, ask=0.07, bid_size=1000.0, ask_size=1000.0),
        }
    )

    result, per_side = evaluate_market(temp_market(), temp_signal(p_true=1.0), client, settings, 1000.0, "temperature")

    assert result.side == "YES"
    assert per_side["YES"].side == "YES"
    assert "route=settlement" in result.reason
    assert "expected_net_return=" in result.reason


def test_unavailable_forecast_signals_do_not_trade():
    settings = Settings(
        min_net_edge=0.01,
        min_order_usd=1.0,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        require_date_hint_for_trade=True,
    )
    client = FakePolymarketClient(
        books={
            "yes": book("yes", bid=0.40, ask=0.41, bid_size=100.0, ask_size=100.0),
            "no": book("no", bid=0.58, ask=0.59, bid_size=100.0, ask_size=100.0),
        }
    )

    result, per_side = evaluate_market(
        temp_market(),
        unavailable_forecast_signal(),
        client,
        settings,
        1000.0,
        "temperature",
    )

    assert result.side == "SKIP"
    assert per_side == {}
    assert "confidence too low" in result.reason


def test_probability_stop_closes_immediately(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        probability_stop_drop_threshold=0.10,
    )
    broker = PaperBroker(settings)
    pos = PaperPosition(
        position_id="p1",
        market_id="m1",
        question="Will NYC reach 90 F on May 25?",
        token_id="yes",
        side="YES",
        entry_price=0.50,
        shares=10.0,
        cost_usd=5.0,
        opened_at=datetime.now(timezone.utc).isoformat(),
        metadata={"entry_p_true": 0.70, "probability_stop_threshold": 0.60},
    )
    broker.state.positions = [pos]
    broker.state.cash_usd = 995.0
    client = FakePolymarketClient(books={"yes": book("yes", bid=0.50, ask=0.52, bid_size=100.0)})
    latest_edges = {("m1", "YES"): EdgeResult("YES", 0.59, 0.50, -0.01, 0.0, 0.0, "latest")}

    messages = maybe_close_positions(broker, client, {"m1": temp_market()}, latest_edges)
    assert any("probability stop" in msg for msg in messages)
    assert broker.state.positions == []


def test_forever_loop_sleep_subtracts_cycle_runtime():
    started_at = datetime(2026, 5, 24, 16, 0, tzinfo=timezone.utc)

    assert _sleep_seconds_until_next_cycle(started_at, 300, now=started_at + timedelta(seconds=180)) == 120
    assert _sleep_seconds_until_next_cycle(started_at, 300, now=started_at + timedelta(seconds=301)) == 0


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
        weather_taker_fee_rate=0.0,
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
                metadata={"entry_p_true": 0.8, "probability_stop_threshold": 0.7, "city": "nyc", "date_hint": "may 25"},
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


def test_run_cycle_reuses_one_ensemble_client_for_all_markets(monkeypatch, tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        min_net_edge=999.0,
        require_date_hint_for_trade=False,
    )
    markets = [
        RawMarket("m1", "Will NYC reach 90 F on May 25?", "m1", True, False, "yes1", "no1"),
        RawMarket("m2", "Will NYC reach 80 F on May 25?", "m2", True, False, "yes2", "no2"),
    ]
    ensemble_ids: list[int] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def discover_weather_markets(self, limit: int):
            return markets[:limit]

        def get_order_book(self, token_id: str) -> OrderBook:
            return book(token_id, bid=0.40, ask=0.50, bid_size=100.0, ask_size=100.0)

        def get_market(self, market_id: str) -> RawMarket:
            return next(m for m in markets if m.market_id == market_id)

    def fake_estimator(question, settings=None, client=None, ensemble_client=None):
        assert ensemble_client is not None
        ensemble_ids.append(id(ensemble_client))
        return temp_signal(p_true=0.5)

    monkeypatch.setattr("weather_bot.live_paper_runner.PolymarketClient", FakeClient)
    monkeypatch.setattr("weather_bot.live_paper_runner.estimate_weather_probability", fake_estimator)

    run_cycle(settings)

    assert len(ensemble_ids) == 2
    assert len(set(ensemble_ids)) == 1


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
