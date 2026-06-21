---
title: Reduce Codex Context Without Deleting Active Workspace Data
date: 2026-06-07
last_updated: 2026-06-22
category: workflow-issues
module: documentation
problem_type: workflow_issue
component: documentation
severity: medium
applies_when:
  - Fresh-task documents are growing beyond their compact contracts
  - Runtime evidence or build caches make repository scans expensive
  - Agents create a new folder or handoff file for every attempt
tags: [context-management, handoff-docs, workflow-tests, documentation]
---

# Reduce Codex Context Without Deleting Active Workspace Data

## 1. What Went Wrong

Large runtime files, duplicate handoff documents, unpacked tool copies, and
temporary test folders made the workspace expensive to inspect. The opposite
mistake was to treat every generated file as permanent evidence and keep it in
the project forever.

## 2. Why It Mattered

Some files are evidence and some are packaging material. `paper_state.json`,
trade ledgers, and raw snapshots explain what the paper bot actually did.
`.pytest-tmp`, `__pycache__`, and an unpacked external tool package do not.
Keeping both categories indiscriminately wastes disk space and tempts agents to
bulk-read irrelevant text.

## 3. Durable Fix

The default document model has three jobs:

- `docs/active/current-task.md` is the only active-work card.
- `docs/production-decisions.md` is the permanent strategy and safety rule book.
- `docs/strategy-validation-roadmap.md` is read only for strategy-validation
  work.

Completed implementation plans, empty progress boards, and one-shot prompt
placeholders are deleted after verified completion. A temporary step-order file
may exist under `docs/active/` only while the task genuinely needs it.

Generated data follows a lifecycle:

- `.pytest-tmp` is recreated for the current test run and removed when pytest
  exits.
- `__pycache__` is disposable and must not be treated as source.
- Unpacked external tools belong outside the repository; a copied `package/`
  tree is removed after use.
- Station residual profiles used by runtime live under `strategy_data/`.
  Rebuild-only source evidence is kept as one ignored
  `.station_residual_cache.zip`, not hundreds of loose workspace files.

The reading rule is equally important: inspect large evidence with file sizes,
counts, headers, tails, filters, and summaries. Do not bulk-read it merely
because it exists.

## 4. What To Check Next Time

- Before adding a rule to `AGENTS.md`, ask whether every future task needs it.
- Keep `docs/active/current-task.md` focused on unfinished work and reset it to
  `Status: none` after completion.
- Do not create a new folder for every attempt. Edit the canonical file or use
  one temporary plan, then delete it.
- Run `tests/test_workflow_defaults.py` after changing workflow documents.
- Verify `.pytest-tmp` disappears after the test process exits.
- Keep runtime evidence and reproducibility archives ignored by Git.
- Never delete `.git/`, paper account ledgers, or raw trading evidence merely
  to save tokens.

## 5. Project-Specific Caution

`paper_state.json` is the paper account book, not a cache. Paper decisions,
trades, raw snapshots, and runner status are audit evidence. Read them in
bounded slices and delete them only under an explicit retention policy.

The residual cache archive is different: normal runtime does not read it.
Developers use it only to regenerate or verify
`strategy_data/station_residual_profiles.json` and its manifest.
