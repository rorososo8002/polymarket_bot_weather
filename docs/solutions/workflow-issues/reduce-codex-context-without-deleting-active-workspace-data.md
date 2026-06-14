---
title: Reduce Codex Context Without Deleting Active Workspace Data
date: 2026-06-07
last_updated: 2026-06-14
category: workflow-issues
module: documentation
problem_type: workflow_issue
component: documentation
severity: medium
applies_when:
  - Fresh-chat handoff docs are growing beyond their tested compact contracts
  - Workflow tests depend on exact documentation headings or line limits
  - New-chat handoff prompts need to survive as single-use notes, not process diaries
tags: [context-management, handoff-docs, workflow-tests, documentation]
---

# Reduce Codex Context Without Deleting Active Workspace Data

## 1. What Went Wrong

Large runtime files and long handoff documents made the workspace expensive to
read. The tempting shortcut was to delete active runtime data or remove useful
handoff detail just to reduce context size.

## 2. Why It Mattered

Runtime data is evidence. Paper decisions, trades, raw snapshots, forecast
caches, and runner status files explain what the bot actually did. Deleting them
can make later debugging or performance review impossible.

The correct problem was not "too many files exist"; it was "read only the amount
needed for the current task."

## 3. How It Was Fixed

`AGENTS.md` now tells agents not to open large runtime files in full. It points
to safer inspection methods: file sizes, counts, tails, filters, summaries, and
small samples.

Progress docs are kept short and current. Older chronological details stay out
of the default read set, while durable lessons move to `docs/solutions/`.

On 2026-06-07, the handoff files were compacted into distinct roles:

- `docs/active/current-task.md` is the only default unfinished-work card.
- `docs/production-progress.md` is an optional compact board.
- `docs/production-decisions.md` is the active rule book.
- `docs/production-implementation-plan.md` is the strategy contract, read only
  when the task touches strategy, risk, forecast, order books, portfolio,
  accounting, settlement, or runner behavior.

The default fresh-chat read set should not be used as a chronological diary.

On 2026-06-13, `tests/test_workflow_defaults.py` caught two preventable
handoff-doc drifts:

- `AGENTS.md` must keep the exact heading text `Mandatory fresh-chat read set`
  because the test searches for that phrase.
- `docs/production-implementation-plan.md` must stay under 350 splitlines.
  Long runtime default tables belong in `.env.example` and
  `src/weather_bot/config.py`; the implementation plan should keep only the
  contract-level anchors a future AI needs before coding.

On 2026-06-14, `docs/active/new-chat-task-prompts.md` had to be restored as a
separate single-use prompt note. The lesson is that context reduction should
not collapse distinct handoff roles into one file. `current-task.md` answers
"what unfinished work is active right now?" while `new-chat-task-prompts.md`
answers "what exact one-shot prompt should a new chat or delegated step follow?"
Those are different jobs.

## 4. What To Check Next Time

- Before adding a new rule to `AGENTS.md`, ask whether every future task really
  needs it.
- Confirm that `docs/production-progress.md` is a current handoff, not a work
  diary.
- Confirm that `docs/active/current-task.md` contains only unfinished work and
  returns to `Status: none` when work is complete.
- Confirm that `docs/active/new-chat-task-prompts.md` exists, stays
  replace-only, and contains `Status: none` when there is no explicit one-shot
  handoff prompt.
- Check that `docs/production-decisions.md` contains active rules, not a second
  chronological ledger.
- Run `tests/test_workflow_defaults.py` after editing AGENTS, active handoff
  docs, production decisions, or the implementation plan.
- Preserve exact tested headings. If a test asserts a phrase, treat that phrase
  as an interface, not copy style.
- Keep bulky default-value lists in their canonical config files and summarize
  only the production contract in default-read documents.
- Keep old process history out of active handoff docs unless every future agent
  truly needs it during startup.
- Inspect large files with bounded reads.
- Do not delete `.git/`, `runtime/`, or `.antigravitycli/` for token savings.

## 5. Project-Specific Caution

Oracle VPS logs and paper-trading runtime files are operational evidence. Use
bounded reads for `paper_decisions.csv`, `paper_trades.csv`,
`paper_raw_snapshots.jsonl`, `forecast_cache.json`, and state files. Do not trim
away safety rules about paper-only trading, SSH key protection, fail-closed
behavior, or WebSocket subscription retention.
