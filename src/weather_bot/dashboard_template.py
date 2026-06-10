"""Static HTML/CSS/JS for the read-only operator dashboard."""

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
    .realized-list { max-height: min(48vh, 520px); overflow: auto; padding: 10px; }
    .trade-list { min-height: 0; align-content: start; }
    .pos-row {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      align-items: center;
      margin-top: 6px;
    }
    .city-cards-section {
      margin-top: 10px;
    }
    .city-cards-title {
      font-size: 11px;
      font-weight: 700;
      color: var(--muted);
      letter-spacing: .03em;
      margin-bottom: 6px;
      padding: 0 2px;
    }
    .city-cards-list {
      display: grid;
      gap: 6px;
      max-height: 260px;
      overflow-y: auto;
      padding-right: 2px;
    }
    .city-card {
      border: 1px solid var(--line);
      background: var(--panel-3);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 11px;
      line-height: 1.5;
    }
    .city-card.ok { border-left: 3px solid var(--green); }
    .city-card.fail { border-left: 3px solid var(--red); }
    .city-card.warn { border-left: 3px solid var(--yellow); }
    .city-card-row { display: flex; justify-content: space-between; gap: 6px; }
    .city-card-row .city-name { font-weight: 800; color: var(--text); }
    .city-card-row .city-status-ok { color: var(--green); font-weight: 700; }
    .city-card-row .city-status-fail { color: var(--red); font-weight: 700; }
    .city-card-detail { color: var(--muted); margin-top: 2px; overflow-wrap: anywhere; }
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
  <div id="lock" class="lock">대시보드 토큰이 없거나 맞지 않습니다. 브라우저에 저장된 토큰을 확인하세요.</div>
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
        <div class="right-stat"><span>최근 예보 갱신</span><strong id="r-latest-forecast" class="neutral">--</strong></div>
        <div class="right-stat"><span>총 이익</span><strong id="r-total-profit">$0</strong></div>
        <div class="right-stat"><span>총 손실</span><strong id="r-total-loss" class="bad">$0</strong></div>
        <div class="right-stat"><span>남은 현금</span><strong id="r-cash">$0</strong></div>
        <div class="health-box">
          <div class="health-title"><span>예보 상태 (Open-Meteo)</span><strong id="r-forecast-health">--</strong></div>
          <div id="r-forecast-success" class="health-detail">마지막 성공 --</div>
          <div id="r-forecast-age" class="health-detail">다음 갱신까지 --</div>
          <div id="r-forecast-error" class="health-detail">최근 실패 이유 --</div>
        </div>
        <div class="city-cards-section">
          <div class="city-cards-title">🌤 도시별 예보 호출 기록</div>
          <div id="r-forecast-cities" class="city-cards-list"><div class="small muted">로딩 중…</div></div>
        </div>
        <div class="health-box">
          <div class="health-title"><span>실시간 주문장 상태</span><strong id="r-websocket-health">--</strong></div>
          <div id="r-websocket-thread" class="health-detail">실시간 수신 스레드 --</div>
          <div id="r-websocket-reconnects" class="health-detail">재연결 --</div>
          <div id="r-websocket-message" class="health-detail">마지막 메시지 --</div>
          <div id="r-websocket-book" class="health-detail">마지막 주문장 --</div>
          <div id="r-websocket-error" class="health-detail">최근 오류 --</div>
        </div>
        <div class="city-cards-section">
          <div class="city-cards-title">🛰 도시별 관측소 호출 기록</div>
          <div id="r-nowcast-cities" class="city-cards-list"><div class="small muted">로딩 중…</div></div>
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
  "ADD": "추매",
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
  const pnl = p.unrealized_pnl || 0;
  const pnlClass = pnl >= 0 ? "win" : "loss";
  const pnlSign = pnl >= 0 ? "+" : "";
  const sideLabel = (p.side || "").toUpperCase() === "YES" ? "Yes" : "No";
  const qLower = (p.question || "").toLowerCase();
  const isHighest = qLower.includes("highest");
  // p_true = YES probability; side probability = what the bot is betting on
  const sideProbPct = p.p_true != null
    ? ((p.side || "").toUpperCase() === "YES" ? p.p_true : (1 - p.p_true)) * 100
    : null;
  const titleHtml = p.market_url
    ? `<a class="market-title market-link" href="${esc(p.market_url)}" target="_blank" rel="noopener noreferrer">${esc(p.question)}</a>`
    : `<div class="market-title">${esc(p.question)}</div>`;
  return `<div class="card open">
    ${titleHtml}
    <div class="pos-row">
      <span class="badge ${(p.side||'').toUpperCase() === 'YES' ? 'yes' : 'no'}">${sideLabel}</span>
      <span class="badge long">Long</span>
      ${p.forecast_c != null ? `<span class="badge forecast">예보 ${tempC(p.forecast_c)}</span>` : ''}
      ${sideProbPct != null ? `<span class="badge neutral">확률 ${sideProbPct.toFixed(0)}%</span>` : ''}
    </div>
    <div class="pos-row">
      <span class="badge price">진입 ${price(p.entry_price)}</span>
      <span class="badge price">현재가 ${price(p.mark_price)}</span>
      <span class="badge ${pnlClass}">${pnlSign}${money(Math.abs(pnl))}</span>
    </div>
    <div class="small muted" style="margin-top:8px">
      ${esc(p.city || "")} ${esc(p.date_hint || "")} · 수량 ${Number(p.shares || 0).toFixed(2)} · 비용 ${money(p.cost_usd)}
      ${p.entry_fee_usdc != null ? ` · 수수료 $${Number(p.entry_fee_usdc).toFixed(4)}` : ''}
      ${p.net_edge != null ? ` · 엣지 ${(Number(p.net_edge)*100).toFixed(1)}%` : ''}
    </div>
  </div>`;
}
function cardForTrade(t) {
  const action = String(t.action || "");
  const isClose = action.includes("CLOSE") || action.includes("SETTLE");
  const pnl = Number(t.cash_delta_or_pnl || 0);
  const sideLabel = (t.side || "").toUpperCase() === "YES" ? "Yes" : "No";
  const actionLabel = actionKo(action);
  return `<div class="card ${isClose ? (pnl >= 0 ? 'profit' : 'loss') : 'open'}">
    <div class="market-title">${esc(t.question)}</div>
    <div class="pos-row">
      <span class="badge neutral">${esc(actionLabel)}</span>
      <span class="badge ${(t.side||'').toUpperCase() === 'YES' ? 'yes' : 'no'}">${sideLabel}</span>
      <span class="badge long">Long</span>
      <span class="badge price">${price(t.price)}</span>
      <span class="badge ${pnl >= 0 ? 'win' : 'loss'}">${pnl >= 0 ? '+' : ''}${money(Math.abs(pnl))}</span>
    </div>
    <div class="small muted" style="margin-top:6px">${esc(t.reason || "")}</div>
  </div>`;
}

