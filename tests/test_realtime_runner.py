import csv
import hashlib
import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from weather_bot import live_paper_runner as runner_module
from weather_bot.config import Settings
from weather_bot.live_paper_runner import (
    RealtimeEvaluationCoalescer,
    StreamBackedPolymarketClient,
    _stream_market_registry,
    run_forever,
)
from weather_bot.models import (
    MarketRuleProvenance,
    MarketTradability,
    OrderBook,
    OrderLevel,
    PaperPosition,
    PaperState,
    RawMarket,
    WeatherSignal,
)
from weather_bot.nowcast import StationNowcastObservation
from weather_bot.residual_probability import ResidualProbabilityEstimate
from weather_bot.station_signal import _today_for_timezone
from weather_bot.weather_client import parse_weather_question


class FakeStream:
    def __init__(self) -> None:
        self.book = OrderBook("yes", bids=[OrderLevel(0.49, 100)], asks=[OrderLevel(0.50, 100)])

    def get_order_book(self, token_id: str) -> OrderBook:
        assert token_id == "yes"
        return self.book


class FakeResidualProfileStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def estimate_bucket(self, **kwargs):
        self.calls.append(kwargs)
        return ResidualProbabilityEstimate(
            usable=True,
            raw_probability=0.98,
            conservative_yes_probability=0.97,
            conservative_no_probability=0.01,
            successes=98,
            sample_days=100,
            profile_key="RKSI|month:06|2200|high|C",
            profile_scope="month",
            reason_code="RESIDUAL_PROBABILITY_OK",
            reason="fixture residual estimate",
        )


def test_stream_backed_client_reads_order_books_from_websocket_cache():
    client = StreamBackedPolymarketClient("https://gamma.example", "https://clob.example", FakeStream())

    book = client.get_order_book("yes")

    assert book.best_bid == 0.49
    assert book.best_ask == 0.50


def test_fetch_books_keeps_available_side_when_other_side_snapshot_missing():
    class PartialClient:
        def get_order_book(self, token_id: str) -> OrderBook:
            if token_id == "yes-token":
                raise KeyError("no websocket orderbook snapshot for token yes-token")
            return OrderBook("no-token", bids=[OrderLevel(0.70, 10)], asks=[OrderLevel(0.72, 12)])

    market = RawMarket(
        market_id="m1",
        question="Will the highest temperature in Seoul be 29°C on June 29?",
        slug="seoul-high",
        active=True,
        closed=False,
        yes_token_id="yes-token",
        no_token_id="no-token",
    )

    books, error = runner_module._fetch_books(market, PartialClient())

    assert error is None
    assert set(books) == {"NO"}
    assert books["NO"].best_ask == 0.72


def test_probability_estimator_receives_residual_profile_store() -> None:
    profile_store = object()
    received: dict[str, object] = {}

    def estimate(question, **kwargs):
        received.update(kwargs)
        return WeatherSignal(0.5, 0.0, "test", question)

    signal = runner_module._call_probability_estimator(
        estimate,
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(),
        residual_profile_store=profile_store,
    )

    assert signal.source == "test"
    assert received["residual_profile_store"] is profile_store


def test_probability_estimator_receives_store_concentrated_sizing_eligibility() -> None:
    class EligibleStore:
        concentrated_sizing_eligible_by_station = {"RKSI": True}

    received: dict[str, object] = {}

    def estimate(question, **kwargs):
        received.update(kwargs)
        return WeatherSignal(0.5, 0.0, "test", question)

    runner_module._call_probability_estimator(
        estimate,
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(),
        residual_profile_store=EligibleStore(),
    )

    assert received["concentrated_sizing_eligible_by_station"] == {"RKSI": True}


