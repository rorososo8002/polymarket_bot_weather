---
title: Replay Paper Trades Before Startup
date: 2026-06-07
category: logic-errors
module: weather_bot.paper, weather_bot.analyze_paper
problem_type: logic_error
component: service_object
symptoms:
  - "`paper_state.json` startup validation only proved that open positions had matching `OPEN` rows."
  - "`ADD`, `PARTIAL_CLOSE`, `CLOSE`, and `SETTLED` rows could drift from saved cash, shares, or cost basis without being caught."
  - "`analyze_paper.py` could report resolved rows without warning about missing open evidence."
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags: [paper-trading, accounting, ledger-replay, fail-closed, reports]
---

# Replay Paper Trades Before Startup

## 1. What The Problem Was

`paper_state.json` is the paper account book: it stores current cash, realized
PnL, and still-open positions. `paper_trades.csv` is the receipt ledger: it
records executed paper actions such as `OPEN`, `ADD`, `PARTIAL_CLOSE`, `CLOSE`,
and `SETTLED`.

Before this fix, startup mostly checked that each open position had a matching
`OPEN` receipt. That was not enough to prove that later add-ons, partial closes,
full closes, or settlement rows still agreed with the saved account book.

## 2. Why It Was A Problem

Paper trading is the experiment used to judge whether the strategy might be
profitable. If the saved state says a position has one share count or cost
basis, but the receipt ledger says another, every later risk check and PnL
calculation is built on a false account.

The dangerous case is quiet drift. A bot that starts from mismatched cash or
cost basis may keep trading and make the 200 USD experiment look better or worse
than it really is.

## 3. How It Was Fixed

Startup now streams `paper_trades.csv` from top to bottom and rebuilds the
accounting state from `BANKROLL_USD`:

```text
OPEN -> create a position and subtract spent cash
ADD -> increase the same position's shares and cost basis
PARTIAL_CLOSE -> reduce shares and cost basis by the closed fraction
CLOSE / SETTLED -> remove the position and apply realized PnL
```

The replayed cash, realized PnL, open-position identity, shares, cost basis, and
average entry price must match `paper_state.json`. If they do not match, the
broker fails closed and refuses to start paper trading.

`analyze_paper.py` now also prints report warnings for evidence gaps such as a
`CLOSE` without a prior `OPEN`, duplicate `OPEN` rows for the same position, or
a missing `paper_decisions.csv` file.

## 4. What To Check Next Time To Prevent The Same Mistake

- Add a test where `OPEN` is followed by `PARTIAL_CLOSE`, then make saved
  `shares` wrong and expect startup to fail.
- Add a test where `OPEN` is followed by `ADD`, then make saved `cost_usd`
  wrong and expect startup to fail.
- Include `CLOSE` or `SETTLED` rows when testing cash and realized PnL replay.
- Report analysis warnings when a resolved trade has no open-entry evidence.
- Keep the replay streaming; do not materialize full runtime CSV ledgers in
  memory.

## 5. What This Project Must Be Especially Careful About

Do not mix archived pre-reset ledgers with the current 200 USD experiment. The
startup replay uses the current `BANKROLL_USD` and current receipt ledger as one
measurement window. If old archived rows are copied back into the active
`paper_trades.csv`, the replay should fail because the active account book and
receipt ledger no longer describe the same experiment.

## Related Issues

- `src/weather_bot/paper.py`
- `src/weather_bot/analyze_paper.py`
- `tests/test_paper_state_io.py`
- `tests/test_analyze_paper.py`
- `docs/solutions/logic-errors/atomic-paper-state-write-fail-closed-load.md`
- `docs/solutions/logic-errors/paper-fees-must-flow-through-accounting.md`
