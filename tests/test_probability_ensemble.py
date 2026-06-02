from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from weather_bot.config import Settings
from weather_bot.probability import (
    OpenMeteoEnsembleClient,
    STATION_MAP,
    _extract_member_values,
    _station_for,
    _today_for_timezone,
    blend_empirical_and_cdf,
    dynamic_sigma_f,
    estimate_weather_probability,
)
from weather_bot.nowcast import StationNowcastObservation
from weather_bot.weather_client import parse_weather_question


def test_extract_member_values_accepts_suffixed_keys_and_bias():
    daily = {
        "time": ["2026-05-23"],
        "temperature_2m_max": [80],
        "temperature_2m_max_member01": [82],
        "temperature_2m_max_gfs_member02": [84],
        "temperature_2m_min": [60],
    }
    vals = _extract_member_values(daily, "temperature_2m_max", 0, bias_f=1.0)
    assert vals == [79.0, 81.0, 83.0]


def test_station_map_contains_only_verified_polymarket_cities():
    assert len(STATION_MAP) == 41
    assert STATION_MAP["seoul"].station_id == "RKSI"
    assert STATION_MAP["london"].station_id == "EGLC"
    assert STATION_MAP["nyc"].station_id == "KLGA"
    assert STATION_MAP["hong kong"].station_name == "Hong Kong Observatory"


def test_unverified_city_is_not_parsed_or_mapped_for_trading():
    parsed = parse_weather_question("Will Austin be 92 F or higher on May 25?")

    assert parsed.city is None
    assert _station_for(parsed) is None


def test_dynamic_sigma_has_floor_and_uses_spread():
    assert dynamic_sigma_f([70, 70, 70], lead_days=0) >= 1.25
    assert dynamic_sigma_f([60, 70, 80], lead_days=2) > dynamic_sigma_f([70, 70, 70], lead_days=0)


def test_blend_probability_is_bounded():
    p = blend_empirical_and_cdf(0.8, mean_f=75, threshold_f=72, sigma_f=3, operator=">=")
    assert 0 <= p <= 1
    assert p > 0.6


def test_timezone_fallback_uses_winter_new_york_offset(monkeypatch):
    def broken_zoneinfo(_name):
        raise RuntimeError("tzdata unavailable")

    monkeypatch.setattr("weather_bot.probability.ZoneInfo", broken_zoneinfo)

    winter_utc = datetime(2026, 1, 2, 4, 30, tzinfo=timezone.utc)

    assert _today_for_timezone("America/New_York", winter_utc) == date(2026, 1, 1)


def test_snow_market_uses_ensemble_snowfall_probability():
    target = _today_for_timezone("America/New_York")

    class FakeEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **_kwargs):
            return {
                "daily": {
                    "time": [target.isoformat()],
                    "snowfall_sum": [0.2],
                    "snowfall_sum_member01": [0.0],
                    "snowfall_sum_member02": [0.3],
                    "snowfall_sum_member03": [0.4],
                }
            }

    signal = estimate_weather_probability(
        "Will NYC get any snow today?",
        ensemble_client=FakeEnsembleClient(),
    )

    assert signal.source == "open-meteo-ensemble-snow"
    assert signal.p_true > 0.5
    assert signal.confidence > 0.0


def test_ensemble_client_caches_identical_forecast_requests(monkeypatch):
    calls = 0

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"daily": {"time": ["2026-05-25"]}}

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    monkeypatch.setattr("weather_bot.probability.requests.get", fake_get)
    client = OpenMeteoEnsembleClient()

    first = client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)
    second = client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)

    assert first == second
    assert calls == 1


def test_ensemble_client_refreshes_expired_memory_cache(monkeypatch):
    now = [datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)]
    calls = 0

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"daily": {"time": ["2026-06-01"], "calls": calls}}

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    monkeypatch.setattr("weather_bot.probability._utc_now", lambda: now[0])
    monkeypatch.setattr("weather_bot.probability.requests.get", fake_get)
    client = OpenMeteoEnsembleClient(cache_ttl_seconds=60)

    first = client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)
    now[0] += timedelta(seconds=59)
    second = client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)
    now[0] += timedelta(seconds=2)
    third = client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)

    assert first == second
    assert third != second
    assert calls == 2


