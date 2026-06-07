from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import inspect
from datetime import datetime, timezone
import math
import threading
import time
from typing import Any

from .config import Settings, load_settings
from .edge import (
    estimate_executable_net_return,
    executable_buy_price,
    fee_adjusted_entry_shares,
    no_net_edge,
    polymarket_taker_fee_per_share,
    yes_net_edge,
)
from .exit_policy import conservative_settlement_value, model_fair_price, target_exit_price
from .models import EdgeResult, MarketDecision, OrderBook, PaperPosition, RawMarket, WeatherSignal
from .nowcast import AviationWeatherMetarNowcastProvider
from .paper import PaperBroker, maybe_close_positions, maybe_settle_resolved_positions
from .polymarket_client import PolymarketClient
from .portfolio import (
    EntryBankrollSnapshot,
    EventPortfolioDecision,
    PortfolioCandidate,
    available_entry_bankroll,
    select_event_portfolio,
    websocket_pricing_block_reason,
)
from .probability import OpenMeteoEnsembleClient, estimate_weather_probability
from .realtime_orderbook import OrderBookMarketStream
from .risk import fractional_kelly_binary
from .runner_status import read_runner_status, update_runner_status_fields, utc_now_iso, write_runner_status
from .stations import TRADING_READY_STATION_MAP
from .weather_client import parse_weather_question, temperature_bucket_interval_bounds_f


ENTRY_BANKROLL_FAIL_CLOSED_REASON = "기존 포지션을 안전하게 평가할 수 없어 신규 진입 차단"

REALTIME_EVALUATION_QUEUE_MAX_EVENTS = 256
REALTIME_EVALUATION_COALESCE_SECONDS = 0.25


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nowcast_bucket_lock_exit_signal(side: str, signal: WeatherSignal) -> tuple[str, str] | None:
    parsed = signal.parsed
    if side != "NO" or parsed is None or signal.nowcast is None:
        return None
    if parsed.variable != "temperature" or parsed.temperature_bucket not in {"exact", "range"}:
        return None

    bounds = temperature_bucket_interval_bounds_f(parsed)
    if bounds is None:
        return None

    if parsed.temperature_metric == "min":
        observed_f = _finite_float(signal.nowcast.get("observed_low_f"))
        observed_c = signal.nowcast.get("observed_low_c")
        observed_label = "observed_low_c"
    else:
        observed_f = _finite_float(signal.nowcast.get("observed_high_f"))
        observed_c = signal.nowcast.get("observed_high_c")
        observed_label = "observed_high_c"

    if observed_f is None or not bounds.contains_f(observed_f):
        return None

    return (
        "nowcast_bucket_lock_risk",
        (
            f"nowcast bucket lock risk: NO held while {observed_label}={observed_c} "
            f"is inside {parsed.temperature_bucket} bucket; p_true={signal.p_true:.3f}"
        ),
    )


def _with_exit_signal(side: str, signal: WeatherSignal, result: EdgeResult) -> EdgeResult:
    risk = _nowcast_bucket_lock_exit_signal(side, signal)
    if risk is None:
        return result
    exit_signal, exit_signal_reason = risk
    return replace(
        result,
        reason=f"{result.reason}; exit_signal={exit_signal}; {exit_signal_reason}",
        exit_signal=exit_signal,
        exit_signal_reason=exit_signal_reason,
    )


def _call_probability_estimator(
    probability_estimator: Any,
    question: str,
    *,
    settings: Settings,
    ensemble_client: OpenMeteoEnsembleClient | None = None,
    observation_provider: Any | None = None,
) -> WeatherSignal:
    kwargs: dict[str, Any] = {"settings": settings}
    if ensemble_client is not None:
        kwargs["ensemble_client"] = ensemble_client
    if observation_provider is not None:
        signature = inspect.signature(probability_estimator)
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
        if accepts_kwargs or "observation_provider" in signature.parameters:
            kwargs["observation_provider"] = observation_provider
    return probability_estimator(question, **kwargs)


def _compact_status_text(value: Any, max_chars: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:max_chars]


def _safe_error_text(exc: BaseException) -> str:
    return _compact_status_text(str(exc) or exc.__class__.__name__)


def _status_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _market_error_status(settings: Settings) -> tuple[int, dict[str, Any] | None]:
    status = read_runner_status(settings)
    last_error = status.get("last_market_error")
    return _status_int(status.get("market_error_count")), last_error if isinstance(last_error, dict) else None


def _market_error_status_fields(count: int, last_error: dict[str, Any] | None) -> dict[str, Any]:
    fields: dict[str, Any] = {"market_error_count": count}
    if last_error is not None:
        fields["last_market_error"] = last_error
    return fields


def _fallback_error_signal(market: RawMarket, error_message: str) -> WeatherSignal:
    try:
        parsed = parse_weather_question(market.question)
    except Exception:  # noqa: BLE001
        parsed = None
    return WeatherSignal(
        p_true=0.5,
        confidence=0.0,
        source="market-evaluation-error",
        note=f"SKIP_ERROR: market evaluation failed; {error_message}",
        parsed=parsed,
    )


def _record_market_evaluation_error(
    broker: PaperBroker,
    market: RawMarket,
    exc: BaseException,
    market_type: str,
    *,
    signal: WeatherSignal | None = None,
    context: str,
) -> tuple[WeatherSignal, EdgeResult, dict[str, Any], int]:
    error_message = _safe_error_text(exc)
    signal = signal or _fallback_error_signal(market, error_message)
    result = EdgeResult(
        "SKIP_ERROR",
        signal.p_true,
        None,
        -999.0,
        0.0,
        0.0,
        f"SKIP_ERROR: market evaluation failed during {context}: {exc.__class__.__name__}: {error_message}",
    )
    current_count, _last_error = _market_error_status(broker.settings)
    error_count = current_count + 1
    error_info = {
        "at": utc_now_iso(),
        "count": error_count,
        "context": context,
        "market_id": market.market_id,
        "slug": market.slug or "",
        "question": _compact_status_text(market.question, 240),
        "error_type": exc.__class__.__name__,
        "message": error_message,
    }
    broker.log_decision(market, result, signal.note, market_type)
    broker.log_raw_snapshot(
        "market_evaluation_error",
        market,
        {
            "status": "error",
            "context": context,
            "error_type": exc.__class__.__name__,
            "error": error_message,
            "market_raw": market.raw,
            "signal": {
                "p_true": signal.p_true,
                "confidence": signal.confidence,
                "source": signal.source,
                "note": signal.note,
                "nowcast": signal.nowcast,
            },
        },
    )
    update_runner_status_fields(
        broker.settings,
        market_error_count=error_count,
        last_market_error=error_info,
    )
    return signal, result, error_info, error_count


class StreamBackedPolymarketClient(PolymarketClient):
    def __init__(self, gamma_base: str, clob_base: str, stream: OrderBookMarketStream) -> None:
        super().__init__(gamma_base, clob_base)
        self.stream = stream

    def get_order_book(self, token_id: str) -> OrderBook:
        return self.stream.get_order_book(token_id)


