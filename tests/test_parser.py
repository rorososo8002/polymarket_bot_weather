import weather_bot.weather_client as weather_client

from weather_bot.weather_client import (
    c_to_f,
    parse_weather_question,
    rounded_temperature_bucket_interval_f,
    temperature_bucket_interval_bounds_f,
)


def test_parse_temperature_market_fahrenheit():
    parsed = parse_weather_question("Will NYC reach 90°F on May 25?")
    assert parsed.city == "nyc"
    assert parsed.variable == "temperature"
    assert parsed.operator == ">="
    assert parsed.temperature_bucket == "threshold"
    assert parsed.threshold_f == 90
    assert parsed.threshold_unit == "F"


def test_parse_temperature_market_celsius_threshold():
    parsed = parse_weather_question("Will Seoul be at least 21C today?")
    assert parsed.city == "seoul"
    assert parsed.variable == "temperature"
    assert parsed.operator == ">="
    assert parsed.threshold_unit == "C"
    assert round(parsed.threshold_f, 1) == 69.8


def test_parse_temperature_market_celsius_below():
    parsed = parse_weather_question("Will Seoul be 21C or lower today?")
    assert parsed.city == "seoul"
    assert parsed.threshold_unit == "C"
    assert parsed.operator == "<="
    assert round(parsed.threshold_f, 1) == 69.8


def test_parse_polymarket_celsius_tail_does_not_use_date_as_temperature():
    parsed = parse_weather_question("Will the highest temperature in London be 22\u00b0C or below on May 25?")
    assert parsed.city == "london"
    assert parsed.operator == "<="
    assert parsed.threshold_unit == "C"
    assert round(parsed.threshold_f, 1) == 71.6
    assert parsed.date_hint == "may 25"


def test_parse_exact_temperature_bucket():
    parsed = parse_weather_question("Will the highest temperature in Seoul be 26\u00b0C on May 25?")
    assert parsed.city == "seoul"
    assert parsed.operator == "=="
    assert parsed.temperature_bucket == "exact"
    assert parsed.threshold_original == 26
    assert round(parsed.threshold_f, 1) == 78.8


def test_parse_fahrenheit_range_temperature_bucket():
    parsed = parse_weather_question("Will the highest temperature in Atlanta be 86-87F on May 25?")

    assert parsed.city == "atlanta"
    assert parsed.variable == "temperature"
    assert parsed.operator == "=="
    assert parsed.temperature_bucket == "range"
    assert parsed.threshold_unit == "F"
    assert parsed.threshold_f != 87
    assert parsed.temperature_range_lower_f == 86
    assert parsed.temperature_range_upper_f == 87
    assert parsed.temperature_range_lower_original == 86
    assert parsed.temperature_range_upper_original == 87
    assert parsed.temperature_range_inclusive is True


def test_range_bucket_interval_preserves_exact_inclusive_settlement_endpoints():
    parsed = parse_weather_question("Will the highest temperature in Atlanta be 86-87F on May 25?")

    assert parsed.temperature_range_lower_original == 86
    assert parsed.temperature_range_upper_original == 87
    assert parsed.temperature_range_lower_f == 86
    assert parsed.temperature_range_upper_f == 87
    assert rounded_temperature_bucket_interval_f(parsed) == (86.0, 87.0)


def test_temperature_comparison_uses_millifahrenheit_scale():
    assert hasattr(weather_client, "temperature_f_to_millif")
    to_millif = weather_client.temperature_f_to_millif

    assert to_millif(67.000) == 67000
    assert to_millif(68.00000000000001) == 68000
    assert to_millif(68.001) == 68001


def test_range_bucket_interval_uses_scaled_boundary_metadata():
    parsed = parse_weather_question("Will the highest temperature in Atlanta be 67-68F on May 25?")
    bounds = temperature_bucket_interval_bounds_f(parsed)

    assert bounds is not None
    assert getattr(bounds, "original_unit", None) == "F"
    assert getattr(bounds, "comparison_unit", None) == "millifahrenheit"
    assert bounds.contains_f(67.000)
    assert bounds.contains_f(68.000)
    assert bounds.contains_f(68.00000000000001)
    assert not bounds.contains_f(68.001)


