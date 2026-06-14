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

`new-chat-task-prompts.md` is only for explicit new-chat handoffs or
step-by-step delegation. It is a single-use prompt note, not a backlog.

## New Chat Prompt

Use this when starting a new chat and you want the agent to continue correctly:

```text
Continue this project. Follow AGENTS.md. First read
docs/active/current-task.md and docs/production-decisions.md. If current-task
is active, continue from Next Action. If current-task is none, use my latest
request and read only the conditional documents needed for that task.

For new-chat handoff or step-by-step delegation, also read
docs/active/new-chat-task-prompts.md and follow only its active prompt.

For strategy-validation work, also read docs/production-implementation-plan.md
and docs/strategy-validation-roadmap.md. Keep the project paper-only unless I
explicitly approve a separate live-trading safety project.
```

## Update Rule

- Replace fields in `current-task.md`; do not append a work diary.
- Use `Status: active` only for real unfinished work.
- When work is complete, set `Status: none`.
- Keep `new-chat-task-prompts.md` replace-only. Remove completed prompts and
  leave `none` when there is no one-shot handoff prompt.
- Put completion evidence in git commits, tests, or reusable
  `docs/solutions/` notes, not in the active task card.
