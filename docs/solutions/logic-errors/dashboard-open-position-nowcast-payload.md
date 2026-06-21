---
title: Surface nowcast evidence in open-position dashboard payloads
date: 2026-06-12
last_updated: 2026-06-19
category: logic-errors
module: weather_bot.dashboard
problem_type: logic_error
component: service_object
symptoms:
  - "Open-position cards showed station -- even though the station nowcast provider had fresh observed evidence."
  - "Runtime decision notes contained observed_high_c, but /api/status positions did not expose nowcast_high_c."
  - "Open positions looked blank when nowcast was intentionally unavailable because the target date was not station-local today yet."
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
- The legacy operator view emphasized generic model and probability badges but did
  not make official-station evidence the primary display.
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
decision note, including later `HOLD` rows rather than only the original entry
row, and include them in each open-position object:

```python
latest_note = latest_decision.get("note", "")
"nowcast_high_c": _nowcast_c_from_note(latest_note, "observed_high_c"),
"nowcast_low_c": _nowcast_c_from_note(latest_note, "observed_low_c"),
"station_lock_strength": station_evidence["lock_strength"],
"station_settlement_boundary_c": station_evidence["settlement_boundary_c"],
```

Add a dashboard payload test with an entry row followed by a newer station
decision:

```python
assert "forecast_c" not in payload["positions"][0]
assert "p_true" not in payload["positions"][0]
assert payload["positions"][0]["nowcast_high_c"] == pytest.approx(24.0)
assert payload["positions"][0]["station_lock_strength"] == "strong_no"
```

## Why This Works
The decision row is the durable bridge between strategy evidence and dashboard
display. The station provider writes observation and lock evidence into the
decision note. Parsing the newest market decision keeps the UI from inventing
a separate source of truth or freezing the station view at entry time.

This also makes the operator view honest: `station --` now means the latest
decision did not contain a usable observed high/low value, not that the station
provider never ran.

When the latest decision explicitly says nowcast was unavailable, surface that
reason too. For example, `nowcast_unavailable=target-date-not-today` means the
bot intentionally refused to use station observations because the target date
had not started in the settlement station's local timezone. The dashboard should
show that as "target date not yet active" rather than leaving the operator to
infer that the station provider is broken.

For open positions, the dashboard can also recover stable station identity from
`TRADING_READY_STATION_MAP` when older runtime metadata lacks `station_id` or
`station_name`. The station registry is already the execution universe, so it is
the right fallback for display-only station names.

## Prevention
- When adding a dashboard badge, add a payload test that asserts the exact API
  key the template reads.
- When the active strategy changes evidence source, remove obsolete visible
  panels and public payload fields instead of merely hiding their labels.
- Debug missing dashboard fields by checking each layer in order:
  runtime evidence, decision/trade ledger, payload builder, then template.
- Show unavailable-nowcast reasons explicitly. A blank badge makes an intended
  fail-closed decision look like missing functionality.
- Whole-degree Celsius source display uses `[N.0C, N+1.0C)`. For a 29C
  exact-high market, `29.1C` is still inside the displayed 29C bucket and
  `30.0C` is the first decisive break above it.

## Related Issues
- [Keep live dashboard refreshes small and operator-focused](../performance-issues/dashboard-live-refresh-payload-cost.md)
