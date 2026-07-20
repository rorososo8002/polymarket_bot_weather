import pytest

from weather_bot.config import Settings, load_settings
from weather_bot.stations import SUPPORTED_CITY_COUNT


def test_supported_city_allowlist_is_not_used_as_discovery_event_cap():
    assert SUPPORTED_CITY_COUNT == 49
    assert not hasattr(Settings, "max_events")
    assert Settings.discovery_max_pages == 8
    assert Settings.discovery_page_size == 100


def test_default_station_strategy_is_hybrid_observation_edge():
    assert Settings.stream_cycle_interval_seconds == 2400
    assert not hasattr(Settings, "forecast_cache_ttl_seconds")
    assert not hasattr(Settings, "forecast_request_min_interval_seconds")
    assert not hasattr(Settings, "forecast_rate_limit_state_path")
    assert Settings.bankroll_usd == 200.0
    assert Settings.strategy_mode == "hybrid_observation_edge"
    assert Settings.intraday_observation_edge_enabled is True
    assert Settings.intraday_min_side_probability == 0.90
    assert Settings.intraday_strong_side_probability == 0.97
    assert Settings.intraday_base_entry_fraction == 0.10
    assert Settings.intraday_strong_entry_fraction == 0.25
    assert Settings.intraday_abnormal_price_entry_fraction == 0.35
    assert Settings.intraday_abnormal_min_net_edge == 0.20
    assert Settings.intraday_hko_needs_audit_fraction_multiplier == 0.25
    assert Settings.intraday_high_confirm_local_hour == 15
    assert Settings.intraday_low_confirm_local_hour == 8
    assert Settings.intraday_us_high_disabled_before_local_hour == 15
    assert Settings.intraday_exact_low_yes_enabled is False
    assert Settings.intraday_us_exact_low_yes_enabled is True
    assert Settings.intraday_enable_above_bucket_no is False
    assert Settings.no_only_new_entries is True


def test_loaded_runtime_strategy_defaults_to_lock_only(monkeypatch):
    monkeypatch.delenv("STRATEGY_MODE", raising=False)

    assert load_settings().strategy_mode == "lock_only"
    assert Settings.official_nowcast_entry_only is False
    assert Settings.official_nowcast_lock_base_entry_fraction == 0.20
    assert Settings.official_nowcast_lock_strong_entry_fraction == 0.50
    assert Settings.max_consecutive_losses == 0
    assert Settings.city_loss_cooldown_hours == 0.0


def test_default_station_residual_probability_and_sizing_tiers():
    assert Settings.station_residual_probability_enabled is True
    assert Settings.station_residual_profile_path == "strategy_data/station_residual_profiles.json"
    assert Settings.station_residual_min_sample_days == 60
    assert Settings.station_residual_wilson_z == pytest.approx(1.645)
    assert Settings.observation_tier_80_probability == pytest.approx(0.80)
    assert Settings.observation_tier_90_probability == pytest.approx(0.90)
    assert Settings.observation_tier_95_probability == pytest.approx(0.95)
    assert Settings.observation_tier_80_fraction == pytest.approx(0.10)
    assert Settings.observation_tier_90_fraction == pytest.approx(0.20)
    assert Settings.observation_tier_95_fraction == pytest.approx(0.20)
    assert Settings.observation_tier_95_fraction <= Settings.max_single_market_fraction


