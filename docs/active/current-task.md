# Current Task

Status: none

## Objective

- No unfinished work is currently recorded for fresh-chat continuation.

## Last Verified State

- Per-market evaluation exceptions now fail closed into observable
  `SKIP_ERROR` decision rows, `market_evaluation_error` raw snapshots, and
  runner-status `market_error_count` / `last_market_error` fields.
- Focused runner hardening tests passed:
  `86 passed`.
- Dashboard/analyze regression tests passed:
  `50 passed`.
- Full pytest passed:
  `467 passed`.

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
