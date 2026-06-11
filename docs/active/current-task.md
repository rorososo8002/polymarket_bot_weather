# Current Task

Status: active

## Objective

Fix the review findings and production drift around forecast cadence, nowcast
freshness, paper reporting, dashboard TTL display, runtime disk safety, and
WebSocket/REST order-book verification. Keep execution paper-only.

## Current Scope

- Keep Open-Meteo real calls cache-protected: 40 trading-ready cities × 8
  batches/day × 31 units = 9 920 units/day.
- Keep station nowcast separate from forecast freshness: AWC METAR 5 min, HKO
  10 min provider floor.
- Keep WebSocket as the primary order-book monitor; REST snapshots are bounded
  in-memory verification/resync only.
- Fix daily report PnL to read `cash_delta_or_pnl`.
- Align docs, `.env.example`, deploy env examples, and tests.
- Prevent large test/runtime artifacts from being read or persisted casually.

## Next Action

Local implementation and full pytest are complete. Commit the verified diff,
push it, then deploy to the Oracle VPS using
`docs/codex/known-good-commands.md`.

## New Chat Prompt

```text
Continue this project. Follow AGENTS.md. First read
docs/active/current-task.md and docs/production-decisions.md. If current-task
is active, continue from Next Action. If current-task is none, use my latest
request and read only the conditional documents needed for that task.
```
