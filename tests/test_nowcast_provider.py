from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from weather_bot import nowcast as nowcast_module
from weather_bot.config import Settings
from weather_bot.nowcast import AviationWeatherMetarNowcastProvider, DEFAULT_NOWCAST_SOURCES
from weather_bot.stations import STATION_MAP, station_audit_rows


FIXTURES = Path(__file__).parent / "fixtures" / "nowcast"


class FakeResponse:
    def __init__(self, payload, status_code: int = 200, *, headers=None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload

    @property
    def text(self):
        if isinstance(self._payload, str):
            return self._payload
        return json.dumps(self._payload)


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_text_fixture(name: str):
    return (FIXTURES / name).read_text(encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def without_awc_bootstrap(
    provider: AviationWeatherMetarNowcastProvider,
) -> AviationWeatherMetarNowcastProvider:
    provider._bootstrap_awc_metar_daily_extremes = lambda *_args, **_kwargs: None
    return provider


def seed_complete_metar_day(
    provider: AviationWeatherMetarNowcastProvider,
    *,
    station_id: str,
    local_date: str,
    last_observed_at: str,
    high_c: float,
    low_c: float,
) -> None:
    provider._metar_daily_extremes_state = {
        "schema_version": 1,
        "stations": {
            station_id: {
                "last_local_date": local_date,
                "last_observed_at": last_observed_at,
                "days": {
                    local_date: {
                        "high_c": high_c,
                        "high_bucket_c": int(high_c),
                        "high_bucket_confirmations": 1,
                        "low_c": low_c,
                        "high_observed_at": last_observed_at,
                        "high_last_observed_at": last_observed_at,
                        "high_drop_observed_at": "",
                        "low_observed_at": last_observed_at,
                        "low_last_observed_at": last_observed_at,
                        "low_rise_observed_at": "",
                        "latest_observed_at": last_observed_at,
                        "complete": True,
                        "blocked_reason": "",
                    }
                },
            }
        },
    }


def test_aviationweather_provider_default_cache_ttl_matches_provider_floor():
    provider = AviationWeatherMetarNowcastProvider(http_get=lambda *_args, **_kwargs: FakeResponse({}))

    assert provider.cache_ttl_seconds == 60


def test_upstream_mode_uses_metar_even_when_wunderground_key_exists(tmp_path):
    calls: list[str] = []

    def fake_get(url, *, params, timeout, headers):
        calls.append(url)
        return FakeResponse(
            [{"icaoId": "RKSI", "obsTime": "2026-06-01T16:00:00Z", "temp": 20.0}]
        )

    provider = AviationWeatherMetarNowcastProvider.from_settings(
        Settings(
            state_path=str(tmp_path / "state.json"),
            strategy_mode="upstream_lock_paper",
            wunderground_api_key="configured-wu-key",
            station_nowcast_cache_ttl_seconds=60,
        )
    )
    provider.http_get = fake_get
    provider = without_awc_bootstrap(provider)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 1, 16, 0, tzinfo=timezone.utc),
    )

    assert calls == [nowcast_module.AVIATIONWEATHER_METAR_SOURCE_URL]
    assert observation.source == "aviationweather-metar"


def test_metar_daily_extremes_state_writes_use_thread_safe_temp_files(tmp_path, monkeypatch):
    state_path = tmp_path / "metar_daily_extremes_state.json"
    provider = AviationWeatherMetarNowcastProvider(metar_daily_extremes_state_path=state_path)
    provider._metar_daily_extremes_state = {"schema_version": 1, "stations": {"RJTT": {"days": {}}}}
    replace_sources: list[str] = []

    def record_replace(src, _dst):
        replace_sources.append(Path(src).name)

    monkeypatch.setattr(nowcast_module.os, "replace", record_replace)
    errors: list[BaseException] = []

    def write_state() -> None:
        try:
            provider._write_metar_daily_extremes_state()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write_state) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(replace_sources) == 2
    assert len(set(replace_sources)) == 2


def provider_for(
    payload,
    *,
    freshness_seconds: int = 5400,
    cache_ttl_seconds: int = 0,
    request_log_path: Path | None = None,
    wunderground_api_key: str = "",
    clock=None,
):
    calls = []

    def fake_get(url, *, params, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        return FakeResponse(payload)

    provider = without_awc_bootstrap(
        AviationWeatherMetarNowcastProvider(
            http_get=fake_get,
            freshness_seconds=freshness_seconds,
            cache_ttl_seconds=cache_ttl_seconds,
            request_log_path=request_log_path,
            wunderground_api_key=wunderground_api_key,
            **({"clock": clock} if clock is not None else {}),
        )
    )
    return provider, calls


def wunderground_provider_for(
    payload,
    *,
    cache_ttl_seconds: int = 0,
    request_log_path: Path | None = None,
    clock=None,
):
    return provider_for(
        payload,
        cache_ttl_seconds=cache_ttl_seconds,
        request_log_path=request_log_path,
        wunderground_api_key="test-wu-key",
        clock=clock,
    )


def test_wunderground_history_direct_recomputes_metric_extremes_and_same_time_correction():
    first_at = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)
    corrected_at = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
    dropped_at = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)
    payload = {
        "metadata": {"units": "m"},
        "observations": [
            {"obs_id": "RKSI", "valid_time_gmt": int(first_at.timestamp()), "temp": 27},
            {"obs_id": "RKSI", "valid_time_gmt": int(corrected_at.timestamp()), "temp": 29},
            # Weather Underground can correct a row without changing its timestamp.
            {"obs_id": "RKSI", "valid_time_gmt": int(corrected_at.timestamp()), "temp": 28},
            {"obs_id": "RKSI", "valid_time_gmt": int(dropped_at.timestamp()), "temp": 26},
        ],
    }
    provider, calls = wunderground_provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 7, 19),
        now=datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.source == "wunderground-history-direct"
    assert observation.observed_high_c == pytest.approx(28.0)
    assert observation.observed_low_c == pytest.approx(26.0)
    assert observation.high_observed_at == corrected_at
    assert observation.high_drop_observed_at == dropped_at
    assert observation.raw_observation_count == 3
    assert observation.daily_extremes_complete is True
    assert len(calls) == 1
    assert calls[0]["url"] == (
        f"https://api.weather.com/v1/geocode/{STATION_MAP['seoul'].latitude}/"
        f"{STATION_MAP['seoul'].longitude}/observations/historical.json"
    )
    assert calls[0]["params"] == {
        "apiKey": "test-wu-key",
        "startDate": "20260719",
        "endDate": "20260719",
        "units": "m",
    }
    assert "test-wu-key" not in observation.source_url


def test_wunderground_fast_shadow_wakes_history_and_records_same_row_lead(tmp_path):
    first_at = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)
    previous_at = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
    fast_at = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)
    initial_history = {
        "metadata": {"units": "m"},
        "observations": [
            {"obs_id": "RKSI", "valid_time_gmt": int(first_at.timestamp()), "temp": 27},
            {"obs_id": "RKSI", "valid_time_gmt": int(previous_at.timestamp()), "temp": 29},
        ],
    }
    updated_history = {
        "metadata": {"units": "m"},
        "observations": [
            *initial_history["observations"],
            {"obs_id": "RKSI", "valid_time_gmt": int(fast_at.timestamp()), "temp": 30},
        ],
    }
    fast_payload = {
        "metadata": {"units": "m"},
        "observation": {
            "key": "RKSI",
            "obs_id": "Incheon International Airport",
            "valid_time_gmt": int(fast_at.timestamp()),
            "temp": 30,
        },
    }
    history_payloads = iter((initial_history, initial_history, updated_history))
    calls: list[str] = []

    def fake_get(url, *, params, timeout, headers):
        calls.append(url)
        if url.endswith("/observations/timeseries.json"):
            return FakeResponse(fast_payload)
        return FakeResponse(next(history_payloads))

    current = [datetime(2026, 7, 19, 1, 59, 55, tzinfo=timezone.utc)]
    request_log_path = tmp_path / "station_requests.jsonl"
    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        request_log_path=request_log_path,
        wunderground_api_key="test-wu-key",
        wunderground_fast_shadow_enabled=True,
        clock=lambda: current[0],
    )

    initial = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"], target_date=date(2026, 7, 19), now=current[0]
    )
    current[0] = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)
    pending = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"], target_date=date(2026, 7, 19), now=current[0]
    )
    current[0] += timedelta(seconds=5)
    matched = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"], target_date=date(2026, 7, 19), now=current[0]
    )

    assert initial.observed_at == previous_at
    assert pending.source == "wunderground-history-direct"
    assert pending.observed_at == previous_at
    assert pending.fast_shadow_state_key == "RKSI|2026-07-19T02:00:00+00:00|30|m"
    assert pending.fast_shadow_match_status == "pending"
    assert matched.observed_at == fast_at
    assert matched.fast_shadow_match_status == "matched"
    assert matched.fast_shadow_first_seen_at == datetime(
        2026, 7, 19, 2, 0, tzinfo=timezone.utc
    )
    assert matched.fast_shadow_daily_first_seen_at == datetime(
        2026, 7, 19, 2, 0, 5, tzinfo=timezone.utc
    )
    assert matched.fast_shadow_lead_seconds == 5
    assert sum(url.endswith("/observations/timeseries.json") for url in calls) == 2
    assert sum(url.endswith("/observations/historical.json") for url in calls) == 3

    match_rows = [
        row
        for row in read_jsonl(request_log_path)
        if row.get("request_mode") == "wunderground_timeseries_shadow_match"
    ]
    assert len(match_rows) == 1
    assert match_rows[0]["fast_first_seen_at"] == "2026-07-19T02:00:00+00:00"
    assert match_rows[0]["daily_history_first_seen_at"] == "2026-07-19T02:00:05+00:00"
    assert match_rows[0]["station_match"] is True
    assert match_rows[0]["time_match"] is True
    assert match_rows[0]["temperature_match"] is True
    assert match_rows[0]["units_match"] is True
    assert match_rows[0]["lead_seconds"] == 5


def test_wunderground_fast_shadow_honors_response_cache_control(tmp_path):
    first_at = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)
    latest_at = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
    history_payload = {
        "metadata": {"units": "m"},
        "observations": [
            {"obs_id": "RKSI", "valid_time_gmt": int(first_at.timestamp()), "temp": 27},
            {"obs_id": "RKSI", "valid_time_gmt": int(latest_at.timestamp()), "temp": 29},
        ],
    }
    fast_payload = {
        "metadata": {"units": "m"},
        "observation": {
            "key": "RKSI",
            "valid_time_gmt": int(latest_at.timestamp()),
            "temp": 29,
        },
    }
    calls: list[str] = []

    def fake_get(url, *, params, timeout, headers):
        del params, timeout, headers
        calls.append(url)
        if url.endswith("/observations/timeseries.json"):
            return FakeResponse(
                fast_payload,
                headers={"Cache-Control": "public, max-age=30"},
            )
        return FakeResponse(history_payload)

    current = [datetime(2026, 7, 19, 1, 59, 55, tzinfo=timezone.utc)]
    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        request_log_path=tmp_path / "station_requests.jsonl",
        wunderground_api_key="test-wu-key",
        wunderground_fast_shadow_enabled=True,
        clock=lambda: current[0],
    )
    station = STATION_MAP["seoul"]
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 7, 19), now=current[0]
    )

    current[0] = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 7, 19), now=current[0]
    )
    current[0] += timedelta(seconds=10)
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 7, 19), now=current[0]
    )
    assert sum(url.endswith("/observations/timeseries.json") for url in calls) == 1
    assert sum(url.endswith("/observations/historical.json") for url in calls) == 3

    current[0] += timedelta(seconds=21)
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 7, 19), now=current[0]
    )
    assert sum(url.endswith("/observations/timeseries.json") for url in calls) == 2
    assert sum(url.endswith("/observations/historical.json") for url in calls) == 4


