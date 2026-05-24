---
title: Run git mutations serially
date: 2026-05-24
category: docs/solutions/workflow-issues
module: repository workflow
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - committing, pushing, or changing repository metadata from an agent session
tags: [git, locking, workflow]
---

# Run git mutations serially

## Context

During the initial publish flow for this repository, two Git metadata mutations
were launched in parallel: renaming the branch and adding the remote. The remote
add succeeded, but the branch command briefly hit a `HEAD.lock` conflict.

## Guidance

Keep Git operations that write `.git/` metadata strictly serial. Reads like
`git status`, `git diff`, and `git ls-remote` can run beside unrelated checks,
but commands such as `git init`, `git branch -M`, `git remote add`, `git add`,
`git commit`, and `git push` should run one at a time.

## Why This Matters

Git uses lock files to protect repository metadata. Parallel write commands can
fail even when each command is valid, leaving the agent to distinguish a real
repository problem from a self-created lock race.

## When to Apply

- Starting or publishing a repository from a previously untracked folder.
- Changing branch names, remotes, index state, or commits.
- Running from environments where sandbox and desktop users both touch the same
  working tree.

## Examples

Do this:

```powershell
git init
git branch -M main
git remote add origin https://github.com/owner/repo.git
git status -sb
```

Avoid dispatching those write commands as parallel tool calls.

## Related

- [VPS live paper runbook](../../VPS_LIVE_PAPER.md)
