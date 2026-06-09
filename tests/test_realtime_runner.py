import csv
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from weather_bot import live_paper_runner as runner_module
from weather_bot.config import Settings
from weather_bot.live_paper_runner import (
    ForecastSignalScheduler,
    RealtimeEvaluationCoalescer,
    StreamBackedPolymarketClient,
    _stream_market_registry,
    run_forever,
)
from weather_bot.models import OrderBook, OrderLevel, PaperPosition, PaperState, RawMarket, WeatherSignal
from weather_bot.nowcast import StationNowcastObservation
from weather_bot.probability import OpenMeteoEnsembleClient, _today_for_timezone
from weather_bot.weather_client import parse_weather_question


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


def test_realtime_evaluation_coalescer_does_not_evaluate_on_enqueue():
    evaluated = threading.Event()
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={"yes": "seoul|2026-05-25|temperature|max"},
        evaluator=lambda _tokens: evaluated.set(),
        coalesce_seconds=0.25,
    )

    worker.start()
    try:
        accepted = worker.enqueue_tokens({"yes"})

        assert accepted == 1
        assert evaluated.wait(0.03) is False
        status = worker.status_snapshot()
        assert status["queue_depth"] == 1
        assert status["dropped_update_count"] == 0
    finally:
        worker.stop(drain=False)


def test_realtime_evaluation_coalescer_merges_burst_updates_by_event():
    calls: list[set[str]] = []
    evaluated = threading.Event()

    def evaluator(tokens: set[str]) -> None:
        calls.append(set(tokens))
        evaluated.set()

    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={
            "seoul-26-yes": "seoul|2026-05-25|temperature|max",
            "seoul-26-no": "seoul|2026-05-25|temperature|max",
        },
        evaluator=evaluator,
        coalesce_seconds=0.01,
    )

    worker.start()
    try:
        worker.enqueue_tokens({"seoul-26-yes"})
        worker.enqueue_tokens({"seoul-26-no"})

        assert evaluated.wait(1.0) is True
        assert calls == [{"seoul-26-yes", "seoul-26-no"}]
        status = worker.status_snapshot()
        assert status["processed_batch_count"] == 1
        assert status["coalesced_update_count"] == 1
    finally:
        worker.stop()


def test_realtime_evaluation_coalescer_records_worker_errors_and_keeps_running():
    calls = 0
    recovered = threading.Event()
    status_updates: list[dict[str, object]] = []

    def evaluator(_tokens: set[str]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("slow strategy evaluator failed")
        recovered.set()

    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={"yes": "seoul|2026-05-25|temperature|max"},
        evaluator=evaluator,
        status_update=status_updates.append,
        coalesce_seconds=0.01,
    )

    worker.start()
    try:
        worker.enqueue_tokens({"yes"})
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and worker.status_snapshot()["error_count"] == 0:
            time.sleep(0.01)
        worker.enqueue_tokens({"yes"})

        assert recovered.wait(1.0) is True
        status = worker.status_snapshot()
        assert status["thread_alive"] is True
        assert status["error_count"] == 1
        assert "slow strategy evaluator failed" in status["last_error"]
        assert status_updates
        assert "slow strategy evaluator failed" in str(status_updates[-1]["last_error"])
    finally:
        worker.stop()


def test_realtime_evaluation_coalescer_bounds_pending_events_and_counts_drops():
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={
            "seoul-yes": "seoul|2026-05-25|temperature|max",
            "london-yes": "london|2026-05-25|temperature|max",
        },
        evaluator=lambda _tokens: None,
        max_pending_events=1,
        coalesce_seconds=0.01,
    )

    assert worker.enqueue_tokens({"seoul-yes"}) == 1
    assert worker.enqueue_tokens({"london-yes"}) == 0

    status = worker.status_snapshot()
    assert status["queue_depth"] == 1
    assert status["dropped_update_count"] == 1


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