def test_wunderground_fast_shadow_timeout_cannot_delay_daily_history_by_twenty_seconds(
    tmp_path,
):
    first_at = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)
    latest_at = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
    history_payload = {
        "metadata": {"units": "m"},
        "observations": [
            {"obs_id": "RKSI", "valid_time_gmt": int(first_at.timestamp()), "temp": 27},
            {"obs_id": "RKSI", "valid_time_gmt": int(latest_at.timestamp()), "temp": 29},
        ],
    }
    current = [datetime(2026, 7, 19, 1, 59, 55, tzinfo=timezone.utc)]
    calls: list[tuple[str, float, datetime]] = []

    def fake_get(url, *, params, timeout, headers):
        del params, headers
        calls.append((url, timeout, current[0]))
        if url.endswith("/observations/timeseries.json"):
            current[0] += timedelta(seconds=timeout)
            raise TimeoutError("fast shadow stalled")
        return FakeResponse(history_payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        timeout=20,
        cache_ttl_seconds=60,
        request_log_path=tmp_path / "station_requests.jsonl",
        wunderground_api_key="test-wu-key",
        wunderground_fast_shadow_enabled=True,
        clock=lambda: current[0],
    )
    station = STATION_MAP["seoul"]
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 7, 19), now=current[0]
    )

    current[0] = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 7, 19), now=current[0]
    )

    assert [
        "fast" if url.endswith("/observations/timeseries.json") else "history"
        for url, _timeout, _called_at in calls
    ] == ["history", "fast", "history"]
    assert calls[1][1] == 2.0
    assert calls[2][2] == datetime(2026, 7, 19, 2, 0, 2, tzinfo=timezone.utc)


def test_wunderground_request_budget_is_shared_and_keeps_fast_wake_state(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(nowcast_module, "WUNDERGROUND_REQUEST_LIMIT_PER_MINUTE", 2)
    monkeypatch.setattr(
        nowcast_module,
        "WUNDERGROUND_HISTORY_RESERVED_REQUESTS_PER_MINUTE",
        0,
    )
    first_at = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)
    previous_at = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
    fast_at = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)
    history_payload = {
        "metadata": {"units": "m"},
        "observations": [
            {"obs_id": "RKSI", "valid_time_gmt": int(first_at.timestamp()), "temp": 27},
            {"obs_id": "RKSI", "valid_time_gmt": int(previous_at.timestamp()), "temp": 29},
        ],
    }
    fast_payload = {
        "metadata": {"units": "m"},
        "observation": {
            "key": "RKSI",
            "valid_time_gmt": int(fast_at.timestamp()),
            "temp": 30,
        },
    }
    calls: list[str] = []

    def fake_get(url, *, params, timeout, headers):
        del params, timeout, headers
        calls.append(url)
        return FakeResponse(
            fast_payload if url.endswith("/observations/timeseries.json") else history_payload
        )

    current = [datetime(2026, 7, 19, 1, 59, 55, tzinfo=timezone.utc)]
    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        request_log_path=tmp_path / "station_requests.jsonl",
        wunderground_api_key="test-wu-key",
        wunderground_fast_shadow_enabled=True,
        clock=lambda: current[0],
    )
    station = STATION_MAP["seoul"]
    initial = provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 7, 19), now=current[0]
    )

    current[0] = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)
    pending = provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 7, 19), now=current[0]
    )

    assert len(calls) == 2
    assert initial.observed_at == previous_at
    assert pending.observed_at == previous_at
    assert pending.fast_shadow_match_status == "pending"
    deferred_rows = [
        row
        for row in read_jsonl(tmp_path / "station_requests.jsonl")
        if row.get("status") == "deferred"
    ]
    assert deferred_rows[-1]["unavailable_reason"] == (
        "wunderground-request-budget-exhausted"
    )


def test_wunderground_fast_budget_yields_capacity_to_daily_history(monkeypatch):
    monkeypatch.setattr(nowcast_module, "WUNDERGROUND_REQUEST_LIMIT_PER_MINUTE", 3)
    monkeypatch.setattr(nowcast_module, "WUNDERGROUND_FAST_REQUEST_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(
        nowcast_module,
        "WUNDERGROUND_HISTORY_RESERVED_REQUESTS_PER_MINUTE",
        1,
    )
    provider = AviationWeatherMetarNowcastProvider(wunderground_api_key="test-wu-key")
    first = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)

    assert provider._reserve_wunderground_request(first, fast=True) is True
    assert provider._reserve_wunderground_request(first, fast=True) is False
    assert provider._reserve_wunderground_request(first) is True
    assert provider._reserve_wunderground_request(first) is True
    assert provider._reserve_wunderground_request(first) is False


def test_wunderground_fast_shadow_existing_history_row_is_only_a_baseline(tmp_path):
    first_at = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)
    latest_at = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
    history_payload = {
        "metadata": {"units": "m"},
        "observations": [
            {"obs_id": "RKSI", "valid_time_gmt": int(first_at.timestamp()), "temp": 27},
            {"obs_id": "RKSI", "valid_time_gmt": int(latest_at.timestamp()), "temp": 29},
        ],
    }
    fast_payload = {
        "metadata": {"units": "m"},
        "observation": {
            "key": "RKSI",
            "obs_id": "Incheon International Airport",
            "valid_time_gmt": int(latest_at.timestamp()),
            "temp": 29,
        },
    }
    calls: list[str] = []

    def fake_get(url, *, params, timeout, headers):
        calls.append(url)
        return FakeResponse(
            fast_payload if url.endswith("/observations/timeseries.json") else history_payload
        )

    current = [datetime(2026, 7, 19, 1, 59, 55, tzinfo=timezone.utc)]
    request_log_path = tmp_path / "station_requests.jsonl"
    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        request_log_path=request_log_path,
        wunderground_api_key="test-wu-key",
        wunderground_fast_shadow_enabled=True,
        clock=lambda: current[0],
    )
    provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"], target_date=date(2026, 7, 19), now=current[0]
    )
    current[0] += timedelta(seconds=1)
    baseline = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"], target_date=date(2026, 7, 19), now=current[0]
    )

    assert baseline.fast_shadow_state_key == ""
    assert sum(url.endswith("/observations/historical.json") for url in calls) == 1
    assert not any(
        row.get("request_mode") == "wunderground_timeseries_shadow_match"
        for row in read_jsonl(request_log_path)
    )
    shadow_rows = [
        row
        for row in read_jsonl(request_log_path)
        if row.get("request_mode") == "wunderground_timeseries_shadow"
    ]
    assert shadow_rows[-1]["new_observation"] is False
    assert shadow_rows[-1]["baseline_already_in_history"] is True


@pytest.mark.parametrize(
    ("fast_metadata", "fast_row", "expected_reason"),
    [
        (
            {"units": "e"},
            {"key": "RKSI", "valid_time_gmt": 1784426400, "temp": 86},
            "wunderground-fast-units-mismatch",
        ),
        (
            {"units": "m"},
            {"key": "KATL", "valid_time_gmt": 1784426400, "temp": 30},
            "wunderground-fast-station-key-mismatch",
        ),
    ],
)
def test_wunderground_fast_shadow_rejects_wrong_units_or_selected_station(
    tmp_path,
    fast_metadata,
    fast_row,
    expected_reason,
):
    first_at = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)
    previous_at = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
    history_payload = {
        "metadata": {"units": "m"},
        "observations": [
            {"obs_id": "RKSI", "valid_time_gmt": int(first_at.timestamp()), "temp": 27},
            {"obs_id": "RKSI", "valid_time_gmt": int(previous_at.timestamp()), "temp": 29},
        ],
    }
    current = [datetime(2026, 7, 19, 1, 59, 55, tzinfo=timezone.utc)]
    request_log_path = tmp_path / "station_requests.jsonl"

    def fake_get(url, *, params, timeout, headers):
        if url.endswith("/observations/timeseries.json"):
            return FakeResponse(
                {"metadata": fast_metadata, "observations": [fast_row]}
            )
        return FakeResponse(history_payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        request_log_path=request_log_path,
        wunderground_api_key="test-wu-key",
        wunderground_fast_shadow_enabled=True,
        clock=lambda: current[0],
    )
    provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"], target_date=date(2026, 7, 19), now=current[0]
    )
    current[0] += timedelta(seconds=5)
    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"], target_date=date(2026, 7, 19), now=current[0]
    )

    assert observation.fast_shadow_state_key == ""
    invalid_rows = [
        row
        for row in read_jsonl(request_log_path)
        if row.get("request_mode") == "wunderground_timeseries_shadow"
        and row.get("status") == "invalid_response"
    ]
    assert invalid_rows[-1]["unavailable_reason"] == expected_reason


def test_wunderground_fast_shadow_403_opens_provider_wide_circuit(tmp_path):
    station_history = {
        "RKSI": {
            "metadata": {"units": "m"},
            "observations": [
                {"obs_id": "RKSI", "valid_time_gmt": 1784422800, "temp": 27},
                {"obs_id": "RKSI", "valid_time_gmt": 1784426400, "temp": 29},
            ],
        },
        "RJTT": {
            "metadata": {"units": "m"},
            "observations": [
                {"obs_id": "RJTT", "valid_time_gmt": 1784422800, "temp": 27},
                {"obs_id": "RJTT", "valid_time_gmt": 1784426400, "temp": 29},
            ],
        },
    }
    fast_calls = 0

    def fake_get(url, *, params, timeout, headers):
        nonlocal fast_calls
        if url.endswith("/observations/timeseries.json"):
            fast_calls += 1
            return FakeResponse({}, status_code=403)
        station_id = "RJTT" if str(STATION_MAP["tokyo"].latitude) in url else "RKSI"
        return FakeResponse(station_history[station_id])

    current = [datetime(2026, 7, 19, 2, 59, 55, tzinfo=timezone.utc)]
    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        request_log_path=tmp_path / "station_requests.jsonl",
        wunderground_api_key="test-wu-key",
        wunderground_fast_shadow_enabled=True,
        clock=lambda: current[0],
    )
    for city in ("seoul", "tokyo"):
        station = STATION_MAP[city]
        provider.observed_temperature_extremes_so_far(
            station, target_date=date(2026, 7, 19), now=current[0]
        )
        current[0] += timedelta(seconds=5)
        provider.observed_temperature_extremes_so_far(
            station, target_date=date(2026, 7, 19), now=current[0]
        )

    assert fast_calls == 1
    assert provider.wunderground_fast_shadow_runtime_status() == {
        "enabled": False,
        "status": "circuit_open",
        "reason": "http-403",
    }


