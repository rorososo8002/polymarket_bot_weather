from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class _HeldPosition:
    market_id: str
    token_id: str
    city: str
    question: str


@dataclass
class _TokenBlock:
    count: int = 0
    latest_ts: str = ""
    latest_age_seconds: int | None = None
    latest_reason: str = ""
    city: str = ""
    market_id: str = ""
    token_id: str = ""


def _read_json(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        warnings.append(f"missing {path.name}")
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"cannot read {path.name}: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _iter_recent_csv_rows(path: Path, tail: int, warnings: list[str]) -> Iterable[dict[str, str]]:
    if not path.exists():
        warnings.append(f"missing {path.name}")
        return []
    rows: deque[dict[str, str]] = deque(maxlen=max(1, tail))
    try:
        with path.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows.append(row)
    except OSError as exc:
        warnings.append(f"cannot read {path.name}: {exc}")
        return []
    return list(rows)


def _iter_recent_jsonl_rows(
    path: Path,
    tail: int,
    warnings: list[str],
    *,
    optional: bool = False,
) -> Iterable[dict[str, Any]]:
    if not path.exists():
        if not optional:
            warnings.append(f"missing {path.name}")
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=max(1, tail))
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    warnings.append(f"cannot parse {path.name} line {line_number}: {exc}")
                    continue
                if isinstance(data, dict):
                    rows.append(data)
    except OSError as exc:
        warnings.append(f"cannot read {path.name}: {exc}")
        return []
    return list(rows)


def _money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return f"${number:.2f}"


def _seconds(value: Any) -> str:
    if value in (None, ""):
        return "NA"
    return f"{value}s"


def _bool(value: Any) -> bool:
    return bool(value)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _held_positions_by_token(state: dict[str, Any]) -> dict[str, _HeldPosition]:
    positions: dict[str, _HeldPosition] = {}
    for item in state.get("positions") or []:
        if not isinstance(item, dict):
            continue
        token_id = str(item.get("token_id") or "")
        if not token_id:
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        positions[token_id] = _HeldPosition(
            market_id=str(item.get("market_id") or ""),
            token_id=token_id,
            city=str(metadata.get("city") or "").strip().lower(),
            question=str(item.get("question") or ""),
        )
    return positions


def _row_city(row: dict[str, str], held: _HeldPosition | None) -> str:
    return (row.get("city") or "").strip().lower() or (held.city if held else "") or "unknown"


def _row_market_id(row: dict[str, str], held: _HeldPosition | None) -> str:
    return (row.get("market_id") or "").strip() or (held.market_id if held else "") or "unknown"


def _age_seconds(reason: str) -> int | None:
    match = re.search(r"depth age\s+(\d+)s\s+exceeds", reason)
    return int(match.group(1)) if match else None


def _is_token_stale_block(row: dict[str, str]) -> bool:
    reason = (row.get("reason") or "").lower()
    token_id = (row.get("token_id") or "").strip()
    return bool(token_id and " token " in reason and "executable order book depth age" in reason)


def _is_whole_stream_block(row: dict[str, str]) -> bool:
    reason = (row.get("reason") or "").lower()
    if _is_token_stale_block(row):
        return False
    return (
        "websocket receiver thread is not running" in reason
        or "websocket executable order book depth is stale" in reason
        or "websocket connection closed" in reason
    )


def _websocket_line(status: dict[str, Any]) -> str:
    websocket = status.get("websocket") if isinstance(status.get("websocket"), dict) else {}
    thread_alive = _bool(websocket.get("thread_alive"))
    stale = _bool(websocket.get("stale"))
    health = "fresh" if thread_alive and not stale else "unhealthy"
    return (
        f"websocket: {health} "
        f"thread_alive={thread_alive} "
        f"stale={stale} "
        f"last_message_age={_seconds(websocket.get('last_message_age_seconds'))} "
        f"stale_book_age={_seconds(websocket.get('stale_book_age_seconds'))} "
        f"reconnects={websocket.get('reconnect_count', 'NA')}"
    )


def _portfolio_rejection_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["", "portfolio_rejections:"]
    if not rows:
        lines.append("- none")
        return lines

    zero_selection_rows = [row for row in rows if _int(row.get("selected_count")) == 0]
    selected_rows = [row for row in rows if _int(row.get("selected_count")) > 0]
    rejected_reasons: Counter[str] = Counter()
    for row in zero_selection_rows:
        counts = row.get("rejected_reason_counts")
        if not isinstance(counts, dict):
            continue
        for reason, count in counts.items():
            rejected_reasons[str(reason)] += _int(count)

    lines.append(
        f"- inspected_rows={len(rows)} "
        f"zero_selection_rows={len(zero_selection_rows)} "
        f"selected_rows={len(selected_rows)}"
    )
    if rejected_reasons:
        lines.extend(f"- {reason}: {count}" for reason, count in rejected_reasons.most_common())
    else:
        lines.append("- zero-selection rows had no rejected_reason_counts")

    if zero_selection_rows:
        latest = zero_selection_rows[-1]
        lines.append(
            "latest_zero_selection: "
            f"event_key={latest.get('event_key', 'unknown')} "
            f"city={latest.get('city', 'unknown')} "
            f"date_hint={latest.get('date_hint', 'unknown')} "
            f"rejected_count={_int(latest.get('rejected_count'))} "
            f"entry_bankroll_usable={_bool(latest.get('entry_bankroll_usable'))}"
        )
    return lines


