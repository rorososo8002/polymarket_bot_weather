import pytest

from weather_bot.config import Settings, load_settings
from weather_bot.stations import SUPPORTED_CITY_COUNT


def test_supported_city_allowlist_is_not_used_as_discovery_event_cap():
    assert SUPPORTED_CITY_COUNT == 41
    assert not hasattr(Settings, "max_events")
    assert Settings.discovery_max_pages == 8
    assert Settings.discovery_page_size == 100


def test_default_forecast_budget_batch_mode():
    # Batch mode: 15 s within-batch gap, 10800 s (3 h) cache TTL between batches.
    # GFS updates every 6 h (processed in 3-4 h); 3 h cache captures each new run.
    # Budget: 40 trading-ready cities x 8 batches/day x 31 units = 9 920 units/day < 10 000 limit.
    assert Settings.stream_cycle_interval_seconds == 2400
    assert Settings.forecast_cache_ttl_seconds == 10800
    assert Settings.forecast_request_min_interval_seconds == 15
    assert Settings.forecast_rate_limit_state_path == ""


def test_default_realtime_orderbook_rest_snapshot_is_bounded_verification():
    assert Settings.orderbook_stream_enabled is True
    assert Settings.orderbook_rest_snapshot_enabled is True
    assert Settings.orderbook_rest_snapshot_interval_seconds == 60


def test_default_station_nowcast_is_pilot_cached_and_freshness_bounded():
    assert Settings.station_nowcast_enabled is True
    assert Settings.station_nowcast_cache_ttl_seconds == 300  # 5 min: matches AWC METAR floor
    assert Settings.station_nowcast_freshness_seconds == 5400
    assert Settings.station_nowcast_request_log_path == ""


def test_default_raw_snapshot_mode_saves_only_error_diagnostics():
    assert Settings.raw_snapshots_mode == "error"
    assert Settings.raw_snapshots_max_bytes == 100 * 1024 * 1024
    assert Settings.raw_snapshots_retention_days == 7
    assert Settings.raw_snapshots_min_free_bytes == 1024 * 1024 * 1024
    assert Settings.raw_snapshots_max_disk_usage_pct == 0.90


def test_default_entry_net_return_filter_uses_official_weather_fee_rate():
    assert Settings.entry_min_expected_net_return_pct == 0.06
    assert Settings.weather_taker_fee_rate == 0.05


def test_default_settlement_runner_is_bounded_and_enabled():
    assert Settings.settlement_runner_enabled is True
    assert Settings.settlement_runner_max_fraction == 0.25
    assert Settings.settlement_runner_min_ev_margin_usd == 0.0


def test_default_city_date_portfolio_caps_shrink_after_one_thousand_dollars():
    assert Settings.bankroll_usd == 100.0
    assert Settings.size_mode == "kelly"
    assert Settings.entry_fraction == 0.20
    assert Settings.fractional_kelly == 0.25
    assert Settings.max_single_market_fraction == 0.10
    assert Settings.add_to_position_drop_pct == 0.10
    assert Settings.max_city_exposure_fraction == 0.20
    assert Settings.max_event_date_exposure_fraction == 0.10
    assert Settings.large_bankroll_event_date_exposure_fraction == 0.05
    assert Settings.event_date_exposure_transition_usd == 1000.0
    assert Settings.max_event_portfolio_legs == 2
    assert Settings.max_total_exposure_fraction == 0.60
    assert Settings.min_order_usd == 10.0


def test_default_settings_pass_numeric_range_validation():
    settings = Settings()

    assert settings.bankroll_usd == 100.0
    assert settings.min_order_usd == 10.0
    assert settings.max_total_exposure_fraction == 0.60


@pytest.mark.parametrize(
    ("override", "expected_name", "expected_reason"),
    [
        ({"min_order_usd": -1.0}, "MIN_ORDER_USD", "greater than 0"),
        ({"weather_taker_fee_rate": -0.01}, "WEATHER_TAKER_FEE_RATE", "at least 0"),
        ({"max_total_exposure_fraction": 2.0}, "MAX_TOTAL_EXPOSURE_FRACTION", "between 0 and 1"),
    ],
)
def test_settings_rejects_invalid_numeric_safety_ranges(override, expected_name, expected_reason):
    with pytest.raises(ValueError, match=expected_name) as exc_info:
        Settings(**override)

    assert expected_reason in str(exc_info.value)


