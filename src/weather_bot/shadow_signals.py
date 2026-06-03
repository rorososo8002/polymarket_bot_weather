from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import requests

from .config import Settings, load_settings
from .models import RawMarket
from .polymarket_client import PolymarketClient


PUBLIC_API_EVIDENCE = "observed_public_api"
PUBLIC_TRADE_SOURCE = "polymarket_public_trade"

CONDITION_ID_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


@dataclass(frozen=True)
class ShadowSignal:
    source: str
    evidence_level: str
    wallet: str
    condition_id: str
    market_id: str
    market_slug: str
    event_slug: str
    question: str
    raw_side: str
    outcome: str
    implied_side: str
    price: float | None
    size: float | None
    usdc_size: float | None
    timestamp: int
    observed_at: str
    transaction_hash: str = ""
    asset: str = ""
    later_outcome: str = ""
    notes: str = ""


@dataclass(frozen=True)
class BotDecision:
    ts: str
    market_id: str
    slug: str
    side: str
    p_true: float | None
    reason: str


@dataclass(frozen=True)
class SignalComparison:
    signal: ShadowSignal
    decision: BotDecision
    timing_relation: str
    lag_seconds: int
    side_relation: str
    signal_won: bool | None
    bot_won: bool | None


HttpGet = Callable[..., Any]


