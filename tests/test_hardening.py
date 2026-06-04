from __future__ import annotations

import json
import csv
from datetime import datetime, timedelta, timezone

import pytest
import requests

from weather_bot.config import Settings
from weather_bot.edge import no_net_edge, polymarket_taker_fee_usdc, yes_net_edge
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


def binary_token_fields(yes_token_id: str, no_token_id: str) -> dict[str, str]:
    return {
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps([yes_token_id, no_token_id]),
    }


def test_discovery_uses_weather_question_shape_and_paginates():
    client = FakePolymarketClient(
        pages={
            0: [{"id": "e0", "markets": [{"id": "x", "question": "Will unrelated thing happen?", "clobTokenIds": json.dumps(["x_yes", "x_no"])}]}],
            50: [
                {
                    "id": "e1",
                    "markets": [
                        {
                            "id": "m1",
                            "question": "Will NYC reach 90°F on May 25?",
                            **binary_token_fields("yes", "no"),
                        }
                    ],
                }
            ],
            100: [],
        }
    )

    markets = client.discover_weather_markets(max_pages=3, page_size=50)

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
                            **binary_token_fields("yes", "no"),
                        },
                        {
                            "id": "m2",
                            "question": "Will the highest temperature in Seoul be 26\u00b0C on May 25?",
                            **binary_token_fields("yes2", "no2"),
                        },
                    ]
                }
            return []

    markets = CategoryClient().discover_weather_markets()

    assert [market.market_id for market in markets] == ["m1", "m2"]


def test_category_discovery_uses_temperature_pages_only():
    seen_paths: list[str] = []

    class CategoryClient(FakePolymarketClient):
        def _get_web_text(self, path: str) -> str:
            seen_paths.append(path)
            return ""

    CategoryClient().discover_weather_markets()

    assert seen_paths == ["/weather/temperature", "/weather/high-temperature", "/weather/low-temperature"]


def test_category_discovery_does_not_stop_after_supported_city_count():
    event_count = 42

    class ManyCategoryEventsClient(FakePolymarketClient):
        def _get_web_text(self, path: str) -> str:
            if path == "/weather/temperature":
                return "".join(
                    f'<a href="/event/highest-temperature-in-seoul-sample-{index}">Seoul</a>'
                    for index in range(event_count)
                )
            return ""

        def _get(self, url: str, params: dict | None = None):
            if "/events/slug/" in url:
                slug = url.rsplit("/", 1)[-1]
                return {
                    "id": slug,
                    "slug": slug,
                    "markets": [
                        {
                            "id": f"market-{slug}",
                            "question": "Will the highest temperature in Seoul be 26\u00b0C on May 25?",
                            **binary_token_fields(f"yes-{slug}", f"no-{slug}"),
                        }
                    ],
                }
            return []

    markets = ManyCategoryEventsClient().discover_weather_markets()

    assert len(markets) == event_count


def test_market_parser_maps_clob_tokens_by_outcomes_when_order_is_reversed():
    client = FakePolymarketClient()

    market = client._parse_market(
        {
            "id": "m1",
            "question": "Will NYC reach 90 F on May 25?",
            "outcomes": json.dumps(["No", "Yes"]),
            "clobTokenIds": json.dumps(["no-token", "yes-token"]),
        }
    )

    assert market.yes_token_id == "yes-token"
    assert market.no_token_id == "no-token"


@pytest.mark.parametrize(
    "row",
    [
        {
            "id": "missing-outcomes",
            "question": "Will NYC reach 90 F on May 25?",
            "clobTokenIds": json.dumps(["first-token", "second-token"]),
        },
        {
            "id": "ambiguous-outcomes",
            "question": "Will NYC reach 90 F on May 25?",
            "outcomes": json.dumps(["Above", "Below"]),
            "clobTokenIds": json.dumps(["first-token", "second-token"]),
        },
    ],
)
def test_discovery_skips_clob_tokens_when_outcomes_do_not_identify_yes_and_no(row):
    client = FakePolymarketClient(pages={0: [{"id": "e1", "markets": [row]}]})

    markets = client.discover_weather_markets(max_pages=1, page_size=50)

    assert markets == []


