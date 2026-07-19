from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import os
from dotenv import load_dotenv

_TRUE_ENV_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_ENV_VALUES = {"0", "false", "no", "n", "off"}

_POSITIVE_NUMBER_SETTINGS = (
    "orderbook_stream_heartbeat_seconds",
    "orderbook_stream_reconnect_seconds",
    "orderbook_stream_stale_seconds",
    "orderbook_rest_snapshot_interval_seconds",
    "runner_health_status_interval_seconds",
    "stream_cycle_interval_seconds",
    "station_nowcast_cache_ttl_seconds",
    "station_refresh_poll_seconds",
    "kma_metar_poll_seconds",
    "kma_metar_timeout_seconds",
    "station_nowcast_freshness_seconds",
    "bankroll_usd",
    "min_order_usd",
    "event_date_exposure_transition_usd",
    "max_holding_hours",
    "station_residual_wilson_z",
)

_POSITIVE_INTEGER_SETTINGS = (
    "discovery_max_pages",
    "discovery_page_size",
    "max_event_portfolio_legs",
    "raw_snapshots_max_bytes",
    "raw_snapshots_retention_days",
    "raw_snapshots_min_free_bytes",
    "skip_diagnostics_max_bytes",
    "skip_diagnostics_archive_max_bytes",
)

_MINIMUM_INTEGER_SETTINGS = (
    ("station_residual_min_sample_days", 1),
    ("max_consecutive_losses", 0),
)

_TCP_PORT_SETTINGS = (
    "dashboard_port",
)

_HOUR_SETTINGS = (
    "intraday_high_confirm_local_hour",
    "intraday_low_confirm_local_hour",
    "intraday_us_high_disabled_before_local_hour",
)

_RATIO_SETTINGS = (
    "min_net_edge",
    "exit_net_edge",
    "probability_stop_drop_threshold",
    "min_profit_pct",
    "take_profit_to_fair_ratio",
    "overheat_margin",
    "edge_fade_max_loss_pct",
    "add_to_position_drop_pct",
    "entry_fraction",
    "fractional_kelly",
    "max_single_market_fraction",
    "max_total_exposure_fraction",
    "max_city_exposure_fraction",
    "max_event_date_exposure_fraction",
    "large_bankroll_event_date_exposure_fraction",
    "entry_min_expected_net_return_pct",
    "intraday_min_side_probability",
    "intraday_strong_side_probability",
    "intraday_base_entry_fraction",
    "intraday_strong_entry_fraction",
    "intraday_abnormal_price_entry_fraction",
    "intraday_abnormal_min_net_edge",
    "intraday_hko_needs_audit_fraction_multiplier",
    "observation_tier_80_probability",
    "observation_tier_90_probability",
    "observation_tier_95_probability",
    "observation_tier_80_fraction",
    "observation_tier_90_fraction",
    "observation_tier_95_fraction",
    "official_nowcast_lock_base_entry_fraction",
    "official_nowcast_lock_strong_entry_fraction",
    "daily_realized_loss_limit_fraction",
    "daily_unrealized_loss_limit_fraction",
    "large_loss_threshold_fraction",
    "max_entry_spread_abs",
    "max_entry_spread_pct",
    "model_error_margin",
    "resolution_error_margin",
    "settlement_runner_max_fraction",
    "probability_shrink_gamma",
    "confidence_size_floor",
    "raw_snapshots_max_disk_usage_pct",
)

_NON_NEGATIVE_NUMBER_SETTINGS = (
    "settlement_runner_min_ev_margin_usd",
    "official_nowcast_lock_near_close_hours",
    "official_nowcast_lock_yes_base_buffer_c",
    "official_nowcast_lock_yes_strong_buffer_c",
    "city_loss_cooldown_hours",
    "large_loss_cooldown_hours",
)

_RATE_SETTINGS = (
    "weather_taker_fee_rate",
)

_RAW_SNAPSHOT_MODES = ("off", "error", "debug")
_SIZE_MODES = ("fixed_fraction", "kelly")
_STRATEGY_MODES = ("lock_only", "intraday_observation_edge", "hybrid_observation_edge")