def test_load_settings_reads_station_residual_probability_and_sizing_tiers(monkeypatch):
    monkeypatch.setenv("STATION_RESIDUAL_PROBABILITY_ENABLED", "false")
    monkeypatch.setenv("STATION_RESIDUAL_PROFILE_PATH", "data/custom-residual-profiles.json")
    monkeypatch.setenv("STATION_RESIDUAL_MIN_SAMPLE_DAYS", "90")
    monkeypatch.setenv("STATION_RESIDUAL_WILSON_Z", "2.0")
    monkeypatch.setenv("OBSERVATION_TIER_80_PROBABILITY", "0.81")
    monkeypatch.setenv("OBSERVATION_TIER_90_PROBABILITY", "0.91")
    monkeypatch.setenv("OBSERVATION_TIER_95_PROBABILITY", "0.96")
    monkeypatch.setenv("OBSERVATION_TIER_80_FRACTION", "0.11")
    monkeypatch.setenv("OBSERVATION_TIER_90_FRACTION", "0.26")
    monkeypatch.setenv("OBSERVATION_TIER_95_FRACTION", "0.49")

    settings = load_settings()

    assert settings.station_residual_probability_enabled is False
    assert settings.station_residual_profile_path == "data/custom-residual-profiles.json"
    assert settings.station_residual_min_sample_days == 90
    assert settings.station_residual_wilson_z == pytest.approx(2.0)
    assert settings.observation_tier_80_probability == pytest.approx(0.81)
    assert settings.observation_tier_90_probability == pytest.approx(0.91)
    assert settings.observation_tier_95_probability == pytest.approx(0.96)
    assert settings.observation_tier_80_fraction == pytest.approx(0.11)
    assert settings.observation_tier_90_fraction == pytest.approx(0.26)
    assert settings.observation_tier_95_fraction == pytest.approx(0.49)


@pytest.mark.parametrize(
    ("override", "expected_name"),
    [
        ({"observation_tier_80_probability": -0.01}, "OBSERVATION_TIER_80_PROBABILITY"),
        ({"observation_tier_95_probability": 1.01}, "OBSERVATION_TIER_95_PROBABILITY"),
        ({"observation_tier_80_fraction": -0.01}, "OBSERVATION_TIER_80_FRACTION"),
        ({"observation_tier_95_fraction": 1.01}, "OBSERVATION_TIER_95_FRACTION"),
    ],
)
def test_station_residual_tier_values_must_be_probabilities(override, expected_name):
    with pytest.raises(ValueError, match=expected_name):
        Settings(**override)


@pytest.mark.parametrize(
    ("override", "expected_reason"),
    [
        ({"observation_tier_90_probability": 0.80}, "strictly ascending"),
        ({"observation_tier_95_probability": 0.89}, "strictly ascending"),
        ({"observation_tier_90_fraction": 0.09}, "ascending"),
        ({"observation_tier_95_fraction": 0.19}, "ascending"),
    ],
)
def test_station_residual_tiers_must_be_strictly_ascending(override, expected_reason):
    with pytest.raises(ValueError, match=expected_reason):
        Settings(**override)


def test_station_residual_top_fraction_cannot_exceed_single_market_cap():
    with pytest.raises(ValueError, match="OBSERVATION_TIER_95_FRACTION"):
        Settings(
            max_single_market_fraction=0.48,
            observation_tier_95_fraction=0.49,
        )


def test_default_top_tier_rejects_lower_single_market_cap():
    with pytest.raises(ValueError, match="OBSERVATION_TIER_95_FRACTION"):
        Settings(max_single_market_fraction=0.10)


@pytest.mark.parametrize("sample_days", [0, -1, 1.5, True])
def test_station_residual_min_sample_days_must_be_positive_integer(sample_days):
    with pytest.raises(ValueError, match="STATION_RESIDUAL_MIN_SAMPLE_DAYS"):
        Settings(station_residual_min_sample_days=sample_days)


@pytest.mark.parametrize("wilson_z", [0.0, -1.0, float("inf"), float("-inf"), float("nan")])
def test_station_residual_wilson_z_must_be_finite_and_positive(wilson_z):
    with pytest.raises(ValueError, match="STATION_RESIDUAL_WILSON_Z"):
        Settings(station_residual_wilson_z=wilson_z)


@pytest.mark.parametrize(
    "strategy_mode",
    [
        "lock_only",
        "upstream_lock_paper",
        "intraday_observation_edge",
        "hybrid_observation_edge",
    ],
)
def test_strategy_mode_allowed_values(strategy_mode):
    assert Settings(strategy_mode=strategy_mode).strategy_mode == strategy_mode


def test_strategy_mode_rejects_unknown_value():
    with pytest.raises(ValueError, match="STRATEGY_MODE"):
        Settings(strategy_mode="forecast_only")


