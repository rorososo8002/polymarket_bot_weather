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


def test_stream_backed_client_prefetches_final_books_concurrently_and_reuses_them():
    class Cache:
        def __init__(self) -> None:
            self.books: dict[str, OrderBook] = {}

        def get_order_book(self, token_id: str) -> OrderBook:
            return self.books[token_id]

    class ConcurrentStream:
        def __init__(self) -> None:
            self.cache = Cache()
            self.barrier = threading.Barrier(2)
            self.refresh_calls: list[str] = []

        def fetch_order_book_snapshot(self, token_id: str) -> OrderBook:
            self.refresh_calls.append(token_id)
            self.barrier.wait(timeout=1.0)
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.84, 100.0)],
                asks=[OrderLevel(0.85, 100.0)],
            )

        def apply_rest_snapshot(self, book: OrderBook) -> None:
            token_id = str(book.token_id)
            self.cache.books[token_id] = book

        def refresh_order_book(self, token_id: str) -> OrderBook:
            book = self.fetch_order_book_snapshot(token_id)
            self.apply_rest_snapshot(book)
            return book

    stream = ConcurrentStream()
    client = StreamBackedPolymarketClient(
        "https://gamma.example",
        "https://clob.example",
        stream,
    )
    client._fetch_clob_market_tradability_uncached = lambda condition_id: condition_id  # type: ignore[method-assign]

    status = client.prefetch_final_entry_checks(
        [("condition-a", "token-a"), ("condition-b", "token-b")]
    )

    assert status == {"requested": 2, "book_ready": 2, "failed": 0, "deferred": 0}
    assert sorted(stream.refresh_calls) == ["token-a", "token-b"]
    assert client.refresh_order_book("token-a").best_ask == pytest.approx(0.85)
    assert sorted(stream.refresh_calls) == ["token-a", "token-b"]


def test_stream_backed_client_does_not_wait_for_one_slow_final_prefetch(monkeypatch):
    class Cache:
        def __init__(self) -> None:
            self.books: dict[str, OrderBook] = {}

        def get_order_book(self, token_id: str) -> OrderBook:
            return self.books[token_id]

    class PartiallySlowStream:
        def __init__(self) -> None:
            self.cache = Cache()
            self.release_slow = threading.Event()
            self.slow_fetch_finished = threading.Event()

        def fetch_order_book_snapshot(self, token_id: str) -> OrderBook:
            if token_id == "slow-token":
                self.release_slow.wait(timeout=1.0)
                self.slow_fetch_finished.set()
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.84, 100.0)],
                asks=[OrderLevel(0.95 if token_id == "slow-token" else 0.85, 100.0)],
            )

        def apply_rest_snapshot(self, book: OrderBook) -> None:
            token_id = str(book.token_id)
            self.cache.books[token_id] = book

        def refresh_order_book(self, token_id: str) -> OrderBook:
            book = OrderBook(
                token_id,
                bids=[OrderLevel(0.81, 100.0)],
                asks=[OrderLevel(0.82, 100.0)],
            )
            self.apply_rest_snapshot(book)
            return book

    stream = PartiallySlowStream()
    client = StreamBackedPolymarketClient(
        "https://gamma.example",
        "https://clob.example",
        stream,
    )
    client._fetch_clob_market_tradability_uncached = lambda condition_id: condition_id  # type: ignore[method-assign]
    monkeypatch.setattr(runner_module, "REALTIME_FINAL_PREFETCH_DEADLINE_SECONDS", 0.05)

    started = time.monotonic()
    status = client.prefetch_final_entry_checks(
        [("condition-fast", "fast-token"), ("condition-slow", "slow-token")]
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.3
    assert status == {"requested": 2, "book_ready": 1, "failed": 0, "deferred": 1}
    assert client.refresh_order_book("fast-token").best_ask == pytest.approx(0.85)
    with pytest.raises(RuntimeError, match="concurrent final check exceeded"):
        client.get_clob_market_tradability("condition-slow")
    with pytest.raises(RuntimeError, match="concurrent final check exceeded"):
        client.refresh_order_book("slow-token")

    stream.release_slow.set()
    assert stream.slow_fetch_finished.wait(timeout=1.0)
    time.sleep(0.05)

    with pytest.raises(KeyError):
        stream.cache.get_order_book("slow-token")
    assert "slow-token" not in client._final_book_prefetched_at


def test_realtime_signal_prefetch_computes_independent_markets_concurrently(monkeypatch):
    markets = [
        RawMarket(
            f"market-{index}",
            "Will the highest temperature in Seoul be 29C on July 17?",
            f"market-{index}",
            True,
            False,
            f"yes-{index}",
            f"no-{index}",
            event_id=f"event-{index}",
        )
        for index in range(4)
    ]
    barrier = threading.Barrier(len(markets))
    worker_names: set[str] = set()

    def estimator(question: str, **_kwargs) -> WeatherSignal:
        worker_names.add(threading.current_thread().name)
        barrier.wait(timeout=1.0)
        return WeatherSignal(
            0.1,
            1.0,
            "concurrent-test",
            "cached observation",
            parse_weather_question(question),
        )

    monkeypatch.setattr(runner_module, "pre_station_tradeability_gate", lambda *_args: None)
    signals: dict[str, WeatherSignal] = {}
    refreshed: dict[str, datetime] = {}

    errors = runner_module._prefetch_realtime_signals(
        markets,
        Settings(),
        signals,
        refreshed,
        probability_estimator=estimator,
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        max_workers=4,
    )

    assert errors == {}
    assert set(signals) == {market.market_id for market in markets}
    assert set(refreshed) == set(signals)
    assert len(worker_names) == len(markets)


def test_realtime_signal_prefetch_keeps_other_markets_when_one_calculation_fails(monkeypatch):
    markets = [
        RawMarket(
            f"market-{temperature}",
            f"Will the highest temperature in Seoul be {temperature}C on July 17?",
            f"market-{temperature}",
            True,
            False,
            f"yes-{temperature}",
            f"no-{temperature}",
            event_id=f"event-{temperature}",
        )
        for temperature in (29, 30)
    ]

    def estimator(question: str, **_kwargs) -> WeatherSignal:
        if "30C" in question:
            raise RuntimeError("one market failed")
        return WeatherSignal(
            0.1,
            1.0,
            "failure-isolation-test",
            "cached observation",
            parse_weather_question(question),
        )

    monkeypatch.setattr(runner_module, "pre_station_tradeability_gate", lambda *_args: None)
    signals: dict[str, WeatherSignal] = {}
    refreshed: dict[str, datetime] = {}

    errors = runner_module._prefetch_realtime_signals(
        markets,
        Settings(),
        signals,
        refreshed,
        probability_estimator=estimator,
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
        max_workers=1,
    )

    assert set(signals) == {"market-29"}
    assert set(refreshed) == {"market-29"}
    assert set(errors) == {"market-30"}
    assert str(errors["market-30"]) == "one market failed"


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


def test_side_liquidity_rejects_crossed_order_book():
    book = OrderBook(
        "no-token",
        bids=[OrderLevel(0.998, 100.0)],
        asks=[OrderLevel(0.370, 100.0)],
    )

    reason = runner_module._side_liquidity_reason("NO", book, Settings(), "temperature")

    assert reason is not None
    assert "CROSSED_ORDER_BOOK" in reason


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


def test_station_refresh_enqueues_high_and_low_exact_no_probes_and_expires_cached_signals():
    high_29 = RawMarket(
        "seoul-high-29",
        "Will the highest temperature in Seoul be 29C on July 8?",
        "seoul-high-29",
        True,
        False,
        "high-29-yes",
        "high-29-no",
        event_id="seoul-high-event",
    )
    high_30_same_event = RawMarket(
        "seoul-high-30",
        "Will the highest temperature in Seoul be 30C on July 8?",
        "seoul-high-30",
        True,
        False,
        "high-30-yes",
        "high-30-no",
        event_id="seoul-high-event",
    )
    low_22 = RawMarket(
        "seoul-low-22",
        "Will the lowest temperature in Seoul be 22C on July 8?",
        "seoul-low-22",
        True,
        False,
        "low-22-yes",
        "low-22-no",
        event_id="seoul-low-event",
    )
    signal_refreshed_at = {
        high_29.market_id: datetime(2026, 7, 8, 3, 0, tzinfo=timezone.utc),
        high_30_same_event.market_id: datetime(2026, 7, 8, 3, 0, tzinfo=timezone.utc),
        low_22.market_id: datetime(2026, 7, 8, 3, 0, tzinfo=timezone.utc),
    }
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={
            "high-29-no": "seoul-high-event",
            "high-30-no": "seoul-high-event",
            "low-22-no": "seoul-low-event",
        },
        evaluator=lambda _tokens: None,
    )

    accepted = runner_module._enqueue_station_refresh_high_exact_no_probes(
        worker,
        [high_29, high_30_same_event, low_22],
        signal_refreshed_at,
    )

    assert accepted == 2
    assert worker.status_snapshot()["queue_depth"] == 2
    assert worker._pending_tokens_by_event == {
        "seoul-high-event": {"high-29-no", "high-30-no"},
        "seoul-low-event": {"low-22-no"},
    }
    assert high_29.market_id not in signal_refreshed_at
    assert high_30_same_event.market_id not in signal_refreshed_at
    assert low_22.market_id not in signal_refreshed_at


