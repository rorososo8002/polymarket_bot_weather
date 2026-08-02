import csv
import hashlib
import json
from queue import SimpleQueue
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

        def apply_rest_snapshot(self, book: OrderBook, *, notify: bool = True) -> None:
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

    cached_status = client.prefetch_final_entry_checks(
        [("condition-a", "token-a"), ("condition-b", "token-b")]
    )

    assert cached_status == {"requested": 2, "book_ready": 2, "failed": 0, "deferred": 0}
    assert sorted(stream.refresh_calls) == ["token-a", "token-b"]
    assert client.refresh_order_book("token-a").best_ask == pytest.approx(0.85)
    assert sorted(stream.refresh_calls) == ["token-a", "token-b"]


def test_final_prefetch_complement_failure_does_not_poison_direct_route():
    class Cache:
        def __init__(self) -> None:
            self.books: dict[str, OrderBook] = {}

        def get_order_book(self, token_id: str) -> OrderBook:
            return self.books[token_id]

    class PartialStream:
        def __init__(self) -> None:
            self.cache = Cache()

        def fetch_order_book_snapshot(self, token_id: str) -> OrderBook:
            if token_id == "yes-complement":
                raise RuntimeError("complement unavailable")
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.83, 100.0)],
                asks=[OrderLevel(0.84, 100.0)],
            )

        def apply_rest_snapshot(self, book: OrderBook, *, notify: bool = True) -> None:
            self.cache.books[str(book.token_id)] = book

        def refresh_order_book(self, token_id: str) -> OrderBook:
            book = self.fetch_order_book_snapshot(token_id)
            self.apply_rest_snapshot(book)
            return book

    client = StreamBackedPolymarketClient(
        "https://gamma.example",
        "https://clob.example",
        PartialStream(),
    )
    client._fetch_clob_market_tradability_uncached = (  # type: ignore[method-assign]
        lambda condition_id: condition_id
    )

    status = client.prefetch_final_entry_checks(
        [("condition-a", "no-direct"), ("condition-a", "yes-complement")]
    )

    assert status == {"requested": 2, "book_ready": 1, "failed": 1, "deferred": 0}
    assert client.get_clob_market_tradability("condition-a") == "condition-a"
    assert client.refresh_order_book("no-direct").best_ask == pytest.approx(0.84)
    with pytest.raises(RuntimeError, match="concurrent final check exceeded"):
        client.refresh_order_book("yes-complement")


def test_stream_backed_client_prefetches_candidate_books_without_tradability_lookup():
    class Cache:
        def __init__(self) -> None:
            self.books: dict[str, OrderBook] = {}

        def get_order_book(self, token_id: str) -> OrderBook:
            return self.books[token_id]

    class CandidateStream:
        def __init__(self) -> None:
            self.cache = Cache()

        def apply_rest_snapshot(self, book: OrderBook, *, notify: bool = True) -> None:
            self.cache.books[str(book.token_id)] = book

    stream = CandidateStream()
    client = StreamBackedPolymarketClient(
        "https://gamma.example",
        "https://clob.example",
        stream,
    )
    client._fetch_clob_market_tradability_uncached = (  # type: ignore[method-assign]
        lambda _condition_id: (_ for _ in ()).throw(AssertionError("candidate prefetch must not fetch tradability"))
    )
    batch_calls: list[tuple[list[str], float | None]] = []

    def get_order_books(token_ids: list[str], *, timeout: float | None = None) -> list[OrderBook]:
        batch_calls.append((list(token_ids), timeout))
        return [
            OrderBook(
                token_id,
                bids=[OrderLevel(0.79, 100.0)],
                asks=[OrderLevel(0.80, 100.0)],
            )
            for token_id in token_ids
        ]

    client.get_order_books = get_order_books  # type: ignore[method-assign]

    status = client.prefetch_candidate_order_books(["token-a", "token-b"])

    assert status == {"requested": 2, "book_ready": 2, "failed": 0, "deferred": 0}
    assert batch_calls == [
        (["token-a", "token-b"], min(0.75, runner_module.REALTIME_FINAL_PREFETCH_DEADLINE_SECONDS))
    ]
    assert stream.cache.get_order_book("token-a").best_ask == pytest.approx(0.80)


def test_candidate_prefetch_retries_only_token_missing_from_batch():
    class Cache:
        def __init__(self) -> None:
            self.books: dict[str, OrderBook] = {}

        def get_order_book(self, token_id: str) -> OrderBook:
            return self.books[token_id]

    class CandidateStream:
        def __init__(self) -> None:
            self.cache = Cache()

        def apply_rest_snapshot(self, book: OrderBook, *, notify: bool = True) -> None:
            self.cache.books[str(book.token_id)] = book

    stream = CandidateStream()
    client = StreamBackedPolymarketClient(
        "https://gamma.example",
        "https://clob.example",
        stream,
    )
    calls: list[list[str]] = []

    def get_order_books(token_ids: list[str], *, timeout: float | None = None) -> list[OrderBook]:
        calls.append(list(token_ids))
        returned = ["token-a"] if len(calls) == 1 else token_ids
        return [
            OrderBook(token, bids=[OrderLevel(0.79, 100.0)], asks=[OrderLevel(0.80, 100.0)])
            for token in returned
        ]

    client.get_order_books = get_order_books  # type: ignore[method-assign]

    status = client.prefetch_candidate_order_books(["token-a", "token-b"])

    assert calls == [["token-a", "token-b"], ["token-b"]]
    assert status == {"requested": 2, "book_ready": 2, "failed": 0, "deferred": 0}
    assert stream.cache.get_order_book("token-a").best_ask == pytest.approx(0.80)
    assert stream.cache.get_order_book("token-b").best_ask == pytest.approx(0.80)
    assert client.get_candidate_order_book_audit("token-b")["status"] == "ready"


def test_due_candidate_book_retry_runs_without_websocket_update():
    class Worker:
        def __init__(self) -> None:
            self.calls: list[tuple[set[str], bool]] = []

        def enqueue_tokens(self, token_ids: set[str], *, urgent: bool = False) -> int:
            self.calls.append((set(token_ids), urgent))
            return len(token_ids)

    worker = Worker()
    retries = {
        "urgent-token": (10.0, "missing_response", True),
        "quiet-token": (10.0, "empty_ask", False),
    }

    assert runner_module._enqueue_due_candidate_book_retries(worker, retries, now_monotonic=9.999) == 0
    assert runner_module._enqueue_due_candidate_book_retries(worker, retries, now_monotonic=10.0) == 2

    assert worker.calls == [({"urgent-token"}, True), ({"quiet-token"}, False)]
    assert retries["urgent-token"][0] == pytest.approx(12.0)
    assert retries["quiet-token"][0] == pytest.approx(15.0)


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

        def apply_rest_snapshot(self, book: OrderBook, *, notify: bool = True) -> None:
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


def test_fetch_books_combines_direct_no_depth_with_complementary_yes_depth():
    class ComplementaryClient:
        def get_order_book(self, token_id: str) -> OrderBook:
            if token_id == "yes-token":
                return OrderBook(
                    token_id,
                    bids=[OrderLevel(0.20, 10.0)],
                    asks=[OrderLevel(0.21, 30.0)],
                )
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.78, 20.0)],
                asks=[OrderLevel(0.81, 5.0)],
            )

    market = RawMarket(
        market_id="m-complement",
        question="Will the highest temperature in Seoul be 29C on July 22?",
        slug="seoul-high-complement",
        active=True,
        closed=False,
        yes_token_id="yes-token",
        no_token_id="no-token",
    )

    books, error = runner_module._fetch_books(
        market,
        ComplementaryClient(),
        allowed_sides={"NO"},
    )

    assert error is None
    assert set(books) == {"NO"}
    assert [(level.price, level.size) for level in books["NO"].asks] == [
        (0.80, 10.0),
        (0.81, 5.0),
    ]
    assert books["NO"].best_bid == pytest.approx(0.79)
    assert books["NO"].raw["complementary_liquidity"]["ask_routes"] == [
        "complement:YES_bid",
        "direct:NO_ask",
    ]


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


def test_station_refresh_can_defer_signal_invalidation_until_evaluator_is_ready():
    market = RawMarket(
        "seoul-high-29",
        "Will the highest temperature in Seoul be 29C on July 8?",
        "seoul-high-29",
        True,
        False,
        "seoul-yes",
        "seoul-no",
        event_id="seoul-high-event",
    )
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={"seoul-no": "seoul-high-event"},
        evaluator=lambda _tokens: None,
    )
    refreshed_at = {market.market_id: datetime(2026, 7, 8, 3, 0, tzinfo=timezone.utc)}
    pending_invalidations: SimpleQueue[str] = SimpleQueue()

    accepted = runner_module._enqueue_station_refresh_high_exact_no_probes(
        worker,
        [market],
        refreshed_at,
        pending_signal_invalidations=pending_invalidations,
        station_ids={runner_module.TRADING_READY_STATION_MAP["seoul"].station_id},
    )

    assert accepted == 1
    assert market.market_id in refreshed_at
    runner_module._drain_pending_signal_invalidations(
        pending_invalidations,
        refreshed_at,
    )
    assert market.market_id not in refreshed_at
    assert worker.status_snapshot()["urgent_queue_depth"] == 1


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
    fast_shadow = runner_module._station_observation_state_key(
        replace(
            observation,
            fast_shadow_state_key="RKSI|2026-07-08T07:30:00+00:00|31|m",
        )
    )
    precise_source = runner_module._station_observation_state_key(
        replace(observation, source="kma-official-public-metars")
    )

    assert current != overdue
    assert current != unavailable
    assert current != fast_shadow
    assert current != precise_source


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
        "high-29-yes": "seoul-high-event",
        "high-29-no": "seoul-high-event",
        "high-30-yes": "seoul-high-event",
        "high-30-no": "seoul-high-event",
        "low-22-yes": "seoul-low-event",
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

    assert trigger_tokens == {
        "upper-tail-yes": "kuala-lumpur-high-event",
        "upper-tail-no": "kuala-lumpur-high-event",
    }


