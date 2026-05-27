from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Settings, load_settings
from .runner_status import read_runner_status


HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Polymarket Weather Bot Dashboard</title>
  <style>
    :root {
      --bg: #030504;
      --panel: #07100c;
      --panel-2: #0a1510;
      --line: #133022;
      --text: #d8ffe6;
      --muted: #6f8a79;
      --green: #00e64d;
      --green-soft: rgba(0, 230, 77, .16);
      --red: #ff2647;
      --red-soft: rgba(255, 38, 71, .16);
      --yellow: #ffb21d;
      --blue: #4ba3ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: radial-gradient(circle at 50% 0%, #0b1c13 0, var(--bg) 36rem);
      color: var(--text);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      letter-spacing: 0;
    }
    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
    }
    .topbar {
      height: 42px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 18px;
      background: rgba(0, 0, 0, .72);
    }
    .brand {
      color: var(--green);
      font-size: 15px;
      font-weight: 900;
      letter-spacing: .22em;
      text-shadow: 0 0 12px rgba(0, 230, 77, .45);
    }
    .statusline {
      display: flex;
      gap: 18px;
      color: var(--muted);
      font-size: 11px;
      white-space: nowrap;
    }
    .statusline b { color: var(--green); font-weight: 800; }
    .grid {
      display: grid;
      grid-template-columns: minmax(270px, 22vw) minmax(520px, 1fr) minmax(300px, 24vw);
      gap: 8px;
      padding: 8px;
      min-height: calc(100vh - 42px);
    }
    .col, .panel {
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(8, 18, 13, .94), rgba(2, 5, 4, .94));
      min-width: 0;
    }
    .col { overflow: hidden; }
    .panel { margin-bottom: 8px; }
    .panel-title {
      height: 31px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 10px;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .16em;
    }
    .panel-body { padding: 10px; }
    .metric-row {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 8px;
    }
    .metric {
      min-height: 76px;
      padding: 10px;
      border: 1px solid #10281c;
      background: rgba(0, 0, 0, .36);
    }
    .metric .label {
      color: var(--muted);
      font-size: 10px;
      text-transform: uppercase;
    }
    .metric .value {
      margin-top: 8px;
      font-size: clamp(18px, 2vw, 29px);
      font-weight: 900;
      color: var(--text);
      white-space: nowrap;
    }
    .metric.good .value { color: var(--green); }
    .metric.bad .value { color: var(--red); }
    .metric.warn .value { color: var(--yellow); }
    .chart-wrap {
      height: min(42vh, 430px);
      min-height: 290px;
      position: relative;
    }
    canvas { width: 100%; height: 100%; display: block; }
    .trade-list, .event-list, .decision-list { display: grid; gap: 8px; }
    .event-list { max-height: calc(100vh - 96px); overflow: auto; padding: 10px; }
    .event {
      display: grid;
      grid-template-columns: 64px 1fr;
      gap: 8px;
      border-bottom: 1px solid rgba(19, 48, 34, .65);
      padding-bottom: 7px;
      font-size: 11px;
    }
    .event .time { color: var(--muted); }
    .event b { color: var(--green); }
    .event .warn { color: var(--yellow); }
    .event .bad { color: var(--red); }
    .card {
      border: 1px solid #10281c;
      background: rgba(0, 0, 0, .34);
      padding: 10px;
    }
    .card.open { border-left: 3px solid var(--green); }
    .card.close { border-left: 3px solid var(--blue); }
    .card.skip { border-left: 3px solid var(--yellow); }
    .market-title {
      font-size: 13px;
      line-height: 1.35;
      color: var(--text);
      margin-bottom: 8px;
    }
    .badges {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
    }
    .badge {
      border: 1px solid #183b2a;
      background: rgba(0, 0, 0, .38);
      color: var(--muted);
      padding: 4px 7px;
      font-size: 11px;
      line-height: 1;
    }
    .badge.yes, .badge.long, .badge.win { color: var(--green); border-color: #0c7a32; background: var(--green-soft); }
    .badge.no, .badge.short, .badge.loss { color: var(--red); border-color: #7a1020; background: var(--red-soft); }
    .badge.price { color: var(--yellow); border-color: #64450e; }
    .badge.neutral { color: var(--blue); border-color: #174b75; }
    .muted { color: var(--muted); }
    .small { font-size: 11px; line-height: 1.45; }
    .split-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .bar {
      height: 14px;
      background: #18070a;
      border: 1px solid #2b0b12;
      position: relative;
      margin: 7px 0;
    }
    .bar span {
      position: absolute;
      left: 0; top: 0; bottom: 0;
      background: linear-gradient(90deg, #00a637, var(--green));
    }
    .right-stat {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
      padding: 7px 0;
      border-bottom: 1px solid rgba(19, 48, 34, .55);
      font-size: 12px;
    }
    .right-stat strong { color: var(--green); font-size: 15px; }
    .lock {
      display: none;
      padding: 14px;
      border: 1px solid var(--red);
      color: var(--red);
      background: var(--red-soft);
      margin: 8px;
    }
    @media (max-width: 1100px) {
      .grid { grid-template-columns: 1fr; }
      .metric-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .chart-wrap { height: 320px; }
      .event-list { max-height: 360px; }
    }
  </style>
</head>
<body>
<div class="shell">
  <header class="topbar">
    <div class="brand">WEATHER BOT OPS</div>
    <div class="statusline">
      <span>SYS <b id="sys-status">SYNC</b></span>
      <span>BOT <b id="bot-status">--</b></span>
      <span>MODE <b>PAPER</b></span>
      <span>UPDATED <b id="updated">--</b></span>
    </div>
  </header>
  <div id="lock" class="lock">대시보드 토큰이 없거나 틀립니다. URL의 <b>?token=...</b> 값을 확인하세요.</div>
  <main class="grid">
    <aside class="col">
      <div class="panel-title">Event Stream <span id="event-count">0</span></div>
      <div id="event-stream" class="event-list"></div>
    </aside>

    <section>
      <div class="metric-row">
        <div class="metric"><div class="label">Initial</div><div id="m-initial" class="value">$0</div></div>
        <div class="metric" id="m-pnl-box"><div class="label">PnL</div><div id="m-pnl" class="value">$0</div></div>
        <div class="metric"><div class="label">Equity</div><div id="m-equity" class="value">$0</div></div>
        <div class="metric good"><div class="label">Wins</div><div id="m-wins" class="value">0</div></div>
        <div class="metric bad"><div class="label">Losses</div><div id="m-losses" class="value">0</div></div>
        <div class="metric warn"><div class="label">Win Rate</div><div id="m-winrate" class="value">0%</div></div>
      </div>

      <div class="panel">
        <div class="panel-title">Equity / PnL Curve <span id="chart-caption">live</span></div>
        <div class="panel-body chart-wrap"><canvas id="equity-chart"></canvas></div>
      </div>

      <div class="split-2">
        <div class="panel">
          <div class="panel-title">Open Positions <span id="open-count">0</span></div>
          <div class="panel-body"><div id="open-positions" class="trade-list"></div></div>
        </div>
        <div class="panel">
          <div class="panel-title">Recent Trades <span id="trade-count">0</span></div>
          <div class="panel-body"><div id="recent-trades" class="trade-list"></div></div>
        </div>
      </div>
    </section>

    <aside class="col">
      <div class="panel-title">Scanner Intelligence</div>
      <div class="panel-body">
        <div class="right-stat"><span>후보 판단</span><strong id="r-decisions">0</strong></div>
        <div class="right-stat"><span>NO FORECAST</span><strong id="r-no-forecast">0</strong></div>
        <div class="right-stat"><span>스킵</span><strong id="r-skips">0</strong></div>
        <div class="right-stat"><span>진입 신호</span><strong id="r-entries">0</strong></div>
        <div class="right-stat"><span>열린 포지션</span><strong id="r-open">0</strong></div>
        <div class="right-stat"><span>총 노출</span><strong id="r-exposure">$0</strong></div>
        <div class="right-stat"><span>현금</span><strong id="r-cash">$0</strong></div>
      </div>
      <div class="panel-title">Buy Pressure</div>
      <div class="panel-body" id="pressure-bars"></div>
      <div class="panel-title">Recent Candidates</div>
      <div class="panel-body"><div id="recent-decisions" class="decision-list"></div></div>
    </aside>
  </main>
</div>

<script>
const params = new URLSearchParams(location.search);
const urlToken = params.get("token");
if (urlToken) localStorage.setItem("dashboardToken", urlToken);
const token = localStorage.getItem("dashboardToken") || "";
const chartSamples = [];

function money(v) {
  const sign = v < 0 ? "-" : "";
  return sign + "$" + Math.abs(v || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
function pct(v) { return ((v || 0) * 100).toFixed(1) + "%"; }
function price(v) { return (v === null || v === undefined || isNaN(v)) ? "--" : Number(v).toFixed(2) + "E"; }
function duration(sec) {
  sec = Math.max(0, Math.round(Number(sec || 0)));
  if (sec < 60) return sec + "s";
  const min = Math.floor(sec / 60);
  const rem = sec % 60;
  return rem ? min + "m " + rem + "s" : min + "m";
}
function shortTime(ts) {
  if (!ts) return "--";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts).slice(11, 19);
  return d.toLocaleTimeString("ko-KR", {hour12:false});
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
}
function setText(id, text) { document.getElementById(id).textContent = text; }

function cardForPosition(p) {
  const pnlClass = (p.unrealized_pnl || 0) >= 0 ? "win" : "loss";
  return `<div class="card open">
    <div class="market-title">${esc(p.question)}</div>
    <div class="badges">
      <span class="badge ${p.side === "YES" ? "yes" : "no"}">${esc(p.side)}</span>
      <span class="badge long">LONG</span>
      <span class="badge price">${price(p.entry_price)} 진입</span>
      <span class="badge neutral">mark ${price(p.mark_price)}</span>
      <span class="badge ${pnlClass}">${money(p.unrealized_pnl)}</span>
    </div>
    <div class="small muted" style="margin-top:8px">${esc(p.city || "")} ${esc(p.date_hint || "")} · shares ${Number(p.shares || 0).toFixed(2)}</div>
  </div>`;
}

function cardForTrade(t) {
  const action = String(t.action || "");
  const isClose = action.includes("CLOSE") || action.includes("SETTLE");
  const pnl = Number(t.cash_delta_or_pnl || 0);
  return `<div class="card ${isClose ? "close" : "open"}">
    <div class="market-title">${esc(t.question)}</div>
    <div class="badges">
      <span class="badge neutral">${esc(action)}</span>
      <span class="badge ${t.side === "YES" ? "yes" : "no"}">${esc(t.side)}</span>
      <span class="badge price">${price(t.price)}</span>
      <span class="badge ${pnl >= 0 ? "win" : "loss"}">${money(pnl)}</span>
    </div>
    <div class="small muted" style="margin-top:8px">${esc(t.reason || "")}</div>
  </div>`;
}

function cardForDecision(d) {
  const cls = d.side === "SKIP" ? "skip" : "open";
  return `<div class="card ${cls}">
    <div class="market-title">${esc(d.question)}</div>
    <div class="badges">
      <span class="badge ${d.side === "YES" ? "yes" : d.side === "NO" ? "no" : "neutral"}">${esc(d.side)}</span>
      <span class="badge price">edge ${Number(d.net_edge || 0).toFixed(3)}</span>
      <span class="badge neutral">p ${Number(d.p_true || 0).toFixed(3)}</span>
    </div>
    <div class="small muted" style="margin-top:8px">${esc(d.reason || "")}</div>
  </div>`;
}

function drawChart(payload) {
  const canvas = document.getElementById("equity-chart");
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, rect.width, rect.height);
  const pad = 28;
  const points = [...(payload.equity_points || [])];
  chartSamples.push({ts: Date.now(), equity: payload.summary.equity});
  while (chartSamples.length > 90) chartSamples.shift();
  for (const s of chartSamples) points.push(s);
  if (points.length < 2) {
    points.push({equity: payload.summary.initial_bankroll}, {equity: payload.summary.equity});
  }
  const ys = points.map(p => Number(p.equity || 0));
  let min = Math.min(...ys), max = Math.max(...ys);
  if (min === max) { min -= 1; max += 1; }
  const x = i => pad + (rect.width - pad * 2) * (i / Math.max(1, points.length - 1));
  const y = v => rect.height - pad - (rect.height - pad * 2) * ((v - min) / (max - min));
  ctx.strokeStyle = "rgba(19,48,34,.9)";
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    const gy = pad + (rect.height - pad * 2) * i / 4;
    ctx.beginPath(); ctx.moveTo(pad, gy); ctx.lineTo(rect.width - pad, gy); ctx.stroke();
  }
  const grad = ctx.createLinearGradient(0, pad, 0, rect.height - pad);
  grad.addColorStop(0, "rgba(0,230,77,.34)");
  grad.addColorStop(1, "rgba(0,230,77,0)");
  ctx.beginPath();
  points.forEach((p, i) => i ? ctx.lineTo(x(i), y(Number(p.equity || 0))) : ctx.moveTo(x(i), y(Number(p.equity || 0))));
  ctx.lineTo(x(points.length - 1), rect.height - pad);
  ctx.lineTo(x(0), rect.height - pad);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();
  ctx.beginPath();
  points.forEach((p, i) => i ? ctx.lineTo(x(i), y(Number(p.equity || 0))) : ctx.moveTo(x(i), y(Number(p.equity || 0))));
  ctx.strokeStyle = payload.summary.total_pnl >= 0 ? "#00e64d" : "#ff2647";
  ctx.lineWidth = 2;
  ctx.shadowBlur = 14;
  ctx.shadowColor = ctx.strokeStyle;
  ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.fillStyle = "#6f8a79";
  ctx.font = "11px monospace";
  ctx.fillText(money(max), 6, pad);
  ctx.fillText(money(min), 6, rect.height - pad);
}

function render(payload) {
  document.getElementById("lock").style.display = "none";
  setText("sys-status", payload.security.auth_required ? "LOCKED" : "OPEN");
  const bot = payload.bot || {};
  const phase = bot.phase ? bot.phase.toUpperCase() : (bot.status || "--");
  const progress = bot.markets_total ? " " + (bot.markets_done || 0) + "/" + bot.markets_total : "";
  const next = bot.next_scan_in_seconds > 0 ? " next " + duration(bot.next_scan_in_seconds) : "";
  setText("bot-status", phase + progress + " " + duration(bot.age_seconds) + next);
  setText("updated", shortTime(payload.generated_at));
  setText("m-initial", money(payload.summary.initial_bankroll));
  setText("m-pnl", money(payload.summary.total_pnl));
  document.getElementById("m-pnl-box").className = "metric " + (payload.summary.total_pnl >= 0 ? "good" : "bad");
  setText("m-equity", money(payload.summary.equity));
  setText("m-wins", payload.summary.wins);
  setText("m-losses", payload.summary.losses);
  setText("m-winrate", pct(payload.summary.win_rate));
  setText("r-decisions", payload.scanner.decisions);
  setText("r-no-forecast", payload.scanner.forecast_unavailable || 0);
  setText("r-skips", payload.scanner.skips);
  setText("r-entries", payload.scanner.entries);
  setText("r-open", payload.summary.open_positions);
  setText("r-exposure", money(payload.summary.exposure));
  setText("r-cash", money(payload.summary.cash));
  setText("open-count", payload.positions.length);
  setText("trade-count", payload.recent_trades.length);
  setText("event-count", payload.events.length);
  document.getElementById("open-positions").innerHTML = payload.positions.length ? payload.positions.map(cardForPosition).join("") : `<div class="small muted">열린 포지션 없음</div>`;
  document.getElementById("recent-trades").innerHTML = payload.recent_trades.length ? payload.recent_trades.slice(0, 8).map(cardForTrade).join("") : `<div class="small muted">거래 기록 없음</div>`;
  document.getElementById("recent-decisions").innerHTML = payload.recent_decisions.length ? payload.recent_decisions.slice(0, 12).map(cardForDecision).join("") : `<div class="small muted">후보 판단 기록 없음</div>`;
  document.getElementById("event-stream").innerHTML = payload.events.map(e => {
    const cls = e.tone === "bad" ? "bad" : e.tone === "warn" ? "warn" : "";
    return `<div class="event"><div class="time">${shortTime(e.ts)}</div><div><b class="${cls}">${esc(e.label)}</b><br><span class="muted">${esc(e.detail)}</span></div></div>`;
  }).join("");
  const pressures = payload.pressure || [];
  document.getElementById("pressure-bars").innerHTML = pressures.length ? pressures.map(p => `
    <div class="small">${esc(p.label)} <span class="muted">${Math.round(p.value * 100)}%</span></div>
    <div class="bar"><span style="width:${Math.max(0, Math.min(100, p.value * 100))}%"></span></div>
  `).join("") : `<div class="small muted">압력 데이터 없음</div>`;
  drawChart(payload);
}

async function tick() {
  try {
    const q = token ? "?token=" + encodeURIComponent(token) : "";
    const res = await fetch("/api/status" + q, {headers: token ? {"X-Dashboard-Token": token} : {}});
    if (res.status === 403) {
      document.getElementById("lock").style.display = "block";
      setText("sys-status", "AUTH");
      return;
    }
    render(await res.json());
  } catch (err) {
    setText("sys-status", "ERROR");
    console.error(err);
  }
}
tick();
setInterval(tick, 2000);
addEventListener("resize", () => tick());
</script>
</body>
</html>
"""


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


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(row: dict[str, Any]) -> str:
    return str(row.get("ts") or row.get("opened_at") or "")


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
    return max(1, int(settings.forecast_refresh_interval_seconds))


def _sorted_recent(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return sorted(rows, key=_parse_ts, reverse=True)[:limit]


def _position_payload(pos: dict[str, Any]) -> dict[str, Any]:
    metadata = pos.get("metadata") if isinstance(pos.get("metadata"), dict) else {}
    entry = _float(pos.get("entry_price"))
    shares = _float(pos.get("shares"))
    cost = _float(pos.get("cost_usd"))
    mark = _float(pos.get("last_mark_price"), entry)
    value = shares * mark
    return {
        "position_id": pos.get("position_id", ""),
        "market_id": pos.get("market_id", ""),
        "question": pos.get("question", ""),
        "side": pos.get("side", ""),
        "entry_price": entry,
        "mark_price": mark,
        "shares": shares,
        "cost_usd": cost,
        "market_value": value,
        "unrealized_pnl": value - cost,
        "opened_at": pos.get("opened_at", ""),
        "city": metadata.get("city", ""),
        "date_hint": metadata.get("date_hint", ""),
        "target_exit_price": _float(metadata.get("last_target_exit_price"), _float(metadata.get("target_exit_price"))),
        "probability_stop_threshold": _float(metadata.get("probability_stop_threshold")),
        "reason": metadata.get("reason", ""),
    }


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


def _equity_points(settings: Settings, trades: list[dict[str, str]], current_curve_value: float) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = [{"ts": "", "equity": settings.bankroll_usd}]
    realized = 0.0
    for row in trades:
        if row.get("action") in {"CLOSE", "SETTLED", "PARTIAL_CLOSE"}:
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
        tone = "warn" if side == "SKIP" else "good"
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


def build_dashboard_payload(settings: Settings | None = None, auth_required: bool = False) -> dict[str, Any]:
    settings = settings or load_settings()
    state = _read_json(Path(settings.state_path))
    trades = _read_csv(Path(settings.trades_csv_path), 800)
    decisions = _read_csv(Path(settings.decisions_csv_path), 800)
    runner_status = read_runner_status(settings)
    positions = [_position_payload(p) for p in state.get("positions", []) if isinstance(p, dict)]
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
    skip_count = sum(1 for row in decisions if row.get("side") == "SKIP")
    entry_count = sum(1 for row in decisions if row.get("side") in {"YES", "NO"})
    forecast_unavailable_count = sum(
        1
        for row in decisions
        if "forecast unavailable" in (row.get("note") or "").lower()
    )
    recent_decisions = _sorted_recent(decisions, 60)
    pressure_yes = sum(1 for row in decisions[-100:] if row.get("side") == "YES")
    pressure_no = sum(1 for row in decisions[-100:] if row.get("side") == "NO")
    pressure_skip = sum(1 for row in decisions[-100:] if row.get("side") == "SKIP")
    denom = max(1, pressure_yes + pressure_no + pressure_skip)
    return {
        "generated_at": _now_iso(),
        "security": {"auth_required": auth_required},
        "bot": _bot_status(settings, trades, decisions, positions, runner_status),
        "summary": {
            "initial_bankroll": settings.bankroll_usd,
            "cash": cash,
            "exposure": exposure,
            "market_value": market_value,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "total_pnl": total_pnl,
            "equity": equity,
            "open_positions": len(positions),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / closed_total if closed_total else 0.0,
        },
        "positions": positions,
        "recent_trades": _sorted_recent(trades, 80),
        "recent_decisions": recent_decisions,
        "scanner": {
            "decisions": len(decisions),
            "forecast_unavailable": forecast_unavailable_count,
            "skips": skip_count,
            "entries": entry_count,
        },
        "pressure": [
            {"label": "YES signals", "value": pressure_yes / denom},
            {"label": "NO signals", "value": pressure_no / denom},
            {"label": "SKIP ratio", "value": pressure_skip / denom},
        ],
        "events": _events(trades, decisions),
        "equity_points": _equity_points(settings, trades, curve_value),
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
        query_token = parse_qs(parsed.query).get("token", [""])[0]
        header_token = self.headers.get("X-Dashboard-Token", "")
        return query_token == token or header_token == token

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
        print(f"{self.address_string()} - {fmt % args}")


def run_dashboard(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    host = settings.dashboard_host
    port = settings.dashboard_port
    token = settings.dashboard_token
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    server.settings = settings  # type: ignore[attr-defined]
    server.dashboard_token = token  # type: ignore[attr-defined]
    token_note = "enabled" if token else "disabled"
    print(f"Weather dashboard listening on http://{host}:{port} auth={token_note}")
    server.serve_forever()


def main() -> None:
    run_dashboard(load_settings())


if __name__ == "__main__":
    main()