def test_wunderground_history_direct_converts_fahrenheit_payload_to_celsius():
    first_at = datetime(2026, 7, 19, 18, 0, tzinfo=timezone.utc)
    latest_at = datetime(2026, 7, 19, 19, 0, tzinfo=timezone.utc)
    provider, calls = wunderground_provider_for(
        {
            "metadata": {"units": "e"},
            "observations": [
                {"obs_id": "KAUS", "valid_time_gmt": int(first_at.timestamp()), "temp": 68},
                {"obs_id": "KAUS", "valid_time_gmt": int(latest_at.timestamp()), "temp": 77},
            ],
        }
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["austin"],
        target_date=date(2026, 7, 19),
        now=datetime(2026, 7, 19, 20, 0, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.observed_high_c == pytest.approx(25.0)
    assert observation.observed_low_c == pytest.approx(20.0)
    assert observation.latest_temp_c == pytest.approx(25.0)
    assert calls[0]["params"]["units"] == "e"


def test_wunderground_history_direct_preserves_fahrenheit_exact_boundaries():
    first_at = datetime(2026, 7, 19, 18, 0, tzinfo=timezone.utc)
    latest_at = datetime(2026, 7, 19, 19, 0, tzinfo=timezone.utc)
    provider, _calls = wunderground_provider_for(
        {
            "metadata": {"units": "e"},
            "observations": [
                {"obs_id": "KAUS", "valid_time_gmt": int(first_at.timestamp()), "temp": 70},
                {"obs_id": "KAUS", "valid_time_gmt": int(latest_at.timestamp()), "temp": 74},
            ],
        }
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["austin"],
        target_date=date(2026, 7, 19),
        now=datetime(2026, 7, 19, 20, 0, tzinfo=timezone.utc),
    )

    assert observation.observed_low_c * 9.0 / 5.0 + 32.0 == pytest.approx(70.0, abs=1e-9)
    assert observation.observed_high_c * 9.0 / 5.0 + 32.0 == pytest.approx(74.0, abs=1e-9)


def test_wunderground_history_learns_cadence_from_first_valid_gap():
    first_at = datetime(2026, 7, 19, 0, 0, tzinfo=timezone.utc)
    latest_at = first_at + timedelta(minutes=30)
    provider, _calls = wunderground_provider_for(
        {
            "metadata": {"units": "m"},
            "observations": [
                {"obs_id": "RKSI", "valid_time_gmt": int(first_at.timestamp()), "temp": 27},
                {"obs_id": "RKSI", "valid_time_gmt": int(latest_at.timestamp()), "temp": 28},
            ],
        }
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 7, 19),
        now=latest_at + timedelta(minutes=1),
    )

    assert observation.learned_observation_interval_seconds == 30 * 60
    assert observation.next_observation_due_at == latest_at + timedelta(minutes=30)


@pytest.mark.parametrize("metadata", [None, {}, {"units": ""}])
def test_wunderground_history_direct_requires_confirmed_response_units(metadata):
    observed_at = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
    payload = {
        "observations": [
            {"obs_id": "RKSI", "valid_time_gmt": int(observed_at.timestamp()), "temp": 28},
        ]
    }
    if metadata is not None:
        payload["metadata"] = metadata
    provider, _calls = wunderground_provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 7, 19),
        now=datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.unavailable_reason == "wunderground-units-missing"


@pytest.mark.parametrize("temperature", [-999, 999])
def test_wunderground_history_direct_rejects_physical_temperature_outliers(temperature):
    observed_at = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
    provider, _calls = wunderground_provider_for(
        {
            "metadata": {"units": "m"},
            "observations": [
                {"obs_id": "RKSI", "valid_time_gmt": int(observed_at.timestamp()), "temp": temperature},
            ],
        }
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 7, 19),
        now=datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.unavailable_reason == "wunderground-temperature-out-of-range"


def test_wunderground_history_accepts_observation_published_during_request():
    requested_at = datetime(2026, 7, 19, 0, 59, 59, tzinfo=timezone.utc)
    observed_at = requested_at + timedelta(seconds=1)
    received_at = requested_at + timedelta(seconds=2)
    clock_values = iter((requested_at, received_at, received_at))
    provider, _calls = wunderground_provider_for(
        {
            "metadata": {"units": "m"},
            "observations": [
                {"obs_id": "RKSI", "valid_time_gmt": int(observed_at.timestamp()), "temp": 28},
            ],
        },
        clock=lambda: next(clock_values),
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 7, 19),
        now=requested_at,
    )

    assert observation.usable is True
    assert observation.observed_at == observed_at


def test_wunderground_history_direct_fails_closed_on_any_station_mismatch():
    observed_at = datetime(2026, 7, 19, 1, 0, tzinfo=timezone.utc)
    provider, _calls = wunderground_provider_for(
        {
            "metadata": {"units": "m"},
            "observations": [
                {"obs_id": "RKSI", "valid_time_gmt": int(observed_at.timestamp()), "temp": 28},
                {"obs_id": "RKPK", "valid_time_gmt": int(observed_at.timestamp()), "temp": 29},
            ],
        }
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 7, 19),
        now=datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.unavailable_reason == "wunderground-observation-station-mismatch"
    assert observation.source == "wunderground-history-direct"


def test_wunderground_history_direct_refreshes_different_stations_in_parallel():
    barrier = threading.Barrier(2)
    counter_lock = threading.Lock()
    active = 0
    max_active = 0
    observed_at = datetime(2026, 7, 19, 11, 30, tzinfo=timezone.utc)

    def fake_get(_url, *, params, timeout, headers):
        nonlocal active, max_active
        del timeout, headers
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            barrier.wait(timeout=2)
            station_id, temp = ("RKSI", 28) if params["units"] == "m" else ("KAUS", 68)
            return FakeResponse(
                {
                    "metadata": {"units": params["units"]},
                    "observations": [
                        {
                            "obs_id": station_id,
                            "valid_time_gmt": int(observed_at.timestamp()),
                            "temp": temp,
                        }
                    ],
                }
            )
        finally:
            with counter_lock:
                active -= 1

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        wunderground_api_key="test-wu-key",
    )
    now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                provider.observed_temperature_extremes_so_far,
                station,
                target_date=date(2026, 7, 19),
                now=now,
            )
            for station in (STATION_MAP["seoul"], STATION_MAP["austin"])
        ]
        observations = [future.result(timeout=3) for future in futures]

    assert provider.supports_parallel_station_refresh is True
    assert all(observation.usable for observation in observations)
    assert max_active == 2


def test_wunderground_entry_refresh_discards_only_selected_station_cache(tmp_path):
    calls: list[str] = []
    observed_at = datetime(2026, 7, 19, 11, 0, tzinfo=timezone.utc)

    def fake_get(url, *, params, timeout, headers):
        del timeout, headers
        station_id = "RKSI" if params["units"] == "m" else "KAUS"
        calls.append(station_id)
        return FakeResponse(
            {
                "metadata": {"units": params["units"]},
                "observations": [
                    {
                        "obs_id": station_id,
                        "valid_time_gmt": int(observed_at.timestamp()),
                        "temp": 28 if station_id == "RKSI" else 68,
                    }
                ],
            }
        )

    request_log_path = tmp_path / "wu-requests.jsonl"
    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=900,
        request_log_path=request_log_path,
        wunderground_api_key="test-wu-key",
        clock=lambda: datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc),
    )
    now = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
    for station in (STATION_MAP["seoul"], STATION_MAP["austin"]):
        provider.observed_temperature_extremes_so_far(
            station,
            target_date=date(2026, 7, 19),
            now=now,
        )

    provider.discard_cached_observations_before_entry(now=now, station_ids={"RKSI"})
    for station in (STATION_MAP["seoul"], STATION_MAP["austin"]):
        provider.observed_temperature_extremes_so_far(
            station,
            target_date=date(2026, 7, 19),
            now=now,
        )

    assert calls == ["RKSI", "KAUS", "RKSI"]
    assert provider.request_log_health()["status"] == "ok"
    log_text = request_log_path.read_text(encoding="utf-8")
    assert "wunderground-history-direct" in log_text
    assert "test-wu-key" not in log_text


def test_wunderground_cache_polls_every_five_seconds_only_near_learned_due_time():
    latest_at = datetime(2026, 7, 18, 16, 0, tzinfo=timezone.utc)
    next_due_at = latest_at + timedelta(minutes=30)
    current = [next_due_at - timedelta(seconds=20)]
    payload = {
        "metadata": {"units": "m"},
        "observations": [
            {
                "obs_id": "RKSI",
                "valid_time_gmt": int((latest_at - timedelta(hours=1)).timestamp()),
                "temp": 25,
            },
            {
                "obs_id": "RKSI",
                "valid_time_gmt": int((latest_at - timedelta(minutes=30)).timestamp()),
                "temp": 26,
            },
            {
                "obs_id": "RKSI",
                "valid_time_gmt": int(latest_at.timestamp()),
                "temp": 27,
            },
        ],
    }
    provider, calls = wunderground_provider_for(
        payload,
        cache_ttl_seconds=60,
        clock=lambda: current[0],
    )

    def observe(at: datetime):
        current[0] = at
        return provider.observed_temperature_extremes_so_far(
            STATION_MAP["seoul"],
            target_date=date(2026, 7, 19),
            now=at,
        )

    initial = observe(current[0])
    assert initial.next_observation_due_at == next_due_at
    observe(next_due_at - timedelta(seconds=10))
    assert len(calls) == 1

    observe(next_due_at - timedelta(seconds=4))
    assert len(calls) == 2
    observe(next_due_at + timedelta(seconds=1))
    assert len(calls) == 3
    observe(next_due_at + timedelta(seconds=2))
    assert len(calls) == 3
    observe(next_due_at + timedelta(seconds=5))
    assert len(calls) == 3
    observe(next_due_at + timedelta(seconds=8))
    assert len(calls) == 4

    observe(next_due_at + timedelta(minutes=9, seconds=58))
    assert len(calls) == 5
    observe(next_due_at + timedelta(minutes=10, seconds=5))
    assert len(calls) == 5


@pytest.mark.parametrize("failure_mode", ["429", "network"])
def test_wunderground_due_poll_backs_off_after_http_or_network_error(failure_mode):
    latest_at = datetime(2026, 7, 18, 16, 0, tzinfo=timezone.utc)
    next_due_at = latest_at + timedelta(minutes=30)
    current = [next_due_at - timedelta(seconds=20)]
    payload = {
        "metadata": {"units": "m"},
        "observations": [
            {
                "obs_id": "RKSI",
                "valid_time_gmt": int((latest_at - timedelta(hours=1)).timestamp()),
                "temp": 25,
            },
            {
                "obs_id": "RKSI",
                "valid_time_gmt": int((latest_at - timedelta(minutes=30)).timestamp()),
                "temp": 26,
            },
            {
                "obs_id": "RKSI",
                "valid_time_gmt": int(latest_at.timestamp()),
                "temp": 27,
            },
        ],
    }
    calls: list[datetime] = []

    def fake_get(_url, *, params, timeout, headers):
        del params, timeout, headers
        calls.append(current[0])
        if len(calls) == 2:
            if failure_mode == "429":
                return FakeResponse({}, status_code=429)
            raise TimeoutError("provider timed out")
        return FakeResponse(payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        wunderground_api_key="test-wu-key",
        clock=lambda: current[0],
    )

    def observe(at: datetime):
        current[0] = at
        return provider.observed_temperature_extremes_so_far(
            STATION_MAP["seoul"],
            target_date=date(2026, 7, 19),
            now=at,
        )

    assert observe(current[0]).usable is True
    assert observe(next_due_at + timedelta(seconds=1)).usable is False
    assert len(calls) == 2

    assert observe(next_due_at + timedelta(seconds=7)).usable is False
    assert len(calls) == 2
    assert observe(next_due_at + timedelta(seconds=62)).usable is True
    assert len(calls) == 3


def metar_sequence_provider(payloads: list[list[dict[str, object]]], *, state_path: Path):
    remaining = iter(payloads)

    def fake_get(url, *, params, timeout, headers):
        return FakeResponse(next(remaining))

    return without_awc_bootstrap(
        AviationWeatherMetarNowcastProvider(
            http_get=fake_get,
            cache_ttl_seconds=0,
            metar_daily_extremes_state_path=state_path,
        )
    )


