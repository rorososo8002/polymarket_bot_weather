---
title: Verify public dashboard API access before sharing the URL
date: 2026-05-29
category: workflow-issues
module: weather_bot.dashboard
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "The user asks for the dashboard URL"
  - "The dashboard HTML loads but the UI shows no live data"
  - "The public VPS URL and local tunnel URL behave differently"
tags: [dashboard, public-url, token, vps, verification]
---

# Verify public dashboard API access before sharing the URL

## Context

The dashboard page at `http://140.245.69.242:8787/` can return HTTP 200 while
the real dashboard data endpoint, `/api/status`, returns HTTP 403 when
`DASHBOARD_TOKEN` is configured. In that state, telling the user to open the bare
URL is misleading: the shell page loads, but the metrics cannot load.

The local address `http://127.0.0.1:8787/` is not authoritative unless an SSH
tunnel is actually listening. The canonical access path for this project is the
public VPS address.

## Guidance

Before giving a dashboard URL, verify both layers from the local machine:

```powershell
curl.exe -i http://140.245.69.242:8787/
curl.exe -i http://140.245.69.242:8787/api/status
```

Interpret the result plainly:

- `/` returns 200 and `/api/status` returns 200: the bare public URL is usable.
- `/` returns 200 and `/api/status` returns 403: the page is alive, but the API
  requires `DASHBOARD_TOKEN`; do not claim the bare URL works.
- `127.0.0.1:8787` fails: the local tunnel is absent; do not suggest it unless
  the user explicitly wants a tunnel.

If the user explicitly wants the public dashboard to work without a token, first
state the tradeoff: the read-only dashboard becomes visible on the public
internet. Only after that approval should an agent clear `DASHBOARD_TOKEN` in
`/etc/polymarket-weather-bot/dashboard.env`, restart
`polymarket-weather-dashboard`, and re-run both curl checks.

## Why This Matters

`systemctl status` only proves the server process is running. It does not prove
the browser can fetch dashboard data without authentication. Verifying the page
and API separately prevents wasting time on local tunnels, stale tokens, or
false "server is alive" conclusions.

## When To Apply

- The user asks "what is the dashboard address?"
- The user says the dashboard does not show anything.
- The dashboard service is active but the browser view is blank or locked.
- The answer would otherwise mention `127.0.0.1:8787`.

## Related

- [Verify remote dashboard state and entry counters before diagnosing paper entries](./verify-remote-dashboard-state-and-entry-counters.md)
- [Verify VPS code, environment, and API health before trusting the dashboard](./verify-vps-code-env-and-api-health-2026-05-26.md)
