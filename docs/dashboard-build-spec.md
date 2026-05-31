# Dashboard Build Spec

This file is the source of truth for rebuilding the paper-trading dashboard UI.
It describes the expected visual system, data contract, and interaction behavior
so a new AI can recreate the same screen from the repository alone.

## Visual System

- Match Polymarket's official brand system:
  - Primary action blue: `#2E5CFF`
  - Base colors: black `#000000` and white `#FFFFFF`
  - Typeface: Inter first, system sans fallback
  - Source: `https://polymarket.com/brand`
- The dashboard is always dark mode. Use these role tokens in
  `src/weather_bot/dashboard.py`:
  - `--bg: #080a0f`
  - `--panel: #0f1117`
  - `--panel-2: #151924`
  - `--panel-3: #1b2030`
  - `--line: #252a36`
  - `--text: #f7f8fa`
  - `--muted: #9ba3b0`
  - `--blue: #2e5cff`
- Keep cards at `8px` radius or less. Use blue for primary market activity,
  green for profit, red for loss, and yellow only for caution/price emphasis.
- Prices must use Polymarket-style cents formatting: `0.21` becomes `21¢`, not
  `0.21E`.
- Temperatures must use the degree symbol: `23°C`.

## Layout

- Left rail: open positions.
- Center: top account metrics, `Equity / PnL Curve`, then `Realized PnL`.
- Right rail: `Scanner Intelligence`, then `Recent Trades`.
- The right rail must be a grid whose `Recent Trades` body fills the remaining
  viewport height. The trade list owns the scrollbar, so the scroll thumb should
  cover the whole visible recent-trades area rather than stopping halfway.
- `Realized PnL` uses a single table with consistent left padding. Numeric
  columns are right-aligned with tabular numbers.

## Scanner Intelligence Contract

Show only operator-useful summary rows:

- `오픈 포지션`: current open position count from paper state.
- `총 오픈 진입금액`: sum of `cost_usd` for current open positions.
- `Open-Meteo 최근 예보`: latest `created_at` timestamp found in
  `forecast_cache.json`.
- `총 수익금`: cumulative positive PnL from closed/settled/partial-close trade
  rows.
- `총 손실금`: cumulative absolute value of negative PnL from closed/settled/
  partial-close trade rows.
- `남은 현금`: current `cash_usd`.

Do not show `누적 후보 판단`, `예보 없음`, `실제 진입`, or `YES/NO 판단` in the UI.
Those counters may still exist internally for tests or future diagnostics, but
they are not dashboard display rows.

Below the summary rows, include two health boxes:

- `예보 상태`: explain the last fresh-request attempt, last successful
  forecast timestamp, cache age, stale warning, recent failure reason, and
  disk-save error. A visible dashboard with an old forecast is not healthy.
- `WebSocket 상태`: explain whether the background receiver thread is alive,
  reconnect count, last incoming message, last actual order-book price update,
  stale-book age, and recent stream error. Trade-only or tick-size-only events
  must not refresh the last order-book timestamp.

## Open Positions Contract

Each open-position card must include:

- Clickable market title linking to `https://polymarket.com/event/{slug}` when a
  slug exists.
- Side badge: `YES` or `NO`.
- `LONG` badge.
- Forecast-weather badge between `LONG` and entry price. It comes from the
  latest decision note for the same `market_id`, parsing `mean=...F` and
  converting to Celsius.
- Entry price in cents, mark price in cents, unrealized PnL, city/date hint, and
  shares.

The forecast badge is for operator judgment. For example, if a market condition
is `29°C or higher` but the latest forecast badge is `28°C`, the operator can see
why a `NO` position may make sense.

## Realized PnL Contract

Rows are sorted newest first by `closed_at`.

Columns:

- `날짜`
- `도시`
- `예측날씨`
- `조건`
- `예상 청산`
- `진입`
- `청산`
- `PNL`
- `수익률`

Closed rows must avoid blank numeric cells. If historical logs are sparse, use
the best available fallback:

- Forecast weather: parsed forecast mean, then market threshold, then `0.0`.
- Expected exit: decision target, then actual exit, then entry, then `0.0`.
- Entry: open row price, then decision `p_exec`, then actual exit, then `0.0`.
- Exit: close row price, then entry, then `0.0`.
- ROI: computed from entry cost when possible, otherwise `0.0`.

## Equity / PnL Curve Contract

- The first chart point uses the earliest trade timestamp available from
  `paper_trades.csv`; if no trade exists, use the oldest available runtime
  timestamp and then the current generated time.
- The range buttons are `1일`, `7일`, `1개월`, `1Y`, and `ALL`.
- Hovering over the line shows a tooltip with date/time, PnL versus initial
  bankroll, and equity.
- The chart line is Poly Blue, with a subtle blue fill. Profit/loss semantics
  are still handled by metric colors outside the chart.

## Testing

Focused dashboard checks:

```powershell
$env:PYTHONPATH='src'; & 'C:\Users\wpdla\Python312\python.exe' -m pytest tests/test_dashboard.py -q
```

Before production use, also verify the page and API from the target host:

```powershell
curl.exe -i http://140.245.69.242:8787/
curl.exe -i "http://140.245.69.242:8787/api/status?token=<DASHBOARD_TOKEN>"
```

Do not print the real dashboard token in logs, docs, commits, or final answers.