def test_station_refresh_high_exact_no_probes_are_bounded_and_rotated():
    markets = [
        RawMarket(
            f"market-{index}",
            f"Will the highest temperature in Seoul be {29 + index}C on July 8?",
            f"market-{index}",
            True,
            False,
            f"yes-{index}",
            f"no-{index}",
            event_id=f"event-{index}",
        )
        for index in range(7)
    ]
    runner_module._station_refresh_probe_cursor = 0

    first_tokens, first_expired = runner_module._station_refresh_high_exact_no_probe_tokens(markets, max_events=3)
    second_tokens, second_expired = runner_module._station_refresh_high_exact_no_probe_tokens(markets, max_events=3)

    assert first_tokens == {"no-0", "no-1", "no-2"}
    assert first_expired == {"market-0", "market-1", "market-2"}
    assert second_tokens == {"no-3", "no-4", "no-5"}
    assert second_expired == {"market-3", "market-4", "market-5"}


def test_changed_station_refresh_enqueues_every_changed_event_without_probe_cap():
    cities = ("seoul", "london", "beijing", "shanghai", "wuhan", "tokyo", "singapore")
    markets = [
        RawMarket(
            f"{city}-market",
            f"Will the highest temperature in {city.title()} be 29C on July 8?",
            f"{city}-market",
            True,
            False,
            f"{city}-yes",
            f"{city}-no",
            event_id=f"{city}-event",
        )
        for city in cities
    ]
    calls: list[set[str]] = []
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={f"{city}-no": f"{city}-event" for city in cities},
        evaluator=lambda tokens: calls.append(set(tokens)),
    )
    signal_refreshed_at = {
        market.market_id: datetime(2026, 7, 8, 3, 0, tzinfo=timezone.utc)
        for market in markets
    }
    changed_station_ids = {
        runner_module.TRADING_READY_STATION_MAP[city].station_id for city in cities
    }

    accepted = runner_module._enqueue_station_refresh_high_exact_no_probes(
        worker,
        markets,
        signal_refreshed_at,
        station_ids=changed_station_ids,
    )

    assert accepted == len(cities)
    assert worker.status_snapshot()["queue_depth"] == len(cities)
    assert signal_refreshed_at == {}

    assert worker._run_pending_batch_once() is True
    assert calls == [{f"{city}-no" for city in cities}]
    assert worker.status_snapshot()["queue_depth"] == 0