def test_settings_rejects_fee_rate_above_one():
    with pytest.raises(ValueError, match="WEATHER_TAKER_FEE_RATE") as exc_info:
        Settings(weather_taker_fee_rate=1.5)

    assert "at most 1" in str(exc_info.value)


@pytest.mark.parametrize("dashboard_port", [0, 70000])
def test_settings_rejects_dashboard_port_outside_tcp_range(dashboard_port):
    with pytest.raises(ValueError, match="DASHBOARD_PORT") as exc_info:
        Settings(dashboard_port=dashboard_port)

    assert "between 1 and 65535" in str(exc_info.value)


def test_settings_defaults_to_batch_mode_forecast_interval():
    # Within-batch gap is 15 s; between-batch gap is controlled by cache TTL (10800 s).
    settings = Settings()

    assert settings.forecast_request_min_interval_seconds == 15


@pytest.mark.parametrize("interval_seconds", [0, 5, 9])
def test_settings_rejects_forecast_request_spacing_below_ten_seconds(interval_seconds):
    with pytest.raises(ValueError, match="FORECAST_REQUEST_MIN_INTERVAL_SECONDS") as exc_info:
        Settings(forecast_request_min_interval_seconds=interval_seconds)

    assert "at least 10" in str(exc_info.value)


@pytest.mark.parametrize(
    ("override", "expected_name"),
    [
        ({"bankroll_usd": 0.0}, "BANKROLL_USD"),
        ({"stream_cycle_interval_seconds": 0}, "STREAM_CYCLE_INTERVAL_SECONDS"),
        ({"forecast_cache_ttl_seconds": 0}, "FORECAST_CACHE_TTL_SECONDS"),
        ({"orderbook_stream_stale_seconds": 0}, "ORDERBOOK_STREAM_STALE_SECONDS"),
    ],
)
def test_settings_rejects_zero_for_positive_runtime_safety_values(override, expected_name):
    with pytest.raises(ValueError, match=expected_name) as exc_info:
        Settings(**override)

    assert "greater than 0" in str(exc_info.value)


@pytest.mark.parametrize(
    ("env_name", "raw_value", "expected_reason"),
    [
        ("MIN_ORDER_USD", "-1", "greater than 0"),
        ("WEATHER_TAKER_FEE_RATE", "-0.01", "at least 0"),
        ("MAX_TOTAL_EXPOSURE_FRACTION", "2.0", "between 0 and 1"),
    ],
)
def test_load_settings_rejects_invalid_numeric_env_at_startup(monkeypatch, env_name, raw_value, expected_reason):
    monkeypatch.setenv(env_name, raw_value)

    with pytest.raises(ValueError, match=env_name) as exc_info:
        load_settings()

    assert expected_reason in str(exc_info.value)


def test_load_settings_rejects_fee_rate_above_one(monkeypatch):
    monkeypatch.setenv("WEATHER_TAKER_FEE_RATE", "1.5")

    with pytest.raises(ValueError, match="WEATHER_TAKER_FEE_RATE") as exc_info:
        load_settings()

    assert "at most 1" in str(exc_info.value)


def test_load_settings_rejects_non_numeric_env_with_setting_name(monkeypatch):
    monkeypatch.setenv("MIN_ORDER_USD", "not-a-number")

    with pytest.raises(ValueError, match="MIN_ORDER_USD") as exc_info:
        load_settings()

    assert "number" in str(exc_info.value)