def test_realtime_price_watch_ignores_ineligible_signals_but_keeps_strong_no_and_held():
    waiting = RawMarket(
        "waiting-market",
        "Will the highest temperature in Seoul be 29C on July 8?",
        "waiting-market",
        True,
        False,
        "waiting-yes",
        "waiting-no",
    )
    strong_no = RawMarket(
        "strong-no-market",
        "Will the highest temperature in London be 25C on July 8?",
        "strong-no-market",
        True,
        False,
        "strong-no-yes",
        "strong-no-no",
    )
    strong_yes = RawMarket(
        "strong-yes-market",
        "Will the highest temperature in Toronto be 28C on July 8?",
        "strong-yes-market",
        True,
        False,
        "strong-yes-yes",
        "strong-yes-no",
    )
    settings = Settings(
        state_path=":memory:",
        trades_csv_path=":memory:",
        decisions_csv_path=":memory:",
        raw_snapshots_path=":memory:",
        no_only_new_entries=True,
    )
    broker = runner_module.PaperBroker(settings)
    broker.state.positions = [
        PaperPosition(
            position_id="held",
            market_id="held-market",
            question="Will the highest temperature in Jeddah be 38C on July 8?",
            token_id="held-yes",
            side="YES",
            entry_price=0.40,
            shares=10,
            cost_usd=4,
            opened_at=datetime.now(timezone.utc).isoformat(),
            metadata={},
        )
    ]
    signals = {
        waiting.market_id: WeatherSignal(
            0.50,
            0.0,
            "official-station-formation-window",
            "waiting",
            parse_weather_question(waiting.question),
        ),
        strong_no.market_id: WeatherSignal(
            0.05,
            0.9,
            "official-station-residual-high-no",
            "strong no",
            parse_weather_question(strong_no.question),
        ),
        strong_yes.market_id: WeatherSignal(
            0.95,
            0.9,
            "official-station-residual-high-yes",
            "strong yes",
            parse_weather_question(strong_yes.question),
        ),
    }

    watched = runner_module._realtime_price_watch_token_ids(
        [waiting, strong_no, strong_yes],
        broker,
        signals,
        settings,
    )

    assert watched == {"strong-no-yes", "strong-no-no", "held-yes"}


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


def test_realtime_no_only_evaluation_reads_yes_book_for_complementary_no_depth(tmp_path):
    question = "Will the highest temperature in Seoul be 27C today?"
    market = RawMarket(
        "seoul-no-only",
        question,
        "seoul-no-only",
        True,
        False,
        "yes-token",
        "no-token",
        event_id="seoul-today",
    )

    class RecordingClient:
        def __init__(self) -> None:
            self.book_calls: list[str] = []

        def get_order_book(self, token_id: str) -> OrderBook:
            self.book_calls.append(token_id)
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.45, 100.0)],
                asks=[OrderLevel(0.50, 100.0)],
            )

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        no_only_new_entries=True,
        min_net_edge=0.99,
    )
    broker = runner_module.PaperBroker(settings)
    signal = WeatherSignal(
        0.05,
        1.0,
        "official-station-lock-test",
        "official_nowcast_lock=test",
        parse_weather_question(question),
    )
    client = RecordingClient()

    runner_module._evaluate_realtime_update(
        {"no-token"},
        client,
        broker,
        settings,
        {"yes-token": market, "no-token": market},
        {market.market_id: signal},
        {market.market_id: "temperature"},
        {},
    )

    assert client.book_calls == ["yes-token", "no-token"]


def test_realtime_update_prefetches_missing_no_book_before_evaluation(tmp_path):
    question = "Will the highest temperature in Seoul be 27C today?"
    market = RawMarket(
        "seoul-prefetch",
        question,
        "seoul-prefetch",
        True,
        False,
        "yes-token",
        "no-token",
        condition_id="condition-no",
        event_id="seoul-today",
    )

    class PrefetchClient:
        def __init__(self) -> None:
            self.books: dict[str, OrderBook] = {}
            self.candidate_prefetch_calls: list[list[str]] = []
            self.final_prefetch_calls: list[list[tuple[str, str]]] = []

        def get_candidate_order_book(self, token_id: str) -> OrderBook:
            return self.books[token_id]

        def prefetch_candidate_order_books(self, token_ids: list[str]) -> dict[str, int]:
            self.candidate_prefetch_calls.append(list(token_ids))
            for token_id in token_ids:
                self.books[token_id] = OrderBook(
                    token_id,
                    bids=[OrderLevel(0.45, 100.0)],
                    asks=[OrderLevel(0.50, 100.0)],
                )
            return {
                "requested": len(token_ids),
                "book_ready": len(token_ids),
                "failed": 0,
                "deferred": 0,
            }

        def prefetch_final_entry_checks(self, checks: list[tuple[str, str]]) -> dict[str, int]:
            self.final_prefetch_calls.append(list(checks))
            return {
                "requested": len(checks),
                "book_ready": len(checks),
                "failed": 0,
                "deferred": 0,
            }

        def refresh_order_book(self, token_id: str) -> OrderBook:
            raise AssertionError(f"serial REST fallback must not run for {token_id}")

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        no_only_new_entries=True,
        min_net_edge=0.99,
    )
    broker = runner_module.PaperBroker(settings)
    signal = WeatherSignal(
        0.05,
        1.0,
        "official-station-lock-test",
        "official_nowcast_lock=test",
        parse_weather_question(question),
    )
    client = PrefetchClient()

    breakdown = runner_module._evaluate_realtime_update(
        {"no-token"},
        client,
        broker,
        settings,
        {"yes-token": market, "no-token": market},
        {market.market_id: signal},
        {market.market_id: "temperature"},
        {},
    )

    assert client.candidate_prefetch_calls == [["yes-token", "no-token"]]
    assert client.final_prefetch_calls == [[]]
    assert breakdown["candidate_book_prefetch"] == {
        "requested": 2,
        "book_ready": 2,
        "failed": 0,
        "deferred": 0,
    }


def test_realtime_prefilter_accepts_complementary_no_ask_when_direct_no_ask_is_missing(tmp_path):
    question = "Will the highest temperature in Seoul be 27C today?"
    market = RawMarket(
        "seoul-complement-only",
        question,
        "seoul-complement-only",
        True,
        False,
        "yes-token",
        "no-token",
        condition_id="condition-no",
        event_id="seoul-today",
    )

    class ComplementOnlyClient:
        def get_candidate_order_book(self, token_id: str) -> OrderBook:
            if token_id == "yes-token":
                return OrderBook(
                    token_id,
                    bids=[OrderLevel(0.16, 100.0)],
                    asks=[OrderLevel(0.18, 100.0)],
                )
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.82, 100.0)],
                asks=[],
            )

        def prefetch_candidate_order_books(self, token_ids: list[str]) -> dict[str, int]:
            return {
                "requested": len(token_ids),
                "book_ready": len(token_ids),
                "failed": 0,
                "deferred": 0,
            }

        def prefetch_final_entry_checks(self, checks: list[tuple[str, str]]) -> dict[str, int]:
            return {"requested": 0, "book_ready": 0, "failed": 0, "deferred": 0}

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        no_only_new_entries=True,
        min_net_edge=0.99,
    )
    broker = runner_module.PaperBroker(settings)
    signal = WeatherSignal(
        0.05,
        1.0,
        "official-station-lock-test",
        "official_nowcast_lock=test",
        parse_weather_question(question),
    )

    breakdown = runner_module._evaluate_realtime_update(
        {"no-token"},
        ComplementOnlyClient(),
        broker,
        settings,
        {"yes-token": market, "no-token": market},
        {market.market_id: signal},
        {market.market_id: "temperature"},
        {},
    )

    assert breakdown["market_count"] == 1
    assert breakdown["book_unavailable_market_count"] == 0


def test_realtime_update_defers_no_ask_market_until_book_becomes_executable(tmp_path):
    question = "Will the highest temperature in Seoul be 27C today?"
    market = RawMarket(
        "seoul-no-ask",
        question,
        "seoul-no-ask",
        True,
        False,
        "yes-token",
        "no-token",
        condition_id="condition-no",
        event_id="seoul-today",
    )

    class BidOnlyClient:
        def get_candidate_order_book(self, token_id: str) -> OrderBook:
            bids = [OrderLevel(0.20, 100.0)] if token_id == "no-token" else []
            return OrderBook(token_id, bids=bids, asks=[])

        def prefetch_candidate_order_books(self, token_ids: list[str]) -> dict[str, int]:
            return {
                "requested": len(token_ids),
                "book_ready": 0,
                "failed": 0,
                "deferred": len(token_ids),
            }

        def prefetch_final_entry_checks(self, checks: list[tuple[str, str]]) -> dict[str, int]:
            return {"requested": 0, "book_ready": 0, "failed": 0, "deferred": 0}

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        no_only_new_entries=True,
    )
    broker = runner_module.PaperBroker(settings)
    wake_when_book_returns: set[str] = set()
    prefilter_skip_state: dict[str, str] = {}

    for _ in range(2):
        breakdown = runner_module._evaluate_realtime_update(
            {"no-token"},
            BidOnlyClient(),
            broker,
            settings,
            {"yes-token": market, "no-token": market},
            {},
            {market.market_id: "temperature"},
            {},
            signal_refreshed_at_by_market={},
            probability_estimator=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("signal calculation must wait for an executable NO ask")
            ),
            wake_when_book_returns=wake_when_book_returns,
            prefilter_skip_state_by_market=prefilter_skip_state,
        )

    assert wake_when_book_returns == {"yes-token", "no-token"}
    assert breakdown["market_count"] == 0
    assert breakdown["book_unavailable_market_count"] == 1
    diagnostic_rows = [
        json.loads(line)
        for line in (tmp_path / "paper_skip_diagnostics.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(diagnostic_rows) == 1
    assert diagnostic_rows[-1]["market_id"] == market.market_id
    assert diagnostic_rows[-1]["reason_code"] == "SKIP_NO_EXECUTABLE_ASK"
    assert "no executable direct or complementary ask" in diagnostic_rows[-1]["reason"]


def test_realtime_exact_no_without_depth_keeps_each_new_observation_in_ledger(tmp_path):
    question = "Will the highest temperature in Seoul be 29C on July 21?"
    market = _wunderground_exact_market(question, market_id="seoul-exact-no-book")

    class BidOnlyClient:
        def get_candidate_order_book(self, token_id: str) -> OrderBook:
            bids = [OrderLevel(0.20, 100.0)] if token_id == market.no_token_id else []
            return OrderBook(token_id, bids=bids, asks=[])

        def prefetch_candidate_order_books(self, token_ids: list[str]) -> dict[str, int]:
            return {
                "requested": len(token_ids),
                "book_ready": 0,
                "failed": 0,
                "deferred": len(token_ids),
            }

        def prefetch_final_entry_checks(self, checks: list[tuple[str, str]]) -> dict[str, int]:
            return {"requested": 0, "book_ready": 0, "failed": 0, "deferred": 0}

    settings = replace(
        _upstream_lock_settings(tmp_path),
        decisions_log_skip_enabled=False,
    )
    broker = runner_module.PaperBroker(settings)
    signals: dict[str, WeatherSignal] = {}
    refreshed_at: dict[str, datetime] = {}
    prefilter_state: dict[str, str] = {}
    observation_times = iter(
        [
            "2026-07-21T04:00:00+00:00",
            "2026-07-21T04:01:00+00:00",
        ]
    )

    def exact_signal_estimator(requested_question, **_kwargs):
        return _upstream_exact_no_signal(
            requested_question,
            nowcast={"observed_at": next(observation_times)},
        )

    def evaluate(at: datetime) -> None:
        runner_module._evaluate_realtime_update(
            {market.no_token_id or ""},
            BidOnlyClient(),
            broker,
            settings,
            {
                market.yes_token_id or "": market,
                market.no_token_id or "": market,
            },
            signals,
            {market.market_id: "temperature"},
            {},
            signal_refreshed_at_by_market=refreshed_at,
            probability_estimator=exact_signal_estimator,
            now=at,
            prefilter_skip_state_by_market=prefilter_state,
        )

    now = datetime(2026, 7, 21, 4, 0, tzinfo=timezone.utc)
    evaluate(now)
    evaluate(now)
    refreshed_at.pop(market.market_id, None)
    evaluate(now + timedelta(minutes=1))

    with Path(settings.decisions_csv_path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["reason_code"] for row in rows] == [
        "SKIP_NO_EXECUTABLE_ASK",
        "SKIP_NO_EXECUTABLE_ASK",
    ]
    assert [row["token_id"] for row in rows] == [market.no_token_id, market.no_token_id]
    assert all("empty_ask" in row["book_status_detail"] for row in rows)
    assert [row["station_observed_at"] for row in rows] == [
        "2026-07-21T04:00:00+00:00",
        "2026-07-21T04:01:00+00:00",
    ]


def test_station_refresh_poll_is_due_every_five_seconds_independent_of_provider_cache():
    settings = Settings(
        station_refresh_poll_seconds=5,
        station_nowcast_cache_ttl_seconds=60,
    )
    refreshed_at = datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc)

    assert runner_module._station_refresh_is_due(
        refreshed_at,
        settings,
        now=refreshed_at + timedelta(seconds=4, milliseconds=999),
    ) is False
    assert runner_module._station_refresh_is_due(
        refreshed_at,
        settings,
        now=refreshed_at + timedelta(seconds=5),
    ) is True


def test_realtime_update_skips_known_yes_leaning_signal_in_no_only_mode(tmp_path):
    question = "Will the highest temperature in Seoul be 27C today?"
    market = RawMarket(
        "seoul-yes-leaning",
        question,
        "seoul-yes-leaning",
        True,
        False,
        "yes-token",
        "no-token",
        condition_id="condition-no",
        event_id="seoul-today",
    )

    class CachedAskClient:
        def get_candidate_order_book(self, token_id: str) -> OrderBook:
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.49, 100.0)],
                asks=[OrderLevel(0.50, 100.0)],
            )

        def prefetch_candidate_order_books(self, token_ids: list[str]) -> dict[str, int]:
            return {"requested": 0, "book_ready": 0, "failed": 0, "deferred": 0}

        def prefetch_final_entry_checks(self, checks: list[tuple[str, str]]) -> dict[str, int]:
            return {"requested": 0, "book_ready": 0, "failed": 0, "deferred": 0}

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        no_only_new_entries=True,
        min_net_edge=0.99,
    )
    broker = runner_module.PaperBroker(settings)
    signals_by_market = {market.market_id: WeatherSignal(
        0.90,
        1.0,
        "official-station-lock-test",
        "official_nowcast_lock=test",
        parse_weather_question(question),
    )}
    prefilter_skip_state: dict[str, str] = {}

    def evaluate() -> dict[str, object]:
        return runner_module._evaluate_realtime_update(
            {"no-token"},
            CachedAskClient(),
            broker,
            settings,
            {"yes-token": market, "no-token": market},
            signals_by_market,
            {market.market_id: "temperature"},
            {},
            prefilter_skip_state_by_market=prefilter_skip_state,
        )

    breakdown = evaluate()

    assert breakdown["market_count"] == 0
    assert breakdown["signal_ineligible_market_count"] == 1
    signals_by_market[market.market_id] = WeatherSignal(
        0.05,
        1.0,
        "official-station-lock-test",
        "official_nowcast_lock=test",
        parse_weather_question(question),
    )
    evaluate()
    signals_by_market[market.market_id] = WeatherSignal(
        0.90,
        1.0,
        "official-station-lock-test",
        "official_nowcast_lock=test",
        parse_weather_question(question),
    )
    evaluate()
    diagnostic_rows = [
        json.loads(line)
        for line in (tmp_path / "paper_skip_diagnostics.jsonl").read_text(encoding="utf-8").splitlines()
        if json.loads(line)["reason_code"] == "SKIP_SIGNAL_INELIGIBLE"
    ]
    assert len(diagnostic_rows) == 2
    assert diagnostic_rows[-1]["market_id"] == market.market_id
    assert diagnostic_rows[-1]["reason_code"] == "SKIP_SIGNAL_INELIGIBLE"


