---
title: Distinguish Open-Meteo concurrent 429 from daily quota cooldowns
date: 2026-06-06
category: workflow-issues
module: forecast_observability
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "Investigating Open-Meteo 429 responses"
  - "Explaining dashboard forecast failure messages"
  - "Changing forecast retry or cooldown policy"
  - "Reviewing forecast_request_log.jsonl after a VPS rate-limit incident"
  - "Handling repeated Open-Meteo ReadTimeout rows for one city/station"
tags: [open-meteo, rate-limit, concurrent-requests, forecast-request-log, cooldown, read-timeout]
---

# Distinguish Open-Meteo concurrent 429 from daily quota cooldowns

## 1. What The Problem Was

The VPS dashboard showed:

```text
Open-Meteo rate limited: {"error":true,"reason":"Too many concurrent requests"} until 2026-06-07T00:15:00+00:00
```

The first half came from Open-Meteo. The second half, the `until` time, came
from our client. Before the fix, the client treated any HTTP 429 as a
daily-limit style cooldown and wrote `forecast_rate_limit_state.json` until the
next UTC day at 00:15.

That is easy to misread. A `Too many concurrent requests` response does not
prove the daily quota was exhausted. It means Open-Meteo thought requests were
too bunched up at that moment.

## 2. Why It Was A Problem

`forecast_request_log.jsonl` is the phone bill: it records real Open-Meteo HTTP
attempts. `forecast_rate_limit_state.json` is the "do not call again yet" note.

On 2026-06-06 UTC, the active VPS log showed only 100 forecast HTTP attempts
since the service restart, with one 429. The last 429 happened after repeated
Chicago/KORD requests hit read timeouts. In the five minutes before the 429,
all seven rows were for one cache key and one city:

- six `ReadTimeout` rows for Chicago/KORD
- one HTTP 429 row with `Too many concurrent requests`

So the issue was not "hundreds of cities burned the whole day budget." The
immediate pattern was a retry burst against one stale forecast key, followed by
a 429 that the old client stored as a next-day cooldown.

The five successful rows immediately before the Chicago failure were five
separate Open-Meteo HTTP calls, not one all-city call:

- Miami/KMIA
- Beijing/ZBAA
- Wellington/NZWN
- Atlanta/KATL
- Los Angeles/KLAX

One forecast call fetches one station/city forecast key. It can contain several
days and ensemble model values for that station, and a successful response can
be reused from `forecast_cache.json`. It does not fetch every supported city in
one request.

## 3. How It Was Diagnosed

Use the request log, not the cache:

```text
/opt/polymarket-weather-bot/data/forecast_request_log.jsonl
```

Check these facts together:

- total HTTP attempt rows
- 429 count and response body
- rows in the 1, 5, 10, 30, and 60 minutes before the last 429
- unique `cache_key` and city count in that window
- rows after the last 429
- `forecast_rate_limit_state.json` `blocked_until`

For this incident, there were zero request-log rows after the 429. That means
the persisted cooldown worked as a stop sign. The questionable part is that the
stop sign lasted until the next UTC reset even though the response body said
concurrency, not daily quota.

## 4. How It Was Fixed

`OpenMeteoEnsembleClient` now classifies 429 response bodies before writing the
cooldown memo:

- `Daily API request limit exceeded` and unknown 429 responses keep the
  conservative daily cooldown until the next UTC 00:15 reset.
- `Too many concurrent requests` writes `kind=concurrent` and a 15-minute
  `blocked_until`.
- Concurrent cooldowns do not set the cycle-wide `disabled_reason`, so the
  client can recover after the short cooldown expires.
- Legacy cooldown files that were written before `kind` existed are
  reclassified from their `reason` text. If the old reason says
  `Too many concurrent requests`, the client caps the old next-day
  `blocked_until` at 15 minutes after `last_rate_limited_at`.
- `forecast_request_log.jsonl` rows and forecast health snapshots include
  `rate_limit_kind`, so dashboard/API investigations can see which kind of 429
  happened.

The client also handles repeated `ReadTimeout` rows before they become a 429:

- `ReadTimeout` is not immediately retried by tenacity.
- The timed-out forecast key receives a 30-minute in-process temporary failure
  memo.
- A later call for the same city/station/date/model key fails fast without a
  new HTTP request while the memo is active.
- Other forecast keys, such as the next city, continue normally.
- The one real timed-out HTTP attempt is logged with
  `temporary_failure_kind=read_timeout` and
  `temporary_failure_blocked_until`.
- Real Open-Meteo forecast HTTP calls are globally drip-fed: one request must
  finish or timeout, then at least `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=15`
  passes before the next real request starts. Cache hits do not wait because
  they are not external calls.

Think of it like this. Daily quota is "the school office is closed until
tomorrow." Concurrent request limiting is "too many students are at the window;
come back after the hallway clears." A `ReadTimeout` is "this one window did
not answer in time; do not keep knocking on the same window right away." They
should not use the same waiting time.

## 5. What To Check Next Time To Prevent The Same Mistake

- Do not explain every 429 as daily quota exhaustion. Read the response body.
- If the body says `Too many concurrent requests`, inspect whether one
  city/cache key is being retried repeatedly after timeouts.
- Check whether multiple markets in the same city-date event are re-calling the
  probability path after a failed forecast request.
- For `ReadTimeout`, verify one same-key timeout creates only one
  `forecast_request_log.jsonl` row and that later same-key calls do not add
  more HTTP rows until the temporary memo expires.
- Verify that a different city/cache key can still fetch while one key is under
  temporary timeout cooldown.
- Verify that `rows_after_last_429` stays at zero when a cooldown is active.
- Check `rate_limit_kind` in the request log and forecast health snapshot.

## 6. What This Project Must Be Especially Careful About

Do not loosen fail-closed trading behavior. Missing, timed-out, or rate-limited
forecasts must still produce `forecast-unavailable` and skip entries.

The implemented fix classifies 429 reasons:

- daily quota responses can keep the next-day `blocked_until` behavior
- concurrent-request responses use a short backoff
- read timeouts put only the timed-out forecast key under a short temporary
  memo instead of retrying immediately
- request logs keep enough evidence to decide if a later broader persisted
  cooldown is needed

The goal is not to trade through missing data. The goal is to stop one stale
forecast key from making the dashboard look like the entire Open-Meteo daily
budget was exhausted.

## Related

- `docs/solutions/workflow-issues/do-not-count-forecast-cache-as-open-meteo-usage.md`
- `docs/solutions/best-practices/drip-feed-open-meteo-forecast-http-requests.md`
- `src/weather_bot/probability.py`
- `src/weather_bot/live_paper_runner.py`