def test_station_refresh_enqueues_only_changed_city_high_exact_no(monkeypatch):
    seoul = runner_module.TRADING_READY_STATION_MAP["seoul"]
    london = runner_module.TRADING_READY_STATION_MAP["london"]
    monkeypatch.setattr(
        runner_module,
        "TRADING_READY_STATION_MAP",
        {"seoul": seoul, "london": london},
    )
    now = datetime(2026, 7, 8, 7, 0, tzinfo=timezone.utc)
    state_by_station = {}
    high_by_station = {seoul.station_id: 29.0, london.station_id: 22.0}
    observed_at_by_station = {seoul.station_id: now, london.station_id: now}

    class FakeProvider:
        def observed_temperature_extremes_so_far(self, station, *, target_date, now):
            high = high_by_station[station.station_id]
            return StationNowcastObservation(
                station_id=station.station_id,
                station_name=station.station_name,
                observed_high_c=high,
                observed_low_c=15.0,
                observed_at=observed_at_by_station[station.station_id],
                high_observed_at=observed_at_by_station[station.station_id],
                low_observed_at=observed_at_by_station[station.station_id],
                source="fixture",
                source_url="https://example.test/source",
                settlement_source_url="https://example.test/settlement",
                freshness_seconds=0,
                unavailable_reason="",
            )

    initial_changed_station_ids = runner_module._refresh_official_station_observations(
        FakeProvider(),
        now=now,
        station_state_by_id=state_by_station,
    )
    high_by_station[seoul.station_id] = 30.0
    observed_at_by_station[seoul.station_id] = now + timedelta(minutes=30)
    changed_station_ids = runner_module._refresh_official_station_observations(
        FakeProvider(),
        now=now + timedelta(minutes=30),
        station_state_by_id=state_by_station,
    )

    seoul_market = RawMarket(
        "seoul-high-29",
        "Will the highest temperature in Seoul be 29C on July 8?",
        "seoul-high-29",
        True,
        False,
        "seoul-yes",
        "seoul-no",
        event_id="seoul-high-event",
    )
    london_market = RawMarket(
        "london-high-21",
        "Will the highest temperature in London be 21C on July 8?",
        "london-high-21",
        True,
        False,
        "london-yes",
        "london-no",
        event_id="london-high-event",
    )
    signal_refreshed_at = {
        seoul_market.market_id: now,
        london_market.market_id: now,
    }
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={
            "seoul-no": "seoul-high-event",
            "london-no": "london-high-event",
        },
        evaluator=lambda _tokens: None,
    )

    accepted = runner_module._enqueue_station_refresh_high_exact_no_probes(
        worker,
        [seoul_market, london_market],
        signal_refreshed_at,
        station_ids=changed_station_ids,
    )

    assert initial_changed_station_ids == set()
    assert changed_station_ids == {seoul.station_id}
    assert accepted == 1
    assert worker._pending_tokens_by_event == {"seoul-high-event": {"seoul-no"}}
    assert seoul_market.market_id not in signal_refreshed_at
    assert london_market.market_id in signal_refreshed_at


def test_station_refresh_detects_high_drop_and_low_rise_reports(monkeypatch):
    station = runner_module.TRADING_READY_STATION_MAP["seoul"]
    monkeypatch.setattr(runner_module, "TRADING_READY_STATION_MAP", {"seoul": station})
    now = datetime(2026, 7, 8, 7, 0, tzinfo=timezone.utc)
    latest = {"temp": 30.0, "high_drop": None, "low_rise": None}

    class FakeProvider:
        def observed_temperature_extremes_so_far(self, _station, *, target_date, now):
            del target_date
            return StationNowcastObservation(
                station_id=station.station_id,
                station_name=station.station_name,
                observed_high_c=30.0,
                observed_low_c=21.0,
                observed_at=now,
                high_observed_at=now - timedelta(hours=1),
                high_drop_observed_at=latest["high_drop"],
                low_observed_at=now - timedelta(hours=2),
                low_rise_observed_at=latest["low_rise"],
                latest_temp_c=latest["temp"],
                source="fixture",
                source_url="https://example.test/source",
                settlement_source_url="https://example.test/settlement",
                freshness_seconds=0,
                unavailable_reason="",
            )

    state_by_station: dict[str, tuple] = {}
    runner_module._refresh_official_station_observations(
        FakeProvider(),
        now=now,
        station_state_by_id=state_by_station,
    )

    latest.update(temp=29.0, high_drop=now + timedelta(minutes=30))
    high_drop_changed = runner_module._refresh_official_station_observations(
        FakeProvider(),
        now=now + timedelta(minutes=30),
        station_state_by_id=state_by_station,
    )
    latest.update(temp=22.0, low_rise=now + timedelta(hours=1))
    low_rise_changed = runner_module._refresh_official_station_observations(
        FakeProvider(),
        now=now + timedelta(hours=1),
        station_state_by_id=state_by_station,
    )

    assert high_drop_changed == {station.station_id}
    assert low_rise_changed == {station.station_id}


def test_station_state_key_detects_due_and_unavailable_status_changes():
    station = runner_module.TRADING_READY_STATION_MAP["seoul"]
    observation = StationNowcastObservation(
        station_id=station.station_id,
        station_name=station.station_name,
        observed_high_c=30.0,
        observed_low_c=21.0,
        observed_at=datetime(2026, 7, 8, 7, 0, tzinfo=timezone.utc),
        high_observed_at=datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc),
        low_observed_at=datetime(2026, 7, 8, 5, 0, tzinfo=timezone.utc),
        source="fixture",
        source_url="https://example.test/source",
        settlement_source_url="https://example.test/settlement",
        freshness_seconds=0,
        unavailable_reason="",
        observation_due_status="current",
    )

    current = runner_module._station_observation_state_key(observation)
    overdue = runner_module._station_observation_state_key(
        replace(observation, observation_due_status="overdue")
    )
    unavailable = runner_module._station_observation_state_key(
        replace(observation, unavailable_reason="stale-observation")
    )

    assert current != overdue
    assert current != unavailable


def test_quiet_market_wakes_when_local_q75_gate_is_crossed():
    market = RawMarket(
        "seoul-high-29",
        "Will the highest temperature in Seoul be 29C on July 8?",
        "seoul-high-29",
        True,
        False,
        "yes-token",
        "no-token",
        event_id="seoul-event",
    )
    signal = WeatherSignal(
        0.5,
        0.0,
        "official-station-formation-q75",
        "waiting for formation q75",
        parse_weather_question(market.question),
        nowcast={
            "station_timezone": "Asia/Seoul",
            "target_date_local": "2026-07-08",
            "data_block_reason": "formation-q75-not-reached",
            "first_final_high_local_minute_q75": 14 * 60 + 30,
        },
        signal_family="intraday_observation_edge",
    )

    urgent, normal, expired = runner_module._scheduled_realtime_probe_tokens(
        [market],
        {market.market_id: signal},
        {},
        now=datetime(2026, 7, 8, 5, 31, tzinfo=timezone.utc),
    )

    assert urgent == {"no-token"}
    assert normal == set()
    assert expired == {market.market_id}


def test_quiet_residual_market_wakes_on_new_local_half_hour_bucket():
    market = RawMarket(
        "seoul-high-29",
        "Will the highest temperature in Seoul be 29C on July 8?",
        "seoul-high-29",
        True,
        False,
        "yes-token",
        "no-token",
        event_id="seoul-event",
    )
    signal = WeatherSignal(
        0.05,
        0.9,
        "official-station-residual-high-no",
        "residual signal",
        parse_weather_question(market.question),
        nowcast={
            "station_timezone": "Asia/Seoul",
            "target_date_local": "2026-07-08",
            "data_block_reason": "",
        },
        signal_family="intraday_observation_edge",
    )
    timer_state: dict[str, str] = {}

    first = runner_module._scheduled_realtime_probe_tokens(
        [market],
        {market.market_id: signal},
        timer_state,
        now=datetime(2026, 7, 8, 5, 29, tzinfo=timezone.utc),
    )
    second = runner_module._scheduled_realtime_probe_tokens(
        [market],
        {market.market_id: signal},
        timer_state,
        now=datetime(2026, 7, 8, 5, 30, tzinfo=timezone.utc),
    )

    assert first == (set(), set(), set())
    assert second == (set(), {"no-token"}, {market.market_id})