def test_ensemble_client_health_reports_stale_cache_and_persistence_error(monkeypatch, tmp_path):
    now = [datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"daily": {"time": ["2026-06-01"]}}

    cache_path = tmp_path / "forecast-cache-dir"
    cache_path.mkdir()
    monkeypatch.setattr("weather_bot.probability._utc_now", lambda: now[0])
    monkeypatch.setattr("weather_bot.probability.requests.get", lambda *_args, **_kwargs: FakeResponse())
    client = OpenMeteoEnsembleClient(cache_path=cache_path, cache_ttl_seconds=60)

    client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)
    fresh = client.health_snapshot()
    now[0] += timedelta(seconds=61)
    stale = client.health_snapshot()

    assert fresh["last_attempt_at"] == "2026-06-01T00:00:00+00:00"
    assert fresh["last_success_at"] == "2026-06-01T00:00:00+00:00"
    assert fresh["last_failure_reason"] == ""
    assert fresh["cache_age_seconds"] == 0
    assert fresh["stale"] is False
    assert fresh["persistence_error"]
    assert "Error" in fresh["persistence_error"]
    assert stale["cache_age_seconds"] == 61
    assert stale["stale"] is True


def test_ensemble_rate_limit_is_not_retried_and_disables_later_calls(monkeypatch):
    calls = 0

    class RateLimitedResponse:
        status_code = 429
        text = "Daily API request limit exceeded"

        def raise_for_status(self):
            err = requests.HTTPError("429 Client Error")
            err.response = self
            raise err

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return RateLimitedResponse()

    monkeypatch.setattr("weather_bot.probability.requests.get", fake_get)
    client = OpenMeteoEnsembleClient()

    with pytest.raises(requests.HTTPError):
        client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)
    assert calls == 1
    assert client.disabled_reason

    with pytest.raises(RuntimeError, match="disabled"):
        client.forecast_daily_ensemble(3.0, 4.0, timezone="UTC", forecast_days=3)
    assert calls == 1


def test_ensemble_client_persists_successful_forecasts_to_disk(monkeypatch, tmp_path):
    calls = 0

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"daily": {"time": ["2026-05-25"], "temperature_2m_max": [80.0]}}

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    cache_path = tmp_path / "forecast_cache.json"
    monkeypatch.setattr("weather_bot.probability.requests.get", fake_get)

    first_client = OpenMeteoEnsembleClient(cache_path=cache_path, cache_ttl_seconds=21600)
    first = first_client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)

    def fail_get(*_args, **_kwargs):
        raise AssertionError("disk cache should avoid network")

    monkeypatch.setattr("weather_bot.probability.requests.get", fail_get)
    second_client = OpenMeteoEnsembleClient(cache_path=cache_path, cache_ttl_seconds=21600)
    second = second_client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)

    assert first == second
    assert calls == 1


def test_ensemble_client_can_read_cached_forecast_after_rate_limit(monkeypatch, tmp_path):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"daily": {"time": ["2026-05-25"], "temperature_2m_max": [80.0]}}

    cache_path = tmp_path / "forecast_cache.json"
    monkeypatch.setattr("weather_bot.probability.requests.get", lambda *_args, **_kwargs: FakeResponse())
    client = OpenMeteoEnsembleClient(cache_path=cache_path, cache_ttl_seconds=21600)
    cached = client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)

    class RateLimitedResponse:
        status_code = 429
        text = "Daily API request limit exceeded"

        def raise_for_status(self):
            err = requests.HTTPError("429 Client Error")
            err.response = self
            raise err

    monkeypatch.setattr("weather_bot.probability.requests.get", lambda *_args, **_kwargs: RateLimitedResponse())
    with pytest.raises(requests.HTTPError):
        client.forecast_daily_ensemble(3.0, 4.0, timezone="UTC", forecast_days=3)

    assert client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3) == cached