def test_residual_profile_loader_reads_verified_manifest_eligibility(tmp_path) -> None:
    profile_path = tmp_path / "station_residual_profiles.json"
    profile_bytes = Path("tests/fixtures/residual_profiles/minimal_profiles.json").read_bytes()
    profile_path.write_bytes(profile_bytes)
    manifest_path = profile_path.with_name("station_residual_profiles.manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "profile_artifact_sha256": hashlib.sha256(profile_bytes).hexdigest(),
                "stations": {
                    "seoul": {
                        "station_id": "RKSI",
                        "concentrated_sizing_eligible": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store = runner_module._load_residual_profile_store(
        Settings(station_residual_profile_path=str(profile_path))
    )

    assert store is not None
    assert store.concentrated_sizing_eligible_by_station == {"RKSI": True}


def test_no_side_uses_explicit_conservative_no_probability_for_edge_and_tier() -> None:
    question = "Will the highest temperature in Seoul be 23C today?"
    signal = WeatherSignal(
        0.45,
        1.0,
        "official-station-residual-high-no",
        "signal_family=intraday_observation_edge",
        parse_weather_question(question),
        raw_probability=0.45,
        conservative_yes_probability=0.30,
        conservative_no_probability=0.91,
        selected_side_probability=0.91,
        signal_family="intraday_observation_edge",
    )
    settings = Settings(
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
    )

    _fee, edge, side_probability = runner_module._side_edge_metrics(
        "NO",
        signal,
        0.20,
        settings,
    )

    assert side_probability == pytest.approx(0.91)
    assert edge == pytest.approx(0.71)


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


def test_realtime_update_callback_ignores_late_stream_update_after_worker_stop():
    assert runner_module._enqueue_realtime_update(None, {"late-token"}) == 0


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


def test_realtime_evaluation_coalescer_keeps_normal_burst_in_one_batch():
    calls: list[set[str]] = []
    event_key_by_token = {f"token-{index}": f"event-{index}" for index in range(10)}
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token=event_key_by_token,
        evaluator=lambda tokens: calls.append(set(tokens)),
        coalesce_seconds=0.0,
    )

    assert worker.enqueue_tokens(set(event_key_by_token)) == 10
    worker._run_pending_batch_once()

    assert len(calls) == 1
    assert len(calls[0]) == 10
    status = worker.status_snapshot()
    assert status["queue_depth"] == 0
    assert status["processed_batch_count"] == 1
    assert status["processed_event_count"] == 10


def test_realtime_evaluation_coalescer_prioritizes_urgent_events_before_old_queue():
    calls: list[set[str]] = []
    priority = {
        "future-event": (10, "future-event"),
        "today-lock-candidate": (1, "today-lock-candidate"),
    }
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={
            "future-token": "future-event",
            "today-token": "today-lock-candidate",
        },
        evaluator=lambda tokens: calls.append(set(tokens)),
        max_batch_events=1,
        coalesce_seconds=0.0,
        event_priority=lambda event_key: priority[event_key],
    )

    assert worker.enqueue_tokens({"future-token"}) == 1
    assert worker.enqueue_tokens({"today-token"}) == 1

    worker._run_pending_batch_once()
    worker._run_pending_batch_once()

    assert calls == [{"today-token"}, {"future-token"}]


def test_realtime_event_priorities_rank_same_day_high_exact_before_future_events():
    now = datetime(2026, 7, 1, 3, 0, tzinfo=timezone.utc)
    today_high = RawMarket(
        "today-high",
        "Will the highest temperature in Taipei be 30°C on July 1?",
        "today-high",
        True,
        False,
        "today-yes",
        "today-no",
        event_id="today-high-event",
        rule_provenance=MarketRuleProvenance(
            market_id="today-high",
            question="Will the highest temperature in Taipei be 30°C on July 1?",
            event_date_local="2026-07-01",
            event_timezone="Asia/Taipei",
        ),
    )
    today_low = RawMarket(
        "today-low",
        "Will the lowest temperature in Taipei be 24°C on July 1?",
        "today-low",
        True,
        False,
        "today-low-yes",
        "today-low-no",
        event_id="today-low-event",
        rule_provenance=MarketRuleProvenance(
            market_id="today-low",
            question="Will the lowest temperature in Taipei be 24°C on July 1?",
            event_date_local="2026-07-01",
            event_timezone="Asia/Taipei",
        ),
    )
    future_high = RawMarket(
        "future-high",
        "Will the highest temperature in Taipei be 30°C on July 2?",
        "future-high",
        True,
        False,
        "future-yes",
        "future-no",
        event_id="future-high-event",
        rule_provenance=MarketRuleProvenance(
            market_id="future-high",
            question="Will the highest temperature in Taipei be 30°C on July 2?",
            event_date_local="2026-07-02",
            event_timezone="Asia/Taipei",
        ),
    )

    priorities = runner_module._realtime_event_priorities(
        [future_high, today_low, today_high],
        open_market_ids=set(),
        now=now,
    )

    assert priorities["today-high-event"] < priorities["today-low-event"]
    assert priorities["today-low-event"] < priorities["future-high-event"]


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


def test_main_dry_start_checks_local_assets_without_starting_runner(monkeypatch, capsys):
    settings = Settings(orderbook_stream_enabled=True)
    loaded: list[Settings] = []

    monkeypatch.setattr(runner_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        runner_module,
        "_load_residual_profile_store",
        lambda actual: loaded.append(actual) or FakeResidualProfileStore(),
    )
    monkeypatch.setattr(
        runner_module,
        "run_forever",
        lambda _settings: pytest.fail("dry start must not enter the long-running loop"),
    )

    runner_module.main(["--dry-start"])

    assert loaded == [settings]
    assert "DRY START OK" in capsys.readouterr().out


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


def test_stream_markets_force_include_held_position_token_when_discovery_market_lacks_it():
    discovered = RawMarket(
        market_id="held",
        question="Will the lowest temperature in Seoul be 21째C on June 29?",
        slug="held",
        active=True,
        closed=False,
        yes_token_id="discovered-yes",
        no_token_id=None,
    )
    broker = type(
        "Broker",
        (),
        {
            "state": PaperState(
                cash_usd=900.0,
                positions=[
                    PaperPosition(
                        position_id="p1",
                        market_id="held",
                        question=discovered.question,
                        token_id="held-no",
                        side="NO",
                        entry_price=0.76,
                        shares=108.0,
                        cost_usd=83.0,
                        opened_at="2026-06-28T21:37:46+00:00",
                    )
                ],
            )
        },
    )()

    stream_markets = runner_module._ensure_open_position_stream_tokens([discovered], broker)

    assert len(stream_markets) == 1
    assert stream_markets[0].yes_token_id == "discovered-yes"
    assert stream_markets[0].no_token_id == "held-no"


def test_stream_token_registry_prioritizes_held_position_tokens():
    seoul = RawMarket(
        market_id="seoul",
        question="Will the lowest temperature in Seoul be 21째C on June 29?",
        slug="seoul",
        active=True,
        closed=False,
        yes_token_id="seoul-yes",
        no_token_id="seoul-no",
    )
    tokyo = RawMarket(
        market_id="tokyo",
        question="Will the lowest temperature in Tokyo be 21째C on June 29?",
        slug="tokyo",
        active=True,
        closed=False,
        yes_token_id="tokyo-yes",
        no_token_id="tokyo-no",
    )
    broker = type(
        "Broker",
        (),
        {
            "state": PaperState(
                cash_usd=900.0,
                positions=[
                    PaperPosition(
                        position_id="p1",
                        market_id="seoul",
                        question=seoul.question,
                        token_id="seoul-no",
                        side="NO",
                        entry_price=0.76,
                        shares=108.0,
                        cost_usd=83.0,
                        opened_at="2026-06-28T21:37:46+00:00",
                    )
                ],
            )
        },
    )()

    market_by_token = runner_module._market_by_token_with_held_positions_first([tokyo, seoul], broker)

    assert list(market_by_token)[:1] == ["seoul-no"]
    assert market_by_token["seoul-no"].market_id == "seoul"
    assert market_by_token["tokyo-yes"].market_id == "tokyo"


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
    monkeypatch.setattr(runner_module, "estimate_station_probability", estimate)

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


def test_realtime_forever_starts_websocket_before_station_signal_evaluation(tmp_path, monkeypatch):
    questions = [
        "Will the highest temperature in Seoul be 27C or higher today?",
        "Will the highest temperature in Tokyo be 31C or higher today?",
    ]
    markets = [
        RawMarket("seoul", questions[0], "seoul", True, False, "seoul-yes", "seoul-no"),
        RawMarket("tokyo", questions[1], "tokyo", True, False, "tokyo-yes", "tokyo-no"),
    ]
    probability_calls: list[str] = []
    station_calls_seen_by_stream: list[str] = []

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
            station_calls_seen_by_stream.extend(probability_calls)
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
    monkeypatch.setattr(runner_module, "estimate_station_probability", estimate)

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

    assert station_calls_seen_by_stream == []
    assert probability_calls == []


def test_realtime_cycle_discards_pending_evaluations_before_stopping_stream(tmp_path, monkeypatch):
    question = "Will the highest temperature in Seoul be 27C today?"
    market = RawMarket("seoul", question, "seoul", True, False, "yes", "no", event_id="seoul-today")
    call_order: list[str] = []
    discovery_calls = 0

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def discover_weather_markets(self, *, max_pages, page_size):
            nonlocal discovery_calls
            discovery_calls += 1
            if discovery_calls > 1:
                raise RuntimeError("stop after planned cycle cleanup")
            return [market]

        def get_order_book(self, token_id):
            return OrderBook(token_id, bids=[OrderLevel(0.45, 100)], asks=[OrderLevel(0.50, 100)])

    class RecordingEvaluator:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            call_order.append("evaluator.start")

        def stop(self, *, drain=True, timeout=5.0):
            call_order.append(f"evaluator.stop(drain={drain})")

        def enqueue_tokens(self, updated_token_ids):
            return len(updated_token_ids)

        def status_snapshot(self):
            return {"thread_alive": True, "queue_depth": 1}

    class RecordingStream:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self, token_ids):
            assert set(token_ids) == {"yes", "no"}
            call_order.append("stream.start")

        def stop(self):
            call_order.append("stream.stop")

        def health_snapshot(self):
            return {"thread_alive": True, "stale": False, "status_reason": "fresh fixture"}

    monkeypatch.setattr(runner_module, "PolymarketClient", FakeClient)
    monkeypatch.setattr(runner_module, "RealtimeEvaluationCoalescer", RecordingEvaluator)
    monkeypatch.setattr(runner_module, "OrderBookMarketStream", RecordingStream)

    class PlannedCycleDateTime:
        calls = 0

        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            base = datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc)
            if cls.calls >= 3:
                return base + timedelta(seconds=2)
            return base

        @classmethod
        def fromisoformat(cls, value):
            return datetime.fromisoformat(value)

    monkeypatch.setattr(runner_module, "datetime", PlannedCycleDateTime)

    def stop_after_error_backoff(_seconds):
        raise RuntimeError("stop after error backoff")

    monkeypatch.setattr(runner_module.time, "sleep", stop_after_error_backoff)

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        stream_cycle_interval_seconds=1,
    )

    with pytest.raises(RuntimeError, match="stop after error backoff"):
        runner_module.run_realtime_forever(settings)

    assert call_order[:3] == [
        "evaluator.start",
        "stream.start",
        "evaluator.stop(drain=False)",
    ]
    assert call_order[3] == "stream.stop"