def test_fast_shadow_wake_records_contemporaneous_no_book_without_trading(tmp_path):
    question = "Will the highest temperature in Seoul be 29C today?"
    market = RawMarket(
        "seoul-fast-shadow",
        question,
        "seoul-fast-shadow",
        True,
        False,
        "yes-token",
        "no-token",
        condition_id="condition-no",
        event_id="seoul-today",
    )

    class CachedAskClient:
        def get_candidate_order_book(self, token_id: str) -> OrderBook:
            if token_id == "yes-token":
                raise KeyError(token_id)
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.84, 75.0)],
                asks=[OrderLevel(0.86, 40.0), OrderLevel(0.88, 100.0)],
                timestamp="2026-07-20T04:00:01Z",
            )

        def prefetch_candidate_order_books(self, token_ids: list[str]) -> dict[str, int]:
            assert token_ids == ["yes-token"]
            return {"requested": 1, "book_ready": 0, "failed": 1, "deferred": 0}

    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        no_only_new_entries=True,
    )
    broker = runner_module.PaperBroker(settings)
    signal = WeatherSignal(
        0.5,
        0.0,
        "official-station-neutral",
        "daily history has not matched the fast row yet",
        parse_weather_question(question),
        nowcast={
            "station_id": "RKSI",
            "fast_shadow_state_key": "RKSI|2026-07-20T04:00:00+00:00|30|m",
            "fast_shadow_match_status": "pending",
            "fast_shadow_first_seen_at": "2026-07-20T04:00:01+00:00",
            "fast_shadow_observed_at": "2026-07-20T04:00:00+00:00",
            "fast_shadow_temp_c": 30.0,
        },
    )
    fast_book_state: dict[str, str] = {}

    for _ in range(2):
        runner_module._evaluate_realtime_update(
            {"no-token"},
            CachedAskClient(),
            broker,
            settings,
            {"yes-token": market, "no-token": market},
            {market.market_id: signal},
            {market.market_id: "temperature"},
            {},
            fast_shadow_book_state_by_market=fast_book_state,
        )

    snapshots = [
        json.loads(line)
        for line in (tmp_path / "raw.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    probes = [
        row for row in snapshots if row["event"] == "wunderground_fast_shadow_book"
    ]
    assert len(probes) == 1
    probe = probes[0]
    assert probe["payload"]["trade_evidence"] is False
    assert probe["payload"]["fast_shadow_state_key"].endswith("|30|m")
    assert probe["payload"]["no_order_book"]["best_ask"] == 0.86
    assert probe["payload"]["no_order_book"]["asks_top5"][0] == {
        "price": 0.86,
        "size": 40.0,
    }
    assert fast_book_state == {
        market.market_id: "RKSI|2026-07-20T04:00:00+00:00|30|m"
    }
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

        def log_decision(self, *_args, **_kwargs):
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
        strategy_mode="hybrid_observation_edge",
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


def _wunderground_exact_market(question: str, *, market_id: str = "strict-lock") -> RawMarket:
    return _entry_gate_market(
        market_id=market_id,
        question=question,
        yes_token_id=f"{market_id}-yes",
        no_token_id=f"{market_id}-no",
        rule_provenance=MarketRuleProvenance(
            market_id=market_id,
            question=question,
            resolution_source=(
                "https://www.wunderground.com/history/daily/kr/incheon/RKSI"
            ),
        ),
    )


def _direct_exact_no_signal(
    question: str,
    *,
    p_true: float = 0.0,
    nowcast_source: str = "wunderground-history-direct",
    precision: str = "verified",
    signal_source: str = "official-station-lock-strong_no",
    signal_family: str = "lock_only",
) -> WeatherSignal:
    return WeatherSignal(
        p_true,
        1.0,
        signal_source,
        "strategy_mode=lock_only; signal_family=lock_only; official_nowcast_lock=strong_no",
        parse_weather_question(question),
        nowcast={
            "station_id": "RKSI",
            "source": nowcast_source,
            "data_block_reason": "",
            "settlement_source_verified": nowcast_source == "wunderground-history-direct",
        },
        entry_size_fraction_override=0.50,
        entry_size_reason="verified exact bucket is irreversibly false",
        strategy_mode="lock_only",
        signal_family=signal_family,
        settlement_precision_confidence=precision,
    )


def _strict_lock_settings(tmp_path) -> Settings:
    return replace(
        _entry_gate_settings(tmp_path),
        strategy_mode="lock_only",
        no_only_new_entries=True,
        max_entry_spread_abs=0.20,
        max_entry_spread_pct=1.0,
        decisions_log_skip_enabled=True,
    )


def _upstream_lock_settings(tmp_path) -> Settings:
    return replace(
        _strict_lock_settings(tmp_path),
        strategy_mode="upstream_lock_paper",
        wunderground_api_key="",
    )


def _upstream_exact_no_signal(question: str, **overrides) -> WeatherSignal:
    signal = _direct_exact_no_signal(
        question,
        nowcast_source="aviationweather-metar",
        signal_family="upstream_lock_paper",
    )
    nowcast = {
        **(signal.nowcast or {}),
        "station_local_date": "2026-07-21",
        "target_date_local": "2026-07-21",
        "daily_extremes_complete": True,
        "entry_evidence_mode": "upstream_same_station_paper",
        "settlement_source_verified": False,
        "upstream_bucket_distance_c": 1.0,
        "upstream_min_bucket_distance_c": 1.0,
    }
    nowcast.update(overrides.pop("nowcast", {}))
    return replace(
        signal,
        note="strategy_mode=upstream_lock_paper; signal_family=upstream_lock_paper; official_nowcast_lock=strong_no",
        nowcast=nowcast,
        strategy_mode="upstream_lock_paper",
        signal_family="upstream_lock_paper",
        entry_size_fraction_override=0.10,
        raw_probability=0.0,
        conservative_yes_probability=0.0,
        conservative_no_probability=1.0,
        raw_selected_side_probability=1.0,
        selected_side_probability=1.0,
        **overrides,
    )


def test_frontier_exact_no_keeps_only_nearest_new_lock_per_high_and_low_event(tmp_path):
    high_markets = [
        _wunderground_exact_market(
            f"Will the highest temperature in Seoul be {threshold}C on July 21?",
            market_id=f"high-{threshold}",
        )
        for threshold in (28, 29, 30)
    ]
    low_markets = [
        replace(
            _wunderground_exact_market(
                f"Will the lowest temperature in Seoul be {threshold}C on July 21?",
                market_id=f"low-{threshold}",
            ),
            event_id="seoul-low-event",
        )
        for threshold in (21, 22, 23)
    ]
    high_markets = [replace(market, event_id="seoul-high-event") for market in high_markets]
    signals = {}
    for market, distance in zip(high_markets, (3.0, 2.0, 1.0), strict=True):
        signals[market.market_id] = _upstream_exact_no_signal(
            market.question,
            nowcast={"upstream_bucket_distance_c": distance},
        )
    for market, distance in zip(low_markets, (1.0, 2.0, 3.0), strict=True):
        signals[market.market_id] = _upstream_exact_no_signal(
            market.question,
            nowcast={"upstream_bucket_distance_c": distance},
        )

    selected = runner_module._frontier_exact_no_market_ids(
        high_markets + low_markets,
        signals,
        _upstream_lock_settings(tmp_path),
    )

    assert selected == {"high-30", "low-21"}

    next_date_market = replace(
        _wunderground_exact_market(
            "Will the highest temperature in Seoul be 29C on July 22?",
            market_id="high-29-next-date",
        ),
        event_id="seoul-high-event",
    )
    next_date_signal = _upstream_exact_no_signal(
        next_date_market.question,
        nowcast={
            "station_local_date": "2026-07-22",
            "target_date_local": "2026-07-22",
            "upstream_bucket_distance_c": 2.0,
        },
    )

    selected = runner_module._frontier_exact_no_market_ids(
        high_markets + [next_date_market],
        {**signals, next_date_market.market_id: next_date_signal},
        _upstream_lock_settings(tmp_path),
    )

    assert selected == {"high-30", "high-29-next-date"}


def test_realtime_frontier_prefetch_keeps_nearest_new_lock_and_held_sibling(
    tmp_path,
    monkeypatch,
):
    markets = [
        replace(
            _wunderground_exact_market(
                f"Will the highest temperature in Seoul be {threshold}C on July 21?",
                market_id=f"high-{threshold}",
            ),
            event_id="seoul-high-event",
        )
        for threshold in (28, 29, 30)
    ]
    signals = {
        market.market_id: _upstream_exact_no_signal(
            market.question,
            nowcast={"upstream_bucket_distance_c": distance},
        )
        for market, distance in zip(markets, (3.0, 2.0, 1.0), strict=True)
    }

    class PrefetchRecordingClient:
        def __init__(self) -> None:
            self.books: dict[str, OrderBook] = {}
            self.candidate_prefetch_calls: list[list[str]] = []

        def get_candidate_order_book(self, token_id: str) -> OrderBook:
            return self.books[token_id]

        def get_order_book(self, token_id: str) -> OrderBook:
            return self.books[token_id]

        def prefetch_candidate_order_books(self, token_ids: list[str]) -> dict[str, int]:
            self.candidate_prefetch_calls.append(list(token_ids))
            for token_id in token_ids:
                self.books[token_id] = OrderBook(
                    token_id,
                    bids=[OrderLevel(0.10, 1000.0)],
                    asks=[OrderLevel(0.84, 1000.0)],
                )
            return {
                "requested": len(token_ids),
                "book_ready": len(token_ids),
                "failed": 0,
                "deferred": 0,
            }

        def prefetch_final_entry_checks(self, checks: list[tuple[str, str]]) -> dict[str, int]:
            return {
                "requested": len(checks),
                "book_ready": len(checks),
                "failed": 0,
                "deferred": 0,
            }

    settings = replace(_upstream_lock_settings(tmp_path), max_holding_hours=999999)
    broker = runner_module.PaperBroker(settings)
    held = markets[1]
    broker.state.positions = [
        PaperPosition(
            position_id="held-high-29",
            market_id=held.market_id,
            question=held.question,
            token_id=held.no_token_id or "",
            side="NO",
            entry_price=0.80,
            shares=10.0,
            cost_usd=8.0,
            opened_at="2026-07-21T00:00:00+00:00",
        )
    ]
    market_by_token = {
        token_id: market
        for market in markets
        for token_id in (market.yes_token_id, market.no_token_id)
        if token_id
    }
    client = PrefetchRecordingClient()
    monkeypatch.setattr(runner_module, "_apply_event_portfolio", lambda *_args, **_kwargs: None)

    breakdown = runner_module._evaluate_realtime_update(
        {market.no_token_id or "" for market in markets},
        client,
        broker,
        settings,
        market_by_token,
        signals,
        {market.market_id: "temperature" for market in markets},
        {},
        now=datetime(2026, 7, 21, 0, 1, tzinfo=timezone.utc),
    )

    assert client.candidate_prefetch_calls == [[
        held.yes_token_id,
        held.no_token_id,
        markets[2].yes_token_id,
        markets[2].no_token_id,
    ]]
    assert breakdown["frontier_selected_market_ids_sample"] == ["high-30"]
    assert breakdown["frontier_excluded_market_ids_sample"] == ["high-28"]

    far_only_client = PrefetchRecordingClient()
    broker.state.positions = []
    far_only_breakdown = runner_module._evaluate_realtime_update(
        {markets[0].no_token_id or ""},
        far_only_client,
        broker,
        settings,
        market_by_token,
        signals,
        {market.market_id: "temperature" for market in markets},
        {},
        now=datetime(2026, 7, 21, 0, 1, tzinfo=timezone.utc),
    )

    assert far_only_client.candidate_prefetch_calls == [[]]
    assert far_only_breakdown["frontier_selected_market_ids_sample"] == ["high-30"]
    assert far_only_breakdown["frontier_excluded_market_ids_sample"] == ["high-28", "high-29"]


def test_realtime_final_prefetch_includes_direct_no_and_complementary_yes(
    tmp_path,
    monkeypatch,
):
    question = "Will the highest temperature in Seoul be 29C on July 21?"
    market = _wunderground_exact_market(
        question,
        market_id="upstream-prefetch-complement",
    )

    class PrefetchRecordingClient:
        def __init__(self) -> None:
            self.final_checks: list[list[tuple[str, str]]] = []

        def get_candidate_order_book(self, token_id: str) -> OrderBook:
            if token_id == market.yes_token_id:
                return OrderBook(
                    token_id,
                    bids=[OrderLevel(0.10, 1000.0)],
                    asks=[OrderLevel(0.12, 1000.0)],
                )
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.83, 1000.0)],
                asks=[OrderLevel(0.84, 1000.0)],
            )

        def prefetch_candidate_order_books(self, token_ids: list[str]) -> dict[str, int]:
            return {
                "requested": len(token_ids),
                "book_ready": len(token_ids),
                "failed": 0,
                "deferred": 0,
            }

        def prefetch_final_entry_checks(self, checks: list[tuple[str, str]]) -> dict[str, int]:
            self.final_checks.append(list(checks))
            return {
                "requested": len(checks),
                "book_ready": len(checks),
                "failed": 0,
                "deferred": 0,
            }

    client = PrefetchRecordingClient()
    settings = _upstream_lock_settings(tmp_path)
    broker = runner_module.PaperBroker(settings)
    monkeypatch.setattr(runner_module, "_apply_event_portfolio", lambda *_args, **_kwargs: None)

    runner_module._evaluate_realtime_update(
        {market.no_token_id or ""},
        client,
        broker,
        settings,
        {
            market.yes_token_id or "": market,
            market.no_token_id or "": market,
        },
        {market.market_id: _upstream_exact_no_signal(question)},
        {market.market_id: "temperature"},
        {},
    )

    assert client.final_checks == [[
        (market.condition_id, market.no_token_id),
        (market.condition_id, market.yes_token_id),
    ]]


