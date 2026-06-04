---
title: Prefetch AWC METAR stations in bulk
date: 2026-06-04
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
queries add load to the public service. The full current METAR cache is useful,
but it only gives current reports. This bot needs the same-day high and low so
far, so it needs multiple reports since local midnight.

That means a simple "latest METAR only" cache file would break the high/low
evidence. The bot still needs a since-midnight response, but it should request
the enabled ICAO station set in one bulk call instead of making one call per
station.

## 3. How It Was Fixed

`AviationWeatherMetarNowcastProvider` now keeps an `awc_metar_bulk_cache`.
That cache is the shared AWC attendance sheet for METAR stations.

On the first METAR miss in a cache refresh, the provider asks AWC for all
enabled METAR station IDs in one JSON request. Each station then parses only
records matching its own ICAO code and target local date. The existing parser
still derives both observed high and observed low from that one response.

`station_nowcast_request_log.jsonl` still records only real HTTP attempts. For
AWC, a row now uses `request_mode=awc_metar_bulk_cache`,
`station_id=METAR_BULK`, and `requested_station_ids` so request counts do not
look like one request per station. HKO stays separate because its official
max/min CSV is already one whole-table request.

## 4. What To Check Next Time

- Test that evaluating multiple METAR stations uses one HTTP call within the
  cache refresh.
- Test that observed high and observed low still come from the same response.
- Keep `target-date-not-today`, stale data, malformed payloads, and unsupported
  stations fail-closed.
- Check `station_nowcast_request_log.jsonl` for real request attempts, not the
  number of station observations produced from a bulk response.
- Do not use the current-only AWC cache file if the code still needs
  since-midnight high/low evidence.

## 5. What This Project Must Be Especially Careful About

This is still paper-only weather evidence. Bulk fetching must not loosen the
settlement-station rule, invent nearby-station substitutions, or guess through
bad data.

If AWC returns missing, stale, malformed, future-date, unsupported, or invalid
data, the bot must skip or stay forecast-only according to the existing
fail-closed rules. The goal is fewer external calls, not weaker evidence.
