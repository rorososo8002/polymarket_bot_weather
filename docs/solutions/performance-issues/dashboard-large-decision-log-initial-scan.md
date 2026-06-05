---
title: Avoid full decision-log scans in runtime readers
date: 2026-05-28
last_updated: 2026-06-05
category: performance-issues
module: weather_bot.dashboard, weather_bot.analyze_paper, weather_bot.shadow_signals
problem_type: performance_issue
component: tooling
severity: medium
symptoms:
  - "The dashboard root page returned HTTP 200, but `/api/status` timed out."
  - "The dashboard service was active while the browser looked disconnected."
  - "The decision CSV had grown large enough that first-request aggregation stalled the API."
  - "Paper analysis or shadow reports could materialize growing paper CSV ledgers in memory."
root_cause: missing_workflow_step
resolution_type: code_fix
tags: [dashboard, runtime-data, csv, performance, oracle]
---

# Avoid full decision-log scans in runtime readers

## Problem

The Oracle dashboard service was active and served `/`, but the browser could not load live status because `/api/status` timed out. The first status request tried to aggregate a very large `paper_decisions.csv`, making the service look hung even though systemd showed it as running.

The same runtime-data rule applies to offline paper reports. `paper_decisions.csv` and `paper_trades.csv` are paper-performance source ledgers, so they naturally grow during long VPS runs and must not be truncated just to make reports faster.

## Symptoms

- `systemctl is-active polymarket-weather-dashboard` returned `active`.
- `curl http://127.0.0.1:8787/` returned HTTP 200 quickly after restart.
- `curl http://127.0.0.1:8787/api/status?...` timed out with 0 bytes.
- `data/paper_decisions.csv` was hundreds of MB, while the dashboard API needed to answer interactively.
- Analysis reports could build a full Python list of decision or trade rows even when the report only needed counts, grouped averages, latest market decisions, or resolved outcomes.

## What Didn't Work

Restarting the dashboard only fixed the static HTML route. The API timed out again because the next status call repeated the expensive initial CSV scan.

## Solution

Keep dashboard requests cheap when runtime files become large:

- Read recent CSV rows from the tail for display.
- Cap the first decision-total scan.
- For oversized decision logs, initialize scanner totals from recent rows and start incremental counting from the current file offset.
- Preserve the append cache for new decisions after the dashboard is already running.
- Expose scope metadata on the dashboard API for scanner totals:
  `decision_totals_exact=true` and `decision_totals_scope=full` mean the
  totals came from a complete decision ledger scan, while
  `decision_totals_exact=false` and `decision_totals_scope=recent_tail` mean
  the dashboard used the large-file recent-tail guard.

Keep paper reports memory-bounded:

- Stream `paper_decisions.csv` and `paper_trades.csv` rows instead of calling `list(csv.DictReader(...))`.
- For `analyze_paper.py`, keep aggregate counts, edge-bucket sums, and one latest entry probability per market for resolved Brier scoring.
- For `shadow_signals.py`, keep the bounded signal set, then stream bot decisions and trades while retaining only matched comparisons and resolved outcome lookups.
- Preserve full-history report semantics by default. Add explicit `--since` or `--max-rows` style options before changing report scope.

## Why This Works

The dashboard is an operations surface, not an offline analytics job. It must remain responsive even when runtime logs grow. Tail-based initialization gives the operator fresh state immediately, and the existing offset cache keeps future appended rows cheap.

Streaming keeps the source-ledger meaning intact while avoiding memory growth proportional to the total row count. The report may still read every row when it promises full-history results, but memory now scales with aggregates, markets, or bounded research samples rather than with every CSV row.

## Prevention

- Do not add dashboard endpoints that parse full runtime CSV/JSONL files on request.
- Treat `paper_decisions.csv`, `paper_trades.csv`, `paper_raw_snapshots.jsonl`, and restart logs as token-, latency-, and memory-dangerous.
- Verify dashboard fixes against large-file behavior, not only small test fixtures.
- When a dashboard aggregate is intentionally bounded for performance, expose
  whether the number is full-history exact or a recent-tail estimate in the API
  response. A fast number that looks cumulative can mislead operators.
- If `/` works but `/api/status` hangs, check file sizes before blaming the SSH tunnel.
- For analysis/report code, add a large-fixture test that fails if a CSV reader is materialized with `list(...)`.