class RealtimeEvaluationCoalescer:
    """Coalesce WebSocket token updates before running strategy evaluation."""

    def __init__(
        self,
        *,
        event_key_by_token: dict[str, str],
        evaluator: Callable[[set[str]], None],
        max_pending_events: int = REALTIME_EVALUATION_QUEUE_MAX_EVENTS,
        coalesce_seconds: float = REALTIME_EVALUATION_COALESCE_SECONDS,
        status_update: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.event_key_by_token = {str(token): str(event_key) for token, event_key in event_key_by_token.items()}
        self.evaluator = evaluator
        self.max_pending_events = max(1, int(max_pending_events))
        self.coalesce_seconds = max(0.0, float(coalesce_seconds))
        self.status_update = status_update
        self._condition = threading.Condition()
        self._pending_tokens_by_event: dict[str, set[str]] = {}
        self._stop_requested = False
        self._drain_on_stop = True
        self._thread: threading.Thread | None = None
        self._enqueued_update_count = 0
        self._coalesced_update_count = 0
        self._dropped_update_count = 0
        self._processed_batch_count = 0
        self._processed_event_count = 0
        self._error_count = 0
        self._inflight_event_count = 0
        self._last_error = ""
        self._last_error_at: str | None = None
        self._last_evaluated_at: str | None = None
        self._last_evaluation_duration_seconds: float | None = None

    def start(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_requested = False
            self._drain_on_stop = True
            self._thread = threading.Thread(
                target=self._run,
                name="polymarket-realtime-evaluator",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> None:
        with self._condition:
            self._stop_requested = True
            self._drain_on_stop = drain
            if not drain:
                self._pending_tokens_by_event.clear()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))

    def enqueue_tokens(self, updated_token_ids: set[str]) -> int:
        tokens_by_event: dict[str, set[str]] = {}
        for token_id in updated_token_ids:
            token = str(token_id)
            event_key = self.event_key_by_token.get(token)
            if event_key:
                tokens_by_event.setdefault(event_key, set()).add(token)
        if not tokens_by_event:
            return 0

        accepted = 0
        with self._condition:
            if self._stop_requested:
                self._dropped_update_count += len(tokens_by_event)
                return 0
            for event_key, tokens in tokens_by_event.items():
                pending = self._pending_tokens_by_event.get(event_key)
                if pending is not None:
                    pending.update(tokens)
                    self._coalesced_update_count += 1
                    accepted += 1
                    continue
                if len(self._pending_tokens_by_event) >= self.max_pending_events:
                    self._dropped_update_count += 1
                    continue
                self._pending_tokens_by_event[event_key] = set(tokens)
                self._enqueued_update_count += 1
                accepted += 1
            if accepted:
                self._condition.notify_all()
        return accepted

    def status_snapshot(self) -> dict[str, object]:
        with self._condition:
            return {
                "thread_alive": bool(self._thread is not None and self._thread.is_alive()),
                "queue_depth": len(self._pending_tokens_by_event),
                "max_pending_events": self.max_pending_events,
                "coalesce_seconds": self.coalesce_seconds,
                "inflight_event_count": self._inflight_event_count,
                "enqueued_update_count": self._enqueued_update_count,
                "coalesced_update_count": self._coalesced_update_count,
                "dropped_update_count": self._dropped_update_count,
                "processed_batch_count": self._processed_batch_count,
                "processed_event_count": self._processed_event_count,
                "error_count": self._error_count,
                "last_error": self._last_error,
                "last_error_at": self._last_error_at,
                "last_evaluated_at": self._last_evaluated_at,
                "last_evaluation_duration_seconds": self._last_evaluation_duration_seconds,
            }

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending_tokens_by_event and not self._stop_requested:
                    self._condition.wait()
                if self._stop_requested and (not self._drain_on_stop or not self._pending_tokens_by_event):
                    return

                deadline = time.monotonic() + self.coalesce_seconds
                while True:
                    if self._stop_requested and not self._drain_on_stop:
                        return
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(timeout=remaining)

                pending = self._pending_tokens_by_event
                self._pending_tokens_by_event = {}
                self._inflight_event_count = len(pending)

            updated_token_ids = {token for tokens in pending.values() for token in tokens}
            started_at = time.monotonic()
            try:
                self.evaluator(updated_token_ids)
            except Exception as exc:  # noqa: BLE001
                self._record_error(exc)
            finally:
                duration = time.monotonic() - started_at
                with self._condition:
                    self._processed_batch_count += 1
                    self._processed_event_count += len(pending)
                    self._inflight_event_count = 0
                    self._last_evaluated_at = utc_now_iso()
                    self._last_evaluation_duration_seconds = round(duration, 3)

    def _record_error(self, exc: BaseException) -> None:
        safe_error = " ".join(str(exc).split())[:240]
        if not safe_error:
            safe_error = exc.__class__.__name__
        with self._condition:
            self._error_count += 1
            self._last_error = f"{exc.__class__.__name__}: {safe_error}"
            self._last_error_at = utc_now_iso()
            snapshot = self.status_snapshot()
        if self.status_update is not None:
            try:
                self.status_update(snapshot)
            except Exception:
                pass


def position_size_usd(
    side_probability: float,
    p_eff: float,
    settings: Settings,
    bankroll_usd: float,
    entry_fraction_override: float | None = None,
    net_edge: float = 0.0,
    min_edge: float | None = None,
) -> float:
    """Return target paper order size in USD."""
    if settings.size_mode.lower() == "kelly":
        frac = fractional_kelly_binary(
            side_probability,
            p_eff,
            settings.fractional_kelly,
            settings.max_single_market_fraction,
            gamma=settings.probability_shrink_gamma,
        )
    else:
        base = entry_fraction_override if entry_fraction_override is not None else settings.entry_fraction
        edge_floor = min_edge if min_edge is not None else settings.min_net_edge
        edge_scale = 0.5 if (net_edge > 0 and net_edge < edge_floor * 2.0) else 1.0
        frac = min(base * edge_scale, settings.max_single_market_fraction)
    return bankroll_usd * max(0.0, frac)


def _market_params(settings: Settings, market_type: str) -> tuple[float, float, float | None]:
    return 0.50, settings.min_net_edge, None


def _bid_notional(book: OrderBook, min_price: float = 0.01) -> float:
    return sum(level.price * level.size for level in book.bids if level.price >= min_price)


def _side_liquidity_reason(side: str, book: OrderBook, market_type: str) -> str | None:
    ask = book.best_ask
    bid = book.best_bid
    if ask is None:
        return f"{side} liquidity filter: no ask [{market_type}]"
    if bid is None:
        return f"{side} liquidity filter: no bid [{market_type}]"
    spread = ask - bid
    if spread > 0.20:
        return f"{side} liquidity filter: spread too wide {spread:.2f} > 0.20 [{market_type}]"
    if ask >= 1.0 or ask <= 0.0:
        return f"{side} liquidity filter: invalid ask={ask:.3f} [{market_type}]"
    if ask < 0.08:
        return f"{side} liquidity filter: extreme low ask={ask:.3f} below 0.08 [{market_type}]"
    bid_value = _bid_notional(book)
    if bid_value < 10.0:
        return f"{side} liquidity filter: exit bid depth ${bid_value:.1f} < $10 [{market_type}]"
    return None


def _fetch_books(market: RawMarket, client: PolymarketClient) -> tuple[dict[str, OrderBook], str | None]:
    books: dict[str, OrderBook] = {}
    try:
        if market.yes_token_id:
            books["YES"] = client.get_order_book(market.yes_token_id)
        if market.no_token_id:
            books["NO"] = client.get_order_book(market.no_token_id)
    except Exception as exc:  # noqa: BLE001
        return books, f"order book error: {exc}"
    return books, None


def _yes_no_sum_reason(books: dict[str, OrderBook], market_type: str) -> str | None:
    yes_book = books.get("YES")
    no_book = books.get("NO")
    if not yes_book or not no_book or yes_book.best_ask is None or no_book.best_ask is None:
        return None
    yes_no_sum = yes_book.best_ask + no_book.best_ask
    if abs(yes_no_sum - 1.0) > 0.05:
        return f"YES+NO ask sum abnormal {yes_no_sum:.3f} outside 1±0.05 [{market_type}]"
    return None


def _side_edge_metrics(
    side: str,
    signal: WeatherSignal,
    p_exec: float,
    settings: Settings,
) -> tuple[float, float, float]:
    entry_fee_per_share = polymarket_taker_fee_per_share(p_exec, settings.weather_taker_fee_rate)
    if side == "YES":
        edge = yes_net_edge(
            signal.p_true,
            p_exec,
            entry_fee_per_share,
            settings.model_error_margin,
            settings.resolution_error_margin,
        )
        side_probability = signal.p_true
    else:
        edge = no_net_edge(
            signal.p_true,
            p_exec,
            entry_fee_per_share,
            settings.model_error_margin,
            settings.resolution_error_margin,
        )
        side_probability = 1.0 - signal.p_true
    return entry_fee_per_share, edge, side_probability


def _max_executable_buy_target_usd(book: OrderBook, fee_rate: float) -> float:
    total = 0.0
    for level in book.asks:
        if level.size <= 0:
            continue
        total += level.size * (level.price + polymarket_taker_fee_per_share(level.price, fee_rate))
    return total


def _side_result(
    side: str,
    book: OrderBook,
    signal: WeatherSignal,
    settings: Settings,
    bankroll_before_entry: float,
    min_edge: float,
    entry_fraction_override: float | None,
    market_type: str,
) -> EdgeResult:
    liquidity_reason = _side_liquidity_reason(side, book, market_type)
    if liquidity_reason:
        return EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, liquidity_reason)

    p_exec, _shares, slip = executable_buy_price(
        book,
        settings.min_order_usd,
        fee_rate=settings.weather_taker_fee_rate,
    )
    if p_exec is None:
        return EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, f"{side} liquidity filter: insufficient ask depth [{market_type}]")

    entry_fee_per_share = 0.0
    edge = -999.0
    size_usd = 0.0
    partial_fill_reason = ""
    for _attempt in range(4):
        entry_fee_per_share, edge, side_probability = _side_edge_metrics(side, signal, p_exec, settings)
        p_eff = p_exec + entry_fee_per_share
        size_usd = position_size_usd(
            side_probability,
            p_eff,
            settings,
            bankroll_before_entry,
            entry_fraction_override,
            net_edge=edge,
            min_edge=min_edge,
        )
        if size_usd < settings.min_order_usd:
            break
        checked_p_exec, _checked_shares, checked_slip = executable_buy_price(
            book,
            size_usd,
            fee_rate=settings.weather_taker_fee_rate,
        )
        if checked_p_exec is None:
            requested_size_usd = size_usd
            capped_size_usd = min(
                requested_size_usd,
                _max_executable_buy_target_usd(book, settings.weather_taker_fee_rate),
            )
            if capped_size_usd + 1e-9 < settings.min_order_usd:
                return EdgeResult(
                    "SKIP",
                    signal.p_true,
                    p_exec,
                    edge,
                    0.0,
                    0.0,
                    f"{side} liquidity filter: insufficient ask depth for minimum order "
                    f"${settings.min_order_usd:.2f}; available=${max(0.0, capped_size_usd):.2f} [{market_type}]",
                )
            checked_p_exec, _checked_shares, checked_slip = executable_buy_price(
                book,
                capped_size_usd,
                fee_rate=settings.weather_taker_fee_rate,
            )
            if checked_p_exec is None:
                return EdgeResult(
                    "SKIP",
                    signal.p_true,
                    p_exec,
                    edge,
                    0.0,
                    0.0,
                    f"{side} liquidity filter: insufficient ask depth for capped order "
                    f"${capped_size_usd:.2f} [{market_type}]",
                )
            size_usd = capped_size_usd
            partial_fill_reason = (
                f", partial_fill=${capped_size_usd:.2f}/${requested_size_usd:.2f}"
            )
        if abs(checked_p_exec - p_exec) <= 1e-12 and abs(checked_slip - slip) <= 1e-12:
            break
        p_exec = checked_p_exec
        slip = checked_slip
    else:
        return EdgeResult(
            "SKIP",
            signal.p_true,
            p_exec,
            edge,
            0.0,
            0.0,
            f"{side} liquidity filter: execution price did not stabilize [{market_type}]",
        )

    if size_usd < settings.min_order_usd:
        reason = (
            f"{side} calculated order ${size_usd:.2f} below minimum order "
            f"${settings.min_order_usd:.2f}; skipping before expected-return estimate [{market_type}]"
        )
        return EdgeResult("SKIP", signal.p_true, p_exec, edge, 0.0, 0.0, reason)

    estimate_shares = fee_adjusted_entry_shares(size_usd, p_exec, settings.weather_taker_fee_rate)
    spread = max(0.0, (book.best_ask or p_exec) - (book.best_bid or p_exec))
    fair = model_fair_price(side, signal.p_true, settings)
    expected_exit = target_exit_price(p_exec, fair, settings)
    expected_exit_estimate = estimate_executable_net_return(
        shares=estimate_shares,
        entry_vwap=p_exec,
        expected_exit_price=expected_exit,
        expected_exit_spread=spread,
        expected_exit_slippage=slip,
        fee_rate=settings.weather_taker_fee_rate,
    )
    settlement_estimate = estimate_executable_net_return(
        shares=estimate_shares,
        entry_vwap=p_exec,
        expected_exit_price=conservative_settlement_value(side, signal.p_true, settings),
        fee_rate=settings.weather_taker_fee_rate,
        hold_to_settlement=True,
    )
    return_estimate = max(
        (expected_exit_estimate, settlement_estimate),
        key=lambda estimate: estimate.expected_net_return_pct,
    )
    return_ok = return_estimate.expected_net_return_pct >= settings.entry_min_expected_net_return_pct
    is_trade = edge > min_edge and size_usd >= settings.min_order_usd and return_ok
    rejection = ""
    if not return_ok:
        rejection = f", reject=expected net return below {settings.entry_min_expected_net_return_pct:.2%}"
    reason = (
        f"{side} edge={edge:.4f}, p_exec_vwap={p_exec:.4f}, route={return_estimate.route}, "
        f"expected_exit={return_estimate.expected_exit_price:.4f}, "
        f"expected_gross=${return_estimate.expected_gross_profit_usdc:.4f}, "
        f"estimated_cost=${return_estimate.estimated_cost_usdc:.4f}, "
        f"expected_net_return={return_estimate.expected_net_return_pct:.2%}, "
        f"entry_fee=${return_estimate.entry_fee_usdc:.4f}, "
        f"exit_fee=${return_estimate.exit_fee_usdc:.4f}, "
        f"exit_market_cost=${return_estimate.exit_market_cost_usdc:.4f}, "
        f"spread_audit={spread:.4f}, slip_audit={slip:.4f}{partial_fill_reason}{rejection} [{market_type}]"
    )
    return EdgeResult(
        side=side if is_trade else "SKIP",
        p_true=signal.p_true,
        p_exec=p_exec,
        net_edge=edge,
        size_usd=size_usd if edge > min_edge and return_ok else 0.0,
        size_shares=estimate_shares if edge > min_edge and return_ok else 0.0,
        reason=reason,
        expected_net_profit_usd=return_estimate.expected_net_profit_usdc if edge > min_edge and return_ok else 0.0,
    )