def test_aviationweather_latest_only_is_not_a_complete_daily_extreme(tmp_path):
    provider = metar_sequence_provider(
        [[
            {
                "icaoId": "RJTT",
                "obsTime": "2026-06-23T06:00:00.000Z",
                "temp": 23.0,
                "dewp": 22.0,
                "wxString": "-RA",
                "rawOb": "RJTT 230600Z 18005KT 9999 FEW020 23/18 Q1010",
            }
        ]],
        state_path=tmp_path / "metar_daily_extremes_state.json",
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 5, tzinfo=timezone.utc),
    )

    assert observation.observed_high_c == 23.0
    assert observation.observed_low_c == 23.0
    assert observation.latest_temp_c == 23.0
    assert observation.latest_dewpoint_c == 22.0
    assert observation.latest_weather == "-RA"
    assert observation.daily_extremes_complete is False
    assert observation.data_block_reason == "metar-daily-extremes-baseline-missing"


def test_aviationweather_parse_accumulates_cross_date_rows_but_returns_target_date_rows(tmp_path):
    provider = AviationWeatherMetarNowcastProvider(
        metar_daily_extremes_state_path=tmp_path / "metar_daily_extremes_state.json"
    )
    payload = [
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-22T14:30:00.000Z",
            "temp": 31.0,
            "rawOb": "RJTT 221430Z 18005KT 9999 FEW020 31/18 Q1010",
        },
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-22T15:30:00.000Z",
            "temp": 20.0,
            "rawOb": "RJTT 221530Z 18005KT 9999 FEW020 20/18 Q1010",
        },
    ]

    observation = provider._parse_payload(
        payload,
        STATION_MAP["tokyo"],
        date(2026, 6, 23),
        datetime(2026, 6, 22, 15, 35, tzinfo=timezone.utc),
        DEFAULT_NOWCAST_SOURCES["RJTT"],
    )

    assert observation.daily_extremes_complete is True
    assert observation.observed_high_c == 20.0
    assert observation.observed_low_c == 20.0
    assert observation.latest_temp_c == 20.0
    assert observation.raw_observation_count == 1


def test_aviationweather_daytime_bootstrap_keeps_four_hour_bulk_cache_and_runs_once(tmp_path):
    calls: list[dict[str, object]] = []
    latest_row = {
        "icaoId": "RJTT",
        "obsTime": "2026-06-23T06:00:00.000Z",
        "temp": 23.0,
        "rawOb": "RJTT 230600Z 18005KT 9999 FEW020 23/18 Q1010",
    }
    historical_payload = [
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-22T14:30:00.000Z",
            "temp": 21.0,
            "rawOb": "RJTT 221430Z 18005KT 9999 FEW020 21/18 Q1010",
        },
        *[
            {
                "icaoId": "RJTT",
                "obsTime": (
                    datetime(2026, 6, 22, 15, 30, tzinfo=timezone.utc)
                    + timedelta(hours=index)
                ).isoformat(),
                "temp": 24.0 if index == 8 else 20.0 + index % 3,
            }
            for index in range(15)
        ],
        latest_row,
    ]
    fast_payload = [
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-23T05:00:00.000Z",
            "temp": 22.0,
            "rawOb": "RJTT 230500Z 18005KT 9999 FEW020 22/18 Q1010",
        },
        latest_row,
    ]

    def fake_get(url, *, params, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        return FakeResponse(historical_payload if params["hours"] == 30 else fast_payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=0,
        metar_daily_extremes_state_path=tmp_path / "metar_daily_extremes_state.json",
    )
    provider._accumulate_metar_daily_extremes(
        STATION_MAP["tokyo"],
        [(datetime(2026, 6, 23, 5, 0, tzinfo=timezone.utc), 22.0)],
        date(2026, 6, 23),
    )
    first = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 5, tzinfo=timezone.utc),
    )
    first_call_count = len(calls)
    second = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 6, 1, tzinfo=timezone.utc),
    )

    bootstrap_calls = [call for call in calls if call["params"]["hours"] == 30]
    fast_calls = [call for call in calls if call["params"]["hours"] == 4]
    assert first_call_count == 1
    assert len(bootstrap_calls) == 1
    assert len(bootstrap_calls[0]["params"]["ids"].split(",")) <= 4
    assert len(fast_calls) == 1
    assert set(fast_calls[0]["params"]["ids"].split(",")) == set(
        provider._awc_metar_bulk_station_ids()
    )
    assert first.daily_extremes_complete is True
    assert first.observed_high_c == 24.0
    assert first.observed_low_c == 20.0
    assert first.latest_temp_c == 23.0
    assert first.raw_observation_count == 16
    assert second.daily_extremes_complete is True


def test_aviationweather_bootstrap_recovers_station_missing_from_successful_group(tmp_path):
    calls: list[dict[str, object]] = []
    complete_tokyo_history = [
        {"icaoId": "RJTT", "obsTime": "2026-06-22T14:30:00Z", "temp": 21.0},
        *[
            {
                "icaoId": "RJTT",
                "obsTime": (
                    datetime(2026, 6, 22, 15, 30, tzinfo=timezone.utc)
                    + timedelta(hours=index)
                ).isoformat(),
                "temp": 20.0 + index % 4,
            }
            for index in range(15)
        ],
        {"icaoId": "RJTT", "obsTime": "2026-06-23T06:00:00Z", "temp": 24.0},
    ]

    def fake_get(url, *, params, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        if params["hours"] == 30 and params["ids"] == "RJTT":
            return FakeResponse(complete_tokyo_history)
        if params["hours"] == 30:
            return FakeResponse(
                [{"icaoId": "RCSS", "obsTime": "2026-06-23T06:00:00Z", "temp": 28.0}]
            )
        return FakeResponse(complete_tokyo_history[-1:])

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=0,
        metar_daily_extremes_state_path=tmp_path / "metar_daily_extremes_state.json",
    )

    missing = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 5, tzinfo=timezone.utc),
    )
    recovered = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 6, 1, tzinfo=timezone.utc),
    )

    bootstrap_calls = [call for call in calls if call["params"]["hours"] == 30]
    assert [call["params"]["ids"] for call in bootstrap_calls] == [
        "RCSS,RJTT,RKPK,RKSI",
        "RJTT",
    ]
    assert missing.daily_extremes_complete is False
    assert recovered.daily_extremes_complete is True
    assert recovered.observed_high_c == 24.0


def test_aviationweather_bootstrap_preserves_no_observations_reason_for_204(tmp_path):
    provider = AviationWeatherMetarNowcastProvider(
        http_get=lambda *_args, **_kwargs: FakeResponse(None, status_code=204),
        cache_ttl_seconds=0,
        metar_daily_extremes_state_path=tmp_path / "metar_daily_extremes_state.json",
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 5, tzinfo=timezone.utc),
    )

    assert observation.unavailable_reason == "no-observations-returned"


def test_aviationweather_bootstrap_retries_truncated_group_with_trigger_station(tmp_path):
    calls: list[dict[str, object]] = []
    complete_single_station_history = [
        {"icaoId": "RJTT", "obsTime": "2026-06-22T14:30:00Z", "temp": 21.0},
        *[
            {
                "icaoId": "RJTT",
                "obsTime": (
                    datetime(2026, 6, 22, 15, 30, tzinfo=timezone.utc)
                    + timedelta(hours=index)
                ).isoformat(),
                "temp": 20.0 + index % 3,
            }
            for index in range(15)
        ],
        {"icaoId": "RJTT", "obsTime": "2026-06-23T06:00:00Z", "temp": 23.0},
    ]

    def fake_get(url, *, params, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        ids = params["ids"].split(",")
        if params["hours"] == 30 and len(ids) > 1:
            return FakeResponse([{"row": index} for index in range(400)])
        if params["hours"] == 30:
            return FakeResponse(complete_single_station_history)
        return FakeResponse(complete_single_station_history[-1:])

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        metar_daily_extremes_state_path=tmp_path / "metar_daily_extremes_state.json",
    )
    truncated = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 5, tzinfo=timezone.utc),
    )
    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 6, 1, tzinfo=timezone.utc),
    )

    bootstrap_calls = [call for call in calls if call["params"]["hours"] == 30]
    station_ids = provider._awc_metar_bulk_station_ids()
    station_index = station_ids.index("RJTT")
    group_start = station_index - station_index % nowcast_module.AWC_METAR_BOOTSTRAP_GROUP_SIZE
    expected_group = station_ids[
        group_start : group_start + nowcast_module.AWC_METAR_BOOTSTRAP_GROUP_SIZE
    ]
    assert [call["params"]["ids"] for call in bootstrap_calls] == [
        ",".join(expected_group),
        "RJTT",
    ]
    assert "RJTT" in expected_group
    assert len(bootstrap_calls[0]["params"]["ids"].split(",")) == 4
    assert truncated.unavailable_reason == "metar-response-row-limit"
    assert observation.daily_extremes_complete is True


def test_aviationweather_bootstrap_discards_truncated_single_station_and_does_not_storm(tmp_path):
    calls: list[dict[str, object]] = []
    current_row = {
        "icaoId": "RJTT",
        "obsTime": "2026-06-23T06:00:00Z",
        "temp": 23.0,
    }

    def fake_get(url, *, params, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        if params["hours"] == 30:
            return FakeResponse([dict(current_row, receiptTime=index) for index in range(400)])
        return FakeResponse([current_row])

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=0,
        metar_daily_extremes_state_path=tmp_path / "metar_daily_extremes_state.json",
    )
    first = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 5, tzinfo=timezone.utc),
    )
    second = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 6, 1, tzinfo=timezone.utc),
    )
    third = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 7, 2, tzinfo=timezone.utc),
    )

    bootstrap_calls = [call for call in calls if call["params"]["hours"] == 30]
    assert len(bootstrap_calls) == 2
    assert len(bootstrap_calls[0]["params"]["ids"].split(",")) == 4
    assert bootstrap_calls[1]["params"]["ids"] == "RJTT"
    assert first.unavailable_reason == "metar-response-row-limit"
    assert second.unavailable_reason == "metar-response-row-limit"
    assert third.daily_extremes_complete is False
    assert third.data_block_reason == "metar-daily-extremes-baseline-missing"


def test_aviationweather_bootstrap_failure_retries_once_then_uses_four_hour_path(tmp_path):
    calls: list[int] = []
    current_row = {
        "icaoId": "RJTT",
        "obsTime": "2026-06-23T06:00:00Z",
        "temp": 23.0,
    }

    def fake_get(url, *, params, timeout, headers):
        calls.append(params["hours"])
        if params["hours"] == 30:
            raise TimeoutError("network down")
        return FakeResponse([current_row])

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=0,
        metar_daily_extremes_state_path=tmp_path / "metar_daily_extremes_state.json",
    )
    first = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 5, tzinfo=timezone.utc),
    )
    retried = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 6, 1, tzinfo=timezone.utc),
    )
    fallback = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 7, 2, tzinfo=timezone.utc),
    )

    assert calls == [30, 30, 4]
    assert first.unavailable_reason == "nowcast-fetch-error:TimeoutError"
    assert retried.unavailable_reason == "nowcast-fetch-error:TimeoutError"
    assert fallback.daily_extremes_complete is False
    assert fallback.data_block_reason == "metar-daily-extremes-baseline-missing"


def test_aviationweather_midnight_handoff_builds_persistent_daily_extremes(tmp_path):
    state_path = tmp_path / "metar_daily_extremes_state.json"
    provider = metar_sequence_provider(
        [
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T14:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221400Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T15:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221500Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
        ],
        state_path=state_path,
    )

    previous = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 22, 14, 5, tzinfo=timezone.utc),
    )
    midnight = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 15, 5, tzinfo=timezone.utc),
    )

    assert previous.daily_extremes_complete is False
    assert midnight.daily_extremes_complete is True
    assert midnight.observed_high_c == 20.0
    assert midnight.observed_low_c == 20.0

    restarted = metar_sequence_provider(
        [[{
            "icaoId": "RJTT",
            "obsTime": "2026-06-22T16:00:00.000Z",
            "temp": 19.0,
            "rawOb": "RJTT 221600Z 18005KT 9999 FEW020 19/18 Q1010",
        }]],
        state_path=state_path,
    )
    continued = restarted.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 16, 5, tzinfo=timezone.utc),
    )

    assert continued.daily_extremes_complete is True
    assert continued.observed_high_c == 20.0
    assert continued.observed_low_c == 19.0


