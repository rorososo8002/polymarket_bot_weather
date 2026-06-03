---
title: Separate SKIP diagnostics from trades and settle exact closed markets
date: 2026-06-03
category: logic-errors
module: weather_bot.dashboard, weather_bot.paper
problem_type: logic_error
component: service_object
severity: high
symptoms:
  - "The dashboard showed no realized trades even though paper closes existed in the ledger."
  - "`Recent Trades` was filled with `SKIP_CITY_CAP` rows instead of executed paper trades."
  - "Closed, past-date weather positions stayed visible as open positions."
root_cause: logic_error
resolution_type: code_fix
tags: [dashboard, paper-trading, settlement, skip-diagnostics, outcome-prices]
---

# Separate SKIP diagnostics from trades and settle exact closed markets

## Problem

The dashboard mixed two different kinds of ledger rows. `paper_trades.csv`
contains actual paper actions such as `OPEN` and `CLOSE`, but it also contains
diagnostic rows such as `SKIP_CITY_CAP`. A burst of SKIPs at the end of the file
hid older real closes, so the dashboard looked like there were no realized
trades.

At the same time, some past-date Polymarket weather markets were `closed=True`
but had empty explicit winner fields. The paper broker only settled when fields
such as `winningOutcome` were present, so clear closed markets could remain in
`paper_state.json` as open positions.

There was one more execution-path trap: the batch `run_cycle()` path checked
settlements, but the actual long-running realtime WebSocket service path did
not run settlement before starting its stream cycle. A settlement helper can be
correct and still have no production effect if the service loop never calls it.

## Symptoms

- `Recent Trades` showed `SKIP_CITY_CAP` cards instead of buy/close activity.
- The realized PnL table said there were no closed trades.
- Old weather questions, such as May markets viewed on June 3, still appeared
  in open positions.
- The bot logs repeated `no websocket orderbook snapshot` mark errors for
  already-closed markets whose order books were no longer useful for normal
  exit pricing.

## What Didn't Work

- Refreshing the dashboard did not fix the display. The page was faithfully
  rendering the latest API payload; the payload itself was built from the wrong
  trade slice.
- Looking only at the last few hundred `paper_trades.csv` rows was misleading.
  Those rows can be all SKIPs while older `OPEN` and `CLOSE` rows still matter
  for paper performance.
- Treating every `closed=True` market as settled would be unsafe. A closed
  market without a clear YES/NO winner should not be guessed.
- Verifying only `run_cycle()` was incomplete because the deployed service uses
  `run_realtime_forever()` for the long-running WebSocket path.

## Solution

Keep the dashboard's trade-history cache, but separate the action types:

- Actual paper trade rows: `OPEN`, `CLOSE`, `SETTLED`, `PARTIAL_CLOSE`
- Realized rows: `CLOSE`, `SETTLED`, `PARTIAL_CLOSE`
- Diagnostic rows: `SKIP_*`, `HOLD_*`, and other non-execution reasons

`Recent Trades`, realized rows, and realized equity points now read from the
cached actual-trade rows instead of the raw tail of `paper_trades.csv`. This
means a large SKIP burst can still exist in the source ledger without hiding
older closes on the dashboard.

For settlement, keep explicit winner fields as the first choice. If they are
missing, infer the winner only when the closed binary market has exact
`outcomePrices`:

```text
Yes=1 and No=0 -> YES wins
Yes=0 and No=1 -> NO wins
anything else -> do not settle
```

This fixed the closed-weather-market case without turning ambiguous prices into
guessed paper PnL.

The realtime runner now applies that same settlement check immediately after it
hydrates held-position markets and before it starts WebSocket subscriptions.
If a held market settles, the runner removes that market from the stream token
set so the bot does not keep waiting for order-book snapshots from a resolved
market.

## Why This Works

`paper_trades.csv` is a source ledger, not a clean UI feed. The dashboard must
translate that ledger into operator-friendly views. Separating diagnostic rows
from executed trade rows preserves both meanings: SKIPs stay available for
investigation, while the trade panels show actual paper trading.

`outcomePrices` are also useful only at the exact binary payout boundary. A
price of `1` means that side pays one dollar at settlement, and `0` means the
other side lost. A price such as `0.52` is not a final winner signal, so the bot
must keep failing closed.

## Prevention

- When a dashboard panel says "trades", test it with many trailing SKIP rows.
  The panel should still show older `OPEN`/`CLOSE` activity.
- When a closed market is still in `paper_state.json`, check both explicit
  winner fields and exact binary `outcomePrices`.
- Test the actual long-running service path, not only a one-cycle helper path,
  whenever a fix is meant to affect the live VPS bot.
- Do not truncate runtime ledgers to fix UI problems. Fix the reader or cache.
- Keep tests for both safe paths: exact `1/0` settlement should close, and
  ambiguous prices should leave the paper position open.
- For this project, paper-only accounting is the evidence base. A misleading
  dashboard can make a strategy look broken or healthy for the wrong reason.

## Related Issues

- [Keep live dashboard refreshes small and operator-focused](../performance-issues/dashboard-live-refresh-payload-cost.md)
- [Avoid full decision-log scans on dashboard startup](../performance-issues/dashboard-large-decision-log-initial-scan.md)
- [Subscribe open position tokens even after market discovery rolls forward](./open-position-stream-subscription-drift.md)
