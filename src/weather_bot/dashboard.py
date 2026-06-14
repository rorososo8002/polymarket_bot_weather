from __future__ import annotations

import csv
import json
import re
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .config import Settings, load_settings
from .dashboard_template import HTML
from .edge import polymarket_taker_fee_usdc
from .runner_status import read_runner_status
from .weather_client import parse_weather_question


_DECISION_TOTALS_LOCK = threading.Lock()
_DECISION_TOTALS_CACHE: dict[str, dict[str, Any]] = {}
_TRADE_TOTALS_LOCK = threading.Lock()
_TRADE_TOTALS_CACHE: dict[str, dict[str, Any]] = {}
TRADE_ACTIVITY_ACTIONS = {"OPEN", "ADD", "CLOSE", "SETTLED", "PARTIAL_CLOSE"}
REALIZED_TRADE_ACTIONS = {"CLOSE", "SETTLED", "PARTIAL_CLOSE"}
TRADE_CACHE_RECENT_LIMIT = 400
MAX_INITIAL_DECISION_TOTAL_SCAN_BYTES = 128 * 1024 * 1024
DECISION_TOTALS_SCOPE_FULL = "full"
DECISION_TOTALS_SCOPE_RECENT_TAIL = "recent_tail"
DECISION_TOTALS_SCOPE_UNAVAILABLE = "unavailable"
MIN_PUBLIC_DASHBOARD_TOKEN_LENGTH = 32
LOCAL_DASHBOARD_HOSTS = {"127.0.0.1", "localhost", "::1"}
WEAK_DASHBOARD_TOKEN_VALUES = {
    "abc",
    "123456",
    "token",
}
WEAK_DASHBOARD_TOKEN_MARKERS = (
    "placeholder",
    "basic",
    "default",
    "changeme",
    "replace",
    "example",
    "sample",
    "secret",
    "password",
)
_TOKEN_QUERY_LOG_RE = re.compile(r"(?i)([?&]token=)([^&\s]*)")
_WEATHER_MARKET_SUFFIX_RE = re.compile(
    r"-(?:\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?[cf]|\d+(?:\.\d+)?(?:c|f)?or(?:higher|below))$",
    re.IGNORECASE,
)


def _is_public_dashboard_host(host: str) -> bool:
    return (host or "").strip().lower() not in LOCAL_DASHBOARD_HOSTS


def _normalized_dashboard_token(token: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (token or "").strip().lower())


def _dashboard_token_security_errors(token: str) -> list[str]:
    stripped = (token or "").strip()
    normalized = _normalized_dashboard_token(stripped)
    errors: list[str] = []
    if not stripped:
        errors.append("DASHBOARD_TOKEN is missing")
    if len(stripped) < MIN_PUBLIC_DASHBOARD_TOKEN_LENGTH:
        errors.append(
            f"DASHBOARD_TOKEN must be at least {MIN_PUBLIC_DASHBOARD_TOKEN_LENGTH} characters"
        )
    if (
        normalized in WEAK_DASHBOARD_TOKEN_VALUES
        or any(marker in normalized for marker in WEAK_DASHBOARD_TOKEN_MARKERS)
    ):
        errors.append("DASHBOARD_TOKEN looks like a placeholder or example value")
    return errors


def _is_weak_dashboard_token(token: str) -> bool:
    return bool(_dashboard_token_security_errors(token))


def _validate_dashboard_startup_security(host: str, token: str) -> None:
    if not _is_public_dashboard_host(host):
        return
    errors = _dashboard_token_security_errors(token)
    if errors:
        raise ValueError(
            f"DASHBOARD_TOKEN is too weak for public DASHBOARD_HOST={host}: "
            + "; ".join(errors)
            + ". Generate a long random token and keep it private, or use "
            "DASHBOARD_HOST=127.0.0.1 for local-only development."
        )


def _redact_dashboard_log_message(message: str) -> str:
    return _TOKEN_QUERY_LOG_RE.sub(r"\1<redacted>", message)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _read_csv(path: Path, limit: int = 500) -> list[dict[str, str]]:
    if not path.exists():
        return []
    limit = max(0, limit)
    if limit == 0:
        return []
    try:
        header = _read_csv_header(path)
        if not header:
            return []
        rows = _read_csv_tail_rows(path, limit)
        if not rows:
            return []
        reader = csv.DictReader([header, *rows])
        return [row for row in reader if any((value or "").strip() for value in row.values())][-limit:]
    except OSError:
        return []


def _read_csv_header(path: Path) -> str:
    with path.open("r", newline="", encoding="utf-8") as f:
        return f.readline().rstrip("\r\n")


def _read_csv_tail_rows(path: Path, limit: int) -> list[str]:
    # Dashboard requests must stay cheap even when runtime CSVs grow to GBs.
    # Read only the final slice instead of materializing the whole file.
    max_bytes = max(256 * 1024, min(8 * 1024 * 1024, limit * 4096))
    size = path.stat().st_size
    with path.open("rb") as f:
        start = max(0, size - max_bytes)
        f.seek(start)
        chunk = f.read()
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    if start == 0 and lines:
        lines = lines[1:]
    return lines[-limit:]