def test_realtime_update_without_signal_fails_closed_without_order_book_lookup(tmp_path):
    question = "Will the highest temperature in Seoul be 27C or higher today?"
    market = RawMarket("seoul-pending", question, "seoul-pending", True, False, "yes", "no", event_id="seoul-today")

    class FakeClient:
        def get_order_book(self, token_id):
            raise AssertionError(f"non-lock station market must not read order book for {token_id}")

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
    assert "confidence too low" in rows[0]["reason"]
    assert broker.state.positions == []


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
    monkeypatch.setattr(runner_module, "estimate_station_probability", estimate)

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


def test_realtime_update_refreshes_station_signal_after_nowcast_cache_ttl(tmp_path):
    signal_refreshed_at = datetime(2026, 6, 2, 13, 0, tzinfo=timezone.utc)
    target = _today_for_timezone("Asia/Seoul", now=signal_refreshed_at)
    question = "Will the highest temperature in Seoul be 27C today?"
    market = RawMarket("seoul-27c", question, "seoul-27c", True, False, "yes", "no", event_id="seoul-today")

    class ChangingNowcastProvider:
        def __init__(self):
            self.calls = 0

        def observed_high_so_far(self, station, *, target_date, now=None):
            assert target_date == target
            self.calls += 1
            observed_high_c = 27.4 if self.calls == 1 else 27.2
            return StationNowcastObservation(
                station_id=station.station_id,
                station_name=station.station_name,
                observed_high_c=observed_high_c,
                observed_at=now,
                high_observed_at=now,
                high_drop_observed_at=now,
                source="aviationweather-metar",
                source_url="https://aviationweather.gov/api/data/metar",
                settlement_source_url="https://www.wunderground.com/history/daily/kr/incheon/RKSI",
                freshness_seconds=60,
                unavailable_reason="",
                raw_observation_count=4,
                update_cadence="fixture",
                high_bucket_confirmations=2,
            )

    class FakeClient:
        def get_order_book(self, token_id):
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.45, 100)],
                asks=[OrderLevel(0.50, 100)],
            )

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        station_nowcast_cache_ttl_seconds=900,
        min_net_edge=0.99,
    )
    nowcast_provider = ChangingNowcastProvider()
    residual_profile_store = FakeResidualProfileStore()
    initial_signal = runner_module._call_probability_estimator(
        runner_module.estimate_station_probability,
        question,
        settings=settings,
        observation_provider=nowcast_provider,
        residual_profile_store=residual_profile_store,
        now=signal_refreshed_at,
    )
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
        observation_provider=nowcast_provider,
        residual_profile_store=residual_profile_store,
        now=signal_refreshed_at + timedelta(seconds=settings.station_nowcast_cache_ttl_seconds + 1),
    )

    assert nowcast_provider.calls == 2
    assert signals_by_market[market.market_id].nowcast["observed_high_c"] == 27.2
    assert signals_by_market[market.market_id].source == "official-station-residual-high-yes"
    assert "evidence=official-station" in signals_by_market[market.market_id].note
    assert [call["observed_extreme"] for call in residual_profile_store.calls] == [
        pytest.approx(27.4),
        pytest.approx(27.2),
    ]


