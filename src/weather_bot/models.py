from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Side = Literal["YES", "NO", "SKIP"]


@dataclass(frozen=True)
class RawMarket:
    market_id: str
    question: str
    slug: str | None
    active: bool
    closed: bool
    yes_token_id: str | None = None
    no_token_id: str | None = None
    condition_id: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class OrderLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    token_id: str
    bids: list[OrderLevel]
    asks: list[OrderLevel]
    market: str | None = None
    timestamp: str | None = None
    min_order_size: float | None = None
    tick_size: float | None = None
    neg_risk: bool | None = None
    book_hash: str | None = None
    last_trade_price: float | None = None
    raw: dict[str, Any] | None = None

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None


@dataclass(frozen=True)
class ParsedWeatherQuestion:
    city: str | None
    latitude: float | None
    longitude: float | None
    threshold_f: float | None
    threshold_original: float | None
    threshold_unit: Literal["F", "C", "UNKNOWN"]
    operator: str | None
    variable: str
    date_hint: str | None = None
    confidence: float = 0.0
    note: str = ""
    threshold_precip_mm: float | None = None  # 강수 질문에서 파싱된 mm 임계값 (None=어떤 비든, 0.1mm 기본값)
    temperature_metric: Literal["max", "min"] = "max"


@dataclass(frozen=True)
class WeatherSignal:
    p_true: float
    confidence: float
    source: str
    note: str
    parsed: ParsedWeatherQuestion | None = None


@dataclass(frozen=True)
class EdgeResult:
    side: Side
    p_true: float
    p_exec: float | None
    net_edge: float
    size_usd: float
    size_shares: float
    reason: str


@dataclass(frozen=True)
class MarketDecision:
    market: RawMarket
    signal: WeatherSignal
    result: EdgeResult


@dataclass
class PaperPosition:
    position_id: str
    market_id: str
    question: str
    token_id: str
    side: Literal["YES", "NO"]
    entry_price: float
    shares: float
    cost_usd: float
    opened_at: str
    last_mark_price: float | None = None
    last_unrealized_pnl: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaperState:
    cash_usd: float
    realized_pnl_usd: float = 0.0
    positions: list[PaperPosition] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    # stats 구조: {"temperature": {"wins": 0, "losses": 0, "pnl": 0.0}, "precipitation": {...}}