@pytest.mark.parametrize(
    "override",
    [
        {"intraday_high_confirm_local_hour": 24},
        {"intraday_low_confirm_local_hour": -1},
        {"intraday_us_high_disabled_before_local_hour": 24},
    ],
)
def test_intraday_local_hours_must_be_clock_hours(override):
    with pytest.raises(ValueError, match="between 0 and 23"):
        Settings(**override)


@pytest.mark.parametrize(
    ("override", "expected_name"),
    [
        ({"intraday_min_side_probability": 0.89}, "INTRADAY_MIN_SIDE_PROBABILITY"),
        ({"intraday_strong_side_probability": 0.96}, "INTRADAY_STRONG_SIDE_PROBABILITY"),
        ({"intraday_abnormal_min_net_edge": 0.19}, "INTRADAY_ABNORMAL_MIN_NET_EDGE"),
    ],
)
def test_intraday_strategy_rejects_values_below_safety_floors(override, expected_name):
    with pytest.raises(ValueError, match=expected_name):
        Settings(**override)


def test_default_realtime_orderbook_rest_snapshot_is_bounded_verification():
    assert Settings.orderbook_stream_enabled is True
    assert Settings.orderbook_rest_snapshot_enabled is True
    assert Settings.orderbook_rest_snapshot_interval_seconds == 60


def test_default_station_nowcast_is_pilot_cached_and_freshness_bounded():
    assert Settings.station_nowcast_enabled is True
    assert Settings.station_nowcast_cache_ttl_seconds == 60  # 1 min: matches AWC METAR documented API cadence
    assert Settings.station_refresh_poll_seconds == 5
    assert Settings.kma_metar_service_key == ""
    assert Settings.kma_metar_poll_seconds == 30
    assert Settings.kma_metar_timeout_seconds == 3.0
    assert Settings.kma_metar_station_ids == "RKSI,RKPK"
    assert Settings.wunderground_api_key == ""
    assert Settings.wunderground_fast_shadow_enabled is False
    assert Settings.station_nowcast_freshness_seconds == 5400
    assert Settings.station_nowcast_request_log_path == ""
    assert Settings.hko_rollover_state_path == ""


def test_default_raw_snapshot_mode_saves_only_error_diagnostics():
    assert Settings.raw_snapshots_mode == "error"
    assert Settings.raw_snapshots_max_bytes == 100 * 1024 * 1024
    assert Settings.raw_snapshots_retention_days == 7
    assert Settings.raw_snapshots_min_free_bytes == 1024 * 1024 * 1024
    assert Settings.raw_snapshots_max_disk_usage_pct == 0.90
    assert Settings.skip_diagnostics_enabled is True
    assert Settings.skip_diagnostics_jsonl_path == ""
    assert Settings.skip_diagnostics_max_bytes == 10 * 1024 * 1024
    assert Settings.skip_diagnostics_archive_max_bytes == 20 * 1024 * 1024


def test_default_entry_net_return_filter_uses_official_weather_fee_rate():
    assert Settings.min_net_edge == 0.08
    assert Settings.entry_min_expected_net_return_pct == 0.04
    assert Settings.max_entry_spread_abs == 0.20
    assert Settings.max_entry_spread_pct == 1.00
    assert Settings.weather_taker_fee_rate == 0.05
    assert Settings.min_profit_pct == 0.08


def test_default_settlement_runner_is_bounded_and_enabled():
    assert Settings.settlement_runner_enabled is True
    assert Settings.settlement_runner_max_fraction == 1.00
    assert Settings.settlement_runner_min_ev_margin_usd == 0.0