def test_aviationweather_counts_repeated_high_integer_bucket_confirmations(tmp_path):
    state_path = tmp_path / "metar_daily_extremes_state.json"
    provider = metar_sequence_provider(
        [
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T14:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221400Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T15:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221500Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T16:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221600Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
        ],
        state_path=state_path,
    )

    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 22, 14, 5, tzinfo=timezone.utc),
    )
    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 15, 5, tzinfo=timezone.utc),
    )
    confirmed = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 16, 5, tzinfo=timezone.utc),
    )

    assert confirmed.observed_high_c == 20.0
    assert confirmed.high_bucket_confirmations == 2


def test_aviationweather_tracks_high_plateau_and_drop_times(tmp_path):
    state_path = tmp_path / "metar_daily_extremes_state.json"
    provider = metar_sequence_provider(
        [
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T14:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221400Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T15:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221500Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T16:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221600Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
        ],
        state_path=state_path,
    )

    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 22, 14, 5, tzinfo=timezone.utc),
    )
    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 15, 5, tzinfo=timezone.utc),
    )
    dropped = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 16, 5, tzinfo=timezone.utc),
    )

    assert dropped.observed_high_c == 21.0
    assert dropped.high_observed_at.isoformat() == "2026-06-22T15:00:00+00:00"
    assert dropped.high_last_observed_at.isoformat() == "2026-06-22T15:00:00+00:00"
    assert dropped.high_drop_observed_at.isoformat() == "2026-06-22T16:00:00+00:00"


def test_aviationweather_tracks_low_plateau_and_rise_times(tmp_path):
    state_path = tmp_path / "metar_daily_extremes_state.json"
    provider = metar_sequence_provider(
        [
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T14:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221400Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T15:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221500Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T16:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221600Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T17:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221700Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
        ],
        state_path=state_path,
    )

    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 22, 14, 5, tzinfo=timezone.utc),
    )
    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 15, 5, tzinfo=timezone.utc),
    )
    before_rise = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 16, 5, tzinfo=timezone.utc),
    )
    risen = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 17, 5, tzinfo=timezone.utc),
    )

    assert before_rise.observed_low_c == 20.0
    assert before_rise.low_rise_observed_at is None
    assert risen.observed_low_c == 20.0
    assert risen.low_observed_at.isoformat() == "2026-06-22T15:00:00+00:00"
    assert risen.low_last_observed_at.isoformat() == "2026-06-22T16:00:00+00:00"
    assert risen.low_rise_observed_at.isoformat() == "2026-06-22T17:00:00+00:00"


def test_aviationweather_observation_gap_invalidates_daily_extremes(tmp_path):
    state_path = tmp_path / "metar_daily_extremes_state.json"
    provider = metar_sequence_provider(
        [
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T14:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221400Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T15:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221500Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T17:00:00.000Z",
                "temp": 19.0,
                "rawOb": "RJTT 221700Z 18005KT 9999 FEW020 19/18 Q1010",
            }],
        ],
        state_path=state_path,
    )

    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 22, 14, 5, tzinfo=timezone.utc),
    )
    complete = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 15, 5, tzinfo=timezone.utc),
    )
    gapped = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 17, 5, tzinfo=timezone.utc),
    )

    assert complete.daily_extremes_complete is True
    assert gapped.daily_extremes_complete is False
    assert gapped.data_block_reason == "metar-observation-gap"
    assert gapped.observed_high_c == 20.0
    assert gapped.observed_low_c == 19.0


def test_aviationweather_provider_returns_fresh_station_high_from_fixture():
    provider, calls = provider_for(load_fixture("aviationweather_rksi_fresh.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.observed_high_c == 26.7
    assert observation.observed_at.isoformat() == "2026-06-02T08:00:00+00:00"
    assert observation.high_observed_at.isoformat() == "2026-06-02T08:00:00+00:00"
    assert observation.freshness_seconds == 1800
    assert observation.source == "aviationweather-metar"
    assert observation.unavailable_reason == ""
    assert observation.raw_observation_count == 3
    requested_ids = set(calls[0]["params"]["ids"].split(","))
    assert "RKSI" in requested_ids
    assert "HKO" not in requested_ids
    assert calls[0]["params"]["format"] == "json"


def test_aviationweather_provider_returns_high_and_low_from_one_cached_fetch():
    provider, calls = provider_for(load_fixture("aviationweather_rksi_fresh.json"), cache_ttl_seconds=900)

    high_observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )
    low_observation = provider.observed_low_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 31, tzinfo=timezone.utc),
    )

    assert high_observation.observed_high_c == 26.7
    assert low_observation.observed_low_c == 18.0
    assert low_observation.low_observed_at.isoformat() == "2026-06-02T00:00:00+00:00"
    assert len(calls) == 1


def test_entry_refresh_discards_cached_bulk_response_and_fetches_new_observation(tmp_path):
    base = [
        {"icaoId": "RKSI", "obsTime": "2026-06-01T15:00:00.000Z", "temp": 20.0},
        {"icaoId": "RKSI", "obsTime": "2026-06-02T06:50:00.000Z", "temp": 28.0},
    ]
    provider = metar_sequence_provider(
        [base, [*base, {"icaoId": "RKSI", "obsTime": "2026-06-02T07:20:00.000Z", "temp": 29.0}]],
        state_path=tmp_path / "metar.json",
    )
    first = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
    )

    provider.discard_cached_observations_before_entry(
        now=datetime(2026, 6, 2, 8, 1, 1, tzinfo=timezone.utc)
    )
    refreshed = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 1, 1, tzinfo=timezone.utc),
    )

    assert first.observed_high_c == pytest.approx(28.0)
    assert refreshed.observed_high_c == pytest.approx(29.0)


def test_entry_refresh_network_error_returns_unavailable_observation(tmp_path):
    payload = [
        {"icaoId": "RKSI", "obsTime": "2026-06-01T15:00:00.000Z", "temp": 20.0},
        {"icaoId": "RKSI", "obsTime": "2026-06-02T06:50:00.000Z", "temp": 28.0},
    ]
    responses = iter((FakeResponse(payload), TimeoutError("network down")))

    def fake_get(*_args, **_kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    provider = without_awc_bootstrap(
        AviationWeatherMetarNowcastProvider(
            http_get=fake_get,
            cache_ttl_seconds=900,
            metar_daily_extremes_state_path=tmp_path / "metar.json",
        )
    )
    provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
    )

    provider.discard_cached_observations_before_entry(
        now=datetime(2026, 6, 2, 8, 1, 1, tzinfo=timezone.utc)
    )
    failed = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 1, 1, tzinfo=timezone.utc),
    )

    assert failed.usable is False
    assert failed.unavailable_reason == "nowcast-fetch-error:TimeoutError"


def test_entry_refresh_respects_one_minute_real_request_floor(tmp_path):
    base = [
        {"icaoId": "RKSI", "obsTime": "2026-06-01T15:00:00.000Z", "temp": 20.0},
        {"icaoId": "RKSI", "obsTime": "2026-06-02T06:50:00.000Z", "temp": 28.0},
    ]
    provider = metar_sequence_provider(
        [
            base,
            [*base, {"icaoId": "RKSI", "obsTime": "2026-06-02T07:20:00.000Z", "temp": 29.0}],
            [*base, {"icaoId": "RKSI", "obsTime": "2026-06-02T07:50:00.000Z", "temp": 30.0}],
        ],
        state_path=tmp_path / "metar.json",
    )
    provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
    )
    provider.discard_cached_observations_before_entry(
        now=datetime(2026, 6, 2, 8, 1, 1, tzinfo=timezone.utc)
    )
    provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 1, 1, tzinfo=timezone.utc),
    )

    provider.discard_cached_observations_before_entry(
        now=datetime(2026, 6, 2, 8, 1, 11, tzinfo=timezone.utc)
    )
    newest = provider.observed_temperature_extremes_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 1, 11, tzinfo=timezone.utc),
    )

    assert newest.observed_high_c == pytest.approx(29.0)


def test_aviationweather_provider_prefetches_multiple_metar_stations_once_per_refresh():
    payload = [
        {
            "icaoId": "KLGA",
            "obsTime": "2026-06-02T10:00:00.000Z",
            "temp": 19.4,
            "rawOb": "KLGA 021000Z 21008KT 10SM FEW050 19/14 A2992 RMK T01940140",
        },
        {
            "icaoId": "KLGA",
            "obsTime": "2026-06-02T18:00:00.000Z",
            "temp": 25.6,
            "rawOb": "KLGA 021800Z 22009KT 10SM FEW050 26/14 A2991",
        },
        {
            "icaoId": "KATL",
            "obsTime": "2026-06-02T11:00:00.000Z",
            "temp": 21.1,
            "rawOb": "KATL 021100Z 23005KT 10SM FEW030 21/16 A3002 RMK T02110160",
        },
        {
            "icaoId": "KATL",
            "obsTime": "2026-06-02T18:00:00.000Z",
            "temp": 29.4,
            "rawOb": "KATL 021800Z 24007KT 10SM FEW040 29/17 A2999 RMK T02940170",
        },
    ]
    provider, calls = provider_for(payload, cache_ttl_seconds=900)
    now = datetime(2026, 6, 2, 18, 30, tzinfo=timezone.utc)

    nyc_observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["nyc"],
        target_date=date(2026, 6, 2),
        now=now,
    )
    atlanta_observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["atlanta"],
        target_date=date(2026, 6, 2),
        now=now,
    )

    assert nyc_observation.observed_high_c == 25.6
    assert nyc_observation.observed_low_c == 19.4
    assert atlanta_observation.observed_high_c == 29.4
    assert atlanta_observation.observed_low_c == 21.1
    assert len(calls) == 1
    requested_ids = set(calls[0]["params"]["ids"].split(","))
    assert {"KLGA", "KATL", "RKSI"}.issubset(requested_ids)


def test_aviationweather_bulk_request_includes_new_verified_station_ids():
    provider, _calls = provider_for([])

    station_ids = set(provider._awc_metar_bulk_station_ids())

    assert {"KAUS", "KBKF", "KHOU", "WMKK", "VILK", "MMMX", "KSFO", "SBGR"}.issubset(station_ids)