def test_realtime_forever_records_discovery_error_before_backoff(tmp_path, monkeypatch):
    class FailingClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def discover_weather_markets(self, *, max_pages, page_size):
            raise RuntimeError("gamma outage")

    sleep_calls: list[float] = []

    def stop_after_error_status(seconds):
        sleep_calls.append(seconds)
        raise RuntimeError("stop after error backoff")

    monkeypatch.setattr(runner_module, "PolymarketClient", FailingClient)
    monkeypatch.setattr(runner_module.time, "sleep", stop_after_error_status)

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
    )

    with pytest.raises(RuntimeError, match="stop after error backoff"):
        runner_module.run_realtime_forever(settings)

    status = json.loads((tmp_path / "paper_runner_status.json").read_text(encoding="utf-8"))
    assert status["phase"] == "error"
    assert status["failed_phase"] == "market_discovery"
    assert "gamma outage" in status["message"]
    assert sleep_calls


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


def test_realtime_forever_settles_resolved_open_positions_before_streaming(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    raw_path = tmp_path / "raw.jsonl"
    portfolio_path = tmp_path / "portfolio.jsonl"
    state_path.write_text(
        json.dumps(
            {
                "cash_usd": 960.0,
                "realized_pnl_usd": 0.0,
                "positions": [
                    {
                        "position_id": "p1",
                        "market_id": "held",
                        "question": "Will the highest temperature in Seoul be 25째C or higher on May 28?",
                        "token_id": "held-no",
                        "side": "NO",
                        "entry_price": 0.40,
                        "shares": 100.0,
                        "cost_usd": 40.0,
                        "opened_at": "2026-05-27T16:21:30+00:00",
                        "last_mark_price": 0.40,
                        "last_unrealized_pnl": 0.0,
                        "metadata": {"slug": "held"},
                    }
                ],
                "stats": {},
            }
        ),
        encoding="utf-8",
    )
    trades_path.write_text(
        "\n".join(
            [
                "ts,action,market_id,slug,question,market_type,side,token_id,shares,price,cash_delta_or_pnl,reason",
                "2026-05-27T16:21:30+00:00,OPEN,held,held,Will the highest temperature in Seoul be 25C or higher on May 28?,temperature,NO,held-no,100.000000,0.400000,-40.000000,fixture held position",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    closed_market = RawMarket(
        market_id="held",
        question="Will the highest temperature in Seoul be 25째C or higher on May 28?",
        slug="held",
        active=False,
        closed=True,
        yes_token_id="held-yes",
        no_token_id="held-no",
        raw={
            "outcomes": json.dumps(["Yes", "No"]),
            "outcomePrices": json.dumps(["1", "0"]),
        },
    )

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def discover_weather_markets(self, *, max_pages, page_size):
            return []

        def get_market(self, market_id):
            assert market_id == "held"
            return closed_market

    class StopStream:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self, token_ids):
            assert list(token_ids) == []
            raise RuntimeError("stop after settlement")

        def stop(self):
            return None

    monkeypatch.setattr(runner_module, "PolymarketClient", FakeClient)
    monkeypatch.setattr(runner_module, "OrderBookMarketStream", StopStream)

    def stop_after_error_backoff(_seconds):
        raise RuntimeError("stop after error backoff")

    monkeypatch.setattr(runner_module.time, "sleep", stop_after_error_backoff)

    settings = Settings(
        state_path=str(state_path),
        trades_csv_path=str(trades_path),
        decisions_csv_path=str(decisions_path),
        raw_snapshots_path=str(raw_path),
        portfolio_decisions_jsonl_path=str(portfolio_path),
        bankroll_usd=1000.0,
    )

    with pytest.raises(RuntimeError, match="stop after error backoff"):
        runner_module.run_realtime_forever(settings)

    status = json.loads((tmp_path / "paper_runner_status.json").read_text(encoding="utf-8"))
    assert status["phase"] == "error"
    assert status["failed_phase"] == "websocket_start"
    assert "stop after settlement" in status["message"]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["positions"] == []
    assert state["cash_usd"] == 960.0
    with trades_path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[-1]["action"] == "CLOSE"
    assert rows[-1]["market_id"] == "held"
    assert float(rows[-1]["cash_delta_or_pnl"]) == -40.0
    assert "resolved winner=YES" in rows[-1]["reason"]


def test_realtime_forever_filters_non_temperature_before_probability_estimator(tmp_path, monkeypatch):
    rain_question = "Will it rain in Chicago on Friday?"
    temperature_question = "Will NYC reach 90 F on May 25?"
    markets = [
        RawMarket("rain", rain_question, "rain", True, False, "rain-yes", "rain-no"),
        RawMarket("temperature", temperature_question, "temperature", True, False, "temp-yes", "temp-no"),
    ]
    probability_calls: list[str] = []
    stream_tokens: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def discover_weather_markets(self, *, max_pages, page_size):
            return markets

        def get_market(self, market_id):
            return next(market for market in markets if market.market_id == market_id)

    class StopStream:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self, token_ids):
            stream_tokens.extend(list(token_ids))
            raise RuntimeError("stop after stream setup")

        def stop(self):
            return None

        def health_snapshot(self):
            return {"thread_alive": True, "stale": False}

    def estimate(question, **_kwargs):
        probability_calls.append(question)
        return WeatherSignal(0.5, 0.9, "test", "test", parse_weather_question(question))

    monkeypatch.setattr(runner_module, "PolymarketClient", FakeClient)
    monkeypatch.setattr(runner_module, "OrderBookMarketStream", StopStream)
    monkeypatch.setattr(runner_module, "estimate_weather_probability", estimate)

    def stop_after_error_backoff(_seconds):
        raise RuntimeError("stop after error backoff")

    monkeypatch.setattr(runner_module.time, "sleep", stop_after_error_backoff)

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
    )

    with pytest.raises(RuntimeError, match="stop after error backoff"):
        runner_module.run_realtime_forever(settings)

    status = json.loads((tmp_path / "paper_runner_status.json").read_text(encoding="utf-8"))
    assert status["phase"] == "error"
    assert status["failed_phase"] == "websocket_start"
    assert "stop after stream setup" in status["message"]
    assert probability_calls == []
    assert set(stream_tokens) == {"temp-yes", "temp-no"}


def test_realtime_forever_starts_websocket_before_forecast_signal_warmup(tmp_path, monkeypatch):
    questions = [
        "Will the highest temperature in Seoul be 27C or higher today?",
        "Will the highest temperature in Tokyo be 31C or higher today?",
    ]
    markets = [
        RawMarket("seoul", questions[0], "seoul", True, False, "seoul-yes", "seoul-no"),
        RawMarket("tokyo", questions[1], "tokyo", True, False, "tokyo-yes", "tokyo-no"),
    ]
    probability_calls: list[str] = []
    forecast_calls_seen_by_stream: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def discover_weather_markets(self, *, max_pages, page_size):
            return markets

        def get_market(self, market_id):
            return next(market for market in markets if market.market_id == market_id)

    class StopStream:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self, token_ids):
            forecast_calls_seen_by_stream.extend(probability_calls)
            assert set(token_ids) == {"seoul-yes", "seoul-no", "tokyo-yes", "tokyo-no"}
            raise RuntimeError("stop after early stream start")

        def stop(self):
            return None

        def health_snapshot(self):
            return {"thread_alive": True, "stale": False}

    def estimate(question, **_kwargs):
        probability_calls.append(question)
        return WeatherSignal(0.5, 0.9, "test", "test", parse_weather_question(question))

    monkeypatch.setattr(runner_module, "PolymarketClient", FakeClient)
    monkeypatch.setattr(runner_module, "OrderBookMarketStream", StopStream)
    monkeypatch.setattr(runner_module, "estimate_weather_probability", estimate)

    def stop_after_error_backoff(_seconds):
        raise RuntimeError("stop after error backoff")

    monkeypatch.setattr(runner_module.time, "sleep", stop_after_error_backoff)

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
    )

    with pytest.raises(RuntimeError, match="stop after error backoff"):
        runner_module.run_realtime_forever(settings)

    assert forecast_calls_seen_by_stream == []
    assert probability_calls == []