@dataclass(frozen=True)
class Settings:
    # Public data endpoints
    gamma_base: str = "https://gamma-api.polymarket.com"
    clob_base: str = "https://clob.polymarket.com"

    # Paper-trading loop
    orderbook_stream_enabled: bool = True
    orderbook_stream_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    orderbook_stream_heartbeat_seconds: int = 10
    orderbook_stream_reconnect_seconds: int = 2
    orderbook_stream_stale_seconds: int = 60
    orderbook_rest_snapshot_enabled: bool = True
    orderbook_rest_snapshot_interval_seconds: int = 60
    runner_health_status_interval_seconds: int = 5
    stream_cycle_interval_seconds: int = 2400
    discovery_max_pages: int = 8
    discovery_page_size: int = 100
    state_path: str = "paper_state.json"
    trades_csv_path: str = "paper_trades.csv"
    decisions_csv_path: str = "paper_decisions.csv"
    # When False (default), SKIP rows are NOT written to paper_decisions.csv.
    # SKIP rows are 95%+ of all writes and have zero analytical value.
    # Set DECISIONS_LOG_SKIP_ENABLED=true only for short debugging sessions.
    decisions_log_skip_enabled: bool = False
    skip_diagnostics_enabled: bool = True
    skip_diagnostics_jsonl_path: str = ""
    skip_diagnostics_max_bytes: int = 10 * 1024 * 1024
    skip_diagnostics_archive_max_bytes: int = 20 * 1024 * 1024
    portfolio_decisions_jsonl_path: str = "paper_event_portfolios.jsonl"
    # When False (default), paper_event_portfolios.jsonl is only written when
    # at least one trade is actually selected (not on every SKIP evaluation).
    portfolio_log_skip_enabled: bool = False
    raw_snapshots_path: str = "paper_raw_snapshots.jsonl"
    raw_snapshots_mode: str = "error"
    raw_snapshots_max_bytes: int = 100 * 1024 * 1024
    raw_snapshots_retention_days: int = 7
    raw_snapshots_min_free_bytes: int = 1024 * 1024 * 1024
    raw_snapshots_max_disk_usage_pct: float = 0.90
    station_nowcast_enabled: bool = True
    station_nowcast_cache_ttl_seconds: int = 60  # 1 min: matches AWC METAR documented API cadence
    station_refresh_poll_seconds: int = 5
    kma_metar_service_key: str = ""
    kma_metar_poll_seconds: int = 30
    kma_metar_timeout_seconds: float = 3.0
    kma_metar_station_ids: str = "RKSI,RKPK"
    wunderground_api_key: str = ""
    wunderground_fast_shadow_enabled: bool = False
    station_nowcast_freshness_seconds: int = 5400
    station_nowcast_request_log_path: str = ""
    hko_rollover_state_path: str = ""
    metar_daily_extremes_state_path: str = ""
    station_residual_probability_enabled: bool = True
    station_residual_profile_path: str = "strategy_data/station_residual_profiles.json"
    station_residual_min_sample_days: int = 60
    station_residual_wilson_z: float = 1.645
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8787
    dashboard_token: str = ""

    # Strategy thresholds
    min_net_edge: float = 0.08
    exit_net_edge: float = 0.00
    # Exit policy: stop is station-side-probability based; profit is model-fair-value based.
    probability_stop_drop_threshold: float = 0.10
    min_profit_pct: float = 0.08
    take_profit_to_fair_ratio: float = 0.70
    overheat_margin: float = 0.02
    edge_fade_max_loss_pct: float = 0.02
    add_to_position_drop_pct: float = 0.10
    max_holding_hours: float = 96.0

    # Risk / sizing. Strategy-specific fractions still pass these portfolio
    # caps, so a strong signal cannot consume the whole paper bankroll.
    size_mode: str = "kelly"
    entry_fraction: float = 0.20
    fractional_kelly: float = 0.25
    max_single_market_fraction: float = 0.50
    max_total_exposure_fraction: float = 0.90
    bankroll_usd: float = 200.0
    min_order_usd: float = 10.0

    # City concentration cap. Related markets from the same weather event should
    # not quietly multiply risk.
    # Example: NYC 70F + NYC 72F + NYC 75F all belong to the same temperature
    # event. Three 5% entries would create 15% exposure to one city.
    # max_city_exposure_fraction caps total position cost for one city divided
    # by bankroll.
    max_city_exposure_fraction: float = 0.20
    # Small paper accounts start with a 10% city-date budget. From a $1,000
    # reference bankroll, that shared city-date budget shrinks to 5%.
    max_event_date_exposure_fraction: float = 0.10
    large_bankroll_event_date_exposure_fraction: float = 0.05
    event_date_exposure_transition_usd: float = 1000.0
    max_event_portfolio_legs: int = 2
    daily_realized_loss_limit_fraction: float = 0.0
    daily_unrealized_loss_limit_fraction: float = 0.0
    max_consecutive_losses: int = 0
    city_loss_cooldown_hours: float = 0.0
    large_loss_threshold_fraction: float = 0.50
    large_loss_cooldown_hours: float = 0.0

    # Paper weather-fee default from the official category schedule.
    # A separate live-execution project must query fee parameters per market.
    entry_min_expected_net_return_pct: float = 0.04
    max_entry_spread_abs: float = 0.20
    max_entry_spread_pct: float = 1.00
    weather_taker_fee_rate: float = 0.05
    model_error_margin: float = 0.03
    resolution_error_margin: float = 0.01
    settlement_runner_enabled: bool = True
    settlement_runner_max_fraction: float = 1.00
    settlement_runner_min_ev_margin_usd: float = 0.0

    # Official same-station strategy modes. Intraday entries remain paper-only
    # and pass the same executable-depth and portfolio gates as station locks.
    strategy_mode: str = "hybrid_observation_edge"
    # New paper entries are NO-only by default. Existing YES positions remain
    # eligible for normal exit handling.
    no_only_new_entries: bool = True
    intraday_observation_edge_enabled: bool = True
    intraday_min_side_probability: float = 0.90
    intraday_strong_side_probability: float = 0.97
    intraday_base_entry_fraction: float = 0.10
    intraday_strong_entry_fraction: float = 0.25
    intraday_abnormal_price_entry_fraction: float = 0.35
    intraday_abnormal_min_net_edge: float = 0.20
    intraday_hko_needs_audit_fraction_multiplier: float = 0.25
    # Deprecated input compatibility only. Formation timing comes from the
    # verified station/month/direction manifest and these values are not used
    # by the active strategy.
    intraday_high_confirm_local_hour: int = 15
    intraday_low_confirm_local_hour: int = 8
    intraday_us_high_disabled_before_local_hour: int = 15
    intraday_exact_low_yes_enabled: bool = False
    intraday_us_exact_low_yes_enabled: bool = True
    intraday_enable_above_bucket_no: bool = False
    observation_tier_80_probability: float = 0.80
    observation_tier_90_probability: float = 0.90
    observation_tier_95_probability: float = 0.95
    observation_tier_80_fraction: float = 0.10
    observation_tier_90_fraction: float = 0.20
    observation_tier_95_fraction: float = 0.20

    official_nowcast_lock_enabled: bool = True
    official_nowcast_entry_only: bool = False
    official_nowcast_lock_base_entry_fraction: float = 0.20
    official_nowcast_lock_strong_entry_fraction: float = 0.50
    official_nowcast_lock_near_close_hours: float = 3.0
    official_nowcast_lock_yes_base_buffer_c: float = 0.50
    official_nowcast_lock_yes_strong_buffer_c: float = 0.75

    # Station-lock score controls
    probability_shrink_gamma: float = 0.65
    confidence_size_floor: float = 0.25
    require_parse_for_trade: bool = True
    # Compatibility switch for older tests/env files. The paper runner still
    # requires an explicit date before station-signal work or trade, even when this is False.
    require_date_hint_for_trade: bool = True
    # Defensive close compares the current station-lock side score with the
    # entry-side score. YES uses p_true; NO uses 1 - p_true.

    def __post_init__(self) -> None:
        _validate_positive_numbers(self, _POSITIVE_NUMBER_SETTINGS)
        _validate_positive_integers(self, _POSITIVE_INTEGER_SETTINGS)
        if self.max_event_portfolio_legs > 2:
            raise ValueError(
                "MAX_EVENT_PORTFOLIO_LEGS must be at most 2; "
                f"got {self.max_event_portfolio_legs!r}"
            )
        _validate_minimum_integers(self, _MINIMUM_INTEGER_SETTINGS)
        _validate_tcp_ports(self, _TCP_PORT_SETTINGS)
        _validate_hours(self, _HOUR_SETTINGS)
        _validate_ratios(self, _RATIO_SETTINGS)
        _validate_non_negative_numbers(self, _NON_NEGATIVE_NUMBER_SETTINGS)
        _validate_rates(self, _RATE_SETTINGS)
        _validate_choice(self, "size_mode", _SIZE_MODES)
        _validate_choice(self, "raw_snapshots_mode", _RAW_SNAPSHOT_MODES)
        _validate_choice(self, "strategy_mode", _STRATEGY_MODES)
        _validate_intraday_strategy_safety(self)
        _validate_observation_tiers(self)