def _read_jsonl(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    if not path.exists() or limit <= 0:
        return []
    max_bytes = max(256 * 1024, min(2 * 1024 * 1024, limit * 8192))
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            start = max(0, size - max_bytes)
            f.seek(start)
            chunk = f.read()
        lines = chunk.decode("utf-8", errors="replace").splitlines()
        if start > 0 and lines:
            lines = lines[1:]
        rows: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows
    except OSError:
        return []


def _empty_decision_totals() -> dict[str, int]:
    return {"decisions": 0, "forecast_unavailable": 0, "skips": 0, "entries": 0}


def _decision_totals_result(totals: dict[str, int], *, exact: bool, scope: str) -> dict[str, Any]:
    return {
        **totals,
        "decision_totals_exact": exact,
        "decision_totals_scope": scope,
    }


def _decision_totals_from_cache(cache: dict[str, Any]) -> dict[str, Any]:
    totals = cache.get("totals") if isinstance(cache.get("totals"), dict) else _empty_decision_totals()
    scope = str(cache.get("scope") or DECISION_TOTALS_SCOPE_FULL)
    return _decision_totals_result(
        totals,
        exact=bool(cache.get("exact", scope == DECISION_TOTALS_SCOPE_FULL)),
        scope=scope,
    )


def _forecast_unavailable(row: dict[str, str]) -> bool:
    text = f"{row.get('reason') or ''} {row.get('note') or ''}".lower()
    return "forecast unavailable" in text or "no forecast" in text


def _count_decision_row(totals: dict[str, int], row: dict[str, str]) -> None:
    if not any((value or "").strip() for value in row.values()):
        return
    side = (row.get("side") or "").upper()
    totals["decisions"] += 1
    if side.startswith("SKIP"):
        totals["skips"] += 1
    elif side in {"YES", "NO"}:
        totals["entries"] += 1
    if _forecast_unavailable(row):
        totals["forecast_unavailable"] += 1


def _decision_totals_from_rows(rows: list[dict[str, str]]) -> dict[str, int]:
    totals = _empty_decision_totals()
    for row in rows:
        _count_decision_row(totals, row)
    return totals


def _split_complete_lines(data: bytes) -> tuple[bytes, bytes]:
    newline_at = data.rfind(b"\n")
    if newline_at < 0:
        return b"", data
    return data[: newline_at + 1], data[newline_at + 1 :]


def _scan_decision_totals(path: Path, stat_size: int, mtime_ns: int) -> dict[str, Any]:
    totals = _empty_decision_totals()
    fieldnames: list[str] = []
    offset = 0
    pending = b""
    try:
        with path.open("rb") as f:
            header_raw = f.readline()
            offset += len(header_raw)
            if not header_raw:
                return {
                    "totals": totals,
                    "exact": True,
                    "scope": DECISION_TOTALS_SCOPE_FULL,
                    "fieldnames": fieldnames,
                    "offset": offset,
                    "pending": pending,
                    "mtime_ns": mtime_ns,
                }
            header = header_raw.decode("utf-8", errors="replace").rstrip("\r\n")
            fieldnames = next(csv.reader([header]), [])
            for raw_line in f:
                offset += len(raw_line)
                if not raw_line.endswith(b"\n"):
                    pending = raw_line
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                for row in csv.DictReader([line], fieldnames=fieldnames):
                    _count_decision_row(totals, row)
    except (OSError, csv.Error):
        return {
            "totals": _empty_decision_totals(),
            "exact": False,
            "scope": DECISION_TOTALS_SCOPE_UNAVAILABLE,
            "fieldnames": [],
            "offset": 0,
            "pending": b"",
            "mtime_ns": 0,
        }
    return {
        "totals": totals,
        "exact": True,
        "scope": DECISION_TOTALS_SCOPE_FULL,
        "fieldnames": fieldnames,
        "offset": min(offset, stat_size),
        "pending": pending,
        "mtime_ns": mtime_ns,
    }


def _recent_decision_totals_cache(path: Path, stat_size: int, mtime_ns: int) -> dict[str, Any]:
    try:
        header = _read_csv_header(path)
        fieldnames = next(csv.reader([header]), []) if header else []
    except (OSError, csv.Error):
        fieldnames = []
    return {
        "totals": _decision_totals_from_rows(_read_csv(path, 5000)),
        "exact": False,
        "scope": DECISION_TOTALS_SCOPE_RECENT_TAIL,
        "fieldnames": fieldnames,
        "offset": stat_size,
        "pending": b"",
        "mtime_ns": mtime_ns,
    }


def _add_appended_decisions(cache: dict[str, Any], chunk: bytes) -> None:
    fieldnames = cache.get("fieldnames") or []
    if not fieldnames:
        return
    complete, pending = _split_complete_lines((cache.get("pending") or b"") + chunk)
    cache["pending"] = pending
    if not complete:
        return
    lines = complete.decode("utf-8", errors="replace").splitlines()
    if not lines:
        return
    totals = cache["totals"]
    try:
        for row in csv.DictReader(lines, fieldnames=fieldnames):
            _count_decision_row(totals, row)
    except csv.Error:
        return


def _decision_totals(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _decision_totals_result(
            _empty_decision_totals(),
            exact=True,
            scope=DECISION_TOTALS_SCOPE_FULL,
        )
    key = str(path.resolve())
    with _DECISION_TOTALS_LOCK:
        try:
            stat = path.stat()
        except OSError:
            return _decision_totals_result(
                _empty_decision_totals(),
                exact=False,
                scope=DECISION_TOTALS_SCOPE_UNAVAILABLE,
            )
        cache = _DECISION_TOTALS_CACHE.get(key)
        cache_offset = int(cache.get("offset", 0)) if cache else 0
        cache_mtime_ns = int(cache.get("mtime_ns", 0)) if cache else 0
        if cache is None or stat.st_size < cache_offset or (stat.st_size == cache_offset and stat.st_mtime_ns != cache_mtime_ns):
            if cache is None and stat.st_size > MAX_INITIAL_DECISION_TOTAL_SCAN_BYTES:
                cache = _recent_decision_totals_cache(path, stat.st_size, stat.st_mtime_ns)
            else:
                cache = _scan_decision_totals(path, stat.st_size, stat.st_mtime_ns)
            _DECISION_TOTALS_CACHE[key] = cache
            return _decision_totals_from_cache(cache)
        if stat.st_size > cache_offset:
            try:
                with path.open("rb") as f:
                    f.seek(cache_offset)
                    chunk = f.read(stat.st_size - cache_offset)
            except OSError:
                return _decision_totals_from_cache(cache)
            _add_appended_decisions(cache, chunk)
            cache["offset"] = stat.st_size
            cache["mtime_ns"] = stat.st_mtime_ns
        return _decision_totals_from_cache(cache)


def _empty_trade_totals() -> dict[str, float]:
    return {"opens": 0, "closes": 0, "realized_profit_usd": 0.0, "realized_loss_usd": 0.0}


def _empty_trade_cache(mtime_ns: int = 0) -> dict[str, Any]:
    return {
        "totals": _empty_trade_totals(),
        "fieldnames": [],
        "offset": 0,
        "pending": b"",
        "mtime_ns": mtime_ns,
        "recent_trades": [],
        "recent_realized_trades": [],
        "open_by_market": {},
        "realized_points": [],
        "realized_running_pnl": 0.0,
    }


def _trim_recent_cache_rows(rows: list[Any], limit: int = TRADE_CACHE_RECENT_LIMIT) -> None:
    overflow = len(rows) - limit
    if overflow > 0:
        del rows[:overflow]


def _count_trade_row(totals: dict[str, float], row: dict[str, str]) -> None:
    action = (row.get("action") or "").upper()
    if action in {"OPEN", "ADD"}:
        totals["opens"] += 1
    elif action in REALIZED_TRADE_ACTIONS:
        totals["closes"] += 1
        pnl = _float(row.get("cash_delta_or_pnl"))
        if pnl >= 0:
            totals["realized_profit_usd"] += pnl
        else:
            totals["realized_loss_usd"] += abs(pnl)


def _record_trade_cache_row(cache: dict[str, Any], row: dict[str, str]) -> None:
    stored = dict(row)
    action = (stored.get("action") or "").upper()
    market_id = stored.get("market_id") or ""
    _count_trade_row(cache["totals"], stored)
    if action in TRADE_ACTIVITY_ACTIONS:
        cache["recent_trades"].append(stored)
        _trim_recent_cache_rows(cache["recent_trades"])
    if action in {"OPEN", "ADD"} and market_id:
        cache["open_by_market"][market_id] = stored
    if action in REALIZED_TRADE_ACTIONS:
        cache["recent_realized_trades"].append(stored)
        _trim_recent_cache_rows(cache["recent_realized_trades"])
        cache["realized_running_pnl"] = _float(cache.get("realized_running_pnl")) + _float(stored.get("cash_delta_or_pnl"))
        cache["realized_points"].append(
            {"ts": stored.get("ts", ""), "realized_pnl": cache["realized_running_pnl"]}
        )
        _trim_recent_cache_rows(cache["realized_points"], 160)


def _scan_trade_totals(path: Path, stat_size: int, mtime_ns: int) -> dict[str, Any]:
    cache = _empty_trade_cache(mtime_ns)
    offset = 0
    pending = b""
    try:
        with path.open("rb") as f:
            header_raw = f.readline()
            offset += len(header_raw)
            if not header_raw:
                cache["offset"] = offset
                return cache
            header = header_raw.decode("utf-8", errors="replace").rstrip("\r\n")
            cache["fieldnames"] = next(csv.reader([header]), [])
            for raw_line in f:
                offset += len(raw_line)
                if not raw_line.endswith(b"\n"):
                    pending = raw_line
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                for row in csv.DictReader([line], fieldnames=cache["fieldnames"]):
                    _record_trade_cache_row(cache, row)
    except (OSError, csv.Error):
        return _empty_trade_cache()
    cache["offset"] = min(offset, stat_size)
    cache["pending"] = pending
    cache["mtime_ns"] = mtime_ns
    return cache


def _add_appended_trades(cache: dict[str, Any], chunk: bytes) -> None:
    fieldnames = cache.get("fieldnames") or []
    if not fieldnames:
        return
    complete, pending = _split_complete_lines((cache.get("pending") or b"") + chunk)
    cache["pending"] = pending
    if not complete:
        return
    lines = complete.decode("utf-8", errors="replace").splitlines()
    if not lines:
        return
    try:
        for row in csv.DictReader(lines, fieldnames=fieldnames):
            _record_trade_cache_row(cache, row)
    except csv.Error:
        return


def _trade_dashboard_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_trade_cache()
    key = str(path.resolve())
    with _TRADE_TOTALS_LOCK:
        try:
            stat = path.stat()
        except OSError:
            return _empty_trade_cache()
        cache = _TRADE_TOTALS_CACHE.get(key)
        cache_offset = int(cache.get("offset", 0)) if cache else 0
        cache_mtime_ns = int(cache.get("mtime_ns", 0)) if cache else 0
        if cache is None or stat.st_size < cache_offset or (stat.st_size == cache_offset and stat.st_mtime_ns != cache_mtime_ns):
            cache = _scan_trade_totals(path, stat.st_size, stat.st_mtime_ns)
            _TRADE_TOTALS_CACHE[key] = cache
        elif stat.st_size > cache_offset:
            try:
                with path.open("rb") as f:
                    f.seek(cache_offset)
                    chunk = f.read(stat.st_size - cache_offset)
            except OSError:
                pass
            else:
                _add_appended_trades(cache, chunk)
                cache["offset"] = stat.st_size
                cache["mtime_ns"] = stat.st_mtime_ns
        return {
            "totals": dict(cache.get("totals") or _empty_trade_totals()),
            "recent_trades": list(cache.get("recent_trades") or []),
            "recent_realized_trades": list(cache.get("recent_realized_trades") or []),
            "open_by_market": dict(cache.get("open_by_market") or {}),
            "realized_points": list(cache.get("realized_points") or []),
        }


def _trade_action_totals(path: Path) -> dict[str, float]:
    return dict(_trade_dashboard_cache(path)["totals"])


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(row: dict[str, Any]) -> str:
    return str(row.get("ts") or row.get("closed_at") or row.get("opened_at") or "")


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _status_interval_seconds(settings: Settings) -> int:
    return max(1, int(settings.stream_cycle_interval_seconds))


def _sorted_recent(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    def sort_key(row: dict[str, Any]) -> tuple[datetime, str]:
        timestamp = _parse_ts(row)
        parsed = _parse_datetime(timestamp)
        return (parsed or datetime.min.replace(tzinfo=timezone.utc), timestamp)

    return sorted(rows, key=sort_key, reverse=True)[:limit]


def _slug_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" in text:
        path_parts = [part for part in urlparse(text).path.split("/") if part]
        if "event" in path_parts:
            event_index = path_parts.index("event")
            if len(path_parts) > event_index + 1:
                return path_parts[event_index + 1].strip()
    return text.strip("/")


def _event_slug_from_market_slug(slug: Any) -> str:
    text = _slug_text(slug)
    if not text:
        return ""
    if "temperature" not in text or "-on-" not in text:
        return text
    return _WEATHER_MARKET_SUFFIX_RE.sub("", text)


def _polymarket_market_url(slug: Any, event_slug: Any = None) -> str:
    text = _slug_text(event_slug) or _event_slug_from_market_slug(slug)
    if not text:
        return ""
    return f"https://polymarket.com/ko/event/{quote(text, safe='')}"


def _position_payload(
    pos: dict[str, Any],
    latest_decision: dict[str, str] | None = None,
    fee_rate: float = 0.0,
    websocket_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = pos.get("metadata") if isinstance(pos.get("metadata"), dict) else {}
    latest_decision = latest_decision or {}
    websocket_health = websocket_health or {}
    entry = _float(pos.get("entry_price"))
    shares = _float(pos.get("shares"))
    cost = _float(pos.get("cost_usd"))
    mark = _float(pos.get("last_mark_price"), entry)
    exit_fee_usdc = polymarket_taker_fee_usdc(shares, mark, fee_rate)
    value = shares * mark - exit_fee_usdc
    exit_liquidity = _exit_liquidity_payload(metadata, shares, cost, mark, fee_rate)
    slug = metadata.get("slug") or latest_decision.get("slug") or ""
    event_slug = metadata.get("event_slug") or latest_decision.get("event_slug") or ""
    latest_note = latest_decision.get("note", "")
    forecast_c = _forecast_c_from_note(latest_note)
    return {
        "position_id": pos.get("position_id", ""),
        "market_id": pos.get("market_id", ""),
        "question": pos.get("question", ""),
        "slug": slug,
        "event_slug": _slug_text(event_slug) or _event_slug_from_market_slug(slug),
        "market_url": _polymarket_market_url(slug, event_slug),
        "side": pos.get("side", ""),
        "entry_price": entry,
        "mark_price": mark,
        "shares": shares,
        "cost_usd": cost,
        "exit_fee_usdc": exit_fee_usdc,
        "market_value": value,
        "unrealized_pnl": value - cost,
        "reference_mark_price": mark,
        "reference_market_value": value,
        "reference_unrealized_pnl": value - cost,
        **exit_liquidity,
        "websocket_status": str(websocket_health.get("status") or "UNKNOWN"),
        "websocket_stale": bool(websocket_health.get("stale")),
        "websocket_stale_book_age_seconds": websocket_health.get("stale_book_age_seconds"),
        "websocket_last_book_at": str(websocket_health.get("last_book_at") or ""),
        "forecast_c": forecast_c,
        "nowcast_high_c": _nowcast_c_from_note(latest_note, "observed_high_c"),
        "nowcast_low_c": _nowcast_c_from_note(latest_note, "observed_low_c"),
        "opened_at": pos.get("opened_at", ""),
        "city": metadata.get("city", ""),
        "date_hint": metadata.get("date_hint", ""),
        "target_exit_price": _float(metadata.get("last_target_exit_price"), _float(metadata.get("target_exit_price"))),
        "probability_stop_threshold": _float(metadata.get("probability_stop_threshold")),
        "reason": metadata.get("reason", ""),
        # --- extra fields for richer dashboard display ---
        "p_true": _optional_float(latest_decision.get("p_true")),
        "net_edge": _optional_float(latest_decision.get("net_edge")),
        "entry_fraction": _optional_float(metadata.get("entry_fraction")) or _optional_float(latest_decision.get("entry_fraction")),
        "entry_fee_usdc": _optional_float(metadata.get("entry_fee_usdc")),
        "market_heat_score": _optional_float(metadata.get("market_heat_score")),
        "model_fair_price": _optional_float(metadata.get("model_fair_price")),
        "market_type": metadata.get("market_type", "temperature"),
    }


def _optional_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def _metadata_float(metadata: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _optional_float(metadata.get(key))
        if value is not None:
            return value
    return None


def _safe_after_fee_value(shares: float, price: float, fee_rate: float) -> float:
    if shares <= 0 or price <= 0:
        return 0.0
    try:
        fee = polymarket_taker_fee_usdc(shares, price, fee_rate)
    except ValueError:
        return 0.0
    return shares * price - fee


def _exit_liquidity_payload(
    metadata: dict[str, Any],
    shares: float,
    cost: float,
    mark: float,
    fee_rate: float,
) -> dict[str, Any]:
    best_bid = _metadata_float(metadata, "best_bid")
    absorbable = _metadata_float(metadata, "absorbable_shares", "exit_available_shares")
    can_fully_close = _optional_bool(metadata.get("can_fully_close"))
    available = None if absorbable is None else max(0.0, min(shares, absorbable))
    full_vwap = _metadata_float(metadata, "full_exit_vwap", "exit_full_vwap")
    half_vwap = _metadata_float(metadata, "half_exit_vwap", "exit_half_vwap")
    partial_vwap = _metadata_float(metadata, "partial_exit_vwap", "exit_partial_vwap")
    if can_fully_close is True and full_vwap is None:
        full_vwap = mark
    if can_fully_close is True and half_vwap is None:
        half_vwap = full_vwap

    if can_fully_close is True:
        liquidity_status = "full"
        executable_shares = shares
    elif available is not None and available > 0:
        liquidity_status = "partial"
        executable_shares = available
    elif best_bid is None and can_fully_close is None and absorbable is None:
        liquidity_status = "unknown"
        executable_shares = 0.0
    else:
        liquidity_status = "blocked"
        executable_shares = 0.0

    if liquidity_status == "full":
        executable_price = full_vwap
    elif liquidity_status == "partial":
        executable_price = partial_vwap if partial_vwap is not None else best_bid
    else:
        executable_price = None
    bid_depth_value = _safe_after_fee_value(executable_shares, executable_price or 0.0, fee_rate)

    blocker = str(
        metadata.get("last_exit_blocker")
        or metadata.get("exit_blocker")
        or metadata.get("blocked_by")
        or ""
    )
    if not blocker:
        if liquidity_status == "blocked":
            blocker = "no_executable_bid_depth"
        elif liquidity_status == "partial":
            blocker = "partial_liquidity"

    return {
        "exit_best_bid": best_bid,
        "exit_available_shares": available,
        "exit_full_vwap": full_vwap,
        "exit_half_vwap": half_vwap,
        "exit_partial_vwap": partial_vwap,
        "exit_liquidity_status": liquidity_status,
        "exit_blocker": blocker,
        "exit_trigger": str(metadata.get("last_exit_trigger") or metadata.get("exit_trigger") or ""),
        "exit_reason": str(metadata.get("last_exit_reason") or metadata.get("exit_reason") or ""),
        "exit_slippage": _metadata_float(metadata, "exit_slippage"),
        "bid_depth_executable_shares": executable_shares,
        "bid_depth_market_value": bid_depth_value,
        "bid_depth_unrealized_pnl": bid_depth_value - cost,
    }


def _f_to_c(value_f: float) -> float:
    return (value_f - 32.0) * 5.0 / 9.0


def _round_optional(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _value_or_zero(value: float | None) -> float:
    return 0.0 if value is None else value


def _forecast_c_from_note(note: str) -> float | None:
    match = re.search(r"\bmean=([-+]?\d+(?:\.\d+)?)F\b", note)
    if not match:
        return None
    return round(_f_to_c(float(match.group(1))), 1)


def _nowcast_c_from_note(note: str, key: str) -> float | None:
    if key not in {"observed_high_c", "observed_low_c"}:
        return None
    match = re.search(rf"\b{re.escape(key)}=([-+]?\d+(?:\.\d+)?)\b", note)
    if not match:
        return None
    return round(float(match.group(1)), 3)


def _target_exit_from_reason(reason: str) -> float | None:
    match = re.search(r"\btarget_exit(?:_price)?[=:]\s*([01](?:\.\d+)?)\b", reason)
    return _optional_float(match.group(1)) if match else None


def _question_summary(question: str) -> dict[str, Any]:
    parsed = parse_weather_question(question)
    threshold_c: float | None = None
    if parsed.threshold_f is not None:
        threshold_c = parsed.threshold_original if parsed.threshold_unit == "C" and parsed.threshold_original is not None else _f_to_c(parsed.threshold_f)
    condition = ""
    if parsed.operator == ">=":
        condition = "or higher"
    elif parsed.operator == "<=":
        condition = "or lower"
    return {
        "city": parsed.city or "",
        "date_hint": parsed.date_hint or "",
        "threshold_c": _round_optional(threshold_c, 1),
        "condition_label": condition,
    }


def _latest_entry_decisions(decisions: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    for row in decisions:
        if (row.get("side") or "").upper() in {"YES", "NO"} and row.get("market_id"):
            latest[row["market_id"]] = row
    return latest


def _latest_forecast_cache_at(settings: Settings) -> str:
    path = Path(settings.forecast_cache_path) if settings.forecast_cache_path else Path(settings.state_path).with_name("forecast_cache.json")
    raw = _read_json(path)
    latest: datetime | None = None
    for entry in raw.values():
        if not isinstance(entry, dict):
            continue
        parsed = _parse_datetime(str(entry.get("created_at") or ""))
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    return latest.replace(microsecond=0).isoformat() if latest is not None else ""


def _live_age_seconds(timestamp: str, recorded_age: Any = None) -> int | None:
    parsed = _parse_datetime(timestamp)
    recorded = int(_float(recorded_age, -1))
    live = max(0, int((datetime.now(timezone.utc) - parsed).total_seconds())) if parsed is not None else -1
    age = max(recorded, live)
    return age if age >= 0 else None


def _forecast_health(settings: Settings, runner_status: dict[str, Any]) -> dict[str, Any]:
    raw = runner_status.get("forecast") if isinstance(runner_status.get("forecast"), dict) else {}
    last_success_at = str(raw.get("last_success_at") or _latest_forecast_cache_at(settings))
    cache_age_seconds = _live_age_seconds(last_success_at, raw.get("cache_age_seconds"))
    stale = bool(raw.get("stale")) or (
        cache_age_seconds is not None and cache_age_seconds > settings.forecast_cache_ttl_seconds
    )
    last_failure_reason = str(raw.get("last_failure_reason") or "")
    persistence_error = str(raw.get("persistence_error") or "")
    if last_failure_reason and not last_success_at:
        status = "FAILED"
    elif stale:
        status = "STALE"
    elif persistence_error or last_failure_reason:
        status = "DEGRADED"
    elif last_success_at:
        status = "HEALTHY"
    else:
        status = "WAITING"
    return {
        "status": status,
        "last_attempt_at": str(raw.get("last_attempt_at") or ""),
        "last_success_at": last_success_at,
        "last_failure_reason": last_failure_reason,
        "cache_age_seconds": cache_age_seconds,
        "cache_ttl_seconds": settings.forecast_cache_ttl_seconds,
        "stale": stale,
        "persistence_error": persistence_error,
    }


def _websocket_health(settings: Settings, runner_status: dict[str, Any]) -> dict[str, Any]:
    raw = runner_status.get("websocket") if isinstance(runner_status.get("websocket"), dict) else {}
    if not raw:
        return {
            "status": "UNKNOWN",
            "thread_alive": None,
            "reconnect_count": 0,
            "last_message_at": "",
            "last_book_at": "",
            "stale_book_age_seconds": None,
            "stale": False,
            "last_error": "",
        }
    last_book_at = str(raw.get("last_book_at") or "")
    stale_book_age_seconds = _live_age_seconds(last_book_at, raw.get("stale_book_age_seconds"))
    stale = bool(raw.get("stale")) or (
        stale_book_age_seconds is not None and stale_book_age_seconds > settings.orderbook_stream_stale_seconds
    )
    thread_alive = bool(raw.get("thread_alive"))
    last_error = str(raw.get("last_error") or "")
    if not thread_alive:
        status = "FAILED"
    elif stale:
        status = "STALE"
    elif last_error:
        status = "DEGRADED"
    else:
        status = "HEALTHY"
    return {
        "status": status,
        "thread_alive": thread_alive,
        "reconnect_count": int(_float(raw.get("reconnect_count"))),
        "last_message_at": str(raw.get("last_message_at") or ""),
        "last_book_at": last_book_at,
        "stale_book_age_seconds": stale_book_age_seconds,
        "stale": stale,
        "last_error": last_error,
    }


def _realized_results(
    trades: list[dict[str, str]],
    decisions: list[dict[str, str]],
    limit: int = 80,
    open_by_market: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    opened_by_market: dict[str, dict[str, str]] = dict(open_by_market or {})
    decision_by_market = _latest_entry_decisions(decisions)
    rows: list[dict[str, Any]] = []
    for trade in trades:
        action = (trade.get("action") or "").upper()
        market_id = trade.get("market_id") or ""
        if action == "OPEN" and market_id:
            opened_by_market[market_id] = trade
            continue
        if action not in REALIZED_TRADE_ACTIONS:
            continue
        opened = opened_by_market.get(market_id, {})
        decision = decision_by_market.get(market_id, {})
        question = trade.get("question") or opened.get("question") or decision.get("question") or ""
        summary = _question_summary(question)
        exit_price = _optional_float(trade.get("price"))
        entry_price = _optional_float(opened.get("price")) or _optional_float(decision.get("p_exec")) or exit_price
        pnl = _float(trade.get("cash_delta_or_pnl"))
        shares = _optional_float(trade.get("shares")) or _optional_float(opened.get("shares")) or 0.0
        entry_cost = abs(_float(opened.get("cash_delta_or_pnl"))) if opened else 0.0
        if entry_cost <= 0 and entry_price is not None and shares > 0:
            entry_cost = entry_price * shares
        target_exit = _optional_float(decision.get("target_exit_price")) or _target_exit_from_reason(opened.get("reason", "")) or exit_price or entry_price
        forecast_c = _forecast_c_from_note(decision.get("note", ""))
        if forecast_c is None:
            forecast_c = summary["threshold_c"]
        rows.append(
            {
                "closed_at": trade.get("ts", ""),
                "market_id": market_id,
                "question": question,
                "side": trade.get("side", ""),
                "action": action,
                "city": summary["city"],
                "date_hint": summary["date_hint"],
                "forecast_c": round(_value_or_zero(forecast_c), 1),
                "threshold_c": round(_value_or_zero(summary["threshold_c"]), 1),
                "condition_label": summary["condition_label"],
                "expected_exit_price": round(_value_or_zero(target_exit), 4),
                "entry_price": round(_value_or_zero(entry_price), 4),
                "exit_price": round(_value_or_zero(exit_price), 4),
                "pnl": round(pnl, 4),
                "roi": round(pnl / entry_cost, 6) if entry_cost > 0 else 0.0,
                "reason": trade.get("reason", ""),
                "p_true": _optional_float(decision.get("p_true")),
                "net_edge": _optional_float(decision.get("net_edge")),
            }
        )
    return _sorted_recent(rows, limit)


def _stats_summary(state: dict[str, Any], trades: list[dict[str, str]]) -> tuple[int, int, float]:
    stats = state.get("stats") if isinstance(state.get("stats"), dict) else {}
    wins = losses = 0
    stats_pnl = 0.0
    for item in stats.values():
        if not isinstance(item, dict):
            continue
        wins += int(_float(item.get("wins")))
        losses += int(_float(item.get("losses")))
        stats_pnl += _float(item.get("pnl"))
    if wins or losses:
        return wins, losses, stats_pnl
    for row in trades:
        action = row.get("action", "")
        if action not in {"CLOSE", "SETTLED", "PARTIAL_CLOSE"}:
            continue
        pnl = _float(row.get("cash_delta_or_pnl"))
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        stats_pnl += pnl
    return wins, losses, stats_pnl


def _first_csv_data_row(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        with path.open("r", newline="", encoding="utf-8") as f:
            header = f.readline().rstrip("\r\n")
            first = f.readline().rstrip("\r\n")
        if not header or not first:
            return {}
        rows = list(csv.DictReader([header, first]))
        return rows[0] if rows else {}
    except (OSError, csv.Error):
        return {}


def _started_at(trades_path: Path, trades: list[dict[str, str]], decisions: list[dict[str, str]], positions: list[dict[str, Any]]) -> str:
    candidates = [_parse_ts(_first_csv_data_row(trades_path))]
    candidates.extend(_parse_ts(row) for row in trades)
    candidates.extend(_parse_ts(row) for row in decisions)
    candidates.extend(str(pos.get("opened_at") or "") for pos in positions)
    parsed = [dt for dt in (_parse_datetime(ts) for ts in candidates) if dt is not None]
    return min(parsed).replace(microsecond=0).isoformat() if parsed else _now_iso()


def _equity_points(
    settings: Settings,
    trades: list[dict[str, str]],
    current_curve_value: float,
    started_at: str,
    realized_points: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = [{"ts": started_at, "equity": settings.bankroll_usd}]
    if realized_points is not None:
        for point in realized_points:
            points.append({
                "ts": str(point.get("ts") or ""),
                "equity": settings.bankroll_usd + _float(point.get("realized_pnl")),
            })
        points.append({"ts": _now_iso(), "equity": current_curve_value})
        return points[-160:]
    realized = 0.0
    for row in trades:
        if row.get("action") in REALIZED_TRADE_ACTIONS:
            realized += _float(row.get("cash_delta_or_pnl"))
            points.append({"ts": row.get("ts", ""), "equity": settings.bankroll_usd + realized})
    points.append({"ts": _now_iso(), "equity": current_curve_value})
    return points[-160:]


def _events(trades: list[dict[str, str]], decisions: list[dict[str, str]]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for row in trades[-80:]:
        action = row.get("action", "")
        pnl = _float(row.get("cash_delta_or_pnl"))
        tone = "bad" if pnl < 0 else "good"
        if action.startswith("SKIP"):
            tone = "warn"
        events.append({
            "ts": row.get("ts", ""),
            "label": action or "TRADE",
            "detail": f"{row.get('side', '')} {row.get('question', '')} {row.get('price', '')} {row.get('reason', '')}",
            "tone": tone,
        })
    for row in decisions[-80:]:
        side = row.get("side", "")
        tone = "warn" if side.startswith("SKIP") else "good"
        events.append({
            "ts": row.get("ts", ""),
            "label": f"DECISION {side}",
            "detail": f"{row.get('question', '')} edge={row.get('net_edge', '')} {row.get('reason', '')}",
            "tone": tone,
        })
    return _sorted_recent(events, 120)


def _bot_status(
    settings: Settings,
    trades: list[dict[str, str]],
    decisions: list[dict[str, str]],
    positions: list[dict[str, Any]],
    runner_status: dict[str, Any],
    health: dict[str, Any],
) -> dict[str, Any]:
    timestamps = [_parse_ts(row) for row in trades + decisions]
    timestamps.extend(str(pos.get("opened_at") or "") for pos in positions)
    runner_updated_at = str(runner_status.get("updated_at") or "")
    timestamps.append(runner_updated_at)
    parsed = [dt for dt in (_parse_datetime(ts) for ts in timestamps) if dt is not None]
    interval = _status_interval_seconds(settings)
    orderbook_mode = "websocket" if settings.orderbook_stream_enabled else "http_poll"
    if not parsed:
        return {
            "status": "NO DATA",
            "last_event_at": "",
            "age_seconds": 0,
            "scan_interval_seconds": interval,
            "orderbook_mode": orderbook_mode,
            "next_scan_in_seconds": interval,
            "phase": "",
            "message": "",
            "markets_done": 0,
            "markets_total": 0,
        }
    last_event = max(parsed)
    now = datetime.now(timezone.utc)
    age = max(0, int((now - last_event).total_seconds()))
    phase = str(runner_status.get("phase") or "")
    if age <= interval * 1.5:
        status = "RUNNING" if phase in {"starting", "discovering", "evaluating", "closing", "streaming"} and runner_updated_at else "WAIT"
    elif age <= interval * 3:
        status = "LATE"
    else:
        status = "STALE"
    component_statuses = {
        str(health.get("forecast", {}).get("status") or ""),
        str(health.get("websocket", {}).get("status") or ""),
    }
    if "FAILED" in component_statuses:
        status = "FAILED"
    elif "STALE" in component_statuses:
        status = "STALE"
    elif "DEGRADED" in component_statuses:
        status = "DEGRADED"
    next_scan_at = _parse_datetime(str(runner_status.get("next_scan_at") or ""))
    next_scan_in = max(0, int((next_scan_at - now).total_seconds())) if next_scan_at is not None else max(0, interval - age)
    return {
        "status": status,
        "last_event_at": last_event.replace(microsecond=0).isoformat(),
        "age_seconds": age,
        "scan_interval_seconds": interval,
        "orderbook_mode": orderbook_mode,
        "next_scan_in_seconds": next_scan_in,
        "phase": phase,
        "message": str(runner_status.get("message") or ""),
        "markets_done": int(_float(runner_status.get("markets_done"))),
        "markets_total": int(_float(runner_status.get("markets_total"))),
        "next_scan_at": str(runner_status.get("next_scan_at") or ""),
    }


def _per_city_forecast_status(settings: Settings, limit: int = 300) -> list[dict[str, Any]]:
    """Latest Open-Meteo forecast call status per city (from request log)."""
    path = Path(settings.forecast_request_log_path) if settings.forecast_request_log_path else Path(settings.state_path).with_name("forecast_request_log.jsonl")
    rows = _read_jsonl(path, limit)
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        city = str(row.get("city") or "")
        if city and city not in ("", "bulk-metar"):
            latest[city] = row
    return sorted(latest.values(), key=lambda r: str(r.get("city") or ""))


def _per_city_nowcast_status(settings: Settings, limit: int = 300) -> list[dict[str, Any]]:
    """Latest METAR nowcast call status per city (from request log).

    AWC METAR fetches all 38 METAR stations in a single bulk HTTP request and
    logs it as city='bulk-metar'. Only Hong Kong uses a separate per-city API
    (HKO maxmin). This function now includes the latest bulk-metar entry so
    the dashboard can show when all 38 stations were last queried.
    """
    path_str = getattr(settings, "station_nowcast_request_log_path", "") or str(Path(settings.state_path).with_name("station_nowcast_request_log.jsonl"))
    rows = _read_jsonl(Path(path_str), limit)
    latest: dict[str, dict[str, Any]] = {}
    latest_bulk: dict[str, Any] | None = None
    for row in rows:
        city = str(row.get("city") or "")
        if city == "bulk-metar":
            latest_bulk = row  # keep the most recent bulk entry
        elif city:
            latest[city] = row
    result = sorted(latest.values(), key=lambda r: str(r.get("city") or ""))
    if latest_bulk is not None:
        result = [latest_bulk] + result  # show bulk entry first
    return result


def build_dashboard_payload(settings: Settings | None = None, auth_required: bool = False) -> dict[str, Any]:
    settings = settings or load_settings()
    state = _read_json(Path(settings.state_path))
    trades = _read_csv(Path(settings.trades_csv_path), 800)
    decisions_path = Path(settings.decisions_csv_path)
    decisions = _read_csv(decisions_path, 500)
    decision_by_market = _latest_entry_decisions(decisions)
    scanner_totals = _decision_totals(decisions_path)
    trades_path = Path(settings.trades_csv_path)
    trade_history = _trade_dashboard_cache(trades_path)
    trade_totals = trade_history["totals"]
    runner_status = read_runner_status(settings)
    event_portfolios = _read_jsonl(Path(settings.portfolio_decisions_jsonl_path), 20)
    health = {
        "forecast": _forecast_health(settings, runner_status),
        "websocket": _websocket_health(settings, runner_status),
    }
    positions = [
        _position_payload(
            p,
            decision_by_market.get(str(p.get("market_id") or "")),
            settings.weather_taker_fee_rate,
            health["websocket"],
        )
        for p in state.get("positions", [])
        if isinstance(p, dict)
    ]
    cash = _float(state.get("cash_usd"), settings.bankroll_usd)
    realized = _float(state.get("realized_pnl_usd"))
    exposure = sum(_float(p.get("cost_usd")) for p in positions)
    market_value = sum(_float(p.get("market_value")) for p in positions)
    unrealized = sum(_float(p.get("unrealized_pnl")) for p in positions)
    equity = cash + market_value
    total_pnl = realized + unrealized
    curve_value = settings.bankroll_usd + total_pnl
    wins, losses, _ = _stats_summary(state, trades)
    closed_total = wins + losses
    recent_trades = _sorted_recent(trade_history["recent_trades"], 80)
    realized_results = _realized_results(
        trade_history["recent_realized_trades"],
        decisions,
        80,
        trade_history["open_by_market"],
    )
    started_at = _started_at(trades_path, trades, decisions, positions)
    return {
        "generated_at": _now_iso(),
        "security": {"auth_required": auth_required},
        "bot": _bot_status(settings, trades, decisions, positions, runner_status, health),
        "health": health,
        "summary": {
            "initial_bankroll": settings.bankroll_usd,
            "cash": cash,
            "exposure": exposure,
            "market_value": market_value,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "realized_profit_usd": round(_float(trade_totals.get("realized_profit_usd")), 4),
            "realized_loss_usd": round(_float(trade_totals.get("realized_loss_usd")), 4),
            "total_pnl": total_pnl,
            "equity": equity,
            "started_at": started_at,
            "open_positions": len(positions),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / closed_total if closed_total else 0.0,
        },
        "positions": positions,
        "recent_trades": recent_trades,
        "realized_results": realized_results,
        "scanner": {
            "decisions": scanner_totals["decisions"],
            "forecast_unavailable": scanner_totals["forecast_unavailable"],
            "skips": scanner_totals["skips"],
            "entries": scanner_totals["entries"],
            "entry_signals": scanner_totals["entries"],
            "decision_totals_exact": scanner_totals["decision_totals_exact"],
            "decision_totals_scope": scanner_totals["decision_totals_scope"],
            "actual_opens": int(trade_totals["opens"]),
            "actual_closes": int(trade_totals["closes"]),
            "latest_forecast_at": _latest_forecast_cache_at(settings),
            "per_city_forecast": _per_city_forecast_status(settings),
            "per_city_nowcast": _per_city_nowcast_status(settings),
        },
        "equity_points": _equity_points(
            settings,
            trades,
            curve_value,
            started_at,
            trade_history["realized_points"],
        ),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "WeatherBotDashboard/0.1"

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = getattr(self.server, "dashboard_token", "")
        if not token:
            return True
        parsed = urlparse(self.path)
        header_token = self.headers.get("X-Dashboard-Token", "")
        if header_token == token:
            return True
        if not bool(getattr(self.server, "dashboard_query_token_allowed", False)):
            return False
        query_token = parse_qs(parsed.query).get("token", [""])[0]
        return query_token == token

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", HTML.encode("utf-8"))
            return
        if parsed.path == "/health":
            self._send(HTTPStatus.OK, "application/json", b'{"ok":true}')
            return
        if parsed.path == "/api/status":
            if not self._authorized():
                self._send(HTTPStatus.FORBIDDEN, "application/json", b'{"error":"forbidden"}')
                return
            payload = build_dashboard_payload(getattr(self.server, "settings"), bool(getattr(self.server, "dashboard_token", "")))
            self._send(HTTPStatus.OK, "application/json", json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            return
        self._send(HTTPStatus.NOT_FOUND, "application/json", b'{"error":"not found"}')

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {_redact_dashboard_log_message(fmt % args)}")


def run_dashboard(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    host = settings.dashboard_host
    port = settings.dashboard_port
    token = settings.dashboard_token
    _validate_dashboard_startup_security(host, token)
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    server.settings = settings  # type: ignore[attr-defined]
    server.dashboard_token = token  # type: ignore[attr-defined]
    server.dashboard_query_token_allowed = not _is_public_dashboard_host(host)  # type: ignore[attr-defined]
    token_note = "enabled" if token else "disabled"
    print(f"Weather dashboard listening on http://{host}:{port} auth={token_note}")
    server.serve_forever()


def main() -> None:
    run_dashboard(load_settings())


if __name__ == "__main__":
    main()
