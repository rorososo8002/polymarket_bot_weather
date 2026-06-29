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
    .city-entry-list { max-height: 330px; }
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
    .market-subtitle {
      color: var(--muted);
      font-size: 11px;
      line-height: 1.45;
      margin: -3px 0 8px;
      overflow-wrap: anywhere;
    }
    .position-heading {
      display: grid;
      gap: 3px;
      margin-bottom: 8px;
    }
    .position-title-main {
      color: var(--text);
      font-size: 13px;
      line-height: 1.35;
      font-weight: 800;
      text-decoration: none;
      overflow-wrap: anywhere;
    }
    .position-title-main:hover { color: var(--blue); }
    .position-title-meta {
      color: var(--muted);
      font-size: 11px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
    .detail-line {
      color: var(--muted);
      font-size: 11px;
      line-height: 1.5;
      margin-top: 6px;
      overflow-wrap: anywhere;
    }
    .detail-line strong { color: var(--text); font-weight: 800; }
    .reference-line {
      color: var(--muted-2);
      font-size: 10px;
      line-height: 1.45;
      margin-top: 4px;
      overflow-wrap: anywhere;
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
    .badge.current-price { color: var(--blue); border-color: rgba(46,92,255,.46); background: var(--blue-soft); }
    .badge.obs-temp { color: #5de0c0; border-color: rgba(93,224,192,.42); background: rgba(93,224,192,.12); }
    .badge.muted-badge { color: var(--muted-2); border-color: var(--line); background: transparent; font-style: italic; }
    .reason-box {
      margin-top: 8px;
      padding: 8px 9px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: rgba(8, 10, 15, .38);
      color: var(--muted);
      font-size: 11px;
      line-height: 1.5;
      overflow-wrap: anywhere;
    }
    .reason-box b { color: var(--text); }
    .reason-facts {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      margin-top: 6px;
    }
    .realized-fact-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(92px, 1fr));
      gap: 6px;
      margin-top: 8px;
    }
    .fact {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 6px 7px;
      background: rgba(27, 32, 48, .72);
    }
    .fact span {
      display: block;
      color: var(--muted-2);
      font-size: 9px;
      font-weight: 800;
      letter-spacing: .02em;
      margin-bottom: 3px;
    }
    .fact strong {
      display: block;
      color: var(--text);
      font-size: 12px;
      line-height: 1.2;
      overflow-wrap: anywhere;
    }
    .logs-panel-body { min-height: 0; overflow: hidden; display: grid; grid-template-columns: 1fr 1fr; gap: 10px; height: 100%; padding: 10px; }
    .logs-col-title { font-size: 11px; font-weight: 700; color: var(--muted); letter-spacing: .03em; margin-bottom: 6px; padding: 0 2px; border-bottom: 1px solid var(--line); padding-bottom: 6px; }
    .logs-col-list { overflow-y: auto; max-height: calc(100vh - 150px); display: grid; gap: 6px; align-content: start; }
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
    .stream-activity {
      margin-top: 6px;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: rgba(8, 10, 15, .34);
      display: grid;
      gap: 7px;
    }
    .stream-activity-head {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      font-size: 11px;
      color: var(--muted);
      font-weight: 800;
    }
    .stream-activity-head strong { color: var(--yellow); }
    .stream-activity-head strong.good { color: var(--green); }
    .stream-activity-head strong.bad { color: var(--red); }
    .stream-bars {
      display: grid;
      grid-template-columns: repeat(14, 1fr);
      gap: 3px;
      align-items: end;
      height: 36px;
    }
    .stream-bars span {
      display: block;
      min-width: 0;
      height: 12px;
      border-radius: 2px;
      background: var(--line-strong);
      opacity: .52;
    }
    .stream-activity.live .stream-bars span {
      background: linear-gradient(180deg, #5de0c0, var(--green));
      animation: streamPulse 1.15s ease-in-out infinite;
    }
    .stream-activity.live .stream-bars span:nth-child(2n) { animation-delay: .12s; }
    .stream-activity.live .stream-bars span:nth-child(3n) { animation-delay: .24s; }
    .stream-activity.live .stream-bars span:nth-child(4n) { animation-delay: .36s; }
    .stream-activity.live .stream-bars span:nth-child(5n) { animation-delay: .48s; }
    @keyframes streamPulse {
      0%, 100% { height: 10px; opacity: .45; }
      45% { height: 34px; opacity: 1; }
    }
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
      white-space: nowrap;
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
      .chart-title { align-items: flex-start; flex-direction: column; }
      .range-controls {
        width: 100%;
        margin-left: 0;
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
      }
      .range-btn { padding: 5px 4px; }
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
        <button id="scanner-tab" class="tab-btn active" type="button" role="tab" aria-selected="true" aria-controls="scanner-panel" data-tab-target="scanner-panel">관측소 감시</button>
        <button id="trades-tab" class="tab-btn" type="button" role="tab" aria-selected="false" aria-controls="trades-panel" data-tab-target="trades-panel">최근 체결 <span id="trade-count">0</span></button>
        <button id="logs-tab" class="tab-btn" type="button" role="tab" aria-selected="false" aria-controls="logs-panel" data-tab-target="logs-panel">관측 신호&amp;스킵</button>
      </div>
      <div class="right-panels">
        <div id="scanner-panel" class="tab-panel active" role="tabpanel" aria-labelledby="scanner-tab">
          <div class="panel-body scanner-body">
        <div class="right-stat"><span>보유 포지션</span><strong id="r-open">0</strong></div>
        <div class="right-stat"><span>총 진입 비용</span><strong id="r-exposure">$0</strong></div>
        <div class="right-stat"><span>최근 관측 성공</span><strong id="r-latest-station" class="neutral">--</strong></div>
        <div class="right-stat"><span>총 손익</span><strong id="r-net-profit">$0</strong></div>
        <div class="right-stat"><span>수익 현황</span><strong id="r-total-profit" class="">$0</strong></div>
        <div class="right-stat"><span>손실 현황</span><strong id="r-total-loss" class="bad">$0</strong></div>
        <div class="right-stat"><span>매매가능현금</span><strong id="r-cash">$0</strong></div>
        <div class="city-cards-section">
          <div class="city-cards-title">도시별 진입 박스</div>
          <div id="r-city-entries" class="city-cards-list city-entry-list"><div class="small muted">로딩 중…</div></div>
        </div>
        <div class="health-box">
          <div class="health-title"><span>공식 관측소 수신 상태</span><strong id="r-station-health">--</strong></div>
          <div id="r-station-success" class="health-detail">마지막 성공 --</div>
          <div id="r-station-age" class="health-detail">관측값 경과 --</div>
          <div id="r-station-error" class="health-detail">최근 실패 이유 --</div>
        </div>
        <div class="health-box">
          <div class="health-title"><span>실시간 주문장 상태</span><strong id="r-websocket-health">--</strong></div>
          <div id="r-websocket-thread" class="health-detail">실시간 수신 스레드 --</div>
          <div id="r-websocket-reconnects" class="health-detail">재연결 --</div>
          <div id="r-websocket-message" class="health-detail">마지막 메시지 --</div>
          <div id="r-websocket-book" class="health-detail">마지막 주문장 --</div>
          <div id="r-websocket-error" class="health-detail">최근 오류 --</div>
          <div id="r-stream-activity" class="stream-activity">
            <div class="stream-activity-head">
              <span>실시간 감시 활동</span>
              <strong id="r-stream-pulse">대기</strong>
            </div>
            <div class="stream-bars" aria-hidden="true">
              <span></span><span></span><span></span><span></span><span></span><span></span><span></span>
              <span></span><span></span><span></span><span></span><span></span><span></span><span></span>
            </div>
            <div id="r-stream-summary" class="health-detail">감시 대상 --</div>
            <div id="r-stream-reason" class="health-detail">상태 설명 --</div>
          </div>
        </div>
        <div class="city-cards-section">
          <div class="city-cards-title">공식 관측소 최근 호출</div>
          <div id="r-station-observations" class="city-cards-list"><div class="small muted">로딩 중…</div></div>
        </div>
        <div class="city-cards-section">
          <div class="city-cards-title">공식 관측소 목록</div>
          <div id="r-station-registry" class="city-cards-list"><div class="small muted">로딩 중…</div></div>
        </div>
          </div>
        </div>

        <div id="trades-panel" class="tab-panel" role="tabpanel" aria-labelledby="trades-tab">
          <div class="panel-body recent-trades-body"><div id="recent-trades" class="trade-list"></div></div>
        </div>
        <div id="logs-panel" class="tab-panel" role="tabpanel" aria-labelledby="logs-tab">
          <div class="logs-panel-body">
            <div style="display:flex;flex-direction:column;min-height:0">
              <div class="logs-col-title">관측소 잠금 신호</div>
              <div id="r-station-signals" class="logs-col-list"><div class="small muted">로딩 중…</div></div>
            </div>
            <div style="display:flex;flex-direction:column;min-height:0">
              <div class="logs-col-title">최근 스킵</div>
              <div id="r-recent-skips" class="logs-col-list"><div class="small muted">로딩 중…</div></div>
            </div>
          </div>
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
function qty(v) {
  return (v === null || v === undefined || isNaN(v)) ? "--" : Number(v).toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 2});
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
const KST_OPTS = {timeZone: "Asia/Seoul"};
function shortTime(ts) {
  if (!ts) return "--";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts).slice(11, 19);
  return d.toLocaleTimeString("ko-KR", {...KST_OPTS, hour12:false});
}
function shortDateTime(ts) {
  if (!ts) return "--";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleString("ko-KR", {...KST_OPTS, month:"short", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false});
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
function exitStatusKo(status) {
  const raw = String(status || "").toLowerCase();
  if (raw === "full") return "전량 가능";
  if (raw === "partial") return "부분 가능";
  if (raw === "blocked") return "청산 막힘";
  return "확인 필요";
}
function exitStatusClass(status) {
  const raw = String(status || "").toLowerCase();
  if (raw === "full") return "win";
  if (raw === "partial") return "price";
  if (raw === "blocked") return "loss";
  return "muted-badge";
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

function sidePositionKo(side) {
  const raw = String(side || "").toUpperCase();
  if (raw === "YES") return "Yes";
  if (raw === "NO") return "No";
  return raw || "--";
}
function bucketLabelFromFields(threshold, condition) {
  if (threshold === null || threshold === undefined || isNaN(Number(threshold))) return "--";
  const suffix = conditionKo(condition);
  return tempC(threshold) + (suffix ? " " + suffix : "");
}
function nowcastUnavailableKo(reason) {
  const raw = String(reason || "").toLowerCase();
  if (!raw) return "관측값 대기";
  if (raw === "target-date-not-today") return "대상일 전이라 대기";
  if (raw === "station-cache-miss") return "관측 캐시 대기";
  if (raw === "station-fetch-failed") return "관측 호출 실패";
  if (raw === "unsupported-station") return "지원 관측소 아님";
  if (raw === "malformed-station-data") return "관측값 형식 오류";
  return raw.replace(/[-_]/g, " ");
}
function nowcastUnavailableDetail(reason) {
  const raw = String(reason || "").toLowerCase();
  if (raw === "target-date-not-today") {
    return "대상 날짜가 아직 해당 도시 기준 오늘이 아니라서 관측소 최고/최저값을 일부러 쓰지 않았습니다.";
  }
  if (raw === "station-fetch-failed") return "관측소 API 호출이 실패해서 이 값은 추측하지 않고 대기합니다.";
  if (raw === "station-cache-miss") return "아직 쓸 수 있는 관측 캐시가 없어 다음 관측 호출을 기다립니다.";
  if (!raw) return "아직 최신 의사결정 기록에 관측값이 없습니다.";
  return nowcastUnavailableKo(raw);
}
function stationLockLabel(value) {
  const raw = String(value || "").toLowerCase();
  if (raw === "strong_no") return "강한 No 확정";
  if (raw === "strong_yes") return "강한 Yes 후보";
  if (raw === "base_yes") return "Yes 후보";
  return "관측 신호 대기";
}
function stationLockClass(value) {
  const raw = String(value || "").toLowerCase();
  if (raw === "strong_no") return "no";
  if (raw.includes("yes")) return "yes";
  return "muted-badge";
}
function stationBoundaryText(row) {
  const parts = [];
  if (row.station_settlement_boundary_c != null) parts.push(`정산 경계 ${tempC(row.station_settlement_boundary_c)}`);
  if (row.station_buffer_c != null) parts.push(`경계 여유 ${tempC(row.station_buffer_c)}`);
  if (row.station_hours_to_close != null) parts.push(`마감까지 ${duration(Number(row.station_hours_to_close) * 3600)}`);
  return parts.join(" · ");
}
function streamStatusClass(status) {
  const raw = String(status || "").toUpperCase();
  if (raw === "HEALTHY") return "good";
  if (raw === "FAILED") return "bad";
  if (raw === "STALE" || raw === "DEGRADED") return "warn";
  return "";
}
function closeReasonParts(reason) {
  const text = String(reason || "");
  const closePart = text.replace(/^entry:[^;]*;?\s*/i, "").trim();
  const source = closePart || text;
  if (!source) return {summary: "", facts: []};
  const facts = [];
  const trigger = closeTrigger(source);
  const observedHigh = (source.match(/observed_high_c=([\d.\-]+)/i) || [])[1];
  const observedLow = (source.match(/observed_low_c=([\d.\-]+)/i) || [])[1];
  const pTrue = (source.match(/p_true=([\d.]+)/i) || [])[1];
  const sideProb = source.match(/side_probability\s+([\d.]+)->([\d.]+)/i);
  const stopThreshold = (source.match(/threshold=([\d.]+)/i) || [])[1];
  const probabilityDrop = (source.match(/drop=([\d.]+)/i) || [])[1];
  const netPnl = (source.match(/net_pnl=([-+]?\d+(?:\.\d+)?)%/i) || [])[1];
  const exitFee = (source.match(/exit_fee=\$([\d.]+)/i) || [])[1];
  const gross = (source.match(/gross=\$([\d.]+)/i) || [])[1];
  const net = (source.match(/net=\$([\d.]+)/i) || [])[1];
  let summary = source;
  if (trigger === "nowcast_bucket_lock_risk" || source.includes("nowcast bucket lock risk")) {
    if (source.includes("NO held")) {
      summary = "관측 최고기온이 선택한 온도칸 안에 들어왔습니다. 우리는 NO를 들고 있었기 때문에, 이 온도칸이 맞을 위험이 커져 손절했습니다.";
    } else if (source.includes("YES held")) {
      summary = "관측값이 선택한 온도칸을 벗어나기 시작했습니다. 우리는 YES를 들고 있었기 때문에, 더 큰 손실을 막으려고 정리했습니다.";
    } else {
      summary = "관측소 현재값 때문에 선택한 온도칸의 손실 위험이 커져 포지션을 정리했습니다.";
    }
  } else if (trigger) {
    if (trigger === "probability_stop") {
      if (sideProb) {
        summary = `진입 때 이 포지션 방향의 관측소 잠금 점수는 ${probPct(sideProb[1])}였는데, 최신 관측소 근거에서 ${probPct(sideProb[2])}까지 내려갔습니다. 방어선 ${probPct(stopThreshold)} 이하라서 수익을 키우는 익절이 아니라 더 큰 손실을 막기 위한 방어청산입니다.`;
      } else {
        summary = "관측소 잠금 점수가 보유 방향과 반대로 약해져 방어선 아래로 내려갔습니다. 수익을 키우는 익절이 아니라 더 큰 손실을 막기 위한 방어청산입니다.";
      }
    } else if (trigger === "take_profit") {
      summary = "가격이 봇이 계산한 목표익절가에 도달했고, 수수료를 뺀 순수익률이 최소 익절 기준을 넘어 포지션을 정리했습니다.";
    } else if (trigger === "overheated_take_profit") {
      summary = "시장 매수가가 봇이 계산한 공정가보다 과하게 비싸졌고, 수수료를 뺀 순수익률이 최소 익절 기준을 넘어 과열익절했습니다.";
    } else if (trigger === "edge_faded") {
      summary = "처음 진입할 때 있던 우위가 사라졌습니다. 손실 제한 안에서 더 오래 들고 갈 이유가 약해져 정리했습니다.";
    } else if (trigger === "max_holding") {
      summary = "정해둔 최대 보유 시간을 넘겨 포지션을 정리했습니다.";
    } else {
      summary = `청산 조건 ${trigger} 때문에 포지션을 정리했습니다.`;
    }
  }
  if (observedHigh !== undefined) facts.push(`관측 최고 ${tempC(parseFloat(observedHigh))}`);
  if (observedLow !== undefined) facts.push(`관측 최저 ${tempC(parseFloat(observedLow))}`);
  if (pTrue !== undefined) facts.push(`관측소 YES 점수 ${(parseFloat(pTrue) * 100).toFixed(1)}%`);
  if (sideProb) facts.push(`포지션 방향확률 ${probPct(sideProb[1])} → ${probPct(sideProb[2])}`);
  if (stopThreshold !== undefined) facts.push(`방어선 ${probPct(stopThreshold)}`);
  if (probabilityDrop !== undefined) facts.push(`확률하락 ${probPct(probabilityDrop)}`);
  if (netPnl !== undefined) facts.push(`순수익률 ${parseFloat(netPnl).toFixed(1)}%`);
  if (gross !== undefined) facts.push(`청산 전 금액 $${parseFloat(gross).toFixed(2)}`);
  if (exitFee !== undefined) facts.push(`청산 수수료 $${parseFloat(exitFee).toFixed(4)}`);
  if (net !== undefined) facts.push(`순수령 $${parseFloat(net).toFixed(2)}`);
  return {summary, facts};
}

function closeTrigger(reason) {
  return (String(reason || "").match(/exit_trigger=([^;]+)/i) || [])[1] || "";
}

function defensiveCloseTrigger(trigger) {
  return ["probability_stop", "edge_faded", "max_holding", "nowcast_bucket_lock_risk"].includes(String(trigger || ""));
}

function probPct(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "--";
}

function probabilityAuditLine(row) {
  const parts = [];
  const rawProbability = Number(row.raw_selected_side_probability);
  const calibratedProbability = Number(row.selected_side_probability);
  const hasRawProbability = row.raw_selected_side_probability != null && Number.isFinite(rawProbability);
  const hasCalibratedProbability = row.selected_side_probability != null && Number.isFinite(calibratedProbability);
  if (hasRawProbability || hasCalibratedProbability) {
    parts.push(`원확률 ${hasRawProbability ? probPct(rawProbability) : "--"} → 보정확률 ${hasCalibratedProbability ? probPct(calibratedProbability) : "--"}`);
  }
  const tier = String(row.probability_tier || "").trim();
  if (tier && tier.toLowerCase() !== "unknown") parts.push(`신뢰등급 ${tier}`);
  const sampleDays = Number(row.calibration_sample_days);
  if (Number.isFinite(sampleDays) && sampleDays > 0) parts.push(`표본 ${Math.trunc(sampleDays)}일`);
  const requestedSize = Number(row.requested_size_usd);
  const executableSize = Number(row.executable_size_usd);
  const hasRequestedSize = row.requested_size_usd != null && Number.isFinite(requestedSize);
  const hasExecutableSize = row.executable_size_usd != null && Number.isFinite(executableSize);
  if (hasRequestedSize || hasExecutableSize) {
    parts.push(`요청금액 ${hasRequestedSize ? money(requestedSize) : "--"} → 체결가능 ${hasExecutableSize ? money(executableSize) : "--"}`);
  }
  return parts.length ? `<div class="detail-line"><strong>확률 보정</strong> ${esc(parts.join(" · "))}</div>` : "";
}

function minuteOfDay(value) {
  const minute = Number(value);
  if (!Number.isFinite(minute)) return "--";
  const normalized = Math.max(0, Math.min(1439, Math.round(minute)));
  return `${String(Math.floor(normalized / 60)).padStart(2, "0")}:${String(normalized % 60).padStart(2, "0")}`;
}

function strategyReasonKo(reason) {
  const raw = String(reason || "").trim();
  const lower = raw.toLowerCase();
  if (!raw) return "아직 전략 허용/차단 이유가 기록되지 않았습니다.";
  if (lower.includes("irreversibly broke") || lower.includes("verified bucket break")) {
    return "공식 당일 관측값이 이미 선택 온도칸을 벗어났습니다. 최고/최저 기록은 되돌릴 수 없어서 이 방향을 허용했습니다.";
  }
  if (lower.includes("formation monitoring started")) {
    return "이 도시·이 달의 과거 통계상 이제부터 판단해도 되는 시간대입니다. 남은 움직임 확률까지 보고 있습니다.";
  }
  if (lower.includes("has not started")) return "아직 이 도시의 보통 최고/최저가 만들어지는 시간 전이라 신규 진입을 막았습니다.";
  if (lower.includes("q75")) return "과거 늦은 날까지 포함한 안전 시간 전이라 정확한 온도칸 진입을 기다립니다.";
  if (lower.includes("16:00")) return "최고기온 정확한 온도칸은 오후 4시 이후에만 봅니다.";
  if (lower.includes("rain/dewpoint")) return "비/이슬점 때문에 밤에 최저기온이 더 내려갈 수 있어 막았습니다.";
  if (lower.includes("clob closes before high")) return "주문장이 최고기온이 보통 만들어지는 시간보다 먼저 닫혀서 최고기온 전략을 막았습니다.";
  return raw.replace(/[-_]/g, " ");
}

function dataBlockReasonKo(reason) {
  const raw = String(reason || "").trim();
  const lower = raw.toLowerCase();
  if (!raw) return "없음";
  if (lower === "low-exact-no-rain-dewpoint-risk") return "비와 이슬점 때문에 최저기온이 한 칸 더 내려갈 위험이 있어 차단";
  if (lower === "metar-daily-extremes-baseline-missing") return "서버 재시작 등으로 오늘 00:00부터의 온도표 기준이 부족해 차단";
  if (lower === "formation-q75-not-reached") return "과거 통계상 아직 너무 이른 시간이라 차단";
  if (lower === "clob-closes-before-high-formation") return "주문장이 최고기온 형성 시간보다 먼저 닫혀 차단";
  return raw.replace(/[-_]/g, " ");
}

function dailyExtremesKo(status, complete) {
  const raw = String(status || "").toLowerCase();
  const ok = raw === "complete" || String(complete).toLowerCase() === "true";
  if (ok) return "사용 가능: 현지 00:00부터 현재까지 최고/최저 온도표가 이어져 있습니다.";
  if (raw === "blocked") return "사용 금지: 오늘 온도표가 끊겼거나 기준값이 부족합니다.";
  return "확인 중: 오늘 온도표가 충분한지 검사 중입니다.";
}

function midnightResetKo(status) {
  const raw = String(status || "").trim().toLowerCase();
  if (!raw || raw === "해당 없음" || raw === "none") return "해당 없음: 홍콩 HKO 자정 이월 방어용 검사입니다.";
  if (raw === "verified") return "확인됨: 새 날짜 값으로 초기화됐습니다.";
  if (raw.includes("block")) return "차단됨: 전날 값이 섞였을 가능성이 있습니다.";
  return status;
}

function monitoringStatusKo(status) {
  const raw = String(status || "").toLowerCase();
  if (raw === "started") return "관찰 시작 후";
  if (raw === "before_start") return "관찰 시작 전";
  if (raw === "missing") return "과거 통계 없음";
  return "확인 중";
}

function formationWindowText(label, q25, median, q75) {
  const early = minuteOfDay(q25);
  const mid = minuteOfDay(median);
  const late = minuteOfDay(q75);
  if (early === "--" && mid === "--" && late === "--") return `${label}: 과거 통계 없음`;
  return `${label}: 보통 ${mid}쯤, 빠른 날 ${early}쯤, 늦은 날 ${late}쯤`;
}

function bookSourceKo(source) {
  const raw = String(source || "").toLowerCase();
  if (raw === "websocket") return "웹소켓 실시간 호가";
  if (raw === "rest") return "REST 보조 호가";
  return "호가 출처 확인 중";
}

function stationStrategyAuditLine(row) {
  const localDate = String(row.station_local_date || row.target_date_local || "--");
  const localTime = String(row.station_local_time || "--");
  const monitoring = String(row.formation_monitoring_status || "not_available");
  const highRange = formationWindowText("최고기온", row.first_final_high_local_minute_q25, row.first_final_high_local_minute_median, row.first_final_high_local_minute_q75);
  const lowRange = formationWindowText("최저기온", row.first_final_low_local_minute_q25, row.first_final_low_local_minute_median, row.first_final_low_local_minute_q75);
  const movement = row.remaining_movement_probability == null || row.remaining_movement_probability === "" ? "--" : probPct(row.remaining_movement_probability);
  const reset = midnightResetKo(row.midnight_reset_status || "");
  const dailyExtremes = dailyExtremesKo(row.daily_extremes_status, row.daily_extremes_complete);
  const blocker = dataBlockReasonKo(row.data_block_reason || "");
  const accepting = String(row.clob_accepting_orders ?? "unknown").toLowerCase();
  const orderbook = String(row.clob_enable_order_book ?? "unknown").toLowerCase();
  const clob = accepting === "true" && orderbook === "true" ? "주문 가능" : accepting === "false" || orderbook === "false" ? "주문 불가" : "주문 상태 미확인";
  const strategyReason = strategyReasonKo(row.strategy_allowed_reason || (monitoring === "started" ? "formation monitoring started" : monitoring === "before_start" ? "station-local formation monitoring has not started" : ""));
  const actualRecord = row.nowcast_high_c != null
    ? `현재까지 최고 ${tempC(row.nowcast_high_c)}${row.observed_at ? ` · 기록 확인 ${shortDateTime(row.observed_at)}` : ""}`
    : (row.nowcast_low_c != null ? `현재까지 최저 ${tempC(row.nowcast_low_c)}${row.observed_at ? ` · 기록 확인 ${shortDateTime(row.observed_at)}` : ""}` : "현재까지 공식 최고/최저 기록 없음");
  return `<div class="detail-line strategy-audit">
    <strong>봇 판단 시간</strong> 관측소 현지 ${esc(localDate)} ${esc(localTime)} · 이 시각 기준으로 공식 관측값과 주문장을 평가했습니다.<br>
    <strong>오늘 실제 기록</strong> ${esc(actualRecord)}<br>
    <strong>전략 시간표</strong> ${esc(monitoringStatusKo(monitoring))} (${minuteOfDay(row.monitoring_start_local_minute)}부터 검사) · 남은 시간에 더 움직일 과거확률 ${esc(movement)}<br>
    <strong>과거 통계</strong> ${esc(highRange)} · ${esc(lowRange)} · 오늘 기록 시간이 아니라 같은 관측소/같은 달의 과거 분포입니다.<br>
    <strong>자료 상태</strong> ${esc(dailyExtremes)} · 자정 초기화 ${esc(reset)} · 차단 이유 ${esc(blocker)}<br>
    <strong>주문장</strong> ${esc(clob)} · <strong>전략 판단</strong> ${esc(strategyReason)}
  </div>`;
}

function cardForPosition(p) {
  const bidDepthPnl = Number(p.bid_depth_unrealized_pnl || 0);
  const bidDepthPnlClass = bidDepthPnl >= 0 ? "win" : "loss";
  const bidDepthPnlSign = bidDepthPnl >= 0 ? "+" : "-";
  const exitStatus = p.exit_liquidity_status || "unknown";
  const exitBlocker = p.exit_blocker ? ` · 차단 ${esc(p.exit_blocker)}` : "";
  const wsAge = p.websocket_stale_book_age_seconds != null ? ` · ${bookSourceKo(p.websocket_last_book_source)} ${duration(p.websocket_stale_book_age_seconds)} 전` : "";
  const sideRaw = (p.side || "").toUpperCase();
  const qLower = (p.question || "").toLowerCase();
  const isHighest = qLower.includes("highest") || qLower.includes("high");
  const displayTitle = p.display_title || [p.city, p.date_hint, p.bucket_label, sideRaw].filter(Boolean).join(" · ") || p.question || "";
  const eventTitle = p.event_title || p.question || "";
  const titleHtml = p.market_url
    ? `<a class="position-title-main" href="${esc(p.market_url)}" target="_blank" rel="noopener noreferrer">${esc(displayTitle)}</a>`
    : `<div class="position-title-main">${esc(displayTitle)}</div>`;
  const nowcastVal = isHighest
    ? (p.nowcast_high_c != null ? p.nowcast_high_c : null)
    : (p.nowcast_low_c != null ? p.nowcast_low_c : null);
  const nowcastLabel = isHighest ? "관측 최고" : "관측 최저";
  const nowcastBadge = nowcastVal != null
    ? `<span class="badge obs-temp">${nowcastLabel} ${tempC(nowcastVal)}</span>`
    : `<span class="badge muted-badge">${esc(nowcastUnavailableKo(p.nowcast_unavailable_reason))}</span>`;
  const lockBadge = `<span class="badge ${stationLockClass(p.station_lock_strength)}">${esc(stationLockLabel(p.station_lock_strength))}</span>`;
  const allocationBadge = p.actual_entry_fraction != null
    ? `<span class="badge neutral">실제 투입 ${(Number(p.actual_entry_fraction) * 100).toFixed(1)}%</span>`
    : (p.station_allocation_fraction != null
      ? `<span class="badge neutral">전략 목표 ${(Number(p.station_allocation_fraction) * 100).toFixed(0)}%</span>`
      : `<span class="badge muted-badge">실제 투입 기록 없음</span>`);
  const requestedAllocationBadge = p.entry_fraction != null
    ? `<span class="badge muted-badge">전략 요청 ${(Number(p.entry_fraction) * 100).toFixed(1)}%</span>`
    : "";
  const boundaryLine = stationBoundaryText(p);
  const stationLabel = p.station_name && p.station_id && p.station_name !== p.station_id
    ? `${p.station_name} (${p.station_id})`
    : (p.station_name || p.station_id || "");
  const observedLine = nowcastVal != null
    ? `${nowcastLabel} ${tempC(nowcastVal)}${p.observed_at ? ` · 관측시각 ${shortDateTime(p.observed_at)}` : ""}`
    : nowcastUnavailableDetail(p.nowcast_unavailable_reason);
  const referencePnl = Number(p.reference_unrealized_pnl ?? p.unrealized_pnl ?? 0);
  const showReference = Number.isFinite(referencePnl) && Math.abs(referencePnl - bidDepthPnl) >= 0.01;
  const referenceSign = referencePnl >= 0 ? "+" : "-";
  const referenceLine = showReference
    ? `<div class="reference-line">참고PnL ${referenceSign}${money(Math.abs(referencePnl))}: 마지막 표시가 기준인 장부 참고값입니다. 실제 청산 판단은 위 청산PnL을 우선합니다.</div>`
    : "";
  return `<div class="card open">
    <div class="position-heading">
      ${titleHtml}
      <div class="position-title-meta">이벤트: ${esc(eventTitle)}</div>
    </div>
    <div class="pos-row">
      <span class="badge ${sideRaw === 'YES' ? 'yes' : 'no'}">${sidePositionKo(sideRaw)}</span>
      <span class="badge price">선택 ${esc(p.bucket_label || "--")}</span>
      ${nowcastBadge}
      ${lockBadge}
      ${allocationBadge}
      ${requestedAllocationBadge}
    </div>
    <div class="pos-row">
      <span class="badge price">진입 ${price(p.entry_price)}</span>
      <span class="badge price">진입금액 ${money(p.cost_usd)}</span>
      <span class="badge neutral">진입시간 ${shortDateTime(p.opened_at)}</span>
      <span class="badge current-price" title="보유 수량을 지금 청산할 때 쓰는 기준가입니다. 시장에서 새로 사는 가격이 아닙니다.">청산기준 ${price(p.mark_price)}</span>
      <span class="badge ${bidDepthPnlClass}">청산PnL ${bidDepthPnlSign}${money(Math.abs(bidDepthPnl))}</span>
    </div>
    <div class="pos-row">
      <span class="badge ${exitStatusClass(exitStatus)}">청산 ${exitStatusKo(exitStatus)}</span>
      <span class="badge price">최고매수 ${price(p.exit_best_bid)}</span>
      <span class="badge current-price" title="전량을 지금 주문장에 팔 때의 평균 매도가">전량 매도평균 ${price(p.exit_full_vwap)}</span>
      <span class="badge current-price" title="절반만 지금 주문장에 팔 때의 평균 매도가">절반 매도평균 ${price(p.exit_half_vwap)}</span>
    </div>
    ${referenceLine}
    <div class="detail-line">
      <strong>관측소</strong> ${esc(stationLabel || "--")} · ${esc(observedLine)}
      ${p.nowcast_source ? ` · 출처 ${esc(p.nowcast_source)}` : ""}
    </div>
    <div class="detail-line"><strong>정산 경계</strong> ${esc(boundaryLine || "관측 신호가 생기면 표시")}</div>
    ${probabilityAuditLine(p)}
    ${stationStrategyAuditLine(p)}
    <div class="detail-line">
      ${esc(p.city || "")} ${esc(p.date_hint || "")} · 수량 ${Number(p.shares || 0).toFixed(2)} · 비용 ${money(p.cost_usd)}
      ${p.entry_fee_usdc != null ? ` · 수수료 $${Number(p.entry_fee_usdc).toFixed(4)}` : ''}
      ${p.net_edge != null ? ` · 엣지 ${(Number(p.net_edge)*100).toFixed(1)}%` : ''}
    </div>
    <div class="detail-line">
      매도가능 ${qty(p.exit_available_shares)} / ${qty(p.shares)} · 청산가치 ${money(p.bid_depth_market_value)}
      · 호가수신 ${statusKo(p.websocket_status)}${p.websocket_stale ? " · 오래됨" : ""}${wsAge}${exitBlocker}
    </div>
  </div>`;
}
function cardForTrade(t) {
  const action = String(t.action || "");
  const isClose = action.includes("CLOSE") || action.includes("SETTLE");
  const pnl = Number(t.cash_delta_or_pnl || 0);
  const entryAmount = Math.abs(Number(t.cash_delta_or_pnl || 0)) || Number(t.price || 0) * Number(t.shares || 0);
  const sideLabel = (t.side || "").toUpperCase() === "YES" ? "Yes" : "No";
  const actionLabel = actionKo(action);
  const isProfit = pnl >= 0;
  const pnlSign = isProfit ? "+" : "-";
  const pnlClass = isProfit ? "win" : "loss";
  const cardClass = isClose ? (isProfit ? "profit" : "loss") : "open";
  const reasonKo = _buildReasonKo(t.reason || "");
  const closeParts = closeReasonParts(t.reason || "");
  const ts = shortDateTime(t.ts || "");
  const closeFacts = closeParts.facts.length
    ? `<div class="reason-facts">${closeParts.facts.map(x => `<span class="badge muted-badge">${esc(x)}</span>`).join("")}</div>`
    : "";
  return `<div class="card ${cardClass}">
    <div class="market-title">${esc(t.question || "")}</div>
    <div class="pos-row">
      <span class="badge ${isClose ? (isProfit ? 'win' : 'loss') : 'neutral'}">${esc(actionLabel)}</span>
      <span class="badge ${(t.side||'').toUpperCase() === 'YES' ? 'yes' : 'no'}">${sidePositionKo(sideLabel)}</span>
      <span class="badge current-price">체결가 ${price(t.price)}</span>
      ${!isClose ? `<span class="badge price">진입금액 ${money(entryAmount)}</span>` : ""}
      <span class="badge ${pnlClass}">${pnlSign}${money(Math.abs(pnl))}</span>
    </div>
    ${reasonKo ? `<div class="reason-box"><b>진입 근거</b><br>${esc(reasonKo)}</div>` : ""}
    ${isClose && closeParts.summary ? `<div class="reason-box"><b>정리 이유</b><br>${esc(closeParts.summary)}${closeFacts}</div>` : ""}
    <div class="small muted" style="margin-top:4px">${ts}</div>
  </div>`;
}

function _buildReasonKo(reason) {
  if (!reason) return "";
  // Only parse the entry: section
  const entryPart = (reason.match(/entry:([^;]*)/i) || [])[1] || reason;
  const lines = [];
  const mp = entryPart.match(/station_p=([\d.]+)/);
  const pe = entryPart.match(/p_exec=([\d.]+)/);
  const ne = entryPart.match(/net_edge=([\d.]+)/);
  const br = entryPart.match(/bankroll=\$([\d.]+)/);
  const ef = entryPart.match(/entry_fraction=([\d.]+)%/);
  const ps = entryPart.match(/probability_stop=([\d.]+)/);
  const mf = entryPart.match(/model_fair=([\d.]+)/);
  const te = entryPart.match(/target_exit=([\d.]+)/);
  const feeM = entryPart.match(/entry_fee=\$([\d.]+)/);
  const heat = entryPart.match(/heat=([\d.\-]+)%/);
  if (mp) lines.push(`관측소 YES점수 ${(parseFloat(mp[1])*100).toFixed(1)}%`);
  if (pe) lines.push(`체결가 ${(parseFloat(pe[1])*100).toFixed(1)}¢`);
  if (mf) lines.push(`정산기준가 ${(parseFloat(mf[1])*100).toFixed(1)}¢`);
  if (ne) lines.push(`엣지 ${(parseFloat(ne[1])*100).toFixed(1)}%`);
  if (te) lines.push(`목표익절 ${(parseFloat(te[1])*100).toFixed(1)}¢`);
  if (ps) lines.push(`방어선 ${(parseFloat(ps[1])*100).toFixed(1)}%`);
  // 진입금액 계산: bankroll × fraction
  if (br && ef) {
    const amt = parseFloat(br[1]) * parseFloat(ef[1]) / 100;
    lines.push(`진입금액 $${amt.toFixed(2)}`);
  }
  if (feeM) lines.push(`수수료 $${feeM[1]}`);
  if (heat) lines.push(`시장과열 ${heat[1]}%`);
  return lines.join(" · ");
}
function _parseCloseReason(reason) {
  if (!reason) return "";
  return closeReasonParts(reason).summary;
}

function realizedCards(rows) {
  if (!rows.length) return `<div class="small muted">확정된 거래가 없습니다</div>`;
  return rows.map(r => {
    const pnl = Number(r.pnl || 0);
    const isProfit = pnl > 0;
    const trigger = r.exit_trigger || closeTrigger(r.reason || "");
    const isDefensiveClose = defensiveCloseTrigger(trigger);
    const resultLabel = isProfit ? (isDefensiveClose ? "방어청산" : "수익") : "손절";
    const cardClass = isProfit ? "profit" : "loss";
    const exitLabel = isProfit ? (isDefensiveClose ? "정리" : "익절") : "손절";
    const sideRaw = (r.side || "").toUpperCase();
    const pnlSign = isProfit ? "+" : "-";
    const bucket = bucketLabelFromFields(r.threshold_c, r.condition_label);
    const closeParts = closeReasonParts(r.reason || "");
    const fact = (label, value, cls = "") => `<div class="fact"><span>${esc(label)}</span><strong class="${cls}">${esc(value)}</strong></div>`;
    const reasonFacts = closeParts.facts.length
      ? `<div class="reason-facts">${closeParts.facts.map(x => `<span class="badge muted-badge">${esc(x)}</span>`).join("")}</div>`
      : "";
    return `<div class="card ${cardClass}">
      <div class="market-title">${esc(r.question || "")}</div>
      <div class="pos-row">
        <span class="badge ${isProfit ? 'win' : 'loss'}">${resultLabel}</span>
        <span class="badge ${sideRaw === 'YES' ? 'yes' : 'no'}">${sidePositionKo(sideRaw)}</span>
        <span class="badge price">선택 ${esc(bucket)}</span>
      </div>
      <div class="realized-fact-grid">
        ${fact("결과", resultLabel, isProfit ? "profit-text" : "loss-text")}
        ${fact("포지션", sidePositionKo(sideRaw))}
        ${fact("진입가", price(r.entry_price))}
        ${fact(exitLabel + "가", price(r.exit_price))}
        ${fact("손익", pnlSign + money(Math.abs(pnl)), isProfit ? "profit-text" : "loss-text")}
        ${fact("수익률", roi(r.roi), isProfit ? "profit-text" : "loss-text")}
        ${fact("도시", r.city || "--")}
        ${fact("시간", shortDateTime(r.closed_at))}
      </div>
      ${closeParts.summary ? `<div class="reason-box"><b>정리 이유</b><br>${esc(closeParts.summary)}${reasonFacts}</div>` : ''}
      <div class="detail-line">${esc(r.city || '')} ${esc(r.date_hint || '')}</div>
    </div>`;
  }).join("");
}

function cityEntryCard(row) {
  const openPnl = Number(row.open_unrealized_pnl || 0);
  const realizedPnl = Number(row.recent_realized_pnl || 0);
  const pnlClass = openPnl >= 0 ? "city-status-ok" : "city-status-fail";
  const realizedClass = realizedPnl >= 0 ? "city-status-ok" : "city-status-fail";
  const positions = (row.positions || []).slice(0, 3).map(p => {
    const pnl = Number(p.unrealized_pnl || 0);
    const cls = pnl >= 0 ? "city-status-ok" : "city-status-fail";
    const title = p.market_url
      ? `<a class="market-link" href="${esc(p.market_url)}" target="_blank" rel="noopener noreferrer">${esc(p.question || "")}</a>`
      : esc(p.question || "");
    return `<div class="city-card-detail">
      ${title}<br>
      ${esc(sidePositionKo(p.side))} · 진입 ${price(p.entry_price)} · 현재 ${price(p.mark_price)} · 금액 ${money(p.cost_usd)} · <span class="${cls}">${signedMoney(pnl)}</span>
    </div>`;
  }).join("");
  const trades = (row.recent_trades || []).slice(0, 3).map(t => {
    const isEntry = ["OPEN", "ADD"].includes(String(t.action || "").toUpperCase());
    const value = isEntry ? money(t.entry_amount_usd) : signedMoney(t.pnl);
    return `<div class="city-card-detail">
      ${shortDateTime(t.ts)} · ${esc(actionKo(t.action))} · ${esc(sidePositionKo(t.side))} · ${price(t.price)} · ${value}
    </div>`;
  }).join("");
  return `<div class="city-card ${Number(row.open_count || 0) ? "ok" : "warn"}">
    <div class="city-card-row">
      <span class="city-name">${esc(row.city || "unknown")}</span>
      <span class="${pnlClass}">${signedMoney(openPnl)}</span>
    </div>
    <div class="city-card-detail">
      보유 ${Number(row.open_count || 0)}개 · 보유 진입금 ${money(row.open_entry_usd)} · 평가 ${money(row.open_market_value_usd)}
    </div>
    <div class="city-card-detail">
      첫 진입 ${shortDateTime(row.first_entry_at)} · 최근 진입 ${shortDateTime(row.latest_entry_at)} · 최근 체결 ${shortDateTime(row.latest_trade_at)}
    </div>
    <div class="city-card-detail">
      최근 진입 ${Number(row.recent_entry_count || 0)}건 / ${money(row.recent_entry_usd)} · 최근 확정 <span class="${realizedClass}">${signedMoney(realizedPnl)}</span>
    </div>
    ${positions ? `<div class="city-card-detail"><strong>보유</strong></div>${positions}` : ""}
    ${trades ? `<div class="city-card-detail"><strong>최근체결</strong></div>${trades}` : ""}
  </div>`;
}

function stationSignalCard(signal) {
  const lock = signal.lock_strength || "";
  const observed = signal.observed_high_c != null
    ? `관측 최고 ${tempC(signal.observed_high_c)}`
    : (signal.observed_low_c != null ? `관측 최저 ${tempC(signal.observed_low_c)}` : "관측값 --");
  const boundary = signal.settlement_boundary_c != null ? `정산 경계 ${tempC(signal.settlement_boundary_c)}` : "정산 경계 --";
  const buffer = signal.buffer_c != null ? `경계 여유 ${tempC(signal.buffer_c)}` : "";
  const close = signal.hours_to_close != null ? `마감까지 ${duration(Number(signal.hours_to_close) * 3600)}` : "";
  const allocation = signal.allocation_fraction != null ? `진입 비중 ${(Number(signal.allocation_fraction) * 100).toFixed(0)}%` : "진입 비중 --";
  const station = [signal.station_name, signal.station_id].filter(Boolean).join(" · ");
  return `<div class="city-card ${String(lock).includes("no") ? "fail" : "ok"}">
    <div class="city-card-row">
      <span class="city-name">${esc(signal.city || signal.station_id || "?")}</span>
      <span class="${String(lock).includes("no") ? "city-status-fail" : "city-status-ok"}">${esc(stationLockLabel(lock))}</span>
    </div>
    <div class="city-card-detail">${esc(signal.question || "")}</div>
    ${station ? `<div class="city-card-detail">공식 관측소: ${esc(station)}</div>` : ""}
    <div class="city-card-detail">${esc([observed, boundary, buffer, close].filter(Boolean).join(" · "))}</div>
    <div class="city-card-detail">${esc(allocation)} · 판단 ${esc(sidePositionKo(signal.side))}</div>
    ${probabilityAuditLine(signal)}
    ${stationStrategyAuditLine(signal)}
    <div class="city-card-detail">관측시각 ${shortDateTime(signal.observed_at || signal.ts)}</div>
  </div>`;
}

function recentSkipCard(skip) {
  const observed = skip.observed_high_c != null
    ? `관측 최고 ${tempC(skip.observed_high_c)}`
    : (skip.observed_low_c != null ? `관측 최저 ${tempC(skip.observed_low_c)}` : "");
  const station = [skip.station_name, skip.station_id].filter(Boolean).join(" · ");
  const reasonKo = skip.reason_ko || "조건을 통과하지 못해 진입하지 않았습니다.";
  return `<div class="city-card warn">
    <div class="city-card-row">
      <span class="city-name">${esc(skip.city || skip.station_id || "?")}</span>
      <span class="city-status-fail">${esc(skip.reason_code || "SKIP")}</span>
    </div>
    <div class="city-card-detail">${esc(skip.question || "")}</div>
    ${station ? `<div class="city-card-detail">공식 관측소: ${esc(station)}</div>` : ""}
    <div class="city-card-detail">${esc(skip.reason_ko || reasonKo)}</div>
    ${observed ? `<div class="city-card-detail">${esc(observed)}</div>` : ""}
    <div class="city-card-detail">${shortDateTime(skip.ts)}</div>
  </div>`;
}

function stationRegistryCard(station) {
  const ready = station.trading_ready === true;
  const cls = ready ? "ok" : "warn";
  const status = ready ? "매매 사용" : "제외";
  const statusCls = ready ? "city-status-ok" : "city-status-fail";
  const official = [station.station_name, station.station_id].filter(Boolean).join(" · ");
  const refs = (station.display_station_references || []).map(ref => {
    const label = [ref.station_name, ref.station_id].filter(Boolean).join(" · ");
    return `<div class="city-card-detail">참고 관측소: ${esc(label)} · ${esc(ref.usage_ko || "표시용 참고")}</div>`;
  }).join("");
  return `<div class="city-card ${cls}">
    <div class="city-card-row">
      <span class="city-name">${esc(station.city || station.station_id || "?")}</span>
      <span class="${statusCls}">${status}</span>
    </div>
    <div class="city-card-detail">정산 관측소: ${esc(official || "--")}</div>
    <div class="city-card-detail">${esc(station.nowcast_call_rule_ko || "")}</div>
    <div class="city-card-detail">관측 타입 ${esc(station.nowcast_source_type || "--")} · 신뢰등급 ${esc(station.nowcast_confidence_grade || "--")}</div>
    ${refs}
  </div>`;
}

function cityNowcastCard(c) {
  const ok = (c.status || "").toUpperCase() === "SUCCESS" || (c.status || "").toUpperCase() === "HIT";
  const cls = ok ? "ok" : ((c.error || c.unavailable_reason) ? "fail" : "warn");
  const statusText = ok ? "✓ 성공" : (c.last_success_at ? "✗ 실패 · 이전 성공 있음" : "✗ 실패");
  const ts = shortDateTime(c.requested_at || c.attempted_at || "");
  const err = c.error || c.unavailable_reason || "";
  const stn = c.station_name || "";
  const hiC = c.observed_high_c != null ? `<span style="color:var(--red)">최고기온 ${tempC(c.observed_high_c)}</span>` : "";
  const loC = c.observed_low_c != null ? `<span style="color:var(--blue)">최저기온 ${tempC(c.observed_low_c)}</span>` : "";
  const temps = [hiC, loC].filter(Boolean).join(" · ");
  const lastSuccess = c.last_success_at ? shortDateTime(c.last_success_at) : "";
  const lastFailure = c.last_failure_at ? shortDateTime(c.last_failure_at) : "";
  const lastFailureError = c.last_failure_error || err;
  // bulk-metar: one AWC request covering the current enabled METAR station set
  const isBulk = (c.city || "") === "bulk-metar" || String(c.request_mode || "").includes("bulk");
  if (isBulk) {
    const stationCount = c.requested_station_count || 38;
    const triggerCity = c.trigger_city ? ` · ${c.trigger_city} 트리거` : "";
    return `<div class="city-card ${cls}">
    <div class="city-card-row">
      <span class="city-name">☁️ AWC METAR 일괄 (${stationCount}개 관측소)</span>
      <span class="${ok ? 'city-status-ok' : 'city-status-fail'}">${statusText}</span>
    </div>
    <div class="city-card-detail" style="color:var(--muted)">홍콩 제외 전체 도시 1회 요청${triggerCity}</div>
    <div class="city-card-detail">최근 시도: ${ts}</div>
    ${lastSuccess ? `<div class="city-card-detail">마지막 성공: ${lastSuccess}</div>` : ""}
    <div class="city-card-detail" style="color:var(--muted)">갱신 주기: 캐시 만료 시 (≥5분)</div>
    ${lastFailureError && !ok ? `<div class="city-card-detail" style="color:var(--red)">최근 실패: ${esc(String(lastFailureError).slice(0, 80))}${lastFailure ? ` · ${lastFailure}` : ""}</div>` : ""}
  </div>`;
  }
  return `<div class="city-card ${cls}">
    <div class="city-card-row">
      <span class="city-name">${esc(c.city || c.station_id || "?")}</span>
      <span class="${ok ? 'city-status-ok' : 'city-status-fail'}">${statusText}</span>
    </div>
    ${stn ? `<div class="city-card-detail">공식 관측소: ${esc(stn)}</div>` : ""}
    <div class="city-card-detail">최근 시도: ${ts}</div>
    ${lastSuccess ? `<div class="city-card-detail">마지막 성공: ${lastSuccess}</div>` : ""}
    ${temps ? `<div class="city-card-detail">${temps}</div>` : ""}
    ${lastFailureError && !ok ? `<div class="city-card-detail" style="color:var(--red)">최근 실패: ${esc(String(lastFailureError).slice(0, 80))}${lastFailure ? ` · ${lastFailure}` : ""}</div>` : ""}
    ${!ok ? `<div class="city-card-detail" style="color:var(--muted)">대응: 다음 관측 주기에 다시 시도하고, 성공 캐시는 유지합니다.</div>` : ""}
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
  setText("r-latest-station", shortDateTime(payload.scanner.latest_station_at));
  const profitUsd = payload.summary.realized_profit_usd || 0;
  const lossUsd = payload.summary.realized_loss_usd || 0;
  const netProfit = profitUsd - lossUsd;
  const netEl = document.getElementById("r-net-profit");
  netEl.textContent = (netProfit >= 0 ? "+" : "-") + money(Math.abs(netProfit)).slice(1);
  netEl.className = netProfit >= 0 ? "" : "bad";
  setText("r-total-profit", "+" + money(profitUsd));
  setText("r-total-loss", "-" + money(lossUsd));
  setText("r-cash", money(payload.summary.cash));
  const cityEntries = payload.city_entries || [];
  document.getElementById("r-city-entries").innerHTML = cityEntries.length
    ? cityEntries.map(cityEntryCard).join("")
    : `<div class="small muted">도시별 진입 내역이 없습니다</div>`;
  const stationHealth = (payload.health || {}).station || {};
  setHealthStatus("r-station-health", stationHealth.status);
  setText("r-station-success", "마지막 성공 " + shortDateTime(stationHealth.last_success_at));
  setText("r-station-age", "관측값 경과 " + (stationHealth.age_seconds == null ? "--" : duration(stationHealth.age_seconds)));
  setText("r-station-error", "최근 실패 이유 " + (stationHealth.last_failure_reason || "--"));
  const websocketHealth = (payload.health || {}).websocket || {};
  setHealthStatus("r-websocket-health", websocketHealth.status);
  setText("r-websocket-thread", "실시간 수신 스레드 " + (websocketHealth.thread_alive === true ? "실행중" : (websocketHealth.thread_alive === false ? "중지" : "--")));
  setText("r-websocket-reconnects", "재연결 " + Number(websocketHealth.reconnect_count || 0));
  setText("r-websocket-message", "마지막 메시지 " + shortDateTime(websocketHealth.last_message_at));
  setText("r-websocket-book", "마지막 주문장 " + shortDateTime(websocketHealth.last_book_at) + " · 경과 " + (websocketHealth.stale_book_age_seconds == null ? "--" : duration(websocketHealth.stale_book_age_seconds)));
  setText("r-websocket-error", "최근 오류 " + (websocketHealth.last_error || "--"));
  const streamActivity = document.getElementById("r-stream-activity");
  const streamPulse = document.getElementById("r-stream-pulse");
  const streamStatus = String(websocketHealth.status || "").toUpperCase();
  const streamLive = streamStatus === "HEALTHY";
  streamActivity.classList.toggle("live", streamLive);
  streamPulse.textContent = streamLive ? "수신 중" : statusKo(streamStatus);
  streamPulse.className = streamStatusClass(streamStatus);
  setText("r-stream-summary", "감시 대상 " + Number(websocketHealth.stream_tokens || 0) + "토큰 · " + Number(websocketHealth.stream_markets || 0) + "마켓 · " + Number(websocketHealth.stream_cities || 0) + "도시");
  setText("r-stream-reason", "상태 설명 " + (websocketHealth.status_reason || (streamLive ? "주문장 메시지를 정상 수신 중" : "--")));
  const stationObservations = (payload.scanner || {}).station_observations || [];
  document.getElementById("r-station-observations").innerHTML = stationObservations.length
    ? stationObservations.map(cityNowcastCard).join("")
    : `<div class="small muted">관측소 호출 기록 없음</div>`;
  const stationRegistry = (payload.scanner || {}).station_registry || [];
  document.getElementById("r-station-registry").innerHTML = stationRegistry.length
    ? stationRegistry.map(stationRegistryCard).join("")
    : `<div class="small muted">공식 관측소 목록 없음</div>`;
  const stationSignals = (payload.scanner || {}).station_signals || [];
  document.getElementById("r-station-signals").innerHTML = stationSignals.length
    ? stationSignals.map(stationSignalCard).join("")
    : `<div class="small muted">아직 정산 잠금 신호가 없습니다</div>`;
  const recentSkips = (payload.scanner || {}).recent_skips || [];
  document.getElementById("r-recent-skips").innerHTML = recentSkips.length
    ? recentSkips.map(recentSkipCard).join("")
    : `<div class="small muted">최근 스킵 기록이 없습니다</div>`;
  setText("open-count", payload.positions.length);
  setText("trade-count", payload.recent_trades.length);
  const realizedRows = payload.realized_results || [];
  setText("realized-count", realizedRows.length);
  document.getElementById("open-positions").innerHTML = payload.positions.length ? payload.positions.map(cardForPosition).join("") : `<div class="small muted">보유 포지션이 없습니다</div>`;
  document.getElementById("realized-results").innerHTML = realizedCards(realizedRows);
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
