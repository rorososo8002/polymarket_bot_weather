---
title: Reduce Codex context without deleting active workspace data
date: 2026-05-31
category: workflow-issues
module: codex workspace
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - "Reducing Codex token usage in an active development workspace"
  - "Large ignored folders such as runtime/, .git/, or .antigravitycli/ remain on disk"
tags: [codex, token-usage, workspace, codexignore, runtime-data, git]
---

# Reduce Codex context without deleting active workspace data

## Context

The active weather-bot workspace contains local operational data, Git metadata,
and a helper virtual environment. These files are useful for development or
operations but are not needed for most code changes. Treating token reduction
as a cleanup task can accidentally remove useful local state or Git history.

## Guidance

Keep using the active workspace. Reduce routine Codex context by controlling
what is read:

- Keep `AGENTS.md` short and always-on.
- Move situation-specific VPS, SSH, runtime-data, strategy, and extended
  engineering rules into `docs/codex/`.
- Add `.codexignore` patterns for `.git/`, `.antigravitycli/`, `runtime/`,
  caches, archives, logs, and generated runtime files.
- Read ignored operational files only when the task specifically needs them.
- Use bounded tails, counts, filters, and samples for large runtime data.

Do not delete `.git/`, `runtime/`, or `.antigravitycli/` merely to reduce token
usage. Delete or archive files only as a separate cleanup decision after
confirming their operational value.

## Why This Matters

Codex spends tokens on files it reads, not simply on files that exist on disk.
Excluding noisy folders keeps searches focused while preserving the working
bot, local Git history, and operational evidence.

## When To Apply

- A coding task does not need VPS logs or runtime CSV files.
- Local helper environments make repository searches noisy.
- `AGENTS.md` has grown because task-specific rules were added globally.
- A cleanup proposal risks mixing token reduction with runtime-data deletion.

## Examples

Before:

```text
Delete runtime/, .git/, and .antigravitycli/ to reduce Codex tokens.
```

After:

```text
Keep the active workspace intact. Exclude runtime/, .git/, and
.antigravitycli/ from normal analysis, then read them only for relevant tasks.
```

## Related

- [Dashboard large decision log initial scan](../performance-issues/dashboard-large-decision-log-initial-scan.md)
- [Verify remote dashboard state and entry counters](./verify-remote-dashboard-state-and-entry-counters.md)
