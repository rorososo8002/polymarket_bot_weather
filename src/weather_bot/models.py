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
    event_id: str | None = None
    event_slug: str | None = None
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
    indicative_best_bid: float | None = None
    indicative_best_ask: float | None = None

    @property
    def best_bid(self) -> float | None:
        for level in self.bids:
            if level.size > 0:
                return level.price
        return None

    @property
    def best_ask(self) -> float | None:
        for level in self.asks:
            if level.size > 0:
                return level.price
        return None

    @property
    def reference_best_bid(self) -> float | None:
        return self.indicative_best_bid if self.indicative_best_bid is not None else self.best_bid

    @property
    def reference_best_ask(self) -> float | None:
        return self.indicative_best_ask if self.indicative_best_ask is not None else self.best_ask


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
    temperature_metric: Literal["max", "min"] = "max"
    temperature_bucket: Literal["threshold", "exact", "range", "lower_tail", "upper_tail"] = "threshold"
    temperature_range_lower_f: float | None = None
    temperature_range_upper_f: float | None = None
    temperature_range_lower_original: float | None = None
    temperature_range_upper_original: float | None = None
    temperature_range_inclusive: bool = False


@dataclass(frozen=True)
class WeatherSignal:
    p_true: float
    confidence: float
    source: str
    note: str
    parsed: ParsedWeatherQuestion | None = None
    nowcast: dict[str, Any] | None = None


@dataclass(frozen=True)
class EdgeResult:
    side: Side
    p_true: float
    p_exec: float | None
    net_edge: float
    size_usd: float
    size_shares: float
    reason: str
    expected_net_profit_usd: float = 0.0


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
    # stats shape: {"temperature": {"wins": 0, "losses": 0, "pnl": 0.0}}