def test_recent_direct_observation_can_reuse_same_fresh_response_for_final_check():
    now = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)
    signal = _direct_exact_no_signal(
        "Will the highest temperature in Seoul be 28째C today?"
    )
    signal = replace(
        signal,
        nowcast={
            **signal.nowcast,
            "bot_received_at": (now - timedelta(seconds=5)).isoformat(),
        },
    )

    assert runner_module._has_recent_direct_observation(signal, now=now) is True
    assert (
        runner_module._has_recent_direct_observation(
            signal,
            now=now + timedelta(milliseconds=1),
        )
        is False
    )


@pytest.mark.parametrize(
    "question",
    [
        "Will the highest temperature in Seoul be 28°C today?",
        "Will the lowest temperature in Seoul be 21°C today?",
    ],
)
def test_lock_only_direct_exact_no_is_symmetric_and_can_size_to_full_bankroll(
    tmp_path,
    question,
):
    market = _wunderground_exact_market(question)
    signal = _direct_exact_no_signal(question)
    no_book = OrderBook(
        market.no_token_id or "",
        bids=[OrderLevel(0.89, 2000.0)],
        asks=[OrderLevel(0.90, 2000.0)],
    )
    client = _AbnormalPriceClient(
        OrderBook(market.yes_token_id or "", bids=[], asks=[]),
        no_book,
    )
    client.books = {
        market.yes_token_id or "": client.books["yes"],
        market.no_token_id or "": no_book,
    }

    result, per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        _strict_lock_settings(tmp_path),
        200.0,
        "temperature",
        allowed_sides={"NO"},
    )

    assert result.side == "NO", result.reason
    assert per_side["NO"].p_exec == pytest.approx(0.90)
    assert per_side["NO"].size_usd == pytest.approx(200.0)
    assert per_side["NO"].event_cap_override_fraction == pytest.approx(1.0)


@pytest.mark.parametrize(
    "question",
    [
        "Will the highest temperature in Seoul be 29°C on July 21?",
        "Will the lowest temperature in Seoul be 22°C on July 21?",
    ],
)
def test_upstream_lock_paper_exact_no_is_symmetric_and_capped_at_ninety_two(
    tmp_path,
    question,
):
    market = _wunderground_exact_market(question, market_id="upstream-lock")
    signal = _upstream_exact_no_signal(question)
    no_book = OrderBook(
        market.no_token_id or "",
        bids=[OrderLevel(0.91, 2000.0)],
        asks=[OrderLevel(0.92, 2000.0)],
    )
    client = _AbnormalPriceClient(
        OrderBook(market.yes_token_id or "", bids=[], asks=[]),
        no_book,
    )
    client.books = {
        market.yes_token_id or "": client.books["yes"],
        market.no_token_id or "": no_book,
    }

    result, per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        _upstream_lock_settings(tmp_path),
        200.0,
        "temperature",
        allowed_sides={"NO"},
    )

    assert result.side == "NO", result.reason
    assert per_side["NO"].p_exec == pytest.approx(0.92)
    assert per_side["NO"].size_usd == pytest.approx(20.0)
    assert per_side["NO"].signal_family == "upstream_lock_paper"
    assert per_side["NO"].event_cap_override_fraction is None


@pytest.mark.parametrize(
    ("nowcast_overrides", "allowed"),
    [
        (
            {
                "source": "kma-official-public-metars",
                "upstream_bucket_distance_c": 1.0,
                "upstream_min_bucket_distance_c": 1.0,
            },
            True,
        ),
        ({"upstream_min_bucket_distance_c": 1.0}, True),
        (
            {
                "source": "kma-official-public-metars",
                "station_id": "RKPK",
                "upstream_bucket_distance_c": 1.0,
                "upstream_min_bucket_distance_c": 1.0,
            },
            False,
        ),
        (
            {
                "source": "kma-official-public-metars",
                "upstream_bucket_distance_c": 0.1,
                "upstream_min_bucket_distance_c": 1.0,
            },
            False,
        ),
    ],
)
def test_upstream_lock_paper_runner_recomputes_source_specific_distance(
    nowcast_overrides,
    allowed,
):
    signal = _upstream_exact_no_signal(
        "Will the highest temperature in Seoul be 29C on July 21?",
        nowcast=nowcast_overrides,
    )

    assert runner_module._is_upstream_lock_paper_exact_no("NO", signal) is allowed


