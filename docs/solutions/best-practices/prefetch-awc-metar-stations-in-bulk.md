---
title: Prefetch AWC METAR stations in bulk
date: 2026-06-04
last_updated: 2026-07-18
category: best-practices
module: station_nowcast
problem_type: best_practice
component: service_object
severity: high
applies_when:
  - "Adding or changing AWC METAR station nowcast code"
  - "Investigating station nowcast HTTP request volume"
  - "Adding trading-ready METAR stations"
  - "Testing observed high/low derivation from METAR data"
  - "Adding a faster direct provider in front of AWC recovery history"
tags: [awc, kma, metar, nowcast, prefetch, request-limit, paper-trading]
---

# Prefetch AWC METAR stations in bulk

## Context

The paper bot reads 47 official ICAO stations. Calling AWC once per city would
create a burst of avoidable requests, but asking for all stations and a full
day in one request is also unsafe.

AWC documents these constraints:

- no more than one request per minute per thread;
- most endpoints return at most 400 rows;
- the supported history parameter is `hours`;
- the old `hoursBeforeNow` parameter was removed in the 2025 API.

Production verification showed the difference clearly. A 24-hour request for
47 stations returned exactly 400 rows and was truncated. A four-hour request
returned 276 rows covering all 47 stations.

## Guidance

Use one shared AWC bulk cache and request all enabled station IDs with
`hours=4`. Each city parses only rows carrying its own `icaoId` or
`station_id`.

The four-hour response is only a restart bridge. It is not the daily-history
ledger. `metar_daily_extremes_state.json` persists the uninterrupted
station-local high and low, survives restarts, and keeps the latest two dates.

Fail closed when the response contains 400 rows:

```python
if isinstance(payload, list) and len(payload) >= 400:
    return unavailable("metar-response-row-limit")
```

Do not silently attach an unsigned row to the requested station. A row missing
both station identifiers is not evidence for any city.

### A faster direct feed must not bypass continuity

A low-latency provider such as the KMA domestic METAR API may be preferred for
the newest Korean report, but it usually does not replace AWC's recovery
history. When the persistent ledger is incomplete or any consecutive gap from
the saved observation through the returned direct rows exceeds the continuity
limit, apply the AWC recovery rows first and the direct report second.

Never combine fields from different timestamps. If a direct payload's newest
report is older than `metar_daily_extremes_state.json`'s saved latest report,
reject that payload and fall back. Otherwise an older raw temperature can be
displayed beside a newer ledger timestamp and create false trading evidence.

Backoff scope must match failure scope. Transport or service failures may open
a shared short circuit breaker, but a malformed or wrong-station response for
one ICAO station must suppress only that station. A bad RKSI row must not stop
a healthy RKPK request.

## Why This Matters

Using the removed parameter produced only a short default response. Replacing
it with a full-day `hours` query exposed the 400-row cap. Either mistake can
erase the real daily high or low and manufacture a false 90-100% trading
probability.

The bounded request plus persistent accumulator satisfies both sides:

- one real HTTP request per refresh;
- every station remains represented;
- daily extremes come from continuous saved observations;
- incomplete, truncated, stale, future, or wrong-station data remains blocked.

## When to Apply

- When changing AWC request parameters or adding stations.
- When investigating missing station history after a restart.
- When a bulk response has suspiciously equal row counts across repeated runs.
- When changing retention or continuity rules for daily extremes.

## Examples

Required regression checks:

- the request contains `hours=4` and never `hoursBeforeNow`;
- evaluating multiple stations uses one real request inside the cache window;
- a 400-row response returns `metar-response-row-limit`;
- latest-only data cannot become a complete daily extreme;
- a continuous local-midnight handoff survives restart;
- a continuity gap invalidates the affected day.
- an internal gap between two direct-provider rows triggers AWC recovery;
- an older direct report cannot inherit a newer saved timestamp;
- one station's malformed direct response does not suppress another station.

## Related

- [Allow local-yesterday nowcast only inside the post-close freshness window](../logic-errors/local-yesterday-nowcast-post-close-window.md)