def test_realtime_update_computes_held_exit_edge_with_fresh_signal(tmp_path):
    """The realtime evaluator updates latest_edges for held positions using fresh signals.

    Previously this test verified 'exit evidence' path (when entry_bankroll was unusable).
    Now that available_entry_bankroll() treats settling positions as $0 (usable=True),
    the evaluation follows the normal path and hits the liquidity filter for the held
    position's small bid depth. p_true is still updated from the fresh signal.
    """
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
        market.market_id: WeatherSignal(
            0.20,
            0.90,
            "official-station-lock-test",
            "official_nowcast_lock=test; fresh signal",
            parse_weather_question(question),
        )
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

    # p_true is updated from fresh signal (was 0.80 entry, now 0.20 fresh)
    held_edge = latest_edges[(market.market_id, "YES")]
    assert held_edge.p_true == 0.20
    # With usable bankroll (settling position treated as $0), the evaluation
    # proceeds and hits the liquidity filter for the held-yes token (only 1.0 qty bid)
    assert held_edge.side == "SKIP"
    assert "liquidity filter" in held_edge.reason or "exit bid depth" in held_edge.reason


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
        settings = Settings()

        def has_position(self, market_id, side):
            return False

        def has_any_position(self, market_id):
            return False

        def open_position(self, market, *_args, **_kwargs):
            opened_market_ids.append(market.market_id)

        def log_trade(self, *_args, **_kwargs):
            return None

    markets = [
        RawMarket(
            "inactive",
            question,
            "inactive",
            False,
            False,
            "inactive-yes",
            "inactive-no",
            condition_id="condition-1",
        ),
        RawMarket(
            "closed",
            question,
            "closed",
            True,
            True,
            "closed-yes",
            "closed-no",
            condition_id="condition-1",
        ),
        RawMarket(
            "open",
            question,
            "open",
            True,
            False,
            "open-yes",
            "open-no",
            condition_id="condition-1",
        ),
    ]

    for market in markets:
        runner_module._open_position_if_needed(
            FakeBroker(),
            market,
            signal,
            result,
            "temperature",
            client=_FinalGateClient(),
        )

    assert opened_market_ids == ["open"]


def _tradability(**overrides) -> MarketTradability:
    values = {
        "active": True,
        "closed": False,
        "archived": False,
        "accepting_orders": True,
        "enable_order_book": True,
        "ready": True,
        "funded": True,
        "condition_id": "condition-1",
        "source": "clob",
        "raw": {},
        "end_date_iso": None,
    }
    values.update(overrides)
    return MarketTradability(**values)


def _entry_gate_market(**overrides) -> RawMarket:
    values = {
        "market_id": "m1",
        "question": "Will NYC reach 90 F on May 25?",
        "slug": "open",
        "active": True,
        "closed": False,
        "yes_token_id": "yes",
        "no_token_id": "no",
        "condition_id": "condition-1",
    }
    values.update(overrides)
    return RawMarket(**values)


def _entry_gate_signal() -> WeatherSignal:
    question = "Will NYC reach 90 F on May 25?"
    return WeatherSignal(
        0.95,
        1.0,
        "official-station-lock-test",
        "official_nowcast_lock=test",
        parse_weather_question(question),
    )


def test_intraday_observation_edge_is_entry_eligible_but_not_a_settlement_lock():
    question = "Will the highest temperature in Seoul be 23C today?"
    signal = WeatherSignal(
        0.90,
        1.0,
        "official-station-intraday-high-base-yes",
        "signal_family=intraday_observation_edge",
        parse_weather_question(question),
        entry_size_fraction_override=0.10,
        entry_size_reason="intraday observation edge",
    )

    assert runner_module._is_official_nowcast_lock(signal) is False
    assert runner_module._is_official_station_entry_signal(signal) is True


def _entry_gate_settings(tmp_path) -> Settings:
    return Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        min_net_edge=0.01,
        min_order_usd=1.0,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        entry_min_expected_net_return_pct=0.01,
    )


def _selected_entry_result() -> runner_module.EdgeResult:
    return runner_module.EdgeResult("YES", 0.95, 0.50, 0.45, 10.0, 20.0, "selected")


class _FinalGateClient:
    def __init__(self, tradability=None, *, lookup_error: Exception | None = None) -> None:
        self.tradability = tradability or _tradability()
        self.lookup_error = lookup_error
        self.tradability_calls: list[str] = []
        self.book_calls: list[str] = []

    def get_clob_market_tradability(self, condition_id: str):
        self.tradability_calls.append(condition_id)
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.tradability

    def get_order_book(self, token_id: str) -> OrderBook:
        self.book_calls.append(token_id)
        return OrderBook(token_id, bids=[OrderLevel(0.49, 1000.0)], asks=[OrderLevel(0.50, 1000.0)])


class _AbnormalPriceClient(_FinalGateClient):
    def __init__(self, yes_book: OrderBook, no_book: OrderBook) -> None:
        super().__init__()
        self.books = {"yes": yes_book, "no": no_book}

    def get_order_book(self, token_id: str) -> OrderBook:
        self.book_calls.append(token_id)
        return self.books[token_id]


def _intraday_signal(p_true: float) -> WeatherSignal:
    question = "Will the highest temperature in NYC be 90F today?"
    entry_fraction = 0.25 if p_true >= 0.97 else 0.10
    return WeatherSignal(
        p_true,
        1.0,
        "official-station-intraday-high-test",
        "strategy_mode=hybrid_observation_edge; signal_family=intraday_observation_edge",
        parse_weather_question(question),
        entry_size_fraction_override=entry_fraction,
        entry_size_reason="intraday observation edge fixture",
    )