def test_ensemble_client_recovers_from_invalid_cache_file(monkeypatch, tmp_path):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"daily": {"time": ["2026-05-25"], "temperature_2m_max": [80.0]}}

    cache_path = tmp_path / "forecast_cache.json"
    cache_path.write_text("", encoding="utf-8")
    monkeypatch.setattr("weather_bot.probability.requests.get", lambda *_args, **_kwargs: FakeResponse())

    client = OpenMeteoEnsembleClient(cache_path=cache_path, cache_ttl_seconds=21600)
    data = client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)

    assert data["daily"]["temperature_2m_max"] == [80.0]
    assert "temperature_2m_max" in cache_path.read_text(encoding="utf-8")


def test_temperature_ensemble_failure_does_not_call_deterministic_fallback():
    class FailingEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **_kwargs):
            raise RuntimeError("rate limited")

    class ForbiddenDeterministicClient:
        def forecast_daily(self, *_args, **_kwargs):
            raise AssertionError("deterministic forecast fallback must not be called")

    signal = estimate_weather_probability(
        "Will Dallas be 92°F or higher on May 25?",
        settings=Settings(),
        client=ForbiddenDeterministicClient(),
        ensemble_client=FailingEnsembleClient(),
    )

    assert signal.source == "forecast-unavailable"
    assert signal.confidence == 0.0
    assert signal.p_true == 0.5
    assert "rate limited" in signal.note


def test_temperature_ensemble_failure_is_not_strategy_data():
    class FailingEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **_kwargs):
            raise RuntimeError("rate limited")

    class ForbiddenDeterministicClient:
        def forecast_daily(self, *_args, **_kwargs):
            raise AssertionError("deterministic forecast fallback must not be called")

    signal = estimate_weather_probability(
        "Will the highest temperature in Seoul be 25°C or higher on May 26?",
        settings=Settings(),
        client=ForbiddenDeterministicClient(),
        ensemble_client=FailingEnsembleClient(),
    )

    assert signal.source == "forecast-unavailable"
    assert signal.confidence == 0.0
    assert "Ensemble forecast unavailable" in signal.note


def test_missing_exact_target_forecast_date_is_not_replaced_by_nearest_date():
    target = _today_for_timezone("Asia/Seoul")
    wrong_date = target - timedelta(days=2)

    class FakeEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **_kwargs):
            return {
                "daily": {
                    "time": [wrong_date.isoformat()],
                    "temperature_2m_max": [100.0],
                    "temperature_2m_max_member01": [101.0],
                    "temperature_2m_max_member02": [102.0],
                    "temperature_2m_max_member03": [103.0],
                }
            }

    signal = estimate_weather_probability(
        "Will the highest temperature in Seoul be 27C or higher today?",
        settings=Settings(),
        ensemble_client=FakeEnsembleClient(),
    )

    assert signal.source == "forecast-unavailable"
    assert signal.confidence == 0.0
    assert signal.p_true == 0.5
    assert target.isoformat() in signal.note
    assert wrong_date.isoformat() not in signal.note


def test_highest_temperature_below_uses_daily_max_not_daily_min():
    target = _today_for_timezone("Europe/London")

    class FakeEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **_kwargs):
            return {
                "daily": {
                    "time": [target.isoformat()],
                    "temperature_2m_max": [80.0],
                    "temperature_2m_max_member01": [81.0],
                    "temperature_2m_max_member02": [82.0],
                    "temperature_2m_max_member03": [83.0],
                    "temperature_2m_min": [55.0],
                    "temperature_2m_min_member01": [56.0],
                    "temperature_2m_min_member02": [57.0],
                    "temperature_2m_min_member03": [58.0],
                }
            }

    signal = estimate_weather_probability(
        "Will the highest temperature in London be 21°C or below today?",
        settings=Settings(),
        ensemble_client=FakeEnsembleClient(),
    )

    assert signal.source == "open-meteo-ensemble-station"
    assert signal.p_true < 0.20