def test_precise_kma_one_c_exact_no_is_labeled_separately(tmp_path):
    question = "Will the highest temperature in Seoul be 29C on July 21?"
    market = _wunderground_exact_market(question, market_id="upstream-kma-one-c")
    signal = _upstream_exact_no_signal(
        question,
        nowcast={
            "source": "kma-official-public-metars",
            "upstream_bucket_distance_c": 1.0,
            "upstream_min_bucket_distance_c": 1.0,
        },
    )
    no_book = OrderBook(
        market.no_token_id or "",
        bids=[OrderLevel(0.84, 2000.0)],
        asks=[OrderLevel(0.85, 2000.0)],
    )
    client = _AbnormalPriceClient(
        OrderBook(market.yes_token_id or "", bids=[], asks=[]),
        no_book,
    )
    client.books = {
        market.yes_token_id or "": client.books["yes"],
        market.no_token_id or "": no_book,
    }

    result, per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        _upstream_lock_settings(tmp_path),
        1000.0,
        "temperature",
        allowed_sides={"NO"},
    )

    assert result.side == "NO"
    assert per_side["NO"].probability_tier == "upstream_1c_exact_no"


def test_upstream_lock_paper_prices_the_single_market_budget(tmp_path):
    question = "Will the highest temperature in Seoul be 29C on July 21?"
    market = _wunderground_exact_market(question, market_id="upstream-city-budget-vwap")
    signal = _upstream_exact_no_signal(question)
    no_book = OrderBook(
        market.no_token_id or "",
        bids=[OrderLevel(0.83, 2000.0)],
        asks=[OrderLevel(0.84, 60.0), OrderLevel(0.90, 2000.0)],
    )
    client = _AbnormalPriceClient(
        OrderBook(market.yes_token_id or "", bids=[], asks=[]),
        no_book,
    )
    client.books = {
        market.yes_token_id or "": client.books["yes"],
        market.no_token_id or "": no_book,
    }

    result, per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        _upstream_lock_settings(tmp_path),
        1000.0,
        "temperature",
        allowed_sides={"NO"},
    )

    assert result.side == "NO"
    assert per_side["NO"].p_exec == pytest.approx(0.8687258687)
    assert per_side["NO"].size_usd == pytest.approx(100.0)


def test_upstream_lock_paper_keeps_smaller_fill_when_full_city_budget_breaks_cap(
    tmp_path,
):
    question = "Will the highest temperature in Seoul be 29C on July 21?"
    market = _wunderground_exact_market(question, market_id="upstream-smaller-safe-fill")
    signal = _upstream_exact_no_signal(question)
    no_book = OrderBook(
        market.no_token_id or "",
        bids=[OrderLevel(0.83, 2000.0)],
        asks=[OrderLevel(0.84, 30.0), OrderLevel(1.00, 2000.0)],
    )
    client = _AbnormalPriceClient(
        OrderBook(market.yes_token_id or "", bids=[], asks=[]),
        no_book,
    )
    client.books = {
        market.yes_token_id or "": client.books["yes"],
        market.no_token_id or "": no_book,
    }

    result, per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        _upstream_lock_settings(tmp_path),
        1000.0,
        "temperature",
        allowed_sides={"NO"},
    )

    assert result.side == "NO"
    assert per_side["NO"].requested_size_usd == pytest.approx(100.0)
    assert 10.0 <= per_side["NO"].size_usd < 100.0
    assert per_side["NO"].p_exec <= 0.92 + 1e-12
    assert "price_capped_fill=" in per_side["NO"].reason


def test_price_cap_budget_scans_depth_once_and_keeps_fee_adjusted_boundary(monkeypatch):
    book = OrderBook(
        "no",
        bids=[],
        asks=[OrderLevel(0.80, 20.0), OrderLevel(0.92, 1000.0)],
    )
    real_executable_buy_price = runner_module.executable_buy_price
    executable_price_calls = 0

    def counted_executable_buy_price(*args, **kwargs):
        nonlocal executable_price_calls
        executable_price_calls += 1
        return real_executable_buy_price(*args, **kwargs)

    monkeypatch.setattr(
        runner_module,
        "executable_buy_price",
        counted_executable_buy_price,
    )

    budget = runner_module._max_executable_budget_at_price_cap(
        book,
        requested_size_usd=50.0,
        minimum_size_usd=10.0,
        price_cap=0.85,
        fee_rate=0.02,
    )

    safe_expensive_shares = (0.85 * 20.0 - 0.80 * 20.0) / (0.92 - 0.85)
    safe_shares = 20.0 + safe_expensive_shares
    expected_budget = safe_shares * (
        0.85 + runner_module.polymarket_taker_fee_per_share(0.85, 0.02)
    )
    assert budget == pytest.approx(expected_budget)
    assert executable_price_calls <= 1

    p_exec, shares, _slippage = real_executable_buy_price(book, budget, fee_rate=0.02)
    assert shares > 0
    assert p_exec == pytest.approx(0.85, abs=1e-12)
    too_large_p_exec, _shares, _slippage = real_executable_buy_price(
        book,
        budget + 0.01,
        fee_rate=0.02,
    )
    assert too_large_p_exec is not None
    assert too_large_p_exec > 0.85
    assert runner_module._max_executable_budget_at_price_cap(
        book,
        requested_size_usd=50.0,
        minimum_size_usd=30.0,
        price_cap=0.85,
        fee_rate=0.02,
    ) is None


def test_upstream_lock_paper_skip_above_cap_keeps_orderbook_depth_for_replay(tmp_path):
    question = "Will the highest temperature in Seoul be 29C on July 21?"
    market = _wunderground_exact_market(question, market_id="upstream-price-audit")
    signal = _upstream_exact_no_signal(question)
    no_book = OrderBook(
        market.no_token_id or "",
        bids=[OrderLevel(0.92, 100.0)],
        asks=[OrderLevel(0.93, 12.0), OrderLevel(0.94, 20.0)],
    )
    client = _AbnormalPriceClient(
        OrderBook(market.yes_token_id or "", bids=[], asks=[]),
        no_book,
    )
    client.books = {
        market.yes_token_id or "": client.books["yes"],
        market.no_token_id or "": no_book,
    }

    result, per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        _upstream_lock_settings(tmp_path),
        200.0,
        "temperature",
        allowed_sides={"NO"},
    )

    assert result.side == "SKIP"
    assert "SKIP_ENTRY_PRICE_TOO_HIGH" in per_side["NO"].reason
    depth = json.loads(per_side["NO"].entry_ask_depth_top5_json)
    assert depth["levels"][0]["price"] == pytest.approx(0.93)
    assert depth["levels"][0]["size"] == pytest.approx(12.0)


def test_lock_only_direct_low_exact_no_opens_through_portfolio_and_final_check(tmp_path):
    question = "Will the lowest temperature in Seoul be 21°C today?"
    market = _wunderground_exact_market(question, market_id="strict-low-e2e")
    signal = _direct_exact_no_signal(question)
    no_book = OrderBook(
        market.no_token_id or "",
        bids=[OrderLevel(0.84, 2000.0)],
        asks=[OrderLevel(0.85, 2000.0)],
    )
    client = _AbnormalPriceClient(
        OrderBook(market.yes_token_id or "", bids=[], asks=[]),
        no_book,
    )
    client.books = {
        market.yes_token_id or "": client.books["yes"],
        market.no_token_id or "": no_book,
    }
    settings = _strict_lock_settings(tmp_path)
    broker = runner_module.PaperBroker(settings)
    result, per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        settings,
        200.0,
        "temperature",
        allowed_sides={"NO"},
    )

    decision = runner_module._apply_event_portfolio(
        broker,
        runner_module._event_portfolio_candidates(
            market,
            signal,
            result,
            per_side,
            "temperature",
        ),
        runner_module.EntryBankrollSnapshot(True, 200.0, 200.0, 200.0, "fixture"),
        client=client,
    )

    assert [(leg.market.market_id, leg.result.side) for leg in decision.selected] == [
        (market.market_id, "NO")
    ]
    assert [(position.market_id, position.side) for position in broker.state.positions] == [
        (market.market_id, "NO")
    ]
    assert broker.state.positions[0].cost_usd == pytest.approx(200.0)
    assert broker.state.positions[0].metadata["probability_tier"] == "lock_exact_no"
    assert broker.state.cash_usd == pytest.approx(settings.bankroll_usd - 200.0)


@pytest.mark.parametrize(
    "question",
    [
        "Will the highest temperature in Seoul be 28°C today?",
        "Will the lowest temperature in Seoul be 21°C today?",
    ],
)
def test_lock_only_direct_exact_no_blocks_execution_vwap_above_ninety_cents(
    tmp_path,
    question,
):
    market = _wunderground_exact_market(question)
    signal = _direct_exact_no_signal(question)
    no_book = OrderBook(
        market.no_token_id or "",
        bids=[OrderLevel(0.89, 2000.0)],
        asks=[OrderLevel(0.9001, 2000.0)],
    )
    client = _AbnormalPriceClient(
        OrderBook(market.yes_token_id or "", bids=[], asks=[]),
        no_book,
    )
    client.books = {
        market.yes_token_id or "": client.books["yes"],
        market.no_token_id or "": no_book,
    }

    result, per_side = runner_module.evaluate_market(
        market,
        signal,
        client,
        _strict_lock_settings(tmp_path),
        200.0,
        "temperature",
        allowed_sides={"NO"},
    )

    assert result.side == "SKIP"
    assert per_side["NO"].side == "SKIP"
    assert "SKIP_ENTRY_PRICE_TOO_HIGH" in per_side["NO"].reason
    assert "max_entry_price=0.9000" in per_side["NO"].reason


@pytest.mark.parametrize(
    ("question", "side", "signal_overrides", "market_has_wunderground", "blocked_detail"),
    [
        (
            "Will the highest temperature in Seoul be 27°C or below today?",
            "NO",
            {},
            True,
            "exact_temperature_bucket_required",
        ),
        (
            "Will the highest temperature in Seoul be 29°C or higher today?",
            "NO",
            {},
            True,
            "exact_temperature_bucket_required",
        ),
        (
            "Will the highest temperature in Seoul be 28°C today?",
            "NO",
            {"p_true": 0.10},
            True,
            "irreversible_no_probability_required",
        ),
        (
            "Will the highest temperature in Seoul be 28°C today?",
            "NO",
            {"p_true": -1e-13},
            True,
            "irreversible_no_probability_required",
        ),
        (
            "Will the highest temperature in Seoul be 28°C today?",
            "NO",
            {"signal_family": "intraday_observation_edge"},
            True,
            "lock_only_signal_family_required",
        ),
        (
            "Will the highest temperature in Seoul be 28°C today?",
            "YES",
            {},
            True,
            "no_side_required",
        ),
        (
            "Will the highest temperature in Seoul be 28°C today?",
            "NO",
            {"nowcast_source": "aviationweather-metar"},
            True,
            "wunderground_direct_nowcast_required",
        ),
        (
            "Will the lowest temperature in Seoul be 21°C today?",
            "NO",
            {"precision": "needs_audit"},
            True,
            "verified_precision_required",
        ),
        (
            "Will the lowest temperature in Seoul be 21°C today?",
            "NO",
            {},
            False,
            "wunderground_settlement_source_required",
        ),
    ],
)
def test_lock_only_new_entry_gate_rejects_every_non_strict_candidate(
    question,
    side,
    signal_overrides,
    market_has_wunderground,
    blocked_detail,
):
    market = _wunderground_exact_market(question)
    if not market_has_wunderground:
        market = replace(market, rule_provenance=None, raw=None)
    signal = _direct_exact_no_signal(question, **signal_overrides)
    result = runner_module.EdgeResult(
        side,
        signal.p_true,
        0.50,
        0.40,
        10.0,
        20.0,
        "candidate",
    )
    candidate = runner_module.PortfolioCandidate(
        market,
        signal,
        result,
        "temperature",
    )

    reason = runner_module._lock_only_exact_no_entry_skip_reason(
        market,
        signal,
        side,
    )
    filtered = runner_module._new_entry_candidates_for_strategy(
        [candidate],
        Settings(strategy_mode="lock_only", no_only_new_entries=False),
    )

    assert reason is not None
    assert "SKIP_LOCK_ONLY_EXACT_NO_REQUIRED" in reason
    assert blocked_detail in reason
    assert filtered == []


