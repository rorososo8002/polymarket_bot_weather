---
title: Make Handoff Plans Executable, Not Just Explanatory
date: 2026-06-14
last_updated: 2026-06-14
category: workflow-issues
module: documentation
problem_type: workflow_issue
component: documentation
severity: medium
applies_when:
  - User asks for a plan that a new chat should execute
  - User asks to compare an attachment with the current repository and continue development
  - Documentation repair work could be mistaken for implementation planning
  - A ready plan should exist without becoming the default active task
tags: [handoff-docs, execution-plan, new-chat, documentation]
---

# Make Handoff Plans Executable, Not Just Explanatory

## Context

The user asked Codex to compare attached strategy advice against the current
repository, preserve completed behavior, reject misleading advice, identify
real gaps, and produce a plan that a new chat could use to continue
development.

The first pass produced a documentation reference plan. That helped explain the
strategy and document structure, but it did not fully satisfy the user's goal:
the user wanted an execution artifact that a fresh agent could follow without
repeating already completed work.

The later cleanup created the executable gap plan, then reset the active docs
to `Status: none` because the plan was ready for future explicit execution, not
an unfinished task already in progress.

## Guidance

When a user says a plan should be given to a new chat, create an executable
handoff plan, not only an explanatory reference.

An executable handoff plan should include:

- the exact plan path
- scope boundaries and explicit non-goals
- code areas to inspect
- ordered execution units
- acceptance criteria per unit
- focused test commands
- cleanup rules for `docs/active/current-task.md` and
  `docs/active/new-chat-task-prompts.md`

Reference docs can still exist, but they must clearly say they are references
and point to the executable plan.

Do not leave a ready plan in `docs/active/current-task.md` unless the user has
actually started that implementation and work remains unfinished. A ready plan
is a map. `current-task.md` is the active resume card. Mixing those roles makes
future agents treat optional or already completed work as mandatory startup
work.

For this project:

- keep durable rules in `AGENTS.md` and `docs/production-decisions.md`
- keep active unfinished work only in `docs/active/current-task.md`
- keep one-shot delegation prompts only in `docs/active/new-chat-task-prompts.md`
- keep reusable implementation plans under `docs/plans/`
- set active docs back to `Status: none` when the current work is complete

## Why This Matters

A reference note answers "what is this project about?"

An execution plan answers "what should the next AI do first, how does it know it
is done, and what must it not touch?"

An active task card answers a third question: "what was already started and must
be resumed now?" That is narrower than "what could be implemented next."

Those are different jobs. If they are mixed up, a future agent can repeat
completed work, skip missing implementation gaps, or spend startup context
re-analyzing a plan that the user did not ask it to execute.

## Example

For this repository, the correct handoff shape after the 2026-06-14
strategy-validation documentation cleanup is:

- executable plan:
  `docs/plans/2026-06-14-001-strategy-validation-gap-closure-plan.md`
- human-readable HTML companion:
  `docs/plans/2026-06-14-001-strategy-validation-gap-closure-plan.html`
- documentation planning reference:
  `docs/strategy-validation-documentation-plan.md`
- active resume card:
  `docs/active/current-task.md` with `Status: none` unless implementation is
  actively unfinished
- one-shot new chat prompt holder:
  `docs/active/new-chat-task-prompts.md` with `Status: none` unless an explicit
  handoff prompt is currently needed

## When To Apply

Apply this rule whenever the user asks for a plan, implementation plan,
revision plan, new-chat handoff, or a document that another AI should execute.

Before finishing, check whether the deliverable can be handed to a fresh agent
without additional explanation. If not, it is not yet an execution plan.

Then check the inverse: if the deliverable is only ready for future explicit
execution, make sure startup docs do not force every fresh agent to execute or
re-analyze it by default.