def test_default_city_date_portfolio_caps_shrink_after_one_thousand_dollars():
    assert Settings.bankroll_usd == 200.0
    assert Settings.size_mode == "kelly"
    assert Settings.entry_fraction == 0.20
    assert Settings.fractional_kelly == 0.25
    assert Settings.max_single_market_fraction == 0.50
    assert Settings.add_to_position_drop_pct == 0.10
    assert Settings.max_city_exposure_fraction == 0.20
    assert Settings.max_event_date_exposure_fraction == 0.10
    assert Settings.large_bankroll_event_date_exposure_fraction == 0.05
    assert Settings.event_date_exposure_transition_usd == 1000.0
    assert Settings.max_event_portfolio_legs == 2
    assert Settings.daily_realized_loss_limit_fraction == 0.0
    assert Settings.daily_unrealized_loss_limit_fraction == 0.0
    assert Settings.large_loss_threshold_fraction == 0.50
    assert Settings.large_loss_cooldown_hours == 0.0
    assert Settings.max_total_exposure_fraction == 0.90
    assert Settings.min_order_usd == 10.0
    assert Settings.official_nowcast_lock_enabled is True
    assert Settings.official_nowcast_entry_only is False
    assert Settings.official_nowcast_lock_base_entry_fraction == 0.20
    assert Settings.official_nowcast_lock_strong_entry_fraction == 0.50
    assert Settings.official_nowcast_lock_near_close_hours == 3.0
    assert Settings.official_nowcast_lock_yes_base_buffer_c == 0.50
    assert Settings.official_nowcast_lock_yes_strong_buffer_c == 0.75


def test_default_settings_pass_numeric_range_validation():
    settings = Settings()

    assert settings.bankroll_usd == 200.0
    assert settings.min_order_usd == 10.0
    assert settings.max_total_exposure_fraction == 0.90


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


