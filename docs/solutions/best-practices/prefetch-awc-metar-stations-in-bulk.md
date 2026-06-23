---
title: Prefetch AWC METAR stations in bulk
date: 2026-06-04
last_updated: 2026-06-24
category: best-practices
module: station_nowcast
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - "Adding or changing AWC METAR station nowcast code"
  - "Investigating station nowcast HTTP request volume"
  - "Adding trading-ready METAR stations"
  - "Testing observed high/low derivation from METAR data"
tags: [awc, metar, nowcast, prefetch, request-log, paper-trading]
---

# Prefetch AWC METAR stations in bulk

## 1. What The Problem Was

The paper bot can use AWC METAR observations for 39 trading-ready ICAO
stations. If the provider calls AWC once for `RKSI`, then once for `KLGA`, then
again for every other station, a refresh can turn into a burst of avoidable
HTTP requests.

Think of it like attendance. The old shape risks calling 39 students one by one.
The safer shape is to get one attendance sheet and let each city find its own
row.

## 2. Why It Was A Problem

AWC recommends cache files for large or frequent access because repeated custom
queries add load to the public service. In production checks on 2026-06-24, the
JSON endpoint returned one latest report per requested station even when the
query asked for a longer time window. Therefore, a query parameter that looks
like “since midnight” is not proof that the response contains the complete
day.

This bot needs the same-day high and low so far. Treating one latest report as
both values made the model compare the wrong temperature against the exact
integer bucket and produced false 90-100% probabilities.

## 3. How It Was Fixed

`AviationWeatherMetarNowcastProvider` now keeps an `awc_metar_bulk_cache`.
That cache is the shared AWC attendance sheet for METAR stations.

On the first METAR miss in a cache refresh, the provider asks AWC for all
enabled METAR station IDs in one JSON request. Each station then parses only
records matching its own ICAO code.

The response is treated as a current observation feed, not a historical daily
series. `metar_daily_extremes_state.json` persists each station's reports and
builds the running high and low across the station-local day. A new day becomes
eligible only when the provider observed the previous day and the next report
crossed local midnight without a gap longer than the accepted METAR cadence.
Missing restart baseline or a later continuity gap keeps the day blocked.

Each AWC row must carry its own station label. `icaoId` and `station_id` are
the observation row's name tag; if both are missing, the row is not evidence
for any requested station and must be skipped. Do not fill that gap with the
requested `station.station_id`, because that would be like writing a student's
name on an unsigned answer sheet after the exam.

`station_nowcast_request_log.jsonl` still records only real HTTP attempts. For
AWC, a row now uses `request_mode=awc_metar_bulk_cache`,
`station_id=METAR_BULK`, and `requested_station_ids` so request counts do not
look like one request per station. HKO stays separate because its official
max/min CSV is already one whole-table request.

## 4. What To Check Next Time

- Test that evaluating multiple METAR stations uses one HTTP call within the
  cache refresh.
- Test that a bulk row missing both `icaoId` and `station_id` is skipped rather
  than treated as the requested station.
- Test that a latest-only response is marked incomplete rather than treated as
  the whole day's high and low.
- Test that an uninterrupted station-local midnight handoff starts a complete
  day and survives a service restart.
- Test that a continuity gap invalidates the current day's accumulated
  extremes.
- Keep `target-date-not-today`, stale data, malformed payloads, and unsupported
  stations fail-closed.
- Check `station_nowcast_request_log.jsonl` for real request attempts, not the
  number of station observations produced from a bulk response.
- Do not infer historical completeness from AWC time-window query parameters.
- Preserve `metar_daily_extremes_state.json` across paper-account resets. It is
  observation evidence, not money or trade history.

## 5. What This Project Must Be Especially Careful About

This is still paper-only weather evidence. Bulk fetching must not loosen the
settlement-station rule, invent nearby-station substitutions, or guess through
bad data.

If AWC returns missing, stale, malformed, future-date, unsupported, or invalid
data, the bot must skip according to the existing fail-closed rules. The goal
is fewer external calls, not weaker evidence.