def test_lock_only_gate_fails_closed_when_signal_belongs_to_another_exact_bucket():
    market_question = "Will the highest temperature in Seoul be 29°C today?"
    signal_question = "Will the highest temperature in Seoul be 28°C today?"
    market = _wunderground_exact_market(market_question)
    signal = _direct_exact_no_signal(signal_question)

    reason = runner_module._lock_only_exact_no_entry_skip_reason(
        market,
        signal,
        "NO",
    )

    assert reason is not None
    assert "blocked_reason=signal_market_bucket_mismatch" in reason


def test_lock_only_strategy_rejection_is_written_to_decision_ledger(tmp_path):
    question = "Will the highest temperature in Seoul be 27°C or below today?"
    market = _wunderground_exact_market(question, market_id="tail-blocked")
    signal = _direct_exact_no_signal(question)
    candidate = runner_module.PortfolioCandidate(
        market,
        signal,
        runner_module.EdgeResult(
            "NO",
            0.0,
            0.50,
            0.50,
            10.0,
            20.0,
            "candidate",
        ),
        "temperature",
    )
    settings = _strict_lock_settings(tmp_path)
    broker = runner_module.PaperBroker(settings)

    decision = runner_module._apply_event_portfolio(
        broker,
        [candidate],
        runner_module.EntryBankrollSnapshot(True, 1000.0, 1000.0, 1000.0, "fixture"),
    )

    assert decision.selected == []
    rows = list(csv.DictReader(Path(settings.decisions_csv_path).open(encoding="utf-8")))
    assert rows[-1]["side"] == "SKIP"
    assert rows[-1]["reason_code"] == "SKIP_LOCK_ONLY_EXACT_NO_REQUIRED"
    assert "blocked_reason=exact_temperature_bucket_required" in rows[-1]["reason"]


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

    assert client.refresh_calls == ["no", "yes"]
    assert result.side == "NO"
    assert result.p_exec == pytest.approx(0.88)
    assert "final_book_source=rest_helper" in result.reason


def test_final_pre_trade_uses_fresh_complementary_yes_bid_for_no_entry(tmp_path):
    question = "Will the highest temperature in Seoul be 29C on July 21?"
    market = _wunderground_exact_market(
        question,
        market_id="upstream-final-complement",
    )
    signal = _upstream_exact_no_signal(question)
    selected = runner_module.EdgeResult(
        "NO",
        0.0,
        0.84,
        0.10,
        10.0,
        11.9,
        "selected upstream exact no",
        signal_family="upstream_lock_paper",
        requested_size_usd=10.0,
        executable_size_usd=10.0,
    )

    class ComplementRefreshClient(_FinalGateClient):
        def __init__(self) -> None:
            super().__init__()
            self.refresh_calls: list[str] = []

        def refresh_order_book(self, token_id: str) -> OrderBook:
            self.refresh_calls.append(token_id)
            if token_id == market.yes_token_id:
                return OrderBook(
                    token_id,
                    bids=[OrderLevel(0.16, 100.0)],
                    asks=[OrderLevel(0.18, 100.0)],
                )
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.82, 100.0)],
                asks=[],
            )

    client = ComplementRefreshClient()
    result = runner_module._final_pre_trade_entry_result(
        market,
        signal,
        selected,
        market.no_token_id or "",
        client,
        _upstream_lock_settings(tmp_path),
        "temperature",
    )

    assert client.refresh_calls == [market.no_token_id, market.yes_token_id]
    assert result.side == "NO"
    assert result.p_exec == pytest.approx(0.84)
    depth = json.loads(result.entry_ask_depth_top5_json)
    assert depth["levels"][0]["route"] == "complement:YES_bid"
    assert depth["routes"] == ["complement:YES_bid"]
    assert "final_book_source=rest_helper:complement" in result.reason


def test_final_pre_trade_direct_exact_no_shrinks_to_safe_fresh_book_budget(tmp_path):
    question = "Will the lowest temperature in Seoul be 21°C today?"
    market = _wunderground_exact_market(question, market_id="seoul-low-21c")
    signal = _direct_exact_no_signal(question)
    selected = runner_module.EdgeResult(
        "NO",
        0.0,
        0.89,
        0.11,
        200.0,
        224.0,
        "selected direct exact no",
        signal_family="lock_only",
        probability_tier="lock_high_exact_no",
        event_cap_override_fraction=1.0,
        requested_size_usd=200.0,
        executable_size_usd=200.0,
    )

    class ShallowerFreshBookClient(_FinalGateClient):
        def __init__(self) -> None:
            super().__init__()
            self.refresh_calls: list[str] = []

        def refresh_order_book(self, token_id: str) -> OrderBook:
            self.refresh_calls.append(token_id)
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.87, 1000.0)],
                asks=[
                    OrderLevel(0.88, 50.0),
                    OrderLevel(0.95, 1000.0),
                ],
            )

    client = ShallowerFreshBookClient()
    result = runner_module._final_pre_trade_entry_result(
        market,
        signal,
        selected,
        market.no_token_id or "",
        client,
        _strict_lock_settings(tmp_path),
        "temperature",
    )

    assert client.refresh_calls == [market.no_token_id, market.yes_token_id]
    assert result.side == "NO"
    assert 62.0 < result.size_usd < 64.0
    assert result.size_usd < selected.size_usd
    assert result.executable_size_usd == pytest.approx(result.size_usd)
    assert result.requested_size_usd == pytest.approx(200.0)
    assert result.p_exec == pytest.approx(0.90, abs=1e-9)
    assert "final_size_reduced=" in result.reason


def test_final_pre_trade_upstream_exact_no_keeps_smaller_fresh_book_fill(tmp_path):
    question = "Will the highest temperature in Seoul be 29C on July 21?"
    market = _wunderground_exact_market(question, market_id="upstream-final-smaller-fill")
    signal = _upstream_exact_no_signal(question)
    selected = runner_module.EdgeResult(
        "NO",
        0.0,
        0.84,
        0.10,
        50.0,
        59.0,
        "selected upstream exact no",
        signal_family="upstream_lock_paper",
        probability_tier="upstream_2c_exact_no",
        requested_size_usd=50.0,
        executable_size_usd=50.0,
    )

    class ShallowerFreshBookClient(_FinalGateClient):
        def refresh_order_book(self, token_id: str) -> OrderBook:
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.83, 1000.0)],
                asks=[OrderLevel(0.84, 25.0), OrderLevel(1.00, 1000.0)],
            )

    result = runner_module._final_pre_trade_entry_result(
        market,
        signal,
        selected,
        market.no_token_id or "",
        ShallowerFreshBookClient(),
        _upstream_lock_settings(tmp_path),
        "temperature",
    )

    assert result.side == "NO"
    assert 10.0 <= result.size_usd < selected.size_usd
    assert result.p_exec <= 0.92 + 1e-12
    assert "final_size_reduced=" in result.reason


