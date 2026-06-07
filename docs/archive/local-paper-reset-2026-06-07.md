# Local Paper Reset - 2026-06-07

The local paper-trading evidence window was reset to a fresh 200 USD account.

Archived previous local runtime files under:

```text
runtime/archive/local-paper-reset-20260607-151554/
```

Reset files:

- `paper_state.json` now starts with 200.0 USD cash, zero realized PnL, and no positions.
- `paper_trades.csv` was recreated with the current trade header and no trade rows.
- `paper_decisions.csv` was recreated with the current decision header and no decision rows.
- `.env` sets `BANKROLL_USD=200` for the local paper experiment.

This is an operator-requested fresh measurement window, not routine runtime
cleanup. Do not mix archived pre-reset rows with post-reset profitability
results.
