# Current Task

Status: none

## Objective

- No unfinished work is currently recorded for fresh-chat continuation.

## Last Verified State

- Fresh-chat handoff now uses this active task card plus
  `docs/production-decisions.md` as the mandatory non-trivial read set.
- Historical process detail is archived under `docs/archive/`.
- Durable prevention rules live under `docs/solutions/`.

## Next Action

- If the user asks to continue work, follow the user's latest request.
- If the request touches strategy, trading-risk, forecast, order books,
  portfolio, paper accounting, settlement, or runner behavior, read
  `docs/production-implementation-plan.md` before changing behavior.

## Files In Play

- None.

## Non-Negotiables

- Preserve paper-only execution unless the user explicitly approves a separate
  live-trading safety pass.
- Do not delete or truncate runtime ledgers for token savings.
- Keep this card replace-only. Do not append completed work history here.

## New Chat Prompt

```text
Continue this project. Follow AGENTS.md. First read
docs/active/current-task.md and docs/production-decisions.md. If current-task
is active, continue from Next Action. If current-task is none, use my latest
request and read only the conditional documents needed for that task.
```