def test_station_refresh_default_probe_batch_fits_one_evaluation_batch():
    assert (
        runner_module.REALTIME_STATION_REFRESH_PROBE_MAX_EVENTS
        <= runner_module.REALTIME_EVALUATION_BATCH_MAX_EVENTS
    )


def test_realtime_evaluation_trigger_tokens_include_high_and_low_exact_no_and_held_positions():
    high_29 = RawMarket(
        "seoul-high-29",
        "Will the highest temperature in Seoul be 29C on July 8?",
        "seoul-high-29",
        True,
        False,
        "high-29-yes",
        "high-29-no",
        event_id="seoul-high-event",
    )
    high_30_same_event = RawMarket(
        "seoul-high-30",
        "Will the highest temperature in Seoul be 30C on July 8?",
        "seoul-high-30",
        True,
        False,
        "high-30-yes",
        "high-30-no",
        event_id="seoul-high-event",
    )
    low_22 = RawMarket(
        "seoul-low-22",
        "Will the lowest temperature in Seoul be 22C on July 8?",
        "seoul-low-22",
        True,
        False,
        "low-22-yes",
        "low-22-no",
        event_id="seoul-low-event",
    )
    broker = runner_module.PaperBroker(
        Settings(
            state_path=":memory:",
            trades_csv_path=":memory:",
            decisions_csv_path=":memory:",
            raw_snapshots_path=":memory:",
        )
    )
    broker.state.positions = [
        PaperPosition(
            position_id="held",
            market_id="held-market",
            question="Will the highest temperature in Jeddah be 38C or higher on July 8?",
            token_id="held-no",
            side="NO",
            entry_price=0.40,
            shares=10,
            cost_usd=4,
            opened_at=datetime.now(timezone.utc).isoformat(),
            metadata={},
        )
    ]

    trigger_tokens = runner_module._realtime_evaluation_trigger_tokens(
        [high_29, high_30_same_event, low_22],
        broker,
    )

    held_event_key = runner_module._market_event_key(
        runner_module._market_from_position(broker.state.positions[0])
    )
    assert trigger_tokens == {
        "high-29-no": "seoul-high-event",
        "high-30-no": "seoul-high-event",
        "low-22-no": "seoul-low-event",
        "held-no": held_event_key,
    }


def test_realtime_evaluation_trigger_includes_upper_tail_high_no():
    upper_tail = RawMarket(
        "kuala-lumpur-36-plus",
        "Will the highest temperature in Kuala Lumpur be 36C or higher on July 16?",
        "kuala-lumpur-36-plus",
        True,
        False,
        "upper-tail-yes",
        "upper-tail-no",
        event_id="kuala-lumpur-high-event",
    )
    broker = runner_module.PaperBroker(
        Settings(
            state_path=":memory:",
            trades_csv_path=":memory:",
            decisions_csv_path=":memory:",
            raw_snapshots_path=":memory:",
        )
    )

    trigger_tokens = runner_module._realtime_evaluation_trigger_tokens([upper_tail], broker)

    assert trigger_tokens == {"upper-tail-no": "kuala-lumpur-high-event"}


def test_empty_changed_station_set_enqueues_no_fallback_probe():
    market = RawMarket(
        "seoul-high-29",
        "Will the highest temperature in Seoul be 29C on July 8?",
        "seoul-high-29",
        True,
        False,
        "high-29-yes",
        "high-29-no",
        event_id="seoul-high-event",
    )
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={"high-29-no": "seoul-high-event"},
        evaluator=lambda _tokens: None,
    )

    accepted = runner_module._enqueue_station_refresh_high_exact_no_probes(
        worker,
        [market],
        {market.market_id: datetime.now(timezone.utc)},
        station_ids=set(),
    )

    assert accepted == 0
    assert worker.status_snapshot()["queue_depth"] == 0


def test_realtime_orderbook_updates_wake_held_and_candidate_tokens():
    broker = runner_module.PaperBroker(
        Settings(
            state_path=":memory:",
            trades_csv_path=":memory:",
            decisions_csv_path=":memory:",
            raw_snapshots_path=":memory:",
        )
    )
    broker.state.positions = [
        PaperPosition(
            position_id="held",
            market_id="held-market",
            question="Will the highest temperature in Jeddah be 38C or higher on July 8?",
            token_id="held-no",
            side="NO",
            entry_price=0.40,
            shares=10,
            cost_usd=4,
            opened_at=datetime.now(timezone.utc).isoformat(),
            metadata={},
        )
    ]

    tokens = runner_module._orderbook_update_tokens_for_realtime_evaluation(
        {"held-no", "high-29-no", "random-token"},
        broker,
        {"high-29-no"},
    )

    assert tokens == {"held-no", "high-29-no"}


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


def test_realtime_event_priority_does_not_favor_one_same_day_high_event_forever():
    markets = [
        RawMarket(
            f"market-{index}",
            f"Will the highest temperature in Seoul be {29 + index}C on July 8?",
            f"market-{index}",
            True,
            False,
            f"yes-{index}",
            f"no-{index}",
            event_id=f"event-{index}",
        )
        for index in (1, 2)
    ]

    priorities = runner_module._realtime_event_priorities(
        markets,
        open_market_ids=set(),
        now=datetime(2026, 7, 8, 3, 0, tzinfo=timezone.utc),
    )

    assert priorities["event-1"] == priorities["event-2"]


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


def test_realtime_evaluation_coalescer_retries_failed_urgent_event_once():
    calls = 0

    def evaluator(_tokens: set[str]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")

    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={"station-token": "station-event"},
        evaluator=evaluator,
        coalesce_seconds=0.0,
        retry_backoff_seconds=0.0,
    )

    worker.enqueue_tokens({"station-token"}, urgent=True)
    assert worker._run_pending_batch_once() is True
    assert worker.status_snapshot()["queue_depth"] == 1
    assert worker.status_snapshot()["urgent_queue_depth"] == 1

    assert worker._run_pending_batch_once() is True
    assert calls == 2
    assert worker.status_snapshot()["queue_depth"] == 0


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
        max_batch_events=10,
        max_normal_batch_events=10,
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


