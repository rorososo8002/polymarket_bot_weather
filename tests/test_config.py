from weather_bot.config import Settings, load_settings
from weather_bot.stations import SUPPORTED_CITY_COUNT


def test_supported_city_allowlist_is_not_used_as_discovery_event_cap():
    assert SUPPORTED_CITY_COUNT == 41
    assert not hasattr(Settings, "max_events")
    assert Settings.discovery_max_pages == 8
    assert Settings.discovery_page_size == 100


def test_default_forecast_cadence_is_thirty_minutes():
    assert Settings.forecast_refresh_interval_seconds == 1800
    assert Settings.forecast_cache_ttl_seconds == 1800


def test_default_station_nowcast_is_pilot_cached_and_freshness_bounded():
    assert Settings.station_nowcast_enabled is True
    assert Settings.station_nowcast_cache_ttl_seconds == 900
    assert Settings.station_nowcast_freshness_seconds == 5400


def test_default_entry_net_return_filter_uses_official_weather_fee_rate():
    assert Settings.entry_min_expected_net_return_pct == 0.06
    assert Settings.weather_taker_fee_rate == 0.05


def test_default_city_date_portfolio_caps_shrink_after_one_thousand_dollars():
    assert Settings.bankroll_usd == 100.0
    assert Settings.entry_fraction == 0.10
    assert Settings.max_single_market_fraction == 0.10
    assert Settings.max_city_exposure_fraction == 0.20
    assert Settings.max_event_date_exposure_fraction == 0.10
    assert Settings.large_bankroll_event_date_exposure_fraction == 0.05
    assert Settings.event_date_exposure_transition_usd == 1000.0
    assert Settings.max_event_portfolio_legs == 2
    assert Settings.max_total_exposure_fraction == 0.90
    assert Settings.min_order_usd == 10.0


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
    monkeypatch.setenv("ENTRY_MIN_EXPECTED_NET_RETURN_PCT", "0.07")
    monkeypatch.setenv("WEATHER_TAKER_FEE_RATE", "0.04")

    settings = load_settings()

    assert settings.enable_precipitation_markets is True
    assert settings.probability_stop_drop_threshold == 0.08
    assert settings.entry_min_expected_net_return_pct == 0.07
    assert settings.weather_taker_fee_rate == 0.04


def test_load_settings_reads_forecast_cache_controls(monkeypatch):
    monkeypatch.setenv("FORECAST_CACHE_PATH", "data/custom_forecast_cache.json")
    monkeypatch.setenv("FORECAST_CACHE_TTL_SECONDS", "600")

    settings = load_settings()

    assert settings.forecast_cache_path == "data/custom_forecast_cache.json"
    assert settings.forecast_cache_ttl_seconds == 600


def test_load_settings_reads_station_nowcast_controls(monkeypatch):
    monkeypatch.setenv("STATION_NOWCAST_ENABLED", "false")
    monkeypatch.setenv("STATION_NOWCAST_CACHE_TTL_SECONDS", "300")
    monkeypatch.setenv("STATION_NOWCAST_FRESHNESS_SECONDS", "1800")

    settings = load_settings()

    assert settings.station_nowcast_enabled is False
    assert settings.station_nowcast_cache_ttl_seconds == 300
    assert settings.station_nowcast_freshness_seconds == 1800


def test_load_settings_reads_realtime_orderbook_stream(monkeypatch):
    monkeypatch.setenv("ORDERBOOK_STREAM_ENABLED", "true")
    monkeypatch.setenv("ORDERBOOK_STREAM_HEARTBEAT_SECONDS", "10")
    monkeypatch.setenv("ORDERBOOK_STREAM_STALE_SECONDS", "45")
    monkeypatch.setenv("RUNNER_HEALTH_STATUS_INTERVAL_SECONDS", "7")
    monkeypatch.setenv("FORECAST_REFRESH_INTERVAL_SECONDS", "600")

    settings = load_settings()

    assert settings.orderbook_stream_enabled is True
    assert settings.orderbook_stream_heartbeat_seconds == 10
    assert settings.orderbook_stream_stale_seconds == 45
    assert settings.runner_health_status_interval_seconds == 7
    assert settings.forecast_refresh_interval_seconds == 600


def test_load_settings_reads_discovery_pagination_safety_controls(monkeypatch):
    monkeypatch.setenv("DISCOVERY_MAX_PAGES", "17")
    monkeypatch.setenv("DISCOVERY_PAGE_SIZE", "75")

    settings = load_settings()

    assert settings.discovery_max_pages == 17
    assert settings.discovery_page_size == 75


def test_load_settings_reads_city_date_portfolio_controls(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DECISIONS_JSONL_PATH", "data/custom-portfolios.jsonl")
    monkeypatch.setenv("MAX_CITY_EXPOSURE_FRACTION", "0.11")
    monkeypatch.setenv("MAX_EVENT_DATE_EXPOSURE_FRACTION", "0.12")
    monkeypatch.setenv("LARGE_BANKROLL_EVENT_DATE_EXPOSURE_FRACTION", "0.04")
    monkeypatch.setenv("EVENT_DATE_EXPOSURE_TRANSITION_USD", "1200")
    monkeypatch.setenv("MAX_EVENT_PORTFOLIO_LEGS", "3")

    settings = load_settings()

    assert settings.portfolio_decisions_jsonl_path == "data/custom-portfolios.jsonl"
    assert settings.max_city_exposure_fraction == 0.11
    assert settings.max_event_date_exposure_fraction == 0.12
    assert settings.large_bankroll_event_date_exposure_fraction == 0.04
    assert settings.event_date_exposure_transition_usd == 1200.0
    assert settings.max_event_portfolio_legs == 3
