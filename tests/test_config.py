from weather_bot.config import load_settings


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
    monkeypatch.setenv("PRICE_STOP_CONFIRMATION_CYCLES", "3")

    settings = load_settings()

    assert settings.enable_precipitation_markets is True
    assert settings.price_stop_confirmation_cycles == 3


def test_load_settings_reads_forecast_cache_controls(monkeypatch):
    monkeypatch.setenv("FORECAST_CACHE_PATH", "data/custom_forecast_cache.json")
    monkeypatch.setenv("FORECAST_CACHE_TTL_SECONDS", "1800")

    settings = load_settings()

    assert settings.forecast_cache_path == "data/custom_forecast_cache.json"
    assert settings.forecast_cache_ttl_seconds == 1800


def test_load_settings_reads_realtime_orderbook_polling(monkeypatch):
    monkeypatch.setenv("ORDERBOOK_POLL_INTERVAL_SECONDS", "3")
    monkeypatch.setenv("FORECAST_REFRESH_INTERVAL_SECONDS", "1800")

    settings = load_settings()

    assert settings.orderbook_poll_interval_seconds == 3
    assert settings.forecast_refresh_interval_seconds == 1800