def test_realtime_update_without_signal_fails_closed_without_order_book_lookup(tmp_path):
    question = "Will the highest temperature in Seoul be 27C or higher today?"
    market = RawMarket("seoul-pending", question, "seoul-pending", True, False, "yes", "no", event_id="seoul-today")

    class FakeClient:
        def get_order_book(self, token_id):
            raise AssertionError(f"forecast-pending market must not read order book for {token_id}")

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        # This test explicitly asserts on SKIP log content.
        decisions_log_skip_enabled=True,
    )
    broker = runner_module.PaperBroker(settings)

    runner_module._evaluate_realtime_update(
        {"yes"},
        FakeClient(),
        broker,
        settings,
        {"yes": market, "no": market},
        {},
        {market.market_id: "temperature"},
        {},
        signal_refreshed_at_by_market={},
    )

    with (tmp_path / "decisions.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["side"] == "SKIP"
    assert "forecast signal pending" in rows[0]["reason"]
    assert broker.state.positions == []


def test_forecast_signal_scheduler_uses_priority_then_resumes_round_robin():
    seoul = RawMarket(
        "seoul",
        "Will the highest temperature in Seoul be 27C or higher today?",
        "seoul",
        True,
        False,
        "seoul-yes",
        "seoul-no",
    )
    tokyo = RawMarket(
        "tokyo",
        "Will the highest temperature in Tokyo be 31C or higher today?",
        "tokyo",
        True,
        False,
        "tokyo-yes",
        "tokyo-no",
    )
    now = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
    scheduler = ForecastSignalScheduler([seoul, tokyo], open_market_ids={"seoul"})
    scheduler.mark_success(tokyo, now - timedelta(minutes=41))
    scheduler.mark_success(seoul, now - timedelta(minutes=31))
    scheduler.enqueue_priority(seoul, "HELD_POSITION", now=now)
    scheduler.enqueue_priority(seoul, "ACTIVE_EVALUATION_STALE_SIGNAL", now=now)

    first = scheduler.next_task(now)
    assert first is not None
    assert first.lane == "priority"
    assert first.market_ids == ["seoul"]
    assert first.priority_reason == "HELD_POSITION,ACTIVE_EVALUATION_STALE_SIGNAL"

    scheduler.mark_success(seoul, now)
    second = scheduler.next_task(now)

    assert second is not None
    assert second.lane == "round_robin"
    assert second.market_ids == ["tokyo"]


def test_realtime_forever_records_missing_websocket_dependency_in_status(tmp_path, monkeypatch):
    question = "Will the highest temperature in Seoul be 27C or higher today?"
    market = RawMarket("seoul-27c", question, "seoul-27c", True, False, "yes", "no")

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def discover_weather_markets(self, *, max_pages, page_size):
            return [market]

        def get_market(self, market_id):
            assert market_id == market.market_id
            return market

    class MissingWebsocketStream:
        def __init__(self, *_args, **_kwargs):
            self.health = {
                "thread_alive": False,
                "stale": True,
                "last_error": "websocket-client import failed: ModuleNotFoundError: No module named 'websocket'",
                "status_reason": (
                    "websocket receiver thread is not running; "
                    "last_error=websocket-client import failed: ModuleNotFoundError: No module named 'websocket'"
                ),
            }

        def start(self, token_ids):
            assert set(token_ids) == {"yes", "no"}
            raise RuntimeError("Install websocket-client to use real-time Polymarket orderbook streaming.")

        def stop(self):
            return None

        def health_snapshot(self):
            return dict(self.health)

    def estimate(question_arg, **_kwargs):
        return WeatherSignal(0.5, 0.9, "test", "test", parse_weather_question(question_arg))

    monkeypatch.setattr(runner_module, "PolymarketClient", FakeClient)
    monkeypatch.setattr(runner_module, "OrderBookMarketStream", MissingWebsocketStream)
    monkeypatch.setattr(runner_module, "estimate_weather_probability", estimate)

    def stop_after_error_backoff(_seconds):
        raise RuntimeError("stop after error backoff")

    monkeypatch.setattr(runner_module.time, "sleep", stop_after_error_backoff)

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
    )

    with pytest.raises(RuntimeError, match="stop after error backoff"):
        runner_module.run_realtime_forever(settings)

    status = json.loads((tmp_path / "paper_runner_status.json").read_text(encoding="utf-8"))
    assert status["phase"] == "error"
    assert status["failed_phase"] == "websocket_start"
    assert "Install websocket-client" in status["message"]
    assert status["websocket"]["thread_alive"] is False
    assert "websocket-client import failed" in status["websocket"]["last_error"]
    assert "No module named 'websocket'" in status["websocket"]["status_reason"]