def _abnormal_market() -> RawMarket:
    return RawMarket(
        "abnormal-1",
        "Will the highest temperature in NYC be 90F today?",
        "abnormal-1",
        True,
        False,
        "yes",
        "no",
        condition_id="condition-1",
        accepting_orders=True,
        enable_order_book=True,
        archived=False,
    )


def _abnormal_settings(tmp_path, **overrides) -> Settings:
    values = {
        "state_path": str(tmp_path / "state.json"),
        "trades_csv_path": str(tmp_path / "trades.csv"),
        "decisions_csv_path": str(tmp_path / "decisions.csv"),
        "raw_snapshots_path": str(tmp_path / "raw.jsonl"),
        "portfolio_decisions_jsonl_path": str(tmp_path / "portfolio.jsonl"),
        "min_net_edge": 0.01,
        "min_order_usd": 10.0,
        "weather_taker_fee_rate": 0.0,
        "model_error_margin": 0.0,
        "resolution_error_margin": 0.0,
        "entry_min_expected_net_return_pct": 0.01,
        "max_entry_spread_abs": 0.05,
        "max_entry_spread_pct": 1.0,
        "max_city_exposure_fraction": 0.90,
        "max_event_date_exposure_fraction": 0.90,
        "large_bankroll_event_date_exposure_fraction": 0.90,
    }
    values.update(overrides)
    return Settings(**values)


def _evaluate_and_open_abnormal_candidate(
    tmp_path,
    *,
    p_true: float,
    yes_ask: float,
    yes_size: float = 1000.0,
    settings_overrides: dict | None = None,
):
    settings = _abnormal_settings(tmp_path, **(settings_overrides or {}))
    broker = runner_module.PaperBroker(settings)
    market = _abnormal_market()
    signal = _intraday_signal(p_true)
    client = _AbnormalPriceClient(
        OrderBook("yes", bids=[OrderLevel(max(0.01, yes_ask - 0.01), 1000.0)], asks=[OrderLevel(yes_ask, yes_size)]),
        OrderBook("no", bids=[OrderLevel(max(0.01, 0.99 - yes_ask), 1000.0)], asks=[OrderLevel(1.0 - yes_ask, 1000.0)]),
    )
    result, per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        settings,
        200.0,
        "temperature",
    )
    final_result = runner_module._open_position_if_needed(
        broker,
        market,
        signal,
        result,
        "temperature",
        client=client,
    )
    return final_result, per_side, broker


def test_abnormal_price_tag_requires_90_probability(tmp_path):
    result, _per_side, broker = _evaluate_and_open_abnormal_candidate(
        tmp_path,
        p_true=0.89,
        yes_ask=0.50,
    )

    assert result is not None
    assert result.side == "YES"
    assert result.price_anomaly is False
    assert result.signal_family == "intraday_observation_edge"
    assert len(broker.state.positions) == 1


def test_abnormal_price_tag_requires_min_net_edge(tmp_path):
    result, _per_side, broker = _evaluate_and_open_abnormal_candidate(
        tmp_path,
        p_true=0.97,
        yes_ask=0.78,
    )

    assert result is not None
    assert result.side == "YES"
    assert result.net_edge == pytest.approx(0.19)
    assert result.price_anomaly is False
    assert result.signal_family == "intraday_observation_edge"
    assert len(broker.state.positions) == 1


def test_abnormal_price_still_requires_executable_depth(tmp_path):
    result, per_side, broker = _evaluate_and_open_abnormal_candidate(
        tmp_path,
        p_true=0.97,
        yes_ask=0.50,
        yes_size=1.0,
    )

    assert result is not None
    assert result.side == "SKIP"
    assert per_side["YES"].price_anomaly is False
    assert "SKIP_NO_EXECUTABLE_DEPTH" in per_side["YES"].reason
    assert "insufficient ask depth" in per_side["YES"].reason
    assert broker.state.positions == []


def test_partial_ask_liquidity_records_requested_and_executable_size(tmp_path):
    result, _per_side, broker = _evaluate_and_open_abnormal_candidate(
        tmp_path,
        p_true=0.97,
        yes_ask=0.50,
        yes_size=80.0,
    )

    assert result is not None
    assert result.side == "YES"
    assert result.requested_size_usd == pytest.approx(100.0)
    assert result.executable_size_usd == pytest.approx(40.0)
    assert result.size_usd == pytest.approx(40.0)
    assert broker.state.positions[0].cost_usd == pytest.approx(40.0)


def test_evaluate_market_blocks_excessive_vwap_price_impact(tmp_path):
    settings = _abnormal_settings(
        tmp_path,
        max_entry_spread_abs=0.20,
        max_entry_spread_pct=1.0,
    )
    market = _abnormal_market()
    signal = WeatherSignal(
        0.99,
        1.0,
        "official-station-residual-high-yes",
        "signal_family=intraday_observation_edge",
        parse_weather_question(market.question),
        entry_size_fraction_override=0.50,
        conservative_yes_probability=0.98,
        conservative_no_probability=0.01,
        selected_side_probability=0.98,
        probability_tier="95",
        event_cap_override_fraction=0.50,
    )
    client = _AbnormalPriceClient(
        OrderBook(
            "yes",
            bids=[OrderLevel(0.23, 1000.0)],
            asks=[OrderLevel(0.24, 1.0), OrderLevel(0.80, 1000.0)],
        ),
        OrderBook("no", bids=[OrderLevel(0.75, 1000.0)], asks=[OrderLevel(0.76, 1000.0)]),
    )

    _result, per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        settings,
        1000.0,
        "temperature",
    )

    assert per_side["YES"].side == "SKIP"
    assert "SKIP_EXCESSIVE_PRICE_IMPACT" in per_side["YES"].reason


