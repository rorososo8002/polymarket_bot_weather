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
    "forecast_refresh_interval_seconds",
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
)

_RATIO_SETTINGS = (
    "min_net_edge",
    "exit_net_edge",
    "probability_stop_drop_threshold",
    "min_profit_pct",
    "take_profit_to_fair_ratio",
    "overheat_margin",
    "edge_fade_max_loss_pct",
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
    "precip_min_net_edge",
    "precip_entry_fraction",
    "precip_min_confidence",
    "precip_max_confidence",
    "probability_shrink_gamma",
)

_NON_NEGATIVE_NUMBER_SETTINGS = (
    "settlement_runner_min_ev_margin_usd",
    "shadow_min_trade_usdc",
)

_RATE_SETTINGS = (
    "weather_taker_fee_rate",
)


@dataclass(frozen=True)
class Settings:
    # Public data endpoints
    gamma_base: str = "https://gamma-api.polymarket.com"
    polymarket_data_base: str = "https://data-api.polymarket.com"
    clob_base: str = "https://clob.polymarket.com"

    # Paper-trading loop
    orderbook_stream_enabled: bool = True
    orderbook_stream_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    orderbook_stream_heartbeat_seconds: int = 10
    orderbook_stream_reconnect_seconds: int = 2
    orderbook_stream_stale_seconds: int = 60
    runner_health_status_interval_seconds: int = 5
    forecast_refresh_interval_seconds: int = 1800
    discovery_max_pages: int = 8
    discovery_page_size: int = 100
    state_path: str = "paper_state.json"
    trades_csv_path: str = "paper_trades.csv"
    decisions_csv_path: str = "paper_decisions.csv"
    portfolio_decisions_jsonl_path: str = "paper_event_portfolios.jsonl"
    raw_snapshots_path: str = "paper_raw_snapshots.jsonl"
    shadow_signals_jsonl_path: str = "shadow_external_signals.jsonl"
    shadow_public_notes_jsonl_path: str = "shadow_public_notes.jsonl"
    shadow_report_path: str = "shadow_signal_report.md"
    shadow_max_markets: int = 100
    shadow_max_trades_per_market: int = 100
    shadow_max_rows: int = 1000
    shadow_min_trade_usdc: float = 100.0
    shadow_compare_window_seconds: int = 86400
    forecast_cache_path: str = ""
    forecast_cache_ttl_seconds: int = 1800
    station_nowcast_enabled: bool = True
    station_nowcast_cache_ttl_seconds: int = 900
    station_nowcast_freshness_seconds: int = 5400
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8787
    dashboard_token: str = ""
    enable_precipitation_markets: bool = False

    # Strategy thresholds
    min_net_edge: float = 0.05
    exit_net_edge: float = 0.00
    # Exit policy: stop is probability-based; profit is model-fair-value based.
    probability_stop_drop_threshold: float = 0.10
    min_profit_pct: float = 0.03
    take_profit_to_fair_ratio: float = 0.70
    overheat_margin: float = 0.02
    edge_fade_max_loss_pct: float = 0.02
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

    # Precipitation-specific parameters. Rain and precipitation are harder to
    # forecast than temperature, so the paper strategy requires stricter gates.
    precip_min_net_edge: float = 0.08        # Higher than the 0.05 temperature edge floor.
    precip_entry_fraction: float = 0.025     # Half-size entries versus the temperature default.
    precip_min_confidence: float = 0.65      # Higher parser-confidence requirement than temperature.
    precip_max_confidence: float = 0.70      # Precipitation-specific ensemble confidence ceiling.

    # Probability model controls
    probability_shrink_gamma: float = 0.65
    default_temperature_sigma_f: float = 4.5
    require_parse_for_trade: bool = True
    # Skip markets whose date cannot be parsed. If date_hint=None fell back to
    # today, the bot could enter an expired or wrong-date market by accident.
    # The safe default is True, which enables this skip.
    require_date_hint_for_trade: bool = True
    # Probability stop compares the current side probability with the entry-side
    # probability. YES uses p_true; NO uses 1 - p_true.

    def __post_init__(self) -> None:
        _validate_positive_numbers(self, _POSITIVE_NUMBER_SETTINGS)
        _validate_positive_integers(self, _POSITIVE_INTEGER_SETTINGS)
        _validate_ratios(self, _RATIO_SETTINGS)
        _validate_non_negative_numbers(self, _NON_NEGATIVE_NUMBER_SETTINGS)
        _validate_rates(self, _RATE_SETTINGS)


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
        polymarket_data_base=os.getenv("POLYMARKET_DATA_BASE", Settings.polymarket_data_base),
        clob_base=os.getenv("POLYMARKET_CLOB_BASE", Settings.clob_base),
        orderbook_stream_enabled=_bool_env("ORDERBOOK_STREAM_ENABLED", Settings.orderbook_stream_enabled),
        orderbook_stream_url=os.getenv("ORDERBOOK_STREAM_URL", Settings.orderbook_stream_url),
        orderbook_stream_heartbeat_seconds=_int_env("ORDERBOOK_STREAM_HEARTBEAT_SECONDS", Settings.orderbook_stream_heartbeat_seconds),
        orderbook_stream_reconnect_seconds=_int_env("ORDERBOOK_STREAM_RECONNECT_SECONDS", Settings.orderbook_stream_reconnect_seconds),
        orderbook_stream_stale_seconds=_int_env("ORDERBOOK_STREAM_STALE_SECONDS", Settings.orderbook_stream_stale_seconds),
        runner_health_status_interval_seconds=_int_env("RUNNER_HEALTH_STATUS_INTERVAL_SECONDS", Settings.runner_health_status_interval_seconds),
        forecast_refresh_interval_seconds=_int_env("FORECAST_REFRESH_INTERVAL_SECONDS", Settings.forecast_refresh_interval_seconds),
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
        shadow_signals_jsonl_path=os.getenv("SHADOW_SIGNALS_JSONL_PATH", Settings.shadow_signals_jsonl_path),
        shadow_public_notes_jsonl_path=os.getenv(
            "SHADOW_PUBLIC_NOTES_JSONL_PATH",
            Settings.shadow_public_notes_jsonl_path,
        ),
        shadow_report_path=os.getenv("SHADOW_REPORT_PATH", Settings.shadow_report_path),
        shadow_max_markets=_int_env("SHADOW_MAX_MARKETS", Settings.shadow_max_markets),
        shadow_max_trades_per_market=_int_env(
            "SHADOW_MAX_TRADES_PER_MARKET",
            Settings.shadow_max_trades_per_market,
        ),
        shadow_max_rows=_int_env("SHADOW_MAX_ROWS", Settings.shadow_max_rows),
        shadow_min_trade_usdc=_float_env("SHADOW_MIN_TRADE_USDC", Settings.shadow_min_trade_usdc),
        shadow_compare_window_seconds=_int_env(
            "SHADOW_COMPARE_WINDOW_SECONDS",
            Settings.shadow_compare_window_seconds,
        ),
        forecast_cache_path=os.getenv("FORECAST_CACHE_PATH", Settings.forecast_cache_path),
        forecast_cache_ttl_seconds=_int_env("FORECAST_CACHE_TTL_SECONDS", Settings.forecast_cache_ttl_seconds),
        station_nowcast_enabled=_bool_env("STATION_NOWCAST_ENABLED", Settings.station_nowcast_enabled),
        station_nowcast_cache_ttl_seconds=_int_env(
            "STATION_NOWCAST_CACHE_TTL_SECONDS",
            Settings.station_nowcast_cache_ttl_seconds,
        ),
        station_nowcast_freshness_seconds=_int_env(
            "STATION_NOWCAST_FRESHNESS_SECONDS",
            Settings.station_nowcast_freshness_seconds,
        ),
        dashboard_host=os.getenv("DASHBOARD_HOST", Settings.dashboard_host),
        dashboard_port=_int_env("DASHBOARD_PORT", Settings.dashboard_port),
        dashboard_token=os.getenv("DASHBOARD_TOKEN", Settings.dashboard_token).strip(),
        enable_precipitation_markets=_bool_env("ENABLE_PRECIPITATION_MARKETS", Settings.enable_precipitation_markets),
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
        precip_min_net_edge=_float_env("PRECIP_MIN_NET_EDGE", Settings.precip_min_net_edge),
        precip_entry_fraction=_float_env("PRECIP_ENTRY_FRACTION", Settings.precip_entry_fraction),
        precip_min_confidence=_float_env("PRECIP_MIN_CONFIDENCE", Settings.precip_min_confidence),
        precip_max_confidence=_float_env("PRECIP_MAX_CONFIDENCE", Settings.precip_max_confidence),
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