def test_discovery_rejects_non_weather_questions_with_ambiguous_words_and_dates():
    false_positives = [
        "Will the Carolina Hurricanes win the 2026 NHL Stanley Cup?",
        "Zelenskyy out as Ukraine president by end of 2026?",
        "Will Mamdani freeze NYC rents before 2027?",
        "Will NYC rents be over 10% on May 25?",
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
    ]

    for question in true_weather_questions:
        assert PolymarketClient._is_weather_market({"question": question})


def test_rest_clob_orderbook_levels_ignore_non_finite_and_invalid_numbers():
    rows = [
        {"price": "bad", "size": "10"},
        {"price": "nan", "size": "10"},
        {"price": "inf", "size": "10"},
        {"price": "-inf", "size": "10"},
        {"price": "-0.10", "size": "10"},
        {"price": "0", "size": "10"},
        {"price": "1", "size": "10"},
        {"price": "0.49", "size": "bad"},
        {"price": "0.48", "size": "nan"},
        {"price": "0.47", "size": "inf"},
        {"price": "0.46", "size": "-inf"},
        {"price": "0.45", "size": "-1"},
        {"price": "0.44", "size": "0"},
        {"price": "0.43", "size": "12"},
    ]

    levels = PolymarketClient._parse_levels(rows)

    assert [(level.price, level.size) for level in levels] == [(0.43, 12.0)]


def test_discovery_rejects_non_temperature_weather_questions():
    non_temperature_questions = [
        "Will it rain in NYC on Friday?",
        "Will Chicago get more than 0.5 inches of rain on May 25?",
        "Will Tokyo get any snow tomorrow?",
        "Will NYC wind speed exceed 20 mph on May 25?",
    ]

    for question in non_temperature_questions:
        assert not PolymarketClient._is_weather_market({"question": question})


def test_discovery_keeps_exact_temperature_bucket():
    assert PolymarketClient._is_weather_market(
        {"question": "Will the highest temperature in Seoul be 26\u00b0C on May 25?"}
    )


def test_discovery_rejects_weather_markets_outside_verified_station_set():
    assert not PolymarketClient._is_weather_market(
        {"question": "Will the highest temperature in Austin be 34\u00b0C or higher on May 25?"}
    )


