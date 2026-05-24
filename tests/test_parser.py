from weather_bot.weather_client import parse_weather_question


def test_parse_temperature_market_fahrenheit():
    parsed = parse_weather_question("Will NYC reach 90°F on May 25?")
    assert parsed.city == "nyc"
    assert parsed.variable == "temperature"
    assert parsed.operator == ">="
    assert parsed.threshold_f == 90
    assert parsed.threshold_unit == "F"


def test_parse_temperature_market_celsius_korean_style():
    parsed = parse_weather_question("오늘 Seoul 최고기온은 21도 이상?")
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


def test_parse_exact_temperature_bucket_is_not_directional():
    parsed = parse_weather_question("Will the highest temperature in Seoul be 26\u00b0C on May 25?")
    assert parsed.city == "seoul"
    assert parsed.threshold_f is None
    assert parsed.operator is None


def test_parse_precipitation_market():
    parsed = parse_weather_question("Will it rain in Chicago on Friday?")
    assert parsed.city == "chicago"
    assert parsed.variable == "precipitation"
