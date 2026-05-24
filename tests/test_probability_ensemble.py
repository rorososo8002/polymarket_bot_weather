from datetime import date, datetime, timezone

from weather_bot.probability import (
    _extract_member_values,
    _today_for_timezone,
    blend_empirical_and_cdf,
    dynamic_sigma_f,
    estimate_weather_probability,
)


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
