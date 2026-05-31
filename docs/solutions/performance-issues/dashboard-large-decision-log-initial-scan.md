---
title: Avoid full decision-log scans on dashboard startup
date: 2026-05-28
category: performance-issues
module: weather_bot.dashboard
problem_type: performance_issue
component: tooling
severity: medium
symptoms:
  - "The dashboard root page returned HTTP 200, but `/api/status` timed out."
  - "The dashboard service was active while the browser looked disconnected."
  - "The decision CSV had grown large enough that first-request aggregation stalled the API."
root_cause: missing_workflow_step
resolution_type: code_fix
tags: [dashboard, runtime-data, csv, performance, oracle]
---

# Avoid full decision-log scans on dashboard startup

## Problem

The Oracle dashboard service was active and served `/`, but the browser could not load live status because `/api/status` timed out. The first status request tried to aggregate a very large `paper_decisions.csv`, making the service look hung even though systemd showed it as running.

## Symptoms

- `systemctl is-active polymarket-weather-dashboard` returned `active`.
- `curl http://127.0.0.1:8787/` returned HTTP 200 quickly after restart.
- `curl http://127.0.0.1:8787/api/status?...` timed out with 0 bytes.
- `data/paper_decisions.csv` was hundreds of MB, while the dashboard API needed to answer interactively.

## What Didn't Work

Restarting the dashboard only fixed the static HTML route. The API timed out again because the next status call repeated the expensive initial CSV scan.

## Solution

Keep dashboard requests cheap when runtime files become large:

- Read recent CSV rows from the tail for display.
- Cap the first decision-total scan.
- For oversized decision logs, initialize scanner totals from recent rows and start incremental counting from the current file offset.
- Preserve the append cache for new decisions after the dashboard is already running.

## Why This Works

The dashboard is an operations surface, not an offline analytics job. It must remain responsive even when runtime logs grow. Tail-based initialization gives the operator fresh state immediately, and the existing offset cache keeps future appended rows cheap.

## Prevention

- Do not add dashboard endpoints that parse full runtime CSV/JSONL files on request.
- Treat `paper_decisions.csv`, `paper_raw_snapshots.jsonl`, and restart logs as token- and latency-dangerous.
- Verify dashboard fixes against large-file behavior, not only small test fixtures.
- If `/` works but `/api/status` hangs, check file sizes before blaming the SSH tunnel.
