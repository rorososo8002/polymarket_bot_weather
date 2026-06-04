import json
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from weather_bot.config import Settings
from weather_bot.probability import (
    OpenMeteoEnsembleClient,
    STATION_MAP,
    WeatherBiasLoadError,
    _extract_member_values,
    _station_for,
    _temperature_bucket_probability,
    _today_for_timezone,
    blend_empirical_and_cdf,
    dynamic_sigma_f,
    estimate_weather_probability,
    load_bias_table,
)
from weather_bot.live_paper_runner import evaluate_market
from weather_bot.models import RawMarket
from weather_bot.nowcast import StationNowcastObservation
from weather_bot.weather_client import c_to_f, parse_weather_question


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


def test_bias_table_uses_defaults_when_weather_bias_json_is_empty(monkeypatch):
    monkeypatch.delenv("WEATHER_BIAS_JSON", raising=False)

    table = load_bias_table()

    assert table["RKSI"]["temperature_2m_max"] == 0.0
    assert table["RKSI"]["temperature_2m_min"] == 0.0


def test_bias_table_rejects_missing_explicit_weather_bias_json(monkeypatch, tmp_path):
    missing_path = tmp_path / "missing-bias.json"
    monkeypatch.setenv("WEATHER_BIAS_JSON", str(missing_path))

    with pytest.raises(WeatherBiasLoadError, match="WEATHER_BIAS_JSON") as exc_info:
        load_bias_table()

    assert str(missing_path) in str(exc_info.value)


def test_bias_table_rejects_broken_explicit_weather_bias_json(monkeypatch, tmp_path):
    bias_path = tmp_path / "broken-bias.json"
    bias_path.write_text("{", encoding="utf-8")
    monkeypatch.setenv("WEATHER_BIAS_JSON", str(bias_path))

    with pytest.raises(WeatherBiasLoadError, match="WEATHER_BIAS_JSON") as exc_info:
        load_bias_table()

    assert "invalid JSON" in str(exc_info.value)


def test_bias_table_loads_valid_explicit_weather_bias_json(monkeypatch, tmp_path):
    bias_path = tmp_path / "bias.json"
    bias_path.write_text('{"RKSI": {"temperature_2m_max": 1.25}}', encoding="utf-8")
    monkeypatch.setenv("WEATHER_BIAS_JSON", str(bias_path))

    table = load_bias_table()

    assert table["RKSI"]["temperature_2m_max"] == 1.25
    assert table["RKSI"]["temperature_2m_min"] == 0.0


def test_broken_explicit_bias_table_blocks_temperature_entry(monkeypatch, tmp_path):
    bias_path = tmp_path / "broken-bias.json"
    bias_path.write_text("{", encoding="utf-8")
    monkeypatch.setenv("WEATHER_BIAS_JSON", str(bias_path))
    question = "Will the highest temperature in Seoul be 27C or higher today?"

    class ForbiddenEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **_kwargs):
            raise AssertionError("broken bias table should block before forecast fetch")

    signal = estimate_weather_probability(
        question,
        settings=Settings(),
        ensemble_client=ForbiddenEnsembleClient(),
    )

    assert signal.source == "forecast-unavailable"
    assert signal.confidence == 0.0
    assert "WEATHER_BIAS_JSON" in signal.note

    market = RawMarket("m1", question, "m1", True, False, "yes-token", "no-token")

    class ForbiddenOrderBookClient:
        def get_order_book(self, _token_id):
            raise AssertionError("forecast-unavailable signal should skip before order books")

    result, per_side = evaluate_market(
        market,
        signal,
        ForbiddenOrderBookClient(),
        Settings(),
        100.0,
        "temperature",
    )

    assert result.side == "SKIP"
    assert result.size_usd == 0.0
    assert per_side == {}
    assert "confidence too low" in result.reason


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


def test_supported_city_without_rule_evidence_is_not_mapped_for_trading(monkeypatch):
    monkeypatch.setattr("weather_bot.probability.TRADING_READY_STATION_MAP", {}, raising=False)
    parsed = parse_weather_question("Will NYC be 90 F or higher today?")

    assert parsed.city == "nyc"
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


