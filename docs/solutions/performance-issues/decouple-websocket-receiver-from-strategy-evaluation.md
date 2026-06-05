---
title: Decouple WebSocket receiving from strategy evaluation
date: 2026-06-06
category: performance-issues
module: weather_bot.live_paper_runner
problem_type: performance_issue
component: background_job
symptoms:
  - "The WebSocket receiver callback could run strategy evaluation and append decision rows inline."
  - "Bursty token updates for one weather event could produce repeated evaluations and high-volume decision ledger writes."
  - "A slow evaluator could delay later price-message handling on the receiver thread."
root_cause: thread_violation
resolution_type: code_fix
severity: high
tags: [websocket, paper-trading, coalescer, queue, runner-status, decision-ledger]
---

# Decouple WebSocket receiving from strategy evaluation

## Problem
The realtime WebSocket receiver was doing too much work. After applying an
order-book update, `on_update()` could immediately run strategy evaluation,
portfolio selection, close checks, and `paper_decisions.csv` writes.

`paper_decisions.csv` is the strategy judgment ledger, not trash output. It
must be preserved, but it should record useful decisions rather than every
tiny price-message burst.

## Symptoms
- `OrderBookMarketStream.apply_message()` called `on_update()` after cache
  updates.
- The realtime runner's `on_update()` called `_evaluate_realtime_update()`
  directly.
- `_evaluate_realtime_update()` ran paper-only strategy math and appended
  decision evidence rows.
- Multiple token updates for one event could trigger duplicate event
  evaluations before the useful state had materially changed.

## What Didn't Work
- Keeping one global lock around direct evaluation protected shared state, but
  it still made the receiver wait for slow strategy work.
- Truncating or rotating `paper_decisions.csv` would reduce disk pressure, but
  it would also destroy the strategy evidence ledger.
- Ignoring WebSocket updates would protect disk usage, but it would also throw
  away executable price evidence.

## Solution
Add a bounded `RealtimeEvaluationCoalescer` worker in
`src/weather_bot/live_paper_runner.py`.

The receiver path now stays small:

```python
def on_update(updated_token_ids: set[str]) -> None:
    evaluator_worker.enqueue_tokens(updated_token_ids)
```

The worker maps token IDs to weather-event keys, waits a short coalescing
window, merges duplicate event updates, and then calls `_evaluate_realtime_update()`
outside the receiver callback. It keeps the old paper-only evaluator and broker
path, but moves that work to a separate thread.

The worker also exposes operational counters in `paper_runner_status.json`
under `realtime_evaluator`:

- `queue_depth`: how many event evaluations are waiting.
- `coalesced_update_count`: how many same-event updates were merged instead of
  evaluated separately.
- `dropped_update_count`: how many new event updates were not queued because
  the bounded queue was full.
- `error_count` and `last_error`: whether worker evaluation failed while the
  WebSocket receiver can keep accepting messages.

## Why This Works
The WebSocket receiver is the market-price intake lane. It should update the
order-book cache and leave quickly so later price messages are not delayed.

The coalescer changes repeated updates for one city/date/event from this shape:

```text
price update -> evaluate -> write decision
price update -> evaluate -> write decision
price update -> evaluate -> write decision
```

into this shape:

```text
price update -> mark event dirty
price update -> merge into same dirty event
worker -> evaluate latest state once -> write decision evidence once
```

That preserves the paper decision ledger while reducing burst noise and keeping
slow evaluation failures observable through runner status.

## Prevention
- Never call heavy strategy evaluation or ledger writes directly from a
  WebSocket receiver callback.
- When many tokens belong to one weather event, coalesce by event key rather
  than by raw token ID.
- Keep the queue bounded and expose queue depth, coalesced count, dropped
  count, and worker errors in runner status.
- Add focused tests that prove enqueueing does not run evaluation inline, burst
  updates merge to one evaluation, and worker exceptions do not kill receiving.

## Related Issues
- [Separate forecast freshness from WebSocket stream health](../logic-errors/explicit-forecast-and-websocket-health.md)
- [Realtime orderbook requirements are not polling requirements](../workflow-issues/realtime-orderbook-requirement-not-polling-2026-05-26.md)
- [Avoid full decision-log scans in runtime readers](./dashboard-large-decision-log-initial-scan.md)
