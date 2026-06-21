---
title: Dashboard skip reasons must be human-readable
date: 2026-06-19
category: ui-bugs
module: weather_bot.dashboard
problem_type: ui_bug
component: service_object
symptoms:
  - "The dashboard showed raw diagnostic text such as residual profile missing."
  - "Operators could not quickly understand why a paper entry was skipped."
root_cause: inadequate_documentation
resolution_type: code_fix
severity: medium
tags: [dashboard, skip-reasons, official-stations, nowcast, paper-trading]
---

# Dashboard skip reasons must be human-readable

## Problem
The official-station dashboard rendered raw strategy diagnostics in recent skip
cards. A row like `SKIP_RESIDUAL_PROFILE_MISSING: station evidence unavailable`
was accurate for developers, but it did not answer the operator's real question:
"why did the bot refuse this trade?"

## Symptoms
- Recent skip cards mixed English internals with Korean dashboard labels.
- The station and observation facts were visible, but the decision explanation
  still required knowing the code path.
- Official-station entry-only mode looked like a generic failure instead of an
  intentional safety gate.

## What Didn't Work
- Showing only `reason_code` is too coarse. `SKIP` tells the operator the bot
  did nothing, not why doing nothing was correct.
- Showing the raw `reason` text is too low-level. It preserves evidence for
  debugging but pushes interpretation onto the operator.

## Solution
Add a separate API field for the operator-facing explanation and keep the raw
diagnostic alongside it for deeper investigation:

```python
"reason": str(row.get("reason") or ""),
"reason_ko": _skip_reason_ko(row),
```

The template should render `reason_ko` in the card body and continue showing
station facts such as station name, station id, and observed high or low.

For official-nowcast entry-only skips, explain the safety rule directly:

```text
예보만으로는 진입하지 않도록 막았습니다...
```

## Why This Works
The raw diagnostic is still preserved in `paper_skip_diagnostics.jsonl`, so no
debugging evidence is lost. The dashboard gains a stable presentation layer:
developer codes stay audit-friendly, while the operator sees the decision in
plain language.

## Prevention
- When adding a dashboard skip category, add a test for both the raw code and
  the human-readable explanation field.
- Keep provider and station facts next to the explanation so the operator can
  see which official station caused the decision.
- Do not translate by replacing the raw reason in logs; add a display field so
  replay and debugging stay intact.

## Related Issues
- [Surface nowcast evidence in open-position dashboard payloads](../logic-errors/dashboard-open-position-nowcast-payload.md)
- [Prefetch AWC METAR stations in bulk](../best-practices/prefetch-awc-metar-stations-in-bulk.md)