def test_load_settings_reads_dashboard_env(monkeypatch):
    monkeypatch.setenv("DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setenv("DASHBOARD_PORT", "9999")
    monkeypatch.setenv("DASHBOARD_TOKEN", "secret")

    settings = load_settings()

    assert settings.dashboard_host == "0.0.0.0"
    assert settings.dashboard_port == 9999
    assert settings.dashboard_token == "secret"


@pytest.mark.parametrize("raw_port", ["0", "70000"])
def test_load_settings_rejects_dashboard_port_outside_tcp_range(monkeypatch, raw_port):
    monkeypatch.setenv("DASHBOARD_PORT", raw_port)

    with pytest.raises(ValueError, match="DASHBOARD_PORT") as exc_info:
        load_settings()

    assert "between 1 and 65535" in str(exc_info.value)


def test_load_settings_reads_conservative_strategy_controls(monkeypatch):
    monkeypatch.setenv("PROBABILITY_STOP_DROP_THRESHOLD", "0.08")
    monkeypatch.setenv("ENTRY_MIN_EXPECTED_NET_RETURN_PCT", "0.07")
    monkeypatch.setenv("WEATHER_TAKER_FEE_RATE", "0.04")
    monkeypatch.setenv("SETTLEMENT_RUNNER_ENABLED", "false")
    monkeypatch.setenv("SETTLEMENT_RUNNER_MAX_FRACTION", "0.20")
    monkeypatch.setenv("SETTLEMENT_RUNNER_MIN_EV_MARGIN_USD", "1.25")

    settings = load_settings()

    assert not hasattr(settings, "enable_precipitation_markets")
    assert settings.probability_stop_drop_threshold == 0.08
    assert settings.entry_min_expected_net_return_pct == 0.07
    assert settings.weather_taker_fee_rate == 0.04
    assert settings.settlement_runner_enabled is False
    assert settings.settlement_runner_max_fraction == 0.20
    assert settings.settlement_runner_min_ev_margin_usd == 1.25


def test_load_settings_reads_forecast_cache_controls(monkeypatch):
    monkeypatch.setenv("FORECAST_CACHE_PATH", "data/custom_forecast_cache.json")
    monkeypatch.setenv("FORECAST_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("FORECAST_REQUEST_MIN_INTERVAL_SECONDS", "90")
    monkeypatch.setenv("FORECAST_REQUEST_LOG_PATH", "data/custom_forecast_request_log.jsonl")
    monkeypatch.setenv("FORECAST_RATE_LIMIT_STATE_PATH", "data/custom_forecast_rate_limit_state.json")

    settings = load_settings()

    assert settings.forecast_cache_path == "data/custom_forecast_cache.json"
    assert settings.forecast_cache_ttl_seconds == 600
    assert settings.forecast_request_min_interval_seconds == 90
    assert settings.forecast_request_log_path == "data/custom_forecast_request_log.jsonl"
    assert settings.forecast_rate_limit_state_path == "data/custom_forecast_rate_limit_state.json"


@pytest.mark.parametrize("raw", ["0", "5", "9"])
def test_load_settings_rejects_forecast_request_spacing_below_ten_seconds(monkeypatch, raw):
    monkeypatch.setenv("FORECAST_REQUEST_MIN_INTERVAL_SECONDS", raw)

    with pytest.raises(ValueError, match="FORECAST_REQUEST_MIN_INTERVAL_SECONDS") as exc_info:
        load_settings()

    assert "at least 10" in str(exc_info.value)


def test_load_settings_reads_station_nowcast_controls(monkeypatch):
    monkeypatch.setenv("STATION_NOWCAST_ENABLED", "false")
    monkeypatch.setenv("STATION_NOWCAST_CACHE_TTL_SECONDS", "300")
    monkeypatch.setenv("STATION_NOWCAST_FRESHNESS_SECONDS", "1800")
    monkeypatch.setenv("STATION_NOWCAST_REQUEST_LOG_PATH", "data/custom_station_nowcast_request_log.jsonl")

    settings = load_settings()

    assert settings.station_nowcast_enabled is False
    assert settings.station_nowcast_cache_ttl_seconds == 300
    assert settings.station_nowcast_freshness_seconds == 1800
    assert settings.station_nowcast_request_log_path == "data/custom_station_nowcast_request_log.jsonl"


def test_load_settings_reads_realtime_orderbook_stream(monkeypatch):
    monkeypatch.setenv("ORDERBOOK_STREAM_ENABLED", "true")
    monkeypatch.setenv("ORDERBOOK_STREAM_HEARTBEAT_SECONDS", "10")
    monkeypatch.setenv("ORDERBOOK_STREAM_STALE_SECONDS", "45")
    monkeypatch.setenv("ORDERBOOK_REST_SNAPSHOT_ENABLED", "false")
    monkeypatch.setenv("ORDERBOOK_REST_SNAPSHOT_INTERVAL_SECONDS", "120")
    monkeypatch.setenv("RUNNER_HEALTH_STATUS_INTERVAL_SECONDS", "7")
    monkeypatch.setenv("STREAM_CYCLE_INTERVAL_SECONDS", "600")

    settings = load_settings()

    assert settings.orderbook_stream_enabled is True
    assert settings.orderbook_stream_heartbeat_seconds == 10
    assert settings.orderbook_stream_stale_seconds == 45
    assert settings.orderbook_rest_snapshot_enabled is False
    assert settings.orderbook_rest_snapshot_interval_seconds == 120
    assert settings.runner_health_status_interval_seconds == 7
    assert settings.stream_cycle_interval_seconds == 600


@pytest.mark.parametrize("raw", ["true", "1", "yes", "y", "on"])
def test_load_settings_accepts_known_true_boolean_values(monkeypatch, raw):
    monkeypatch.setenv("REQUIRE_DATE_HINT_FOR_TRADE", raw)

    settings = load_settings()

    assert settings.require_date_hint_for_trade is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "n", "off"])
