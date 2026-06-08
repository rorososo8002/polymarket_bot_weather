---
title: Realtime forecast scheduler must use real forecast success time
date: 2026-06-08
category: logic-errors
module: weather_bot.live_paper_runner
problem_type: logic_error
component: background_job
symptoms:
  - "A cache-hit signal could be treated as if a new real Open-Meteo forecast had just succeeded."
  - "Forecast refresh TTLs could drift from the actual forecast answer timestamp."
  - "Runner status could show a later next eligible request time than the evidence justified."
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [realtime, forecast-scheduler, open-meteo, forecast-cache, ttl, paper-trading]
---

# Realtime forecast scheduler must use real forecast success time

## Problem

The realtime runner now starts the CLOB WebSocket before all forecast signals
are ready, then fills a signal registry from a background forecast worker. That
shape creates a subtle timing trap: attaching a `WeatherSignal` to the registry
is not the same event as a real Open-Meteo forecast success.

If the worker refreshes a market from `forecast_cache.json`, the bot has a
usable signal, but it has not made a new external forecast call. The scheduler
must therefore keep TTLs tied to the cached forecast's original success time,
not the registry attachment time.

## Symptoms

- A cache-hit signal could reset a 40m/30m/20m scheduler clock even though no
  real Open-Meteo request occurred.
- A held-position or opportunity market could wait too long for its next
  eligible refresh because the worker used the current time as success time.
- `forecast_worker.next_eligible_request_time` could look safer than the real
  forecast evidence was.

## What Didn't Work

- Marking scheduler success with `datetime.now()` after a worker task finishes
  is too broad. It is correct only when the task actually produced a fresh real
  forecast success.
- Treating every non-error `WeatherSignal` as equal hides the difference between
  a fresh external answer, a disk/memory cache hit, and a fail-closed
  `forecast-unavailable` signal.

## Solution

Keep the startup order split:

```text
temperature token set known
-> start WebSocket stream
-> forecast worker fills signals_by_market as keys become ready
```

But when the worker marks a forecast key successful, use the ensemble client's
`last_success_at` from `health_snapshot()` when available:

```python
result = self._process_task(task)
if result.has_supported_signal:
    self.scheduler.mark_success(task, result.success_at)
else:
    self.scheduler.mark_failure(task, "forecast signal unavailable")
```

`result.success_at` is derived from the Open-Meteo ensemble client health, with
the worker timestamp used only as a fallback for tests or injected estimators.
That keeps cache-hit signals useful without pretending they are new real HTTP
successes.

## Why This Works

`signals_by_market` is the realtime trading permission registry. It answers:
"Does this market currently have a supported signal?"

The forecast scheduler answers a different question: "When may this forecast
key use the next single real Open-Meteo request slot?"

Those clocks must stay separate. A registry update can happen because of a
cache hit, but the next eligible real request must still be based on the last
successful forecast evidence timestamp. This preserves the production rule:
general refresh 40m, held-position refresh 30m, and priority refresh 20m from
last real success, while Open-Meteo calls remain one-at-a-time.

## Prevention

- When a worker stores a cached signal, do not automatically reset the forecast
  scheduler TTL to now.
- Check `forecast_request_log.jsonl` or `OpenMeteoEnsembleClient.health_snapshot()`
  when reasoning about real forecast attempts and success times.
- Test the two clocks separately: signal registry attachment can happen
  immediately from cache, but the next real request slot must still respect the
  original forecast success timestamp.
- Keep `forecast-unavailable` as a fail-closed signal and mark the scheduler
  failure/cooldown path instead of treating it as a successful refresh.

## Related Issues

- [Drip-feed Open-Meteo forecast HTTP requests](../best-practices/drip-feed-open-meteo-forecast-http-requests.md)
- [Realtime nowcast signals must refresh on the nowcast TTL](./realtime-nowcast-signal-refresh-must-follow-nowcast-ttl.md)
- [Explicit forecast and WebSocket health](./explicit-forecast-and-websocket-health.md)