def test_realtime_evaluation_coalescer_default_keeps_normal_batches_preemptible():
    calls: list[set[str]] = []
    event_key_by_token = {f"token-{index}": f"event-{index}" for index in range(10)}
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token=event_key_by_token,
        evaluator=lambda tokens: calls.append(set(tokens)),
        coalesce_seconds=0.0,
    )

    assert worker.enqueue_tokens(set(event_key_by_token)) == 10
    worker._run_pending_batch_once()

    assert runner_module.REALTIME_NORMAL_EVALUATION_BATCH_MAX_EVENTS == 1
    assert len(calls) == 1
    assert len(calls[0]) == min(
        len(event_key_by_token),
        runner_module.REALTIME_NORMAL_EVALUATION_BATCH_MAX_EVENTS,
    )
    assert worker.status_snapshot()["queue_depth"] == 10 - len(calls[0])


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


def test_realtime_evaluation_coalescer_station_change_jumps_ahead_of_normal_queue():
    calls: list[set[str]] = []

    def evaluator(tokens: set[str]) -> None:
        time.sleep(0.05)
        calls.append(set(tokens))

    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={
            "normal-token": "normal-event",
            "station-token": "station-event",
        },
        evaluator=evaluator,
        max_batch_events=1,
        coalesce_seconds=0.0,
        event_priority=lambda event_key: (0 if event_key == "normal-event" else 9, event_key),
    )

    assert worker.enqueue_tokens({"normal-token"}) == 1
    assert worker.enqueue_tokens({"station-token"}, urgent=True) == 1

    worker._run_pending_batch_once()
    worker._run_pending_batch_once()

    assert calls == [{"station-token"}, {"normal-token"}]
    status = worker.status_snapshot()
    assert status["urgent_queue_depth"] == 0
    assert status["urgent_processed_event_count"] == 1
    assert status["last_urgent_enqueued_at"]
    assert status["last_urgent_evaluated_at"]
    assert status["last_urgent_queue_wait_seconds"] is not None
    assert status["last_urgent_evaluation_duration_seconds"] >= 0.04
    assert status["last_urgent_evaluation_lag_seconds"] is not None


def test_realtime_evaluation_coalescer_drains_existing_backlog_without_recoalescing():
    completed = threading.Event()
    calls: list[set[str]] = []
    event_key_by_token = {f"token-{index}": f"event-{index}" for index in range(8)}

    def evaluator(tokens: set[str]) -> None:
        calls.append(set(tokens))
        if len(calls) == 2:
            completed.set()

    worker = RealtimeEvaluationCoalescer(
        event_key_by_token=event_key_by_token,
        evaluator=evaluator,
        max_batch_events=4,
        coalesce_seconds=0.20,
    )

    started_at = time.monotonic()
    worker.start()
    try:
        worker.enqueue_tokens(set(event_key_by_token), urgent=True)
        assert completed.wait(1.0) is True
        assert time.monotonic() - started_at < 0.35
        assert len(calls) == 2
    finally:
        worker.stop(drain=False)


def test_urgent_event_displaces_normal_when_queue_is_full_and_promotes_pending_event():
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={
            "normal-one": "normal-one-event",
            "normal-two": "normal-two-event",
            "urgent-token": "urgent-event",
        },
        evaluator=lambda _tokens: None,
        max_pending_events=2,
        max_batch_events=1,
        coalesce_seconds=0.0,
    )

    assert worker.enqueue_tokens({"normal-one", "normal-two"}) == 2
    assert worker.enqueue_tokens({"urgent-token"}, urgent=True) == 1
    assert worker.status_snapshot()["queue_depth"] == 2
    assert worker.status_snapshot()["urgent_queue_depth"] == 1
    assert "urgent-event" in worker._pending_tokens_by_event

    remaining_normal_token = next(
        token
        for token in ("normal-one", "normal-two")
        if worker.event_key_by_token[token] in worker._pending_tokens_by_event
    )
    assert worker.enqueue_tokens({remaining_normal_token}, urgent=True) == 1
    assert worker.status_snapshot()["urgent_queue_depth"] == 2


def test_realtime_event_priorities_rank_same_day_high_and_low_exact_before_future_events():
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

    assert priorities["today-high-event"] == priorities["today-low-event"]
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


def test_realtime_stream_markets_exclude_future_events_but_keep_positions():
    now = datetime(2026, 7, 3, 5, 0, tzinfo=timezone.utc)
    today = RawMarket(
        market_id="today",
        question="Will the highest temperature in Seoul be 29°C on July 3?",
        slug="today",
        active=True,
        closed=False,
        yes_token_id="today-yes",
        no_token_id="today-no",
        event_id="today-event",
        rule_provenance=MarketRuleProvenance(
            market_id="today",
            question="Will the highest temperature in Seoul be 29°C on July 3?",
            event_date_local="2026-07-03",
            event_timezone="Asia/Seoul",
        ),
    )
    future = RawMarket(
        market_id="future",
        question="Will the highest temperature in Seoul be 29°C on July 5?",
        slug="future",
        active=True,
        closed=False,
        yes_token_id="future-yes",
        no_token_id="future-no",
        event_id="future-event",
        rule_provenance=MarketRuleProvenance(
            market_id="future",
            question="Will the highest temperature in Seoul be 29°C on July 5?",
            event_date_local="2026-07-05",
            event_timezone="Asia/Seoul",
        ),
    )
    held_future = replace(future, market_id="held-future", no_token_id="held-no")
    broker = type(
        "Broker",
        (),
        {
            "state": PaperState(
                cash_usd=900.0,
                positions=[
                    PaperPosition(
                        position_id="p1",
                        market_id="held-future",
                        question=held_future.question,
                        token_id="held-no",
                        side="NO",
                        entry_price=0.76,
                        shares=108.0,
                        cost_usd=83.0,
                        opened_at="2026-07-03T04:50:00+00:00",
                    )
                ],
            )
        },
    )()

    selected = runner_module._select_realtime_stream_markets([future, today, held_future], broker, now=now)

    assert [market.market_id for market in selected] == ["today", "held-future"]


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
        RawMarket(
            "temperature",
            temperature_question,
            "temperature",
            True,
            False,
            "temp-yes",
            "temp-no",
            rule_provenance=MarketRuleProvenance(
                market_id="temperature",
                question=temperature_question,
                event_date_local=datetime.now(timezone.utc).date().isoformat(),
                event_timezone="UTC",
            ),
        ),
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