def test_abnormal_price_uses_size_override_but_obeys_caps(tmp_path):
    result, _per_side, broker = _evaluate_and_open_abnormal_candidate(
        tmp_path,
        p_true=0.97,
        yes_ask=0.50,
        settings_overrides={
            "max_single_market_fraction": 0.30,
            "observation_tier_80_fraction": 0.10,
            "observation_tier_90_fraction": 0.20,
            "observation_tier_95_fraction": 0.30,
        },
    )

    assert result is not None
    assert result.side == "YES"
    assert result.price_anomaly is True
    assert result.strategy_mode == "hybrid_observation_edge"
    assert result.signal_family == "abnormal_official_station_mispricing"
    assert result.entry_size_fraction_override == pytest.approx(0.30)
    assert result.probability_tier == "95"
    assert result.event_cap_override_fraction is None
    assert result.size_usd == pytest.approx(60.0)
    assert broker.state.positions[0].cost_usd == pytest.approx(60.0)


def test_evaluate_market_does_not_create_event_override_from_probability_alone(tmp_path):
    settings = _abnormal_settings(
        tmp_path,
        observation_tier_80_fraction=0.10,
        observation_tier_90_fraction=0.25,
        observation_tier_95_fraction=0.50,
    )
    market = _abnormal_market()
    signal = WeatherSignal(
        0.97,
        1.0,
        "official-station-residual-high-yes",
        "signal_family=intraday_observation_edge",
        parse_weather_question(market.question),
        entry_size_fraction_override=0.25,
        conservative_yes_probability=0.96,
        conservative_no_probability=0.02,
        selected_side_probability=0.96,
        probability_tier="90",
        event_cap_override_fraction=None,
    )
    client = _AbnormalPriceClient(
        OrderBook("yes", bids=[OrderLevel(0.49, 1000.0)], asks=[OrderLevel(0.50, 1000.0)]),
        OrderBook("no", bids=[OrderLevel(0.49, 1000.0)], asks=[OrderLevel(0.50, 1000.0)]),
    )

    result, _per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        settings,
        200.0,
        "temperature",
    )

    assert result.entry_size_fraction_override == pytest.approx(0.25)
    assert result.probability_tier == "90"
    assert result.event_cap_override_fraction is None


def test_evaluate_market_skip_preserves_calibration_audit_fields(tmp_path):
    settings = _abnormal_settings(
        tmp_path,
        entry_min_expected_net_return_pct=0.99,
    )
    market = _abnormal_market()
    signal = WeatherSignal(
        0.97,
        1.0,
        "official-station-residual-high-yes",
        "signal_family=intraday_observation_edge",
        parse_weather_question(market.question),
        raw_probability=0.97,
        conservative_yes_probability=0.96,
        conservative_no_probability=0.02,
        raw_selected_side_probability=0.97,
        selected_side_probability=0.96,
        calibration_sample_days=1460,
        calibration_profile_key="RKSI|month=6|minute=900|high|C",
        calibration_status="RESIDUAL_PROBABILITY_OK",
        probability_tier="95",
        entry_size_fraction_override=0.50,
        event_cap_override_fraction=0.50,
    )
    client = _AbnormalPriceClient(
        OrderBook("yes", bids=[OrderLevel(0.49, 1000.0)], asks=[OrderLevel(0.50, 1000.0)]),
        OrderBook("no", bids=[OrderLevel(0.49, 1000.0)], asks=[OrderLevel(0.50, 1000.0)]),
    )

    result, _per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        settings,
        200.0,
        "temperature",
    )

    assert result.side == "SKIP"
    assert result.raw_selected_side_probability == pytest.approx(0.97)
    assert result.selected_side_probability == pytest.approx(0.96)
    assert result.probability_tier == "95"
    assert result.calibration_sample_days == 1460
    assert result.calibration_profile_key == "RKSI|month=6|minute=900|high|C"
    assert result.calibration_status == "RESIDUAL_PROBABILITY_OK"
    assert result.requested_size_usd == pytest.approx(100.0)
    assert result.executable_size_usd == pytest.approx(100.0)
    assert result.event_cap_override_fraction == pytest.approx(0.50)


def _assert_evaluation_skips_untradable_market(market_overrides, expected_reason):
    result, per_side = runner_module.evaluate_market(
        _entry_gate_market(**market_overrides),
        _entry_gate_signal(),
        _FinalGateClient(),
        Settings(),
        200.0,
        "temperature",
    )

    assert result.side == "SKIP"
    assert expected_reason in result.reason
    assert per_side == {}


def test_open_position_blocks_accepting_orders_false():
    _assert_evaluation_skips_untradable_market(
        {"accepting_orders": False},
        "SKIP_NOT_ACCEPTING_ORDERS",
    )


def test_open_position_blocks_enable_order_book_false():
    _assert_evaluation_skips_untradable_market(
        {"enable_order_book": False},
        "SKIP_ORDERBOOK_DISABLED",
    )


def test_open_position_blocks_archived_market():
    _assert_evaluation_skips_untradable_market(
        {"archived": True},
        "SKIP_MARKET_ARCHIVED",
    )


def test_final_pre_trade_fetches_clob_tradability(tmp_path):
    broker = runner_module.PaperBroker(_entry_gate_settings(tmp_path))
    client = _FinalGateClient()

    final_result = runner_module._open_position_if_needed(
        broker,
        _entry_gate_market(),
        _entry_gate_signal(),
        _selected_entry_result(),
        "temperature",
        client=client,
    )

    assert final_result.side == "YES"
    assert client.tradability_calls == ["condition-1"]
    assert client.book_calls == ["yes"]
    assert len(broker.state.positions) == 1


