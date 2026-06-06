# Active Work Handoff

This directory exists for one purpose: resume unfinished work without turning
the project documentation into a diary.

## Mandatory Fresh-Chat Read Set

For non-trivial work, read these first:

1. `AGENTS.md`
2. `docs/active/current-task.md`
3. `docs/production-decisions.md`

`current-task.md` is the active task card. If it says `Status: active`, continue
from its `Next Action`. If it says `Status: none`, there is no unfinished task
to resume; use the user's latest request and the conditional reads in
`AGENTS.md`.

## New Chat Prompt

Use this when starting a new chat and you want the agent to continue correctly:

```text
Continue this project. Follow AGENTS.md. First read
docs/active/current-task.md and docs/production-decisions.md. If current-task
is active, continue from Next Action. If current-task is none, use my latest
request and read only the conditional documents needed for that task.
```

## Update Rule

- Replace fields in `current-task.md`; do not append a work diary.
- Use `Status: active` only for real unfinished work.
- When work is complete, set `Status: none`.
- Put completion history in git commits, tests, `docs/archive/`, or
  `docs/solutions/`, not in the active task card.