def test_aviationweather_provider_accepts_matching_new_station_and_rejects_other_station():
    payload = [
        {
            "icaoId": "KAUS",
            "obsTime": "2026-06-19T20:00:00.000Z",
            "temp": 35.6,
            "rawOb": "KAUS 192000Z 17010KT 10SM FEW060 36/22 A2990",
        },
        {
            "icaoId": "KHOU",
            "obsTime": "2026-06-19T20:00:00.000Z",
            "temp": 34.4,
            "rawOb": "KHOU 192000Z 18008KT 10SM FEW050 34/24 A2991",
        },
    ]
    provider, _calls = provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["austin"],
        target_date=date(2026, 6, 19),
        now=datetime(2026, 6, 19, 20, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.observed_high_c == 35.6
    assert observation.raw_observation_count == 1


def test_aviationweather_provider_rejects_bulk_row_without_station_id():
    payload = [
        {
            "obsTime": "2026-06-02T18:00:00.000Z",
            "temp": 25.6,
            "rawOb": "KLGA 021800Z 22009KT 10SM FEW050 26/14 A2991",
        }
    ]
    provider, _calls = provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["nyc"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 18, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_low_c is None
    assert observation.unavailable_reason == "malformed-observation-payload"


def test_aviationweather_request_log_records_external_fetch_not_cache_hit(tmp_path):
    request_log_path = tmp_path / "station_nowcast_request_log.jsonl"
    provider, calls = provider_for(
        load_fixture("aviationweather_rksi_fresh.json"),
        cache_ttl_seconds=900,
        request_log_path=request_log_path,
        clock=lambda: datetime(2026, 6, 2, 8, 30, 5, tzinfo=timezone.utc),
    )

    provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )
    provider.observed_low_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 31, tzinfo=timezone.utc),
    )

    rows = read_jsonl(request_log_path)

    assert len(calls) == 1
    request_rows = [row for row in rows if row.get("request_mode") == "awc_metar_bulk_cache"]
    delivery_rows = [row for row in rows if row.get("request_mode") == "observation_delivery"]

    assert len(request_rows) == 1
    assert len(delivery_rows) == 1
    assert request_rows[0]["city"] == "bulk-metar"
    assert request_rows[0]["station_id"] == "METAR_BULK"
    assert request_rows[0]["station_name"] == "Aviation Weather Center METAR bulk prefetch"
    assert request_rows[0]["trigger_city"] == "seoul"
    assert request_rows[0]["trigger_station_id"] == STATION_MAP["seoul"].station_id
    assert STATION_MAP["seoul"].station_id in request_rows[0]["requested_station_ids"]
    assert request_rows[0]["source"] == "aviationweather-metar"
    assert request_rows[0]["status"] == "success"
    assert request_rows[0]["status_code"] == 200
    assert request_rows[0]["cache_miss_reason"] == "empty-cache"
    assert request_rows[0]["requested_at"] == "2026-06-02T08:30:05+00:00"
    assert delivery_rows[0]["station_id"] == "RKSI"
    assert delivery_rows[0]["observation_observed_at"] == "2026-06-02T08:00:00+00:00"
    assert delivery_rows[0]["request_started_at"] == "2026-06-02T08:30:05+00:00"
    assert delivery_rows[0]["latest_temp_c"] == 26.7
    assert delivery_rows[0]["bot_detection_latency_seconds"] == 1805


def test_aviationweather_bulk_request_floor_is_one_minute_even_when_station_cache_is_short(tmp_path):
    request_log_path = tmp_path / "station_nowcast_request_log.jsonl"
    clock_now = [datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc)]
    provider, calls = provider_for(
        load_fixture("aviationweather_rksi_fresh.json"),
        cache_ttl_seconds=60,
        request_log_path=request_log_path,
        clock=lambda: clock_now[0],
    )

    provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )
    provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, 59, 999000, tzinfo=timezone.utc),
    )
    clock_now[0] = datetime(2026, 6, 2, 8, 31, 0, tzinfo=timezone.utc)
    provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 31, 0, tzinfo=timezone.utc),
    )

    rows = [
        row
        for row in read_jsonl(request_log_path)
        if row.get("request_mode") == "awc_metar_bulk_cache"
    ]
    assert len(calls) == 2
    assert len(rows) == 2
    assert rows[0]["requested_at"] == "2026-06-02T08:30:00+00:00"
    assert rows[1]["requested_at"] == "2026-06-02T08:31:00+00:00"


def test_kma_metar_is_primary_for_configured_korean_station(tmp_path):
    calls: list[str] = []

    def fake_get(url, *, params, timeout, headers):
        calls.append(url)
        assert params["icao"] == "RKSI"
        assert params["ServiceKey"] == "test-key"
        return FakeResponse(
            {
                "response": {
                    "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                    "body": {
                        "items": {
                            "item": [
                                {
                                    "icaoCode": "RKSI",
                                    "metarMsg": "METAR RKSI 170430Z 03008KT CAVOK 31/24 Q1001 NOSIG=",
                                }
                            ]
                        }
                    },
                }
            }
        )

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        request_log_path=tmp_path / "requests.jsonl",
        kma_metar_service_key="test-key",
        kma_metar_poll_seconds=30,
        kma_metar_station_ids={"RKSI", "RKPK"},
        clock=lambda: datetime(2026, 7, 17, 4, 30, 21, tzinfo=timezone.utc),
    )
    seed_complete_metar_day(
        provider,
        station_id="RKSI",
        local_date="2026-07-17",
        last_observed_at="2026-07-17T04:00:00+00:00",
        high_c=30.0,
        low_c=24.0,
    )

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 7, 17),
        now=datetime(2026, 7, 17, 4, 30, 20, tzinfo=timezone.utc),
    )

    assert calls == [nowcast_module.KMA_METAR_SOURCE_URL]
    assert observation.source == "kma-aviation-metar"
    assert observation.latest_temp_c == 31.0
    assert observation.observed_at.isoformat() == "2026-07-17T04:30:00+00:00"
    assert observation.request_started_at.isoformat() == "2026-07-17T04:30:21+00:00"
    assert observation.bot_detection_latency_seconds == 21
    assert observation.source_latency_status == "provider-timestamp-unavailable"
    rows = read_jsonl(tmp_path / "requests.jsonl")
    assert [row["request_mode"] for row in rows] == ["kma_metar_fast", "observation_delivery"]
    assert rows[-1]["request_started_at"] == "2026-07-17T04:30:21+00:00"


def test_kma_metar_failure_falls_back_to_awc(tmp_path):
    awc_payload = load_fixture("aviationweather_rksi_fresh.json")
    calls: list[str] = []

    def fake_get(url, *, params, timeout, headers):
        calls.append(url)
        if url == nowcast_module.KMA_METAR_SOURCE_URL:
            return FakeResponse({}, status_code=503)
        return FakeResponse(awc_payload)

    provider = without_awc_bootstrap(
        AviationWeatherMetarNowcastProvider(
            http_get=fake_get,
            cache_ttl_seconds=60,
            request_log_path=tmp_path / "requests.jsonl",
            kma_metar_service_key="test-key",
            kma_metar_station_ids={"RKSI"},
        )
    )

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert calls == [nowcast_module.KMA_METAR_SOURCE_URL, nowcast_module.AVIATIONWEATHER_METAR_SOURCE_URL]
    assert observation.source == "aviationweather-metar"
    assert observation.latest_temp_c == 26.7


@pytest.mark.parametrize(
    "kma_payload",
    [
        {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                "body": {
                    "items": {
                        "item": [{
                            "icaoCode": "RKSI",
                            "metarMsg": "METAR RKPK 020830Z 03008KT CAVOK 31/24 Q1001 NOSIG=",
                        }]
                    }
                },
            }
        },
        {
            "response": {
                "body": {
                    "items": {
                        "item": [{
                            "icaoCode": "RKSI",
                            "metarMsg": "METAR RKSI 020830Z 03008KT CAVOK 31/24 Q1001 NOSIG=",
                        }]
                    }
                }
            }
        },
    ],
)
def test_invalid_kma_identity_or_status_falls_back_to_awc(tmp_path, kma_payload):
    awc_payload = load_fixture("aviationweather_rksi_fresh.json")

    def fake_get(url, *, params, timeout, headers):
        if url == nowcast_module.KMA_METAR_SOURCE_URL:
            return FakeResponse(kma_payload)
        return FakeResponse(awc_payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        kma_metar_service_key="test-key",
        kma_metar_station_ids={"RKSI"},
    )
    seed_complete_metar_day(
        provider,
        station_id="RKSI",
        local_date="2026-06-02",
        last_observed_at="2026-06-02T05:00:00+00:00",
        high_c=24.0,
        low_c=18.0,
    )

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.source == "aviationweather-metar"
    assert observation.latest_temp_c == 26.7


def test_awc_recovery_is_applied_before_newer_kma_report(tmp_path):
    kma_payload = {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {
                "items": {
                    "item": [{
                        "icaoCode": "RKSI",
                        "metarMsg": "METAR RKSI 170430Z 03008KT CAVOK 31/24 Q1001 NOSIG=",
                    }]
                }
            },
        }
    }
    awc_payload = [
        {
            "icaoId": "RKSI",
            "obsTime": f"2026-07-17T0{hour}:00:00.000Z",
            "temp": temp,
            "rawOb": f"METAR RKSI 170{hour}00Z 03008KT CAVOK {int(temp):02d}/24 Q1001",
        }
        for hour, temp in ((1, 25.0), (2, 27.0), (3, 29.0), (4, 30.0))
    ]
    calls: list[str] = []

    def fake_get(url, *, params, timeout, headers):
        calls.append(url)
        return FakeResponse(kma_payload if url == nowcast_module.KMA_METAR_SOURCE_URL else awc_payload)

    now = datetime(2026, 7, 17, 4, 30, 20, tzinfo=timezone.utc)
    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        kma_metar_service_key="test-key",
        kma_metar_station_ids={"RKSI"},
        clock=lambda: now,
    )
    seed_complete_metar_day(
        provider,
        station_id="RKSI",
        local_date="2026-07-17",
        last_observed_at="2026-07-17T00:00:00+00:00",
        high_c=24.0,
        low_c=24.0,
    )

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 7, 17),
        now=now,
    )

    assert calls == [nowcast_module.KMA_METAR_SOURCE_URL, nowcast_module.AVIATIONWEATHER_METAR_SOURCE_URL]
    assert observation.source == "kma-aviation-metar"
    assert observation.daily_extremes_complete is True
    assert observation.latest_temp_c == 31.0
    assert observation.observed_high_c == 31.0


def test_kma_internal_history_gap_requests_awc_recovery(tmp_path):
    provider = AviationWeatherMetarNowcastProvider()
    seed_complete_metar_day(
        provider,
        station_id="RKSI",
        local_date="2026-07-17",
        last_observed_at="2026-07-17T00:00:00+00:00",
        high_c=24.0,
        low_c=24.0,
    )
    rows = [
        {"icaoId": "RKSI", "obsTime": "2026-07-17T00:30:00Z", "temp": 25.0},
        {"icaoId": "RKSI", "obsTime": "2026-07-17T04:30:00Z", "temp": 31.0},
    ]

    assert provider._kma_recovery_required(
        STATION_MAP["seoul"], date(2026, 7, 17), rows
    ) is True


def test_older_kma_report_cannot_mix_with_newer_persisted_observation(tmp_path):
    kma_payload = {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {
                "items": {
                    "item": [{
                        "icaoCode": "RKSI",
                        "metarMsg": "METAR RKSI 170400Z 03008KT CAVOK 27/24 Q1001 NOSIG=",
                    }]
                }
            },
        }
    }
    awc_payload = [{
        "icaoId": "RKSI",
        "obsTime": "2026-07-17T04:30:00.000Z",
        "temp": 30.0,
        "rawOb": "METAR RKSI 170430Z 03008KT CAVOK 30/24 Q1001",
    }]
    calls: list[str] = []

    def fake_get(url, *, params, timeout, headers):
        calls.append(url)
        return FakeResponse(kma_payload if url == nowcast_module.KMA_METAR_SOURCE_URL else awc_payload)

    now = datetime(2026, 7, 17, 4, 31, tzinfo=timezone.utc)
    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        kma_metar_service_key="test-key",
        kma_metar_station_ids={"RKSI"},
        clock=lambda: now,
    )
    seed_complete_metar_day(
        provider,
        station_id="RKSI",
        local_date="2026-07-17",
        last_observed_at="2026-07-17T04:30:00+00:00",
        high_c=31.0,
        low_c=24.0,
    )

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"], target_date=date(2026, 7, 17), now=now
    )

    assert calls == [nowcast_module.KMA_METAR_SOURCE_URL, nowcast_module.AVIATIONWEATHER_METAR_SOURCE_URL]
    assert observation.source == "aviationweather-metar"
    assert observation.observed_at.isoformat() == "2026-07-17T04:30:00+00:00"
    assert observation.latest_temp_c == 30.0
    assert observation.observed_high_c == 31.0


