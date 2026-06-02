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

Progress docs are kept short and current with these sections:

- `Completed`
- `In Progress`
- `Next Work`
- `For The Next AI`

Older chronological details should move to `docs/archive/`, while durable
lessons should move to `docs/solutions/`.

## 4. What To Check Next Time

- Before adding a new rule to `AGENTS.md`, ask whether every future task really
  needs it.
- Confirm that `docs/production-progress.md` is a current handoff, not a work
  diary.
- Inspect large files with bounded reads.
- Do not delete `.git/`, `runtime/`, or `.antigravitycli/` for token savings.

## 5. Project-Specific Caution

Oracle VPS logs and paper-trading runtime files are operational evidence. Use
bounded reads for `paper_decisions.csv`, `paper_trades.csv`,
`paper_raw_snapshots.jsonl`, `forecast_cache.json`, and state files. Do not trim
away safety rules about paper-only trading, SSH key protection, fail-closed
behavior, or WebSocket subscription retention.
