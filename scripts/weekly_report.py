#!/usr/bin/env python3
"""
weekly_report.py — 페이퍼 트레이딩 주간 성과 리포트
매주 월요일 00:00 UTC 자동 실행 (cron).
결과를 /opt/polymarket-weather-bot/data/weekly_report_YYYYMMDD.txt 에 저장.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


DATA_DIR = Path(os.getenv("DATA_DIR", "/opt/polymarket-weather-bot/data"))
TRADES_CSV = DATA_DIR / "paper_trades.csv"
STATE_JSON = DATA_DIR / "paper_state.json"
REPORT_DIR = DATA_DIR


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_trades(since: datetime | None = None) -> list[dict]:
    if not TRADES_CSV.exists():
        return []
    rows = []
    with TRADES_CSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if since:
                try:
                    ts = datetime.fromisoformat(row.get("ts", "").replace("Z", "+00:00"))
                    if ts < since:
                        continue
                except (ValueError, KeyError):
                    pass
            rows.append(row)
    return rows


def _load_state() -> dict:
    if not STATE_JSON.exists():
        return {}
    try:
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_city(question: str) -> str:
    """Extract city name from market question."""
    q = question.lower()
    for phrase in ("highest temperature in ", "lowest temperature in ", "temperature in "):
        idx = q.find(phrase)
        if idx != -1:
            rest = question[idx + len(phrase):]
            city = rest.split(" on ")[0].strip()
            return city
    return "unknown"


def _parse_bucket_type(question: str) -> str:
    q = question.lower()
    if "or below" in q or "lower" in q:
        return "lower_tail"
    if "or above" in q or "upper" in q:
        return "upper_tail"
    if "or" in q:
        return "range"
    return "exact"


def build_report(week_start: datetime, week_end: datetime) -> str:
    trades = _load_trades(since=week_start)
    state = _load_state()

    # --- 집계 ---
    closed_trades = [t for t in trades if t.get("action") in {"CLOSE", "PARTIAL_CLOSE", "SETTLED"}]
    open_trades = [t for t in trades if t.get("action") == "OPEN"]

    total_pnl = 0.0
    wins = 0
    losses = 0
    city_stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
    bucket_stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
    best_trade = {"pnl": float("-inf"), "question": ""}
    worst_trade = {"pnl": float("inf"), "question": ""}

    for t in closed_trades:
        try:
            pnl = float(t.get("realized_pnl_usd") or t.get("net_pnl_usd") or 0)
        except (ValueError, TypeError):
            pnl = 0.0

        total_pnl += pnl
        question = t.get("question", "")
        city = _parse_city(question)
        bucket = _parse_bucket_type(question)

        if pnl > 0:
            wins += 1
            city_stats[city]["wins"] += 1
            bucket_stats[bucket]["wins"] += 1
        elif pnl < 0:
            losses += 1
            city_stats[city]["losses"] += 1
            bucket_stats[bucket]["losses"] += 1

        city_stats[city]["pnl"] += pnl
        bucket_stats[bucket]["pnl"] += pnl

        if pnl > best_trade["pnl"]:
            best_trade = {"pnl": pnl, "question": question}
        if pnl < worst_trade["pnl"]:
            worst_trade = {"pnl": pnl, "question": question}

    # 현재 잔고
    cash = state.get("cash_usd", 0.0)
    open_positions = state.get("positions", [])
    open_value = sum(
        float(p.get("shares", 0)) * float(p.get("last_mark_price", 0))
        for p in open_positions
    )
    total_value = cash + open_value

    win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0.0

    # --- 리포트 작성 ---
    sep = "=" * 60
    lines = [
        sep,
        f"📊 페이퍼 트레이딩 주간 리포트",
        f"   기간: {week_start.strftime('%Y-%m-%d')} ~ {week_end.strftime('%Y-%m-%d')} (UTC)",
        f"   생성: {_utc_now().strftime('%Y-%m-%d %H:%M UTC')}",
        sep,
        "",
        "💰 수익 요약",
        f"   이번 주 실현 손익 : ${total_pnl:+.2f}",
        f"   현재 현금          : ${cash:.2f}",
        f"   미실현 평가액      : ${open_value:.2f}",
        f"   총 평가금액        : ${total_value:.2f}",
        "",
        "📊 거래 통계",
        f"   승리 / 패배        : {wins} / {losses}",
        f"   승률               : {win_rate:.1%}",
        f"   신규 진입          : {len(open_trades)} 건",
        f"   청산               : {len(closed_trades)} 건",
        f"   현재 보유 포지션   : {len(open_positions)} 개",
    ]

    if best_trade["question"]:
        lines += [
            "",
            f"🏆 최고 거래  : ${best_trade['pnl']:+.2f}  {best_trade['question'][:60]}",
            f"💀 최악 거래  : ${worst_trade['pnl']:+.2f}  {worst_trade['question'][:60]}",
        ]

    # 도시별
    if city_stats:
        lines += ["", "🏙️  도시별 성과 (손익 기준 정렬)"]
        for city, s in sorted(city_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            total_c = s["wins"] + s["losses"]
            wr_c = s["wins"] / total_c if total_c > 0 else 0.0
            lines.append(
                f"   {city:<20s}: ${s['pnl']:+.2f}  "
                f"({s['wins']}승/{s['losses']}패  {wr_c:.0%})"
            )

    # 버킷 타입별
    if bucket_stats:
        lines += ["", "🪣 버킷 타입별 성과"]
        for bucket, s in sorted(bucket_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            total_b = s["wins"] + s["losses"]
            wr_b = s["wins"] / total_b if total_b > 0 else 0.0
            lines.append(
                f"   {bucket:<15s}: ${s['pnl']:+.2f}  "
                f"({s['wins']}승/{s['losses']}패  {wr_b:.0%})"
            )

    # 현재 오픈 포지션
    if open_positions:
        lines += ["", "📌 현재 보유 포지션"]
        for p in open_positions:
            q = p.get("question", "")[:55]
            side = p.get("side", "")
            shares = float(p.get("shares", 0))
            mark = float(p.get("last_mark_price", 0))
            entry = float(p.get("avg_entry_price", 0))
            unrealized = shares * (mark - entry)
            lines.append(f"   [{side}] {q}  미실현 ${unrealized:+.2f}")

    lines += ["", sep, ""]
    return "\n".join(lines)


def main() -> None:
    now = _utc_now()
    # 지난 7일 = 이번 주 리포트
    week_end = now
    week_start = now - timedelta(days=7)

    report = build_report(week_start, week_end)
    print(report)

    # 파일 저장
    fname = REPORT_DIR / f"weekly_report_{now.strftime('%Y%m%d')}.txt"
    fname.write_text(report, encoding="utf-8")
    print(f"리포트 저장: {fname}", file=sys.stderr)

    # 오래된 리포트 삭제 (30일 이상)
    for old in REPORT_DIR.glob("weekly_report_*.txt"):
        try:
            age = now - datetime.fromtimestamp(old.stat().st_mtime, tz=timezone.utc)
            if age.days > 30:
                old.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    main()