def test_malformed_kma_for_one_station_does_not_suppress_another(tmp_path):
    awc_payload = [{
        "icaoId": "RKSI",
        "obsTime": "2026-07-17T04:30:00.000Z",
        "temp": 30.0,
        "rawOb": "METAR RKSI 170430Z 03008KT CAVOK 30/24 Q1001",
    }]
    requested_kma_stations: list[str] = []

    def fake_get(url, *, params, timeout, headers):
        if url != nowcast_module.KMA_METAR_SOURCE_URL:
            return FakeResponse(awc_payload)
        station_id = params["icao"]
        requested_kma_stations.append(station_id)
        raw_station_id = "RKPK" if station_id == "RKSI" else station_id
        return FakeResponse({
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                "body": {
                    "items": {
                        "item": [{
                            "icaoCode": station_id,
                            "metarMsg": (
                                f"METAR {raw_station_id} 170430Z 03008KT "
                                "CAVOK 31/24 Q1001 NOSIG="
                            ),
                        }]
                    }
                },
            }
        })

    now = datetime(2026, 7, 17, 4, 30, 20, tzinfo=timezone.utc)
    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=0,
        kma_metar_service_key="test-key",
        kma_metar_station_ids={"RKSI", "RKPK"},
        clock=lambda: now,
    )
    for station_id in ("RKSI", "RKPK"):
        seed_complete_metar_day(
            provider,
            station_id=station_id,
            local_date="2026-07-17",
            last_observed_at="2026-07-17T04:00:00+00:00",
            high_c=30.0,
            low_c=24.0,
        )

    provider.observed_high_so_far(
        STATION_MAP["seoul"], target_date=date(2026, 7, 17), now=now
    )
    busan = provider.observed_high_so_far(
        STATION_MAP["busan"], target_date=date(2026, 7, 17), now=now
    )

    assert requested_kma_stations == ["RKSI", "RKPK"]
    assert busan.source == "kma-aviation-metar"
    assert busan.latest_temp_c == 31.0


def test_kma_timeout_is_short_and_suppresses_same_cycle_retry(tmp_path):
    calls: list[tuple[str, float]] = []
    clock_now = [datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc)]

    def fake_get(url, *, params, timeout, headers):
        calls.append((url, timeout))
        if url == nowcast_module.KMA_METAR_SOURCE_URL:
            raise TimeoutError("KMA unavailable")
        return FakeResponse(load_fixture("aviationweather_rksi_fresh.json"))

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=0,
        kma_metar_service_key="test-key",
        kma_metar_timeout_seconds=3.0,
        kma_metar_station_ids={"RKSI", "RKPK"},
        clock=lambda: clock_now[0],
    )
    now = clock_now[0]

    provider.observed_high_so_far(STATION_MAP["seoul"], target_date=date(2026, 6, 2), now=now)
    provider.observed_high_so_far(STATION_MAP["busan"], target_date=date(2026, 6, 2), now=now)

    kma_calls = [call for call in calls if call[0] == nowcast_module.KMA_METAR_SOURCE_URL]
    assert kma_calls == [(nowcast_module.KMA_METAR_SOURCE_URL, 3.0)]

    clock_now[0] += timedelta(seconds=31)
    provider.observed_high_so_far(
        STATION_MAP["seoul"], target_date=date(2026, 6, 2), now=clock_now[0]
    )

    kma_calls = [call for call in calls if call[0] == nowcast_module.KMA_METAR_SOURCE_URL]
    assert kma_calls == [
        (nowcast_module.KMA_METAR_SOURCE_URL, 3.0),
        (nowcast_module.KMA_METAR_SOURCE_URL, 3.0),
    ]


def test_request_log_health_reports_write_failure(tmp_path):
    provider = AviationWeatherMetarNowcastProvider(request_log_path=tmp_path)

    provider._append_request_log({"request_mode": "test"})

    health = provider.request_log_health()
    assert health["status"] == "error"
    assert health["error"] in {"PermissionError", "IsADirectoryError"}
    assert health["last_success_at"] == ""


def test_aviationweather_provider_supports_verified_icao_station_beyond_seoul():
    provider, calls = provider_for(load_fixture("aviationweather_klga_fresh.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["nyc"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 18, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.station_id == "KLGA"
    assert observation.observed_high_c == 25.6
    assert observation.source == "aviationweather-metar"
    requested_ids = set(calls[0]["params"]["ids"].split(","))
    assert "KLGA" in requested_ids


def test_aviationweather_provider_marks_stale_observations_unusable():
    provider, _calls = provider_for(load_fixture("aviationweather_rksi_stale.json"), freshness_seconds=3600)

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c == 24.0
    assert observation.freshness_seconds == 10800
    assert observation.unavailable_reason == "stale-observation"


def test_aviationweather_provider_marks_malformed_payload_unusable():
    provider, _calls = provider_for(load_fixture("aviationweather_rksi_malformed.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_at is None
    assert observation.unavailable_reason == "malformed-observation-payload"


def test_aviationweather_provider_fails_closed_on_future_observation():
    payload = [
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-06T15:40:00.000Z",
            "temp": 31.0,
            "rawOb": "RJTT 061540Z 19008KT 9999 FEW025 31/20 Q1008",
        }
    ]
    provider, _calls = provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 7),
        now=datetime(2026, 6, 6, 15, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_low_c is None
    assert observation.unavailable_reason == "future-observation"


def test_aviationweather_provider_fails_closed_when_target_date_is_not_today():
    provider, calls = provider_for(load_fixture("aviationweather_rksi_fresh.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 3),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.unavailable_reason == "target-date-not-today"
    assert calls == []


def test_aviationweather_provider_uses_fresh_yesterday_extremes_after_local_midnight():
    payload = [
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-05T15:10:00.000Z",
            "temp": 20.0,
            "rawOb": "RJTT 051510Z 18005KT 9999 FEW020 20/16 Q1010",
        },
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-06T06:00:00.000Z",
            "temp": 31.0,
            "rawOb": "RJTT 060600Z 19008KT 9999 FEW025 31/20 Q1008",
        },
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-06T14:50:00.000Z",
            "temp": 26.0,
            "rawOb": "RJTT 061450Z 16005KT 9999 FEW020 26/19 Q1009",
        },
    ]
    provider, calls = provider_for(payload, freshness_seconds=5400)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 15, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.station_id == "RJTT"
    assert observation.observed_high_c == 31.0
    assert observation.observed_low_c == 20.0
    assert observation.observed_at.isoformat() == "2026-06-06T14:50:00+00:00"
    assert observation.freshness_seconds == 2400
    assert observation.unavailable_reason == ""
    assert calls[0]["params"]["hours"] == 4
    assert "hoursBeforeNow" not in calls[0]["params"]


def test_aviationweather_provider_learns_station_cadence_and_marks_publication_gap():
    payload = [
        {"icaoId": "RJTT", "obsTime": "2026-06-05T14:50:00Z", "temp": 21.0},
        {"icaoId": "RJTT", "obsTime": "2026-06-05T15:20:00Z", "temp": 22.0},
        {"icaoId": "RJTT", "obsTime": "2026-06-05T15:50:00Z", "temp": 23.0},
    ]
    provider, _calls = provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 5, 16, 23, tzinfo=timezone.utc),
    )

    assert observation.learned_observation_interval_seconds == 1800
    assert observation.next_observation_due_at.isoformat() == "2026-06-05T16:20:00+00:00"
    assert observation.observation_due_status == "overdue"


def test_aviationweather_provider_blocks_a_response_at_the_400_row_limit():
    record = {
        "icaoId": "RJTT",
        "obsTime": "2026-06-06T06:00:00.000Z",
        "temp": 31.0,
        "rawOb": "RJTT 060600Z 19008KT 9999 FEW025 31/20 Q1008",
    }
    provider, _calls = provider_for([dict(record, receiptTime=index) for index in range(400)])

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 6, 5, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.unavailable_reason == "metar-response-row-limit"


def test_aviationweather_provider_fails_closed_when_yesterday_window_expired():
    provider, calls = provider_for([], freshness_seconds=3600)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 16, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_low_c is None
    assert observation.unavailable_reason == "target-date-post-close-window-expired"
    assert calls == []


def test_aviationweather_provider_skips_station_without_verified_observation_source():
    provider, calls = provider_for(load_fixture("aviationweather_rksi_fresh.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["karachi"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.unavailable_reason == "nowcast-source-unmapped"
    assert calls == []


def hko_provider_for(
    payload: str,
    *,
    freshness_seconds: int = 5400,
    cache_ttl_seconds: int = 0,
    request_log_path: Path | None = None,
):
    calls = []

    def fake_get(url, *, params=None, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        return FakeResponse(payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        freshness_seconds=freshness_seconds,
        cache_ttl_seconds=cache_ttl_seconds,
        request_log_path=request_log_path,
    )
    return provider, calls


def hko_sequence_provider(payloads: list[str], *, state_path: Path):
    calls = []
    remaining = iter(payloads)

    def fake_get(url, *, params=None, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        return FakeResponse(next(remaining))

    return AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        freshness_seconds=5400,
        cache_ttl_seconds=0,
        hko_rollover_state_path=state_path,
    ), calls


def hko_csv(timestamp: str, high_c: float, low_c: float) -> str:
    return (
        "Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),"
        "Minimum Air Temperature Since Midnight(degree Celsius)\n"
        f"{timestamp},HK Observatory,{high_c},{low_c}\n"
    )


def test_hko_provider_returns_max_temperature_since_midnight_from_fixture():
    provider, calls = hko_provider_for(load_text_fixture("hko_maxmin_fresh.csv"))

    observation = provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.station_id == "HKO"
    assert observation.observed_high_c == 30.0
    assert observation.observed_at.isoformat() == "2026-06-02T03:30:00+00:00"
    assert observation.high_observed_at is None
    assert observation.observed_low_c == 27.6
    assert observation.freshness_seconds == 900
    assert observation.source == "hko-maxmin-since-midnight"
    assert "latest_since_midnight_maxmin.csv" in calls[0]["url"]


def test_hko_observation_older_than_two_update_cycles_is_stale():
    provider, _calls = hko_provider_for(
        hko_csv("202606021130", 30.0, 27.6),
        freshness_seconds=5400,
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 51, tzinfo=timezone.utc),
    )

    assert observation.freshness_seconds == 1260
    assert observation.usable is False
    assert observation.unavailable_reason == "stale-observation"


def test_hko_midnight_carryover_331_is_blocked_until_292_reset_is_proven(tmp_path):
    provider, calls = hko_sequence_provider(
        [
            hko_csv("202606212350", 33.1, 28.0),
            hko_csv("202606220000", 33.1, 28.0),
            hko_csv("202606220330", 29.2, 28.6),
        ],
        state_path=tmp_path / "hko_rollover_state.json",
    )

    previous = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 21),
        now=datetime(2026, 6, 21, 15, 55, tzinfo=timezone.utc),
    )
    carryover = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 21, 16, 10, tzinfo=timezone.utc),
    )
    reset = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 21, 19, 35, tzinfo=timezone.utc),
    )

    assert previous.usable is False
    assert previous.unavailable_reason == "hko-rollover-baseline-missing"
    assert carryover.usable is False
    assert carryover.observed_high_c == 33.1
    assert carryover.unavailable_reason == "hko-midnight-reset-pending"
    assert carryover.midnight_reset_status == "pending_previous_day_match"
    assert reset.usable is True
    assert reset.observed_high_c == 29.2
    assert reset.observed_low_c == 28.6
    assert reset.midnight_reset_status == "verified"
    assert len(calls) == 3


