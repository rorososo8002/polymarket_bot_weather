---
title: Runner status writes need collision-resistant temp files
date: 2026-06-15
category: logic-errors
module: weather_bot.runner_status, weather_bot.live_paper_runner, weather_bot.dashboard
problem_type: logic_error
component: background_job
symptoms:
  - "The live bot repeatedly logged No such file or directory for paper_runner_status.json.tmp during runner_status_update."
  - "Dashboard health showed station signal STALE and WebSocket FAILED after a fresh paper reset even though no streamable tokens existed yet."
  - "The bot stayed active under systemd while the realtime cycle kept retrying from status-write failures."
root_cause: thread_violation
resolution_type: code_fix
severity: high
tags: [runner-status, heartbeat, websocket, dashboard, concurrency, paper-trading]
---

# Runner status writes need collision-resistant temp files

## Problem

After a fresh paper-account reset, the dashboard showed stale station-signal health
and failed realtime order-book health. The VPS log showed repeated realtime
cycle failures while writing `paper_runner_status.json`.

## Symptoms

- `journalctl -u polymarket-weather-bot` showed:

```text
REALTIME ERROR: realtime refresh cycle failed during runner_status_update:
[Errno 2] No such file or directory:
'/opt/polymarket-weather-bot/data/paper_runner_status.json.tmp'
-> '/opt/polymarket-weather-bot/data/paper_runner_status.json'
```

- The authenticated dashboard API showed `station_signal.status=STALE` and
  `websocket.status=FAILED`.
- `paper_runner_status.json` showed `0 tokens across 0 markets`, so there was
  no concrete WebSocket token that should have been subscribed yet.

## What Didn't Work

- Treating `systemctl is-active` as proof of health missed the failing
  realtime cycle.
- Looking only at the dashboard labels made the problem look like stale weather
  data or a broken WebSocket connection.
- Restarting services alone would not remove the race because the main
  realtime loop and worker status callbacks could write the same heartbeat
  file again.

## Solution

Make `paper_runner_status.json` writes safe for concurrent status updates:

- serialize write/update operations with a module-level lock
- write each payload to a unique temp path such as
  `paper_runner_status.json.<uuid>.tmp`
- then atomically replace the final status file

Also distinguish a zero-token discovery cycle from a WebSocket failure:

- `_stream_status_phase(..., token_count=0)` returns `stream_waiting`
- dashboard station-signal health returns `WAITING` when no observation attempt,
  success, failure, or persistence error exists
- dashboard WebSocket health returns `WAITING` when no streamable temperature
  token exists and there is no concrete WebSocket error

## Why This Works

`paper_runner_status.json` is a heartbeat board, not an account ledger. It is
updated by the main realtime loop and by worker callbacks. If all writers share
one `.tmp` path, writer A can replace and remove the temp file before writer B
calls `os.replace`, causing writer B to crash with `No such file or directory`.

Unique temp names remove that collision. The lock also keeps read-modify-write
status updates from stepping on each other inside one Python process.

The zero-token dashboard change prevents a separate operator confusion:
when there is nothing to subscribe to yet, the correct state is waiting for
market discovery, not failed executable order-book depth.

## Prevention

- Any status file written by multiple threads must use collision-resistant temp
  paths before atomic replace.
- Add a regression test that records the temp names used by consecutive
  `write_runner_status` and `update_runner_status_fields` calls.
- Add a dashboard regression test for the fresh-reset, zero-token state:
  station-signal health should be `WAITING`, WebSocket health should be `WAITING`,
  and the bot should not report a false failure.
- Verify live fixes with both the dashboard API and recent service logs; HTML
  liveness alone is not enough.

## Related Issues

- [Runner heartbeat and wall-clock cadence for long paper bot cycles](./runner-heartbeat-cadence-status-2026-05-25.md)
- [Realtime cycle exceptions must update runner status](./realtime-cycle-exceptions-must-update-runner-status.md)