def _no_valid_side_reason(base_reason: str, per_side: dict[str, EdgeResult]) -> str:
    details = [
        result.reason
        for side in ("YES", "NO")
        if (result := per_side.get(side)) is not None and result.reason
    ]
    if not details:
        return base_reason
    return f"{base_reason} {' | '.join(details)}"


def _entry_bankroll_skip_reason(bankroll_before_entry: float, reason: str | None = None) -> str:
    detail = f"; {reason}" if reason else ""
    return f"{ENTRY_BANKROLL_FAIL_CLOSED_REASON}; entry_bankroll=${bankroll_before_entry:.2f}{detail}"


def pre_forecast_tradeability_gate(
    market: RawMarket,
    settings: Settings,
    market_type: str = "temperature",
) -> tuple[WeatherSignal, EdgeResult] | None:
    """Return a SKIP decision when a market should not reach forecast fetching."""
    parsed = parse_weather_question(market.question)

    def skip(source: str, note: str, reason: str) -> tuple[WeatherSignal, EdgeResult]:
        signal = WeatherSignal(
            p_true=0.5,
            confidence=0.0,
            source=source,
            note=note,
            parsed=parsed,
        )
        result = EdgeResult(
            side="SKIP",
            p_true=signal.p_true,
            p_exec=None,
            net_edge=-999.0,
            size_usd=0.0,
            size_shares=0.0,
            reason=reason,
        )
        return signal, result

    if parsed.variable != "temperature" or parsed.threshold_f is None or parsed.operator is None:
        note = "Unsupported weather market skipped before forecast request. " + parsed.note
        return skip(
            "unsupported-weather-market",
            note,
            f"unsupported-weather-market: refusing non-temperature or weakly parsed market before forecast [{market_type}]",
        )

    if parsed.city is None:
        return skip(
            "fallback",
            f"Could not parse city before forecast request. {parsed.note}",
            f"city not parsed: refusing market before forecast [{market_type}]",
        )

    if parsed.city.lower() not in TRADING_READY_STATION_MAP:
        return skip(
            "unsupported-station",
            f"{parsed.city} is not in the trading-ready Polymarket settlement-station allowlist with stored rule evidence.",
            f"unsupported-station: refusing market before forecast [{market_type}]",
        )

    if parsed.date_hint is None:
        return skip(
            "pre-forecast-skip",
            f"date_hint=None: Open-Meteo forecast skipped before request. {parsed.note}",
            f"date_hint=None: refusing undated market before forecast [{market_type}]",
        )

    return None


