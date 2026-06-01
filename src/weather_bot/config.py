from __future__ import annotations

from dataclasses import dataclass
import os
from dotenv import load_dotenv

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
    forecast_refresh_interval_seconds: int = 1800
    discovery_max_pages: int = 8
    discovery_page_size: int = 100
    state_path: str = "paper_state.json"
    trades_csv_path: str = "paper_trades.csv"
    decisions_csv_path: str = "paper_decisions.csv"
    portfolio_decisions_jsonl_path: str = "paper_event_portfolios.jsonl"
    raw_snapshots_path: str = "paper_raw_snapshots.jsonl"
    forecast_cache_path: str = ""
    forecast_cache_ttl_seconds: int = 1800
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

    # 도시별 중복 노출 한도 (같은 날씨 이벤트 파생 마켓 몰빵 방지)
    # NYC 70°F + NYC 72°F + NYC 75°F 모두 같은 기온 이벤트 → 각 5%씩 넣으면 15% 노출
    # max_city_exposure_fraction: 한 도시의 모든 포지션 cost 합 / bankroll 상한
    max_city_exposure_fraction: float = 0.20
    # 작은 paper 계좌는 도시+날짜 예산을 10%로 시작하되, 기준금 $1,000부터 5%로 축소한다.
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

    # 강수 마켓 전용 파라미터 (비/강수는 기온보다 예측이 훨씬 어렵다 → 더 엄격한 기준)
    precip_min_net_edge: float = 0.08        # 기온 0.05 대비 높은 최소 엣지 요구
    precip_entry_fraction: float = 0.025     # 기온 0.05 대비 절반 사이즈로 베팅
    precip_min_confidence: float = 0.65      # 기온 0.50 대비 높은 파싱 신뢰도 요구
    precip_max_confidence: float = 0.70      # 앙상블 신뢰도 상한선 (강수 특화)

    # Probability model controls
    probability_shrink_gamma: float = 0.65
    default_temperature_sigma_f: float = 4.5
    require_parse_for_trade: bool = True
    # 날짜 파싱 불명확 마켓 SKIP: date_hint=None이면 오늘 날짜로 fallback되어
    # 만료 마켓에 잘못 진입할 수 있으므로 기본값 True (SKIP 활성화)
    require_date_hint_for_trade: bool = True
    # Probability stop compares the current side probability with the entry-side
    # probability. YES uses p_true; NO uses 1 - p_true.


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


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
        forecast_cache_path=os.getenv("FORECAST_CACHE_PATH", Settings.forecast_cache_path),
        forecast_cache_ttl_seconds=_int_env("FORECAST_CACHE_TTL_SECONDS", Settings.forecast_cache_ttl_seconds),
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
