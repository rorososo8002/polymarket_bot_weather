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
from .edge import polymarket_taker_fee_usdc
from .runner_status import read_runner_status
from .weather_client import parse_weather_question


_DECISION_TOTALS_LOCK = threading.Lock()
_DECISION_TOTALS_CACHE: dict[str, dict[str, Any]] = {}
_TRADE_TOTALS_LOCK = threading.Lock()
_TRADE_TOTALS_CACHE: dict[str, dict[str, Any]] = {}
TRADE_ACTIVITY_ACTIONS = {"OPEN", "CLOSE", "SETTLED", "PARTIAL_CLOSE"}
REALIZED_TRADE_ACTIONS = {"CLOSE", "SETTLED", "PARTIAL_CLOSE"}
TRADE_CACHE_RECENT_LIMIT = 400
MAX_INITIAL_DECISION_TOTAL_SCAN_BYTES = 128 * 1024 * 1024
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


HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>폴리마켓 날씨 봇 대시보드</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #080a0f;
      --panel: #0f1117;
      --panel-2: #151924;
      --panel-3: #1b2030;
      --line: #252a36;
      --line-strong: #333a4a;
      --text: #f7f8fa;
      --muted: #9ba3b0;
      --muted-2: #697180;
      --green: #00c853;
      --green-soft: rgba(0, 200, 83, .14);
      --red: #ff4d4f;
      --red-soft: rgba(255, 77, 79, .13);
      --yellow: #f5b83d;
      --yellow-soft: rgba(245, 184, 61, .14);
      --blue: #2e5cff;
      --blue-soft: rgba(46, 92, 255, .18);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
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
      gap: 12px;
      padding: 0 18px;
      background: rgba(8, 10, 15, .94);
      backdrop-filter: blur(14px);
      min-width: 0;
    }
    .brand {
      color: var(--text);
      font-size: 15px;
      font-weight: 800;
      letter-spacing: -.01em;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .statusline {
      display: flex;
      gap: 18px;
      color: var(--muted);
      font-size: 11px;
      white-space: nowrap;
      min-width: 0;
    }
    .statusline b { color: var(--green); font-weight: 800; }
    .grid {
      display: grid;
      grid-template-columns: minmax(270px, 22vw) minmax(520px, 1fr) minmax(300px, 24vw);
      gap: 8px;
      padding: 8px;
      min-height: calc(100vh - 42px);
    }
    .grid > *,
    section {
      min-width: 0;
    }
    .col, .panel {
      border: 1px solid var(--line);
      background: var(--panel);
      min-width: 0;
      border-radius: 8px;
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
      font-weight: 700;
      letter-spacing: .02em;
    }
    .panel-body { padding: 10px; min-width: 0; }
    .metric-row {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 8px;
    }
    .metric {
      min-height: 76px;
      padding: 10px;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
    }
    .metric .label {
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
    }
    .metric .value {
      margin-top: 8px;
      font-size: clamp(18px, 1.6vw, 27px);
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
    .trade-list, .position-list, .realized-list { display: grid; gap: 8px; }
    .position-list { max-height: calc(100vh - 96px); overflow: auto; padding: 10px; }
    .realized-list { max-height: min(32vh, 360px); overflow: auto; }
    .trade-list { min-height: 0; align-content: start; }
    .right-col {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      min-height: 0;
      max-height: calc(100vh - 58px);
    }
    .right-tabs {
      display: flex;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    .tab-btn {
      flex: 1 1 0;
      appearance: none;
      border: 0;
      border-right: 1px solid var(--line);
      background: transparent;
      color: var(--muted);
      padding: 9px 10px;
      font: inherit;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: .02em;
      cursor: pointer;
    }
    .tab-btn:last-child { border-right: 0; }
    .tab-btn:hover,
    .tab-btn:focus-visible {
      color: var(--text);
      outline: none;
      background: rgba(46, 92, 255, .10);
    }
    .tab-btn.active {
      color: #ffffff;
      background: var(--blue);
    }
    .right-panels {
      min-height: 0;
      overflow: hidden;
    }
    .tab-panel {
      display: none;
      height: 100%;
      min-height: 0;
    }
    .tab-panel.active {
      display: grid;
      grid-template-rows: minmax(0, 1fr);
    }
    .scanner-body,
    .recent-trades-body {
      min-height: 0;
      padding: 10px;
      overflow: auto;
    }
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
      border: 1px solid var(--line);
      background: var(--panel-2);
      padding: 12px;
      border-radius: 8px;
      min-width: 0;
    }
    .card.open { border-left: 3px solid var(--blue); }
    .card.close { border-left: 3px solid var(--blue); }
    .card.skip { border-left: 3px solid var(--yellow); }
    .card.profit { border-left: 3px solid var(--green); }
    .card.loss { border-left: 3px solid var(--red); }
    .market-title {
      font-size: 13px;
      line-height: 1.35;
      color: var(--text);
      margin-bottom: 8px;
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    .market-link {
      display: inline-block;
      text-decoration: none;
      color: var(--text);
    }
    .market-link:hover { color: var(--blue); }
    .market-link:focus-visible {
      outline: 2px solid var(--blue);
      outline-offset: 3px;
      border-radius: 4px;
    }
    .badges {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
    }
    .badge {
      border: 1px solid var(--line-strong);
      background: var(--panel-3);
      color: var(--muted);
      padding: 4px 7px;
      font-size: 11px;
      line-height: 1;
      font-weight: 700;
      border-radius: 4px;
    }
    .badge.yes, .badge.long, .badge.win { color: var(--green); border-color: #0c7a32; background: var(--green-soft); }
    .badge.no, .badge.short, .badge.loss { color: var(--red); border-color: #7a1020; background: var(--red-soft); }
    .badge.price { color: var(--yellow); border-color: rgba(245, 184, 61, .42); background: var(--yellow-soft); }
    .badge.neutral { color: var(--blue); border-color: rgba(46, 92, 255, .46); background: var(--blue-soft); }
    .badge.forecast { color: var(--text); border-color: rgba(46, 92, 255, .7); background: var(--blue); }
    .muted { color: var(--muted); }
    .small { font-size: 11px; line-height: 1.45; overflow-wrap: anywhere; }
    .split-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .result-table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-size: 11px;
      min-width: 620px;
    }
    .result-table th,
    .result-table td {
      border-bottom: 1px solid rgba(19, 48, 34, .62);
      padding: 9px 10px;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    .result-table th {
      color: var(--muted);
      text-align: left;
      font-size: 10px;
      font-weight: 750;
      position: sticky;
      top: 0;
      background: var(--panel);
      z-index: 1;
    }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .profit-text { color: var(--green); }
    .loss-text { color: var(--red); }
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
      padding: 9px 0;
      border-bottom: 1px solid rgba(19, 48, 34, .55);
      font-size: 12px;
    }
    .right-stat strong { color: var(--green); font-size: 15px; }
    .right-stat strong.bad { color: var(--red); }
    .right-stat strong.neutral { color: var(--text); }
    .health-box {
      display: grid;
      gap: 5px;
      margin-top: 10px;
      padding: 9px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel-2);
    }
    .health-title {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      font-size: 12px;
      font-weight: 800;
    }
    .health-title strong { color: var(--green); }
    .health-title strong.bad { color: var(--red); }
    .health-title strong.warn { color: var(--yellow); }
    .health-detail { color: var(--muted); font-size: 11px; line-height: 1.45; overflow-wrap: anywhere; }
    .chart-title {
      gap: 10px;
      min-height: 38px;
      height: auto;
      padding: 7px 10px;
    }
    .range-controls {
      display: flex;
      align-items: center;
      gap: 4px;
      margin-left: auto;
    }
    .range-btn {
      appearance: none;
      border: 1px solid transparent;
      background: transparent;
      color: var(--muted);
      border-radius: 4px;
      padding: 5px 8px;
      font: inherit;
      font-size: 11px;
      font-weight: 750;
      cursor: pointer;
    }
    .range-btn:hover,
    .range-btn:focus-visible {
      color: var(--text);
      border-color: var(--line-strong);
      outline: none;
    }
    .range-btn.active {
      color: #ffffff;
      background: var(--blue);
      border-color: var(--blue);
    }
    .chart-caption {
      color: var(--muted-2);
      font-size: 11px;
      font-weight: 700;
      margin-left: 6px;
    }
    .chart-tooltip {
      position: absolute;
      display: none;
      pointer-events: none;
      padding: 8px 10px;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      background: rgba(15, 17, 23, .96);
      box-shadow: 0 12px 30px rgba(0, 0, 0, .32);
      color: var(--text);
      font-size: 12px;
      line-height: 1.35;
      z-index: 5;
      min-width: 124px;
    }
    .chart-tooltip b { color: var(--blue); }
    .lock {
      display: none;
      padding: 14px;
      border: 1px solid var(--red);
      color: var(--red);
      background: var(--red-soft);
      margin: 8px;
    }
    @media (max-width: 1100px) {
      .topbar {
        height: auto;
        min-height: 42px;
        align-items: flex-start;
        flex-direction: column;
        gap: 5px;
        padding: 7px 10px;
      }
      .statusline {
        flex-wrap: wrap;
        gap: 5px 12px;
        white-space: normal;
      }
      .grid { grid-template-columns: 1fr; }
      .metric-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .chart-wrap { height: 320px; }
      .position-list, .realized-list { max-height: 360px; }
      .right-col { max-height: 640px; }
      .result-table { min-width: 0; font-size: 10px; }
      .result-table th,
      .result-table td { padding: 8px 5px; }
    }
    @media (min-width: 1101px) and (max-width: 1500px) {
      .metric-row { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
<div class="shell">
  <header class="topbar">
    <div class="brand">폴리마켓 날씨 봇</div>
    <div class="statusline">
      <span>시스템 <b id="sys-status">동기화</b></span>
      <span>봇 <b id="bot-status">--</b></span>
      <span>모드 <b>종이매매</b></span>
      <span>업데이트 <b id="updated">--</b></span>
    </div>
  </header>
  <div id="lock" class="lock">대시보드 토큰이 없거나 맞지 않습니다. 주소의 <b>?token=...</b> 값을 확인하세요.</div>
  <main class="grid">
    <aside class="col">
      <div class="panel-title">보유 포지션 <span id="open-count">0</span></div>
      <div id="open-positions" class="position-list"></div>
    </aside>

    <section>
      <div class="metric-row">
        <div class="metric"><div class="label">초기 자금</div><div id="m-initial" class="value">$0</div></div>
        <div class="metric" id="m-pnl-box"><div class="label">손익</div><div id="m-pnl" class="value">$0</div></div>
        <div class="metric"><div class="label">평가자산</div><div id="m-equity" class="value">$0</div></div>
        <div class="metric good"><div class="label">이긴 거래</div><div id="m-wins" class="value">0</div></div>
        <div class="metric bad"><div class="label">진 거래</div><div id="m-losses" class="value">0</div></div>
        <div class="metric warn"><div class="label">승률</div><div id="m-winrate" class="value">0%</div></div>
      </div>

      <div class="panel">
        <div class="panel-title chart-title">
          <span>자산 / 손익 곡선 <span id="chart-caption" class="chart-caption">실시간</span></span>
          <div class="range-controls" aria-label="차트 기간">
            <button class="range-btn" data-range="1D" type="button">1일</button>
            <button class="range-btn" data-range="7D" type="button">7일</button>
            <button class="range-btn" data-range="1M" type="button">1개월</button>
            <button class="range-btn" data-range="1Y" type="button">1년</button>
            <button class="range-btn active" data-range="ALL" type="button">전체</button>
          </div>
        </div>
        <div class="panel-body chart-wrap">
          <canvas id="equity-chart"></canvas>
          <div id="chart-tooltip" class="chart-tooltip"></div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-title">확정 손익 <span id="realized-count">0</span></div>
        <div class="panel-body">
          <div id="realized-results" class="realized-list"></div>
        </div>
      </div>
    </section>

    <aside class="col right-col">
      <div class="right-tabs" role="tablist" aria-label="오른쪽 정보">
        <button id="scanner-tab" class="tab-btn active" type="button" role="tab" aria-selected="true" aria-controls="scanner-panel" data-tab-target="scanner-panel">스캐너 정보</button>
        <button id="trades-tab" class="tab-btn" type="button" role="tab" aria-selected="false" aria-controls="trades-panel" data-tab-target="trades-panel">최근 체결 <span id="trade-count">0</span></button>
      </div>
      <div class="right-panels">
        <div id="scanner-panel" class="tab-panel active" role="tabpanel" aria-labelledby="scanner-tab">
          <div class="panel-body scanner-body">
        <div class="right-stat"><span>보유 포지션</span><strong id="r-open">0</strong></div>
        <div class="right-stat"><span>총 진입 비용</span><strong id="r-exposure">$0</strong></div>
        <div class="right-stat"><span>최근 Open-Meteo 예보</span><strong id="r-latest-forecast" class="neutral">--</strong></div>
        <div class="right-stat"><span>총 이익</span><strong id="r-total-profit">$0</strong></div>
        <div class="right-stat"><span>총 손실</span><strong id="r-total-loss" class="bad">$0</strong></div>
        <div class="right-stat"><span>남은 현금</span><strong id="r-cash">$0</strong></div>
        <div class="health-box">
          <div class="health-title"><span>예보 상태</span><strong id="r-forecast-health">--</strong></div>
          <div id="r-forecast-success" class="health-detail">마지막 성공 --</div>
          <div id="r-forecast-attempt" class="health-detail">마지막 시도 --</div>
          <div id="r-forecast-age" class="health-detail">예보 저장 후 경과 --</div>
          <div id="r-forecast-error" class="health-detail">최근 실패 이유 --</div>
          <div id="r-forecast-persistence" class="health-detail">파일 저장 오류 --</div>
        </div>
        <div class="health-box">
          <div class="health-title"><span>실시간 주문장 상태</span><strong id="r-websocket-health">--</strong></div>
          <div id="r-websocket-thread" class="health-detail">실시간 수신 스레드 --</div>
          <div id="r-websocket-reconnects" class="health-detail">재연결 --</div>
          <div id="r-websocket-message" class="health-detail">마지막 메시지 --</div>
          <div id="r-websocket-book" class="health-detail">마지막 주문장 --</div>
          <div id="r-websocket-error" class="health-detail">최근 오류 --</div>
        </div>
        <div class="health-box">
          <div class="health-title"><span>이벤트 포트폴리오</span><strong id="r-portfolio-event">--</strong></div>
          <div class="health-detail">기준 자금 &lt; $1,000: 도시-날짜 10% · $1,000 이상: 5% · 최대 2개</div>
          <div class="health-detail">다리 1개당 최소 $10 · 도시 전체 20% · 전체 보유 90%</div>
          <div class="health-detail">서로 다른 구간 조합 비교: YES+YES · YES+NO · NO+NO</div>
          <div id="r-portfolio-budget" class="health-detail">최근 기준 예산 --</div>
          <div id="r-portfolio-selected" class="health-detail">선택된 다리 --</div>
          <div id="r-portfolio-rejected" class="health-detail">거절된 다리 --</div>
          <div id="r-portfolio-scenario" class="health-detail">최악 시나리오 --</div>
        </div>
          </div>
        </div>
        <div id="trades-panel" class="tab-panel" role="tabpanel" aria-labelledby="trades-tab">
          <div class="panel-body recent-trades-body"><div id="recent-trades" class="trade-list"></div></div>
        </div>
      </div>
    </aside>
  </main>
</div>

<script>
const params = new URLSearchParams(location.search);
const urlToken = params.get("token");
if (urlToken) {
  localStorage.setItem("dashboardToken", urlToken);
  params.delete("token");
  const cleanQuery = params.toString();
  history.replaceState(null, "", location.pathname + (cleanQuery ? "?" + cleanQuery : "") + location.hash);
}
const token = localStorage.getItem("dashboardToken") || "";
let chartRange = "ALL";
let chartHoverX = null;
const RANGE_MS = {
  "1D": 24 * 60 * 60 * 1000,
  "7D": 7 * 24 * 60 * 60 * 1000,
  "1M": 30 * 24 * 60 * 60 * 1000,
  "1Y": 365 * 24 * 60 * 60 * 1000,
  "ALL": null,
};

function money(v) {
  const sign = v < 0 ? "-" : "";
  return sign + "$" + Math.abs(v || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
function pct(v) { return ((v || 0) * 100).toFixed(1) + "%"; }
function price(v) {
  if (v === null || v === undefined || isNaN(v)) return "--";
  const cents = Number(v) * 100;
  const whole = Math.abs(cents - Math.round(cents)) < 0.05;
  return cents.toLocaleString(undefined, {minimumFractionDigits: whole ? 0 : 1, maximumFractionDigits: whole ? 0 : 1}) + "¢";
}
function tempC(v) {
  if (v === null || v === undefined || isNaN(v)) return "--";
  const n = Number(v);
  return n.toLocaleString(undefined, {minimumFractionDigits: Number.isInteger(n) ? 0 : 1, maximumFractionDigits: 1}) + "°C";
}
function signedMoney(v) { return money(Number(v || 0)); }
function roi(v) { return (v === null || v === undefined || isNaN(v)) ? "--" : pct(Number(v)); }
function duration(sec) {
  sec = Math.max(0, Math.round(Number(sec || 0)));
  if (sec < 60) return sec + "초";
  const min = Math.floor(sec / 60);
  const rem = sec % 60;
  return rem ? min + "분 " + rem + "초" : min + "분";
}
function shortTime(ts) {
  if (!ts) return "--";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts).slice(11, 19);
  return d.toLocaleTimeString("ko-KR", {hour12:false});
}
function shortDateTime(ts) {
  if (!ts) return "--";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleString("ko-KR", {month:"short", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false});
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
}
const STATUS_KO = {
  "LOCKED": "잠김",
  "OPEN": "열림",
  "AUTH": "인증 필요",
  "ERROR": "오류",
  "RUNNING": "실행중",
  "WAIT": "대기",
  "LATE": "지연",
  "STALE": "오래됨",
  "FAILED": "실패",
  "DEGRADED": "불안정",
  "HEALTHY": "정상",
  "UNKNOWN": "알 수 없음",
  "WAITING": "대기",
  "NO DATA": "데이터 없음",
};
const PHASE_KO = {
  "starting": "시작중",
  "discovering": "시장 탐색중",
  "evaluating": "평가중",
  "closing": "청산 확인중",
  "streaming": "실시간 수신중",
  "stream_error": "실시간 오류",
};
const ACTION_KO = {
  "OPEN": "진입",
  "CLOSE": "청산",
  "SETTLED": "정산",
  "PARTIAL_CLOSE": "부분 청산",
};
function statusKo(status) {
  const raw = String(status || "").toUpperCase();
  return STATUS_KO[raw] || status || "--";
}
function phaseKo(phase) { return PHASE_KO[String(phase || "")] || phase; }
function sideKo(side) {
  const raw = String(side || "").toUpperCase();
  return raw === "YES" ? "예" : (raw === "NO" ? "아니오" : (side || "--"));
}
function actionKo(action) {
  const raw = String(action || "").toUpperCase();
  return ACTION_KO[raw] || action || "--";
}
function conditionKo(label) {
  const raw = String(label || "").toLowerCase();
  if (raw === "or higher") return "이상";
  if (raw === "or lower") return "이하";
  return label || "";
}
function setText(id, text) { document.getElementById(id).textContent = text; }
function setHealthStatus(id, status) {
  const element = document.getElementById(id);
  const raw = String(status || "").toUpperCase();
  element.textContent = statusKo(raw);
  element.className = raw === "FAILED" ? "bad" : (raw === "STALE" || raw === "DEGRADED" ? "warn" : "");
}

function cardForPosition(p) {
  const pnlClass = (p.unrealized_pnl || 0) >= 0 ? "win" : "loss";
  const title = esc(p.question);
  const titleHtml = p.market_url
    ? `<a class="market-title market-link" href="${esc(p.market_url)}" target="_blank" rel="noopener noreferrer">${title}</a>`
    : `<div class="market-title">${title}</div>`;
  return `<div class="card open">
    ${titleHtml}
    <div class="badges">
      <span class="badge ${p.side === "YES" ? "yes" : "no"}">${esc(sideKo(p.side))}</span>
      <span class="badge long">보유</span>
      <span class="badge forecast">${tempC(p.forecast_c)}</span>
      <span class="badge price">진입 ${price(p.entry_price)}</span>
      <span class="badge neutral">현재가 ${price(p.mark_price)}</span>
      <span class="badge ${pnlClass}">${money(p.unrealized_pnl)}</span>
    </div>
    <div class="small muted" style="margin-top:8px">${esc(p.city || "")} ${esc(p.date_hint || "")} · 수량 ${Number(p.shares || 0).toFixed(2)}</div>
  </div>`;
}

function cardForTrade(t) {
  const action = String(t.action || "");
  const isClose = action.includes("CLOSE") || action.includes("SETTLE");
  const pnl = Number(t.cash_delta_or_pnl || 0);
  return `<div class="card ${isClose ? "close" : "open"}">
    <div class="market-title">${esc(t.question)}</div>
    <div class="badges">
      <span class="badge neutral">${esc(actionKo(action))}</span>
      <span class="badge ${t.side === "YES" ? "yes" : "no"}">${esc(sideKo(t.side))}</span>
      <span class="badge price">${price(t.price)}</span>
      <span class="badge ${pnl >= 0 ? "win" : "loss"}">${money(pnl)}</span>
    </div>
    <div class="small muted" style="margin-top:8px">${esc(t.reason || "")}</div>
  </div>`;
}

function realizedTable(rows) {
  if (!rows.length) return `<div class="small muted">확정된 거래가 없습니다</div>`;
  return `<table class="result-table">
    <thead><tr>
      <th>날짜</th><th>도시</th><th class="num">예보</th><th>조건</th>
      <th class="num">예상 청산가</th><th class="num">진입가</th><th class="num">청산가</th>
      <th class="num">손익</th><th class="num">수익률</th>
    </tr></thead>
    <tbody>${rows.map(r => {
      const good = Number(r.pnl || 0) >= 0;
      return `<tr>
        <td>${esc(r.date_hint || shortTime(r.closed_at))}</td>
        <td>${esc(r.city || "--")}</td>
        <td class="num">${tempC(r.forecast_c)}</td>
        <td>${tempC(r.threshold_c)} ${esc(conditionKo(r.condition_label))}</td>
        <td class="num">${price(r.expected_exit_price)}</td>
        <td class="num">${price(r.entry_price)}</td>
        <td class="num">${price(r.exit_price)}</td>
        <td class="num ${good ? "profit-text" : "loss-text"}">${signedMoney(r.pnl)}</td>
        <td class="num ${good ? "profit-text" : "loss-text"}">${roi(r.roi)}</td>
      </tr>`;
    }).join("")}</tbody>
  </table>`;
}

function drawChart(payload) {
  const canvas = document.getElementById("equity-chart");
  const tooltip = document.getElementById("chart-tooltip");
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, rect.width, rect.height);
  const padL = 54, padR = 18, padT = 26, padB = 34;
  const rawPoints = [...(payload.equity_points || [])]
    .map(p => {
      const ts = p.ts || payload.summary.started_at || payload.generated_at;
      const t = new Date(ts).getTime();
      return {ts, t: Number.isFinite(t) ? t : Date.now(), equity: Number(p.equity || 0)};
    })
    .filter(p => Number.isFinite(p.equity));
  const newest = rawPoints.length ? Math.max(...rawPoints.map(p => Number.isFinite(p.t) ? p.t : 0)) : Date.now();
  const windowMs = RANGE_MS[chartRange];
  let points = windowMs ? rawPoints.filter(p => Number.isFinite(p.t) && p.t >= newest - windowMs) : rawPoints;
  if (points.length < 2) points = rawPoints.slice(-2);
  if (points.length < 2) {
    points.push(
      {ts: payload.summary.started_at || payload.generated_at, t: Date.now(), equity: payload.summary.initial_bankroll},
      {ts: payload.generated_at, t: Date.now(), equity: payload.summary.equity}
    );
  }
  const ys = points.map(p => Number(p.equity || 0));
  let min = Math.min(...ys), max = Math.max(...ys);
  if (min === max) { min -= 1; max += 1; }
  const minT = Math.min(...points.map(p => Number.isFinite(p.t) ? p.t : newest));
  const maxT = Math.max(...points.map(p => Number.isFinite(p.t) ? p.t : newest));
  const x = p => {
    if (maxT === minT) return padL;
    return padL + (rect.width - padL - padR) * ((p.t - minT) / (maxT - minT));
  };
  const y = v => rect.height - padB - (rect.height - padT - padB) * ((v - min) / (max - min));
  ctx.strokeStyle = "rgba(255,255,255,.08)";
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    const gy = padT + (rect.height - padT - padB) * i / 4;
    ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(rect.width - padR, gy); ctx.stroke();
  }
  const grad = ctx.createLinearGradient(0, padT, 0, rect.height - padB);
  grad.addColorStop(0, "rgba(46,92,255,.36)");
  grad.addColorStop(1, "rgba(46,92,255,0)");
  ctx.beginPath();
  points.forEach((p, i) => i ? ctx.lineTo(x(p), y(p.equity)) : ctx.moveTo(x(p), y(p.equity)));
  ctx.lineTo(x(points[points.length - 1]), rect.height - padB);
  ctx.lineTo(x(points[0]), rect.height - padB);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();
  ctx.beginPath();
  points.forEach((p, i) => i ? ctx.lineTo(x(p), y(p.equity)) : ctx.moveTo(x(p), y(p.equity)));
  ctx.strokeStyle = "#2E5CFF";
  ctx.lineWidth = 2;
  ctx.shadowBlur = 10;
  ctx.shadowColor = ctx.strokeStyle;
  ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.fillStyle = "#9ba3b0";
  ctx.font = "11px Inter, system-ui, sans-serif";
  ctx.fillText(money(max), 8, padT + 4);
  ctx.fillText(money(min), 8, rect.height - padB + 4);
  ctx.fillText(shortDateTime(points[0].ts), padL, rect.height - 10);
  const endLabel = shortDateTime(points[points.length - 1].ts);
  ctx.fillText(endLabel, Math.max(padL, rect.width - padR - ctx.measureText(endLabel).width), rect.height - 10);
  if (chartHoverX !== null && points.length) {
    let nearest = points[0];
    let nearestX = x(nearest);
    for (const p of points) {
      const px = x(p);
      if (Math.abs(px - chartHoverX) < Math.abs(nearestX - chartHoverX)) {
        nearest = p;
        nearestX = px;
      }
    }
    const nearestY = y(nearest.equity);
    ctx.strokeStyle = "rgba(255,255,255,.28)";
    ctx.beginPath(); ctx.moveTo(nearestX, padT); ctx.lineTo(nearestX, rect.height - padB); ctx.stroke();
    ctx.fillStyle = "#2E5CFF";
    ctx.beginPath(); ctx.arc(nearestX, nearestY, 4, 0, Math.PI * 2); ctx.fill();
    const pnl = nearest.equity - Number(payload.summary.initial_bankroll || 0);
    tooltip.innerHTML = `${shortDateTime(nearest.ts)}<br><b>${money(pnl)}</b><br><span class="muted">평가자산 ${money(nearest.equity)}</span>`;
    tooltip.style.display = "block";
    tooltip.style.left = Math.min(Math.max(8, nearestX + 10), rect.width - 150) + "px";
    tooltip.style.top = Math.max(8, nearestY - 54) + "px";
  } else {
    tooltip.style.display = "none";
  }
  const started = payload.summary.started_at ? shortDateTime(payload.summary.started_at) + "부터" : "실시간";
  setText("chart-caption", started);
}

function render(payload) {
  window.__lastPayload = payload;
  document.getElementById("lock").style.display = "none";
  setText("sys-status", payload.security.auth_required ? statusKo("LOCKED") : statusKo("OPEN"));
  const bot = payload.bot || {};
  const phase = bot.phase ? " · " + phaseKo(bot.phase) : "";
  const progress = bot.markets_total ? " " + (bot.markets_done || 0) + "/" + bot.markets_total : "";
  const next = bot.next_scan_in_seconds > 0 ? " 다음 " + duration(bot.next_scan_in_seconds) : "";
  setText("bot-status", statusKo(bot.status) + phase + progress + " " + duration(bot.age_seconds) + next);
  setText("updated", shortTime(payload.generated_at));
  setText("m-initial", money(payload.summary.initial_bankroll));
  setText("m-pnl", money(payload.summary.total_pnl));
  document.getElementById("m-pnl-box").className = "metric " + (payload.summary.total_pnl >= 0 ? "good" : "bad");
  setText("m-equity", money(payload.summary.equity));
  setText("m-wins", payload.summary.wins);
  setText("m-losses", payload.summary.losses);
  setText("m-winrate", pct(payload.summary.win_rate));
  setText("r-open", payload.summary.open_positions);
  setText("r-exposure", money(payload.summary.exposure));
  setText("r-latest-forecast", shortDateTime(payload.scanner.latest_forecast_at));
  setText("r-total-profit", money(payload.summary.realized_profit_usd || 0));
  setText("r-total-loss", money(payload.summary.realized_loss_usd || 0));
  setText("r-cash", money(payload.summary.cash));
  const forecastHealth = (payload.health || {}).forecast || {};
  setHealthStatus("r-forecast-health", forecastHealth.status);
  setText("r-forecast-success", "마지막 성공 " + shortDateTime(forecastHealth.last_success_at));
  setText("r-forecast-attempt", "마지막 시도 " + shortDateTime(forecastHealth.last_attempt_at));
  setText("r-forecast-age", "예보 저장 후 경과 " + (forecastHealth.cache_age_seconds == null ? "--" : duration(forecastHealth.cache_age_seconds)));
  setText("r-forecast-error", "최근 실패 이유 " + (forecastHealth.last_failure_reason || "--"));
  setText("r-forecast-persistence", "파일 저장 오류 " + (forecastHealth.persistence_error || "--"));
  const websocketHealth = (payload.health || {}).websocket || {};
  setHealthStatus("r-websocket-health", websocketHealth.status);
  setText("r-websocket-thread", "실시간 수신 스레드 " + (websocketHealth.thread_alive === true ? "실행중" : (websocketHealth.thread_alive === false ? "중지" : "--")));
  setText("r-websocket-reconnects", "재연결 " + Number(websocketHealth.reconnect_count || 0));
  setText("r-websocket-message", "마지막 메시지 " + shortDateTime(websocketHealth.last_message_at));
  setText("r-websocket-book", "마지막 주문장 " + shortDateTime(websocketHealth.last_book_at) + " · 경과 " + (websocketHealth.stale_book_age_seconds == null ? "--" : duration(websocketHealth.stale_book_age_seconds)));
  setText("r-websocket-error", "최근 오류 " + (websocketHealth.last_error || "--"));
  const eventPortfolio = (payload.scanner || {}).latest_event_portfolio || {};
  const selectedLegs = eventPortfolio.selected_legs || [];
  const rejectedLegs = eventPortfolio.rejected_legs || [];
  const scenarios = eventPortfolio.scenario_pnl_usd || {};
  const worstScenario = Object.values(scenarios).length ? Math.min(...Object.values(scenarios).map(Number)) : null;
  setText("r-portfolio-event", eventPortfolio.event_key || "--");
  setText("r-portfolio-budget", "최근 기준 자금 " + money(eventPortfolio.entry_bankroll_usd || 0) + " · 한도 " + money(eventPortfolio.event_cap_usd || 0) + " · 예상 순이익 " + money(eventPortfolio.expected_net_profit_usd || 0) + " · 예상 로그 성장 " + pct(eventPortfolio.expected_log_growth || 0));
  setText("r-portfolio-selected", "선택된 다리 " + (selectedLegs.length ? selectedLegs.map(item => (item.market_id || "?") + " " + sideKo(item.side || "") + " " + money(item.size_usd || 0)).join(" | ") : "--"));
  setText("r-portfolio-rejected", "거절된 다리 " + (rejectedLegs.length ? rejectedLegs.map(item => (item.market_id || "?") + ": " + (item.reason || "")).join(" | ") : "--"));
  setText("r-portfolio-scenario", "최악 시나리오 " + (worstScenario == null ? "--" : money(worstScenario)));
  setText("open-count", payload.positions.length);
  setText("trade-count", payload.recent_trades.length);
  const realizedRows = payload.realized_results || [];
  setText("realized-count", realizedRows.length);
  document.getElementById("open-positions").innerHTML = payload.positions.length ? payload.positions.map(cardForPosition).join("") : `<div class="small muted">보유 포지션이 없습니다</div>`;
  document.getElementById("realized-results").innerHTML = realizedTable(realizedRows);
  document.getElementById("recent-trades").innerHTML = payload.recent_trades.length ? payload.recent_trades.map(cardForTrade).join("") : `<div class="small muted">최근 체결 내역이 없습니다</div>`;
  drawChart(payload);
}

async function tick() {
  try {
    const res = await fetch("/api/status", {headers: token ? {"X-Dashboard-Token": token} : {}});
    if (res.status === 403) {
      document.getElementById("lock").style.display = "block";
      setText("sys-status", statusKo("AUTH"));
      return;
    }
    render(await res.json());
  } catch (err) {
    setText("sys-status", statusKo("ERROR"));
    console.error(err);
  }
}
tick();
const chartCanvas = document.getElementById("equity-chart");
chartCanvas.addEventListener("mousemove", event => {
  const rect = chartCanvas.getBoundingClientRect();
  chartHoverX = event.clientX - rect.left;
  drawChart(window.__lastPayload || {summary:{equity:0,total_pnl:0,initial_bankroll:0}, equity_points:[]});
});
chartCanvas.addEventListener("mouseleave", () => {
  chartHoverX = null;
  drawChart(window.__lastPayload || {summary:{equity:0,total_pnl:0,initial_bankroll:0}, equity_points:[]});
});
document.querySelectorAll(".range-btn").forEach(button => {
  button.addEventListener("click", () => {
    chartRange = button.dataset.range || "ALL";
    document.querySelectorAll(".range-btn").forEach(item => item.classList.toggle("active", item === button));
    drawChart(window.__lastPayload || {summary:{equity:0,total_pnl:0,initial_bankroll:0}, equity_points:[]});
  });
});
document.querySelectorAll(".tab-btn").forEach(button => {
  button.addEventListener("click", () => {
    const targetId = button.dataset.tabTarget || "scanner-panel";
    document.querySelectorAll(".tab-btn").forEach(item => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".tab-panel").forEach(panel => {
      panel.classList.toggle("active", panel.id === targetId);
    });
  });
});
let refreshTimer = null;
function scheduleRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(tick, document.hidden ? 30000 : 5000);
}
scheduleRefresh();
document.addEventListener("visibilitychange", () => { scheduleRefresh(); tick(); });
addEventListener("resize", () => drawChart(window.__lastPayload || {summary:{equity:0,total_pnl:0,initial_bankroll:0}, equity_points:[]}));
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


def _forecast_unavailable(row: dict[str, str]) -> bool:
    text = f"{row.get('reason') or ''} {row.get('note') or ''}".lower()
    return "forecast unavailable" in text or "no forecast" in text


def _count_decision_row(totals: dict[str, int], row: dict[str, str]) -> None:
    if not any((value or "").strip() for value in row.values()):
        return
    side = (row.get("side") or "").upper()
    totals["decisions"] += 1
    if side == "SKIP":
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
            "fieldnames": [],
            "offset": 0,
            "pending": b"",
            "mtime_ns": 0,
        }
    return {
        "totals": totals,
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


def _decision_totals(path: Path) -> dict[str, int]:
    if not path.exists():
        return _empty_decision_totals()
    key = str(path.resolve())
    with _DECISION_TOTALS_LOCK:
        try:
            stat = path.stat()
        except OSError:
            return _empty_decision_totals()
        cache = _DECISION_TOTALS_CACHE.get(key)
        cache_offset = int(cache.get("offset", 0)) if cache else 0
        cache_mtime_ns = int(cache.get("mtime_ns", 0)) if cache else 0
        if cache is None or stat.st_size < cache_offset or (stat.st_size == cache_offset and stat.st_mtime_ns != cache_mtime_ns):
            if cache is None and stat.st_size > MAX_INITIAL_DECISION_TOTAL_SCAN_BYTES:
                cache = _recent_decision_totals_cache(path, stat.st_size, stat.st_mtime_ns)
            else:
                cache = _scan_decision_totals(path, stat.st_size, stat.st_mtime_ns)
            _DECISION_TOTALS_CACHE[key] = cache
            return dict(cache["totals"])
        if stat.st_size > cache_offset:
            try:
                with path.open("rb") as f:
                    f.seek(cache_offset)
                    chunk = f.read(stat.st_size - cache_offset)
            except OSError:
                return dict(cache["totals"])
            _add_appended_decisions(cache, chunk)
            cache["offset"] = stat.st_size
            cache["mtime_ns"] = stat.st_mtime_ns
        return dict(cache["totals"])


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
    if action == "OPEN":
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
    if action == "OPEN" and market_id:
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
    return max(1, int(settings.forecast_refresh_interval_seconds))


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
) -> dict[str, Any]:
    metadata = pos.get("metadata") if isinstance(pos.get("metadata"), dict) else {}
    latest_decision = latest_decision or {}
    entry = _float(pos.get("entry_price"))
    shares = _float(pos.get("shares"))
    cost = _float(pos.get("cost_usd"))
    mark = _float(pos.get("last_mark_price"), entry)
    exit_fee_usdc = polymarket_taker_fee_usdc(shares, mark, fee_rate)
    value = shares * mark - exit_fee_usdc
    slug = metadata.get("slug") or latest_decision.get("slug") or ""
    event_slug = metadata.get("event_slug") or latest_decision.get("event_slug") or ""
    forecast_c = _forecast_c_from_note(latest_decision.get("note", ""))
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
        "forecast_c": forecast_c,
        "opened_at": pos.get("opened_at", ""),
        "city": metadata.get("city", ""),
        "date_hint": metadata.get("date_hint", ""),
        "target_exit_price": _float(metadata.get("last_target_exit_price"), _float(metadata.get("target_exit_price"))),
        "probability_stop_threshold": _float(metadata.get("probability_stop_threshold")),
        "reason": metadata.get("reason", ""),
    }


def _optional_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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
            "actual_opens": int(trade_totals["opens"]),
            "actual_closes": int(trade_totals["closes"]),
            "latest_forecast_at": _latest_forecast_cache_at(settings),
            "latest_event_portfolio": event_portfolios[-1] if event_portfolios else {},
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
    token_note = "enabled" if token else "disabled"
    print(f"Weather dashboard listening on http://{host}:{port} auth={token_note}")
    server.serve_forever()


def main() -> None:
    run_dashboard(load_settings())


if __name__ == "__main__":
    main()
