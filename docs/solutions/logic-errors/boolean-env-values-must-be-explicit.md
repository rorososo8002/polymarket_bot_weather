---
title: Boolean env values must be explicit
date: 2026-06-03
category: logic-errors
module: weather_bot.config
problem_type: logic_error
component: tooling
symptoms:
  - "Unknown boolean environment values such as treu, enabled, or maybe were parsed as False."
  - "A misspelled safety setting could disable a fail-closed trading guard without an operator-visible startup error."
root_cause: config_error
resolution_type: code_fix
severity: high
tags: [config, boolean-env, fail-closed, paper-trading, safety]
---

# Boolean env values must be explicit

## Problem
`_bool_env` treated every non-empty value outside the true list as `False`.
That meant a typo such as `REQUIRE_DATE_HINT_FOR_TRADE=treu` could silently
disable the date-hint safety guard.

## Symptoms
- `true`, `1`, `yes`, `y`, and `on` returned `True`.
- Any other spelling, including `treu`, `enabled`, or `maybe`, returned
  `False` instead of failing startup.
- A safety switch could look configured while the paper bot actually ran with
  the guard turned off.

## What Didn't Work
- Relying on Python truthiness or a one-sided true-value set. This is too loose
  for trading safety settings because every unknown value collapses to the same
  result as an intentional `false`.

## Solution
Split boolean parsing into three explicit outcomes:

```python
_TRUE_ENV_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_ENV_VALUES = {"0", "false", "no", "n", "off"}

if normalized in _TRUE_ENV_VALUES:
    return True
if normalized in _FALSE_ENV_VALUES:
    return False
raise ValueError(...)
```

Tests now cover the accepted true aliases, accepted false aliases, and rejected
unknown values for `REQUIRE_DATE_HINT_FOR_TRADE`.

## Why This Works
Boolean settings are switches, not free-form text. A parser needs to distinguish
three cases: intentionally on, intentionally off, and invalid. The invalid case
must stop startup so the operator fixes the setting before the paper strategy
continues.

## Prevention
- For every new boolean environment variable, parse through `_bool_env` instead
  of writing a custom truthiness check.
- Test both true aliases and false aliases when adding a boolean setting.
- Add at least one invalid-value test that expects `ValueError`; this proves
  typos cannot silently become `False`.
- Be especially careful with safety switches such as
  `REQUIRE_DATE_HINT_FOR_TRADE`. This project should skip uncertain markets
  instead of guessing or disabling guards through configuration mistakes.

## Related Issues
- `docs/solutions/security-issues/dashboard-public-host-token-fail-closed.md`
- `docs/solutions/logic-errors/forecast-date-must-match-market-date.md`
