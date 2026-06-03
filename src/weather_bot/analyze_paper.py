from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .config import load_settings


@dataclass
class _DecisionSummary:
    decisions_count: int = 0
    entries_count: int = 0
    skips_count: int = 0
    skip_reasons: Counter[str] = field(default_factory=Counter)
    bucket_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    bucket_p_true_sums: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    latest_entry_p_yes_by_market: dict[str, float] = field(default_factory=dict)


@dataclass
class _TradeSummary:
    trades_count: int = 0
    brier_error_sum: float = 0.0
    brier_count: int = 0


def _iter_csv_rows(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists():
        return
    with path.open("r", newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


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


def _summarize_decisions(decisions_path: Path) -> _DecisionSummary:
    summary = _DecisionSummary()
    for row in _iter_csv_rows(decisions_path):
        summary.decisions_count += 1
        side = row.get("side")
        if side == "SKIP":
            summary.skips_count += 1
            summary.skip_reasons[_skip_label(row.get("reason", ""))] += 1
            continue
        if side not in {"YES", "NO"}:
            continue

        summary.entries_count += 1
        market_id = row.get("market_id", "")
        summary.latest_entry_p_yes_by_market[market_id] = _float(row.get("p_true"), 0.5)
        bucket = _edge_bucket(_float(row.get("net_edge")))
        summary.bucket_counts[bucket] += 1
        summary.bucket_p_true_sums[bucket] += _float(row.get("p_true"), 0.5)
    return summary


def _summarize_trades(trades_path: Path, latest_entry_p_yes_by_market: dict[str, float]) -> _TradeSummary:
    summary = _TradeSummary()
    for trade in _iter_csv_rows(trades_path):
        summary.trades_count += 1
        winner = _resolved_winner(trade.get("reason", ""))
        if winner is None:
            continue
        p_yes = latest_entry_p_yes_by_market.get(trade.get("market_id", ""))
        if p_yes is None:
            continue
        outcome_yes = 1.0 if winner == "YES" else 0.0
        summary.brier_error_sum += (p_yes - outcome_yes) ** 2
        summary.brier_count += 1
    return summary


def _brier_from_summary(summary: _TradeSummary) -> tuple[float | None, int]:
    if summary.brier_count == 0:
        return None, 0
    return summary.brier_error_sum / summary.brier_count, summary.brier_count


def build_report(decisions_path: Path, trades_path: Path) -> str:
    decision_summary = _summarize_decisions(decisions_path)
    trade_summary = _summarize_trades(trades_path, decision_summary.latest_entry_p_yes_by_market)

    lines = [
        (
            f"decisions={decision_summary.decisions_count} "
            f"entries={decision_summary.entries_count} "
            f"skips={decision_summary.skips_count} "
            f"trades={trade_summary.trades_count}"
        ),
        "",
        "skip_reasons:",
    ]
    for reason, count in decision_summary.skip_reasons.most_common():
        lines.append(f"- {reason}: {count}")

    lines.extend(["", "edge_buckets:"])
    for bucket in ("edge < 5%", "edge 5-8%", "edge 8-10%", "edge >= 10%"):
        count = decision_summary.bucket_counts.get(bucket, 0)
        if count:
            avg_p_true = decision_summary.bucket_p_true_sums[bucket] / count
            lines.append(f"- {bucket}: count={count} avg_p_true={avg_p_true:.3f}")

    score, n = _brier_from_summary(trade_summary)
    lines.extend(["", "resolution_quality:"])
    lines.append("resolved_brier=NA n=0" if score is None else f"resolved_brier={score:.4f} n={n}")
    return "\n".join(lines)


def main() -> None:
    settings = load_settings()
    print(build_report(Path(settings.decisions_csv_path), Path(settings.trades_csv_path)))


if __name__ == "__main__":
    main()
