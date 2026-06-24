---
title: Residual profile lookup must use the latest past checkpoint
date: 2026-06-25
category: logic-errors
module: weather_bot.residual_probability
problem_type: logic_error
component: service_object
symptoms:
  - "A valid city and month returned SKIP_RESIDUAL_PROFILE_MISSING between stored profile checkpoints."
  - "Austin at local 17:00 was blocked even though a valid 16:30 profile existed."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [residual-profile, formation-time, paper-trading, future-leakage, fail-closed]
---

# Residual profile lookup must use the latest past checkpoint

## Problem

Runtime requested the profile for the current floored local minute by exact key.
Some station profiles do not contain every 30-minute checkpoint, so a valid
signal was blocked even when an earlier same-day checkpoint existed.

## Symptoms

- Austin local 17:00 returned `SKIP_RESIDUAL_PROFILE_MISSING`.
- The profile file contained 16:30 and 17:30 entries for the same station,
  month, direction, and unit.

## What Didn't Work

- Exact-key lookup treated a sparse time series as if every checkpoint existed.
- Choosing the numerically nearest checkpoint would be worse: at 17:00 it could
  select 17:30 and leak information that was not available yet.

## Solution

Search backward in 30-minute steps and use the first matching profile. Apply
the same `profile.local_minute <= requested_minute` rule when detecting a
wrong-unit profile.

The regression test supplies 16:30 and 17:30 profiles, requests 17:00, and
asserts that 16:30 is selected. This proves both fallback and no-future leakage.

## Why This Works

A residual profile answers “from this point onward, how much did the daily
extreme still move?” An older checkpoint uses only information already
available at decision time. A later checkpoint would make paper performance
unreplayable by peeking into the future.

## Prevention

- Test sparse profile grids with distinguishable past and future histograms.
- Keep request minutes on the validated 30-minute grid.
- Never replace this rule with absolute-nearest-time lookup.

## Related Issues

- `historical-weather-profiles-must-honor-qc-and-local-day-boundaries.md`