def test_hko_records_when_current_daily_extremes_were_first_confirmed(tmp_path):
    state_path = tmp_path / "hko_rollover_state.json"
    provider, _calls = hko_sequence_provider(
        [
            hko_csv("202606212350", 33.1, 28.0),
            hko_csv("202606220330", 29.2, 28.6),
            hko_csv("202606221200", 31.0, 28.6),
            hko_csv("202606221400", 31.0, 27.9),
        ],
        state_path=state_path,
    )
    station = STATION_MAP["hong kong"]
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 21), now=datetime(2026, 6, 21, 15, 55, tzinfo=timezone.utc)
    )
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 21, 19, 35, tzinfo=timezone.utc)
    )
    high_update = provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 22, 4, 5, tzinfo=timezone.utc)
    )
    low_update = provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 22, 6, 5, tzinfo=timezone.utc)
    )

    assert high_update.high_observed_at.isoformat() == "2026-06-22T04:00:00+00:00"
    assert high_update.low_observed_at.isoformat() == "2026-06-21T19:30:00+00:00"
    assert low_update.high_observed_at.isoformat() == "2026-06-22T04:00:00+00:00"
    assert low_update.low_observed_at.isoformat() == "2026-06-22T06:00:00+00:00"
    day = json.loads(state_path.read_text(encoding="utf-8"))["days"]["2026-06-22"]
    assert day == {
        "high_c": 31.0,
        "high_first_observed_at": "2026-06-22T04:00:00+00:00",
        "latest_observed_at": "2026-06-22T06:00:00+00:00",
        "low_c": 27.9,
        "low_first_observed_at": "2026-06-22T06:00:00+00:00",
    }


def test_hko_keeps_only_the_latest_two_local_dates(tmp_path):
    state_path = tmp_path / "hko_rollover_state.json"
    provider, _calls = hko_sequence_provider(
        [
            hko_csv("202606212350", 33.1, 28.0),
            hko_csv("202606220330", 29.2, 28.6),
            hko_csv("202606230330", 29.0, 28.8),
            hko_csv("202606240330", 28.5, 29.0),
        ],
        state_path=state_path,
    )
    station = STATION_MAP["hong kong"]
    for target_date, now in [
        (date(2026, 6, 21), datetime(2026, 6, 21, 15, 55, tzinfo=timezone.utc)),
        (date(2026, 6, 22), datetime(2026, 6, 21, 19, 35, tzinfo=timezone.utc)),
        (date(2026, 6, 23), datetime(2026, 6, 22, 19, 35, tzinfo=timezone.utc)),
        (date(2026, 6, 24), datetime(2026, 6, 23, 19, 35, tzinfo=timezone.utc)),
    ]:
        provider.observed_temperature_extremes_so_far(station, target_date=target_date, now=now)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert sorted(state["days"]) == ["2026-06-23", "2026-06-24"]


def test_hko_restart_without_previous_day_baseline_fails_closed(tmp_path):
    provider, _calls = hko_sequence_provider(
        [hko_csv("202606220330", 29.2, 28.6)],
        state_path=tmp_path / "missing" / "hko_rollover_state.json",
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 21, 19, 35, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.unavailable_reason == "hko-rollover-baseline-missing"
    assert observation.midnight_reset_status == "blocked_baseline_missing"


def test_hko_same_day_high_decrease_is_blocked_after_reset(tmp_path):
    provider, _calls = hko_sequence_provider(
        [
            hko_csv("202606212350", 33.1, 28.0),
            hko_csv("202606220330", 29.2, 28.6),
            hko_csv("202606220400", 29.0, 28.5),
        ],
        state_path=tmp_path / "hko_rollover_state.json",
    )
    station = STATION_MAP["hong kong"]
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 21), now=datetime(2026, 6, 21, 15, 55, tzinfo=timezone.utc)
    )
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 21, 19, 35, tzinfo=timezone.utc)
    )
    observation = provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 21, 20, 5, tzinfo=timezone.utc)
    )

    assert observation.usable is False
    assert observation.unavailable_reason == "hko-same-day-high-decreased"


def test_hko_same_day_low_increase_is_blocked_after_reset(tmp_path):
    provider, _calls = hko_sequence_provider(
        [
            hko_csv("202606212350", 33.1, 28.0),
            hko_csv("202606220330", 29.2, 28.6),
            hko_csv("202606220400", 29.3, 28.7),
        ],
        state_path=tmp_path / "hko_rollover_state.json",
    )
    station = STATION_MAP["hong kong"]
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 21), now=datetime(2026, 6, 21, 15, 55, tzinfo=timezone.utc)
    )
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 21, 19, 35, tzinfo=timezone.utc)
    )
    observation = provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 21, 20, 5, tzinfo=timezone.utc)
    )

    assert observation.usable is False
    assert observation.unavailable_reason == "hko-same-day-low-increased"


def test_hko_provider_uses_fresh_yesterday_extremes_after_local_midnight():
    payload = """Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),Minimum Air Temperature Since Midnight(degree Celsius)
202606062350,HK Observatory,30.2,27.1
"""
    provider, calls = hko_provider_for(payload, freshness_seconds=5400)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 16, 5, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.station_id == "HKO"
    assert observation.observed_high_c == 30.2
    assert observation.observed_low_c == 27.1
    assert observation.observed_at.isoformat() == "2026-06-06T15:50:00+00:00"
    assert observation.freshness_seconds == 900
    assert observation.unavailable_reason == ""
    assert len(calls) == 1


def test_hko_request_log_records_external_fetch_not_cache_hit(tmp_path):
    request_log_path = tmp_path / "station_nowcast_request_log.jsonl"
    provider, calls = hko_provider_for(
        load_text_fixture("hko_maxmin_fresh.csv"),
        cache_ttl_seconds=900,
        request_log_path=request_log_path,
    )
    provider.clock = lambda: datetime(2026, 6, 2, 3, 45, 2, tzinfo=timezone.utc)

    provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
    )
    provider.observed_low_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 46, tzinfo=timezone.utc),
    )

    rows = read_jsonl(request_log_path)

    assert len(calls) == 1
    request_rows = [row for row in rows if row.get("request_mode") != "observation_delivery"]
    delivery_rows = [row for row in rows if row.get("request_mode") == "observation_delivery"]
    assert len(request_rows) == 1
    assert len(delivery_rows) == 1
    assert request_rows[0]["city"] == "hong kong"
    assert request_rows[0]["station_id"] == STATION_MAP["hong kong"].station_id
    assert request_rows[0]["source"] == "hko-maxmin-since-midnight"
    assert request_rows[0]["status"] == "success"
    assert request_rows[0]["status_code"] == 200
    assert request_rows[0]["cache_miss_reason"] == "empty-cache"
    assert request_rows[0]["requested_at"] == "2026-06-02T03:45:02+00:00"
    assert delivery_rows[0]["station_id"] == "HKO"
    assert delivery_rows[0]["observation_observed_at"] == "2026-06-02T03:30:00+00:00"
    assert delivery_rows[0]["request_started_at"] == "2026-06-02T03:45:02+00:00"
    assert delivery_rows[0]["bot_detection_latency_seconds"] == 902
    assert [row["request_mode"] for row in rows] == [
        "station_fetch",
        "observation_delivery",
    ]


def test_hko_request_floor_is_ten_minutes_even_when_station_cache_is_short(tmp_path):
    request_log_path = tmp_path / "station_nowcast_request_log.jsonl"
    clock_now = [datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc)]
    provider, calls = hko_provider_for(
        load_text_fixture("hko_maxmin_fresh.csv"),
        cache_ttl_seconds=60,
        request_log_path=request_log_path,
    )
    provider.clock = lambda: clock_now[0]

    provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
    )
    provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 50, tzinfo=timezone.utc),
    )
    clock_now[0] = datetime(2026, 6, 2, 3, 56, tzinfo=timezone.utc)
    provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 56, tzinfo=timezone.utc),
    )

    rows = [
        row
        for row in read_jsonl(request_log_path)
        if row.get("request_mode") != "observation_delivery"
    ]
    assert len(calls) == 2
    assert len(rows) == 2
    assert rows[0]["requested_at"] == "2026-06-02T03:45:00+00:00"
    assert rows[1]["requested_at"] == "2026-06-02T03:56:00+00:00"


def test_slow_hko_request_does_not_block_metar_refresh():
    hko_started = threading.Event()
    release_hko = threading.Event()
    metar_finished = threading.Event()
    errors: list[BaseException] = []
    awc_payload = load_fixture("aviationweather_rksi_fresh.json")
    hko_payload = load_text_fixture("hko_maxmin_fresh.csv")

    def fake_get(url, *, params, timeout, headers):
        del params, timeout, headers
        if "weather.gov.hk" in url:
            hko_started.set()
            assert release_hko.wait(2)
            return FakeResponse(hko_payload)
        return FakeResponse(awc_payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=0,
    )

    def refresh_hko() -> None:
        try:
            provider.observed_high_so_far(
                STATION_MAP["hong kong"],
                target_date=date(2026, 6, 2),
                now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def refresh_metar() -> None:
        try:
            provider.discard_cached_observations_before_entry(
                station_ids={"RKSI"},
                now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
            )
            provider.observed_high_so_far(
                STATION_MAP["seoul"],
                target_date=date(2026, 6, 2),
                now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
            )
            metar_finished.set()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    hko_thread = threading.Thread(target=refresh_hko)
    metar_thread = threading.Thread(target=refresh_metar)
    hko_thread.start()
    assert hko_started.wait(1)
    metar_thread.start()

    assert metar_finished.wait(0.5)
    release_hko.set()
    metar_thread.join(1)
    hko_thread.join(1)
    assert not metar_thread.is_alive()
    assert not hko_thread.is_alive()
    assert errors == []


def test_hko_provider_marks_malformed_csv_unusable():
    provider, _calls = hko_provider_for(load_text_fixture("hko_maxmin_malformed.csv"))

    observation = provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.unavailable_reason == "malformed-observation-payload"


def test_hko_provider_fails_closed_on_future_observation():
    payload = """Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),Minimum Air Temperature Since Midnight(degree Celsius)
202606062350,HK Observatory,30.2,27.1
"""
    provider, _calls = hko_provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 15, 20, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_low_c is None
    assert observation.unavailable_reason == "future-observation"


def test_station_nowcast_audit_marks_enabled_and_skipped_sources():
    rows = {row["city"]: row for row in station_audit_rows()}

    assert rows["hong kong"]["nowcast_source_type"] == "hko_maxmin_since_midnight"
    assert rows["hong kong"]["nowcast_provider_status"] == "provider_enabled"
    assert rows["seoul"]["nowcast_provider_status"] == "provider_enabled"
    assert rows["nyc"]["nowcast_provider_status"] == "provider_enabled"
    assert rows["karachi"]["nowcast_provider_status"] == "provider_unavailable"


def test_default_nowcast_sources_match_enabled_station_registry():
    enabled_station_ids = {
        str(row["station_id"])
        for row in station_audit_rows()
        if row["nowcast_provider_status"] == "provider_enabled"
    }

    assert set(DEFAULT_NOWCAST_SOURCES) == enabled_station_ids
    assert "HKO" in DEFAULT_NOWCAST_SOURCES
    assert "OPMR" not in DEFAULT_NOWCAST_SOURCES
