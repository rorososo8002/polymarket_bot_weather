---
title: Paper State And Trade Ledger Updates Need A Transaction Journal
date: 2026-06-06
last_updated: 2026-06-06
category: logic-errors
module: weather_bot.paper
problem_type: logic_error
component: service_object
symptoms:
  - "`paper_state.json` could be saved while the matching `paper_trades.csv` row failed to append."
  - "`PARTIAL_CLOSE` could append a trade row before the account state save failed."
  - "Startup could load open positions even when the trade ledger was missing or an interrupted update needed operator review."
  - "Startup could treat a missing `paper_state.json` as a fresh account even when executed trade rows already existed."
  - "Startup could load an open position whose matching `OPEN` row was absent from `paper_trades.csv`."
root_cause: missing_workflow_step
resolution_type: code_fix
severity: high
tags: [paper-trading, accounting, transaction-journal, ledger, fail-closed]
---

# Paper State And Trade Ledger Updates Need A Transaction Journal

## 1. What The Problem Was

`paper_state.json` is the paper bot's account book. It stores current cash,
open positions, average entry prices, realized PnL, and stats.

`paper_trades.csv` is the execution ledger. It records the actual paper actions
such as `OPEN`, `ADD`, `CLOSE`, and `PARTIAL_CLOSE`.

Before this fix, those two ledgers were not updated as one protected unit. Some
paths saved state first and logged the trade second. `PARTIAL_CLOSE` logged the
trade first and saved state second.

## 2. Why It Was A Problem

If the process or disk failed between those two writes, the bot could continue
from a half-updated accounting story:

- State changed but no trade row exists.
- Trade row exists but state did not change.
- In-memory broker state stayed mutated after a failed save.

That breaks the paper experiment. The bot is not sending real orders, but the
paper ledgers are the evidence used to judge whether the strategy might ever
deserve live-trading research. If the account book and execution diary disagree,
profit, drawdown, exposure, Brier scoring, and dashboard interpretation all
become suspect.

## 3. How It Was Fixed

Executed accounting actions now run through a small transaction journal:

```text
write paper_state.json.journal
  -> mutate in-memory account state
  -> save paper_state.json atomically
  -> append the matching paper_trades.csv row
  -> clear paper_state.json.journal
```

If any step fails, the broker leaves `paper_state.json.journal` in place and
raises `PaperAccountingTransactionError`. Later accounting writes in the same
process fail closed, and the next `PaperBroker` startup raises
`PaperStateLoadError` so the operator must reconcile the ledgers before
trading continues.

If state saving fails before anything reaches disk, the broker rolls the
in-memory `PaperState` back to the snapshot from before the attempted action.
If the state save succeeded but trade logging failed, the journal remains
because the disk state may already have changed.

The journal and state-file atomic replace path also retries short transient
`PermissionError` failures. This is mainly for Windows local verification,
where a temp file or destination can be briefly locked while the atomic replace
is otherwise safe. The retry window is small; persistent failures still leave
the journal and fail closed for operator reconciliation.

Startup also catches obvious state/trade evidence drift. If
`paper_state.json` is missing but `paper_trades.csv` already contains executed
accounting actions, the broker refuses to start a fresh account over that
execution ledger. If `paper_state.json` has open positions, startup checks that
`paper_trades.csv` exists, has core columns such as `action`, `market_id`,
`side`, and `token_id`, and contains a matching `OPEN` row for each open
position's market, side, and token. The check streams trade rows and stops
early once every open position is matched.

Existing trade CSVs are no longer rewritten just to add newer columns. New
files use the current full header, while legacy files keep their historical
header and report code falls back when structured entry metadata is absent.

## 4. What To Check Next Time To Prevent The Same Mistake

- For every executed paper action, test both `save_state()` failure and
  `log_trade()` failure.
- Test all money-changing paths separately: `OPEN`, `ADD`, `CLOSE`, and
  `PARTIAL_CLOSE`.
- Assert that failed saves roll back in-memory state when disk state has not
  been committed.
- Assert that failed trade logging leaves a journal so startup fails closed.
- Test startup evidence drift in both directions: executed trade rows without
  `paper_state.json`, and open state positions without matching `OPEN` rows.
- Simulate one transient `PermissionError` on journal replacement so Windows
  file-lock timing does not create flaky full-suite failures.
- Do not rewrite evidence ledgers for schema migration convenience. Preserve
  legacy headers and make readers backward-compatible.
- Add fixture trade rows whenever a test creates open positions directly in
  `paper_state.json`.

## 5. What This Project Must Be Especially Careful About

Paper trading is the measurement layer. A wrong paper ledger can make an unsafe
strategy look profitable, or make a useful strategy look broken.

The safe rule is:

```text
account book and execution ledger move together
if either side is uncertain, stop
do not delete, truncate, or rewrite evidence to make startup easier
```

`paper_state.json.journal` is not noise. It is an operator-visible warning that
the ledgers may need reconciliation.

## Related Issues

- [Atomic Paper State Writes And Fail-Closed Loads](atomic-paper-state-write-fail-closed-load.md)
- [Brier Score Must Use OPEN Entry Probability](brier-score-must-use-open-entry-probability.md)
- [Rotate raw snapshots without truncating paper ledgers](../workflow-issues/rotate-raw-snapshots-without-truncating-ledgers.md)
