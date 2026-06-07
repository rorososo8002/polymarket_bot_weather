# Dashboard Build Spec

This file is the compact source of truth for rebuilding or modifying the
paper-trading dashboard.

## Purpose

The dashboard is an operator surface. It should show whether the paper bot is
alive, whether forecast/order-book data is fresh, what positions are open, and
what realized paper PnL has been recorded. It must not behave like an expensive
offline analytics job on every refresh.

## Visual System

- Dark mode only.
- Use Polymarket blue `#2E5CFF` for primary activity.
- Use green for profit, red for loss, and yellow only for caution.
- Keep cards at `8px` radius or less.
- Use Inter first, then system sans-serif fallback.
- Price display should use cents-style Polymarket formatting where useful.
- Temperature display should preserve the parsed unit and display Celsius when
  the UI needs a compact operator label.

## Data Contract

The dashboard reads generated runtime files:

```text
paper_state.json
paper_trades.csv
paper_decisions.csv
paper_event_portfolios.jsonl
forecast_cache.json
paper_runner_status.json
```

`paper_state.json` is the account book. It is not just a cache. It defines
current cash, open positions, average entry prices, and realized PnL.

`paper_trades.csv` is the receipt ledger. Show only executed paper actions in
trade-history panels: `OPEN`, `ADD`, `PARTIAL_CLOSE`, `CLOSE`, and `SETTLED`.

`paper_decisions.csv` is the strategy judgment ledger. SKIP rows are diagnostics,
not executed trades.

`paper_event_portfolios.jsonl` is portfolio-selection evidence. Read it with a
bounded tail read; do not load the entire file per refresh.

## Required Panels

- Account summary: cash, open position count, total entry cost, realized profit,
  realized loss, and portfolio/equity indicators.
- Open positions: market title/link, side, shares, entry price, mark price,
  unrealized PnL, city/date hint, and latest forecast context when available.
- Realized PnL: closed/settled/partial-close rows sorted newest first by parsed
  close time.
- Scanner/status: runner phase, forecast health, WebSocket health, recent
  market-evaluation errors, and latest event-portfolio decision summary.

## Performance Contract

- Do not materialize huge CSV/JSONL files in memory for every request.
- Read recent rows from the tail for live UI panels.
- Stream full ledgers only for report meanings that explicitly promise full
  history.
- Dashboard scanner totals must disclose scope:
  `decision_totals_exact=true` means full-ledger totals, while
  `decision_totals_scope=recent_tail` means large-file protection was used.

## Public Dashboard Security

Public hosts such as `0.0.0.0` or `::` require a real random `DASHBOARD_TOKEN`
with at least 32 characters. Public `/api/status` authentication must use the
`X-Dashboard-Token` header. Public `?token=...` API authentication must remain
rejected because URLs leak through history, logs, copied links, and screen
sharing.

Anyone who knows or discovers a public dashboard URL, including automated
scanners, can try to reach it.

## Verification

Focused dashboard checks:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q tests/test_dashboard.py
```

Before production use, also verify the page and authenticated API from the
target host. Do not print the real dashboard token in logs, docs, commits, or
final answers.
