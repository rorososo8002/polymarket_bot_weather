import pytest

from weather_bot.config import Settings
from weather_bot.live_paper_runner import StreamBackedPolymarketClient, run_forever
from weather_bot.models import OrderBook, OrderLevel


class FakeStream:
    def __init__(self) -> None:
        self.book = OrderBook("yes", bids=[OrderLevel(0.49, 100)], asks=[OrderLevel(0.50, 100)])

    def get_order_book(self, token_id: str) -> OrderBook:
        assert token_id == "yes"
        return self.book


def test_stream_backed_client_reads_order_books_from_websocket_cache():
    client = StreamBackedPolymarketClient("https://gamma.example", "https://clob.example", FakeStream())

    book = client.get_order_book("yes")

    assert book.best_bid == 0.49
    assert book.best_ask == 0.50


def test_run_forever_uses_websocket_mode_by_default(monkeypatch):
    calls: list[str] = []

    def fake_realtime(settings):
        calls.append("realtime")

    monkeypatch.setattr("weather_bot.live_paper_runner.run_realtime_forever", fake_realtime)

    run_forever(Settings(orderbook_stream_enabled=True))

    assert calls == ["realtime"]


def test_run_forever_rejects_disabling_realtime_orderbook_stream():
    with pytest.raises(RuntimeError, match="real-time order-book stream"):
        run_forever(Settings(orderbook_stream_enabled=False))
