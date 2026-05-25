---
title: Runner heartbeat and wall-clock cadence for long paper bot cycles
date: 2026-05-25
category: logic-errors
module: weather_bot.live_paper_runner
problem_type: logic_error
component: background_job
symptoms:
  - "Dashboard showed no new bot activity after the last decision timestamp even though systemd reported the bot service active"
  - "A fixed refresh interval stretched into longer visible gaps because the loop slept after a long cycle completed"
  - "Market discovery and weather evaluation could run for minutes without writing any status visible to the dashboard"
root_cause: missing_workflow_step
resolution_type: code_fix
severity: high
tags: [heartbeat, dashboard, systemd, scheduling, paper-trading]
---

# Runner heartbeat and wall-clock cadence for long paper bot cycles

## Problem
The live paper bot looked stopped from the dashboard because the only visible timestamps came from decisions and trades. When market discovery or weather evaluation took several minutes, there was no fresh dashboard-visible status even though the `systemd` process was still active.

## Symptoms
- The dashboard appeared to stop at the last decision timestamp.
- `systemctl is-active polymarket-weather-bot` returned `active`, but that did not prove the strategy loop was making progress.
- The configured refresh interval behaved like "cycle runtime plus interval" instead of a wall-clock cadence.

## What Didn't Work
- Checking only `systemd` active/enabled status was insufficient. A live process can still be blocked inside discovery, evaluation, network retries, or sleep.
- Relying on `paper_decisions.csv` alone missed in-progress phases before the next decision row was written.

## Solution
Add a runner heartbeat file next to `paper_state.json` and have the dashboard treat it as bot activity:

```python
write_runner_status(settings, "discovering", message="discovering markets", cycle_started_at=cycle_started_at)
write_runner_status(settings, "evaluating", message=f"evaluating {idx}/{len(markets)}", markets_done=idx, markets_total=len(markets))
write_runner_status(settings, "streaming", message="websocket streaming ...", cycle_started_at=cycle_started_at)
```

The dashboard reads `paper_runner_status.json` and reports `phase`, progress, `last_event_at`, and `next_scan_in_seconds` so a long discovery phase is visible as `DISCOVERING` rather than looking dead.

Also keep long-running loops on wall-clock cadence. In the current realtime runner, market discovery and forecast signals refresh every `forecast_refresh_interval_seconds`; order-book updates arrive through WebSocket events between refreshes:

```python
refresh_started_at = datetime.now(timezone.utc)
while True:
    elapsed = (datetime.now(timezone.utc) - refresh_started_at).total_seconds()
    if elapsed >= settings.forecast_refresh_interval_seconds:
        break
    time.sleep(1)
```

## Why This Works
The heartbeat separates process liveness from strategy progress. The dashboard no longer needs a new trade or decision to prove the bot is alive, and the scheduler no longer adds a full scan interval after an already-long cycle.

## Prevention
- Treat `systemd active` as only a process-level signal; verify domain progress with heartbeat or output data timestamps.
- Any background job with long blocking phases should write phase/progress status before and during the phase.
- Add regression tests for dashboard heartbeat precedence and wall-clock cadence calculations.

## Related Issues
- `docs/solutions/logic-errors/entry-stop-guard-vwap-spread-2026-05-24.md`