def test_final_upstream_exact_no_rejection_is_kept_in_decision_ledger(tmp_path):
    question = "Will the highest temperature in Seoul be 29C on July 21?"
    settings = replace(
        _upstream_lock_settings(tmp_path),
        decisions_log_skip_enabled=False,
    )
    broker = runner_module.PaperBroker(settings)
    market = _wunderground_exact_market(
        question,
        market_id="upstream-final-rejection-audit",
    )
    signal = _upstream_exact_no_signal(
        question,
        nowcast={"observed_at": "2026-07-21T04:00:00+00:00"},
    )
    selected = runner_module.EdgeResult(
        "NO",
        0.0,
        0.84,
        0.10,
        50.0,
        59.0,
        "selected upstream exact no",
        strategy_mode="upstream_lock_paper",
        signal_family="upstream_lock_paper",
        probability_tier="upstream_2c_exact_no",
        conservative_yes_probability=0.0,
        conservative_no_probability=1.0,
        requested_size_usd=50.0,
        executable_size_usd=50.0,
    )

    class TooExpensiveFreshBookClient(_FinalGateClient):
        def get_order_book(self, token_id: str) -> OrderBook:
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.92, 1000.0)],
                asks=[OrderLevel(0.93, 1000.0)],
            )

    result = runner_module._open_position_if_needed(
        broker,
        market,
        signal,
        selected,
        "temperature",
        client=TooExpensiveFreshBookClient(),
    )

    assert result is not None
    assert result.side == "SKIP"
    assert "SKIP_FINAL_UPSTREAM_BUDGET" in result.reason
    with Path(settings.decisions_csv_path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["market_id"] == market.market_id
    assert rows[0]["reason_code"] == "SKIP_FINAL_UPSTREAM_BUDGET"
    assert rows[0]["station_observed_at"] == "2026-07-21T04:00:00+00:00"


def test_final_station_recheck_failure_keeps_original_exact_no_audit_once(tmp_path):
    question = "Will the highest temperature in Seoul be 29C on July 21?"
    settings = replace(
        _upstream_lock_settings(tmp_path),
        decisions_log_skip_enabled=False,
    )
    market = _wunderground_exact_market(
        question,
        market_id="upstream-final-station-recheck-audit",
    )
    signal = _upstream_exact_no_signal(
        question,
        nowcast={"observed_at": "2026-07-21T04:00:00+00:00"},
    )
    selected = runner_module.EdgeResult(
        "NO",
        0.0,
        0.84,
        0.10,
        50.0,
        59.0,
        "selected upstream exact no",
        strategy_mode="upstream_lock_paper",
        signal_family="upstream_lock_paper",
        probability_tier="upstream_2c_exact_no",
        conservative_yes_probability=0.0,
        conservative_no_probability=1.0,
        requested_size_usd=50.0,
        executable_size_usd=50.0,
    )

    def unavailable_estimator(requested_question, **_kwargs):
        return WeatherSignal(
            0.5,
            0.0,
            "official-station-unavailable",
            "fresh official request failed",
            parse_weather_question(requested_question),
        )

    for _ in range(2):
        broker = runner_module.PaperBroker(settings)
        result = runner_module._open_position_if_needed(
            broker,
            market,
            signal,
            selected,
            "temperature",
            client=_FinalGateClient(),
            probability_estimator=unavailable_estimator,
            observation_provider=object(),
        )
        assert result is not None
        assert result.side == "SKIP"
        assert "SKIP_FINAL_STATION_SIGNAL" in result.reason

    with Path(settings.decisions_csv_path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["reason_code"] == "SKIP_FINAL_STATION_SIGNAL"
    assert rows[0]["station_observed_at"] == "2026-07-21T04:00:00+00:00"


def test_paper_ledger_rejection_returns_skip_and_keeps_exact_no_reason(tmp_path):
    question = "Will the highest temperature in Seoul be 29C on July 21?"
    settings = replace(
        _upstream_lock_settings(tmp_path),
        decisions_log_skip_enabled=False,
    )
    broker = runner_module.PaperBroker(settings)
    broker.state.cash_usd = 5.0
    market = _wunderground_exact_market(
        question,
        market_id="upstream-paper-ledger-rejection",
    )
    signal = _upstream_exact_no_signal(
        question,
        nowcast={
            "observed_at": "2026-07-21T04:00:00+00:00",
            "freshness_seconds": 0,
        },
    )
    selected = runner_module.EdgeResult(
        "NO",
        0.0,
        0.84,
        0.10,
        50.0,
        59.0,
        "selected upstream exact no",
        strategy_mode="upstream_lock_paper",
        signal_family="upstream_lock_paper",
        probability_tier="upstream_2c_exact_no",
        conservative_yes_probability=0.0,
        conservative_no_probability=1.0,
        requested_size_usd=50.0,
        executable_size_usd=50.0,
    )

    result = runner_module._open_position_if_needed(
        broker,
        market,
        signal,
        selected,
        "temperature",
        client=_FinalGateClient(),
    )

    assert result is not None
    assert result.side == "SKIP"
    assert result.reason.startswith("SKIP_SINGLE_MARKET_CAP")
    assert broker.state.positions == []
    with Path(settings.decisions_csv_path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["reason_code"] == "SKIP_SINGLE_MARKET_CAP"
    assert rows[0]["station_observed_at"] == "2026-07-21T04:00:00+00:00"


def test_final_pre_trade_exact_no_resizes_to_configured_return_floor(tmp_path):
    question = "Will the lowest temperature in Seoul be 21째C today?"
    market = _wunderground_exact_market(question, market_id="seoul-low-return-floor")
    signal = _direct_exact_no_signal(question)
    selected = runner_module.EdgeResult(
        "NO",
        0.0,
        0.90,
        0.10,
        200.0,
        222.0,
        "selected direct exact no",
        signal_family="lock_only",
        probability_tier="lock_exact_no",
        event_cap_override_fraction=1.0,
        requested_size_usd=200.0,
        executable_size_usd=200.0,
    )

    class LayeredBookClient(_FinalGateClient):
        def refresh_order_book(self, token_id: str) -> OrderBook:
            return OrderBook(
                token_id,
                bids=[OrderLevel(0.87, 1000.0)],
                asks=[
                    OrderLevel(0.88, 50.0),
                    OrderLevel(0.90, 1000.0),
                ],
            )

    settings = replace(
        _strict_lock_settings(tmp_path),
        entry_min_expected_net_return_pct=0.12,
    )
    result = runner_module._final_pre_trade_entry_result(
        market,
        signal,
        selected,
        market.no_token_id or "",
        LayeredBookClient(),
        settings,
        "temperature",
    )

    assert result.side == "NO"
    assert 124.0 < result.size_usd < 126.0
    assert result.p_exec == pytest.approx(1.0 / 1.12, abs=1e-9)
    assert "final_size_reduced=" in result.reason


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


def test_candidate_book_scan_refreshes_preferred_and_its_complement():
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
    assert set(books) == {"YES", "NO"}
    assert client.refresh_calls == ["no", "yes"]


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
    assert client.refresh_calls == ["no", "yes"]


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


def test_official_station_refresh_can_poll_only_selected_station_ids(monkeypatch):
    seoul = runner_module.TRADING_READY_STATION_MAP["seoul"]
    london = runner_module.TRADING_READY_STATION_MAP["london"]
    monkeypatch.setattr(
        runner_module,
        "TRADING_READY_STATION_MAP",
        {"seoul": seoul, "london": london},
    )
    calls: list[str] = []

    class FakeProvider:
        def observed_temperature_extremes_so_far(self, station, *, target_date, now):
            del target_date, now
            calls.append(station.station_id)

    runner_module._refresh_official_station_observations(
        FakeProvider(),
        now=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
        station_ids={seoul.station_id},
    )

    assert calls == [seoul.station_id]


def test_official_station_refresh_prepares_cold_start_history_before_city_fetches(
    monkeypatch,
):
    seoul = runner_module.TRADING_READY_STATION_MAP["seoul"]
    london = runner_module.TRADING_READY_STATION_MAP["london"]
    monkeypatch.setattr(
        runner_module,
        "TRADING_READY_STATION_MAP",
        {"seoul": seoul, "london": london},
    )
    prepared = threading.Event()
    prepared_station_ids: list[set[str] | None] = []

    class Provider:
        supports_parallel_station_refresh = True

        def prepare_daily_extremes(self, *, now, station_ids=None):
            assert now == datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)
            prepared_station_ids.append(set(station_ids) if station_ids is not None else None)
            prepared.set()

        def observed_temperature_extremes_so_far(self, station, *, target_date, now):
            del station, target_date, now
            assert prepared.is_set()

    runner_module._refresh_official_station_observations(
        Provider(),
        now=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
        station_ids={seoul.station_id, london.station_id},
    )

    assert prepared_station_ids == [{seoul.station_id, london.station_id}]


def test_official_station_refresh_rotates_which_city_is_submitted_first(monkeypatch):
    seoul = runner_module.TRADING_READY_STATION_MAP["seoul"]
    london = runner_module.TRADING_READY_STATION_MAP["london"]
    tokyo = runner_module.TRADING_READY_STATION_MAP["tokyo"]
    monkeypatch.setattr(
        runner_module,
        "TRADING_READY_STATION_MAP",
        {"seoul": seoul, "london": london, "tokyo": tokyo},
    )
    monkeypatch.setattr(runner_module, "_station_observation_refresh_cursor", 0)
    calls: list[str] = []

    class SequentialProvider:
        supports_parallel_station_refresh = False

        def observed_temperature_extremes_so_far(self, station, *, target_date, now):
            del target_date, now
            calls.append(station.station_id)

    now = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)
    runner_module._refresh_official_station_observations(SequentialProvider(), now=now)
    first_round = list(calls)
    calls.clear()
    runner_module._refresh_official_station_observations(SequentialProvider(), now=now)

    assert first_round == [seoul.station_id, london.station_id, tokyo.station_id]
    assert calls == [london.station_id, tokyo.station_id, seoul.station_id]


def test_official_station_health_refresh_polls_direct_sources_in_parallel(monkeypatch):
    seoul = runner_module.TRADING_READY_STATION_MAP["seoul"]
    london = runner_module.TRADING_READY_STATION_MAP["london"]
    monkeypatch.setattr(
        runner_module,
        "TRADING_READY_STATION_MAP",
        {"seoul": seoul, "london": london},
    )
    barrier = threading.Barrier(2)
    active_lock = threading.Lock()
    active = 0
    max_active = 0

    class ParallelProvider:
        supports_parallel_station_refresh = True

        def observed_temperature_extremes_so_far(self, station, *, target_date, now):
            nonlocal active, max_active
            del target_date
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            try:
                barrier.wait(timeout=2)
                return StationNowcastObservation(
                    station_id=station.station_id,
                    station_name=station.station_name,
                    observed_high_c=25.0,
                    observed_low_c=15.0,
                    observed_at=now,
                    high_observed_at=now,
                    source="wunderground-history-direct",
                    source_url="https://example.test/history",
                    settlement_source_url="https://example.test/settlement",
                    freshness_seconds=0,
                    unavailable_reason="",
                )
            finally:
                with active_lock:
                    active -= 1

    runner_module._refresh_official_station_observations(
        ParallelProvider(),
        now=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
    )

    assert max_active == 2


def test_direct_station_refresh_releases_fast_city_without_waiting_for_slow_city(monkeypatch):
    seoul = runner_module.TRADING_READY_STATION_MAP["seoul"]
    london = runner_module.TRADING_READY_STATION_MAP["london"]
    monkeypatch.setattr(
        runner_module,
        "TRADING_READY_STATION_MAP",
        {"seoul": seoul, "london": london},
    )
    slow_release = threading.Event()
    fast_released = threading.Event()
    callbacks: list[set[str]] = []
    state_by_station = {
        seoul.station_id: ("old",),
        london.station_id: ("old",),
    }

    class ParallelProvider:
        supports_parallel_station_refresh = True

        def observed_temperature_extremes_so_far(self, station, *, target_date, now):
            del target_date
            if station.station_id == london.station_id:
                assert slow_release.wait(timeout=2)
            return StationNowcastObservation(
                station_id=station.station_id,
                station_name=station.station_name,
                observed_high_c=25.0,
                observed_low_c=15.0,
                observed_at=now,
                high_observed_at=now,
                source="wunderground-history-direct",
                source_url="https://example.test/history",
                settlement_source_url="https://example.test/settlement",
                freshness_seconds=0,
                unavailable_reason="",
            )

    def on_changed(station_ids: set[str]) -> None:
        callbacks.append(set(station_ids))
        if seoul.station_id in station_ids:
            fast_released.set()

    worker = threading.Thread(
        target=runner_module._refresh_official_station_observations,
        kwargs={
            "observation_provider": ParallelProvider(),
            "now": datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            "station_state_by_id": state_by_station,
            "on_shared_metar_refreshed": on_changed,
        },
    )
    worker.start()
    try:
        assert fast_released.wait(timeout=1)
        assert callbacks[0] == {seoul.station_id}
    finally:
        slow_release.set()
        worker.join(timeout=2)

    assert worker.is_alive() is False
    assert {seoul.station_id} in callbacks
    assert {london.station_id} in callbacks


def test_initial_parallel_station_refresh_releases_each_station_for_startup_probe(monkeypatch):
    seoul = runner_module.TRADING_READY_STATION_MAP["seoul"]
    london = runner_module.TRADING_READY_STATION_MAP["london"]
    monkeypatch.setattr(
        runner_module,
        "TRADING_READY_STATION_MAP",
        {"seoul": seoul, "london": london},
    )
    markets = [
        RawMarket(
            "seoul-high-29",
            "Will the highest temperature in Seoul be 29C on July 20?",
            "seoul-high-29",
            True,
            False,
            "high-yes",
            "high-no",
            event_id="seoul-high-event",
        ),
        RawMarket(
            "seoul-low-22",
            "Will the lowest temperature in Seoul be 22C on July 20?",
            "seoul-low-22",
            True,
            False,
            "low-yes",
            "low-no",
            event_id="seoul-low-event",
        ),
    ]
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={
            "high-no": "seoul-high-event",
            "low-no": "seoul-low-event",
        },
        evaluator=lambda _tokens: None,
    )
    callbacks: list[set[str]] = []

    class ParallelProvider:
        supports_parallel_station_refresh = True

        def observed_temperature_extremes_so_far(self, station, *, target_date, now):
            del target_date
            return StationNowcastObservation(
                station_id=station.station_id,
                station_name=station.station_name,
                observed_high_c=30.0,
                observed_low_c=21.0,
                observed_at=now,
                high_observed_at=now,
                low_observed_at=now,
                source="wunderground-history-direct",
                source_url="https://example.test/history",
                settlement_source_url="https://example.test/settlement",
                freshness_seconds=0,
                unavailable_reason="",
            )

    def release_startup_probe(station_ids: set[str]) -> None:
        callbacks.append(station_ids)
        runner_module._enqueue_official_station_refresh_updates(
            worker,
            markets,
            {},
            {},
            {},
            station_ids=station_ids,
            now=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
        )

    changed = runner_module._refresh_official_station_observations(
        ParallelProvider(),
        now=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
        station_state_by_id={},
        on_shared_metar_refreshed=release_startup_probe,
    )

    assert changed == set()
    assert len(callbacks) == 2
    assert set().union(*callbacks) == {seoul.station_id, london.station_id}
    assert worker._pending_tokens_by_event == {
        "seoul-high-event": {"high-no"},
        "seoul-low-event": {"low-no"},
    }


