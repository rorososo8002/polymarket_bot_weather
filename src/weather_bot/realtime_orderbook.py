from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from .models import OrderBook, OrderLevel
from .orderbook_validation import finite_float, valid_level_size, valid_orderbook_price

MARKET_STREAM_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WEBSOCKET_CLIENT_MISSING_MESSAGE = "Install websocket-client to use real-time Polymarket orderbook streaming."


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _safe_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text}"[:240]


def _websocket_client_import_error(exc: BaseException) -> str:
    return f"websocket-client import failed: {_safe_error(exc)}"


def _import_websocket_client() -> Any:
    try:
        import websocket  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(WEBSOCKET_CLIENT_MISSING_MESSAGE) from exc
    return websocket


def _executable_orderbook_update_token_ids(message: str | dict[str, Any] | list[dict[str, Any]]) -> set[str]:
    if isinstance(message, str):
        try:
            message = json.loads(message)
        except json.JSONDecodeError:
            return set()
    if isinstance(message, list):
        token_ids: set[str] = set()
        for item in message:
            token_ids.update(_executable_orderbook_update_token_ids(item))
        return token_ids
    if not isinstance(message, dict):
        return set()
    event_type = str(message.get("event_type") or "")
    if event_type == "book":
        token_id = str(message.get("asset_id") or "")
        if token_id and isinstance(message.get("bids"), list) and isinstance(message.get("asks"), list):
            return {token_id}
        return set()
    if event_type == "price_change":
        token_ids: set[str] = set()
        price_changes = message.get("price_changes")
        if not isinstance(price_changes, list):
            return token_ids
        for change in price_changes:
            if not isinstance(change, dict):
                continue
            token_id = str(change.get("asset_id") or "")
            side = str(change.get("side") or "").upper()
            price = valid_orderbook_price(change.get("price"))
            size = valid_level_size(change.get("size"), allow_zero=True)
            if token_id and side in {"BUY", "SELL"} and price is not None and size is not None:
                token_ids.add(token_id)
        return token_ids
    return set()


def _contains_executable_orderbook_update(message: str | dict[str, Any] | list[dict[str, Any]]) -> bool:
    return bool(_executable_orderbook_update_token_ids(message))


def market_subscription_message(asset_ids: Iterable[str]) -> dict[str, Any]:
    return {
        "type": "market",
        "assets_ids": [str(asset_id) for asset_id in asset_ids if str(asset_id)],
        "custom_feature_enabled": True,
    }


def _levels(raw_levels: Any, *, reverse: bool) -> list[OrderLevel] | None:
    if not isinstance(raw_levels, list):
        return None
    levels: list[OrderLevel] = []
    for level in raw_levels:
        if not isinstance(level, dict):
            continue
        price = valid_orderbook_price(level.get("price"))
        size = valid_level_size(level.get("size"), allow_zero=False)
        if price is None or size is None:
            continue
        levels.append(OrderLevel(price, size))
    return sorted(levels, key=lambda level: level.price, reverse=reverse)


def _set_level(levels: list[OrderLevel], price: float, size: float, *, reverse: bool) -> list[OrderLevel]:
    kept = [level for level in levels if abs(level.price - price) > 1e-12]
    if size > 0:
        kept.append(OrderLevel(price, size))
    return sorted(kept, key=lambda level: level.price, reverse=reverse)