def test_realtime_price_update_evaluates_only_the_changed_market_in_event(tmp_path):
    now = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)
    first = RawMarket(
        "seoul-27c",
        "Will the highest temperature in Seoul be 27C today?",
        "seoul-27c",
        True,
        False,
        "yes-27",
        "no-27",
        event_id="seoul-today",
    )
    second = RawMarket(
        "seoul-28c",
        "Will the highest temperature in Seoul be 28C today?",
        "seoul-28c",
        True,
        False,
        "yes-28",
        "no-28",
        event_id="seoul-today",
    )

    class FakeClient:
        def get_order_book(self, token_id):
            raise AssertionError(f"low-confidence market must not read order book for {token_id}")

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        decisions_log_skip_enabled=True,
    )
    broker = runner_module.PaperBroker(settings)
    signals = {
        market.market_id: WeatherSignal(
            0.50,
            0.20,
            "test",
            "fresh low-confidence signal",
            parse_weather_question(market.question),
        )
        for market in (first, second)
    }
    refreshed_at = {market.market_id: now for market in (first, second)}

    runner_module._evaluate_realtime_update(
        {"no-27"},
        FakeClient(),
        broker,
        settings,
        {
            "yes-27": first,
            "no-27": first,
            "yes-28": second,
            "no-28": second,
        },
        signals,
        {first.market_id: "temperature", second.market_id: "temperature"},
        {
            (second.market_id, runner_module.REALTIME_LAST_EVALUATION_SIDE): runner_module.EdgeResult(
                "SKIP", 0.50, None, -999.0, 0.0, 0.0, "already evaluated"
            )
        },
        signal_refreshed_at_by_market=refreshed_at,
        now=now,
    )

    with (tmp_path / "decisions.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["market_id"] for row in rows] == [first.market_id]


def test_realtime_price_update_does_not_refresh_ttl_stale_sibling_market(tmp_path):
    now = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)
    first = RawMarket(
        "seoul-27c",
        "Will the highest temperature in Seoul be 27C today?",
        "seoul-27c",
        True,
        False,
        "yes-27",
        "no-27",
        event_id="seoul-today",
    )
    second = RawMarket(
        "seoul-28c",
        "Will the highest temperature in Seoul be 28C today?",
        "seoul-28c",
        True,
        False,
        "yes-28",
        "no-28",
        event_id="seoul-today",
    )

    class FakeClient:
        def get_order_book(self, token_id):
            raise AssertionError(f"low-confidence market must not read order book for {token_id}")

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        decisions_log_skip_enabled=True,
        station_nowcast_cache_ttl_seconds=60,
    )
    broker = runner_module.PaperBroker(settings)
    signals = {
        market.market_id: WeatherSignal(
            0.50,
            0.20,
            "test",
            "stale low-confidence signal",
            parse_weather_question(market.question),
        )
        for market in (first, second)
    }
    stale_at = now - timedelta(seconds=61)
    refreshed_at = {market.market_id: stale_at for market in (first, second)}
    already_evaluated = {
        (market.market_id, runner_module.REALTIME_LAST_EVALUATION_SIDE): runner_module.EdgeResult(
            "SKIP", 0.50, None, -999.0, 0.0, 0.0, "already evaluated"
        )
        for market in (first, second)
    }

    def refresh_signal(question, **_kwargs):
        return WeatherSignal(0.50, 0.20, "test", "refreshed", parse_weather_question(question))

    runner_module._evaluate_realtime_update(
        {"no-27"},
        FakeClient(),
        broker,
        settings,
        {
            "yes-27": first,
            "no-27": first,
            "yes-28": second,
            "no-28": second,
        },
        signals,
        {first.market_id: "temperature", second.market_id: "temperature"},
        already_evaluated,
        signal_refreshed_at_by_market=refreshed_at,
        probability_estimator=refresh_signal,
        now=now,
    )

    with (tmp_path / "decisions.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["market_id"] for row in rows] == [first.market_id]


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
        settings = Settings(no_only_new_entries=False)

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
        no_only_new_entries=False,
    )


def _selected_entry_result() -> runner_module.EdgeResult:
    return runner_module.EdgeResult("YES", 0.95, 0.50, 0.45, 10.0, 20.0, "selected")


def test_no_only_new_entries_blocks_yes_before_any_final_book_request(tmp_path):
    broker = runner_module.PaperBroker(
        replace(_entry_gate_settings(tmp_path), no_only_new_entries=True)
    )
    client = _FinalGateClient()

    result = runner_module._open_position_if_needed(
        broker,
        _entry_gate_market(),
        _entry_gate_signal(),
        _selected_entry_result(),
        "temperature",
        client=client,
    )

    assert result.side == "SKIP"
    assert "SKIP_NO_ONLY_NEW_ENTRY" in result.reason
    assert client.tradability_calls == []
    assert client.book_calls == []
    assert broker.state.positions == []


def test_no_only_new_entries_removes_yes_before_portfolio_selection():
    market = _entry_gate_market()
    signal = _entry_gate_signal()
    candidates = [
        runner_module.PortfolioCandidate(
            market,
            signal,
            runner_module.EdgeResult(side, 0.95, 0.50, 0.40, 10.0, 20.0, side),
            "temperature",
        )
        for side in ("YES", "NO")
    ]

    filtered = runner_module._new_entry_candidates_for_strategy(
        candidates,
        Settings(no_only_new_entries=True),
    )

    assert [candidate.result.side for candidate in filtered] == ["NO"]


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
        "no_only_new_entries": False,
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


def test_wrh_timeseries_market_blocks_awc_lock_only_entry(tmp_path):
    question = "Will the highest temperature in Moscow be 20°C on July 7?"
    market = RawMarket(
        "moscow-wrh",
        question,
        "highest-temperature-in-moscow-on-july-7-2026",
        True,
        False,
        "yes",
        "no",
        condition_id="condition-1",
        accepting_orders=True,
        enable_order_book=True,
        archived=False,
        rule_provenance=MarketRuleProvenance(
            market_id="moscow-wrh",
            question=question,
            description=(
                "This market will resolve according to NOAA WRH timeseries "
                "https://www.weather.gov/wrh/timeseries?site=UUWW using the Temp column."
            ),
            city="Moscow",
            station_id="UUWW",
            unit="C",
            condition_type="exact",
            exact_value=20.0,
        ),
    )
    signal = WeatherSignal(
        0.0,
        0.95,
        "official-station-lock-strong_no",
        "official same-day high broke exact bucket",
        parse_weather_question(question),
        nowcast={
            "station_id": "UUWW",
            "station_timezone": "Europe/Moscow",
            "target_date_local": "2026-07-07",
            "observed_high_c": 21.0,
            "latest_observation_source": "aviationweather-metar",
            "data_block_reason": "",
        },
        settlement_precision_confidence="verified",
    )
    client = _AbnormalPriceClient(
        OrderBook("yes", bids=[OrderLevel(0.11, 1000.0)], asks=[OrderLevel(0.12, 1000.0)]),
        OrderBook("no", bids=[OrderLevel(0.88, 1000.0)], asks=[OrderLevel(0.89, 1000.0)]),
    )

    result, per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        _abnormal_settings(tmp_path),
        1000.0,
        "temperature",
    )

    assert result.side == "SKIP"
    assert "SKIP_WRH_TIMESERIES_UNVERIFIED" in result.reason
    assert per_side == {}
    assert client.book_calls == []