def test_snow_market_is_unsupported_and_does_not_fetch_forecast():
    calls = 0

    class ForbiddenEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("snow markets are outside the temperature-only strategy")

    signal = estimate_weather_probability(
        "Will NYC get any snow today?",
        settings=Settings(),
        ensemble_client=ForbiddenEnsembleClient(),
    )

    assert calls == 0
    assert signal.source == "unsupported-weather-market"
    assert signal.confidence == 0.0
    assert "Unsupported non-temperature weather market" in signal.note


def test_ensemble_client_requests_temperature_daily_variables_only(monkeypatch, tmp_path):
    captured_daily_params: list[str] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"daily": {"time": ["2026-06-01"], "temperature_2m_max": [80.0]}}

    def fake_get(_url, *, params, timeout):
        captured_daily_params.append(params["daily"])
        return FakeResponse()

    monkeypatch.setattr("weather_bot.probability.requests.get", fake_get)
    client = OpenMeteoEnsembleClient.from_settings(
        Settings(
            state_path=str(tmp_path / "state.json"),
        )
    )

    client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)

    assert len(captured_daily_params) == 1
    daily = captured_daily_params[0]
    assert daily == "temperature_2m_max,temperature_2m_min"


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
    request_log_path = tmp_path / "forecast_request_log.jsonl"
    monkeypatch.setattr("weather_bot.probability.requests.get", fake_get)

    first_client = OpenMeteoEnsembleClient(
        cache_path=cache_path,
        cache_ttl_seconds=21600,
        request_log_path=request_log_path,
    )
    first = first_client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)

    def fail_get(*_args, **_kwargs):
        raise AssertionError("disk cache should avoid network")

    monkeypatch.setattr("weather_bot.probability.requests.get", fail_get)
    second_client = OpenMeteoEnsembleClient(
        cache_path=cache_path,
        cache_ttl_seconds=21600,
        request_log_path=request_log_path,
    )
    second = second_client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)

    rows = [json.loads(line) for line in request_log_path.read_text(encoding="utf-8").splitlines()]
    assert first == second
    assert calls == 1
    assert len(rows) == 1
    assert rows[0]["cache_miss_reason"] == "disk-cache-missing"


def test_ensemble_client_logs_successful_network_attempts(monkeypatch, tmp_path):
    now = [datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)]
    calls = 0

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"daily": {"time": ["2026-06-01"], "calls": calls}}

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    request_log_path = tmp_path / "forecast_request_log.jsonl"
    monkeypatch.setattr("weather_bot.probability._utc_now", lambda: now[0])
    monkeypatch.setattr("weather_bot.probability.requests.get", fake_get)
    client = OpenMeteoEnsembleClient(
        cache_ttl_seconds=60,
        request_log_path=request_log_path,
    )

    client.forecast_daily_ensemble(
        1.0,
        2.0,
        timezone="UTC",
        forecast_days=3,
        city="test city",
        station_id="TEST",
        station_name="Test Station",
    )
    client.forecast_daily_ensemble(
        1.0,
        2.0,
        timezone="UTC",
        forecast_days=3,
        city="test city",
        station_id="TEST",
        station_name="Test Station",
    )

    rows = [json.loads(line) for line in request_log_path.read_text(encoding="utf-8").splitlines()]
    assert calls == 1
    assert len(rows) == 1
    assert rows[0]["attempted_at"] == "2026-06-01T00:00:00+00:00"
    assert rows[0]["status"] == "success"
    assert rows[0]["status_code"] == 200
    assert rows[0]["forecast_days"] == 3
    assert rows[0]["latitude"] == 1.0
    assert rows[0]["longitude"] == 2.0
    assert rows[0]["timezone"] == "UTC"
    assert rows[0]["cache_miss_reason"] == "cache-not-configured"
    assert rows[0]["city"] == "test city"
    assert rows[0]["station_id"] == "TEST"
    assert rows[0]["station_name"] == "Test Station"