class OrderBookStreamCache:
    def __init__(self) -> None:
        self._books: dict[str, OrderBook] = {}
        self._snapshot_token_ids: set[str] = set()
        self._lock = threading.RLock()

    def get_order_book(self, token_id: str) -> OrderBook:
        with self._lock:
            book = self._books.get(str(token_id))
            if book is None:
                raise KeyError(f"no websocket orderbook snapshot for token {token_id}")
            return book

    def apply_message(self, message: str | dict[str, Any] | list[dict[str, Any]]) -> set[str]:
        if isinstance(message, str):
            try:
                message = json.loads(message)
            except json.JSONDecodeError:
                return set()
        if isinstance(message, list):
            updated: set[str] = set()
            for item in message:
                updated.update(self.apply_message(item))
            return updated
        if not isinstance(message, dict):
            return set()
        event_type = str(message.get("event_type") or "")
        if event_type == "book":
            return self._apply_book(message)
        if event_type == "price_change":
            return self._apply_price_change(message)
        if event_type == "best_bid_ask":
            return self._apply_best_bid_ask(message)
        if event_type == "last_trade_price":
            return self._apply_last_trade_price(message)
        if event_type == "tick_size_change":
            return self._apply_tick_size(message)
        return set()

    def _apply_book(self, message: dict[str, Any]) -> set[str]:
        token_id = str(message.get("asset_id") or "")
        if not token_id:
            return set()
        bids = _levels(message.get("bids"), reverse=True)
        asks = _levels(message.get("asks"), reverse=False)
        if bids is None or asks is None:
            return set()
        book = OrderBook(
            token_id=token_id,
            bids=bids,
            asks=asks,
            market=str(message.get("market") or "") or None,
            timestamp=str(message.get("timestamp") or "") or None,
            book_hash=str(message.get("hash") or "") or None,
            raw=message,
        )
        with self._lock:
            self._books[token_id] = book
            self._snapshot_token_ids.add(token_id)
        return {token_id}

    def _apply_price_change(self, message: dict[str, Any]) -> set[str]:
        updated: set[str] = set()
        price_changes = message.get("price_changes")
        if not isinstance(price_changes, list):
            return updated
        with self._lock:
            for change in price_changes:
                if not isinstance(change, dict):
                    continue
                token_id = str(change.get("asset_id") or "")
                if not token_id:
                    continue
                if token_id not in self._snapshot_token_ids:
                    continue
                side = str(change.get("side") or "").upper()
                if side not in {"BUY", "SELL"}:
                    continue
                price = valid_orderbook_price(change.get("price"))
                size = valid_level_size(change.get("size"), allow_zero=True)
                if price is None or size is None:
                    continue
                current = self._books.get(token_id) or OrderBook(token_id, [], [], market=str(message.get("market") or "") or None)
                bids, asks = current.bids, current.asks
                if side == "BUY":
                    bids = _set_level(bids, price, size, reverse=True)
                elif side == "SELL":
                    asks = _set_level(asks, price, size, reverse=False)
                self._books[token_id] = OrderBook(
                    token_id=token_id,
                    bids=bids,
                    asks=asks,
                    market=current.market or str(message.get("market") or "") or None,
                    timestamp=str(message.get("timestamp") or "") or current.timestamp,
                    min_order_size=current.min_order_size,
                    tick_size=current.tick_size,
                    neg_risk=current.neg_risk,
                    book_hash=str(change.get("hash") or "") or current.book_hash,
                    last_trade_price=current.last_trade_price,
                    raw=message,
                    indicative_best_bid=None if side == "BUY" else current.indicative_best_bid,
                    indicative_best_ask=None if side == "SELL" else current.indicative_best_ask,
                )
                updated.add(token_id)
        return updated

    def _apply_best_bid_ask(self, message: dict[str, Any]) -> set[str]:
        token_id = str(message.get("asset_id") or "")
        if not token_id:
            return set()
        bid = valid_orderbook_price(message.get("best_bid"))
        ask = valid_orderbook_price(message.get("best_ask"))
        if bid is None and ask is None:
            return set()
        with self._lock:
            current = self._books.get(token_id) or OrderBook(token_id, [], [], market=str(message.get("market") or "") or None)
            self._books[token_id] = OrderBook(
                token_id=token_id,
                bids=current.bids,
                asks=current.asks,
                market=current.market or str(message.get("market") or "") or None,
                timestamp=str(message.get("timestamp") or "") or current.timestamp,
                min_order_size=current.min_order_size,
                tick_size=current.tick_size,
                neg_risk=current.neg_risk,
                book_hash=current.book_hash,
                last_trade_price=current.last_trade_price,
                raw=message,
                indicative_best_bid=bid if bid is not None else current.indicative_best_bid,
                indicative_best_ask=ask if ask is not None else current.indicative_best_ask,
            )
        return {token_id}

    def _apply_last_trade_price(self, message: dict[str, Any]) -> set[str]:
        token_id = str(message.get("asset_id") or "")
        if not token_id:
            return set()
        last_trade_price = valid_orderbook_price(message.get("price"))
        if last_trade_price is None:
            return set()
        with self._lock:
            current = self._books.get(token_id) or OrderBook(token_id, [], [], market=str(message.get("market") or "") or None)
            self._books[token_id] = OrderBook(
                token_id=token_id,
                bids=current.bids,
                asks=current.asks,
                market=current.market or str(message.get("market") or "") or None,
                timestamp=str(message.get("timestamp") or "") or current.timestamp,
                min_order_size=current.min_order_size,
                tick_size=current.tick_size,
                neg_risk=current.neg_risk,
                book_hash=current.book_hash,
                last_trade_price=last_trade_price,
                raw=message,
                indicative_best_bid=current.indicative_best_bid,
                indicative_best_ask=current.indicative_best_ask,
            )
        return {token_id}

    def _apply_tick_size(self, message: dict[str, Any]) -> set[str]:
        token_id = str(message.get("asset_id") or "")
        if not token_id:
            return set()
        tick_size = finite_float(message.get("new_tick_size"))
        if tick_size is None or tick_size <= 0.0:
            return set()
        with self._lock:
            current = self._books.get(token_id)
            if current is None:
                return set()
            self._books[token_id] = OrderBook(
                token_id=current.token_id,
                bids=current.bids,
                asks=current.asks,
                market=current.market,
                timestamp=str(message.get("timestamp") or "") or current.timestamp,
                min_order_size=current.min_order_size,
                tick_size=tick_size,
                neg_risk=current.neg_risk,
                book_hash=current.book_hash,
                last_trade_price=current.last_trade_price,
                raw=message,
                indicative_best_bid=current.indicative_best_bid,
                indicative_best_ask=current.indicative_best_ask,
            )
        return {token_id}