def test_realtime_update_refreshes_nowcast_signal_after_station_cache_ttl_without_refetching_forecast(tmp_path, monkeypatch):
    target = _today_for_timezone("Asia/Seoul")
    question = "Will the highest temperature in Seoul be 27C or higher today?"
    market = RawMarket("seoul-27c", question, "seoul-27c", True, False, "yes", "no", event_id="seoul-today")
    http_calls = 0

    class FakeForecastResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "daily": {
                    "time": [target.isoformat()],
                    "temperature_2m_max": [78.0],
                    "temperature_2m_max_member01": [79.0],
                    "temperature_2m_max_member02": [80.0],
                    "temperature_2m_max_member03": [81.0],
                }
            }

    def fake_forecast_get(*_args, **_kwargs):
        nonlocal http_calls
        http_calls += 1
        return FakeForecastResponse()

    class ChangingNowcastProvider:
        def __init__(self):
            self.calls = 0

        def observed_high_so_far(self, station, *, target_date, now=None):
            self.calls += 1
            observed_high_c = 25.0 if self.calls == 1 else 27.0
            return StationNowcastObservation(
                station_id=station.station_id,
                station_name=station.station_name,
                observed_high_c=observed_high_c,
                observed_at=datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
                high_observed_at=datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
                source="aviationweather-metar",
                source_url="https://aviationweather.gov/api/data/metar",
                settlement_source_url="https://www.wunderground.com/history/daily/kr/incheon/RKSI",
                freshness_seconds=60,
                unavailable_reason="",
                raw_observation_count=4,
                update_cadence="fixture",
            )

    class FakeClient:
        def get_order_book(self, token_id):
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.45, 100)],
                asks=[OrderLevel(0.50, 100)],
            )

    monkeypatch.setattr("weather_bot.probability.requests.get", fake_forecast_get)
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        forecast_cache_path=str(tmp_path / "forecast_cache.json"),
        forecast_cache_ttl_seconds=2400,
        station_nowcast_cache_ttl_seconds=900,
        min_net_edge=0.99,
    )
    ensemble_client = OpenMeteoEnsembleClient.from_settings(settings)
    nowcast_provider = ChangingNowcastProvider()
    initial_signal = runner_module._call_probability_estimator(
        runner_module.estimate_weather_probability,
        question,
        settings=settings,
        ensemble_client=ensemble_client,
        observation_provider=nowcast_provider,
    )
    signal_refreshed_at = datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc)
    broker = runner_module.PaperBroker(settings)
    signals_by_market = {market.market_id: initial_signal}

    runner_module._evaluate_realtime_update(
        {"yes"},
        FakeClient(),
        broker,
        settings,
        {"yes": market, "no": market},
        signals_by_market,
        {market.market_id: "temperature"},
        {},
        signal_refreshed_at_by_market={market.market_id: signal_refreshed_at},
        ensemble_client=ensemble_client,
        observation_provider=nowcast_provider,
        now=signal_refreshed_at + timedelta(seconds=settings.station_nowcast_cache_ttl_seconds + 1),
    )

    assert nowcast_provider.calls == 2
    assert http_calls == 1
    assert signals_by_market[market.market_id].nowcast["observed_high_c"] == 27.0
    assert "evidence=forecast-plus-nowcast" in signals_by_market[market.market_id].note