function _buildReasonKo(reason) {
  // Parse entry: model_p=X, side=Y, p_exec=Z ... into Korean
  if (!reason) return "";
  const lines = [];
  const mp = reason.match(/model_p=([\d.]+)/);
  const pe = reason.match(/p_exec=([\d.]+)/);
  const ne = reason.match(/net_edge=([\d.]+)/);
  const br = reason.match(/bankroll=\$([\d.]+)/);
  const ef = reason.match(/entry_fraction=([\d.]+)%/);
  const ps = reason.match(/probability_stop=([\d.]+)/);
  const mf = reason.match(/model_fair=([\d.]+)/);
  const te = reason.match(/target_exit=([\d.]+)/);
  const fee = reason.match(/entry_fee=\$([\d.]+)/);
  if (mp) lines.push(`YES 확률: ${(parseFloat(mp[1])*100).toFixed(1)}%`);
  if (pe) lines.push(`체결가: ${(parseFloat(pe[1])*100).toFixed(1)}¢`);
  if (mf) lines.push(`봇 공정가: ${(parseFloat(mf[1])*100).toFixed(1)}¢`);
  if (ne) lines.push(`엣지(유리함): ${(parseFloat(ne[1])*100).toFixed(1)}%`);
  if (te) lines.push(`목표 익절가: ${(parseFloat(te[1])*100).toFixed(1)}¢`);
  if (ps) lines.push(`손절 트리거 확률: ${(parseFloat(ps[1])*100).toFixed(1)}%`);
  if (ef) lines.push(`투자 비율: ${ef[1]}%`);
  if (br) lines.push(`자본금: $${br[1]}`);
  if (fee) lines.push(`수수료: $${fee[1]}`);
  return lines.join(" · ");
}

function realizedCards(rows) {
  if (!rows.length) return `<div class="small muted">확정된 거래가 없습니다</div>`;
  return rows.map(r => {
    const pnl = Number(r.pnl || 0);
    const isProfit = pnl > 0;
    const resultLabel = isProfit ? "수익" : "손절";
    const cardClass = isProfit ? "profit" : "loss";
    const exitLabel = isProfit ? "익절가" : "손절가";
    const sideLabel = (r.side || "").toUpperCase() === "YES" ? "Yes" : "No";
    const pnlSign = isProfit ? "+" : "";
    // side probability from p_true
    const sideProbPct = r.p_true != null
      ? ((r.side || "").toUpperCase() === "YES" ? r.p_true : (1 - r.p_true)) * 100
      : null;
    const reasonKo = _buildReasonKo(r.reason || "");
    // Human-readable reason for close
    let closeReason = r.reason || "";
    // Strip the entry: prefix if present, show clean close reason
    const closePart = closeReason.replace(/^entry:[^;]*;?\s*/i, "").trim();
    return `<div class="card ${cardClass}">
      <div class="market-title">${esc(r.question || "")}</div>
      <div class="pos-row">
        <span class="badge ${isProfit ? 'win' : 'loss'}">${resultLabel}</span>
        <span class="badge ${(r.side||'').toUpperCase() === 'YES' ? 'yes' : 'no'}">${sideLabel}</span>
        <span class="badge long">Long</span>
        ${r.forecast_c ? `<span class="badge forecast">예보 ${tempC(r.forecast_c)}</span>` : ''}
        ${sideProbPct != null ? `<span class="badge neutral">확률 ${sideProbPct.toFixed(0)}%</span>` : ''}
      </div>
      <div class="pos-row">
        <span class="badge price">진입 ${price(r.entry_price)}</span>
        <span class="badge price">${exitLabel} ${price(r.exit_price)}</span>
        <span class="badge ${isProfit ? 'win' : 'loss'}">${pnlSign}${money(Math.abs(pnl))}</span>
        <span class="badge neutral">수익률 ${roi(r.roi)}</span>
      </div>
      ${reasonKo ? `<div class="small muted" style="margin-top:6px">${esc(reasonKo)}</div>` : ''}
      ${closePart ? `<div class="small muted" style="margin-top:4px">이유: ${esc(closePart)}</div>` : ''}
      <div class="small muted" style="margin-top:4px">${esc(r.city || '')} ${esc(r.date_hint || '')} · ${shortDateTime(r.closed_at)}</div>
    </div>`;
  }).join("");
}