def test_load_settings_accepts_known_false_boolean_values(monkeypatch, raw):
    monkeypatch.setenv("REQUIRE_DATE_HINT_FOR_TRADE", raw)

    settings = load_settings()

    assert settings.require_date_hint_for_trade is False


@pytest.mark.parametrize("raw", ["treu", "enabled", "maybe"])
def test_load_settings_rejects_unknown_boolean_values(monkeypatch, raw):
    monkeypatch.setenv("REQUIRE_DATE_HINT_FOR_TRADE", raw)

    with pytest.raises(ValueError, match="REQUIRE_DATE_HINT_FOR_TRADE"):
        load_settings()


def test_load_settings_reads_discovery_pagination_safety_controls(monkeypatch):
    monkeypatch.setenv("DISCOVERY_MAX_PAGES", "17")
    monkeypatch.setenv("DISCOVERY_PAGE_SIZE", "75")

    settings = load_settings()

    assert settings.discovery_max_pages == 17
    assert settings.discovery_page_size == 75


def test_load_settings_reads_city_date_portfolio_controls(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_DECISIONS_JSONL_PATH", "data/custom-portfolios.jsonl")
    monkeypatch.setenv("ADD_TO_POSITION_DROP_PCT", "0.15")
    monkeypatch.setenv("MAX_CITY_EXPOSURE_FRACTION", "0.11")
    monkeypatch.setenv("MAX_EVENT_DATE_EXPOSURE_FRACTION", "0.12")
    monkeypatch.setenv("LARGE_BANKROLL_EVENT_DATE_EXPOSURE_FRACTION", "0.04")
    monkeypatch.setenv("EVENT_DATE_EXPOSURE_TRANSITION_USD", "1200")
    monkeypatch.setenv("MAX_EVENT_PORTFOLIO_LEGS", "3")

    settings = load_settings()

    assert settings.portfolio_decisions_jsonl_path == "data/custom-portfolios.jsonl"
    assert settings.add_to_position_drop_pct == 0.15
    assert settings.max_city_exposure_fraction == 0.11
    assert settings.max_event_date_exposure_fraction == 0.12
    assert settings.large_bankroll_event_date_exposure_fraction == 0.04
    assert settings.event_date_exposure_transition_usd == 1200.0
    assert settings.max_event_portfolio_legs == 3


def test_load_settings_reads_raw_snapshot_storage_mode(monkeypatch):
    monkeypatch.setenv("RAW_SNAPSHOTS_MODE", "debug")
    monkeypatch.setenv("RAW_SNAPSHOTS_MAX_BYTES", "12345")
    monkeypatch.setenv("RAW_SNAPSHOTS_RETENTION_DAYS", "9")
    monkeypatch.setenv("RAW_SNAPSHOTS_MIN_FREE_BYTES", "54321")
    monkeypatch.setenv("RAW_SNAPSHOTS_MAX_DISK_USAGE_PCT", "0.75")

    settings = load_settings()

    assert settings.raw_snapshots_mode == "debug"
    assert settings.raw_snapshots_max_bytes == 12345
    assert settings.raw_snapshots_retention_days == 9
    assert settings.raw_snapshots_min_free_bytes == 54321
    assert settings.raw_snapshots_max_disk_usage_pct == 0.75


def test_load_settings_rejects_unknown_raw_snapshot_storage_mode(monkeypatch):
    monkeypatch.setenv("RAW_SNAPSHOTS_MODE", "always")

    with pytest.raises(ValueError, match="RAW_SNAPSHOTS_MODE"):
        load_settings()


def test_load_settings_normalizes_size_mode_choice(monkeypatch):
    monkeypatch.setenv("SIZE_MODE", "KeLlY")

    settings = load_settings()

    assert settings.size_mode == "kelly"


def test_load_settings_rejects_unknown_size_mode(monkeypatch):
    monkeypatch.setenv("SIZE_MODE", "kellyy")

    with pytest.raises(ValueError, match="SIZE_MODE") as exc_info:
        load_settings()

    assert "fixed_fraction" in str(exc_info.value)
    assert "kelly" in str(exc_info.value)
