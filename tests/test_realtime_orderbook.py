import json
from datetime import datetime, timedelta, timezone

from weather_bot.edge import executable_buy_price, executable_sell_price
from weather_bot.models import OrderBook
from weather_bot.realtime_orderbook import OrderBookMarketStream, OrderBookStreamCache, market_subscription_message


def test_market_subscription_uses_token_ids_with_custom_features():
    message = market_subscription_message(["yes", "no"])

    assert message == {
        "type": "market",
        "assets_ids": ["yes", "no"],
        "custom_feature_enabled": True,
    }


def test_stream_cache_applies_book_snapshot_and_price_changes():
    cache = OrderBookStreamCache()

    updated = cache.apply_message(
        {
            "event_type": "book",
            "asset_id": "yes",
            "market": "condition",
            "bids": [{"price": "0.49", "size": "10"}, {"price": "0.48", "size": "30"}],
            "asks": [{"price": "0.52", "size": "20"}, {"price": "0.53", "size": "40"}],
            "timestamp": "1",
            "hash": "abc",
        }
    )

    assert updated == {"yes"}
    book = cache.get_order_book("yes")
    assert isinstance(book, OrderBook)
    assert book.best_bid == 0.49
    assert book.best_ask == 0.52

    updated = cache.apply_message(
        {
            "event_type": "price_change",
            "market": "condition",
            "price_changes": [
                {"asset_id": "yes", "side": "BUY", "price": "0.50", "size": "15"},
                {"asset_id": "yes", "side": "SELL", "price": "0.52", "size": "0"},
            ],
            "timestamp": "2",
        }
    )

    assert updated == {"yes"}
    book = cache.get_order_book("yes")
    assert book.best_bid == 0.50
    assert book.best_ask == 0.53


def test_book_snapshot_ignores_malformed_levels_and_keeps_valid_levels():
    cache = OrderBookStreamCache()

    updated = cache.apply_message(
        {
            "event_type": "book",
            "asset_id": "yes",
            "market": "condition",
            "bids": [
                {"price": "bad", "size": "10"},
                {"price": "-0.10", "size": "10"},
                {"price": "nan", "size": "10"},
                {"price": "0.49", "size": "bad"},
                {"price": "0.48", "size": "inf"},
                {"price": "0.47", "size": "-1"},
                {"price": "0.46", "size": "12"},
            ],
            "asks": [
                {"price": "0.52", "size": "20"},
                {"price": "inf", "size": "20"},
                {"price": "0.53", "size": "bad"},
            ],
            "timestamp": "1",
        }
    )

    assert updated == {"yes"}
    book = cache.get_order_book("yes")
    assert [(level.price, level.size) for level in book.bids] == [(0.46, 12.0)]
    assert [(level.price, level.size) for level in book.asks] == [(0.52, 20.0)]


def test_price_change_ignores_malformed_changes_and_keeps_valid_changes():
    cache = OrderBookStreamCache()
    cache.apply_message(
        {
            "event_type": "book",
            "asset_id": "yes",
            "market": "condition",
            "bids": [{"price": "0.49", "size": "10"}],
            "asks": [{"price": "0.52", "size": "20"}],
            "timestamp": "1",
        }
    )

    updated = cache.apply_message(
        {
            "event_type": "price_change",
            "market": "condition",
            "price_changes": [
                {"asset_id": "yes", "side": "BUY", "price": "bad", "size": "15"},
                {"asset_id": "yes", "side": "BUY", "price": "nan", "size": "15"},
                {"asset_id": "yes", "side": "BUY", "price": "-0.10", "size": "15"},
                {"asset_id": "yes", "side": "SELL", "price": "0.53", "size": "bad"},
                {"asset_id": "yes", "side": "SELL", "price": "0.53", "size": "inf"},
                {"asset_id": "yes", "side": "SELL", "price": "0.53", "size": "-1"},
                {"asset_id": "yes", "side": "BUY", "price": "0.50", "size": "15"},
                {"asset_id": "yes", "side": "SELL", "price": "0.52", "size": "0"},
                {"asset_id": "yes", "side": "SELL", "price": "0.53", "size": "25"},
            ],
            "timestamp": "2",
        }
    )

    assert updated == {"yes"}
    book = cache.get_order_book("yes")
    assert [(level.price, level.size) for level in book.bids] == [(0.50, 15.0), (0.49, 10.0)]
    assert [(level.price, level.size) for level in book.asks] == [(0.53, 25.0)]