def test_realtime_update_refreshes_held_exit_edge_when_entry_bankroll_is_unusable(tmp_path):
    question = "Will the highest temperature in Seoul be 27C or higher today?"
    market = RawMarket("seoul-held", question, "seoul-held", True, False, "held-yes", "held-no", event_id="seoul-today")

    class FakeClient:
        def get_order_book(self, token_id):
            if token_id == "held-yes":
                return OrderBook(
                    token_id,
                    bids=[OrderLevel(0.40, 1.0)],
                    asks=[OrderLevel(0.50, 100.0)],
                )
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.40, 100.0)],
                asks=[OrderLevel(0.50, 100.0)],
            )

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        min_net_edge=0.99,
        max_holding_hours=999999,
    )
    broker = runner_module.PaperBroker(settings)
    broker.state.positions = [
        PaperPosition(
            position_id="held-position",
            market_id=market.market_id,
            question=question,
            token_id="held-yes",
            side="YES",
            entry_price=0.50,
            shares=100.0,
            cost_usd=50.0,
            opened_at="2026-06-02T00:00:00+00:00",
            metadata={
                "entry_p_true": 0.80,
                "entry_side_probability": 0.80,
                "probability_stop_threshold": 0.0,
                "market_type": "temperature",
            },
        )
    ]
    signals_by_market = {
        market.market_id: WeatherSignal(0.20, 0.90, "test", "fresh signal", parse_weather_question(question))
    }
    latest_edges: dict[tuple[str, str], runner_module.EdgeResult] = {}

    runner_module._evaluate_realtime_update(
        {"held-yes"},
        FakeClient(),
        broker,
        settings,
        {"held-yes": market, "held-no": market},
        signals_by_market,
        {market.market_id: "temperature"},
        latest_edges,
    )

    held_edge = latest_edges[(market.market_id, "YES")]
    assert held_edge.p_true == 0.20
    assert "exit evidence" in held_edge.reason


