---
title: Verify the Python command before trusting empty pytest output
date: 2026-06-01
category: workflow-issues
module: windows-python-test-runner
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - "A pytest command exits with code 0 but prints no normal collection or pass summary"
  - "Windows PATH may contain a workspace-adjacent executable named python"
tags: [windows, python, pytest, path, verification]
---

# Verify the Python command before trusting empty pytest output

## Context

A baseline focused-test command appeared to exit successfully but printed no
pytest summary. On Windows, `Get-Command python` showed that a workspace-adjacent
executable named `python` was shadowing the installed Python interpreter.

## Guidance

Treat an empty pytest result as unverified, even when the shell reports exit
code `0`. Check command resolution first:

```powershell
Get-Command python | Format-List Source,CommandType,Version
where.exe python
```

Then run pytest through a verified interpreter path:

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

## Why This Matters

An exit code is useful only when the intended program actually ran. Trusting an
empty result can create a false test-pass report and allow broken code to move
forward.

## When To Apply

- A Python or pytest command returns suspiciously fast.
- Pytest prints no pass count, failure count, or collection output.
- `python --version` does not print a normal interpreter version.

## Examples

Suspicious result:

```text
exit code: 0
output: <empty>
```

Verified result:

```text
29 passed in 0.59s
```
