from weather_bot.live_paper_runner import _market_type_from_signal
from weather_bot.models import ParsedWeatherQuestion, WeatherSignal


def test_snow_signals_use_precipitation_risk_controls():
    parsed = ParsedWeatherQuestion(
        city="nyc",
        latitude=40.7789,
        longitude=-73.9692,
        threshold_f=None,
        threshold_original=None,
        threshold_unit="UNKNOWN",
        operator=None,
        variable="snow",
        date_hint="today",
    )
    signal = WeatherSignal(0.7, 0.7, "test", "", parsed)

    assert _market_type_from_signal(signal) == "precipitation"
