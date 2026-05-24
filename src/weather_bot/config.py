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
    scan_interval_seconds: int = 300
    max_markets: int = 40
    state_path: str = "paper_state.json"
    trades_csv_path: str = "paper_trades.csv"
    decisions_csv_path: str = "paper_decisions.csv"
    raw_snapshots_path: str = "paper_raw_snapshots.jsonl"

    # Strategy thresholds
    min_net_edge: float = 0.05
    exit_net_edge: float = 0.00
    # Exit policy: stop is fixed from entry; profit is model-fair-value based.
    stop_loss_pct: float = 0.10
    min_profit_pct: float = 0.03
    take_profit_to_fair_ratio: float = 0.70
    overheat_margin: float = 0.02
    edge_fade_max_loss_pct: float = 0.02
    max_holding_hours: float = 96.0

    # Risk / sizing. Default is fixed 5% per valid signal, matching your example.
    # Set SIZE_MODE=kelly if you want pure fractional-Kelly sizing.
    size_mode: str = "fixed_fraction"
    entry_fraction: float = 0.05
    fractional_kelly: float = 0.10
    max_single_market_fraction: float = 0.05
    max_total_exposure_fraction: float = 0.30
    bankroll_usd: float = 1000.0
    min_order_usd: float = 1.0

    # 도시별 중복 노출 한도 (같은 날씨 이벤트 파생 마켓 몰빵 방지)
    # NYC 70°F + NYC 72°F + NYC 75°F 모두 같은 기온 이벤트 → 각 5%씩 넣으면 15% 노출
    # max_city_exposure_fraction: 한 도시의 모든 포지션 cost 합 / bankroll 상한
    max_city_exposure_fraction: float = 0.08
    # max_event_date_exposure_fraction: 같은 도시+날짜 조합 포지션의 bankroll 비율 상한
    max_event_date_exposure_fraction: float = 0.05

    # Cost / safety buffers. Polymarket fee should be read by market when you add live execution.
    estimated_fee_per_share: float = 0.02  # Polymarket 실수수료: 매수/매도 각 1~2%. 0.02(2%)로 보수적 설정.
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
    # 편더멘탈 손절: 진입 당시 p_true 대비 현재 p_true가 이 값 이상 하락하면 청산
    # 가격 손절을 기다리지 않고 모델 확률이 크게 달라졌을 때 먼저 나온다
    p_true_drop_threshold: float = 0.15


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
        scan_interval_seconds=_int_env("SCAN_INTERVAL_SECONDS", Settings.scan_interval_seconds),
        max_markets=_int_env("MAX_MARKETS", Settings.max_markets),
        state_path=os.getenv("STATE_PATH", Settings.state_path),
        trades_csv_path=os.getenv("TRADES_CSV_PATH", Settings.trades_csv_path),
        decisions_csv_path=os.getenv("DECISIONS_CSV_PATH", Settings.decisions_csv_path),
        raw_snapshots_path=os.getenv("RAW_SNAPSHOTS_PATH", Settings.raw_snapshots_path),
        min_net_edge=_float_env("MIN_NET_EDGE", Settings.min_net_edge),
        exit_net_edge=_float_env("EXIT_NET_EDGE", Settings.exit_net_edge),
        stop_loss_pct=_float_env("STOP_LOSS_PCT", Settings.stop_loss_pct),
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
        estimated_fee_per_share=_float_env("ESTIMATED_FEE_PER_SHARE", Settings.estimated_fee_per_share),
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
        p_true_drop_threshold=_float_env("P_TRUE_DROP_THRESHOLD", Settings.p_true_drop_threshold),
    )
