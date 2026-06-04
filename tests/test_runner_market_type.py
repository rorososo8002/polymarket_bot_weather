from weather_bot.config import Settings
from weather_bot.live_paper_runner import run_cycle
from weather_bot.models import OrderBook, OrderLevel, RawMarket, WeatherSignal
from weather_bot.weather_client import parse_weather_question


def test_run_cycle_filters_non_temperature_before_probability_estimator(monkeypatch, tmp_path):
    rain_question = "Will it rain in Chicago on Friday?"
    temperature_question = "Will NYC reach 90 F on May 25?"
    markets = [
        RawMarket("rain", rain_question, "rain", True, False, "rain-yes", "rain-no"),
        RawMarket("temperature", temperature_question, "temperature", True, False, "temp-yes", "temp-no"),
    ]
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        min_net_edge=1.0,
        require_date_hint_for_trade=False,
    )
    probability_calls: list[str] = []

    class CycleClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def discover_weather_markets(self, max_pages: int, page_size: int):
            return markets

        def get_order_book(self, token_id: str) -> OrderBook:
            return OrderBook(token_id, bids=[OrderLevel(0.45, 100)], asks=[OrderLevel(0.50, 100)])

        def get_market(self, market_id: str) -> RawMarket:
            return next(market for market in markets if market.market_id == market_id)

    def estimate(question, **_kwargs):
        probability_calls.append(question)
        return WeatherSignal(0.5, 0.9, "test", "test", parse_weather_question(question))

    monkeypatch.setattr("weather_bot.live_paper_runner.PolymarketClient", CycleClient)
    monkeypatch.setattr("weather_bot.live_paper_runner.estimate_weather_probability", estimate)

    decisions = run_cycle(settings)

    assert probability_calls == [temperature_question]
    assert [decision.market.market_id for decision in decisions] == ["temperature"]


def test_run_cycle_skips_undated_temperature_market_before_forecast_request(monkeypatch, tmp_path):
    question_without_date = "Will NYC reach 90 F?"
    markets = [
        RawMarket("undated-temperature", question_without_date, "undated-temperature", True, False, "yes", "no"),
    ]
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        trades_csv_path=str(tmp_path / "trades.csv"),
        decisions_csv_path=str(tmp_path / "decisions.csv"),
        raw_snapshots_path=str(tmp_path / "raw.jsonl"),
        portfolio_decisions_jsonl_path=str(tmp_path / "portfolio.jsonl"),
        require_date_hint_for_trade=True,
    )
    forecast_request_count = 0

    class CycleClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def discover_weather_markets(self, max_pages: int, page_size: int):
            return markets

        def get_order_book(self, token_id: str) -> OrderBook:
            raise AssertionError("undated markets must skip before order-book fetch")

        def get_market(self, market_id: str) -> RawMarket:
            return markets[0]

    def forbidden_forecast_request(*_args, **_kwargs):
        nonlocal forecast_request_count
        forecast_request_count += 1
        raise AssertionError("undated markets must skip before Open-Meteo request")

    monkeypatch.setattr("weather_bot.live_paper_runner.PolymarketClient", CycleClient)
    monkeypatch.setattr("weather_bot.probability.requests.get", forbidden_forecast_request)

    decisions = run_cycle(settings)

    assert forecast_request_count == 0
    assert len(decisions) == 1
    assert decisions[0].market.market_id == "undated-temperature"
    assert decisions[0].result.side == "SKIP"
    assert "date_hint=None" in decisions[0].result.reason