def build_runtime_report(data_dir: Path | str, tail: int = 500) -> str:
    data_path = Path(data_dir)
    warnings: list[str] = []
    status = _read_json(data_path / "paper_runner_status.json", warnings)
    state = _read_json(data_path / "paper_state.json", warnings)
    recent_trades = list(_iter_recent_csv_rows(data_path / "paper_trades.csv", tail, warnings))
    recent_portfolios = list(
        _iter_recent_jsonl_rows(
            data_path / "paper_event_portfolios.jsonl",
            tail,
            warnings,
            optional=True,
        )
    )
    held_by_token = _held_positions_by_token(state)

    action_counts = Counter((row.get("action") or "").strip() or "UNKNOWN" for row in recent_trades)
    reason_counts = Counter((row.get("reason_code") or row.get("action") or "").strip() or "UNKNOWN" for row in recent_trades)
    whole_stream_blocks = 0
    token_blocks: dict[str, _TokenBlock] = {}
    for row in recent_trades:
        action = (row.get("action") or "").strip().upper()
        if action != "HOLD_STREAM_UNHEALTHY":
            continue
        if _is_whole_stream_block(row):
            whole_stream_blocks += 1
            continue
        if not _is_token_stale_block(row):
            continue
        token_id = (row.get("token_id") or "").strip()
        held = held_by_token.get(token_id)
        block = token_blocks.setdefault(token_id, _TokenBlock(token_id=token_id))
        block.count += 1
        block.latest_ts = row.get("ts") or block.latest_ts
        block.latest_age_seconds = _age_seconds(row.get("reason") or "")
        block.latest_reason = row.get("reason") or ""
        block.city = _row_city(row, held)
        block.market_id = _row_market_id(row, held)

    token_stale_blocks = sum(block.count for block in token_blocks.values())
    last_trade_ts = recent_trades[-1].get("ts") if recent_trades else "NA"
    lines = [
        (
            f"runtime_status: phase={status.get('phase', 'unknown')} "
            f"open_positions={status.get('open_positions', len(state.get('positions') or []))} "
            f"exposure={_money(status.get('exposure_usd'))}"
        ),
        f"updated_at={status.get('updated_at', 'NA')} last_trade_ts={last_trade_ts} tail_rows={len(recent_trades)}",
        _websocket_line(status),
        (
            "realtime_evaluator: "
            f"thread_alive={_bool((status.get('realtime_evaluator') or {}).get('thread_alive'))} "
            f"queue_depth={(status.get('realtime_evaluator') or {}).get('queue_depth', 'NA')} "
            f"errors={(status.get('realtime_evaluator') or {}).get('error_count', 'NA')}"
        ),
        "",
        "recent_actions:",
    ]
    if action_counts:
        lines.extend(f"- {action}: {count}" for action, count in action_counts.most_common())
    else:
        lines.append("- none")

    lines.extend(["", "recent_reason_codes:"])
    if reason_counts:
        lines.extend(f"- {reason}: {count}" for reason, count in reason_counts.most_common())
    else:
        lines.append("- none")

    lines.extend([
        "",
        "websocket_blocks:",
        f"- whole_stream_blocks={whole_stream_blocks}",
        f"- token_stale_blocks={token_stale_blocks}",
    ])
    if token_blocks:
        lines.append("token_stale_blocks:")
        for block in sorted(token_blocks.values(), key=lambda item: (-item.count, item.city, item.market_id, item.token_id)):
            age = f"{block.latest_age_seconds}s" if block.latest_age_seconds is not None else "NA"
            lines.append(
                f"- {block.city} market_id={block.market_id} token={block.token_id} "
                f"count={block.count} latest_age={age}"
            )
    else:
        lines.append("token_stale_blocks: none")

    lines.extend(_portfolio_rejection_lines(recent_portfolios))

    websocket = status.get("websocket") if isinstance(status.get("websocket"), dict) else {}
    if _bool(websocket.get("thread_alive")) and not _bool(websocket.get("stale")) and token_stale_blocks:
        recommendation = "investigate held-token liquidity/subscription before loosening global stale limits"
    elif whole_stream_blocks:
        recommendation = "investigate whole-stream WebSocket reconnects before changing strategy thresholds"
    elif not warnings:
        recommendation = "no immediate runtime blocker in the inspected tail"
    else:
        recommendation = "fix missing or unreadable runtime evidence before interpreting the report"
    lines.extend(["", f"recommendation: {recommendation}", "", "warnings:"])
    lines.extend(f"- {warning}" for warning in warnings) if warnings else lines.append("- none")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize paper runtime WebSocket and held-token freshness blockers.")
    parser.add_argument("--data-dir", default="data", help="Directory containing paper runtime files.")
    parser.add_argument("--tail", type=int, default=500, help="Number of recent trade rows to inspect.")
    args = parser.parse_args()
    print(build_runtime_report(Path(args.data_dir), tail=args.tail))


if __name__ == "__main__":
    main()
