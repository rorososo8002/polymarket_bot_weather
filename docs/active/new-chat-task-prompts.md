# New Chat Task Prompts

Status: active

## Active Prompt

```text
Continue this project. Follow AGENTS.md. First read
docs/active/current-task.md and docs/production-decisions.md.

The explicit strategy-validation gap plan has been implemented, focused-tested,
and pushed to origin/main. Do not reimplement work items 1 through 12.

Deploy latest origin/main (currently 8935bf9) to the Oracle VPS using
docs/codex/known-good-commands.md, restart affected services, and verify the
live dashboard HTML plus authenticated /api/status without printing secrets.

Keep execution paper-only. Do not add wallet, private-key, signing, real-order,
redemption, claim, copy-trading, or LiveBroker behavior.
```

## Purpose

Use this file only when the user wants a new chat or delegated step to continue
from a precise one-shot prompt.

This file is not the active task card and not a backlog. The active unfinished
work source remains `docs/active/current-task.md`.

## Update Rule

- Replace this file's active prompt instead of appending history.
- Keep at most one active prompt.
- When that prompt is completed, replace it with the next single prompt or set
  `Status: none` and `Active Prompt` to `none`.