def test_ensemble_client_logs_rate_limit_network_attempt(monkeypatch, tmp_path):
    now = [datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)]

    class RateLimitedResponse:
        status_code = 429
        text = "Daily API request limit exceeded"

        def raise_for_status(self):
            err = requests.HTTPError("429 Client Error")
            err.response = self
            raise err

    request_log_path = tmp_path / "forecast_request_log.jsonl"
    monkeypatch.setattr("weather_bot.probability._utc_now", lambda: now[0])
    monkeypatch.setattr("weather_bot.probability.requests.get", lambda *_args, **_kwargs: RateLimitedResponse())
    client = OpenMeteoEnsembleClient(request_log_path=request_log_path)

    with pytest.raises(requests.HTTPError):
        client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)

    rows = [json.loads(line) for line in request_log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["status"] == "http_error"
    assert rows[0]["status_code"] == 429
    assert rows[0]["error"].startswith("Open-Meteo rate limited")


def test_ensemble_client_persists_rate_limit_cooldown_across_clients(monkeypatch, tmp_path):
    now = [datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)]
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

    request_log_path = tmp_path / "forecast_request_log.jsonl"
    rate_limit_state_path = tmp_path / "forecast_rate_limit_state.json"
    monkeypatch.setattr("weather_bot.probability._utc_now", lambda: now[0])
    monkeypatch.setattr("weather_bot.probability.requests.get", fake_get)

    first_client = OpenMeteoEnsembleClient(
        request_log_path=request_log_path,
        rate_limit_state_path=rate_limit_state_path,
    )
    with pytest.raises(requests.HTTPError):
        first_client.forecast_daily_ensemble(1.0, 2.0, timezone="UTC", forecast_days=3)

    second_client = OpenMeteoEnsembleClient(
        request_log_path=request_log_path,
        rate_limit_state_path=rate_limit_state_path,
    )
    with pytest.raises(RuntimeError, match="rate limited until"):
        second_client.forecast_daily_ensemble(3.0, 4.0, timezone="UTC", forecast_days=3)

    rows = [json.loads(line) for line in request_log_path.read_text(encoding="utf-8").splitlines()]
    state = json.loads(rate_limit_state_path.read_text(encoding="utf-8"))
    assert calls == 1
    assert len(rows) == 1
    assert rows[0]["status_code"] == 429
    assert state["blocked_until"] == "2026-06-02T00:15:00+00:00"
    assert second_client.health_snapshot()["rate_limit_blocked_until"] == "2026-06-02T00:15:00+00:00"


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


def test_range_temperature_bucket_uses_exact_inclusive_fahrenheit_bounds():
    parsed = parse_weather_question("Will the highest temperature in Atlanta be 86-87F today?")
    member_values_f = [85.999, 86.0, 86.5, 87.0, 87.001]

    _probability, empirical_p = _temperature_bucket_probability(
        parsed,
        member_values_f,
        mean_f=sum(member_values_f) / len(member_values_f),
        sigma_f=1.25,
    )

    assert empirical_p == pytest.approx(3 / 5)


@pytest.mark.parametrize(
    ("temperature_f", "expected_vote"),
    [
        (85.999, 0.0),
        (86.0, 1.0),
        (86.2, 1.0),
        (86.5, 1.0),
        (86.999, 1.0),
        (87.0, 1.0),
        (87.001, 0.0),
    ],
)
def test_range_temperature_bucket_applies_general_fahrenheit_inequality(temperature_f, expected_vote):
    parsed = parse_weather_question("Will the highest temperature in Atlanta be 86-87F today?")

    _probability, empirical_p = _temperature_bucket_probability(
        parsed,
        [temperature_f],
        mean_f=temperature_f,
        sigma_f=1.25,
    )

    assert empirical_p == expected_vote


def test_range_temperature_bucket_uses_exact_converted_celsius_bounds_without_rounding():
    parsed = parse_weather_question("Will the highest temperature in London be 22-23C today?")
    member_values_f = [c_to_f(value_c) for value_c in [21.999, 22.0, 22.5, 23.0, 23.001]]

    _probability, empirical_p = _temperature_bucket_probability(
        parsed,
        member_values_f,
        mean_f=sum(member_values_f) / len(member_values_f),
        sigma_f=1.25,
    )

    assert parsed.threshold_unit == "C"
    assert parsed.temperature_range_lower_original == 22.0
    assert parsed.temperature_range_upper_original == 23.0
    assert parsed.temperature_range_lower_f == c_to_f(22.0)
    assert parsed.temperature_range_upper_f == c_to_f(23.0)
    assert empirical_p == pytest.approx(3 / 5)