@pytest.mark.parametrize(
    ("probability", "expected_fraction", "expected_size"),
    [
        (0.96, 0.20, 40.0),
        (0.94, 0.10, 20.0),
        (0.92, 0.05, 10.0),
    ],
)
def test_yes_entries_use_conservative_probability_size_caps(
    tmp_path,
    probability,
    expected_fraction,
    expected_size,
):
    question = "Will the highest temperature in NYC be 90F today?"
    market = _abnormal_market()
    signal = WeatherSignal(
        probability,
        1.0,
        "official-station-residual-high-yes",
        "signal_family=intraday_observation_edge",
        parse_weather_question(question),
        entry_size_fraction_override=0.50,
        conservative_yes_probability=probability,
        conservative_no_probability=0.01,
        selected_side_probability=probability,
        probability_tier="95",
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
        _abnormal_settings(tmp_path, max_single_market_fraction=0.90),
        200.0,
        "temperature",
    )

    assert result.side == "YES"
    assert result.entry_size_fraction_override == pytest.approx(expected_fraction)
    assert result.requested_size_usd == pytest.approx(expected_size)
    assert result.size_usd == pytest.approx(expected_size)


def test_partial_ask_liquidity_records_requested_and_executable_size(tmp_path):
    result, _per_side, broker = _evaluate_and_open_abnormal_candidate(
        tmp_path,
        p_true=0.97,
        yes_ask=0.50,
        yes_size=50.0,
    )

    assert result is not None
    assert result.side == "YES"
    assert result.requested_size_usd == pytest.approx(40.0)
    assert result.executable_size_usd == pytest.approx(25.0)
    assert result.size_usd == pytest.approx(25.0)
    assert broker.state.positions[0].cost_usd == pytest.approx(25.0)


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
    assert result.entry_size_fraction_override == pytest.approx(0.20)
    assert result.probability_tier == "95"
    assert result.event_cap_override_fraction is None
    assert result.size_usd == pytest.approx(40.0)
    assert broker.state.positions[0].cost_usd == pytest.approx(40.0)


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

    assert result.entry_size_fraction_override == pytest.approx(0.20)
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
    assert result.requested_size_usd == pytest.approx(40.0)
    assert result.executable_size_usd == pytest.approx(40.0)
    assert result.event_cap_override_fraction == pytest.approx(0.20)


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


def test_final_pre_trade_refreshes_lock_only_no_book_with_rest_helper(tmp_path):
    question = "Will the highest temperature in Seoul be 29C on July 8?"
    settings = _entry_gate_settings(tmp_path)
    signal = WeatherSignal(
        0.0,
        1.0,
        "official-station-lock-strong_no",
        "official_nowcast_lock=strong_no",
        parse_weather_question(question),
        nowcast={"station_id": "RKSI"},
        settlement_precision_confidence="verified",
    )
    market = _entry_gate_market(
        market_id="seoul-29c",
        question=question,
        yes_token_id="yes",
        no_token_id="no",
    )
    selected = runner_module.EdgeResult(
        "NO",
        0.0,
        0.91,
        0.08,
        100.0,
        109.0,
        "selected lock-only no",
    )

    class RestRefreshingClient(_FinalGateClient):
        def __init__(self) -> None:
            super().__init__()
            self.book = OrderBook(
                "no",
                bids=[OrderLevel(0.98, 1000.0)],
                asks=[OrderLevel(0.99, 1000.0)],
            )
            self.refresh_calls: list[str] = []

        def refresh_order_book(self, token_id: str) -> OrderBook:
            self.refresh_calls.append(token_id)
            self.book = OrderBook(
                token_id,
                bids=[OrderLevel(0.87, 1000.0)],
                asks=[OrderLevel(0.88, 1000.0)],
            )
            return self.book

        def get_order_book(self, token_id: str) -> OrderBook:
            self.book_calls.append(token_id)
            return self.book

    client = RestRefreshingClient()

    result = runner_module._final_pre_trade_entry_result(
        market,
        signal,
        selected,
        "no",
        client,
        settings,
        "temperature",
    )

    assert client.refresh_calls == ["no"]
    assert result.side == "NO"
    assert result.p_exec == pytest.approx(0.88)
    assert "final_book_source=rest_helper" in result.reason


def test_final_pre_trade_refreshes_non_lock_selected_book_with_rest_helper(tmp_path):
    question = "Will the highest temperature in Seoul be 31C on July 8?"
    settings = _entry_gate_settings(tmp_path)
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
        settlement_precision_confidence="verified",
    )
    market = _entry_gate_market(
        market_id="seoul-31c",
        question=question,
        yes_token_id="yes",
        no_token_id="no",
    )
    selected = runner_module.EdgeResult(
        "YES", 0.97, 0.55, 0.35, 20.0, 35.0, "selected residual yes"
    )

    class RestRefreshingClient(_FinalGateClient):
        def __init__(self) -> None:
            super().__init__()
            self.refresh_calls: list[str] = []

        def refresh_order_book(self, token_id: str) -> OrderBook:
            self.refresh_calls.append(token_id)
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.50, 1000.0)],
                asks=[OrderLevel(0.52, 1000.0)],
            )

    client = RestRefreshingClient()
    result = runner_module._final_pre_trade_entry_result(
        market,
        signal,
        selected,
        "yes",
        client,
        settings,
        "temperature",
    )

    assert client.refresh_calls == ["yes"]
    assert result.side == "YES"
    assert result.p_exec == pytest.approx(0.52)
    assert "final_book_source=rest_helper" in result.reason


