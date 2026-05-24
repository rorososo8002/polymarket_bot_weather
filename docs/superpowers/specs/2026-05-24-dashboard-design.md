# Weather Bot Dashboard Design

## Goal

Build a 24-hour human dashboard for the paper-trading bot so the operator can
see what markets the bot is scanning, what it opened or closed, and whether the
strategy is winning or losing without reading CSV files or spending Codex tokens.

## Architecture

- Add `weather-dashboard`, a Python standard-library HTTP server.
- Keep it read-only: it never places orders or mutates paper state.
- Read the existing runtime files:
  - `paper_state.json`
  - `paper_trades.csv`
  - `paper_decisions.csv`
  - `paper_raw_snapshots.jsonl`
- Serve:
  - `/` for the dashboard UI
  - `/api/status` for JSON status used by browser polling
  - `/health` for service checks

## UI

The dashboard uses a dark terminal-style operations layout:

- Left: event stream of decisions, opens, closes, skips, and errors.
- Center: metrics and equity/PnL graph, plus open positions and recent trades.
- Right: scanner intelligence, candidate/skip counts, signal pressure bars, and
  recent candidate cards.

Open-position cards show the market title, side, long badge, entry price with
`E` notation, mark price, unrealized PnL, city, date hint, and shares.

## Security

The service supports `DASHBOARD_TOKEN`. On VPS, the systemd env requires a
non-empty long random token and the URL is:

```text
http://SERVER_IP:8787/?token=YOUR_DASHBOARD_TOKEN
```

Without a token, the dashboard can still run locally for development on
`127.0.0.1`.

## Deployment

Add:

- `deploy/systemd/polymarket-weather-dashboard.service`
- `deploy/systemd/dashboard.env.example`

The dashboard runs separately from `polymarket-weather-bot`, so the UI can be
restarted without restarting the trading loop.

## Testing

Unit tests cover payload summarization from state, trades, and decisions. The
deployment tests verify the dashboard service and env templates.
