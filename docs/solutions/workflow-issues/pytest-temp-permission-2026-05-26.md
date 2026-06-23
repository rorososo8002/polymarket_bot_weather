---
title: Use workspace temp dirs when pytest cannot scan Windows temp
date: 2026-05-26
last_updated: 2026-06-23
category: docs/solutions/workflow-issues
module: pytest verification
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - pytest tmp_path setup fails with PermissionError under Windows user temp
  - multiple local pytest commands would run concurrently in the same workspace
  - stale .pytest-tmp/current children cannot be removed because of WinError 5
tags: [pytest, windows, tempdir, verification, parallelism, permissions]
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

On 2026-06-20, a stale child under `.pytest-tmp/current` kept an unusable
Windows ACL. Even a serial pytest run failed before the affected test body ran
with `PermissionError: [WinError 5]`. The root `conftest.py` uses best-effort
cleanup, so it could not repair an already inaccessible child in place.

On 2026-06-23, the full suite reached test bodies but different runner tests
failed at `os.replace(tmp_path, paper_runner_status.json)`. Each test passed
alone, and the failing test changed between runs. This was a separate transient
Windows destination-file lock, not a pytest fixture or stale-directory failure.

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

If a serial run still reports `WinError 5` while removing a stale child, do not
fight that child one file at a time. Verify both paths remain inside the
workspace, move the whole `current` directory to a quarantine name, and rerun
pytest so `conftest.py` can create a clean `current` directory:

```powershell
$root = (Resolve-Path -LiteralPath '.').Path
$source = Join-Path $root '.pytest-tmp\current'
$target = Join-Path $root '.pytest-tmp\current-quarantine'
Move-Item -LiteralPath $source -Destination $target
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

Use a unique quarantine name if that target already exists. Quarantine first;
do not recursively delete an inaccessible directory as part of routine
verification.

If the traceback reaches application code and fails specifically while
atomically replacing a frequently read status file, do not quarantine the
pytest directory. Keep the unique temporary file and retry only
`PermissionError` with a short bounded delay, as the paper account writer
already does. Continue raising after the bounded attempts so real permission
or disk failures still fail closed.

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
- If a serial run reports `WinError 5` for a child already under
  `.pytest-tmp/current`, quarantine the whole `current` directory and retry.
- The stack trace points into `_pytest\tmpdir.py` or `_pytest\pathlib.py`.
- Focused tests pass, but tests using `tmp_path` cannot start.
- If the stack trace instead points into the application's `os.replace`, add
  or verify a bounded atomic-replace retry before treating it as pytest setup
  corruption.

## Examples

The corrected run for this update passed with:

```text
114 passed
```

The 2026-06-14 parallel-run collision was resolved by rerunning the affected
pytest file serially; the retry passed, followed by a full-suite pass.

The 2026-06-20 stale-ACL failure was resolved by moving `.pytest-tmp/current`
to `.pytest-tmp/current-step8-quarantine`; the same workflow test then passed
with `9 passed`.

The 2026-06-23 runner-status failure was reproduced with a regression test that
forced the first `os.replace` call to raise `PermissionError`. A bounded
0.01/0.05/0.1-second retry made that test pass, followed by the full local and
Oracle suites with `665 passed`.

## Related

- [Run git mutations serially](serial-git-mutations-2026-05-24.md)
- `docs/codex/known-good-commands.md`