@pytest.mark.parametrize(
    ("override", "expected_name"),
    [
        ({"bankroll_usd": 0.0}, "BANKROLL_USD"),
        ({"stream_cycle_interval_seconds": 0}, "STREAM_CYCLE_INTERVAL_SECONDS"),
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
    monkeypatch.setenv("MAX_ENTRY_SPREAD_ABS", "0.12")
    monkeypatch.setenv("MAX_ENTRY_SPREAD_PCT", "0.75")
    monkeypatch.setenv("WEATHER_TAKER_FEE_RATE", "0.04")
    monkeypatch.setenv("SETTLEMENT_RUNNER_ENABLED", "false")
    monkeypatch.setenv("SETTLEMENT_RUNNER_MAX_FRACTION", "0.20")
    monkeypatch.setenv("SETTLEMENT_RUNNER_MIN_EV_MARGIN_USD", "1.25")
    monkeypatch.setenv("OFFICIAL_NOWCAST_LOCK_ENABLED", "false")
    monkeypatch.setenv("OFFICIAL_NOWCAST_ENTRY_ONLY", "true")
    monkeypatch.setenv("OFFICIAL_NOWCAST_LOCK_BASE_ENTRY_FRACTION", "0.21")
    monkeypatch.setenv("OFFICIAL_NOWCAST_LOCK_STRONG_ENTRY_FRACTION", "0.49")
    monkeypatch.setenv("OFFICIAL_NOWCAST_LOCK_NEAR_CLOSE_HOURS", "2.5")
    monkeypatch.setenv("OFFICIAL_NOWCAST_LOCK_YES_BASE_BUFFER_C", "0.40")
    monkeypatch.setenv("OFFICIAL_NOWCAST_LOCK_YES_STRONG_BUFFER_C", "0.80")

    settings = load_settings()

    assert not hasattr(settings, "enable_precipitation_markets")
    assert settings.probability_stop_drop_threshold == 0.08
    assert settings.entry_min_expected_net_return_pct == 0.07
    assert settings.max_entry_spread_abs == 0.12
    assert settings.max_entry_spread_pct == 0.75
    assert settings.weather_taker_fee_rate == 0.04
    assert settings.settlement_runner_enabled is False
    assert settings.settlement_runner_max_fraction == 0.20
    assert settings.settlement_runner_min_ev_margin_usd == 1.25
    assert settings.official_nowcast_lock_enabled is False
    assert settings.official_nowcast_entry_only is True
    assert settings.official_nowcast_lock_base_entry_fraction == 0.21
    assert settings.official_nowcast_lock_strong_entry_fraction == 0.49
    assert settings.official_nowcast_lock_near_close_hours == 2.5
    assert settings.official_nowcast_lock_yes_base_buffer_c == 0.40
    assert settings.official_nowcast_lock_yes_strong_buffer_c == 0.80


def test_load_settings_reads_intraday_observation_edge_controls(monkeypatch):
    monkeypatch.setenv("STRATEGY_MODE", "intraday_observation_edge")
    monkeypatch.setenv("INTRADAY_OBSERVATION_EDGE_ENABLED", "false")
    monkeypatch.setenv("INTRADAY_MIN_SIDE_PROBABILITY", "0.91")
    monkeypatch.setenv("INTRADAY_STRONG_SIDE_PROBABILITY", "0.98")
    monkeypatch.setenv("INTRADAY_BASE_ENTRY_FRACTION", "0.11")
    monkeypatch.setenv("INTRADAY_STRONG_ENTRY_FRACTION", "0.26")
    monkeypatch.setenv("INTRADAY_ABNORMAL_PRICE_ENTRY_FRACTION", "0.36")
    monkeypatch.setenv("INTRADAY_ABNORMAL_MIN_NET_EDGE", "0.21")
    monkeypatch.setenv("INTRADAY_HKO_NEEDS_AUDIT_FRACTION_MULTIPLIER", "0.20")
    monkeypatch.setenv("INTRADAY_HIGH_CONFIRM_LOCAL_HOUR", "14")
    monkeypatch.setenv("INTRADAY_LOW_CONFIRM_LOCAL_HOUR", "7")
    monkeypatch.setenv("INTRADAY_US_HIGH_DISABLED_BEFORE_LOCAL_HOUR", "16")
    monkeypatch.setenv("INTRADAY_EXACT_LOW_YES_ENABLED", "true")
    monkeypatch.setenv("INTRADAY_US_EXACT_LOW_YES_ENABLED", "false")
    monkeypatch.setenv("INTRADAY_ENABLE_ABOVE_BUCKET_NO", "true")
    monkeypatch.setenv("NO_ONLY_NEW_ENTRIES", "false")

    settings = load_settings()

    assert settings.strategy_mode == "intraday_observation_edge"
    assert settings.intraday_observation_edge_enabled is False
    assert settings.intraday_min_side_probability == pytest.approx(0.91)
    assert settings.intraday_strong_side_probability == pytest.approx(0.98)
    assert settings.intraday_base_entry_fraction == pytest.approx(0.11)
    assert settings.intraday_strong_entry_fraction == pytest.approx(0.26)
    assert settings.intraday_abnormal_price_entry_fraction == pytest.approx(0.36)
    assert settings.intraday_abnormal_min_net_edge == pytest.approx(0.21)
    assert settings.intraday_hko_needs_audit_fraction_multiplier == pytest.approx(0.20)
    assert settings.intraday_high_confirm_local_hour == 14
    assert settings.intraday_low_confirm_local_hour == 7
    assert settings.intraday_us_high_disabled_before_local_hour == 16
    assert settings.intraday_exact_low_yes_enabled is True
    assert settings.intraday_us_exact_low_yes_enabled is False
    assert settings.intraday_enable_above_bucket_no is True
    assert settings.no_only_new_entries is False


def test_default_observation_tiers_cap_unconfirmed_probability_sizing():
    settings = Settings()

    assert settings.observation_tier_90_fraction == pytest.approx(0.20)
    assert settings.observation_tier_95_fraction == pytest.approx(0.20)


def test_load_settings_reads_station_nowcast_controls(monkeypatch):
    monkeypatch.setenv("STATION_NOWCAST_ENABLED", "false")
    monkeypatch.setenv("STATION_NOWCAST_CACHE_TTL_SECONDS", "300")
    monkeypatch.setenv("STATION_NOWCAST_FRESHNESS_SECONDS", "1800")
    monkeypatch.setenv("STATION_NOWCAST_REQUEST_LOG_PATH", "data/custom_station_nowcast_request_log.jsonl")
    monkeypatch.setenv("STATION_REFRESH_POLL_SECONDS", "7")
    monkeypatch.setenv("KMA_METAR_SERVICE_KEY", "secret-value")
    monkeypatch.setenv("KMA_METAR_POLL_SECONDS", "25")
    monkeypatch.setenv("KMA_METAR_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("KMA_METAR_STATION_IDS", "RKSI")
    monkeypatch.setenv("WUNDERGROUND_API_KEY", "wu-secret-value")
    monkeypatch.setenv("WUNDERGROUND_FAST_SHADOW_ENABLED", "true")
    monkeypatch.setenv("HKO_ROLLOVER_STATE_PATH", "data/custom_hko_rollover_state.json")

    settings = load_settings()

    assert settings.station_nowcast_enabled is False
    assert settings.station_nowcast_cache_ttl_seconds == 300
    assert settings.station_nowcast_freshness_seconds == 1800
    assert settings.station_nowcast_request_log_path == "data/custom_station_nowcast_request_log.jsonl"
    assert settings.station_refresh_poll_seconds == 7
    assert settings.kma_metar_service_key == "secret-value"
    assert settings.kma_metar_poll_seconds == 25
    assert settings.kma_metar_timeout_seconds == 2.5
    assert settings.kma_metar_station_ids == "RKSI"
    assert settings.wunderground_api_key == "wu-secret-value"
    assert settings.wunderground_fast_shadow_enabled is True
    assert settings.hko_rollover_state_path == "data/custom_hko_rollover_state.json"


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
    monkeypatch.setenv("MAX_EVENT_PORTFOLIO_LEGS", "1")

    settings = load_settings()

    assert settings.portfolio_decisions_jsonl_path == "data/custom-portfolios.jsonl"
    assert settings.add_to_position_drop_pct == 0.15
    assert settings.max_city_exposure_fraction == 0.11
    assert settings.max_event_date_exposure_fraction == 0.12
    assert settings.large_bankroll_event_date_exposure_fraction == 0.04
    assert settings.event_date_exposure_transition_usd == 1200.0
    assert settings.max_event_portfolio_legs == 1


def test_load_settings_rejects_more_than_two_city_date_portfolio_legs(monkeypatch):
    monkeypatch.setenv("MAX_EVENT_PORTFOLIO_LEGS", "3")

    with pytest.raises(ValueError, match="MAX_EVENT_PORTFOLIO_LEGS.*at most 2"):
        load_settings()


def test_load_settings_reads_raw_snapshot_storage_mode(monkeypatch):
    monkeypatch.setenv("RAW_SNAPSHOTS_MODE", "debug")
    monkeypatch.setenv("RAW_SNAPSHOTS_MAX_BYTES", "12345")
    monkeypatch.setenv("RAW_SNAPSHOTS_RETENTION_DAYS", "9")
    monkeypatch.setenv("RAW_SNAPSHOTS_MIN_FREE_BYTES", "54321")
    monkeypatch.setenv("RAW_SNAPSHOTS_MAX_DISK_USAGE_PCT", "0.75")
    monkeypatch.setenv("SKIP_DIAGNOSTICS_ENABLED", "false")
    monkeypatch.setenv("SKIP_DIAGNOSTICS_JSONL_PATH", "data/skip.jsonl")
    monkeypatch.setenv("SKIP_DIAGNOSTICS_MAX_BYTES", "22222")
    monkeypatch.setenv("SKIP_DIAGNOSTICS_ARCHIVE_MAX_BYTES", "33333")

    settings = load_settings()

    assert settings.raw_snapshots_mode == "debug"
    assert settings.raw_snapshots_max_bytes == 12345
    assert settings.raw_snapshots_retention_days == 9
    assert settings.raw_snapshots_min_free_bytes == 54321
    assert settings.raw_snapshots_max_disk_usage_pct == 0.75
    assert settings.skip_diagnostics_enabled is False
    assert settings.skip_diagnostics_jsonl_path == "data/skip.jsonl"
    assert settings.skip_diagnostics_max_bytes == 22222
    assert settings.skip_diagnostics_archive_max_bytes == 33333


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
