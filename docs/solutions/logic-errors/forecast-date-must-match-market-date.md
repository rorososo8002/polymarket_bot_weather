---
title: Forecast date must match the market date
date: 2026-06-02
category: logic-errors
module: weather_bot.probability
problem_type: logic_error
component: service_object
symptoms:
  - "A weather market could be evaluated with the nearest available forecast date when the target date was missing."
  - "A paper trade could use plausible but wrong-date weather data instead of skipping."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [forecast-quality, date-matching, paper-trading, fail-closed, open-meteo]
---

# Forecast date must match the market date

## Problem
The probability model accepted the nearest available Open-Meteo forecast date
when the exact target market date was missing. That meant a June 20 market could
be judged with a June 18 forecast if June 20 was absent from `daily.time`.

## Symptoms
- `_date_index` returned the nearest parsed date when `target.isoformat()` was
  not present.
- `estimate_weather_probability` could still return
  `source=open-meteo-ensemble-station` even though the forecast date did not
  match the market date.
- The paper-trading loop would treat that signal as strategy data instead of a
  missing-data skip.

## What Didn't Work
- Treating a near forecast date as a harmless fallback. For weather markets,
  one city-date event is the risk unit, so the wrong date is a different event,
  not a lower-quality estimate of the same event.
- Looking only for sufficient ensemble member values. Member count does not
  prove the values belong to the target date.

## Solution
Make date lookup exact-only:

```python
def _date_index(daily: dict[str, Any], target: date) -> int:
    times = daily.get("time") or []
    target_s = target.isoformat()
    for idx, value in enumerate(times):
        if str(value) == target_s:
            return idx
    raise ValueError(f"missing exact forecast date for target_date={target_s}")
```

The raised `ValueError` is caught by `estimate_weather_probability`, which
returns `source=forecast-unavailable`, `confidence=0.0`, and neutral
`p_true=0.5`. In this project, that is the strategy-safe SKIP path: the signal
is visible as a data problem but cannot open a paper trade.

## Why This Works
The bot is trying to verify whether a Polymarket weather strategy can make
money under realistic paper-trading inputs. If the forecast date is wrong, the
input no longer describes the market being evaluated. Exact date matching keeps
paper results from being contaminated by a different day's weather.

## Prevention
- Add regression tests where `daily.time` contains a nearby wrong date and
  assert `source == "forecast-unavailable"`.
- Assert the wrong date is not reported as the selected forecast date in the
  signal note.
- Review any future forecast fallback code for exact city, station, and date
  matching before allowing it to feed strategy evaluation.
- Treat missing, stale, malformed, unsupported, and wrong-date data as SKIP
  conditions, not as opportunities to guess.

## Related Issues
- [Do not use deterministic forecast fallback for strategy validation](../workflow-issues/do-not-use-deterministic-forecast-fallback-for-strategy-validation-2026-05-26.md)
- [Separate forecast freshness from WebSocket stream health](./explicit-forecast-and-websocket-health.md)