def test_malformed_book_shape_fails_closed_without_replacing_existing_book():
    cache = OrderBookStreamCache()
    cache.apply_message(
        {
            "event_type": "book",
            "asset_id": "yes",
            "market": "condition",
            "bids": [{"price": "0.49", "size": "10"}],
            "asks": [{"price": "0.52", "size": "20"}],
            "timestamp": "1",
        }
    )

    assert cache.apply_message({"event_type": "book", "asset_id": "yes", "bids": "bad", "asks": []}) == set()

    book = cache.get_order_book("yes")
    assert [(level.price, level.size) for level in book.bids] == [(0.49, 10.0)]
    assert [(level.price, level.size) for level in book.asks] == [(0.52, 20.0)]


def test_stream_cache_accepts_json_message_lists():
    cache = OrderBookStreamCache()
    raw = json.dumps(
        [
            {
                "event_type": "best_bid_ask",
                "asset_id": "no",
                "market": "condition",
                "best_bid": "0.41",
                "best_ask": "0.44",
                "timestamp": "3",
            }
        ]
    )

    assert cache.apply_message(raw) == {"no"}
    book = cache.get_order_book("no")
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.indicative_best_bid == 0.41
    assert book.indicative_best_ask == 0.44


def test_best_bid_ask_only_does_not_create_executable_depth():
    cache = OrderBookStreamCache()

    assert cache.apply_message(
        {
            "event_type": "best_bid_ask",
            "asset_id": "yes",
            "market": "condition",
            "best_bid": "0.41",
            "best_ask": "0.44",
            "timestamp": "3",
        }
    ) == {"yes"}

    book = cache.get_order_book("yes")
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.indicative_best_bid == 0.41
    assert book.indicative_best_ask == 0.44
    assert book.bids == []
    assert book.asks == []
    assert executable_buy_price(book, 0.20) == (None, 0.0, 0.0)
    assert executable_sell_price(book, 0.5) == (None, 0.0)


def test_best_bid_ask_updates_reference_price_without_moving_snapshot_depth():
    cache = OrderBookStreamCache()
    cache.apply_message(
        {
            "event_type": "book",
            "asset_id": "yes",
            "market": "condition",
            "bids": [{"price": "0.49", "size": "10"}],
            "asks": [{"price": "0.52", "size": "20"}],
            "timestamp": "1",
        }
    )

    assert cache.apply_message(
        {
            "event_type": "best_bid_ask",
            "asset_id": "yes",
            "market": "condition",
            "best_bid": "0.50",
            "best_ask": "0.51",
            "timestamp": "2",
        }
    ) == {"yes"}

    book = cache.get_order_book("yes")
    assert book.best_bid == 0.49
    assert book.best_ask == 0.52
    assert book.indicative_best_bid == 0.50
    assert book.indicative_best_ask == 0.51
    assert [(level.price, level.size) for level in book.bids] == [(0.49, 10.0)]
    assert [(level.price, level.size) for level in book.asks] == [(0.52, 20.0)]
    assert executable_sell_price(book, 1.0) == (0.49, 0.0)


def test_stream_cache_tracks_last_trade_price_from_stream_event():
    cache = OrderBookStreamCache()

    assert cache.apply_message(
        {
            "event_type": "last_trade_price",
            "asset_id": "yes",
            "market": "condition",
            "price": "0.57",
            "timestamp": "4",
        }
    ) == {"yes"}

    book = cache.get_order_book("yes")
    assert book.last_trade_price == 0.57


def test_market_stream_health_tracks_messages_books_and_staleness(monkeypatch):
    now = [datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)]

    class AliveThread:
        def is_alive(self):
            return True

    monkeypatch.setattr("weather_bot.realtime_orderbook._utc_now", lambda: now[0])
    stream = OrderBookMarketStream(stale_seconds=60)
    stream._thread = AliveThread()
    stream.apply_message(
        {
            "event_type": "book",
            "asset_id": "yes",
            "bids": [{"price": "0.49", "size": "10"}],
            "asks": [{"price": "0.52", "size": "20"}],
        }
    )

    fresh = stream.health_snapshot()
    now[0] += timedelta(seconds=61)
    stale = stream.health_snapshot()

    assert fresh["thread_alive"] is True
    assert fresh["last_message_at"] == "2026-06-01T00:00:00+00:00"
    assert fresh["last_book_at"] == "2026-06-01T00:00:00+00:00"
    assert fresh["stale_book_age_seconds"] == 0
    assert fresh["stale"] is False
    assert stale["stale_book_age_seconds"] == 61
    assert stale["stale"] is True


