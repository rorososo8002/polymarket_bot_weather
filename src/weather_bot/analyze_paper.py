from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from .config import load_settings


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _float(value: str | None, default: float = 0.0) -> float:
    try:
        return default if value in (None, "") else float(value)
    except ValueError:
        return default


def _skip_label(reason: str) -> str:
    return (reason.split(":", 1)[0] or "unknown").strip()


def _edge_bucket(edge: float) -> str:
    if edge >= 0.10:
        return "edge >= 10%"
    if edge >= 0.08:
        return "edge 8-10%"
    if edge >= 0.05:
        return "edge 5-8%"
    return "edge < 5%"


def _resolved_winner(reason: str) -> str | None:
    match = re.search(r"resolved winner=(YES|NO)", reason, re.IGNORECASE)
    return match.group(1).upper() if match else None


def _brier(decisions: Iterable[dict[str, str]], trades: Iterable[dict[str, str]]) -> tuple[float | None, int]:
    latest_entry_by_market: dict[str, dict[str, str]] = {}
    for row in decisions:
        if row.get("side") in {"YES", "NO"}:
            latest_entry_by_market[row.get("market_id", "")] = row

    errors: list[float] = []
    for trade in trades:
        winner = _resolved_winner(trade.get("reason", ""))
        if winner is None:
            continue
        decision = latest_entry_by_market.get(trade.get("market_id", ""))
        if not decision:
            continue
        p_yes = _float(decision.get("p_true"), 0.5)
        outcome_yes = 1.0 if winner == "YES" else 0.0
        errors.append((p_yes - outcome_yes) ** 2)
    if not errors:
        return None, 0
    return sum(errors) / len(errors), len(errors)


def build_report(decisions_path: Path, trades_path: Path) -> str:
    decisions = _read_csv(decisions_path)
    trades = _read_csv(trades_path)
    entries = [row for row in decisions if row.get("side") in {"YES", "NO"}]
    skips = [row for row in decisions if row.get("side") == "SKIP"]

    lines = [
        f"decisions={len(decisions)} entries={len(entries)} skips={len(skips)} trades={len(trades)}",
        "",
        "skip_reasons:",
    ]
    for reason, count in Counter(_skip_label(row.get("reason", "")) for row in skips).most_common():
        lines.append(f"- {reason}: {count}")

    bucket_values: dict[str, list[float]] = defaultdict(list)
    for row in entries:
        bucket_values[_edge_bucket(_float(row.get("net_edge")))].append(_float(row.get("p_true"), 0.5))

    lines.extend(["", "edge_buckets:"])
    for bucket in ("edge < 5%", "edge 5-8%", "edge 8-10%", "edge >= 10%"):
        values = bucket_values.get(bucket, [])
        if values:
            lines.append(f"- {bucket}: count={len(values)} avg_p_true={sum(values) / len(values):.3f}")

    score, n = _brier(decisions, trades)
    lines.extend(["", "resolution_quality:"])
    lines.append("resolved_brier=NA n=0" if score is None else f"resolved_brier={score:.4f} n={n}")
    return "\n".join(lines)


def main() -> None:
    settings = load_settings()
    print(build_report(Path(settings.decisions_csv_path), Path(settings.trades_csv_path)))


if __name__ == "__main__":
    main()