def test_multi_bucket_temperature_probabilities_share_one_consistent_distribution():
    target = _today_for_timezone("Asia/Seoul")
    member_values_c = [17.0, 18.2, 19.4, 20.6, 21.8, 23.0, 24.2, 25.4, 26.6, 27.8, 29.0]
    member_values_f = [value * 9.0 / 5.0 + 32.0 for value in member_values_c]

    class FakeEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **_kwargs):
            daily = {"time": [target.isoformat()], "temperature_2m_max": [member_values_f[0]]}
            daily.update(
                {
                    f"temperature_2m_max_member{idx:02d}": [value]
                    for idx, value in enumerate(member_values_f[1:], start=1)
                }
            )
            return {"daily": daily}

    questions = [
        "Will the highest temperature in Seoul be 18°C or below today?",
        *[
            f"Will the highest temperature in Seoul be {value}°C today?"
            for value in range(19, 28)
        ],
        "Will the highest temperature in Seoul be 28°C or higher today?",
    ]

    signals = [
        estimate_weather_probability(question, settings=Settings(), ensemble_client=FakeEnsembleClient())
        for question in questions
    ]

    assert all(signal.source == "open-meteo-ensemble-station" for signal in signals)
    assert sum(signal.p_true for signal in signals) == pytest.approx(1.0)


def test_temperature_nowcast_can_confirm_threshold_crossing_without_city_fallback():
    target = _today_for_timezone("Asia/Seoul")
    member_values_f = [75.2, 76.0, 76.8, 77.0]

    class FakeEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **_kwargs):
            daily = {"time": [target.isoformat()], "temperature_2m_max": [member_values_f[0]]}
            daily.update(
                {
                    f"temperature_2m_max_member{idx:02d}": [value]
                    for idx, value in enumerate(member_values_f[1:], start=1)
                }
            )
            return {"daily": daily}

    class FreshNowcastProvider:
        def observed_high_so_far(self, station, *, target_date, now=None):
            assert station.station_id == "RKSI"
            assert target_date == target
            return StationNowcastObservation(
                station_id="RKSI",
                station_name=station.station_name,
                observed_high_c=27.0,
                observed_at=datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
                high_observed_at=datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
                source="aviationweather-metar",
                source_url="https://aviationweather.gov/api/data/metar",
                settlement_source_url="https://www.wunderground.com/history/daily/kr/incheon/RKSI",
                freshness_seconds=1800,
                unavailable_reason="",
                raw_observation_count=4,
                update_cadence="fixture",
            )

    signal = estimate_weather_probability(
        "Will the highest temperature in Seoul be 27C or higher today?",
        settings=Settings(),
        ensemble_client=FakeEnsembleClient(),
        observation_provider=FreshNowcastProvider(),
    )

    assert signal.source == "open-meteo-ensemble-station+nowcast"
    assert signal.p_true == 1.0
    assert signal.confidence >= 0.95
    assert signal.nowcast["observed_high_c"] == 27.0
    assert "evidence=forecast-plus-nowcast" in signal.note


def test_temperature_nowcast_unavailable_keeps_forecast_only_signal():
    target = _today_for_timezone("Asia/Seoul")

    class FakeEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **_kwargs):
            return {
                "daily": {
                    "time": [target.isoformat()],
                    "temperature_2m_max": [80.0],
                    "temperature_2m_max_member01": [81.0],
                    "temperature_2m_max_member02": [82.0],
                    "temperature_2m_max_member03": [83.0],
                }
            }

    class StaleNowcastProvider:
        def observed_high_so_far(self, station, *, target_date, now=None):
            return StationNowcastObservation(
                station_id=station.station_id,
                station_name=station.station_name,
                observed_high_c=24.0,
                observed_at=datetime(2026, 6, 2, 5, 0, tzinfo=timezone.utc),
                high_observed_at=datetime(2026, 6, 2, 5, 0, tzinfo=timezone.utc),
                source="aviationweather-metar",
                source_url="https://aviationweather.gov/api/data/metar",
                settlement_source_url="https://www.wunderground.com/history/daily/kr/incheon/RKSI",
                freshness_seconds=10800,
                unavailable_reason="stale-observation",
                raw_observation_count=2,
                update_cadence="fixture",
            )

    signal = estimate_weather_probability(
        "Will the highest temperature in Seoul be 27C or higher today?",
        settings=Settings(),
        ensemble_client=FakeEnsembleClient(),
        observation_provider=StaleNowcastProvider(),
    )

    assert signal.source == "open-meteo-ensemble-station"
    assert 0.0 < signal.p_true < 1.0
    assert signal.nowcast["unavailable_reason"] == "stale-observation"
    assert "evidence=forecast-only" in signal.note
    assert "nowcast_unavailable=stale-observation" in signal.note