def test_exact_temperature_bucket_uses_displayed_value_without_half_step():
    parsed = parse_weather_question("Will the highest temperature in Atlanta be 87F on May 25?")
    bounds = temperature_bucket_interval_bounds_f(parsed)

    assert parsed.temperature_bucket == "exact"
    assert rounded_temperature_bucket_interval_f(parsed) == (87.0, 87.0)
    assert bounds is not None
    assert bounds.contains_f(87.0)
    assert not bounds.contains_f(86.999)
    assert not bounds.contains_f(87.001)


def test_exact_celsius_bucket_uses_displayed_integer_without_hidden_range():
    parsed = parse_weather_question("Will the highest temperature in Singapore be 29\u00b0C on June 12?")
    bounds = temperature_bucket_interval_bounds_f(parsed)
    target_f = c_to_f(29.0)

    assert parsed.temperature_bucket == "exact"
    assert parsed.threshold_unit == "C"
    assert rounded_temperature_bucket_interval_f(parsed) == (target_f, target_f)
    assert bounds is not None
    assert getattr(bounds, "original_unit", None) == "C"
    assert getattr(bounds, "comparison_unit", None) == "millifahrenheit"
    assert bounds.contains_f(target_f)
    assert not bounds.contains_f(c_to_f(28.999))
    assert not bounds.contains_f(c_to_f(29.001))


def test_parse_celsius_range_temperature_bucket():
    parsed = parse_weather_question("Will the highest temperature in London be 22-23C on May 25?")

    assert parsed.city == "london"
    assert parsed.operator == "=="
    assert parsed.threshold_unit == "C"
    assert parsed.temperature_bucket == "range"
    assert parsed.temperature_range_lower_original == 22
    assert parsed.temperature_range_upper_original == 23
    assert parsed.temperature_range_lower_f == 22 * 9 / 5 + 32
    assert parsed.temperature_range_upper_f == 23 * 9 / 5 + 32
    assert parsed.temperature_range_inclusive is True


def test_parse_lower_tail_temperature_bucket():
    parsed = parse_weather_question("Will the highest temperature in Seoul be 18\u00b0C or below on May 25?")
    assert parsed.operator == "<="
    assert parsed.temperature_bucket == "lower_tail"
    assert parsed.threshold_original == 18


def test_parse_upper_tail_temperature_bucket():
    parsed = parse_weather_question("Will the highest temperature in Seoul be 28\u00b0C or higher on May 25?")
    assert parsed.operator == ">="
    assert parsed.temperature_bucket == "upper_tail"
    assert parsed.threshold_original == 28


def test_rain_question_is_not_a_supported_temperature_market():
    parsed = parse_weather_question("Will it rain in Chicago on Friday?")

    assert parsed.city == "chicago"
    assert parsed.variable == "unsupported"
    assert parsed.threshold_f is None
    assert parsed.operator is None
    assert "non-temperature weather market" in parsed.note


def test_snow_question_is_not_a_supported_temperature_market():
    parsed = parse_weather_question("Will Tokyo get any snow tomorrow?")

    assert parsed.city == "tokyo"
    assert parsed.variable == "unsupported"
    assert parsed.threshold_f is None
    assert parsed.operator is None
    assert parsed.date_hint == "tomorrow"
    assert "non-temperature weather market" in parsed.note


def test_wind_question_with_numeric_threshold_is_not_temperature():
    parsed = parse_weather_question("Will NYC wind speed exceed 20 mph on May 25?")

    assert parsed.city == "nyc"
    assert parsed.variable == "unsupported"
    assert parsed.threshold_f is None
    assert parsed.operator is None
    assert parsed.date_hint == "may 25"
    assert "non-temperature weather market" in parsed.note


def test_non_weather_numeric_comparison_is_not_temperature():
    parsed = parse_weather_question("Will NYC rents be over 10% on May 25?")

    assert parsed.city == "nyc"
    assert parsed.variable == "unsupported"
    assert parsed.threshold_f is None
    assert parsed.operator is None
    assert parsed.date_hint == "may 25"
    assert "temperature condition not parsed" in parsed.note