function cityForecastCard(c) {
  const ok = (c.status || "").toUpperCase() === "SUCCESS" || (c.status || "").toUpperCase() === "HIT";
  const cls = ok ? "ok" : ((c.error || c.unavailable_reason) ? "fail" : "warn");
  const statusText = ok ? "✓ 성공" : ("✗ 실패");
  const ts = shortDateTime(c.attempted_at || "");
  const err = c.error || c.unavailable_reason || "";
  return `<div class="city-card ${cls}">
    <div class="city-card-row">
      <span class="city-name">${esc(c.city || c.station_id || "?")}</span>
      <span class="${ok ? 'city-status-ok' : 'city-status-fail'}">${statusText}</span>
    </div>
    <div class="city-card-detail">${ts}${err ? " · " + esc(err.slice(0, 60)) : ""}</div>
  </div>`;
}

function cityNowcastCard(c) {
  const ok = (c.status || "").toUpperCase() === "SUCCESS" || (c.status || "").toUpperCase() === "HIT";
  const cls = ok ? "ok" : ((c.error || c.unavailable_reason) ? "fail" : "warn");
  const statusText = ok ? "✓ 성공" : "✗ 실패";
  const ts = shortDateTime(c.requested_at || "");
  const err = c.error || c.unavailable_reason || "";
  const stn = c.station_name ? ` (${c.station_name})` : "";
  return `<div class="city-card ${cls}">
    <div class="city-card-row">
      <span class="city-name">${esc(c.city || c.station_id || "?")}</span>
      <span class="${ok ? 'city-status-ok' : 'city-status-fail'}">${statusText}</span>
    </div>
    <div class="city-card-detail">${ts}${esc(stn)}${err ? " · " + esc(err.slice(0, 60)) : ""}</div>
  </div>`;
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
  const ttl = 10800; // 3h forecast cache TTL
  const age = forecastHealth.cache_age_seconds;
  const remaining = age != null ? Math.max(0, ttl - age) : null;
  setText("r-forecast-age", "다음 갱신까지 " + (remaining != null ? duration(remaining) : "--"));
  setText("r-forecast-error", "최근 실패 이유 " + (forecastHealth.last_failure_reason || "--"));
  const websocketHealth = (payload.health || {}).websocket || {};
  setHealthStatus("r-websocket-health", websocketHealth.status);
  setText("r-websocket-thread", "실시간 수신 스레드 " + (websocketHealth.thread_alive === true ? "실행중" : (websocketHealth.thread_alive === false ? "중지" : "--")));
  setText("r-websocket-reconnects", "재연결 " + Number(websocketHealth.reconnect_count || 0));
  setText("r-websocket-message", "마지막 메시지 " + shortDateTime(websocketHealth.last_message_at));
  setText("r-websocket-book", "마지막 주문장 " + shortDateTime(websocketHealth.last_book_at) + " · 경과 " + (websocketHealth.stale_book_age_seconds == null ? "--" : duration(websocketHealth.stale_book_age_seconds)));
  setText("r-websocket-error", "최근 오류 " + (websocketHealth.last_error || "--"));
  // Per-city forecast cards
  const forecastCities = (payload.scanner || {}).per_city_forecast || [];
  document.getElementById("r-forecast-cities").innerHTML = forecastCities.length
    ? forecastCities.map(cityForecastCard).join("")
    : `<div class="small muted">예보 호출 기록 없음</div>`;
  // Per-city nowcast cards
  const nowcastCities = (payload.scanner || {}).per_city_nowcast || [];
  document.getElementById("r-nowcast-cities").innerHTML = nowcastCities.length
    ? nowcastCities.map(cityNowcastCard).join("")
    : `<div class="small muted">관측소 호출 기록 없음</div>`;
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