def test_final_pre_trade_logs_entry_ask_depth_top5(tmp_path):
    broker = runner_module.PaperBroker(_entry_gate_settings(tmp_path))

    class DepthClient(_FinalGateClient):
        def get_order_book(self, token_id: str) -> OrderBook:
            self.book_calls.append(token_id)
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.49, 1000.0)],
                asks=[
                    OrderLevel(0.50, 1.0),
                    OrderLevel(0.51, 2.0),
                    OrderLevel(0.52, 300.0),
                    OrderLevel(0.53, 4.0),
                    OrderLevel(0.54, 5.0),
                    OrderLevel(0.55, 6.0),
                ],
            )

    final_result = runner_module._open_position_if_needed(
        broker,
        _entry_gate_market(),
        _entry_gate_signal(),
        _selected_entry_result(),
        "temperature",
        client=DepthClient(),
    )

    assert final_result.side == "YES"
    with Path(broker.trades_csv_path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    snapshot_text = rows[-1]["entry_ask_depth_top5_json"]
    assert f"entry_ask_depth_top5={snapshot_text}" in rows[-1]["reason"]
    snapshot = json.loads(snapshot_text)
    assert snapshot["target_usd"] == 100.0
    assert snapshot["target_executable"] is True
    assert snapshot["entry_size_usd"] == pytest.approx(10.0)
    assert [level["price"] for level in snapshot["levels"]] == [0.5, 0.51, 0.52, 0.53, 0.54]
    assert snapshot["levels"][1]["cumulative_notional_usd"] == pytest.approx(1.52)


def test_final_pre_trade_blocks_new_excessive_vwap_price_impact(tmp_path):
    broker = runner_module.PaperBroker(
        replace(
            _entry_gate_settings(tmp_path),
            max_entry_spread_abs=0.20,
            max_entry_spread_pct=1.0,
        )
    )

    class ImpactClient(_FinalGateClient):
        def get_order_book(self, token_id: str) -> OrderBook:
            self.book_calls.append(token_id)
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.23, 1000.0)],
                asks=[OrderLevel(0.24, 1.0), OrderLevel(0.80, 1000.0)],
            )

    result = runner_module._open_position_if_needed(
        broker,
        _entry_gate_market(),
        _entry_gate_signal(),
        runner_module.EdgeResult("YES", 0.95, 0.24, 0.70, 100.0, 400.0, "selected"),
        "temperature",
        client=ImpactClient(),
    )

    assert result.side == "SKIP"
    assert "SKIP_EXCESSIVE_PRICE_IMPACT" in result.reason
    assert broker.state.positions == []


def test_final_pre_trade_revalidates_station_signal_and_blocks_probability_drop(tmp_path):
    question = "Will the highest temperature in Seoul be 23C today?"
    settings = _entry_gate_settings(tmp_path)
    broker = runner_module.PaperBroker(settings)
    market = RawMarket(
        "seoul-23c",
        question,
        "seoul-23c",
        True,
        False,
        "yes",
        "no",
        condition_id="condition-1",
        accepting_orders=True,
        enable_order_book=True,
        archived=False,
    )
    initial_signal = WeatherSignal(
        0.97,
        1.0,
        "official-station-residual-high-yes",
        "signal_family=intraday_observation_edge",
        parse_weather_question(question),
        raw_probability=0.97,
        conservative_yes_probability=0.96,
        conservative_no_probability=0.02,
        selected_side_probability=0.96,
        signal_family="intraday_observation_edge",
        entry_size_fraction_override=0.50,
        probability_tier="95",
        event_cap_override_fraction=0.50,
    )
    provisional_result = runner_module.EdgeResult(
        "YES",
        0.97,
        0.50,
        0.46,
        50.0,
        100.0,
        "selected from stale signal",
    )
    calls: list[str] = []

    def final_estimator(question, **_kwargs):
        calls.append(question)
        return WeatherSignal(
            0.5,
            0.0,
            "official-station-residual-below-tier",
            "signal_family=intraday_observation_edge; residual_probability=below observation tier",
            parse_weather_question(question),
            raw_probability=0.89,
            conservative_yes_probability=0.79,
            conservative_no_probability=0.05,
            selected_side_probability=0.79,
            calibration_status="RESIDUAL_PROBABILITY_OK",
        )

    final_result = runner_module._open_position_if_needed(
        broker,
        market,
        initial_signal,
        provisional_result,
        "temperature",
        client=_FinalGateClient(),
        probability_estimator=final_estimator,
        observation_provider=object(),
        residual_profile_store=object(),
    )

    assert calls == [question]
    assert final_result.side == "SKIP"
    assert "SKIP_FINAL_STATION_SIGNAL" in final_result.reason
    assert broker.state.positions == []


def test_final_pre_trade_reuses_just_verified_station_signal(tmp_path):
    question = "Will the highest temperature in Seoul be 23C today?"
    settings = _entry_gate_settings(tmp_path)
    broker = runner_module.PaperBroker(settings)
    market = _entry_gate_market()
    signal = WeatherSignal(
        0.97,
        1.0,
        "official-station-residual-high-yes",
        "signal_family=intraday_observation_edge",
        parse_weather_question(question),
        conservative_yes_probability=0.96,
        conservative_no_probability=0.02,
        selected_side_probability=0.96,
        signal_family="intraday_observation_edge",
        entry_size_fraction_override=0.50,
        probability_tier="95",
        event_cap_override_fraction=0.50,
    )

    def unavailable_estimator(_question, **_kwargs):
        raise AssertionError("fresh station evidence must not be fetched again")

    final_result = runner_module._open_position_if_needed(
        broker,
        market,
        signal,
        _selected_entry_result(),
        "temperature",
        client=_FinalGateClient(),
        probability_estimator=unavailable_estimator,
        observation_provider=object(),
        residual_profile_store=object(),
        decision_ts=datetime.now(timezone.utc).isoformat(),
    )

    assert final_result.side == "YES"
    assert len(broker.state.positions) == 1


def test_final_pre_trade_blocks_when_clob_accepting_orders_false(tmp_path):
    broker = runner_module.PaperBroker(_entry_gate_settings(tmp_path))
    client = _FinalGateClient(_tradability(accepting_orders=False))

    final_result = runner_module._open_position_if_needed(
        broker,
        _entry_gate_market(),
        _entry_gate_signal(),
        _selected_entry_result(),
        "temperature",
        client=client,
    )

    assert final_result.side == "SKIP"
    assert "SKIP_NOT_ACCEPTING_ORDERS" in final_result.reason
    assert client.book_calls == []
    assert broker.state.positions == []


