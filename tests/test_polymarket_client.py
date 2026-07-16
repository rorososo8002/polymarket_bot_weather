from __future__ import annotations

import json
from typing import Any

from weather_bot.polymarket_client import PolymarketClient


def _binary_token_fields() -> dict[str, str]:
    return {
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps(["yes-token", "no-token"]),
    }


def test_get_order_books_uses_one_batch_request(monkeypatch) -> None:
    calls: list[tuple[str, list[dict[str, str]], float]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, Any]]:
            return [
                {
                    "market": "condition-a",
                    "asset_id": "token-a",
                    "timestamp": "123",
                    "hash": "hash-a",
                    "bids": [{"price": "0.79", "size": "20"}],
                    "asks": [{"price": "0.80", "size": "30"}],
                    "min_order_size": "1",
                    "tick_size": "0.01",
                    "neg_risk": False,
                    "last_trade_price": "0.79",
                },
                {
                    "market": "condition-b",
                    "asset_id": "token-b",
                    "timestamp": "124",
                    "hash": "hash-b",
                    "bids": [],
                    "asks": [{"price": "0.90", "size": "10"}],
                    "min_order_size": "1",
                    "tick_size": "0.01",
                    "neg_risk": False,
                    "last_trade_price": "0.89",
                },
            ]

    def fake_post(url: str, *, json: list[dict[str, str]], timeout: float) -> Response:
        calls.append((url, json, timeout))
        return Response()

    monkeypatch.setattr("weather_bot.polymarket_client.requests.post", fake_post)
    client = PolymarketClient("https://gamma.example", "https://clob.example")

    books = client.get_order_books(["token-a", "token-b"], timeout=1.5)

    assert calls == [
        (
            "https://clob.example/books",
            [{"token_id": "token-a"}, {"token_id": "token-b"}],
            1.5,
        )
    ]
    assert [book.token_id for book in books] == ["token-a", "token-b"]
    assert books[0].best_ask == 0.80


def _weather_market_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "market-1",
        "question": "Will NYC reach 90 F on May 25?",
        **_binary_token_fields(),
    }
    row.update(overrides)
    return row


class _DiscoveryClient(PolymarketClient):
    def __init__(self, row: dict[str, Any]) -> None:
        super().__init__("https://gamma.example", "https://clob.example")
        self.row = row

    def _get_web_text(self, path: str) -> str:
        return ""

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        if url.endswith("/events") and int((params or {}).get("offset", 0)) == 0:
            return [{"id": "event-1", "markets": [self.row]}]
        return []


class _ClobInfoClient(PolymarketClient):
    def __init__(self) -> None:
        super().__init__("https://gamma.example", "https://clob.example")
        self.calls: list[str] = []

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append(url)
        return {
            "conditionId": "condition-1",
            "active": "true",
            "closed": "false",
            "archived": 0,
            "acceptingOrders": "true",
            "enable_order_book": "true",
            "ready": 1,
            "funded": "false",
            "end_date_iso": "2026-06-20T18:00:00Z",
        }


def test_parse_market_preserves_tradability_fields_camel_case() -> None:
    client = PolymarketClient("https://gamma.example", "https://clob.example")

    market = client._parse_market(
        _weather_market_row(
            acceptingOrders="true",
            enableOrderBook="false",
            acceptingOrderTimestamp="2026-06-20T01:02:03Z",
            endDateIso="2026-06-20T23:59:59Z",
            archived="false",
            ready="true",
            funded="1",
        )
    )

    assert market.accepting_orders is True
    assert market.enable_order_book is False
    assert market.accepting_order_timestamp == "2026-06-20T01:02:03Z"
    assert market.end_date_iso == "2026-06-20T23:59:59Z"
    assert market.archived is False
    assert market.ready is True
    assert market.funded is True
    assert market.tradability_source == "gamma"


def test_parse_market_preserves_tradability_fields_snake_case() -> None:
    client = PolymarketClient("https://gamma.example", "https://clob.example")

    market = client._parse_market(
        _weather_market_row(
            accepting_orders=False,
            enable_order_book=True,
            accepting_order_timestamp="2026-06-20T02:03:04Z",
            end_date="2026-06-21T00:00:00Z",
            archived=True,
            ready=False,
            funded=True,
        )
    )

    assert market.accepting_orders is False
    assert market.enable_order_book is True
    assert market.accepting_order_timestamp == "2026-06-20T02:03:04Z"
    assert market.end_date_iso == "2026-06-21T00:00:00Z"
    assert market.archived is True
    assert market.ready is False
    assert market.funded is True


def test_discovery_does_not_treat_end_date_as_closed() -> None:
    client = _DiscoveryClient(
        _weather_market_row(
            active=True,
            closed=False,
            endDate="2020-01-01T00:00:00Z",
            acceptingOrders=True,
            enableOrderBook=True,
        )
    )

    markets = client.discover_weather_markets(max_pages=1, page_size=10)

    assert [market.market_id for market in markets] == ["market-1"]
    assert markets[0].end_date_iso == "2020-01-01T00:00:00Z"


def test_discovery_excludes_accepting_orders_false_when_explicit() -> None:
    client = _DiscoveryClient(
        _weather_market_row(active=True, closed=False, acceptingOrders=False)
    )

    markets = client.discover_weather_markets(max_pages=1, page_size=10)

    assert markets == []


def test_discovery_excludes_accepting_orders_unknown() -> None:
    client = _DiscoveryClient(_weather_market_row(active=True, closed=False))

    markets = client.discover_weather_markets(max_pages=1, page_size=10)

    assert markets == []


def test_discovery_excludes_orderbook_unknown_or_disabled() -> None:
    for enable_order_book in (None, False):
        row = _weather_market_row(active=True, closed=False, acceptingOrders=True)
        if enable_order_book is not None:
            row["enableOrderBook"] = enable_order_book
        client = _DiscoveryClient(row)

        markets = client.discover_weather_markets(max_pages=1, page_size=10)

        assert markets == []


def test_get_clob_market_tradability_parses_response_and_uses_cache() -> None:
    client = _ClobInfoClient()

    first = client.get_clob_market_tradability("condition-1")
    second = client.get_clob_market_tradability("condition-1")

    assert first.__class__.__name__ == "MarketTradability"
    assert first.active is True
    assert first.closed is False
    assert first.archived is False
    assert first.accepting_orders is True
    assert first.enable_order_book is True
    assert first.ready is True
    assert first.funded is False
    assert first.condition_id == "condition-1"
    assert first.end_date_iso == "2026-06-20T18:00:00Z"
    assert first.source == "clob"
    assert second is first
    assert client.calls == ["https://clob.example/clob-markets/condition-1"]
