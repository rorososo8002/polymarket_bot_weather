# Production Progress

This file is an optional compact board. The default resume source is
`docs/active/current-task.md`.

## Current State

- No active implementation, deployment, or debugging work is in progress.
- The project is a paper-only temperature-market bot.
- Runtime ledgers are intentionally ignored by git and should start fresh only
  when the user asks for a fresh experiment window.

## Next Work

1. Use `docs/active/current-task.md` for unfinished work.
2. Use `docs/production-decisions.md` for active safety and trading rules.
3. Use `docs/production-implementation-plan.md` when changing strategy,
   forecast, order-book, portfolio, accounting, settlement, dashboard runtime,
   or runner behavior.
4. Use `docs/codex/known-good-commands.md` before local pytest, VPS/SSH,
   deployment, or dashboard verification.
5. Diagnose repeated SKIPs before changing thresholds, risk caps, or data-source
   assumptions.
6. Keep live trading, wallet connection, private keys, real orders, and copy
   trading out of scope unless the user explicitly approves a separate
   live-trading safety pass.

## For The Next AI

Read `AGENTS.md`, `docs/active/current-task.md`, and
`docs/production-decisions.md` first. If `current-task.md` says `Status: none`,
start from the user's latest request and read only the conditional docs needed
for that request.
