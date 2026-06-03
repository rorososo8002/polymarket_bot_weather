import pytest

from weather_bot import live_paper_runner as runner_module
from weather_bot.config import Settings
from weather_bot.live_paper_runner import StreamBackedPolymarketClient, _stream_market_registry, run_forever
from weather_bot.models import OrderBook, OrderLevel, PaperPosition, PaperState, RawMarket


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


def test_stream_registry_includes_open_positions_missing_from_discovery():
    discovered = RawMarket(
        market_id="current",
        question="Will the highest temperature in Seoul be 27°C or higher on May 29?",
        slug="current",
        active=True,
        closed=False,
        yes_token_id="current-yes",
        no_token_id="current-no",
    )
    held = RawMarket(
        market_id="held",
        question="Will the highest temperature in Seoul be 25°C or higher on May 28?",
        slug="held",
        active=True,
        closed=False,
        yes_token_id="held-yes",
        no_token_id="held-no",
    )

    class FakeClient:
        def get_market(self, market_id):
            assert market_id == "held"
            return held

    class FakeBroker:
        state = PaperState(
            cash_usd=950.0,
            positions=[
                PaperPosition(
                    position_id="p1",
                    market_id="held",
                    question=held.question,
                    token_id="held-no",
                    side="NO",
                    entry_price=0.60,
                    shares=10.0,
                    cost_usd=6.0,
                    opened_at="2026-05-27T16:21:30+00:00",
                )
            ],
        )

    registry = _stream_market_registry(FakeClient(), FakeBroker(), [discovered])

    assert set(registry) == {"current", "held"}
    assert registry["held"].no_token_id == "held-no"


def test_stream_registry_reconstructs_open_position_when_market_hydration_fails():
    held_question = "Will the highest temperature in Seoul be 25°C or higher on May 28?"

    class FakeClient:
        def get_market(self, market_id):
            assert market_id == "held"
            raise RuntimeError("gamma unavailable")

    class FakeBroker:
        state = PaperState(
            cash_usd=950.0,
            positions=[
                PaperPosition(
                    position_id="p1",
                    market_id="held",
                    question=held_question,
                    token_id="held-no",
                    side="NO",
                    entry_price=0.60,
                    shares=10.0,
                    cost_usd=6.0,
                    opened_at="2026-05-27T16:21:30+00:00",
                )
            ],
        )

    registry = _stream_market_registry(FakeClient(), FakeBroker(), [])

    assert set(registry) == {"held"}
    assert registry["held"].yes_token_id is None
    assert registry["held"].no_token_id == "held-no"


def test_stream_status_phase_surfaces_dead_and_stale_websocket():
    assert hasattr(runner_module, "_stream_status_phase")

    dead_phase, dead_message = runner_module._stream_status_phase(
        {"thread_alive": False, "stale": True},
        token_count=82,
        market_count=41,
        event_count=7,
        city_count=4,
    )
    stale_phase, stale_message = runner_module._stream_status_phase(
        {"thread_alive": True, "stale": True},
        token_count=82,
        market_count=41,
        event_count=7,
        city_count=4,
    )

    assert dead_phase == "stream_error"
    assert "stopped" in dead_message
    assert stale_phase == "stream_stale"
    assert "stale" in stale_message


def test_stream_status_phase_includes_operator_recovery_context():
    phase, message = runner_module._stream_status_phase(
        {
            "thread_alive": True,
            "stale": True,
            "status_reason": "last executable order book depth age 61s exceeds 60s",
            "reconnect_count": 2,
        },
        token_count=82,
        market_count=41,
        event_count=7,
        city_count=4,
    )

    assert phase == "stream_stale"
    assert "last executable order book depth age 61s exceeds 60s" in message
    assert "new entries blocked" in message
    assert "held-position exit evaluation paused" in message
    assert "reconnects=2" in message


def test_stream_rebuild_is_only_for_dead_websocket_threads():
    assert hasattr(runner_module, "_stream_should_rebuild")

    assert runner_module._stream_should_rebuild({"thread_alive": False}, token_count=2) is True
    assert runner_module._stream_should_rebuild({"thread_alive": True, "stale": True}, token_count=2) is False
    assert runner_module._stream_should_rebuild({"thread_alive": False}, token_count=0) is False


def test_runner_groups_binary_submarkets_by_weather_event_and_reports_coverage():
    markets = [
        RawMarket(
            "seoul-lower",
            "Will the highest temperature in Seoul be 18°C or below on May 25?",
            "seoul-lower",
            True,
            False,
            "seoul-lower-yes",
            "seoul-lower-no",
            event_id="seoul-may-25",
        ),
        RawMarket(
            "seoul-exact",
            "Will the highest temperature in Seoul be 19°C on May 25?",
            "seoul-exact",
            True,
            False,
            "seoul-exact-yes",
            "seoul-exact-no",
            event_id="seoul-may-25",
        ),
        RawMarket(
            "london-exact",
            "Will the highest temperature in London be 24°C on May 25?",
            "london-exact",
            True,
            False,
            "london-exact-yes",
            "london-exact-no",
            event_id="london-may-25",
        ),
    ]

    grouped = runner_module._group_weather_markets_by_event(markets)
    coverage = runner_module._discovery_coverage(markets)

    assert [len(group) for group in grouped] == [2, 1]
    assert coverage == {"events": 2, "cities": 2, "markets": 3}

    phase, message = runner_module._stream_status_phase(
        {"thread_alive": True, "stale": False},
        token_count=6,
        market_count=3,
        event_count=2,
        city_count=2,
    )

    assert phase == "streaming"
    assert message == "websocket streaming 6 tokens across 3 markets, 2 events, 2 cities"
