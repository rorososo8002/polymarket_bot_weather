---
title: Atomic Paper State Writes And Fail-Closed Loads
date: 2026-06-03
last_updated: 2026-06-04
category: logic-errors
module: weather_bot.paper
problem_type: logic_error
component: service_object
symptoms:
  - "`PaperBroker.save_state()` wrote directly to `paper_state.json`."
  - "A partially written or structurally invalid paper state could leave the bot without a trustworthy cash and position ledger."
  - "`cash_usd` could load when negative, and `stats` values could be coerced from strings, booleans, fractional counts, or NaN."
  - "A position with an invalid side, non-finite shares, out-of-range entry price, negative cost, empty IDs, or non-object metadata could still load."
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags: [paper-trading, state-file, atomic-write, fail-closed, accounting, validation]
---

# Atomic Paper State Writes And Fail-Closed Loads

## 1. What The Problem Was

`paper_state.json` is the paper bot's account book. It stores cash, realized
PnL, and open positions. Before this fix, `PaperBroker.save_state()` wrote the
new JSON directly to that file.

If the process stopped during that write, the file could be left as broken JSON
such as a half-written object or an unfinished positions list.

## 2. Why It Was A Problem

Paper trading is still a trading simulation. The bot must know how much cash it
has and which positions it owns before it decides whether another entry is safe.

A corrupt state file means the bot does not have a reliable account book.
Continuing from guessed defaults could hide real exposure, overstate cash, or
erase open paper positions. In this project, unknown accounting state must
fail closed, which means the bot stops instead of making another trade decision
from unsafe data.

## 3. How It Was Fixed

`PaperBroker.save_state()` now writes the full JSON payload to a temporary file
in the same directory first. Only after that write succeeds does it replace the
real `paper_state.json` with `os.replace()`.

That sequence matters:

```text
write complete temporary file
  -> atomically replace paper_state.json
  -> remove leftover temp file if replacement fails
```

`PaperBroker.load_state()` now raises `PaperStateLoadError` when the state file
is corrupt JSON, unreadable, not a JSON object, missing required accounting
fields, or structurally invalid. Missing state files still start a fresh paper
account, because that is the normal first-run path. Existing but invalid state
files do not.

The loader also validates each saved open position before constructing a
`PaperPosition`. That matters because a position is not decorative history; it
is the current exposure used for risk limits, liquidation value, and later PnL.
The saved `entry_price` is the average entry price field in this codebase, so it
must stay in the binary-market price range from 0 to 1. `shares` must be a real
finite positive number, `side` must be `YES` or `NO`, `cost_usd` must be
non-negative, `market_id` and `token_id` must identify the held market and
token, and `metadata` must be a JSON object when present.

The loader now validates top-level account numbers and market-type stats with
the same fail-closed posture. `cash_usd` is the paper account's spendable cash,
so it must be a real finite number and cannot be negative. `realized_pnl_usd`
is the already-settled profit/loss total, so it may be positive or negative but
must still be a real finite number. Stats are the scorecard used for win rate
and cumulative PnL summaries: `wins` and `losses` must be non-negative integer
counts, while stats `pnl` must be a real finite number. String numbers,
booleans, fractional win/loss counts, and `NaN` are rejected instead of being
coerced into a believable-looking ledger.

## 4. What To Check Next Time To Prevent The Same Mistake

- For any ledger-like runtime file, test that the first write target is a temp
  file, not the live file.
- Spy on `os.replace()` in tests and assert the old live file still exists
  until replacement happens.
- Add a corrupt-file test that proves the bot raises a domain-specific error
  instead of quietly starting from default cash.
- Add field-level position fixtures for bad sides, string or NaN shares,
  out-of-range entry prices, negative costs, empty IDs, and non-object metadata.
- Add account-level fixtures for negative cash, non-numeric cash, non-finite
  realized PnL, non-integer stats counts, negative stats counts, and non-finite
  stats PnL.
- Preserve the corrupt file for investigation; do not overwrite it during
  startup failure handling.
- Keep normal first-run behavior separate from corrupt-file behavior: missing
  file can initialize, existing invalid file must fail closed.

## 5. What This Project Must Be Especially Careful About

`paper_state.json` is not just a cache. A cache can usually be rebuilt; this
file is the paper strategy's account book. If it is wrong, every later risk
calculation is built on the wrong cash and exposure.

Whenever the bot changes cash, positions, or realized PnL, state persistence
must protect the existing good file until the new file is fully written. When
state loading is uncertain, paper trading should pause and make the failure
visible instead of guessing.

## Related Issues

- `src/weather_bot/paper.py`
- `tests/test_paper_state_io.py`
- `docs/solutions/logic-errors/paper-fees-must-flow-through-accounting.md`