def test_realtime_update_logs_market_exception_as_skip_error(tmp_path):
    question = "Will the highest temperature in Seoul be 27C or higher today?"
    market = RawMarket("seoul-error", question, "seoul-error", True, False, "yes", "no", event_id="seoul-today")

    class FakeClient:
        def get_order_book(self, token_id):
            raise AssertionError("evaluation should fail before trading on guessed books")

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        station_nowcast_cache_ttl_seconds=1,
    )
    broker = runner_module.PaperBroker(settings)
    parsed = parse_weather_question(question)
    signals_by_market = {market.market_id: WeatherSignal(0.50, 0.90, "stale", "stale signal", parsed)}

    def failing_estimator(*_args, **_kwargs):
        raise RuntimeError("nowcast refresh exploded")

    runner_module._evaluate_realtime_update(
        {"yes"},
        FakeClient(),
        broker,
        settings,
        {"yes": market, "no": market},
        signals_by_market,
        {market.market_id: "temperature"},
        {},
        signal_refreshed_at_by_market={market.market_id: datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc)},
        probability_estimator=failing_estimator,
        now=datetime(2026, 6, 2, 0, 0, 2, tzinfo=timezone.utc),
    )

    with (tmp_path / "decisions.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["side"] == "SKIP_ERROR"
    assert "nowcast refresh exploded" in rows[0]["reason"]

    status = json.loads((tmp_path / "paper_runner_status.json").read_text(encoding="utf-8"))
    assert status["market_error_count"] == 1
    assert status["last_market_error"]["market_id"] == market.market_id
    assert "nowcast refresh exploded" in status["last_market_error"]["message"]

    raw_payload = json.loads((tmp_path / "raw.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert raw_payload["event"] == "market_evaluation_error"
    assert raw_payload["payload"]["context"] == "realtime_update"


def test_open_position_if_needed_blocks_inactive_or_closed_markets():
    question = "Will NYC reach 90 F on May 25?"
    result = runner_module.EdgeResult("YES", 0.70, 0.50, 0.20, 10.0, 20.0, "entry")
    signal = WeatherSignal(0.70, 0.90, "test", "test", parse_weather_question(question))
    opened_market_ids: list[str] = []

    class FakeBroker:
        def has_position(self, market_id, side):
            return False

        def has_any_position(self, market_id):
            return False

        def open_position(self, market, *_args, **_kwargs):
            opened_market_ids.append(market.market_id)

    markets = [
        RawMarket("inactive", question, "inactive", False, False, "inactive-yes", "inactive-no"),
        RawMarket("closed", question, "closed", True, True, "closed-yes", "closed-no"),
        RawMarket("open", question, "open", True, False, "open-yes", "open-no"),
    ]

    for market in markets:
        runner_module._open_position_if_needed(FakeBroker(), market, signal, result, "temperature")

    assert opened_market_ids == ["open"]


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
