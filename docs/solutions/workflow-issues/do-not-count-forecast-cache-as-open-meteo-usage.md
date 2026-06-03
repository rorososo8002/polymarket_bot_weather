---
title: Do not count forecast cache entries as Open-Meteo usage
date: 2026-06-04
category: workflow-issues
module: forecast_observability
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "Investigating Open-Meteo rate limits or daily usage"
  - "Comparing forecast cache size with reported API usage"
  - "Adding observability for external API calls"
tags: [open-meteo, forecast-cache, request-log, observability, paper-trading]
---

# Do not count forecast cache entries as Open-Meteo usage

## 1. What The Problem Was

The bot hit Open-Meteo's daily API limit, but `forecast_cache.json` showed only
a small number of cached entries. That made the API limit look suspicious, as if
the bot had barely called Open-Meteo.

## 2. Why It Was A Problem

`forecast_cache.json` is a result cache. Think of it as the last useful answer
for each station/model/forecast-days key. When the same key is refreshed, the
old entry is overwritten. It also does not record failed HTTP attempts.

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

The request log is also included in the VPS runtime logrotate rule so it rotates
over 10MB into `data/archive/` and compresses with zstd.

## 4. What To Check Next Time

- Use `forecast_request_log.jsonl` for real HTTP attempt counts.
- Use `forecast_cache.json` only to inspect latest successful forecast data and
  cache freshness.
- Separate successful calls from `429` rate-limit rows when diagnosing quota
  exhaustion.
- Remember that Open-Meteo may calculate official usage differently from raw
  HTTP attempt count for large requests.

## 5. What This Project Must Be Especially Careful About

Do not "fix" a rate-limit problem by loosening the fail-closed rule. Missing or
rate-limited forecasts must still produce `forecast-unavailable` and skip
entries. The request log exists to explain the failure clearly, not to justify
guessing around missing forecast data.