def evaluate_market(
    market: RawMarket,
    signal: WeatherSignal,
    client: PolymarketClient,
    settings: Settings,
    bankroll_before_entry: float,
    market_type: str = "temperature",
    entry_bankroll_reason: str | None = None,
) -> tuple[EdgeResult, dict[str, EdgeResult]]:
    """Evaluate live YES/NO books and return the best executable paper result."""
    min_confidence, min_edge, entry_fraction_override = _market_params(settings, market_type)

    if settings.require_parse_for_trade and signal.confidence < min_confidence:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, f"confidence too low: {signal.confidence:.2f} < {min_confidence:.2f} [{market_type}]")
        return result, {}

    if signal.parsed is not None and signal.parsed.date_hint is None:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, f"date_hint=None: refusing undated market [{market_type}]")
        return result, {}

    if bankroll_before_entry <= 0:
        result = EdgeResult(
            "SKIP",
            signal.p_true,
            None,
            -999.0,
            0.0,
            0.0,
            _entry_bankroll_skip_reason(bankroll_before_entry, entry_bankroll_reason),
        )
        return result, {}

    books, fetch_error = _fetch_books(market, client)
    if fetch_error:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, fetch_error)
        return result, {}

    sum_reason = _yes_no_sum_reason(books, market_type)
    if sum_reason:
        result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, sum_reason)
        return result, {side: _with_exit_signal(side, signal, result) for side in books}

    best_result = EdgeResult("SKIP", signal.p_true, None, -999.0, 0.0, 0.0, "No valid side evaluated.")
    per_side: dict[str, EdgeResult] = {}
    for side, book in books.items():
        result = _side_result(
            side,
            book,
            signal,
            settings,
            bankroll_before_entry,
            min_edge,
            entry_fraction_override,
            market_type,
        )
        result = _with_exit_signal(side, signal, result)
        per_side[side] = result
        if result.net_edge > best_result.net_edge:
            best_result = result

    if best_result.side == "SKIP":
        prefix = "trade blocked" if best_result.net_edge > min_edge else f"edge below {min_edge:.2%}"
        reason = best_result.reason
        if best_result.net_edge <= -999.0:
            reason = _no_valid_side_reason(reason, per_side)
        best_result = EdgeResult(
            side="SKIP",
            p_true=signal.p_true,
            p_exec=best_result.p_exec,
            net_edge=best_result.net_edge,
            size_usd=0.0,
            size_shares=0.0,
            reason=f"{prefix} [{market_type}]. {reason}",
        )
    elif best_result.net_edge <= min_edge:
        best_result = EdgeResult(
            side="SKIP",
            p_true=signal.p_true,
            p_exec=best_result.p_exec,
            net_edge=best_result.net_edge,
            size_usd=0.0,
            size_shares=0.0,
            reason=f"edge below {min_edge:.2%} [{market_type}]. {best_result.reason}",
        )
    return best_result, per_side


def _event_portfolio_candidates(
    market: RawMarket,
    signal: WeatherSignal,
    result: EdgeResult,
    per_side: dict[str, EdgeResult],
    market_type: str,
    decision_ts: str = "",
) -> list[PortfolioCandidate]:
    executable = [
        PortfolioCandidate(market, signal, edge_result, market_type, decision_ts)
        for edge_result in per_side.values()
        if edge_result.side in {"YES", "NO"}
    ]
    return executable or [PortfolioCandidate(market, signal, result, market_type, decision_ts)]


def _refresh_held_exit_edges_from_signal(
    broker: PaperBroker,
    market: RawMarket,
    signal: WeatherSignal,
    latest_edges: dict[tuple[str, str], EdgeResult],
    entry_bankroll_reason: str | None,
) -> dict[str, EdgeResult]:
    refreshed: dict[str, EdgeResult] = {}
    reason = "held exit evidence refreshed from latest signal while new entries are blocked"
    if entry_bankroll_reason:
        reason = f"{reason}; entry_bankroll_reason={entry_bankroll_reason}"
    for pos in broker.state.positions:
        if pos.market_id != market.market_id:
            continue
        edge = EdgeResult(
            pos.side,
            signal.p_true,
            None,
            -999.0,
            0.0,
            0.0,
            reason,
        )
        edge = _with_exit_signal(pos.side, signal, edge)
        latest_edges[(pos.market_id, pos.side)] = edge
        refreshed[pos.side] = edge
    return refreshed


def _is_temperature_market(market: RawMarket) -> bool:
    parsed = parse_weather_question(market.question)
    return parsed.variable == "temperature" and parsed.threshold_f is not None and parsed.operator is not None


def _temperature_markets_only(markets: list[RawMarket]) -> list[RawMarket]:
    return [market for market in markets if _is_temperature_market(market)]


def _market_from_position(pos: PaperPosition) -> RawMarket:
    return RawMarket(
        market_id=pos.market_id,
        question=pos.question,
        slug=pos.metadata.get("slug"),
        active=True,
        closed=False,
        yes_token_id=pos.token_id if pos.side == "YES" else None,
        no_token_id=pos.token_id if pos.side == "NO" else None,
        event_slug=pos.metadata.get("event_slug"),
    )


