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
    "runner_health_status_interval_seconds",
    "stream_cycle_interval_seconds",
    "forecast_cache_ttl_seconds",
    "station_nowcast_cache_ttl_seconds",
    "station_nowcast_freshness_seconds",
    "bankroll_usd",
    "min_order_usd",
    "event_date_exposure_transition_usd",
    "max_holding_hours",
    "default_temperature_sigma_f",
)

_POSITIVE_INTEGER_SETTINGS = (
    "discovery_max_pages",
    "discovery_page_size",
    "max_event_portfolio_legs",
    "raw_snapshots_max_bytes",
    "raw_snapshots_retention_days",
    "raw_snapshots_min_free_bytes",
)

_MINIMUM_INTEGER_SETTINGS = (
    ("forecast_request_min_interval_seconds", 10),  # hard floor; batch mode uses 15 s within-batch
)

_TCP_PORT_SETTINGS = (
    "dashboard_port",
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
    "model_error_margin",
    "resolution_error_margin",
    "settlement_runner_max_fraction",
    "probability_shrink_gamma",
    "raw_snapshots_max_disk_usage_pct",
)

_NON_NEGATIVE_NUMBER_SETTINGS = (
    "settlement_runner_min_ev_margin_usd",
)

_RATE_SETTINGS = (
    "weather_taker_fee_rate",
)

