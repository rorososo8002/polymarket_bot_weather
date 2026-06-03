---
title: Do not count weather cache entries as external API usage
date: 2026-06-04
category: workflow-issues
module: forecast_observability
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "Investigating Open-Meteo rate limits or daily usage"
  - "Investigating AWC METAR or HKO station nowcast usage"
  - "Comparing forecast cache size with reported API usage"
  - "Adding observability for external API calls"
tags: [open-meteo, forecast-cache, request-log, observability, paper-trading, station-nowcast, metar, hko]
---

# Do not count weather cache entries as external API usage

## 1. What The Problem Was

The bot hit Open-Meteo's daily API limit, but `forecast_cache.json` showed only
a small number of cached entries. That made the API limit look suspicious, as if
the bot had barely called Open-Meteo.

The same observability gap can happen with station nowcast data. A 15-minute
station nowcast cache can reduce AWC METAR and HKO requests, but the cache
itself is not a request-count ledger.

## 2. Why It Was A Problem

`forecast_cache.json` is a result cache. Think of it as the last useful answer
for each station/model/forecast-days key. When the same key is refreshed, the
old entry is overwritten. It also does not record failed HTTP attempts.

The in-memory station nowcast cache has the same shape. It stores the latest
`StationNowcastObservation` for one station-date so daily high and daily low can
reuse one official observation response. That is useful, but it only tells you
what data is available now, not how many times the bot went outside to ask AWC
or HKO for fresh data.

That means it is not a call-count ledger. Counting cache entries is like
counting the number of answer sheets left on the desk, not the number of times
the teacher was asked a question.

## 3. How It Was Fixed

The Open-Meteo ensemble client now writes a separate
`forecast_request_log.jsonl` row only when it makes a real network request.
Cached reads do not add rows.

Each row records safe operational facts:

- when the request was attempted
- the forecast cache key
- why the cache missed, such as missing disk cache or disabled cache
- safe city/station labels when the probability path has them
- station coordinates rounded to four decimals
- timezone and forecast-days parameters
- status such as `success`, `http_error`, `json_error`, or `error`
- HTTP status code, including `429` rate-limit responses

The station nowcast provider follows the same rule for official observation
sources. It writes one `station_nowcast_request_log.jsonl` row only when it
makes a real AWC METAR or HKO max/min HTTP request. Cache hits do not add rows.

Each station nowcast request row records:

- request time
- city and settlement-station code from `STATION_MAP`
- source such as `aviationweather-metar` or `hko-maxmin-since-midnight`
- target date and timezone
- cache-miss reason such as `empty-cache`, `expired-cache`, or `cache-disabled`
- status, HTTP status code, and parse/unavailable reason when available

Both request logs are included in the VPS runtime logrotate rule so they rotate
over 10MB into `data/archive/` and compress with zstd.

## 4. What To Check Next Time

- Use `forecast_request_log.jsonl` for real HTTP attempt counts.
- Use `station_nowcast_request_log.jsonl` for real METAR/HKO observation
  attempt counts.
- Use `forecast_cache.json` only to inspect latest successful forecast data and
  cache freshness.
- Use station nowcast cache behavior only to explain reuse; do not count cache
  hits as external API requests.
- Separate successful calls from `429` rate-limit rows when diagnosing quota
  exhaustion.
- Remember that Open-Meteo may calculate official usage differently from raw
  HTTP attempt count for large requests.

## 5. What This Project Must Be Especially Careful About

Do not "fix" a rate-limit problem by loosening the fail-closed rule. Missing or
rate-limited forecasts must still produce `forecast-unavailable` and skip
entries. The request log exists to explain the failure clearly, not to justify
guessing around missing forecast data.

For station nowcast, keep the settlement-station boundary just as strict. AWC
METAR and HKO rows help the paper bot use same-station observed high/low data,
but missing, stale, malformed, future-date, unmapped, or unsupported
observations must remain forecast-only or fail-closed. The request log explains
official observation usage; it must not become a reason to substitute nearby
stations.