def _sleep_seconds_until_next_cycle(started_at: datetime, interval_seconds: int, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    elapsed = (now - started_at).total_seconds()
    return max(0.0, float(interval_seconds) - elapsed)


def refresh_open_position_edges(
    broker: PaperBroker,
    client: PolymarketClient,
    settings: Settings,
    latest_edges: dict[tuple[str, str], EdgeResult],
    market_by_id: dict[str, RawMarket],
    probability_estimator=estimate_weather_probability,
    ensemble_client: OpenMeteoEnsembleClient | None = None,
    observation_provider: Any | None = None,
) -> None:
    """Refresh model probability and edge for held positions missing from the scan."""
    for pos in broker.state.positions:
        key = (pos.market_id, pos.side)
        if key in latest_edges:
            continue
        market = market_by_id.get(pos.market_id) or _market_from_position(pos)
        if not _is_temperature_market(market):
            continue
        gated = pre_forecast_tradeability_gate(market, settings)
        if gated is not None:
            _signal, result = gated
            latest_edges[key] = result
            market_by_id.setdefault(pos.market_id, market)
            continue
        signal = _call_probability_estimator(
            probability_estimator,
            pos.question,
            settings=settings,
            ensemble_client=ensemble_client,
            observation_provider=observation_provider,
        )
        market_type = "temperature"
        _best, per_side = evaluate_market(
            market,
            signal,
            client,
            settings,
            broker.current_bankroll_before_entry(),
            market_type,
        )
        if pos.side in per_side:
            latest_edges[key] = per_side[pos.side]
        market_by_id.setdefault(pos.market_id, market)


def _hydrate_open_position_markets(client: PolymarketClient, broker: PaperBroker, market_by_id: dict[str, RawMarket]) -> None:
    for pos in broker.state.positions:
        if pos.market_id in market_by_id:
            continue
        try:
            market_by_id[pos.market_id] = client.get_market(pos.market_id)
        except Exception:
            market_by_id[pos.market_id] = _market_from_position(pos)


def _stream_market_registry(
    client: PolymarketClient,
    broker: PaperBroker,
    markets: list[RawMarket],
) -> dict[str, RawMarket]:
    market_by_id = {market.market_id: market for market in markets}
    _hydrate_open_position_markets(client, broker, market_by_id)
    return market_by_id


def _settle_resolved_positions_before_streaming(
    broker: PaperBroker,
    market_by_id: dict[str, RawMarket],
) -> list[str]:
    open_market_ids_before = {pos.market_id for pos in broker.state.positions}
    messages = maybe_settle_resolved_positions(broker, market_by_id)
    if messages:
        open_market_ids_after = {pos.market_id for pos in broker.state.positions}
        for market_id in open_market_ids_before - open_market_ids_after:
            market_by_id.pop(market_id, None)
    return messages


def _market_event_key(market: RawMarket) -> str:
    if market.event_id:
        return market.event_id
    parsed = parse_weather_question(market.question)
    return "|".join([
        parsed.city or "unknown-city",
        parsed.date_hint or "unknown-date",
        parsed.variable,
        parsed.temperature_metric,
    ])


def _group_weather_markets_by_event(markets: list[RawMarket]) -> list[list[RawMarket]]:
    groups: dict[str, list[RawMarket]] = {}
    for market in markets:
        groups.setdefault(_market_event_key(market), []).append(market)
    return list(groups.values())


def _discovery_coverage(markets: list[RawMarket]) -> dict[str, int]:
    groups = _group_weather_markets_by_event(markets)
    cities = {
        parsed.city
        for market in markets
        if (parsed := parse_weather_question(market.question)).city
    }
    return {"events": len(groups), "cities": len(cities), "markets": len(markets)}


def run_cycle(settings: Settings | None = None) -> list[MarketDecision]:
    settings = settings or load_settings()
    cycle_started_at = utc_now_iso()
    market_error_count = 0
    last_market_error: dict[str, Any] | None = None
    write_runner_status(
        settings,
        "starting",
        message="starting cycle",
        cycle_started_at=cycle_started_at,
        **_market_error_status_fields(market_error_count, last_market_error),
    )
    client = PolymarketClient(settings.gamma_base, settings.clob_base)
    ensemble_client = OpenMeteoEnsembleClient.from_settings(settings)
    observation_provider = AviationWeatherMetarNowcastProvider.from_settings(settings)
    broker = PaperBroker(settings)
    try:
        write_runner_status(
            settings,
            "discovering",
            message="discovering markets",
            cycle_started_at=cycle_started_at,
            **_market_error_status_fields(market_error_count, last_market_error),
        )
        discovered_markets = client.discover_weather_markets(
            max_pages=settings.discovery_max_pages,
            page_size=settings.discovery_page_size,
        )
    except Exception as exc:  # noqa: BLE001
        write_runner_status(
            settings,
            "error",
            message=f"market discovery failed: {exc}",
            cycle_started_at=cycle_started_at,
            **_market_error_status_fields(market_error_count, last_market_error),
        )
        print(f"DATA ERROR: could not fetch live Polymarket markets: {exc}")
        print("Check internet/DNS/VPN, then run live-paper-bot again.")
        return []

    discovered_markets = _temperature_markets_only(discovered_markets)
    event_groups = _group_weather_markets_by_event(discovered_markets)
    markets = [market for group in event_groups for market in group]
    coverage = _discovery_coverage(markets)
    market_by_id = {m.market_id: m for m in markets}
    decisions: list[MarketDecision] = []
    latest_edges: dict[tuple[str, str], EdgeResult] = {}

    print(
        f"\nLIVE PAPER CYCLE | markets={len(markets)} | events={coverage['events']} | "
        f"cities={coverage['cities']} | "
        f"cash=${broker.state.cash_usd:.2f} | exposure=${broker.total_exposure():.2f} | "
        f"bankroll=${broker.current_bankroll_before_entry():.2f} | open_positions={len(broker.state.positions)}"
    )
    write_runner_status(
        settings,
        "evaluating",
        message=f"evaluating 0/{len(markets)} markets across {coverage['events']} events, {coverage['cities']} cities",
        cycle_started_at=cycle_started_at,
        markets_done=0,
        markets_total=len(markets),
        events_total=coverage["events"],
        cities_total=coverage["cities"],
        cash_usd=round(broker.state.cash_usd, 2),
        exposure_usd=round(broker.total_exposure(), 2),
        open_positions=len(broker.state.positions),
        **_market_error_status_fields(market_error_count, last_market_error),
    )
    markets_done = 0
    for event_markets in event_groups:
        entry_bankroll = available_entry_bankroll(broker, client)
        candidates: list[PortfolioCandidate] = []
        for market in event_markets:
            markets_done += 1
            try:
                market_type = "temperature"
                gated = pre_forecast_tradeability_gate(market, settings, market_type)
                if gated is not None:
                    signal, result = gated
                    per_side: dict[str, EdgeResult] = {}
                else:
                    signal = _call_probability_estimator(
                        estimate_weather_probability,
                        market.question,
                        settings=settings,
                        ensemble_client=ensemble_client,
                        observation_provider=observation_provider,
                    )
                    result, per_side = evaluate_market(
                        market,
                        signal,
                        client,
                        settings,
                        entry_bankroll.entry_bankroll,
                        market_type,
                        entry_bankroll.reason,
                    )
                for side, edge_result in per_side.items():
                    latest_edges[(market.market_id, side)] = edge_result
                decisions.append(MarketDecision(market=market, signal=signal, result=result))
                decision_ts = broker.log_decision(market, result, signal.note, market_type)
                candidates.extend(_event_portfolio_candidates(market, signal, result, per_side, market_type, decision_ts))
                broker.log_raw_snapshot(
                    "decision",
                    market,
                    {
                        "market_raw": market.raw,
                        "signal": {
                            "p_true": signal.p_true,
                            "confidence": signal.confidence,
                            "source": signal.source,
                            "note": signal.note,
                            "nowcast": signal.nowcast,
                        },
                        "per_side": {side: edge.__dict__ for side, edge in per_side.items()},
                    },
                )
                print("-" * 100)
                print(f"Q: {market.question}")
                print(f"P_true={signal.p_true:.3f} confidence={signal.confidence:.2f} source={signal.source}")
                print(f"Decision={result.side} edge={result.net_edge:.3f} p_exec={result.p_exec} size=${result.size_usd:.2f}")
                print(f"Reason: {result.reason}")
                print(f"Note: {signal.note}")
            except Exception as exc:  # noqa: BLE001
                signal, result, last_market_error, market_error_count = _record_market_evaluation_error(
                    broker,
                    market,
                    exc,
                    "temperature",
                    context="cycle",
                )
                decisions.append(MarketDecision(market=market, signal=signal, result=result))
                candidates.extend(_event_portfolio_candidates(market, signal, result, {}, "temperature"))
                print("-" * 100)
                print(f"Q: {market.question}")
                print(f"ERROR: {exc}")
            write_runner_status(
                settings,
                "evaluating",
                message=f"evaluating {markets_done}/{len(markets)}",
                cycle_started_at=cycle_started_at,
                markets_done=markets_done,
                markets_total=len(markets),
                events_total=coverage["events"],
                cities_total=coverage["cities"],
                last_market=market.question,
                cash_usd=round(broker.state.cash_usd, 2),
                exposure_usd=round(broker.total_exposure(), 2),
                open_positions=len(broker.state.positions),
                **_market_error_status_fields(market_error_count, last_market_error),
            )
        portfolio = _apply_event_portfolio(broker, candidates, entry_bankroll)
        print(
            f"EVENT PORTFOLIO {portfolio.event_key}: selected={len(portfolio.selected)} "
            f"exposure=${portfolio.selected_exposure_usd:.2f} cap=${portfolio.event_cap_usd:.2f} "
            f"expected_net_profit=${portfolio.expected_net_profit_usd:.2f}"
        )

    write_runner_status(
        settings,
        "closing",
        message="checking settlements and exits",
        cycle_started_at=cycle_started_at,
        markets_done=len(markets),
        markets_total=len(markets),
        events_total=coverage["events"],
        cities_total=coverage["cities"],
        **_market_error_status_fields(market_error_count, last_market_error),
    )
    _hydrate_open_position_markets(client, broker, market_by_id)
    settlement_msgs = maybe_settle_resolved_positions(broker, market_by_id)
    for msg in settlement_msgs:
        print(msg)

    refresh_open_position_edges(
        broker,
        client,
        settings,
        latest_edges,
        market_by_id,
        ensemble_client=ensemble_client,
        observation_provider=observation_provider,
    )
    close_msgs = maybe_close_positions(broker, client, market_by_id, latest_edges)
    for msg in close_msgs:
        print(msg)
    print(
        f"SUMMARY cash=${broker.state.cash_usd:.2f} realized_pnl=${broker.state.realized_pnl_usd:.2f} "
        f"exposure=${broker.total_exposure():.2f} bankroll=${broker.current_bankroll_before_entry():.2f}"
    )
    print(broker.stats_summary())
    write_runner_status(
        settings,
        "cycle_complete",
        message=f"cycle complete {len(decisions)}/{len(markets)}",
        cycle_started_at=cycle_started_at,
        markets_done=len(markets),
        markets_total=len(markets),
        events_total=coverage["events"],
        cities_total=coverage["cities"],
        cash_usd=round(broker.state.cash_usd, 2),
        exposure_usd=round(broker.total_exposure(), 2),
        open_positions=len(broker.state.positions),
        **_market_error_status_fields(market_error_count, last_market_error),
    )
    return decisions


def _market_token_ids(market: RawMarket) -> list[str]:
    return [token_id for token_id in (market.yes_token_id, market.no_token_id) if token_id]


def _open_position_if_needed(
    broker: PaperBroker,
    market: RawMarket,
    signal: WeatherSignal,
    result: EdgeResult,
    market_type: str,
    entry_bankroll_usd: float | None = None,
    decision_ts: str = "",
    add_to_existing_position_id: str | None = None,
) -> None:
    if result.side not in {"YES", "NO"}:
        return
    if not market.active or market.closed:
        return
    token_id = market.yes_token_id if result.side == "YES" else market.no_token_id
    allow_same_side_add = (
        add_to_existing_position_id is not None
        and broker.has_position(market.market_id, result.side)
    )
    if (broker.has_any_position(market.market_id) and not allow_same_side_add) or not token_id:
        return
    city = signal.parsed.city if signal.parsed is not None else ""
    date_hint = signal.parsed.date_hint if signal.parsed is not None else ""
    broker.open_position(
        market,
        token_id,
        result,
        market_type,
        city=city or "",
        date_hint=date_hint or "",
        entry_bankroll_usd=entry_bankroll_usd,
        decision_ts=decision_ts,
        allow_same_side_add=allow_same_side_add,
    )


def _apply_event_portfolio(
    broker: PaperBroker,
    candidates: list[PortfolioCandidate],
    entry_bankroll: EntryBankrollSnapshot,
) -> EventPortfolioDecision:
    decision = select_event_portfolio(broker, candidates, entry_bankroll)
    broker.log_event_portfolio_decision(decision.to_log_payload())
    for candidate in decision.selected:
        _open_position_if_needed(
            broker,
            candidate.market,
            candidate.signal,
            candidate.result,
            candidate.market_type,
            entry_bankroll_usd=entry_bankroll.entry_bankroll,
            decision_ts=candidate.decision_ts,
            add_to_existing_position_id=candidate.add_to_existing_position_id,
        )
    return decision


def _evaluate_realtime_update(
    updated_token_ids: set[str],
    client: StreamBackedPolymarketClient,
    broker: PaperBroker,
    settings: Settings,
    market_by_token: dict[str, RawMarket],
    signals_by_market: dict[str, WeatherSignal],
    market_types: dict[str, str],
    latest_edges: dict[tuple[str, str], EdgeResult],
    *,
    signal_refreshed_at_by_market: dict[str, datetime] | None = None,
    probability_estimator: Any = estimate_weather_probability,
    ensemble_client: OpenMeteoEnsembleClient | None = None,
    observation_provider: Any | None = None,
    now: datetime | None = None,
) -> None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    websocket_health: dict[str, object] = {}
    stream = getattr(client, "stream", None)
    if stream is not None and hasattr(stream, "health_snapshot"):
        websocket_health = stream.health_snapshot()
    touched_events = {
        _market_event_key(market_by_token[token_id])
        for token_id in updated_token_ids
        if token_id in market_by_token
    }
    market_by_id = {market.market_id: market for market in market_by_token.values()}
    event_groups: dict[str, list[RawMarket]] = {}
    for market in market_by_id.values():
        event_groups.setdefault(_market_event_key(market), []).append(market)
    for event_key in touched_events:
        entry_bankroll = available_entry_bankroll(broker, client)
        candidates: list[PortfolioCandidate] = []
        for market in event_groups[event_key]:
            market_type = market_types.get(market.market_id, "temperature")
            try:
                _refresh_realtime_signal_if_needed(
                    market,
                    settings,
                    signals_by_market,
                    signal_refreshed_at_by_market,
                    probability_estimator=probability_estimator,
                    ensemble_client=ensemble_client,
                    observation_provider=observation_provider,
                    now=current,
                )
                signal = signals_by_market[market.market_id]
                result, per_side = evaluate_market(
                    market,
                    signal,
                    client,
                    settings,
                    entry_bankroll.entry_bankroll,
                    market_type,
                    entry_bankroll.reason,
                )
                for side, edge_result in per_side.items():
                    latest_edges[(market.market_id, side)] = edge_result
                held_exit_edges: dict[str, EdgeResult] = {}
                if not per_side and "entry_bankroll=$" in result.reason:
                    held_exit_edges = _refresh_held_exit_edges_from_signal(
                        broker,
                        market,
                        signal,
                        latest_edges,
                        entry_bankroll.reason,
                    )
                decision_ts = broker.log_decision(market, result, signal.note, market_type)
                candidates.extend(_event_portfolio_candidates(market, signal, result, per_side, market_type, decision_ts))
                broker.log_raw_snapshot(
                    "realtime_decision",
                    market,
                    {
                        "updated_token_ids": sorted(updated_token_ids),
                        "signal": {
                            "p_true": signal.p_true,
                            "confidence": signal.confidence,
                            "source": signal.source,
                            "note": signal.note,
                            "nowcast": signal.nowcast,
                        },
                        "entry_bankroll": entry_bankroll.__dict__,
                        "websocket": websocket_health,
                        "per_side": {side: edge.__dict__ for side, edge in per_side.items()},
                        "held_exit_edges": {side: edge.__dict__ for side, edge in held_exit_edges.items()},
                    },
                )
            except Exception as exc:  # noqa: BLE001
                signal, result, _last_market_error, _market_error_count = _record_market_evaluation_error(
                    broker,
                    market,
                    exc,
                    market_type,
                    signal=signals_by_market.get(market.market_id),
                    context="realtime_update",
                )
                candidates.extend(_event_portfolio_candidates(market, signal, result, {}, market_type))
        _apply_event_portfolio(broker, candidates, entry_bankroll)
    for message in maybe_close_positions(broker, client, market_by_id, latest_edges):
        print(message)


def _refresh_realtime_signal_if_needed(
    market: RawMarket,
    settings: Settings,
    signals_by_market: dict[str, WeatherSignal],
    signal_refreshed_at_by_market: dict[str, datetime] | None,
    *,
    probability_estimator: Any = estimate_weather_probability,
    ensemble_client: OpenMeteoEnsembleClient | None = None,
    observation_provider: Any | None = None,
    now: datetime | None = None,
) -> None:
    if signal_refreshed_at_by_market is None:
        return
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    last_refreshed_at = signal_refreshed_at_by_market.get(market.market_id)
    if last_refreshed_at is None:
        signal_refreshed_at_by_market[market.market_id] = current
        return
    last_refreshed_at = last_refreshed_at.astimezone(timezone.utc)
    if (current - last_refreshed_at).total_seconds() < settings.station_nowcast_cache_ttl_seconds:
        return

    gated = pre_forecast_tradeability_gate(market, settings, "temperature")
    if gated is not None:
        signal, _result = gated
    else:
        signal = _call_probability_estimator(
            probability_estimator,
            market.question,
            settings=settings,
            ensemble_client=ensemble_client,
            observation_provider=observation_provider,
        )
    signals_by_market[market.market_id] = signal
    signal_refreshed_at_by_market[market.market_id] = current


def _stream_status_phase(
    websocket_health: dict[str, object],
    *,
    token_count: int,
    market_count: int,
    event_count: int,
    city_count: int,
) -> tuple[str, str]:
    coverage = f"{token_count} tokens across {market_count} markets, {event_count} events, {city_count} cities"
    block_reason = websocket_pricing_block_reason(websocket_health)
    recovery = ""
    if block_reason:
        recovery = f": {block_reason}"
    if not websocket_health.get("thread_alive"):
        return "stream_error", f"websocket thread stopped{recovery}; {coverage}"
    if websocket_health.get("stale"):
        return "stream_stale", f"websocket order book stale{recovery}; {coverage}"
    return "streaming", f"websocket streaming {coverage}"


def _stream_should_rebuild(websocket_health: dict[str, object], *, token_count: int) -> bool:
    return token_count > 0 and not bool(websocket_health.get("thread_alive"))


def _realtime_error_backoff_seconds(settings: Settings) -> float:
    return min(max(float(settings.runner_health_status_interval_seconds), 5.0), 60.0)


def run_realtime_forever(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    while True:
        refresh_started_at = datetime.now(timezone.utc)
        cycle_started_at = utc_now_iso()
        failed_phase = "initializing"
        broker: PaperBroker | None = None
        ensemble_client: OpenMeteoEnsembleClient | None = None
        stream: OrderBookMarketStream | None = None
        evaluator_worker: RealtimeEvaluationCoalescer | None = None
        try:
            discovery_client = PolymarketClient(settings.gamma_base, settings.clob_base)
            ensemble_client = OpenMeteoEnsembleClient.from_settings(settings)
            observation_provider = AviationWeatherMetarNowcastProvider.from_settings(settings)
            broker = PaperBroker(settings)
            failed_phase = "market_discovery"
            write_runner_status(
                settings,
                "discovering",
                message="discovering markets for websocket stream",
                cycle_started_at=cycle_started_at,
                **_market_error_status_fields(0, None),
            )
            discovered_markets = discovery_client.discover_weather_markets(
                max_pages=settings.discovery_max_pages,
                page_size=settings.discovery_page_size,
            )
            failed_phase = "market_preparation"
            discovered_markets = _temperature_markets_only(discovered_markets)
            event_groups = _group_weather_markets_by_event(discovered_markets)
            markets = [market for group in event_groups for market in group]
            market_by_id = _stream_market_registry(discovery_client, broker, markets)
            for msg in _settle_resolved_positions_before_streaming(broker, market_by_id):
                print(msg)
            open_market_ids = {pos.market_id for pos in broker.state.positions}
            stream_candidates = _temperature_markets_only(list(market_by_id.values()))
            stream_markets: list[RawMarket] = []
            precomputed_signals: dict[str, WeatherSignal] = {}
            for market in stream_candidates:
                market_type = "temperature"
                gated = pre_forecast_tradeability_gate(market, settings, market_type)
                if gated is not None:
                    signal, result = gated
                    precomputed_signals[market.market_id] = signal
                    broker.log_decision(market, result, signal.note, market_type)
                    broker.log_raw_snapshot(
                        "pre_forecast_skip",
                        market,
                        {
                            "market_raw": market.raw,
                            "signal": {
                                "p_true": signal.p_true,
                                "confidence": signal.confidence,
                                "source": signal.source,
                                "note": signal.note,
                                "nowcast": signal.nowcast,
                            },
                            "per_side": {},
                        },
                    )
                    if market.market_id in open_market_ids:
                        stream_markets.append(market)
                    continue
                stream_markets.append(market)
            coverage = _discovery_coverage(stream_markets)
            market_by_token = {
                token_id: market
                for market in stream_markets
                for token_id in _market_token_ids(market)
            }
            signals_by_market: dict[str, WeatherSignal] = {}
            signal_refreshed_at_by_market: dict[str, datetime] = {}
            market_types: dict[str, str] = {}
            failed_phase = "forecast_preparation"
            for market in stream_markets:
                signal = precomputed_signals.get(market.market_id)
                if signal is None:
                    signal = _call_probability_estimator(
                        estimate_weather_probability,
                        market.question,
                        settings=settings,
                        ensemble_client=ensemble_client,
                        observation_provider=observation_provider,
                    )
                signals_by_market[market.market_id] = signal
                signal_refreshed_at_by_market[market.market_id] = datetime.now(timezone.utc)
                market_types[market.market_id] = "temperature"

            latest_edges: dict[tuple[str, str], EdgeResult] = {}
            update_lock = threading.RLock()
            stream_holder: dict[str, StreamBackedPolymarketClient] = {}
            event_key_by_token = {
                token_id: _market_event_key(market)
                for token_id, market in market_by_token.items()
            }

            def evaluate_queued_update(updated_token_ids: set[str]) -> None:
                with update_lock:
                    stream_client = stream_holder.get("client")
                    if stream_client is not None:
                        _evaluate_realtime_update(
                            updated_token_ids,
                            stream_client,
                            broker,
                            settings,
                            market_by_token,
                            signals_by_market,
                            market_types,
                            latest_edges,
                            signal_refreshed_at_by_market=signal_refreshed_at_by_market,
                            ensemble_client=ensemble_client,
                            observation_provider=observation_provider,
                        )

            def update_evaluator_status(status: dict[str, object]) -> None:
                update_runner_status_fields(settings, realtime_evaluator=status)

            evaluator_worker = RealtimeEvaluationCoalescer(
                event_key_by_token=event_key_by_token,
                evaluator=evaluate_queued_update,
                status_update=update_evaluator_status,
            )
            evaluator_worker.start()

            def on_update(updated_token_ids: set[str]) -> None:
                evaluator_worker.enqueue_tokens(updated_token_ids)

            def build_stream() -> OrderBookMarketStream:
                return OrderBookMarketStream(
                    settings.orderbook_stream_url,
                    on_update=on_update,
                    heartbeat_seconds=settings.orderbook_stream_heartbeat_seconds,
                    reconnect_seconds=settings.orderbook_stream_reconnect_seconds,
                    stale_seconds=settings.orderbook_stream_stale_seconds,
                )

            failed_phase = "websocket_start"
            stream = build_stream()
            stream_holder["client"] = StreamBackedPolymarketClient(settings.gamma_base, settings.clob_base, stream)
            stream.start(market_by_token.keys())

            def write_stream_status(websocket_health: dict[str, object] | None = None) -> None:
                websocket_health = websocket_health or stream.health_snapshot()
                market_error_count, last_market_error = _market_error_status(settings)
                phase, message = _stream_status_phase(
                    websocket_health,
                    token_count=len(market_by_token),
                    market_count=len(stream_markets),
                    event_count=coverage["events"],
                    city_count=coverage["cities"],
                )
                write_runner_status(
                    settings,
                    phase,
                    message=message,
                    cycle_started_at=cycle_started_at,
                    markets_done=0,
                    markets_total=len(stream_markets),
                    events_total=coverage["events"],
                    cities_total=coverage["cities"],
                    cash_usd=round(broker.state.cash_usd, 2),
                    exposure_usd=round(broker.total_exposure(), 2),
                    open_positions=len(broker.state.positions),
                    forecast=ensemble_client.health_snapshot(),
                    websocket=websocket_health,
                    realtime_evaluator=evaluator_worker.status_snapshot() if evaluator_worker is not None else None,
                    **_market_error_status_fields(market_error_count, last_market_error),
                )

            failed_phase = "runner_status_update"
            write_stream_status()
            status_updated_at = datetime.now(timezone.utc)
            try:
                failed_phase = "websocket_monitoring"
                while True:
                    now = datetime.now(timezone.utc)
                    elapsed = (now - refresh_started_at).total_seconds()
                    if elapsed >= settings.stream_cycle_interval_seconds:
                        break
                    websocket_health = stream.health_snapshot()
                    if _stream_should_rebuild(websocket_health, token_count=len(market_by_token)):
                        failed_phase = "runner_status_update"
                        write_stream_status(websocket_health)
                        print(f"STREAM REBUILD {websocket_health.get('status_reason') or 'websocket thread stopped'}")
                        failed_phase = "websocket_rebuild"
                        with update_lock:
                            stream.stop()
                            stream = build_stream()
                            stream_holder["client"] = StreamBackedPolymarketClient(settings.gamma_base, settings.clob_base, stream)
                            stream.start(market_by_token.keys())
                        status_updated_at = now
                    if (now - status_updated_at).total_seconds() >= settings.runner_health_status_interval_seconds:
                        failed_phase = "runner_status_update"
                        write_stream_status()
                        status_updated_at = now
                    failed_phase = "websocket_monitoring"
                    time.sleep(1)
            finally:
                try:
                    stream.stop()
                except Exception:  # noqa: BLE001
                    failed_phase = "websocket_stop"
                    raise
                stream = None
                if evaluator_worker is not None:
                    evaluator_worker.stop()
                    evaluator_worker = None
        except Exception as exc:  # noqa: BLE001
            evaluator_status = evaluator_worker.status_snapshot() if evaluator_worker is not None else None
            websocket_health = None
            if stream is not None and hasattr(stream, "health_snapshot"):
                try:
                    websocket_health = stream.health_snapshot()
                except Exception as health_exc:  # noqa: BLE001
                    websocket_health = {
                        "thread_alive": False,
                        "stale": True,
                        "last_error": f"{health_exc.__class__.__name__}: {health_exc}",
                        "status_reason": "websocket health snapshot failed during realtime error handling",
                    }
            if evaluator_worker is not None:
                evaluator_worker.stop(drain=False)
                evaluator_worker = None
            if stream is not None:
                try:
                    stream.stop()
                except Exception as stop_exc:  # noqa: BLE001
                    print(f"STREAM STOP ERROR after realtime failure: {stop_exc}")
                stream = None
            message = f"realtime refresh cycle failed during {failed_phase}: {exc}"
            market_error_count, last_market_error = _market_error_status(settings)
            write_runner_status(
                settings,
                "error",
                message=message,
                failed_phase=failed_phase,
                error_type=exc.__class__.__name__,
                cycle_started_at=cycle_started_at,
                cash_usd=round(broker.state.cash_usd, 2) if broker is not None else None,
                exposure_usd=round(broker.total_exposure(), 2) if broker is not None else None,
                open_positions=len(broker.state.positions) if broker is not None else None,
                forecast=ensemble_client.health_snapshot() if ensemble_client is not None else None,
                websocket=websocket_health,
                realtime_evaluator=evaluator_status,
                **_market_error_status_fields(market_error_count, last_market_error),
            )
            print(f"REALTIME ERROR: {message}")
            backoff_seconds = _realtime_error_backoff_seconds(settings)
            print(f"Retrying realtime refresh cycle in {backoff_seconds:.0f}s.")
            time.sleep(backoff_seconds)


def run_forever(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    if not settings.orderbook_stream_enabled:
        raise RuntimeError("ORDERBOOK_STREAM_ENABLED=false disables the required real-time order-book stream.")
    run_realtime_forever(settings)


def main() -> None:
    run_forever(load_settings())


if __name__ == "__main__":
    main()
