from weather_bot.weather_client import parse_weather_question


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
