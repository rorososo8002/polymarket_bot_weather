---
title: Keep dashboard market value separate from station-audit iteration
date: 2026-06-23
category: logic-errors
module: weather_bot.dashboard
problem_type: logic_error
component: service_object
symptoms:
  - "The dashboard shell loaded but every account metric stayed at zero and panels stayed on loading."
  - "Authenticated /api/status requests closed without a response."
  - "Position rendering raised TypeError when subtracting a float from a string."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [dashboard, variable-shadowing, station-audit, paper-position, regression-test]
---

# Keep dashboard market value separate from station-audit iteration

## Problem

The public dashboard loaded its static HTML, but its authenticated data API
crashed whenever an open position contained stored station-audit metadata.

## Symptoms

- The browser showed `$0`, `loading`, and `system error` despite a live account.
- The server logged `TypeError: unsupported operand type(s) for -: 'str' and 'float'`.

## What Didn't Work

Checking only the dashboard service and root URL was insufficient: both were
healthy because the exception occurred only while building authenticated data.

## Solution

Rename the station-audit loop value from `value` to `audit_value`. The old name
overwrote the numeric position market value in the enclosing function scope.

## Why This Works

Python `for` loop variables remain in the surrounding function scope. Keeping
the audit value under a distinct name preserves the numeric market value used
for unrealized PnL.

## Prevention

- Keep the regression fixture with string-valued `station_audit` metadata and
  assert that dashboard position PnL and audit fields are both produced.
- Verify authenticated `/api/status`, not only the static dashboard root.
