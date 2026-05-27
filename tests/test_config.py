from weather_bot.config import Settings, load_settings
from weather_bot.stations import SUPPORTED_CITY_COUNT


def test_default_max_markets_tracks_supported_city_count():
    assert Settings.max_markets == SUPPORTED_CITY_COUNT


def test_default_forecast_cadence_is_thirty_minutes():
    assert Settings.forecast_refresh_interval_seconds == 1800
    assert Settings.forecast_cache_ttl_seconds == 1800


def test_load_settings_reads_dashboard_env(monkeypatch):
    monkeypatch.setenv("DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setenv("DASHBOARD_PORT", "9999")
    monkeypatch.setenv("DASHBOARD_TOKEN", "secret")

    settings = load_settings()

    assert settings.dashboard_host == "0.0.0.0"
    assert settings.dashboard_port == 9999
    assert settings.dashboard_token == "secret"


def test_load_settings_reads_conservative_strategy_controls(monkeypatch):
    monkeypatch.setenv("ENABLE_PRECIPITATION_MARKETS", "true")
    monkeypatch.setenv("PROBABILITY_STOP_DROP_THRESHOLD", "0.08")

    settings = load_settings()

    assert settings.enable_precipitation_markets is True
    assert settings.probability_stop_drop_threshold == 0.08


def test_load_settings_reads_forecast_cache_controls(monkeypatch):
    monkeypatch.setenv("FORECAST_CACHE_PATH", "data/custom_forecast_cache.json")
    monkeypatch.setenv("FORECAST_CACHE_TTL_SECONDS", "600")

    settings = load_settings()

    assert settings.forecast_cache_path == "data/custom_forecast_cache.json"
    assert settings.forecast_cache_ttl_seconds == 600


def test_load_settings_reads_realtime_orderbook_stream(monkeypatch):
    monkeypatch.setenv("ORDERBOOK_STREAM_ENABLED", "true")
    monkeypatch.setenv("ORDERBOOK_STREAM_HEARTBEAT_SECONDS", "10")
    monkeypatch.setenv("FORECAST_REFRESH_INTERVAL_SECONDS", "600")

    settings = load_settings()

    assert settings.orderbook_stream_enabled is True
    assert settings.orderbook_stream_heartbeat_seconds == 10
    assert settings.forecast_refresh_interval_seconds == 600
