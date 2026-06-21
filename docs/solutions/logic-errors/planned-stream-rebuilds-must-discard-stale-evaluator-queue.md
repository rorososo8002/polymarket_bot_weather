---
title: Planned stream rebuilds must discard stale evaluator queue
date: 2026-06-18
category: logic-errors
module: weather_bot.live_paper_runner
problem_type: logic_error
component: background_job
symptoms:
  - "HOLD_STREAM_UNHEALTHY rows appeared at the normal 40-minute stream-cycle cadence."
  - "The hold reason said the WebSocket receiver thread was not running even though the stream later reported fresh executable depth."
  - "Held-position exit checks could run against a stream that the runner had intentionally stopped for a planned rebuild."
root_cause: async_timing
resolution_type: code_fix
severity: high
tags: [websocket, realtime-evaluator, stream-cycle, paper-trading, hold-stream-unhealthy]
---

# Planned stream rebuilds must discard stale evaluator queue

## Problem
The realtime runner rebuilt its WebSocket subscription every 40 minutes, but the
planned shutdown sequence stopped the old stream before stopping the realtime
evaluator. Any queued evaluation that drained after that point saw an
intentionally stopped receiver and wrote artificial `HOLD_STREAM_UNHEALTHY`
rows.

## Symptoms
- `paper_trades.csv` showed `HOLD_STREAM_UNHEALTHY` clusters on the
  `STREAM_CYCLE_INTERVAL_SECONDS=2400` cadence.
- The reason text often said `websocket receiver thread is not running`.
- A later status check could show `thread_alive=true`, `stale=false`, and fresh
  executable depth, proving this was not necessarily a persistent exchange or
  liquidity outage.

## What Didn't Work
- Treating the rows as pure market illiquidity was misleading. Some token-level
  stale-depth blocks were real, but the repeated whole-stream rows matched the
  runner's planned rebuild interval.
- Increasing stale thresholds would only hide true stale-book safety checks.
- Replacing WebSocket monitoring with polling would violate the executable-depth
  contract and still would not fix the stale queued work.

## Solution
At the normal stream-cycle boundary, stop the realtime evaluator with
`drain=False` before stopping the old WebSocket stream:

```python
if evaluator_worker is not None:
    failed_phase = "realtime_evaluator_stop"
    evaluator_worker.stop(drain=False)
    evaluator_worker = None
```

Then stop the station-signal worker and only then stop the old stream. The
discarded queue entries are old-window hints, not durable account evidence. The
next stream window will rebuild subscriptions and re-evaluate from fresh
executable depth.

A regression test should assert the planned cleanup order:

```text
evaluator.stop(drain=False) -> station_signal.stop -> stream.stop
```

## Why This Works
`RealtimeEvaluationCoalescer` is the strategy-evaluation waiting line. It is
useful while the stream window is active because it merges bursty token updates
before running paper strategy logic.

At a planned rebuild boundary, that queue belongs to the old stream window. If
it drains after `stream.stop()`, the exit logic correctly observes a dead
receiver, but the dead receiver is self-inflicted. Dropping the queue first
keeps true stale/dead WebSocket blocks intact while preventing the runner from
recording its own planned shutdown as a market-data failure.

## Prevention
- For planned stream rebuilds, discard queued realtime evaluator work before
  stopping the old WebSocket receiver.
- Keep token-level stale-depth checks unchanged; they still protect held exits
  when a specific token lacks fresh executable depth.
- Add lifecycle-order tests for long-running worker shutdowns, not only tests
  for steady-state streaming.

## Related Issues
- [Realtime cycle exceptions must update runner status](./realtime-cycle-exceptions-must-update-runner-status.md)
- [Decouple WebSocket receiving from strategy evaluation](../performance-issues/decouple-websocket-receiver-from-strategy-evaluation.md)