def test_discovery_rejects_supported_city_without_rule_evidence(monkeypatch):
    monkeypatch.setattr("weather_bot.polymarket_client.TRADING_READY_STATION_MAP", {}, raising=False)

    assert not PolymarketClient._is_weather_market(
        {"question": "Will the highest temperature in NYC be 30\u00b0C or higher on May 25?"}
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

    markets = PagingClient().discover_weather_markets(max_pages=2, page_size=50)

    assert markets == []
    assert seen_offsets == [0, 50]


def test_discovery_returns_partial_results_when_later_gamma_page_errors():
    class FlakyClient(FakePolymarketClient):
        def _get(self, url: str, params: dict | None = None):
            offset = int((params or {}).get("offset", 0))
            if offset == 0:
                return [
                    {
                        "id": "e1",
                        "markets": [
                            {
                                "id": "m1",
                                "question": "Will NYC reach 90 F on May 25?",
                                **binary_token_fields("yes", "no"),
                            }
                        ],
                    }
                ]
            raise requests.HTTPError("later page failed")

    markets = FlakyClient().discover_weather_markets(max_pages=2, page_size=50)

    assert [market.market_id for market in markets] == ["m1"]


def test_event_discovery_keeps_every_supported_submarket_without_city_count_truncation():
    class EventClient(FakePolymarketClient):
        def _get(self, url: str, params: dict | None = None):
            if url.endswith("/events"):
                return [
                    {
                        "id": "seoul-may-25",
                        "slug": "highest-temperature-in-seoul-on-may-25-2026",
                        "markets": [
                            {
                                "id": "lower",
                                "question": "Will the highest temperature in Seoul be 18°C or below on May 25?",
                                **binary_token_fields("lower-yes", "lower-no"),
                            },
                            {
                                "id": "exact",
                                "question": "Will the highest temperature in Seoul be 19°C on May 25?",
                                **binary_token_fields("exact-yes", "exact-no"),
                            },
                            {
                                "id": "upper",
                                "question": "Will the highest temperature in Seoul be 28°C or higher on May 25?",
                                **binary_token_fields("upper-yes", "upper-no"),
                            },
                        ],
                    },
                    {
                        "id": "london-may-25",
                        "markets": [
                            {
                                "id": "london",
                                "question": "Will the highest temperature in London be 24°C on May 25?",
                                **binary_token_fields("london-yes", "london-no"),
                            }
                        ],
                    },
                ]
            return []

    markets = EventClient().discover_weather_markets(max_pages=1)

    assert [market.market_id for market in markets] == ["lower", "exact", "upper", "london"]
    assert {market.event_id for market in markets} == {"seoul-may-25", "london-may-25"}


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


def test_indicative_best_ask_does_not_hide_abnormal_yes_no_depth_sum():
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
            "yes": OrderBook(
                "yes",
                bids=[OrderLevel(0.80, 1000.0)],
                asks=[OrderLevel(0.82, 1000.0)],
                indicative_best_bid=0.48,
                indicative_best_ask=0.49,
            ),
            "no": OrderBook(
                "no",
                bids=[OrderLevel(0.80, 1000.0)],
                asks=[OrderLevel(0.82, 1000.0)],
                indicative_best_bid=0.49,
                indicative_best_ask=0.50,
            ),
        }
    )

    result, per_side = evaluate_market(temp_market(), temp_signal(p_true=0.95), client, settings, 1000.0, "temperature")

    assert result.side == "SKIP"
    assert per_side["YES"].reason == result.reason
    assert per_side["NO"].reason == result.reason
    assert "YES+NO ask sum abnormal 1.640" in result.reason


def test_indicative_best_bid_does_not_rescue_wide_executable_spread():
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
            "yes": OrderBook(
                "yes",
                bids=[OrderLevel(0.10, 1000.0)],
                asks=[OrderLevel(0.50, 1000.0)],
                indicative_best_bid=0.49,
                indicative_best_ask=0.50,
            ),
            "no": book("no", bid=0.49, ask=0.50, bid_size=1000.0, ask_size=1000.0),
        }
    )

    result, per_side = evaluate_market(temp_market(), temp_signal(p_true=0.80), client, settings, 1000.0, "temperature")

    assert result.side == "SKIP"
    assert per_side["YES"].side == "SKIP"
    assert "YES liquidity filter: spread too wide 0.40 > 0.20" in per_side["YES"].reason


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


def test_indicative_best_bid_only_does_not_mark_or_close_position(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        probability_stop_drop_threshold=0.10,
    )
    broker = PaperBroker(settings)
    broker.state.positions = [
        PaperPosition(
            position_id="p1",
            market_id="m1",
            question="Will NYC reach 90 F on May 25?",
            token_id="yes",
            side="YES",
            entry_price=0.50,
            shares=10.0,
            cost_usd=5.0,
            opened_at=datetime.now(timezone.utc).isoformat(),
            last_mark_price=0.50,
            last_unrealized_pnl=-5.0,
            metadata={"entry_p_true": 0.80, "probability_stop_threshold": 0.70},
        )
    ]
    client = FakePolymarketClient(
        books={
            "yes": OrderBook(
                "yes",
                bids=[],
                asks=[],
                indicative_best_bid=0.90,
                indicative_best_ask=0.91,
            )
        }
    )
    latest_edges = {("m1", "YES"): EdgeResult("YES", 0.20, 0.50, -0.50, 0.0, 0.0, "probability stop")}

    messages = maybe_close_positions(broker, client, {"m1": temp_market()}, latest_edges)

    assert len(broker.state.positions) == 1
    position = broker.state.positions[0]
    assert position.last_mark_price == 0.50
    assert position.last_unrealized_pnl == -5.0
    assert any("HOLD_NO_LIQUIDITY" in message for message in messages)
    rows = list(csv.DictReader((tmp_path / "trades.csv").open(encoding="utf-8")))
    assert rows[0]["action"] == "HOLD_NO_LIQUIDITY"
    assert float(rows[0]["price"]) == 0.50


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