def test_market_stream_health_tracks_reconnects_and_dead_thread():
    stream = OrderBookMarketStream(stale_seconds=60)

    stream._record_reconnect(RuntimeError("socket closed"))
    health = stream.health_snapshot()

    assert health["thread_alive"] is False
    assert health["reconnect_count"] == 1
    assert health["stale"] is True
    assert "socket closed" in health["last_error"]


def test_market_stream_does_not_treat_trade_only_event_as_orderbook_refresh(monkeypatch):
    now = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)

    class AliveThread:
        def is_alive(self):
            return True

    monkeypatch.setattr("weather_bot.realtime_orderbook._utc_now", lambda: now)
    stream = OrderBookMarketStream(stale_seconds=60)
    stream._thread = AliveThread()

    stream.apply_message({"event_type": "last_trade_price", "asset_id": "yes", "price": "0.57"})
    health = stream.health_snapshot()

    assert health["last_message_at"] == "2026-06-01T00:00:00+00:00"
    assert health["last_book_at"] is None


def test_market_stream_does_not_treat_best_bid_ask_as_executable_book_refresh(monkeypatch):
    now = [datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)]

    class AliveThread:
        def is_alive(self):
            return True

    monkeypatch.setattr("weather_bot.realtime_orderbook._utc_now", lambda: now[0])
    stream = OrderBookMarketStream(stale_seconds=60)
    stream._thread = AliveThread()
    stream._started_at = now[0]

    stream.apply_message(
        {
            "event_type": "best_bid_ask",
            "asset_id": "yes",
            "best_bid": "0.41",
            "best_ask": "0.44",
        }
    )
    health = stream.health_snapshot()

    assert health["last_message_at"] == "2026-06-01T00:00:00+00:00"
    assert health["last_book_at"] is None
    assert health["stale"] is False
    assert "waiting for executable order book depth" in health["status_reason"]

    now[0] += timedelta(seconds=61)
    stale = stream.health_snapshot()

    assert stale["stale"] is True
    assert "no executable order book depth received" in stale["status_reason"]


def test_market_stream_tracks_executable_book_freshness_by_token(monkeypatch):
    now = [datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)]

    class AliveThread:
        def is_alive(self):
            return True

    monkeypatch.setattr("weather_bot.realtime_orderbook._utc_now", lambda: now[0])
    stream = OrderBookMarketStream(stale_seconds=60)
    stream._thread = AliveThread()
    stream._started_at = now[0]

    stream.apply_message(
        {
            "event_type": "book",
            "asset_id": "stale-token",
            "bids": [{"price": "0.49", "size": "10"}],
            "asks": [{"price": "0.52", "size": "20"}],
        }
    )
    now[0] += timedelta(seconds=30)
    stream.apply_message(
        {
            "event_type": "best_bid_ask",
            "asset_id": "stale-token",
            "best_bid": "0.50",
            "best_ask": "0.53",
        }
    )
    now[0] += timedelta(seconds=31)
    stream.apply_message(
        {
            "event_type": "book",
            "asset_id": "fresh-token",
            "bids": [{"price": "0.61", "size": "10"}],
            "asks": [{"price": "0.63", "size": "20"}],
        }
    )

    stream_health = stream.health_snapshot()
    stale_token_health = stream.token_health_snapshot("stale-token")
    fresh_token_health = stream.token_health_snapshot("fresh-token")

    assert stream_health["stale"] is False
    assert stream_health["last_book_at"] == "2026-06-01T00:01:01+00:00"
    assert stream_health["last_book_at_by_token"] == {
        "fresh-token": "2026-06-01T00:01:01+00:00",
        "stale-token": "2026-06-01T00:00:00+00:00",
    }
    assert stale_token_health["last_book_at"] == "2026-06-01T00:00:00+00:00"
    assert stale_token_health["stale_book_age_seconds"] == 61
    assert stale_token_health["stale"] is True
    assert "token stale-token executable order book depth age 61s exceeds 60s" in stale_token_health["status_reason"]
    assert fresh_token_health["last_book_at"] == "2026-06-01T00:01:01+00:00"
    assert fresh_token_health["stale"] is False
