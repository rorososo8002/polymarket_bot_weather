---
title: Market evaluation errors must write diagnostics
date: 2026-06-07
category: logic-errors
module: weather_bot.live_paper_runner
problem_type: logic_error
component: background_job
symptoms:
  - "A per-market evaluation exception could appear only as a console ERROR or MARK ERROR message."
  - "paper_decisions.csv could miss the failed market, making the dashboard look healthier than the runner was."
  - "paper_runner_status.json could lose the last per-market failure after later status writes."
root_cause: missing_workflow_step
resolution_type: code_fix
severity: high
tags: [paper-trading, decision-log, runner-status, raw-snapshots, fail-closed, dashboard]
---

# Market evaluation errors must write diagnostics

## 1. What The Problem Was

The paper runner handled some per-market evaluation exceptions by printing an
`ERROR` or `MARK ERROR` message. That was not enough. A single failed market
could disappear from the durable paper evidence if the exception happened before
`paper_decisions.csv` received a row.

`paper_decisions.csv` is the strategy judgment ledger. It is not just a cache.
It tells the operator which markets were evaluated, opened, skipped, or rejected.
If an exception skips that ledger, later reports and dashboards cannot explain
the missing market.

## 2. Why It Was A Problem

The dashboard reads durable files, not the operator's terminal memory. If the
terminal saw an error but `paper_decisions.csv` and `paper_runner_status.json`
did not preserve it, the bot could look normal even though one market failed.

For this project, that matters because paper trading is the experiment. We are
checking whether the strategy can make money safely over time. Missing failures
make the experiment look cleaner than it really was, and that can tempt unsafe
threshold changes or, worse, confidence in a strategy that did not actually run
cleanly.

## 3. How It Was Fixed

Per-market evaluation failures now use one shared diagnostic path:

- Write a `SKIP_ERROR` row to `paper_decisions.csv`.
- Write a `market_evaluation_error` row to `paper_raw_snapshots.jsonl`.
- Update `paper_runner_status.json` with `market_error_count` and
  `last_market_error`.
- Continue to skip the failed market instead of guessing a probability, price,
  or trade.

`SKIP_ERROR` is still a skip. It means "do not trade this market." The extra
`ERROR` part tells operators that this was not a normal strategy filter such as
low confidence or weak edge. It was an exception during evaluation.

The dashboard and analysis readers now count any decision side starting with
`SKIP` as a skip, so `SKIP_ERROR` remains in the diagnostic bucket instead of
being mistaken for an entry.

## 4. What To Check Next Time To Prevent The Same Mistake

- When catching a market-level exception, ask where the evidence lands:
  `paper_decisions.csv`, `paper_raw_snapshots.jsonl`, and
  `paper_runner_status.json` should all tell the same story.
- Add a regression test with one fake market whose probability refresh or
  evaluation raises an exception.
- Assert that the failed market gets a decision row, an error raw snapshot, and
  runner-status `market_error_count` / `last_market_error`.
- Make later status writes preserve the per-market error fields until the next
  runner cycle intentionally resets them.

## 5. What This Project Must Be Especially Careful About

Fail closed means skip, not guess. If a forecast refresh, order-book read,
parser path, or realtime update fails for a market, the bot must not invent a
probability or fill price just to keep the cycle moving.

Paper-only execution is also part of the safety rule. Error handling must never
connect wallets, sign orders, submit real orders, or introduce live-trading
fallbacks. The correct response to uncertain market evaluation is observable
paper diagnostic evidence plus no trade.

Related docs:

- [Realtime cycle exceptions must update runner status](realtime-cycle-exceptions-must-update-runner-status.md)
- [Empty decision CSV must still receive its header](empty-decision-csv-must-write-header.md)
- [Rotate raw snapshots without truncating paper ledgers](../workflow-issues/rotate-raw-snapshots-without-truncating-ledgers.md)