def test_profit_exit_recovers_principal_and_keeps_settlement_runner(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        min_profit_pct=0.03,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        settlement_runner_max_fraction=0.25,
    )
    broker = PaperBroker(settings)
    pos = PaperPosition(
        position_id="p1",
        market_id="m1",
        question="Will NYC reach 90 F on May 25?",
        token_id="yes",
        side="YES",
        entry_price=0.20,
        shares=100.0,
        cost_usd=20.0,
        opened_at=datetime.now(timezone.utc).isoformat(),
        metadata={"entry_p_true": 0.95, "probability_stop_threshold": 0.85},
    )
    broker.state.positions = [pos]
    broker.state.cash_usd = 980.0
    client = FakePolymarketClient(books={"yes": book("yes", bid=0.80, ask=0.82, bid_size=200.0)})
    latest_edges = {("m1", "YES"): EdgeResult("YES", 0.95, 0.80, 0.10, 0.0, 0.0, "latest")}

    messages = maybe_close_positions(broker, client, {"m1": temp_market()}, latest_edges)

    assert any("PARTIAL_CLOSE YES" in msg for msg in messages)
    assert len(broker.state.positions) == 1
    runner = broker.state.positions[0]
    assert round(runner.shares, 6) == 25.0
    assert round(runner.cost_usd, 6) == 5.0
    rows = list(csv.DictReader((tmp_path / "trades.csv").open(encoding="utf-8")))
    assert [row["action"] for row in rows] == ["PARTIAL_CLOSE", "HOLD_RUNNER"]
    assert "tranche=principal_recovery" in rows[0]["reason"]
    assert "tranche=settlement_runner" in rows[1]["reason"]


def test_probability_deterioration_still_full_closes_without_runner(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        settlement_runner_max_fraction=0.25,
    )
    broker = PaperBroker(settings)
    broker.state.positions = [
        PaperPosition(
            position_id="p1",
            market_id="m1",
            question="Will NYC reach 90 F on May 25?",
            token_id="yes",
            side="YES",
            entry_price=0.20,
            shares=100.0,
            cost_usd=20.0,
            opened_at=datetime.now(timezone.utc).isoformat(),
            metadata={"entry_p_true": 0.95, "probability_stop_threshold": 0.85},
        )
    ]
    broker.state.cash_usd = 980.0
    client = FakePolymarketClient(books={"yes": book("yes", bid=0.80, ask=0.82, bid_size=200.0)})
    latest_edges = {("m1", "YES"): EdgeResult("YES", 0.84, 0.80, -0.01, 0.0, 0.0, "latest")}

    messages = maybe_close_positions(broker, client, {"m1": temp_market()}, latest_edges)

    assert any("probability stop" in msg for msg in messages)
    assert broker.state.positions == []
    rows = list(csv.DictReader((tmp_path / "trades.csv").open(encoding="utf-8")))
    assert [row["action"] for row in rows] == ["CLOSE"]


def test_probability_stop_low_liquidity_partial_close_does_not_mark_error(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
    )
    broker = PaperBroker(settings)
    broker.state.positions = [
        PaperPosition(
            position_id="p1",
            market_id="m1",
            question="Will NYC reach 90 F on May 25?",
            token_id="yes",
            side="YES",
            entry_price=0.50,
            shares=100.0,
            cost_usd=50.0,
            opened_at=datetime.now(timezone.utc).isoformat(),
            metadata={"entry_p_true": 0.70, "probability_stop_threshold": 0.60},
        )
    ]
    broker.state.cash_usd = 950.0
    client = FakePolymarketClient(books={"yes": book("yes", bid=0.50, ask=0.52, bid_size=20.0)})
    latest_edges = {("m1", "YES"): EdgeResult("YES", 0.59, 0.50, -0.01, 0.0, 0.0, "latest")}

    messages = maybe_close_positions(broker, client, {"m1": temp_market()}, latest_edges)

    assert not any("MARK ERROR" in msg for msg in messages)
    assert any("PARTIAL_CLOSE YES" in msg for msg in messages)
    assert len(broker.state.positions) == 1
    assert round(broker.state.positions[0].shares, 6) == 80.0
    rows = list(csv.DictReader((tmp_path / "trades.csv").open(encoding="utf-8")))
    assert [row["action"] for row in rows] == ["PARTIAL_CLOSE"]


