---
title: Allow local-yesterday nowcast only inside the post-close freshness window
date: 2026-06-07
last_updated: 2026-06-25
category: logic-errors
module: weather_bot.nowcast
problem_type: logic_error
component: service_object
symptoms:
  - "A just-ended Tokyo target date returned target-date-not-today after local midnight."
  - "Fresh final station high/low evidence became unavailable exactly when held exits needed it."
  - "A removed AWC history parameter hid missing observations after restart."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [nowcast, target-date, post-close, awc, hko, paper-trading]
---

# Allow local-yesterday nowcast only inside the post-close freshness window

## Problem

Station nowcast originally allowed only the station's local today. When Tokyo
crossed from June 6 to June 7, a June 6 market immediately became
`target-date-not-today`, even though final same-station observed high/low was
the most useful evidence for an already-held paper position.

## Symptoms

- The entry path became unavailable right after local midnight.
- Held-position exit and settlement-risk checks could miss fresh final
  observation evidence.
- The provider used the removed `hoursBeforeNow` parameter and later risked
  exceeding AWC's 400-row response maximum when requesting a full day.

## What Didn't Work

- Simply removing the `target-date-not-today` guard would be too loose. It
  could let stale old target dates into probability logic.
- Treating HKO like AWC history would also be wrong. HKO's max/min CSV is an
  official latest table, so the row must itself report the target date.

## Solution

Keep the same-station rule, but make the target-date gate more precise:

```python
if target_date == local_today:
    return ""
if target_date == local_today - timedelta(days=1):
    if seconds_after_local_midnight <= freshness_seconds:
        return ""
    return "target-date-post-close-window-expired"
return "target-date-not-today"
```

Then let the provider parser enforce the rest:

- AWC rows still must carry the exact requested station ID.
- HKO rows still must match the HK Observatory provider row.
- The observation timestamp must be on the target local date.
- Future observations fail closed as `future-observation`.
- Stale observations fail closed as `stale-observation`.

AWC uses the documented `hours=4` recovery window for the 47-station bulk
request. The persistent `metar_daily_extremes_state.json` file supplies the
target day's running high/low across local midnight and restart. If the AWC
response reaches its 400-row maximum, the provider fails closed instead of
using truncated history.

## Why This Works

`target_date` is the market's exam date. Local midnight does not make the exam
date irrelevant; it makes the final observation evidence more important for
positions that are still open.

The fix allows only the station's local yesterday and only while the normal
station freshness window is still open. That means the bot can use fresh final
observations after a market day closes, but it still refuses old dates, stale
rows, future rows, missing values, and wrong-station data.

This preserves the project boundary: the change improves held-position exit and
settlement-risk evidence. It is not a new-entry booster and does not touch live
trading, wallets, private keys, or real orders.

## Prevention

- Test the local-midnight boundary for Asian stations such as Tokyo/RJTT.
- Test HKO separately from AWC because the providers expose different data
  shapes.
- Test the documented AWC parameter name and the provider's 400-row boundary.
- Keep daily-high tied to observed high and daily-low tied to observed low.
- Keep `target-date-not-today` for future dates and dates older than local
  yesterday.

## Related Issues

- [Prefetch AWC METAR stations in bulk](../best-practices/prefetch-awc-metar-stations-in-bulk.md)
