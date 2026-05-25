import json

from weather_bot.models import OrderBook
from weather_bot.realtime_orderbook import OrderBookStreamCache, market_subscription_message


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
    assert book.best_bid == 0.41
    assert book.best_ask == 0.44


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