def test_settlement_risk_blocks_runner_and_closes_full_position(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        min_profit_pct=0.03,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        settlement_runner_max_fraction=0.25,
    )
    broker = PaperBroker(settings)
    broker.state.positions = [
        PaperPosition(
            position_id="p1",
            market_id="m1",
            question="Will NYC reach 90 F on May 25?",
            token_id="yes",
            side="YES",
            entry_price=0.20,
            shares=100.0,
            cost_usd=20.0,
            opened_at=datetime.now(timezone.utc).isoformat(),
            metadata={"entry_p_true": 0.70, "probability_stop_threshold": 0.60},
        )
    ]
    broker.state.cash_usd = 980.0
    client = FakePolymarketClient(books={"yes": book("yes", bid=0.80, ask=0.82, bid_size=200.0)})
    latest_edges = {("m1", "YES"): EdgeResult("YES", 0.65, 0.80, -0.05, 0.0, 0.0, "latest")}

    messages = maybe_close_positions(broker, client, {"m1": temp_market()}, latest_edges)

    assert any("settlement runner blocked" in msg for msg in messages)
    assert broker.state.positions == []
    rows = list(csv.DictReader((tmp_path / "trades.csv").open(encoding="utf-8")))
    assert [row["action"] for row in rows] == ["CLOSE"]
    assert "settlement runner blocked" in rows[0]["reason"]


def test_active_runner_closes_when_settlement_value_turns_unfavorable(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        min_profit_pct=0.03,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        settlement_runner_max_fraction=0.25,
    )
    broker = PaperBroker(settings)
    broker.state.positions = [
        PaperPosition(
            position_id="p1",
            market_id="m1",
            question="Will NYC reach 90 F on May 25?",
            token_id="yes",
            side="YES",
            entry_price=0.20,
            shares=25.0,
            cost_usd=5.0,
            opened_at=datetime.now(timezone.utc).isoformat(),
            metadata={
                "entry_p_true": 0.70,
                "probability_stop_threshold": 0.60,
                "settlement_runner_active": True,
            },
        )
    ]
    broker.state.cash_usd = 995.0
    client = FakePolymarketClient(books={"yes": book("yes", bid=0.80, ask=0.82, bid_size=200.0)})
    latest_edges = {("m1", "YES"): EdgeResult("YES", 0.65, 0.80, -0.05, 0.0, 0.0, "latest")}

    messages = maybe_close_positions(broker, client, {"m1": temp_market()}, latest_edges)

    assert any("settlement runner blocked" in msg for msg in messages)
    assert broker.state.positions == []
    rows = list(csv.DictReader((tmp_path / "trades.csv").open(encoding="utf-8")))
    assert [row["action"] for row in rows] == ["CLOSE"]


def test_active_runner_hold_log_reports_actual_held_shares(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        min_profit_pct=0.03,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        settlement_runner_max_fraction=0.25,
    )
    broker = PaperBroker(settings)
    broker.state.positions = [
        PaperPosition(
            position_id="p1",
            market_id="m1",
            question="Will NYC reach 90 F on May 25?",
            token_id="yes",
            side="YES",
            entry_price=0.20,
            shares=25.0,
            cost_usd=5.0,
            opened_at=datetime.now(timezone.utc).isoformat(),
            metadata={
                "entry_p_true": 0.95,
                "probability_stop_threshold": 0.85,
                "settlement_runner_active": True,
            },
        )
    ]
    broker.state.cash_usd = 995.0
    client = FakePolymarketClient(books={"yes": book("yes", bid=0.80, ask=0.82, bid_size=200.0)})
    latest_edges = {("m1", "YES"): EdgeResult("YES", 0.95, 0.80, 0.10, 0.0, 0.0, "latest")}

    messages = maybe_close_positions(broker, client, {"m1": temp_market()}, latest_edges)

    assert any("HOLD_RUNNER YES shares=25.00" in msg for msg in messages)
    assert broker.state.positions[0].shares == 25.0
    rows = list(csv.DictReader((tmp_path / "trades.csv").open(encoding="utf-8")))
    assert [row["action"] for row in rows] == ["HOLD_RUNNER"]
    assert "held_shares=25.0000" in rows[0]["reason"]