def _setting_display_name(field_name: str) -> str:
    return field_name.upper()


def _finite_number(settings: Settings, field_name: str) -> float:
    value = getattr(settings, field_name)
    if isinstance(value, bool):
        raise ValueError(f"{_setting_display_name(field_name)} must be a number; got {value!r}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{_setting_display_name(field_name)} must be a number; got {value!r}") from exc
    if not isfinite(numeric):
        raise ValueError(f"{_setting_display_name(field_name)} must be finite; got {value!r}")
    return numeric


def _validate_positive_numbers(settings: Settings, field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        value = _finite_number(settings, field_name)
        if value <= 0:
            raise ValueError(f"{_setting_display_name(field_name)} must be greater than 0; got {value!r}")


def _validate_positive_integers(settings: Settings, field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        value = getattr(settings, field_name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{_setting_display_name(field_name)} must be a positive integer; got {value!r}")
        if value <= 0:
            raise ValueError(f"{_setting_display_name(field_name)} must be greater than 0; got {value!r}")


def _validate_minimum_integers(settings: Settings, field_specs: tuple[tuple[str, int], ...]) -> None:
    for field_name, minimum in field_specs:
        value = getattr(settings, field_name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                f"{_setting_display_name(field_name)} must be an integer at least {minimum}; got {value!r}"
            )
        if value < minimum:
            raise ValueError(f"{_setting_display_name(field_name)} must be at least {minimum}; got {value!r}")


def _validate_tcp_ports(settings: Settings, field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        value = getattr(settings, field_name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                f"{_setting_display_name(field_name)} must be an integer between 1 and 65535; got {value!r}"
            )
        if value < 1 or value > 65535:
            raise ValueError(f"{_setting_display_name(field_name)} must be between 1 and 65535; got {value!r}")


def _validate_hours(settings: Settings, field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        value = getattr(settings, field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 23:
            raise ValueError(
                f"{_setting_display_name(field_name)} must be an integer between 0 and 23; got {value!r}"
            )


def _validate_ratios(settings: Settings, field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        value = _finite_number(settings, field_name)
        if value < 0 or value > 1:
            raise ValueError(f"{_setting_display_name(field_name)} must be between 0 and 1; got {value!r}")


def _validate_non_negative_numbers(settings: Settings, field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        value = _finite_number(settings, field_name)
        if value < 0:
            raise ValueError(f"{_setting_display_name(field_name)} must be at least 0; got {value!r}")


def _validate_rates(settings: Settings, field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        value = _finite_number(settings, field_name)
        if value < 0:
            raise ValueError(f"{_setting_display_name(field_name)} must be at least 0; got {value!r}")
        if value > 1:
            raise ValueError(f"{_setting_display_name(field_name)} must be at most 1; got {value!r}")


def _validate_choice(settings: Settings, field_name: str, allowed_values: tuple[str, ...]) -> None:
    value = getattr(settings, field_name)
    if not isinstance(value, str):
        raise ValueError(f"{_setting_display_name(field_name)} must be one of {allowed_values}; got {value!r}")
    normalized = value.strip().lower()
    if normalized not in allowed_values:
        allowed = ", ".join(allowed_values)
        raise ValueError(f"{_setting_display_name(field_name)} must be one of: {allowed}; got {value!r}")
    if normalized != value:
        object.__setattr__(settings, field_name, normalized)


def _validate_intraday_strategy_safety(settings: Settings) -> None:
    safety_floors = (
        ("intraday_min_side_probability", 0.90),
        ("intraday_strong_side_probability", 0.97),
        ("intraday_abnormal_min_net_edge", 0.20),
    )
    for field_name, minimum in safety_floors:
        value = _finite_number(settings, field_name)
        if value < minimum:
            raise ValueError(
                f"{_setting_display_name(field_name)} must be at least {minimum:.2f}; got {value!r}"
            )


def _validate_observation_tiers(settings: Settings) -> None:
    probability_fields = (
        "observation_tier_80_probability",
        "observation_tier_90_probability",
        "observation_tier_95_probability",
    )
    fraction_fields = (
        "observation_tier_80_fraction",
        "observation_tier_90_fraction",
        "observation_tier_95_fraction",
    )
    probability_values = tuple(_finite_number(settings, field_name) for field_name in probability_fields)
    if not probability_values[0] < probability_values[1] < probability_values[2]:
        names = ", ".join(_setting_display_name(field_name) for field_name in probability_fields)
        raise ValueError(f"{names} must be strictly ascending; got {probability_values!r}")

    fraction_values = tuple(_finite_number(settings, field_name) for field_name in fraction_fields)
    if not fraction_values[0] <= fraction_values[1] <= fraction_values[2]:
        names = ", ".join(_setting_display_name(field_name) for field_name in fraction_fields)
        raise ValueError(f"{names} must be ascending; got {fraction_values!r}")

    top_fraction = _finite_number(settings, "observation_tier_95_fraction")
    single_market_cap = _finite_number(settings, "max_single_market_fraction")
    if top_fraction > single_market_cap:
        raise ValueError(
            "OBSERVATION_TIER_95_FRACTION must not exceed MAX_SINGLE_MARKET_FRACTION; "
            f"got {top_fraction!r} > {single_market_cap!r}"
        )


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid numeric value for {name}. Expected a number; got {raw!r}") from exc


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid integer value for {name}. Expected an integer; got {raw!r}") from exc


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_ENV_VALUES:
        return True
    if normalized in _FALSE_ENV_VALUES:
        return False
    allowed = ", ".join(sorted(_TRUE_ENV_VALUES | _FALSE_ENV_VALUES))
    raise ValueError(f"Invalid boolean value for {name}. Expected one of: {allowed}")


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        gamma_base=os.getenv("POLYMARKET_GAMMA_BASE", Settings.gamma_base),
        clob_base=os.getenv("POLYMARKET_CLOB_BASE", Settings.clob_base),
        orderbook_stream_enabled=_bool_env("ORDERBOOK_STREAM_ENABLED", Settings.orderbook_stream_enabled),
        orderbook_stream_url=os.getenv("ORDERBOOK_STREAM_URL", Settings.orderbook_stream_url),
        orderbook_stream_heartbeat_seconds=_int_env("ORDERBOOK_STREAM_HEARTBEAT_SECONDS", Settings.orderbook_stream_heartbeat_seconds),
        orderbook_stream_reconnect_seconds=_int_env("ORDERBOOK_STREAM_RECONNECT_SECONDS", Settings.orderbook_stream_reconnect_seconds),
        orderbook_stream_stale_seconds=_int_env("ORDERBOOK_STREAM_STALE_SECONDS", Settings.orderbook_stream_stale_seconds),
        orderbook_rest_snapshot_enabled=_bool_env(
            "ORDERBOOK_REST_SNAPSHOT_ENABLED",
            Settings.orderbook_rest_snapshot_enabled,
        ),
        orderbook_rest_snapshot_interval_seconds=_int_env(
            "ORDERBOOK_REST_SNAPSHOT_INTERVAL_SECONDS",
            Settings.orderbook_rest_snapshot_interval_seconds,
        ),
        runner_health_status_interval_seconds=_int_env("RUNNER_HEALTH_STATUS_INTERVAL_SECONDS", Settings.runner_health_status_interval_seconds),
        stream_cycle_interval_seconds=_int_env("STREAM_CYCLE_INTERVAL_SECONDS", Settings.stream_cycle_interval_seconds),
        discovery_max_pages=_int_env("DISCOVERY_MAX_PAGES", Settings.discovery_max_pages),
        discovery_page_size=_int_env("DISCOVERY_PAGE_SIZE", Settings.discovery_page_size),
        state_path=os.getenv("STATE_PATH", Settings.state_path),
        trades_csv_path=os.getenv("TRADES_CSV_PATH", Settings.trades_csv_path),
        decisions_csv_path=os.getenv("DECISIONS_CSV_PATH", Settings.decisions_csv_path),
        decisions_log_skip_enabled=_bool_env(
            "DECISIONS_LOG_SKIP_ENABLED", Settings.decisions_log_skip_enabled
        ),
        skip_diagnostics_enabled=_bool_env(
            "SKIP_DIAGNOSTICS_ENABLED",
            Settings.skip_diagnostics_enabled,
        ),
        skip_diagnostics_jsonl_path=os.getenv(
            "SKIP_DIAGNOSTICS_JSONL_PATH",
            Settings.skip_diagnostics_jsonl_path,
        ),
        skip_diagnostics_max_bytes=_int_env(
            "SKIP_DIAGNOSTICS_MAX_BYTES",
            Settings.skip_diagnostics_max_bytes,
        ),
        skip_diagnostics_archive_max_bytes=_int_env(
            "SKIP_DIAGNOSTICS_ARCHIVE_MAX_BYTES",
            Settings.skip_diagnostics_archive_max_bytes,
        ),
        portfolio_decisions_jsonl_path=os.getenv(
            "PORTFOLIO_DECISIONS_JSONL_PATH",
            Settings.portfolio_decisions_jsonl_path,
        ),
        portfolio_log_skip_enabled=_bool_env(
            "PORTFOLIO_LOG_SKIP_ENABLED", Settings.portfolio_log_skip_enabled
        ),
        raw_snapshots_path=os.getenv("RAW_SNAPSHOTS_PATH", Settings.raw_snapshots_path),
        raw_snapshots_mode=os.getenv("RAW_SNAPSHOTS_MODE", Settings.raw_snapshots_mode),
        raw_snapshots_max_bytes=_int_env("RAW_SNAPSHOTS_MAX_BYTES", Settings.raw_snapshots_max_bytes),
        raw_snapshots_retention_days=_int_env(
            "RAW_SNAPSHOTS_RETENTION_DAYS",
            Settings.raw_snapshots_retention_days,
        ),
        raw_snapshots_min_free_bytes=_int_env(
            "RAW_SNAPSHOTS_MIN_FREE_BYTES",
            Settings.raw_snapshots_min_free_bytes,
        ),
        raw_snapshots_max_disk_usage_pct=_float_env(
            "RAW_SNAPSHOTS_MAX_DISK_USAGE_PCT",
            Settings.raw_snapshots_max_disk_usage_pct,
        ),
        station_nowcast_enabled=_bool_env("STATION_NOWCAST_ENABLED", Settings.station_nowcast_enabled),
        station_nowcast_cache_ttl_seconds=_int_env(
            "STATION_NOWCAST_CACHE_TTL_SECONDS",
            Settings.station_nowcast_cache_ttl_seconds,
        ),
        station_refresh_poll_seconds=_int_env(
            "STATION_REFRESH_POLL_SECONDS",
            Settings.station_refresh_poll_seconds,
        ),
        kma_metar_service_key=os.getenv(
            "KMA_METAR_SERVICE_KEY",
            Settings.kma_metar_service_key,
        ),
        kma_metar_poll_seconds=_int_env(
            "KMA_METAR_POLL_SECONDS",
            Settings.kma_metar_poll_seconds,
        ),
        kma_metar_timeout_seconds=_float_env(
            "KMA_METAR_TIMEOUT_SECONDS",
            Settings.kma_metar_timeout_seconds,
        ),
        kma_metar_station_ids=os.getenv(
            "KMA_METAR_STATION_IDS",
            Settings.kma_metar_station_ids,
        ),
        wunderground_api_key=os.getenv(
            "WUNDERGROUND_API_KEY",
            Settings.wunderground_api_key,
        ),
        wunderground_fast_shadow_enabled=_bool_env(
            "WUNDERGROUND_FAST_SHADOW_ENABLED",
            Settings.wunderground_fast_shadow_enabled,
        ),
        station_nowcast_freshness_seconds=_int_env(
            "STATION_NOWCAST_FRESHNESS_SECONDS",
            Settings.station_nowcast_freshness_seconds,
        ),
        station_nowcast_request_log_path=os.getenv(
            "STATION_NOWCAST_REQUEST_LOG_PATH",
            Settings.station_nowcast_request_log_path,
        ),
        hko_rollover_state_path=os.getenv(
            "HKO_ROLLOVER_STATE_PATH",
            Settings.hko_rollover_state_path,
        ),
        metar_daily_extremes_state_path=os.getenv(
            "METAR_DAILY_EXTREMES_STATE_PATH",
            Settings.metar_daily_extremes_state_path,
        ),
        station_residual_probability_enabled=_bool_env(
            "STATION_RESIDUAL_PROBABILITY_ENABLED",
            Settings.station_residual_probability_enabled,
        ),
        station_residual_profile_path=os.getenv(
            "STATION_RESIDUAL_PROFILE_PATH",
            Settings.station_residual_profile_path,
        ),
        station_residual_min_sample_days=_int_env(
            "STATION_RESIDUAL_MIN_SAMPLE_DAYS",
            Settings.station_residual_min_sample_days,
        ),
        station_residual_wilson_z=_float_env(
            "STATION_RESIDUAL_WILSON_Z",
            Settings.station_residual_wilson_z,
        ),
        dashboard_host=os.getenv("DASHBOARD_HOST", Settings.dashboard_host),
        dashboard_port=_int_env("DASHBOARD_PORT", Settings.dashboard_port),
        dashboard_token=os.getenv("DASHBOARD_TOKEN", Settings.dashboard_token).strip(),
        min_net_edge=_float_env("MIN_NET_EDGE", Settings.min_net_edge),
        exit_net_edge=_float_env("EXIT_NET_EDGE", Settings.exit_net_edge),
        probability_stop_drop_threshold=_float_env(
            "PROBABILITY_STOP_DROP_THRESHOLD",
            Settings.probability_stop_drop_threshold,
        ),
        min_profit_pct=_float_env("MIN_PROFIT_PCT", Settings.min_profit_pct),
        take_profit_to_fair_ratio=_float_env("TAKE_PROFIT_TO_FAIR_RATIO", Settings.take_profit_to_fair_ratio),
        overheat_margin=_float_env("OVERHEAT_MARGIN", Settings.overheat_margin),
        edge_fade_max_loss_pct=_float_env("EDGE_FADE_MAX_LOSS_PCT", Settings.edge_fade_max_loss_pct),
        add_to_position_drop_pct=_float_env("ADD_TO_POSITION_DROP_PCT", Settings.add_to_position_drop_pct),
        max_holding_hours=_float_env("MAX_HOLDING_HOURS", Settings.max_holding_hours),
        size_mode=os.getenv("SIZE_MODE", Settings.size_mode),
        entry_fraction=_float_env("ENTRY_FRACTION", Settings.entry_fraction),
        fractional_kelly=_float_env("FRACTIONAL_KELLY", Settings.fractional_kelly),
        max_single_market_fraction=_float_env("MAX_SINGLE_MARKET_FRACTION", Settings.max_single_market_fraction),
        max_total_exposure_fraction=_float_env("MAX_TOTAL_EXPOSURE_FRACTION", Settings.max_total_exposure_fraction),
        bankroll_usd=_float_env("BANKROLL_USD", Settings.bankroll_usd),
        min_order_usd=_float_env("MIN_ORDER_USD", Settings.min_order_usd),
        entry_min_expected_net_return_pct=_float_env(
            "ENTRY_MIN_EXPECTED_NET_RETURN_PCT",
            Settings.entry_min_expected_net_return_pct,
        ),
        max_entry_spread_abs=_float_env("MAX_ENTRY_SPREAD_ABS", Settings.max_entry_spread_abs),
        max_entry_spread_pct=_float_env("MAX_ENTRY_SPREAD_PCT", Settings.max_entry_spread_pct),
        weather_taker_fee_rate=_float_env("WEATHER_TAKER_FEE_RATE", Settings.weather_taker_fee_rate),
        model_error_margin=_float_env("MODEL_ERROR_MARGIN", Settings.model_error_margin),
        resolution_error_margin=_float_env("RESOLUTION_ERROR_MARGIN", Settings.resolution_error_margin),
        settlement_runner_enabled=_bool_env("SETTLEMENT_RUNNER_ENABLED", Settings.settlement_runner_enabled),
        settlement_runner_max_fraction=_float_env(
            "SETTLEMENT_RUNNER_MAX_FRACTION",
            Settings.settlement_runner_max_fraction,
        ),
        settlement_runner_min_ev_margin_usd=_float_env(
            "SETTLEMENT_RUNNER_MIN_EV_MARGIN_USD",
            Settings.settlement_runner_min_ev_margin_usd,
        ),
        # The long-running production entrypoint must fail safe when an env file
        # omits this line. Explicit offline experiments can still construct
        # Settings(strategy_mode="hybrid_observation_edge").
        strategy_mode=os.getenv("STRATEGY_MODE", "lock_only"),
        no_only_new_entries=_bool_env(
            "NO_ONLY_NEW_ENTRIES",
            Settings.no_only_new_entries,
        ),
        intraday_observation_edge_enabled=_bool_env(
            "INTRADAY_OBSERVATION_EDGE_ENABLED",
            Settings.intraday_observation_edge_enabled,
        ),
        intraday_min_side_probability=_float_env(
            "INTRADAY_MIN_SIDE_PROBABILITY",
            Settings.intraday_min_side_probability,
        ),
        intraday_strong_side_probability=_float_env(
            "INTRADAY_STRONG_SIDE_PROBABILITY",
            Settings.intraday_strong_side_probability,
        ),
        intraday_base_entry_fraction=_float_env(
            "INTRADAY_BASE_ENTRY_FRACTION",
            Settings.intraday_base_entry_fraction,
        ),
        intraday_strong_entry_fraction=_float_env(
            "INTRADAY_STRONG_ENTRY_FRACTION",
            Settings.intraday_strong_entry_fraction,
        ),
        intraday_abnormal_price_entry_fraction=_float_env(
            "INTRADAY_ABNORMAL_PRICE_ENTRY_FRACTION",
            Settings.intraday_abnormal_price_entry_fraction,
        ),
        intraday_abnormal_min_net_edge=_float_env(
            "INTRADAY_ABNORMAL_MIN_NET_EDGE",
            Settings.intraday_abnormal_min_net_edge,
        ),
        intraday_hko_needs_audit_fraction_multiplier=_float_env(
            "INTRADAY_HKO_NEEDS_AUDIT_FRACTION_MULTIPLIER",
            Settings.intraday_hko_needs_audit_fraction_multiplier,
        ),
        intraday_high_confirm_local_hour=_int_env(
            "INTRADAY_HIGH_CONFIRM_LOCAL_HOUR",
            Settings.intraday_high_confirm_local_hour,
        ),
        intraday_low_confirm_local_hour=_int_env(
            "INTRADAY_LOW_CONFIRM_LOCAL_HOUR",
            Settings.intraday_low_confirm_local_hour,
        ),
        intraday_us_high_disabled_before_local_hour=_int_env(
            "INTRADAY_US_HIGH_DISABLED_BEFORE_LOCAL_HOUR",
            Settings.intraday_us_high_disabled_before_local_hour,
        ),
        intraday_exact_low_yes_enabled=_bool_env(
            "INTRADAY_EXACT_LOW_YES_ENABLED",
            Settings.intraday_exact_low_yes_enabled,
        ),
        intraday_us_exact_low_yes_enabled=_bool_env(
            "INTRADAY_US_EXACT_LOW_YES_ENABLED",
            Settings.intraday_us_exact_low_yes_enabled,
        ),
        intraday_enable_above_bucket_no=_bool_env(
            "INTRADAY_ENABLE_ABOVE_BUCKET_NO",
            Settings.intraday_enable_above_bucket_no,
        ),
        observation_tier_80_probability=_float_env(
            "OBSERVATION_TIER_80_PROBABILITY",
            Settings.observation_tier_80_probability,
        ),
        observation_tier_90_probability=_float_env(
            "OBSERVATION_TIER_90_PROBABILITY",
            Settings.observation_tier_90_probability,
        ),
        observation_tier_95_probability=_float_env(
            "OBSERVATION_TIER_95_PROBABILITY",
            Settings.observation_tier_95_probability,
        ),
        observation_tier_80_fraction=_float_env(
            "OBSERVATION_TIER_80_FRACTION",
            Settings.observation_tier_80_fraction,
        ),
        observation_tier_90_fraction=_float_env(
            "OBSERVATION_TIER_90_FRACTION",
            Settings.observation_tier_90_fraction,
        ),
        observation_tier_95_fraction=_float_env(
            "OBSERVATION_TIER_95_FRACTION",
            Settings.observation_tier_95_fraction,
        ),
        official_nowcast_lock_enabled=_bool_env(
            "OFFICIAL_NOWCAST_LOCK_ENABLED",
            Settings.official_nowcast_lock_enabled,
        ),
        official_nowcast_entry_only=_bool_env(
            "OFFICIAL_NOWCAST_ENTRY_ONLY",
            Settings.official_nowcast_entry_only,
        ),
        official_nowcast_lock_base_entry_fraction=_float_env(
            "OFFICIAL_NOWCAST_LOCK_BASE_ENTRY_FRACTION",
            Settings.official_nowcast_lock_base_entry_fraction,
        ),
        official_nowcast_lock_strong_entry_fraction=_float_env(
            "OFFICIAL_NOWCAST_LOCK_STRONG_ENTRY_FRACTION",
            Settings.official_nowcast_lock_strong_entry_fraction,
        ),
        official_nowcast_lock_near_close_hours=_float_env(
            "OFFICIAL_NOWCAST_LOCK_NEAR_CLOSE_HOURS",
            Settings.official_nowcast_lock_near_close_hours,
        ),
        official_nowcast_lock_yes_base_buffer_c=_float_env(
            "OFFICIAL_NOWCAST_LOCK_YES_BASE_BUFFER_C",
            Settings.official_nowcast_lock_yes_base_buffer_c,
        ),
        official_nowcast_lock_yes_strong_buffer_c=_float_env(
            "OFFICIAL_NOWCAST_LOCK_YES_STRONG_BUFFER_C",
            Settings.official_nowcast_lock_yes_strong_buffer_c,
        ),
        probability_shrink_gamma=_float_env("PROBABILITY_SHRINK_GAMMA", Settings.probability_shrink_gamma),
        confidence_size_floor=_float_env("CONFIDENCE_SIZE_FLOOR", Settings.confidence_size_floor),
        require_parse_for_trade=_bool_env("REQUIRE_PARSE_FOR_TRADE", Settings.require_parse_for_trade),
        require_date_hint_for_trade=_bool_env("REQUIRE_DATE_HINT_FOR_TRADE", Settings.require_date_hint_for_trade),
        max_city_exposure_fraction=_float_env("MAX_CITY_EXPOSURE_FRACTION", Settings.max_city_exposure_fraction),
        max_event_date_exposure_fraction=_float_env("MAX_EVENT_DATE_EXPOSURE_FRACTION", Settings.max_event_date_exposure_fraction),
        large_bankroll_event_date_exposure_fraction=_float_env(
            "LARGE_BANKROLL_EVENT_DATE_EXPOSURE_FRACTION",
            Settings.large_bankroll_event_date_exposure_fraction,
        ),
        daily_realized_loss_limit_fraction=_float_env(
            "DAILY_REALIZED_LOSS_LIMIT_FRACTION",
            Settings.daily_realized_loss_limit_fraction,
        ),
        daily_unrealized_loss_limit_fraction=_float_env(
            "DAILY_UNREALIZED_LOSS_LIMIT_FRACTION",
            Settings.daily_unrealized_loss_limit_fraction,
        ),
        max_consecutive_losses=_int_env("MAX_CONSECUTIVE_LOSSES", Settings.max_consecutive_losses),
        city_loss_cooldown_hours=_float_env("CITY_LOSS_COOLDOWN_HOURS", Settings.city_loss_cooldown_hours),
        large_loss_threshold_fraction=_float_env("LARGE_LOSS_THRESHOLD_FRACTION", Settings.large_loss_threshold_fraction),
        large_loss_cooldown_hours=_float_env("LARGE_LOSS_COOLDOWN_HOURS", Settings.large_loss_cooldown_hours),
        event_date_exposure_transition_usd=_float_env(
            "EVENT_DATE_EXPOSURE_TRANSITION_USD",
            Settings.event_date_exposure_transition_usd,
        ),
        max_event_portfolio_legs=_int_env("MAX_EVENT_PORTFOLIO_LEGS", Settings.max_event_portfolio_legs),
    )
