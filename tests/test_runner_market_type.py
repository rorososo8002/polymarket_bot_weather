from weather_bot.config import Settings
from weather_bot.live_paper_runner import _market_type_from_signal, evaluate_market
from weather_bot.models import OrderBook, OrderLevel, ParsedWeatherQuestion, RawMarket, WeatherSignal


class FakeClient:
    def get_order_book(self, token_id: str) -> OrderBook:
        return OrderBook(token_id, bids=[OrderLevel(0.45, 100)], asks=[OrderLevel(0.50, 100)])


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


def test_precipitation_markets_are_disabled_by_default():
    parsed = ParsedWeatherQuestion(
        city="chicago",
        latitude=41.995,
        longitude=-87.9336,
        threshold_f=None,
        threshold_original=None,
        threshold_unit="UNKNOWN",
        operator=None,
        variable="precipitation",
        date_hint="friday",
        confidence=0.9,
    )
    signal = WeatherSignal(0.8, 0.9, "test", "", parsed)
    market = RawMarket("m1", "Will it rain in Chicago on Friday?", "rain", True, False, "yes", "no")

    result, per_side = evaluate_market(market, signal, FakeClient(), Settings(), 1000.0, "precipitation")

    assert result.side == "SKIP"
    assert per_side == {}
    assert "disabled" in result.reason