def test_stale_websocket_pauses_held_position_exit_evaluation(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        weather_taker_fee_rate=0.0,
    )
    broker = PaperBroker(settings)
    broker.state.positions = [
        PaperPosition(
            position_id="p1",
            market_id="m1",
            question="Will NYC reach 90 F on May 25?",
            token_id="yes",
            side="YES",
            entry_price=0.20,
            shares=25.0,
            cost_usd=5.0,
            opened_at=datetime.now(timezone.utc).isoformat(),
            metadata={"entry_p_true": 0.70, "probability_stop_threshold": 0.60},
        )
    ]
    broker.state.cash_usd = 995.0
    client = FakePolymarketClient(books={"yes": book("yes", bid=0.80, ask=0.82, bid_size=200.0)})

    class StaleStream:
        def health_snapshot(self):
            return {
                "thread_alive": True,
                "stale": True,
                "status_reason": "last executable order book depth age 61s exceeds 60s",
            }

    client.stream = StaleStream()
    latest_edges = {("m1", "YES"): EdgeResult("YES", 0.30, 0.80, -0.20, 0.0, 0.0, "probability stop")}

    messages = maybe_close_positions(broker, client, {"m1": temp_market()}, latest_edges)

    assert len(broker.state.positions) == 1
    assert any("HOLD_STREAM_UNHEALTHY" in msg for msg in messages)
    rows = list(csv.DictReader((tmp_path / "trades.csv").open(encoding="utf-8")))
    assert [row["action"] for row in rows] == ["HOLD_STREAM_UNHEALTHY"]
    assert "held-position exit evaluation paused" in rows[0]["reason"]
    assert "last executable order book depth age 61s exceeds 60s" in rows[0]["reason"]


def test_low_liquidity_limits_principal_recovery_tranche(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        min_profit_pct=0.03,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        settlement_runner_max_fraction=0.25,
    )
    broker = PaperBroker(settings)
    broker.state.positions = [
        PaperPosition(
            position_id="p1",
            market_id="m1",
            question="Will NYC reach 90 F on May 25?",
            token_id="yes",
            side="YES",
            entry_price=0.20,
            shares=100.0,
            cost_usd=20.0,
            opened_at=datetime.now(timezone.utc).isoformat(),
            metadata={"entry_p_true": 0.95, "probability_stop_threshold": 0.85},
        )
    ]
    broker.state.cash_usd = 980.0
    client = FakePolymarketClient(books={"yes": book("yes", bid=0.80, ask=0.82, bid_size=20.0)})
    latest_edges = {("m1", "YES"): EdgeResult("YES", 0.95, 0.80, 0.10, 0.0, 0.0, "latest")}

    messages = maybe_close_positions(broker, client, {"m1": temp_market()}, latest_edges)

    assert any("low_liquidity" in msg for msg in messages)
    assert len(broker.state.positions) == 1
    runner = broker.state.positions[0]
    assert round(runner.shares, 6) == 80.0
    rows = list(csv.DictReader((tmp_path / "trades.csv").open(encoding="utf-8")))
    assert [row["action"] for row in rows] == ["PARTIAL_CLOSE", "HOLD_RUNNER"]
    assert "low_liquidity" in rows[0]["reason"]


def test_forever_loop_sleep_subtracts_cycle_runtime():
    started_at = datetime(2026, 5, 24, 16, 0, tzinfo=timezone.utc)

    assert _sleep_seconds_until_next_cycle(started_at, 300, now=started_at + timedelta(seconds=180)) == 120
    assert _sleep_seconds_until_next_cycle(started_at, 300, now=started_at + timedelta(seconds=301)) == 0