def test_range_temperature_bucket_probability_differs_from_exact_bucket():
    target = _today_for_timezone("America/New_York")
    member_values_f = [85.999, 86.0, 86.5, 87.0, 87.001]

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

    range_signal = estimate_weather_probability(
        "Will the highest temperature in Atlanta be 86-87F today?",
        settings=Settings(),
        ensemble_client=FakeEnsembleClient(),
    )
    exact_signal = estimate_weather_probability(
        "Will the highest temperature in Atlanta be 87F today?",
        settings=Settings(),
        ensemble_client=FakeEnsembleClient(),
    )

    assert range_signal.source == "open-meteo-ensemble-station"
    assert range_signal.parsed is not None
    assert range_signal.parsed.temperature_bucket == "range"
    assert range_signal.parsed.temperature_range_lower_f == 86
    assert range_signal.parsed.temperature_range_upper_f == 87
    assert range_signal.parsed.temperature_range_inclusive is True
    assert "bucket=range" in range_signal.note
    assert exact_signal.parsed is not None
    assert exact_signal.parsed.temperature_bucket == "exact"
    assert range_signal.p_true > exact_signal.p_true
    assert range_signal.p_true != exact_signal.p_true


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


def test_lowest_temperature_ignores_observed_high_nowcast():
    target = _today_for_timezone("Asia/Seoul")
    member_values_f = [56.0, 57.0, 58.0, 59.0]

    class FakeEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **_kwargs):
            daily = {"time": [target.isoformat()], "temperature_2m_min": [member_values_f[0]]}
            daily.update(
                {
                    f"temperature_2m_min_member{idx:02d}": [value]
                    for idx, value in enumerate(member_values_f[1:], start=1)
                }
            )
            return {"daily": daily}

    class FreshHighNowcastProvider:
        def observed_high_so_far(self, station, *, target_date, now=None):
            assert station.station_id == "RKSI"
            assert target_date == target
            return StationNowcastObservation(
                station_id="RKSI",
                station_name=station.station_name,
                observed_high_c=30.0,
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
        "Will the lowest temperature in Seoul be 15C or below today?",
        settings=Settings(),
        ensemble_client=FakeEnsembleClient(),
        observation_provider=FreshHighNowcastProvider(),
    )

    assert signal.source == "open-meteo-ensemble-station"
    assert signal.p_true > 0.50
    assert "evidence=forecast-only" in signal.note
    assert "nowcast_unavailable=observed-low-provider-not-supplied" in signal.note


def test_lowest_temperature_nowcast_can_confirm_low_threshold_crossing():
    target = _today_for_timezone("Asia/Seoul")
    member_values_f = [62.0, 63.0, 64.0, 65.0]

    class FakeEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **_kwargs):
            daily = {"time": [target.isoformat()], "temperature_2m_min": [member_values_f[0]]}
            daily.update(
                {
                    f"temperature_2m_min_member{idx:02d}": [value]
                    for idx, value in enumerate(member_values_f[1:], start=1)
                }
            )
            return {"daily": daily}

    class FreshExtremesNowcastProvider:
        def observed_temperature_extremes_so_far(self, station, *, target_date, now=None):
            assert station.station_id == "RKSI"
            assert target_date == target
            return StationNowcastObservation(
                station_id="RKSI",
                station_name=station.station_name,
                observed_high_c=30.0,
                observed_at=datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
                high_observed_at=datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
                source="aviationweather-metar",
                source_url="https://aviationweather.gov/api/data/metar",
                settlement_source_url="https://www.wunderground.com/history/daily/kr/incheon/RKSI",
                freshness_seconds=1800,
                unavailable_reason="",
                raw_observation_count=4,
                update_cadence="fixture",
                observed_low_c=14.0,
                low_observed_at=datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc),
            )

    signal = estimate_weather_probability(
        "Will the lowest temperature in Seoul be 15C or below today?",
        settings=Settings(),
        ensemble_client=FakeEnsembleClient(),
        observation_provider=FreshExtremesNowcastProvider(),
    )

    assert signal.source == "open-meteo-ensemble-station+nowcast"
    assert signal.p_true == 1.0
    assert signal.confidence >= 0.95
    assert signal.nowcast["observed_low_c"] == 14.0
    assert "observed-low-reached-lower-tail" in signal.note


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