def test_final_pre_trade_blocks_when_clob_lookup_fails(tmp_path):
    broker = runner_module.PaperBroker(_entry_gate_settings(tmp_path))
    client = _FinalGateClient(lookup_error=RuntimeError("clob unavailable"))

    final_result = runner_module._open_position_if_needed(
        broker,
        _entry_gate_market(),
        _entry_gate_signal(),
        _selected_entry_result(),
        "temperature",
        client=client,
    )

    assert final_result.side == "SKIP"
    assert "SKIP_TRADABILITY_UNKNOWN" in final_result.reason
    assert client.book_calls == []
    assert broker.state.positions == []


def test_final_pre_trade_does_not_block_only_because_end_date_is_past(tmp_path):
    broker = runner_module.PaperBroker(_entry_gate_settings(tmp_path))
    client = _FinalGateClient()

    final_result = runner_module._open_position_if_needed(
        broker,
        _entry_gate_market(end_date_iso="2020-01-01T00:00:00Z"),
        _entry_gate_signal(),
        _selected_entry_result(),
        "temperature",
        client=client,
    )

    assert final_result.side == "YES"
    assert len(broker.state.positions) == 1


def test_final_pre_trade_blocks_high_when_clob_closes_before_formation_window(tmp_path):
    broker = runner_module.PaperBroker(_entry_gate_settings(tmp_path))
    client = _FinalGateClient(
        _tradability(end_date_iso="2026-05-25T16:00:00Z")
    )
    question = "Will the highest temperature in NYC be 90F on May 25?"
    signal = WeatherSignal(
        0.96,
        1.0,
        "official-station-residual-high-yes",
        "signal_family=intraday_observation_edge",
        parse_weather_question(question),
        nowcast={
            "station_timezone": "America/New_York",
            "target_date_local": "2026-05-25",
            "strategy_direction": "high",
            "first_final_high_local_minute_q25": 13 * 60,
        },
        signal_family="intraday_observation_edge",
    )

    final_result = runner_module._open_position_if_needed(
        broker,
        _entry_gate_market(question=question),
        signal,
        _selected_entry_result(),
        "temperature",
        client=client,
    )

    assert final_result.side == "SKIP"
    assert "SKIP_HIGH_FORMATION_AFTER_CLOB_CLOSE" in final_result.reason
    assert signal.nowcast["data_block_reason"] == "clob-closes-before-high-formation"
    assert signal.nowcast["strategy_allowed_reason"] == "blocked because CLOB closes before high formation"
    assert client.book_calls == []
    assert broker.state.positions == []


def test_open_position_if_needed_rechecks_fresh_spread_before_broker_open(tmp_path):
    question = "Will NYC reach 90 F on May 25?"
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        min_net_edge=0.01,
        min_order_usd=1.0,
        max_entry_spread_abs=0.05,
        max_entry_spread_pct=1.0,
        weather_taker_fee_rate=0.0,
        model_error_margin=0.0,
        resolution_error_margin=0.0,
        entry_min_expected_net_return_pct=0.01,
        size_mode="fixed_fraction",
        entry_fraction=0.10,
    )
    broker = runner_module.PaperBroker(settings)
    market = RawMarket("m1", question, "open", True, False, "yes", "no", condition_id="condition-1")
    signal = WeatherSignal(0.90, 0.90, "test", "test", parse_weather_question(question))
    result = runner_module.EdgeResult("YES", 0.90, 0.50, 0.40, 10.0, 20.0, "selected")

    class FinalBookClient:
        def get_clob_market_tradability(self, condition_id: str):
            assert condition_id == "condition-1"
            return _tradability()

        def get_order_book(self, token_id: str) -> OrderBook:
            assert token_id == "yes"
            return OrderBook("yes", bids=[OrderLevel(0.44, 1000.0)], asks=[OrderLevel(0.50, 1000.0)])

    final_result = runner_module._open_position_if_needed(
        broker,
        market,
        signal,
        result,
        "temperature",
        client=FinalBookClient(),
    )

    assert final_result.side == "SKIP"
    assert "SKIP_WIDE_SPREAD" in final_result.reason
    assert broker.state.positions == []
    assert "SKIP_WIDE_SPREAD" in (tmp_path / "trades.csv").read_text(encoding="utf-8")


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


def test_stream_status_phase_waits_when_no_streamable_tokens():
    phase, message = runner_module._stream_status_phase(
        {"thread_alive": False, "stale": True, "status_reason": "websocket receiver thread is not running"},
        token_count=0,
        market_count=0,
        event_count=0,
        city_count=0,
    )

    assert phase == "stream_waiting"
    assert "no streamable temperature markets" in message
    assert "0 tokens across 0 markets" in message


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


def test_stream_rebuild_recovers_dead_or_stale_websocket_threads():
    assert hasattr(runner_module, "_stream_should_rebuild")

    assert runner_module._stream_should_rebuild({"thread_alive": False}, token_count=2) is True
    assert runner_module._stream_should_rebuild({"thread_alive": True, "stale": True}, token_count=2) is True
    assert runner_module._stream_should_rebuild({"thread_alive": True, "stale": False}, token_count=2) is False
    assert runner_module._stream_should_rebuild({"thread_alive": False}, token_count=0) is False


def test_official_station_health_refresh_polls_every_ready_station_for_its_local_date():
    calls = []

    class FakeProvider:
        def observed_temperature_extremes_so_far(self, station, *, target_date, now):
            calls.append((station.station_id, target_date, now))

    now = datetime(2026, 6, 24, 15, 30, tzinfo=timezone.utc)
    runner_module._refresh_official_station_observations(FakeProvider(), now=now)

    assert len(calls) == len(runner_module.TRADING_READY_STATION_MAP)
    by_station = {station_id: target_date for station_id, target_date, _now in calls}
    assert by_station["RKSI"].isoformat() == "2026-06-25"
    assert by_station["KLGA"].isoformat() == "2026-06-24"
    assert all(observed_now == now for _station_id, _target_date, observed_now in calls)


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