class PublicPolymarketDataClient:
    """Small public Data API client for bounded shadow research.

    This client intentionally uses unauthenticated public endpoints only. It is
    not used by the live paper runner and cannot place orders.
    """

    def __init__(
        self,
        data_base: str = "https://data-api.polymarket.com",
        timeout: float = 15.0,
        get: HttpGet | None = None,
    ) -> None:
        self.data_base = data_base.rstrip("/")
        self.timeout = timeout
        self._get = get or requests.get

    def _get_json(self, path: str, params: dict[str, str]) -> Any:
        response = self._get(f"{self.data_base}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_trades_for_market(
        self,
        condition_id: str,
        *,
        limit: int = 100,
        min_cash: float | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(0, min(int(limit), 10_000))
        params = {
            "market": condition_id,
            "limit": str(limit),
            "offset": "0",
            "takerOnly": "true",
        }
        if min_cash is not None and min_cash > 0:
            params["filterType"] = "CASH"
            params["filterAmount"] = str(float(min_cash))
        data = self._get_json("/trades", params)
        return data if isinstance(data, list) else []

    def get_user_activity(
        self,
        wallet: str,
        *,
        limit: int = 100,
        market_condition_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(0, min(int(limit), 500))
        params = {
            "user": wallet,
            "limit": str(limit),
            "offset": "0",
            "type": "TRADE",
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        }
        if market_condition_ids:
            params["market"] = ",".join(market_condition_ids)
        data = self._get_json("/activity", params)
        return data if isinstance(data, list) else []

    def get_top_holders_for_markets(
        self,
        condition_ids: Sequence[str],
        *,
        limit: int = 20,
        min_balance: int = 1,
    ) -> list[dict[str, Any]]:
        condition_ids = [cid for cid in condition_ids if _is_condition_id(cid)]
        if not condition_ids:
            return []
        limit = max(0, min(int(limit), 20))
        min_balance = max(0, min(int(min_balance), 999_999))
        params = {
            "market": ",".join(condition_ids),
            "limit": str(limit),
            "minBalance": str(min_balance),
        }
        data = self._get_json("/holders", params)
        return data if isinstance(data, list) else []


def public_trade_to_signal(
    row: dict[str, Any],
    market_lookup: dict[str, RawMarket] | None = None,
) -> ShadowSignal | None:
    condition_id = str(row.get("conditionId") or row.get("condition_id") or "")
    if not _is_condition_id(condition_id):
        return None
    raw_side = _normalize_side(row.get("side"))
    outcome = _normalize_side(row.get("outcome"))
    implied_side = _implied_binary_side(raw_side, outcome)
    if not implied_side:
        return None

    timestamp = _int_value(row.get("timestamp"))
    if timestamp is None:
        return None

    price = _float_value(row.get("price"))
    size = _float_value(row.get("size"))
    usdc_size = _float_value(row.get("usdcSize"))
    if usdc_size is None and price is not None and size is not None:
        usdc_size = round(price * size, 8)

    market = market_lookup.get(condition_id) if market_lookup else None
    slug = str(row.get("slug") or (market.slug if market else "") or "")
    question = str(row.get("title") or row.get("question") or (market.question if market else "") or "")
    event_slug = str(row.get("eventSlug") or (market.event_slug if market else "") or "")
    later_outcome = _normalize_side(row.get("later_outcome") or row.get("laterOutcome") or "")
    if not later_outcome and market is not None:
        later_outcome = later_outcome_from_gamma_market(market)

    return ShadowSignal(
        source=PUBLIC_TRADE_SOURCE,
        evidence_level=PUBLIC_API_EVIDENCE,
        wallet=str(row.get("proxyWallet") or row.get("wallet") or ""),
        condition_id=condition_id,
        market_id=str((market.market_id if market else None) or row.get("marketId") or condition_id),
        market_slug=slug,
        event_slug=event_slug,
        question=question,
        raw_side=raw_side,
        outcome=outcome,
        implied_side=implied_side,
        price=price,
        size=size,
        usdc_size=usdc_size,
        timestamp=timestamp,
        observed_at=_iso_from_timestamp(timestamp),
        transaction_hash=str(row.get("transactionHash") or row.get("transaction_hash") or ""),
        asset=str(row.get("asset") or ""),
        later_outcome=later_outcome,
    )


def collect_public_trade_signals(
    markets: Sequence[RawMarket],
    data_client: PublicPolymarketDataClient,
    *,
    max_markets: int = 100,
    trades_per_market: int = 100,
    min_trade_usdc: float = 100.0,
    max_rows: int = 1_000,
) -> list[ShadowSignal]:
    market_lookup = {
        str(market.condition_id): market
        for market in markets
        if market.condition_id and _is_condition_id(str(market.condition_id))
    }
    condition_ids = list(market_lookup.keys())[: max(0, int(max_markets))]
    signals: list[ShadowSignal] = []
    for condition_id in condition_ids:
        rows = data_client.get_trades_for_market(
            condition_id,
            limit=trades_per_market,
            min_cash=min_trade_usdc,
        )
        for row in rows:
            signal = public_trade_to_signal(row, market_lookup)
            if signal is None or signal.usdc_size is None:
                continue
            if signal.usdc_size < max(0.0, float(min_trade_usdc)):
                continue
            signals.append(signal)
    return _dedupe_and_bound(signals, max_rows=max_rows)


def discover_and_collect_shadow_signals(settings: Settings | None = None) -> list[ShadowSignal]:
    settings = settings or load_settings()
    market_client = PolymarketClient(
        settings.gamma_base,
        settings.clob_base,
    )
    markets = market_client.discover_weather_markets(
        max_pages=settings.discovery_max_pages,
        page_size=settings.discovery_page_size,
    )
    data_client = PublicPolymarketDataClient(settings.polymarket_data_base)
    return collect_public_trade_signals(
        markets,
        data_client,
        max_markets=settings.shadow_max_markets,
        trades_per_market=settings.shadow_max_trades_per_market,
        min_trade_usdc=settings.shadow_min_trade_usdc,
        max_rows=settings.shadow_max_rows,
    )


def write_bounded_shadow_jsonl(
    path: Path,
    signals: Iterable[ShadowSignal | None],
    *,
    max_rows: int = 1_000,
) -> None:
    existing = read_shadow_jsonl(path, max_rows=max_rows * 2) if path.exists() else []
    bounded = _dedupe_and_bound([*existing, *[signal for signal in signals if signal is not None]], max_rows=max_rows)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for signal in bounded:
            f.write(json.dumps(asdict(signal), ensure_ascii=False, sort_keys=True) + "\n")


def read_shadow_jsonl(path: Path, *, max_rows: int = 1_000) -> list[ShadowSignal]:
    if not path.exists():
        return []
    rows: list[ShadowSignal] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if len(rows) >= max_rows:
                break
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                rows.append(_signal_from_dict(data))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    return rows


def compare_signals_to_bot(
    signals: Iterable[ShadowSignal | None],
    decisions_path: Path,
    *,
    comparison_window_seconds: int = 86_400,
) -> list[SignalComparison]:
    signal_refs: list[tuple[ShadowSignal, datetime]] = []
    signals_by_slug: dict[str, list[int]] = defaultdict(list)
    signals_by_market_id: dict[str, list[int]] = defaultdict(list)
    for signal in signals:
        if signal is None:
            continue
        signal_dt = _parse_iso(signal.observed_at)
        if signal_dt is None:
            continue
        index = len(signal_refs)
        signal_refs.append((signal, signal_dt))
        if signal.market_slug:
            signals_by_slug[signal.market_slug].append(index)
        if signal.market_id:
            signals_by_market_id[signal.market_id].append(index)
        if signal.condition_id:
            signals_by_market_id[signal.condition_id].append(index)

    candidates: dict[int, tuple[int, BotDecision]] = {}
    for decision in _iter_bot_decisions(decisions_path):
        decision_dt = _parse_iso(decision.ts)
        if decision_dt is None:
            continue
        matching_signal_indexes: set[int] = set()
        if decision.slug:
            matching_signal_indexes.update(signals_by_slug.get(decision.slug, ()))
        if decision.market_id:
            matching_signal_indexes.update(signals_by_market_id.get(decision.market_id, ()))
        for index in matching_signal_indexes:
            _signal, signal_dt = signal_refs[index]
            lag = int((signal_dt - decision_dt).total_seconds())
            if abs(lag) > comparison_window_seconds:
                continue
            if index not in candidates or abs(lag) < abs(candidates[index][0]):
                candidates[index] = (lag, decision)

    comparisons: list[SignalComparison] = []
    for index, (signal, _signal_dt) in enumerate(signal_refs):
        candidate = candidates.get(index)
        if candidate is None:
            continue
        lag, decision = candidate
        comparisons.append(_build_comparison(signal, decision, lag))
    return comparisons


def build_shadow_report(
    signals_path: Path,
    decisions_path: Path,
    trades_path: Path | None = None,
    *,
    public_notes_path: Path | None = None,
    max_rows: int = 1_000,
    min_resolved_for_experiment: int = 20,
    comparison_window_seconds: int = 86_400,
) -> str:
    signals = read_shadow_jsonl(signals_path, max_rows=max_rows)
    if trades_path is not None:
        signals = attach_later_outcomes_from_trades(signals, trades_path)
    comparisons = compare_signals_to_bot(
        signals,
        decisions_path,
        comparison_window_seconds=comparison_window_seconds,
    )

    evidence_counts = Counter(signal.evidence_level for signal in signals)
    note_counts = _public_note_counts(public_notes_path)
    timing_counts = Counter(comparison.timing_relation for comparison in comparisons)
    side_counts = Counter(comparison.side_relation for comparison in comparisons)

    resolved = [comparison for comparison in comparisons if comparison.signal_won is not None]
    signal_wins = sum(1 for comparison in resolved if comparison.signal_won)
    matched_entries = [comparison for comparison in resolved if comparison.bot_won is not None]
    matched_signal_wins = sum(1 for comparison in matched_entries if comparison.signal_won)
    bot_wins = sum(1 for comparison in matched_entries if comparison.bot_won)
    conclusion = _experiment_conclusion(
        signal_wins=matched_signal_wins,
        signal_n=len(matched_entries),
        bot_wins=bot_wins,
        bot_n=len(matched_entries),
        min_resolved_for_experiment=min_resolved_for_experiment,
    )

    lines = [
        "# Whale And External-Signal Shadow Research Report",
        "",
        "## Scope",
        "",
        "- Automatic copy trading: prohibited",
        "- Live orders: prohibited",
        "- Private data collection: prohibited",
        "- Use only public Polymarket Data API/Gamma API rows and manually verified public posts as research input",
        "",
        "## Bounded Dataset",
        "",
        f"- signals_loaded={len(signals)}",
        f"- retention_max_rows={max_rows}",
        f"- source_path={signals_path}",
        "",
        "## Evidence Summary",
        "",
        f"- observed_public_api={evidence_counts.get(PUBLIC_API_EVIDENCE, 0)}",
        f"- public_note_evidence={note_counts.get('evidence', 0)}",
        f"- public_note_speculation={note_counts.get('speculation', 0)}",
        f"- public_note_unclassified={note_counts.get('unclassified', 0)}",
        "",
        "## Timing Vs Paper Decisions",
        "",
        f"- compared={len(comparisons)}",
        f"- external_before_bot={timing_counts.get('external_before_bot', 0)}",
        f"- external_after_bot={timing_counts.get('external_after_bot', 0)}",
        f"- same_time={timing_counts.get('same_time', 0)}",
        f"- same_side={side_counts.get('same_side', 0)}",
        f"- opposite_side={side_counts.get('opposite_side', 0)}",
        f"- bot_skip={side_counts.get('bot_skip', 0)}",
        "",
        "## Resolved Outcomes",
        "",
        f"- external_signal_wins={signal_wins}/{len(resolved)}",
        f"- matched_external_signal_wins={matched_signal_wins}/{len(matched_entries)}",
        f"- matched_bot_entry_wins={bot_wins}/{len(matched_entries)}",
        "",
        "## Conclusion",
        "",
        f"- {conclusion}",
    ]
    return "\n".join(lines)


def attach_later_outcomes_from_trades(signals: Sequence[ShadowSignal], trades_path: Path) -> list[ShadowSignal]:
    outcomes = _resolved_outcomes_from_trades(trades_path)
    updated: list[ShadowSignal] = []
    for signal in signals:
        if signal.later_outcome:
            updated.append(signal)
            continue
        winner = (
            outcomes.get(("slug", signal.market_slug))
            or outcomes.get(("market_id", signal.market_id))
            or outcomes.get(("market_id", signal.condition_id))
        )
        updated.append(replace(signal, later_outcome=winner or ""))
    return updated


def later_outcome_from_gamma_market(market: RawMarket) -> str:
    if not market.closed or not market.raw:
        return ""

    for key in ("winner", "winningOutcome", "resolvedOutcome", "resolution"):
        winner = _normalize_side(market.raw.get(key))
        if winner in {"YES", "NO"}:
            return winner

    outcomes = _json_list(market.raw.get("outcomes"))
    prices = _json_list(market.raw.get("outcomePrices"))
    if len(outcomes) != len(prices):
        return ""
    for outcome, price in zip(outcomes, prices):
        side = _normalize_side(outcome)
        if side not in {"YES", "NO"}:
            continue
        numeric_price = _float_value(price)
        if numeric_price is not None and numeric_price >= 0.99:
            return side
    return ""


def _build_comparison(signal: ShadowSignal, decision: BotDecision, lag_seconds: int) -> SignalComparison:
    if lag_seconds > 0:
        timing_relation = "external_after_bot"
    elif lag_seconds < 0:
        timing_relation = "external_before_bot"
    else:
        timing_relation = "same_time"

    bot_side = _normalize_side(decision.side)
    if bot_side not in {"YES", "NO"}:
        side_relation = "bot_skip"
    elif bot_side == signal.implied_side:
        side_relation = "same_side"
    else:
        side_relation = "opposite_side"

    winner = _normalize_side(signal.later_outcome)
    signal_won = signal.implied_side == winner if winner in {"YES", "NO"} else None
    bot_won = bot_side == winner if bot_side in {"YES", "NO"} and winner in {"YES", "NO"} else None
    return SignalComparison(
        signal=signal,
        decision=decision,
        timing_relation=timing_relation,
        lag_seconds=lag_seconds,
        side_relation=side_relation,
        signal_won=signal_won,
        bot_won=bot_won,
    )


def _experiment_conclusion(
    *,
    signal_wins: int,
    signal_n: int,
    bot_wins: int,
    bot_n: int,
    min_resolved_for_experiment: int,
) -> str:
    if signal_n != bot_n:
        return "paper-only experiment promotion: hold - external signals and bot entries must be compared on the same resolved sample."
    if signal_n < min_resolved_for_experiment:
        return (
            "paper-only experiment promotion: hold - the resolved sample is too small. "
            f"At least {min_resolved_for_experiment} rows are required, but only {signal_n} are available."
        )
    signal_rate = signal_wins / signal_n if signal_n else 0.0
    bot_rate = bot_wins / bot_n if bot_n else 0.0
    if bot_n and signal_rate >= bot_rate + 0.05:
        return (
            "paper-only experiment promotion: candidate - the research suggests this signal may improve returns. "
            "Consider testing a strategy change next. The next step must still be a paper-only A/B experiment, "
            "not automatic copy trading."
        )
    return "paper-only experiment promotion: hold - public signals do not yet show enough evidence of outperforming current paper decisions."


def _iter_bot_decisions(path: Path) -> Iterable[BotDecision]:
    for row in _iter_csv_rows(path):
        yield BotDecision(
            ts=str(row.get("ts") or ""),
            market_id=str(row.get("market_id") or ""),
            slug=str(row.get("slug") or ""),
            side=_normalize_side(row.get("side")),
            p_true=_float_value(row.get("p_true")),
            reason=str(row.get("reason") or ""),
        )


def _resolved_outcomes_from_trades(path: Path) -> dict[tuple[str, str], str]:
    outcomes: dict[tuple[str, str], str] = {}
    for row in _iter_csv_rows(path):
        winner = _resolved_winner(str(row.get("reason") or ""))
        if not winner:
            continue
        slug = str(row.get("slug") or "")
        market_id = str(row.get("market_id") or "")
        if slug:
            outcomes[("slug", slug)] = winner
        if market_id:
            outcomes[("market_id", market_id)] = winner
    return outcomes


def _resolved_winner(reason: str) -> str:
    match = re.search(r"resolved winner=(YES|NO)", reason, re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _public_note_counts(path: Path | None) -> Counter:
    counts: Counter[str] = Counter()
    if path is None or not path.exists():
        return counts
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                counts["unclassified"] += 1
                continue
            classification = str(payload.get("classification") or "").lower()
            if classification == "evidence":
                counts["evidence"] += 1
            elif classification in {"speculation", "inference", "guess"}:
                counts["speculation"] += 1
            else:
                counts["unclassified"] += 1
    return counts


def _iter_csv_rows(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def _signal_from_dict(data: dict[str, Any]) -> ShadowSignal:
    return ShadowSignal(
        source=str(data.get("source") or ""),
        evidence_level=str(data.get("evidence_level") or ""),
        wallet=str(data.get("wallet") or ""),
        condition_id=str(data.get("condition_id") or ""),
        market_id=str(data.get("market_id") or ""),
        market_slug=str(data.get("market_slug") or ""),
        event_slug=str(data.get("event_slug") or ""),
        question=str(data.get("question") or ""),
        raw_side=str(data.get("raw_side") or ""),
        outcome=str(data.get("outcome") or ""),
        implied_side=str(data.get("implied_side") or ""),
        price=_float_value(data.get("price")),
        size=_float_value(data.get("size")),
        usdc_size=_float_value(data.get("usdc_size")),
        timestamp=int(data.get("timestamp") or 0),
        observed_at=str(data.get("observed_at") or ""),
        transaction_hash=str(data.get("transaction_hash") or ""),
        asset=str(data.get("asset") or ""),
        later_outcome=_normalize_side(data.get("later_outcome") or ""),
        notes=str(data.get("notes") or ""),
    )


def _dedupe_and_bound(signals: Sequence[ShadowSignal], *, max_rows: int) -> list[ShadowSignal]:
    limit = max(0, int(max_rows))
    if limit == 0:
        return []
    seen: set[str] = set()
    deduped: list[ShadowSignal] = []
    for signal in sorted(signals, key=lambda item: (item.timestamp, item.transaction_hash), reverse=True):
        key = _signal_key(signal)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(signal)
        if len(deduped) >= limit:
            break
    return deduped


def _signal_key(signal: ShadowSignal) -> str:
    return "|".join(
        [
            signal.transaction_hash,
            signal.condition_id,
            signal.wallet,
            signal.raw_side,
            signal.outcome,
            signal.asset,
            str(signal.timestamp),
            str(signal.price),
            str(signal.size),
        ]
    )


def _is_condition_id(value: str) -> bool:
    return bool(CONDITION_ID_RE.match(value))


def _normalize_side(value: Any) -> str:
    return str(value or "").strip().upper()


def _implied_binary_side(raw_side: str, outcome: str) -> str:
    if raw_side == "BUY" and outcome in {"YES", "NO"}:
        return outcome
    if raw_side == "SELL" and outcome == "YES":
        return "NO"
    if raw_side == "SELL" and outcome == "NO":
        return "YES"
    return ""


def _float_value(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _int_value(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso_from_timestamp(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main(argv: Sequence[str] | None = None) -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Build a public shadow-signal research report.")
    parser.add_argument("--collect", action="store_true", help="Fetch bounded public trade signals before reporting.")
    parser.add_argument("--signals-path", default=settings.shadow_signals_jsonl_path)
    parser.add_argument("--decisions-path", default=settings.decisions_csv_path)
    parser.add_argument("--trades-path", default=settings.trades_csv_path)
    parser.add_argument("--public-notes-path", default=settings.shadow_public_notes_jsonl_path)
    parser.add_argument("--report-path", default=settings.shadow_report_path)
    args = parser.parse_args(argv)

    signals_path = Path(args.signals_path)
    if args.collect:
        signals = discover_and_collect_shadow_signals(settings)
        write_bounded_shadow_jsonl(signals_path, signals, max_rows=settings.shadow_max_rows)

    public_notes_path = Path(args.public_notes_path)
    report = build_shadow_report(
        signals_path,
        Path(args.decisions_path),
        Path(args.trades_path),
        public_notes_path=public_notes_path if public_notes_path.exists() else None,
        max_rows=settings.shadow_max_rows,
        comparison_window_seconds=settings.shadow_compare_window_seconds,
    )
    report_path = Path(args.report_path)
    if report_path.parent != Path("."):
        report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