_RAW_SNAPSHOT_MODES = ("off", "error", "debug")
_SIZE_MODES = ("fixed_fraction", "kelly")


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
    runner_health_status_interval_seconds: int = 5
    stream_cycle_interval_seconds: int = 2400
    discovery_max_pages: int = 8
    discovery_page_size: int = 100
    state_path: str = "paper_state.json"
    trades_csv_path: str = "paper_trades.csv"
    decisions_csv_path: str = "paper_decisions.csv"
    portfolio_decisions_jsonl_path: str = "paper_event_portfolios.jsonl"
    raw_snapshots_path: str = "paper_raw_snapshots.jsonl"
    raw_snapshots_mode: str = "error"
    raw_snapshots_max_bytes: int = 100 * 1024 * 1024
    raw_snapshots_retention_days: int = 7
    raw_snapshots_min_free_bytes: int = 1024 * 1024 * 1024
    raw_snapshots_max_disk_usage_pct: float = 0.90
    forecast_cache_path: str = ""
    forecast_cache_ttl_seconds: int = 10800  # 3 h: GFS updates every 6 h (processed in 3-4 h); 39 cities x 8 batches/day x 31 units = 9 672 < 10 000
    forecast_request_min_interval_seconds: int = 15  # within-batch gap; cache TTL controls between-batch wait
    forecast_request_log_path: str = ""
    forecast_rate_limit_state_path: str = ""
    station_nowcast_enabled: bool = True
    station_nowcast_cache_ttl_seconds: int = 300  # 5 min: matches AWC METAR floor for timely exit signals
    station_nowcast_freshness_seconds: int = 5400
    station_nowcast_request_log_path: str = ""
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8787
    dashboard_token: str = ""

    # Strategy thresholds
    min_net_edge: float = 0.05
    exit_net_edge: float = 0.00
    # Exit policy: stop is probability-based; profit is model-fair-value based.
    probability_stop_drop_threshold: float = 0.10
    min_profit_pct: float = 0.03
    take_profit_to_fair_ratio: float = 0.70
    overheat_margin: float = 0.02
    edge_fade_max_loss_pct: float = 0.02
    add_to_position_drop_pct: float = 0.10
    max_holding_hours: float = 96.0

    # Risk / sizing. A small paper account can put its full 10% city-date budget
    # into one strong leg. The event optimizer may split larger budgets across
    # at most two legs, but every opened leg must still meet MIN_ORDER_USD.
    # Set SIZE_MODE=kelly if you want pure fractional-Kelly sizing.
    size_mode: str = "fixed_fraction"
    entry_fraction: float = 0.10
    fractional_kelly: float = 0.10
    max_single_market_fraction: float = 0.10
    max_total_exposure_fraction: float = 0.90
    bankroll_usd: float = 100.0
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

    # Paper weather-fee default from the official category schedule.
    # A separate live-execution project must query fee parameters per market.
    entry_min_expected_net_return_pct: float = 0.06
    weather_taker_fee_rate: float = 0.05
    model_error_margin: float = 0.03
    resolution_error_margin: float = 0.01
    settlement_runner_enabled: bool = True
    settlement_runner_max_fraction: float = 0.25
    settlement_runner_min_ev_margin_usd: float = 0.0

    # Probability model controls
    probability_shrink_gamma: float = 0.65
    default_temperature_sigma_f: float = 4.5
    require_parse_for_trade: bool = True
    # Compatibility switch for older tests/env files. The paper runner still
    # requires an explicit date before forecast or trade, even when this is False.
    require_date_hint_for_trade: bool = True
    # Probability stop compares the current side probability with the entry-side
    # probability. YES uses p_true; NO uses 1 - p_true.

    def __post_init__(self) -> None:
        _validate_positive_numbers(self, _POSITIVE_NUMBER_SETTINGS)
        _validate_positive_integers(self, _POSITIVE_INTEGER_SETTINGS)
        _validate_minimum_integers(self, _MINIMUM_INTEGER_SETTINGS)
        _validate_tcp_ports(self, _TCP_PORT_SETTINGS)
        _validate_ratios(self, _RATIO_SETTINGS)
        _validate_non_negative_numbers(self, _NON_NEGATIVE_NUMBER_SETTINGS)
        _validate_rates(self, _RATE_SETTINGS)
        _validate_choice(self, "size_mode", _SIZE_MODES)
        _validate_choice(self, "raw_snapshots_mode", _RAW_SNAPSHOT_MODES)


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
        runner_health_status_interval_seconds=_int_env("RUNNER_HEALTH_STATUS_INTERVAL_SECONDS", Settings.runner_health_status_interval_seconds),
        stream_cycle_interval_seconds=_int_env("STREAM_CYCLE_INTERVAL_SECONDS", Settings.stream_cycle_interval_seconds),
        discovery_max_pages=_int_env("DISCOVERY_MAX_PAGES", Settings.discovery_max_pages),
        discovery_page_size=_int_env("DISCOVERY_PAGE_SIZE", Settings.discovery_page_size),
        state_path=os.getenv("STATE_PATH", Settings.state_path),
        trades_csv_path=os.getenv("TRADES_CSV_PATH", Settings.trades_csv_path),
        decisions_csv_path=os.getenv("DECISIONS_CSV_PATH", Settings.decisions_csv_path),
        portfolio_decisions_jsonl_path=os.getenv(
            "PORTFOLIO_DECISIONS_JSONL_PATH",
            Settings.portfolio_decisions_jsonl_path,
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
        forecast_cache_path=os.getenv("FORECAST_CACHE_PATH", Settings.forecast_cache_path),
        forecast_cache_ttl_seconds=_int_env("FORECAST_CACHE_TTL_SECONDS", Settings.forecast_cache_ttl_seconds),
        forecast_request_min_interval_seconds=_int_env(
            "FORECAST_REQUEST_MIN_INTERVAL_SECONDS",
            Settings.forecast_request_min_interval_seconds,
        ),
        forecast_request_log_path=os.getenv(
            "FORECAST_REQUEST_LOG_PATH",
            Settings.forecast_request_log_path,
        ),
        forecast_rate_limit_state_path=os.getenv(
            "FORECAST_RATE_LIMIT_STATE_PATH",
            Settings.forecast_rate_limit_state_path,
        ),
        station_nowcast_enabled=_bool_env("STATION_NOWCAST_ENABLED", Settings.station_nowcast_enabled),
        station_nowcast_cache_ttl_seconds=_int_env(
            "STATION_NOWCAST_CACHE_TTL_SECONDS",
            Settings.station_nowcast_cache_ttl_seconds,
        ),
        station_nowcast_freshness_seconds=_int_env(
            "STATION_NOWCAST_FRESHNESS_SECONDS",
            Settings.station_nowcast_freshness_seconds,
        ),
        station_nowcast_request_log_path=os.getenv(
            "STATION_NOWCAST_REQUEST_LOG_PATH",
            Settings.station_nowcast_request_log_path,
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
        probability_shrink_gamma=_float_env("PROBABILITY_SHRINK_GAMMA", Settings.probability_shrink_gamma),
        default_temperature_sigma_f=_float_env("DEFAULT_TEMPERATURE_SIGMA_F", Settings.default_temperature_sigma_f),
        require_parse_for_trade=_bool_env("REQUIRE_PARSE_FOR_TRADE", Settings.require_parse_for_trade),
        require_date_hint_for_trade=_bool_env("REQUIRE_DATE_HINT_FOR_TRADE", Settings.require_date_hint_for_trade),
        max_city_exposure_fraction=_float_env("MAX_CITY_EXPOSURE_FRACTION", Settings.max_city_exposure_fraction),
        max_event_date_exposure_fraction=_float_env("MAX_EVENT_DATE_EXPOSURE_FRACTION", Settings.max_event_date_exposure_fraction),
        large_bankroll_event_date_exposure_fraction=_float_env(
            "LARGE_BANKROLL_EVENT_DATE_EXPOSURE_FRACTION",
            Settings.large_bankroll_event_date_exposure_fraction,
        ),
        event_date_exposure_transition_usd=_float_env(
            "EVENT_DATE_EXPOSURE_TRANSITION_USD",
            Settings.event_date_exposure_transition_usd,
        ),
        max_event_portfolio_legs=_int_env("MAX_EVENT_PORTFOLIO_LEGS", Settings.max_event_portfolio_legs),
    )
