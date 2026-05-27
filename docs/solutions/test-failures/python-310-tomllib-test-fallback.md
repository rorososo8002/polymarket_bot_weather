---
title: Keep deployment tests compatible with Python 3.10 tomllib fallback
date: 2026-05-28
category: test-failures
module: deployment tests
problem_type: test_failure
component: testing_framework
symptoms:
  - "Oracle Ubuntu 22.04 pytest failed during collection with ModuleNotFoundError: No module named 'tomllib'"
  - "The project declared requires-python >=3.10 but the deployment test imported the Python 3.11-only tomllib module directly"
root_cause: config_error
resolution_type: test_fix
severity: medium
tags: [python-310, tomllib, tomli, pytest, oracle-deploy]
---

# Keep deployment tests compatible with Python 3.10 tomllib fallback

## Problem

Oracle Ubuntu 22.04 ships Python 3.10 by default. During the Oracle migration,
the app installed cleanly but `python -m pytest -q` failed before running tests
because `tests/test_deployment_files.py` imported `tomllib` directly.

## Symptoms

- `pytest` stopped during collection, before any test body ran.
- The stack trace ended with `ModuleNotFoundError: No module named 'tomllib'`.
- Local tests could pass on newer Python, while the production-like VPS test run
  failed on Python 3.10.

## What Didn't Work

- Re-running the deployment did not help because dependencies were already
  installed correctly.
- Installing `pytest` alone was insufficient. The failing import was in the
  test file, and `tomllib` is only standard library on Python 3.11+.

## Solution

Keep the test compatible with the declared Python floor:

```python
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
```

Also declare the fallback explicitly in dev dependencies so Python 3.10 test
environments do not rely on transitive installs:

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8.0.0",
  "tomli>=2.0.0; python_version < '3.11'"
]
```

## Why This Works

The production server is allowed by `requires-python = ">=3.10"`, so the test
suite must support Python 3.10. `tomli` provides the same TOML parsing API shape
needed by the test, while newer Python versions continue using the standard
library `tomllib`.

## Prevention

- When deployment targets use Python 3.10, run the full suite on Python 3.10
  before trusting the VPS deployment.
- Any test helper that imports a module added after the project's minimum
  Python version needs a fallback dependency or a raised minimum version.
- Keep dev-only compatibility dependencies in `[project.optional-dependencies]`
  rather than relying on transitive packages pulled in by the test runner.

## Related Issues

- [Use workspace temp dirs when pytest cannot scan Windows temp](../workflow-issues/pytest-temp-permission-2026-05-26.md)
- [Verify VPS code, env, and API health before trusting dashboards](../workflow-issues/verify-vps-code-env-and-api-health-2026-05-26.md)
