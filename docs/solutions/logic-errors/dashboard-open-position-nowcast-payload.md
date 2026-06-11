---
title: Surface nowcast evidence in open-position dashboard payloads
date: 2026-06-12
category: logic-errors
module: weather_bot.dashboard
problem_type: logic_error
component: service_object
symptoms:
  - "Open-position cards showed station -- even though the station nowcast provider had fresh observed evidence."
  - "Runtime decision notes contained observed_high_c, but /api/status positions did not expose nowcast_high_c."
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [dashboard, nowcast, paper-trading, open-positions, api-status]
---

# Surface nowcast evidence in open-position dashboard payloads

## Problem
The dashboard open-position card showed `station --` for a held Singapore
temperature position even though the bot had already fetched AWC METAR nowcast
data and recorded `observed_high_c=29.0` in the matching decision note.

## Symptoms
- The operator could see forecast and probability badges, but no station badge.
- `station_nowcast_request_log.jsonl` showed successful bulk METAR requests.
- The latest decision row for the market contained `observed_high_c=29.0`, but
  the position object returned by `/api/status` had no `nowcast_high_c` key.

## What Didn't Work
- Treating the missing badge as a provider or cadence problem would chase the
  wrong layer. The nowcast request had succeeded; the data was lost only while
  packaging the dashboard response.
- Restarting the dashboard alone would not help because the old code still
  omitted the field every time it built the payload.

## Solution
Keep the dashboard template and API payload contract aligned. If
`dashboard_template.py` renders `p.nowcast_high_c` or `p.nowcast_low_c`, then
`dashboard.py::_position_payload()` must parse those values from the latest
decision note and include them in each open-position object:

```python
latest_note = latest_decision.get("note", "")
"nowcast_high_c": _nowcast_c_from_note(latest_note, "observed_high_c"),
"nowcast_low_c": _nowcast_c_from_note(latest_note, "observed_low_c"),
```

Add a dashboard payload test with a decision note containing both forecast
evidence and same-station nowcast evidence:

```python
assert payload["positions"][0]["forecast_c"] == pytest.approx(28.7)
assert payload["positions"][0]["nowcast_high_c"] == pytest.approx(29.0)
assert payload["positions"][0]["nowcast_low_c"] is None
```

## Why This Works
The decision row is the durable bridge between strategy evidence and dashboard
display. The station provider writes the nowcast evidence into the decision
note, and the dashboard already uses that same note for forecast temperature.
Parsing the observed value in the same payload-building step keeps the UI from
inventing a separate source of truth.

This also makes the operator view honest: `station --` now means the latest
decision did not contain a usable observed high/low value, not that the station
provider never ran.

## Prevention
- When adding a dashboard badge, add a payload test that asserts the exact API
  key the template reads.
- Debug missing dashboard fields by checking each layer in order:
  runtime evidence, decision/trade ledger, payload builder, then template.
- For held-position nowcast exits, remember exact Celsius buckets are rounded
  intervals. For a 29C exact-high market, 29.1C is still inside the 29C bucket;
  only above the exact bucket boundary, about 29.5C, should make held YES
  probability collapse to zero and trigger the normal probability stop path.

## Related Issues
- [Realtime nowcast signals must refresh on the nowcast TTL](./realtime-nowcast-signal-refresh-must-follow-nowcast-ttl.md)
- [Do not use observed high nowcast for daily-low markets](./observed-high-nowcast-daily-low-markets.md)
- [Keep live dashboard refreshes small and operator-focused](../performance-issues/dashboard-live-refresh-payload-cost.md)
