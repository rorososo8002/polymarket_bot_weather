---
title: Use workspace temp dirs when pytest cannot scan Windows temp
date: 2026-05-26
last_updated: 2026-06-14
category: docs/solutions/workflow-issues
module: pytest verification
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - pytest tmp_path setup fails with PermissionError under Windows user temp
  - multiple local pytest commands would run concurrently in the same workspace
tags: [pytest, windows, tempdir, verification, parallelism]
---

# Use workspace temp dirs when pytest cannot scan Windows temp

## Context

During the production weather-bot update, the full pytest suite initially failed
before test bodies ran. The failure came from pytest trying to enumerate
`C:\Users\wpdla\AppData\Local\Temp\pytest-of-wpdla` while creating `tmp_path`
fixtures.

On 2026-06-14, two focused pytest files were launched in parallel from the same
workspace. One process tried to create `.pytest-tmp/current` while another still
held or removed it, producing `FileExistsError`, `WinError 183`, and cleanup
warnings with `WinError 145`.

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

Run local pytest commands serially in this workspace. If separate pytest
processes truly need to run at the same time, give each process a distinct
`--basetemp` path; otherwise they race over `.pytest-tmp/current`.

## Why This Matters

The test failure is environmental, not a product regression. Making the
workspace path automatic avoids wasting one failed run before applying the
known workaround. It also avoids administrator access and unsafe cleanup of the
global temp tree. Running shared-basetemp pytest processes serially avoids a
false red build during verification.

## When to Apply

- Run local Windows pytest from the repository root so `conftest.py` loads.
- If an older checkout lacks root `conftest.py`, apply the manual workspace-temp
  workaround before running the full suite.
- If full-suite pytest still fails during fixture setup with `PermissionError`,
  inspect whether an explicit `--basetemp` overrode the repository default.
- If `.pytest-tmp/current` appears in `FileExistsError`, `WinError 183`, or
  `WinError 145`, check whether pytest commands were launched in parallel.
- The stack trace points into `_pytest\tmpdir.py` or `_pytest\pathlib.py`.
- Focused tests pass, but tests using `tmp_path` cannot start.

## Examples

The corrected run for this update passed with:

```text
114 passed
```

The 2026-06-14 parallel-run collision was resolved by rerunning the affected
pytest file serially; the retry passed, followed by a full-suite pass.

## Related

- [Run git mutations serially](serial-git-mutations-2026-05-24.md)
- `docs/codex/known-good-commands.md`
