---
title: Use workspace temp dirs when pytest cannot scan Windows temp
date: 2026-05-26
last_updated: 2026-06-01
category: docs/solutions/workflow-issues
module: pytest verification
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - pytest tmp_path setup fails with PermissionError under Windows user temp
tags: [pytest, windows, tempdir, verification]
---

# Use workspace temp dirs when pytest cannot scan Windows temp

## Context

During the production weather-bot update, the full pytest suite initially failed
before test bodies ran. The failure came from pytest trying to enumerate
`C:\Users\wpdla\AppData\Local\Temp\pytest-of-wpdla` while creating `tmp_path`
fixtures.

## Guidance

The repository root `conftest.py` now makes workspace temp storage the default.
Run pytest normally from the repository root:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

At startup, pytest automatically uses a stable workspace path:

```text
.pytest-tmp/current
```

The repository config disables pytest's cache provider, best-effort deletes older
`.pytest-tmp/pytest-*` folders, and removes `.pytest_cache` so local test runs
do not keep accumulating workspace trash. A caller can still pass `--basetemp`
explicitly when a special run needs a different location.

## Why This Matters

The test failure is environmental, not a product regression. Making the
workspace path automatic avoids wasting one failed run before applying the
known workaround. It also avoids administrator access and unsafe cleanup of the
global temp tree.

## When to Apply

- Run local Windows pytest from the repository root so `conftest.py` loads.
- If an older checkout lacks root `conftest.py`, apply the manual workspace-temp
  workaround before running the full suite.
- If full-suite pytest still fails during fixture setup with `PermissionError`,
  inspect whether an explicit `--basetemp` overrode the repository default.
- The stack trace points into `_pytest\tmpdir.py` or `_pytest\pathlib.py`.
- Focused tests pass, but tests using `tmp_path` cannot start.

## Examples

The corrected run for this update passed with:

```text
114 passed
```

## Related

- [Run git mutations serially](serial-git-mutations-2026-05-24.md)
- `docs/codex/known-good-commands.md`