def test_today_date_uses_station_timezone_not_local_machine_timezone():
    parsed = parse_weather_question("Will NYC reach 90°F today?")
    now_utc = datetime(2026, 5, 25, 1, 0, tzinfo=timezone.utc)

    assert _target_date_from_hint(parsed, timezone_name="America/New_York", now=now_utc).isoformat() == "2026-05-24"


def test_weekday_date_hint_is_parsed():
    parsed = parse_weather_question("Will Chicago reach 90 F on Friday?")

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
        min_net_edge=1.0,
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

        def discover_weather_markets(self, max_pages: int, page_size: int):
            return markets

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


def test_paper_round_trip_cash_and_pnl_include_taker_fees(tmp_path):
    settings = Settings(
        bankroll_usd=100.0,
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        min_order_usd=1.0,
        weather_taker_fee_rate=0.05,
    )
    broker = PaperBroker(settings)
    pos = broker.open_position(
        temp_market(),
        "yes",
        EdgeResult("YES", 0.80, 0.50, 0.10, 10.0, 20.0, "test"),
    )

    assert pos is not None
    entry_fee = polymarket_taker_fee_usdc(pos.shares, 0.50, settings.weather_taker_fee_rate)
    assert pos.shares * 0.50 + entry_fee == pytest.approx(pos.cost_usd, abs=1e-5)
    assert broker.state.cash_usd == pytest.approx(100.0 - pos.cost_usd)

    shares = pos.shares
    cost = pos.cost_usd
    pnl = broker.close_position(pos, temp_market(), 0.60, "test")
    exit_fee = polymarket_taker_fee_usdc(shares, 0.60, settings.weather_taker_fee_rate)
    net_proceeds = shares * 0.60 - exit_fee

    assert broker.state.cash_usd == pytest.approx(100.0 - cost + net_proceeds)
    assert pnl == pytest.approx(net_proceeds - cost)


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


def test_closed_market_settles_from_binary_outcome_prices_when_winner_field_missing():
    settings = Settings()
    broker = PaperBroker(settings)
    broker.state = PaperState(
        cash_usd=900.0,
        positions=[
            PaperPosition(
                position_id="p1",
                market_id="m1",
                question="Will NYC reach 90째F on May 25?",
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
        question="Will NYC reach 90째F on May 25?",
        slug="nyc-90f-may-25",
        active=False,
        closed=True,
        yes_token_id="yes",
        no_token_id="no",
        raw={
            "outcomes": json.dumps(["Yes", "No"]),
            "outcomePrices": json.dumps(["1", "0"]),
        },
    )

    messages = maybe_settle_resolved_positions(broker, {"m1": market})

    assert messages == ["SETTLED YES pnl=$60.00 payout=1.0000 reason=resolved winner=YES"]
    assert broker.state.cash_usd == 1000.0
    assert broker.state.positions == []


def test_closed_market_does_not_settle_from_ambiguous_outcome_prices():
    settings = Settings()
    broker = PaperBroker(settings)
    position = PaperPosition(
        position_id="p1",
        market_id="m1",
        question="Will NYC reach 90째F on May 25?",
        token_id="yes",
        side="YES",
        entry_price=0.40,
        shares=100.0,
        cost_usd=40.0,
        opened_at=datetime.now(timezone.utc).isoformat(),
    )
    broker.state = PaperState(cash_usd=900.0, positions=[position])
    market = RawMarket(
        market_id="m1",
        question="Will NYC reach 90째F on May 25?",
        slug="nyc-90f-may-25",
        active=False,
        closed=True,
        yes_token_id="yes",
        no_token_id="no",
        raw={
            "outcomes": json.dumps(["Yes", "No"]),
            "outcomePrices": json.dumps(["0.52", "0.48"]),
        },
    )

    messages = maybe_settle_resolved_positions(broker, {"m1": market})

    assert messages == []
    assert broker.state.positions == [position]


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
