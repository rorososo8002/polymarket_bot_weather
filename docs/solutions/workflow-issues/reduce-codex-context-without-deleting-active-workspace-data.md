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

## 4. What To Check Next Time

- Before adding a new rule to `AGENTS.md`, ask whether every future task really
  needs it.
- Confirm that `docs/production-progress.md` is a current handoff, not a work
  diary.
- Confirm that `docs/active/current-task.md` contains only unfinished work and
  returns to `Status: none` when work is complete.
- Check that `docs/production-decisions.md` contains active rules, not a second
  chronological ledger.
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
