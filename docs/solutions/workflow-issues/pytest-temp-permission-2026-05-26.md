---
title: Use workspace temp dirs when pytest cannot scan Windows temp
date: 2026-05-26
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

When pytest setup fails with a Windows temp permission error, create a temp
folder inside the workspace and point `TMP` and `TEMP` at it for that test run.

```powershell
New-Item -ItemType Directory -Force -Path '.pytest-tmp' | Out-Null
$env:PYTHONPATH='src'
$env:TMP=(Resolve-Path '.pytest-tmp').Path
$env:TEMP=$env:TMP
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

Remove the workspace temp folder after verification if it was created only for
the agent run.

## Why This Matters

The test failure is environmental, not a product regression. Re-running with a
workspace temp directory preserves the value of the full suite without needing
administrator access or unsafe cleanup of the global temp tree.

## When to Apply

- Full-suite pytest fails during fixture setup with `PermissionError`.
- The stack trace points into `_pytest\tmpdir.py` or `_pytest\pathlib.py`.
- Focused tests pass, but tests using `tmp_path` cannot start.

## Examples

The corrected run for this update passed with:

```text
70 passed
```

## Related

- [Run git mutations serially](serial-git-mutations-2026-05-24.md)