class OrderBookMarketStream:
    def __init__(
        self,
        url: str = MARKET_STREAM_URL,
        *,
        on_update: Callable[[set[str]], None] | None = None,
        heartbeat_seconds: int = 10,
        reconnect_seconds: int = 2,
        stale_seconds: int = 60,
    ) -> None:
        self.url = url
        self.cache = OrderBookStreamCache()
        self.on_update = on_update
        self.heartbeat_seconds = heartbeat_seconds
        self.reconnect_seconds = reconnect_seconds
        self.stale_seconds = max(1, int(stale_seconds))
        self._asset_ids: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws: Any = None
        self._health_lock = threading.Lock()
        self._started_at: datetime | None = None
        self._last_message_at: datetime | None = None
        self._last_book_at: datetime | None = None
        self._last_book_at_by_token: dict[str, datetime] = {}
        self._reconnect_count = 0
        self._last_error = ""

    def get_order_book(self, token_id: str) -> OrderBook:
        return self.cache.get_order_book(token_id)

    def start(self, asset_ids: Iterable[str]) -> None:
        self._asset_ids = [str(asset_id) for asset_id in asset_ids if str(asset_id)]
        if not self._asset_ids:
            return
        if self._thread and self._thread.is_alive():
            return
        try:
            _import_websocket_client()
        except RuntimeError as exc:
            cause = exc.__cause__ if isinstance(exc.__cause__, BaseException) else exc
            with self._health_lock:
                self._last_error = _websocket_client_import_error(cause)
            raise
        self._stop.clear()
        with self._health_lock:
            self._started_at = _utc_now()
        self._thread = threading.Thread(target=self._run_forever, name="polymarket-orderbook-ws", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)

    def apply_message(self, message: str | dict[str, Any] | list[dict[str, Any]]) -> set[str]:
        now = _utc_now()
        with self._health_lock:
            self._last_message_at = now
        updated = self.cache.apply_message(message)
        executable_updated = updated.intersection(_executable_orderbook_update_token_ids(message))
        if executable_updated:
            with self._health_lock:
                self._last_book_at = now
                for token_id in executable_updated:
                    self._last_book_at_by_token[token_id] = now
        if executable_updated and self.on_update is not None:
            self.on_update(executable_updated)
        return updated

    def _record_reconnect(self, exc: BaseException | None = None) -> None:
        with self._health_lock:
            self._reconnect_count += 1
            if exc is not None:
                self._last_error = _safe_error(exc)

    def health_snapshot(self, *, now: datetime | None = None) -> dict[str, object]:
        current_time = now or _utc_now()
        thread_alive = bool(self._thread and self._thread.is_alive())
        with self._health_lock:
            started_at = self._started_at
            last_message_at = self._last_message_at
            last_book_at = self._last_book_at
            last_book_at_by_token = dict(self._last_book_at_by_token)
            reconnect_count = self._reconnect_count
            last_error = self._last_error

        stale_book_age_seconds: int | None = None
        if last_book_at is not None:
            stale_book_age_seconds = max(0, int((current_time - last_book_at).total_seconds()))
        last_message_age_seconds: int | None = None
        if last_message_at is not None:
            last_message_age_seconds = max(0, int((current_time - last_message_at).total_seconds()))
        seconds_since_start: int | None = None
        if started_at is not None:
            seconds_since_start = max(0, int((current_time - started_at).total_seconds()))
        waiting_too_long = bool(
            last_book_at is None
            and started_at is not None
            and (current_time - started_at).total_seconds() > self.stale_seconds
        )
        stale = (
            not thread_alive
            or waiting_too_long
            or (stale_book_age_seconds is not None and stale_book_age_seconds > self.stale_seconds)
        )
        if not thread_alive:
            status_reason = "websocket receiver thread is not running"
        elif waiting_too_long:
            status_reason = (
                f"no executable order book depth received for {seconds_since_start}s "
                f"after stream start; threshold={self.stale_seconds}s"
            )
        elif stale_book_age_seconds is not None and stale_book_age_seconds > self.stale_seconds:
            status_reason = (
                f"last executable order book depth age {stale_book_age_seconds}s "
                f"exceeds {self.stale_seconds}s"
            )
        elif last_book_at is None:
            status_reason = "waiting for executable order book depth"
        else:
            status_reason = f"executable order book depth fresh; age={stale_book_age_seconds}s"
        if reconnect_count:
            status_reason = f"{status_reason}; reconnects={reconnect_count}"
        if last_error and stale:
            status_reason = f"{status_reason}; last_error={last_error}"
        return {
            "thread_alive": thread_alive,
            "reconnect_count": reconnect_count,
            "last_message_at": _utc_iso(last_message_at) if last_message_at else None,
            "last_message_age_seconds": last_message_age_seconds,
            "last_book_at": _utc_iso(last_book_at) if last_book_at else None,
            "last_book_at_by_token": {
                token_id: _utc_iso(last_seen_at)
                for token_id, last_seen_at in sorted(last_book_at_by_token.items())
            },
            "stale_book_age_seconds": stale_book_age_seconds,
            "stale": stale,
            "last_error": last_error,
            "status_reason": status_reason,
        }

    def token_health_snapshot(self, token_id: str, *, now: datetime | None = None) -> dict[str, object]:
        token = str(token_id)
        current_time = now or _utc_now()
        thread_alive = bool(self._thread and self._thread.is_alive())
        with self._health_lock:
            started_at = self._started_at
            last_message_at = self._last_message_at
            last_book_at = self._last_book_at_by_token.get(token)
            reconnect_count = self._reconnect_count
            last_error = self._last_error

        stale_book_age_seconds: int | None = None
        if last_book_at is not None:
            stale_book_age_seconds = max(0, int((current_time - last_book_at).total_seconds()))
        last_message_age_seconds: int | None = None
        if last_message_at is not None:
            last_message_age_seconds = max(0, int((current_time - last_message_at).total_seconds()))
        seconds_since_start: int | None = None
        if started_at is not None:
            seconds_since_start = max(0, int((current_time - started_at).total_seconds()))
        waiting_too_long = bool(
            last_book_at is None
            and started_at is not None
            and (current_time - started_at).total_seconds() > self.stale_seconds
        )
        stale = (
            not thread_alive
            or waiting_too_long
            or (stale_book_age_seconds is not None and stale_book_age_seconds > self.stale_seconds)
        )
        if not thread_alive:
            status_reason = "websocket receiver thread is not running"
        elif waiting_too_long:
            status_reason = (
                f"token {token} has no executable order book depth for {seconds_since_start}s "
                f"after stream start; threshold={self.stale_seconds}s"
            )
        elif stale_book_age_seconds is not None and stale_book_age_seconds > self.stale_seconds:
            status_reason = (
                f"token {token} executable order book depth age {stale_book_age_seconds}s "
                f"exceeds {self.stale_seconds}s"
            )
        elif last_book_at is None:
            status_reason = f"token {token} waiting for executable order book depth"
        else:
            status_reason = f"token {token} executable order book depth fresh; age={stale_book_age_seconds}s"
        if reconnect_count:
            status_reason = f"{status_reason}; reconnects={reconnect_count}"
        if last_error and stale:
            status_reason = f"{status_reason}; last_error={last_error}"
        return {
            "token_id": token,
            "thread_alive": thread_alive,
            "reconnect_count": reconnect_count,
            "last_message_at": _utc_iso(last_message_at) if last_message_at else None,
            "last_message_age_seconds": last_message_age_seconds,
            "last_book_at": _utc_iso(last_book_at) if last_book_at else None,
            "stale_book_age_seconds": stale_book_age_seconds,
            "stale": stale,
            "last_error": last_error,
            "status_reason": status_reason,
        }

    def _run_forever(self) -> None:
        try:
            websocket = _import_websocket_client()
        except RuntimeError as exc:
            cause = exc.__cause__ if isinstance(exc.__cause__, BaseException) else exc
            with self._health_lock:
                self._last_error = _websocket_client_import_error(cause)
            raise

        while not self._stop.is_set():
            try:
                app = websocket.WebSocketApp(
                    self.url,
                    on_open=self._on_open,
                    on_message=lambda _ws, message: self.apply_message(message),
                    on_error=lambda _ws, error: self._on_error(error),
                )
                self._ws = app
                app.run_forever()
                if not self._stop.is_set():
                    self._record_reconnect(RuntimeError("websocket connection closed"))
            except Exception as exc:  # pragma: no cover - depends on remote network behavior
                self._record_reconnect(exc)
            if not self._stop.is_set():
                time.sleep(max(0, self.reconnect_seconds))

    def _on_open(self, ws: Any) -> None:
        with self._health_lock:
            self._last_error = ""
        ws.send(json.dumps(market_subscription_message(self._asset_ids)))

        def heartbeat() -> None:
            while not self._stop.is_set():
                time.sleep(max(1, self.heartbeat_seconds))
                try:
                    ws.send("PING")
                except Exception:
                    return

        threading.Thread(target=heartbeat, name="polymarket-orderbook-ping", daemon=True).start()

    def _on_error(self, error: object) -> None:
        with self._health_lock:
            self._last_error = str(error)[:240]
