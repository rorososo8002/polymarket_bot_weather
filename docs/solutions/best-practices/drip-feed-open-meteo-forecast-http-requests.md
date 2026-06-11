---
title: Drip-feed Open-Meteo forecast HTTP requests
date: 2026-06-06
category: best-practices
module: weather_bot.probability
problem_type: best_practice
component: background_job
severity: medium
applies_when:
  - "Changing Open-Meteo forecast cadence or cache settings"
  - "Investigating Too many concurrent requests rate limits"
  - "Handling forecast ReadTimeout rows"
  - "Explaining forecast request budgets"
tags: [open-meteo, rate-limit, forecast-cache, request-throttle, paper-trading]
---

# Drip-feed Open-Meteo forecast HTTP requests

## 1. What The Problem Was

The bot could call Open-Meteo in a burst when many markets needed stale
forecast data. A slow city could then time out repeatedly, and the next request
could arrive while Open-Meteo was still handling the previous server-side work.

The visible symptom was a 429 response such as:

```text
Open-Meteo rate limited: {"error":true,"reason":"Too many concurrent requests"}
```

This did not mean every supported city was fetched by one magic all-city call.
Each real forecast HTTP request is one station/city forecast key. A successful
response can include multiple days and ensemble model values for that station,
but it does not fetch all 40 trading-ready cities at once.

## 2. Why It Was A Problem

Open-Meteo is the external forecast telephone line. If the bot places several
calls too close together, or calls the same slow city again right after a
timeout, Open-Meteo can treat the traffic as overlapping work and reject it.

`forecast_cache.json` is the answer sheet on the desk. It stores the latest
successful forecast per key so the bot can reuse it without another external
call.

`forecast_request_log.jsonl` is the phone bill. It records real Open-Meteo HTTP
attempts. Use this file when counting calls. Do not count overwritten cache
entries as API usage.

Without a real request throttle, a cache miss could become "everyone line up at
the office window at once." The safer shape is one student at the window, then
a pause, then the next student.

## 3. How It Was Fixed

`OpenMeteoEnsembleClient` now has a global request throttle for real HTTP
calls:

```text
city request starts
request finishes or times out
wait at least FORECAST_REQUEST_MIN_INTERVAL_SECONDS
next city request starts
```

Production defaults:

```text
FORECAST_CACHE_TTL_SECONDS=10800
FORECAST_REQUEST_MIN_INTERVAL_SECONDS=15
STREAM_CYCLE_INTERVAL_SECONDS=2400
```

`FORECAST_CACHE_TTL_SECONDS=10800` means a successful forecast answer sheet is
fresh for 3 hours. With 40 trading-ready cities, this keeps the daily Open-Meteo
budget under 10,000 units while still allowing in-memory signal refreshes to
reuse the cached answer.

`FORECAST_REQUEST_MIN_INTERVAL_SECONDS=15` means the next real Open-Meteo HTTP
call cannot start until the previous real request has finished or timed out and
at least 15 seconds have passed.

`STREAM_CYCLE_INTERVAL_SECONDS=2400` is the market-discovery and WebSocket
rebuild interval. It is not permission to burst forecast HTTP calls.

Cache hits do not wait because they are not phone calls. They only read the
answer sheet already stored locally.

## 4. What To Check Next Time To Prevent The Same Mistake

- Check `forecast_request_log.jsonl` when counting real Open-Meteo attempts.
- Verify cache hits do not sleep or write request-log rows.
- Verify cache misses serialize across multiple `OpenMeteoEnsembleClient`
  instances.
- Verify the second real request starts only after the first request finishes
  or times out and the configured gap has passed.
- Keep `ReadTimeout` fail-fast behavior for the same forecast key, so one slow
  city does not immediately call again.
- Keep 429 classification: daily quota and concurrent-request responses do not
  use the same cooldown.

## 5. What This Project Must Be Especially Careful About

This bot is paper-only, but paper results still need honest evidence. Missing,
stale, timed-out, unsupported, or rate-limited forecast data must still skip
instead of guessing.

Do not fix request pressure by batching many cities into one forecast request
unless Open-Meteo's API contract and budget are explicitly reviewed. The
current production rule is simple and observable: one real forecast HTTP
request at a time, minimum 60 seconds after finish or timeout, cache hits are
free local reads.

## Related

- [Distinguish Open-Meteo concurrent 429 from daily quota cooldowns](../workflow-issues/distinguish-open-meteo-concurrent-429-from-daily-limit.md)
- [Do not count weather cache entries as external API usage](../workflow-issues/do-not-count-forecast-cache-as-open-meteo-usage.md)
- [Production decisions](../../production-decisions.md)
