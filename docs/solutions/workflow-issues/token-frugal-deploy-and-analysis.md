---
title: Keep deploy and analysis work token-frugal
date: 2026-06-15
category: workflow-issues
module: agent workflow, oracle-vps
problem_type: workflow_issue
component: development_workflow
severity: high
applies_when:
  - "Running routine VPS deploys"
  - "Analyzing or developing from an active task card"
  - "Choosing how much verification or document reading is enough"
tags: [token-budget, deployment, verification, handoff, workflow]
---

# Keep deploy and analysis work token-frugal

## Context

A routine server deploy consumed far too much context because the agent repeated
document reads, rebuilt and redeployed after doc-only handoff commits, and ran
more status checks than the claim required.

## Guidance

Use the smallest workflow that proves the user-visible result.

For routine VPS deploys:

1. Read only the mandatory active docs and the known-good command doc.
2. SSH preflight once.
3. Transfer the deploy payload once.
4. Run remote pytest once.
5. Restart the affected services once.
6. Verify service state, dashboard HTML, bare `/api/status` 403, query-token
   403, and header-authenticated `/api/status` 200.
7. Stop.

Do not redeploy only because `docs/active/current-task.md`,
`docs/active/new-chat-task-prompts.md`, or another handoff-only document
changed after the runtime deploy. Commit and push those docs, but leave the
server alone unless the docs are needed by runtime behavior or the user asks for
an exact server-source mirror.

For analysis and development:

- Prefer `rg` and targeted file reads over broad document sweeps.
- Trust a fresh active-task summary unless the task needs the source document.
- Do not repeat successful tests, SSH checks, or dashboard checks without a new
  reason.
- Explain less while working; report only decisions, blockers, and evidence.

## Why This Matters

Verification is required, but redundant verification is not rigor. It burns
context, slows the user down, and makes simple operations feel chaotic.

The right standard is: enough evidence to support the claim, no extra ceremony.

## When to Apply

- The user asks for a server deploy.
- The active task already says which plan items are complete.
- A check has already passed and no relevant state changed after it.
- The next contemplated action would only make docs and server source mirror
  each other, not change runtime behavior.

## Related

- `AGENTS.md`
- `docs/codex/known-good-commands.md`
- [Deploy payload copy must preserve app root access](./deploy-payload-copy-must-preserve-app-root-access.md)
