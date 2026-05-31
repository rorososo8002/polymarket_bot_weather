---
title: Keep live dashboard refreshes small and operator-focused
date: 2026-05-29
category: performance-issues
module: weather_bot.dashboard
problem_type: performance_issue
component: dashboard
severity: medium
symptoms:
  - "Dashboard CPU is higher than the paper bot even though the page is read-only"
  - "The UI repeatedly shows high-volume SKIP or candidate rows that the operator does not need"
  - "The dashboard API is requested every few seconds by an open browser tab"
root_cause: "The live dashboard treated every refresh as a fresh full operational feed, including skip/candidate/event rows and uncached cumulative trade totals."
resolution_type: optimization
tags: [dashboard, performance, polling, payload, skip-logs, vps]
---

# Keep live dashboard refreshes small and operator-focused

## Problem

The dashboard was read-only, but it still consumed meaningful CPU because the
browser polled `/api/status` every 2 seconds and the payload included recent
SKIP-heavy decision/event data. An open dashboard tab produced about 900 API
requests in 30 minutes.

## Symptoms

- `weather-dashboard` used more CPU than expected for a read-only page.
- The left panel was dominated by `DECISION SKIP` rows.
- `/api/status` responses were much larger than the operator needed.
- The trade CSV cumulative count path scanned more than necessary.

## Solution

Keep the dashboard live, but make each refresh cheaper:

- Replace high-volume event/SKIP feeds with open positions.
- Replace the old open-position/recent-trade bottom area with a realized PnL
  table that shows date, city, forecast temperature, threshold, expected exit,
  entry, exit, PnL, and ROI.
- Move bounded recent trades to the right column.
- Remove `events`, `recent_decisions`, and pressure rows from the API payload.
- Cache cumulative trade action totals incrementally instead of scanning the
  trade CSV on every request.
- Poll every 5 seconds while visible and every 30 seconds while hidden.

## Why This Works

"Realtime" for this bot means the operator sees current positions and account
state within a few seconds. It does not require sending hundreds of repeated
SKIP rows every 2 seconds. Other production dashboards usually avoid this by
using one or more of: server-side caches, smaller payloads, WebSockets/SSE for
small deltas, slower background-tab polling, pagination, or pre-aggregated
metrics.

## Prevention

Before adding dashboard panels, ask whether the operator will act on the data.
If not, keep it as an aggregate or omit it. Do not add unbounded recent feeds for
high-frequency events such as SKIP decisions. Measure API response size and
request rate after UI changes, not just whether the page loads.

## Related

- [Avoid full decision-log scans on dashboard startup](./dashboard-large-decision-log-initial-scan.md)