def test_initial_sequential_station_refresh_releases_startup_probe(monkeypatch):
    seoul = runner_module.TRADING_READY_STATION_MAP["seoul"]
    monkeypatch.setattr(
        runner_module,
        "TRADING_READY_STATION_MAP",
        {"seoul": seoul},
    )
    callbacks: list[set[str]] = []

    class SequentialProvider:
        supports_parallel_station_refresh = False

        def observed_temperature_extremes_so_far(self, station, *, target_date, now):
            del target_date
            return StationNowcastObservation(
                station_id=station.station_id,
                station_name=station.station_name,
                observed_high_c=30.0,
                observed_low_c=21.0,
                observed_at=now,
                high_observed_at=now,
                low_observed_at=now,
                source="wunderground-history-direct",
                source_url="https://example.test/history",
                settlement_source_url="https://example.test/settlement",
                freshness_seconds=0,
                unavailable_reason="",
            )

    changed = runner_module._refresh_official_station_observations(
        SequentialProvider(),
        now=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
        station_state_by_id={},
        on_shared_metar_refreshed=lambda station_ids: callbacks.append(station_ids),
    )

    assert changed == set()
    assert callbacks == [{seoul.station_id}]


def test_runner_releases_only_newly_completed_station_ids(tmp_path, monkeypatch):
    market = RawMarket(
        "seoul-high-29",
        "Will the highest temperature in Seoul be 29C today?",
        "seoul-high-29",
        True,
        False,
        "yes",
        "no",
        event_id="seoul-high-event",
    )
    released: list[set[str]] = []
    refresh_station_ids: list[set[str]] = []
    evaluation_can_start = threading.Event()
    evaluation_started = threading.Event()
    allow_evaluation_to_finish = threading.Event()
    released_before_evaluation_finished: list[bool] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def discover_weather_markets(self, *, max_pages, page_size):
            del max_pages, page_size
            return [market]

        def get_market(self, market_id):
            assert market_id == market.market_id
            return market

    class RecordingEvaluator:
        def __init__(self, *, evaluator, **_kwargs):
            self.evaluator = evaluator
            self.thread = None

        def start(self):
            def run_evaluation():
                assert evaluation_can_start.wait(timeout=1)
                self.evaluator({"no"})

            self.thread = threading.Thread(
                target=run_evaluation,
                daemon=True,
            )
            self.thread.start()

        def stop(self, *, drain=True, timeout=5.0):
            del drain
            allow_evaluation_to_finish.set()
            if self.thread is not None:
                self.thread.join(timeout=timeout)

        def enqueue_tokens(self, _token_ids, *, urgent=False):
            del urgent
            return 1

        def status_snapshot(self):
            return {"thread_alive": True, "queue_depth": 0}

    class RecordingStream:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self, token_ids):
            assert set(token_ids) == {"yes", "no"}
            evaluation_can_start.set()
            assert evaluation_started.wait(timeout=1)

        def stop(self):
            return None

        def health_snapshot(self):
            return {"thread_alive": True, "stale": False, "status_reason": "fresh fixture"}

    class ProviderFactory:
        @staticmethod
        def from_settings(_settings):
            return object()

    def refresh_with_two_completions(
        _provider,
        *,
        now,
        station_state_by_id,
        on_shared_metar_refreshed,
        station_ids=None,
    ):
        del now, station_state_by_id
        refresh_station_ids.append(set(station_ids or set()))
        release_timer = threading.Timer(0.2, allow_evaluation_to_finish.set)
        release_timer.start()
        on_shared_metar_refreshed({"FIRST"})
        on_shared_metar_refreshed({"SECOND"})
        release_timer.join(timeout=1)
        return {"FIRST", "SECOND"}

    def record_release(
        _worker,
        _markets,
        _signals,
        _timer_buckets,
        _signal_refreshed,
        *,
        station_ids,
        now,
    ):
        del now
        if station_ids:
            released.append(set(station_ids))
            released_before_evaluation_finished.append(
                not allow_evaluation_to_finish.is_set()
            )

    def record_station_release(*_args, station_ids, **_kwargs):
        record_release(None, None, None, None, None, station_ids=station_ids, now=None)
        return 0

    def block_realtime_evaluation(*_args, **_kwargs):
        evaluation_started.set()
        assert allow_evaluation_to_finish.wait(timeout=2)
        return {}

    monkeypatch.setattr(runner_module, "PolymarketClient", FakeClient)
    monkeypatch.setattr(runner_module, "OrderBookMarketStream", RecordingStream)
    monkeypatch.setattr(runner_module, "RealtimeEvaluationCoalescer", RecordingEvaluator)
    monkeypatch.setattr(runner_module, "AviationWeatherMetarNowcastProvider", ProviderFactory)
    monkeypatch.setattr(runner_module, "pre_station_tradeability_gate", lambda *_args: None)
    monkeypatch.setattr(runner_module, "_select_realtime_stream_markets", lambda markets, *_args, **_kwargs: markets)
    monkeypatch.setattr(runner_module, "_load_residual_profile_store", lambda _settings: None)
    monkeypatch.setattr(runner_module, "_refresh_official_station_observations", refresh_with_two_completions)
    monkeypatch.setattr(runner_module, "_enqueue_official_station_refresh_updates", record_release)
    monkeypatch.setattr(
        runner_module,
        "_enqueue_station_refresh_high_exact_no_probes",
        record_station_release,
    )
    monkeypatch.setattr(runner_module, "_evaluate_realtime_update", block_realtime_evaluation)

    def stop_after_first_loop(_seconds):
        raise RuntimeError("stop after station release")

    monkeypatch.setattr(runner_module.time, "sleep", stop_after_first_loop)
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        strategy_mode="hybrid_observation_edge",
        stream_cycle_interval_seconds=60,
    )

    with pytest.raises(RuntimeError, match="stop after station release"):
        runner_module.run_realtime_forever(settings)

    assert released == [{"FIRST"}, {"SECOND"}]
    assert released_before_evaluation_finished == [True, True]
    assert refresh_station_ids == [
        {runner_module.TRADING_READY_STATION_MAP["seoul"].station_id}
    ]


def test_lock_only_runtime_rejects_missing_wunderground_key(tmp_path):
    settings = replace(
        _entry_gate_settings(tmp_path),
        strategy_mode="lock_only",
        wunderground_api_key="",
    )

    with pytest.raises(RuntimeError, match="WUNDERGROUND_API_KEY"):
        runner_module.run_realtime_forever(settings)


def test_upstream_lock_paper_runtime_accepts_missing_wunderground_key(tmp_path):
    settings = replace(
        _entry_gate_settings(tmp_path),
        strategy_mode="upstream_lock_paper",
        wunderground_api_key="",
    )

    runner_module._validate_runtime_strategy_dependencies(settings)


def test_station_refresh_releases_shared_metar_before_slow_single_station_source(monkeypatch):
    seoul = runner_module.TRADING_READY_STATION_MAP["seoul"]
    london = runner_module.TRADING_READY_STATION_MAP["london"]
    hong_kong = runner_module.TRADING_READY_STATION_MAP["hong kong"]
    monkeypatch.setattr(
        runner_module,
        "TRADING_READY_STATION_MAP",
        {"hong kong": hong_kong, "seoul": seoul, "london": london},
    )
    calls: list[str] = []
    released: list[set[str]] = []
    expected_metar_changes = {seoul.station_id, london.station_id}
    state_by_station = {
        seoul.station_id: ("old",),
        london.station_id: ("old",),
        hong_kong.station_id: ("old",),
    }
    markets = [
        RawMarket(
            "seoul-29",
            "Will the highest temperature in Seoul be 29C on July 18?",
            "seoul-29",
            True,
            False,
            "seoul-yes",
            "seoul-no",
            event_id="seoul-event",
        ),
        RawMarket(
            "london-24",
            "Will the highest temperature in London be 24C on July 18?",
            "london-24",
            True,
            False,
            "london-yes",
            "london-no",
            event_id="london-event",
        ),
    ]
    worker = RealtimeEvaluationCoalescer(
        event_key_by_token={
            "seoul-no": "seoul-event",
            "london-no": "london-event",
        },
        evaluator=lambda _tokens: None,
    )

    def release_metar_changes(changed_ids: set[str]) -> None:
        released.append(changed_ids)
        runner_module._enqueue_official_station_refresh_updates(
            worker,
            markets,
            {},
            {},
            {},
            station_ids=changed_ids,
            now=datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc),
        )

    class FakeProvider:
        def observed_temperature_extremes_so_far(self, station, *, target_date, now):
            del target_date, now
            if station.station_id == hong_kong.station_id:
                assert released == [expected_metar_changes]
                assert worker.status_snapshot()["urgent_queue_depth"] == 2
            calls.append(station.station_id)
            return StationNowcastObservation(
                station_id=station.station_id,
                station_name=station.station_name,
                observed_high_c=30.0,
                observed_low_c=20.0,
                observed_at=datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc),
                high_observed_at=datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc),
                source="fixture",
                source_url="https://example.test/source",
                settlement_source_url="https://example.test/settlement",
                freshness_seconds=0,
                unavailable_reason="",
            )

    changed_station_ids = runner_module._refresh_official_station_observations(
        FakeProvider(),
        now=datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc),
        station_state_by_id=state_by_station,
        on_shared_metar_refreshed=release_metar_changes,
    )

    assert calls == [seoul.station_id, london.station_id, hong_kong.station_id]
    assert released == [expected_metar_changes]
    assert changed_station_ids == expected_metar_changes | {hong_kong.station_id}
    assert worker.status_snapshot()["urgent_queue_depth"] == 2


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
