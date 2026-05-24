from weather_bot.probability import (
    _extract_member_values,
    blend_empirical_and_cdf,
    dynamic_sigma_f,
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
