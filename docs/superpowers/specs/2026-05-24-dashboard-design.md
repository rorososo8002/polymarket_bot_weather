# Weather Bot Dashboard Design

Status: Historical seed spec. The current rebuild contract is
`docs/dashboard-build-spec.md`.

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

The current dashboard uses a Polymarket-style dark trading layout:

- Left: open positions with clickable Polymarket market links.
- Center: metrics, equity/PnL chart with range buttons and hover PnL tooltip,
  plus realized PnL.
- Right: scanner intelligence and recent trades.

Open-position cards show market title, side, long badge, forecast-weather badge,
entry price in cents, mark price in cents, unrealized PnL, city, date hint, and
shares.

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