def test_evaluate_market_uses_stream_book_until_final_candidate_is_selected(tmp_path):
    question = "Will the highest temperature in Seoul be 29C on July 8?"
    signal = WeatherSignal(
        0.0,
        1.0,
        "official-station-lock-strong_no",
        "official_nowcast_lock=strong_no",
        parse_weather_question(question),
        nowcast={"station_id": "RKSI"},
        settlement_precision_confidence="verified",
    )
    market = _entry_gate_market(
        market_id="seoul-29c",
        question=question,
        yes_token_id="yes",
        no_token_id="no",
    )

    class RestRefreshingClient(_FinalGateClient):
        def __init__(self) -> None:
            super().__init__()
            self.books = {
                "yes": OrderBook("yes", bids=[OrderLevel(0.01, 1000.0)], asks=[OrderLevel(0.02, 1000.0)]),
                "no": OrderBook("no", bids=[OrderLevel(0.87, 1000.0)], asks=[OrderLevel(0.88, 1000.0)]),
            }
            self.refresh_calls: list[str] = []

        def refresh_order_book(self, token_id: str) -> OrderBook:
            self.refresh_calls.append(token_id)
            self.books[token_id] = OrderBook(
                token_id,
                bids=[OrderLevel(0.98, 1000.0)],
                asks=[OrderLevel(0.99, 1000.0)],
            )
            return self.books[token_id]

        def get_order_book(self, token_id: str) -> OrderBook:
            self.book_calls.append(token_id)
            return self.books[token_id]

    client = RestRefreshingClient()
    result, per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        _entry_gate_settings(tmp_path),
        100.0,
        "temperature",
    )

    assert client.refresh_calls == []
    assert result.side == "NO"
    assert per_side["NO"].p_exec == pytest.approx(0.88)


def test_candidate_book_scan_uses_cache_only_without_rest_fallback():
    market = _entry_gate_market(
        market_id="seoul-29c",
        question="Will the highest temperature in Seoul be 29C on July 8?",
        yes_token_id="yes",
        no_token_id="no",
    )

    class CandidateCacheClient:
        def __init__(self) -> None:
            self.rest_fallback_calls: list[str] = []

        def get_candidate_order_book(self, token_id: str) -> OrderBook:
            if token_id == "no":
                return OrderBook("no", bids=[OrderLevel(0.87, 100.0)], asks=[OrderLevel(0.88, 100.0)])
            raise KeyError(token_id)

        def get_order_book(self, token_id: str) -> OrderBook:
            self.rest_fallback_calls.append(token_id)
            return OrderBook(token_id, bids=[], asks=[])

    client = CandidateCacheClient()
    books, error = runner_module._fetch_books(market, client)

    assert error is None
    assert set(books) == {"NO"}
    assert client.rest_fallback_calls == []


def test_candidate_book_scan_refreshes_only_preferred_missing_side():
    market = _entry_gate_market(
        market_id="paris-34c",
        question="Will the highest temperature in Paris be 34C on July 14?",
        yes_token_id="yes",
        no_token_id="no",
    )

    class CandidateClient:
        def __init__(self) -> None:
            self.refresh_calls: list[str] = []

        def get_candidate_order_book(self, token_id: str) -> OrderBook:
            raise KeyError(token_id)

        def refresh_order_book(self, token_id: str) -> OrderBook:
            self.refresh_calls.append(token_id)
            return OrderBook(token_id, bids=[OrderLevel(0.80, 100.0)], asks=[OrderLevel(0.82, 100.0)])

    client = CandidateClient()
    books, error = runner_module._fetch_books(market, client, preferred_side="NO")

    assert error is None
    assert set(books) == {"NO"}
    assert client.refresh_calls == ["no"]


def test_candidate_book_scan_refreshes_crossed_preferred_side():
    market = _entry_gate_market(
        market_id="paris-34c",
        question="Will the highest temperature in Paris be 34C on July 14?",
        yes_token_id="yes",
        no_token_id="no",
    )

    class CandidateClient:
        def __init__(self) -> None:
            self.refresh_calls: list[str] = []

        def get_candidate_order_book(self, token_id: str) -> OrderBook:
            if token_id == "no":
                return OrderBook(token_id, bids=[OrderLevel(0.998, 100.0)], asks=[OrderLevel(0.37, 100.0)])
            raise KeyError(token_id)

        def refresh_order_book(self, token_id: str) -> OrderBook:
            self.refresh_calls.append(token_id)
            return OrderBook(token_id, bids=[OrderLevel(0.80, 100.0)], asks=[OrderLevel(0.82, 100.0)])

    client = CandidateClient()
    books, error = runner_module._fetch_books(market, client, preferred_side="NO")

    assert error is None
    assert books["NO"].best_bid == pytest.approx(0.80)
    assert books["NO"].best_ask == pytest.approx(0.82)
    assert client.refresh_calls == ["no"]


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


def test_final_pre_trade_revalidates_even_when_decision_is_recent(tmp_path):
    question = "Will the highest temperature in Seoul be 23C today?"
    settings = _entry_gate_settings(tmp_path)
    broker = runner_module.PaperBroker(settings)
    market = _entry_gate_market(question=question)
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

    calls = []

    def changed_estimator(question, **_kwargs):
        calls.append(question)
        return WeatherSignal(
            0.5,
            0.0,
            "official-station-observation-report-pending",
            "newer station evidence is pending",
            parse_weather_question(question),
        )

    final_result = runner_module._open_position_if_needed(
        broker,
        market,
        signal,
        _selected_entry_result(),
        "temperature",
        client=_FinalGateClient(),
        probability_estimator=changed_estimator,
        observation_provider=object(),
        residual_profile_store=object(),
        decision_ts=datetime.now(timezone.utc).isoformat(),
    )

    assert calls == [question]
    assert final_result.side == "SKIP"
    assert "SKIP_FINAL_STATION_SIGNAL" in final_result.reason
    assert broker.state.positions == []


def test_final_pre_trade_revalidates_official_lock_signal(tmp_path):
    question = "Will the highest temperature in Seoul be 23C today?"
    broker = runner_module.PaperBroker(_entry_gate_settings(tmp_path))
    market = _entry_gate_market(question=question)
    signal = WeatherSignal(
        0.0,
        1.0,
        "official-station-lock-strong_no",
        "official_nowcast_lock=strong_no",
        parse_weather_question(question),
    )
    selected = runner_module.EdgeResult("NO", 0.0, 0.50, 0.45, 10.0, 20.0, "selected lock")
    calls = []

    def changed_estimator(requested_question, **_kwargs):
        calls.append(requested_question)
        return WeatherSignal(
            0.5,
            0.0,
            "official-station-unavailable",
            "fresh official request failed",
            parse_weather_question(requested_question),
        )

    final_result = runner_module._open_position_if_needed(
        broker,
        market,
        signal,
        selected,
        "temperature",
        client=_FinalGateClient(),
        probability_estimator=changed_estimator,
        observation_provider=object(),
    )

    assert calls == [question]
    assert final_result.side == "SKIP"
    assert "SKIP_FINAL_STATION_SIGNAL" in final_result.reason
    assert broker.state.positions == []


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
        no_only_new_entries=False,
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
