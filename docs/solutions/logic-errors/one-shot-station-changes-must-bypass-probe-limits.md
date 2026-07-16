---
title: One-shot station changes must bypass bounded probe limits
date: 2026-07-17
category: logic-errors
module: weather_bot.live_paper_runner
problem_type: logic_error
component: background_job
symptoms:
  - "Only the first four city events were reevaluated after one official-station refresh changed several stations."
  - "Station-driven candidates waited behind ordinary order-book updates or disappeared after one evaluator exception."
  - "Later cities reached final REST price checks only after the executable ask had already moved near 0.99."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [official-station, one-shot-event, urgent-queue, retry, coalescer, parallel-prefetch, paper-trading]
---

# One-shot station changes must bypass bounded probe limits

## Problem

An official station change is a one-shot wake-up signal. The old runner reused
the four-event rotating probe that was designed for quiet-period background
checks. When more than four city events were affected, the remaining events
could be omitted permanently because the same station state might not change
again.

This made a healthy process look deceptively normal: the WebSocket stayed
connected and some cities traded, while other cities never reached the
candidate or skip-decision stage.

## Symptoms

- The same European cities appeared repeatedly while later or less frequently
  updated cities had no matching realtime evaluation evidence.
- A high moving from 30 to 29, or a low moving from 21 to 22, did not guarantee
  that every linked event was reevaluated.
- Ordinary price updates could sit ahead of the station change in one queue.
- A full queue could drop the station change like any ordinary update.
- An evaluator exception removed the dequeued event without retrying it.
- Reapplying the coalescing delay before every small batch compounded latency.

## What Did Not Work

- Increasing only the station polling rate wastes the official API allowance
  but does not recover an event that was never queued.
- Keeping the four-event round-robin is appropriate for fallback probes, not
  for a one-shot change notification.
- Increasing only the batch size does not fix queue priority, queue-full loss,
  or exception loss.
- Logging an evaluator error explains the loss but does not give the event
  another chance to be processed.
- Parallel REST calls without a deadline can let one slow request block every
  ready city.
- Abandoning a timed-out future without invalidation lets its older response
  arrive later and overwrite a newer fallback book.

## Root Cause

The runner treated two different jobs as if they had the same delivery
contract:

1. A quiet-period probe is repeatable and may be bounded and rotated.
2. A detected station-state change is non-repeatable and every affected event
   must be delivered at least once.

The queue also had no urgent class, no ordinary-event eviction policy, and no
bounded retry. Final candidate network checks were serialized even though
their reads were independent.

## Solution

When `changed_station_ids` is present, map every changed station to every
supported current event and bypass the four-event probe cap. Keep the cap and
rotation only when no actual station change was reported.

The realtime coalescer now:

- marks station changes as urgent;
- processes urgent events ahead of ordinary book noise;
- promotes an already queued ordinary event when its station changes;
- evicts one ordinary event for an urgent event when the bounded queue is full;
- preserves urgency through one bounded retry after an evaluator exception;
- waits for coalescing only on the first idle-to-active batch and drains an
  existing backlog without another delay;
- accepts up to 64 events in the default batch, covering the current city set.

The station-state key includes observation time, current and extreme
temperatures, high-departure and low-rebound confirmation, publication-due
status, and unavailability reason. A time-driven safety-state transition can
therefore wake the correct events even when the numeric temperature is
unchanged.

## Fast Final Checks Without Racing the Ledger

Candidate calculations run first, then independent CLOB tradability and REST
book reads are prefetched with at most eight workers. The global prefetch
deadline is 1.5 seconds, so one slow city cannot hold every ready city.

Only raw responses are fetched in worker threads. A per-token generation
number is checked before publishing a result into the shared cache. A timed-out
worker is invalidated before the serialized fallback begins, so its older
response cannot overwrite a newer fallback book when it eventually returns.

Cash, exposure, positions, `paper_state.json`, and CSV ledger writes remain
serialized. Before each event portfolio is applied, the runner recalculates
the current entry bankroll and exposure room.

## Why This Works

The change fixes delivery before changing strategy thresholds. Every actual
station change reaches the evaluator, urgent evidence is not buried under
ordinary price noise, and a transient failure gets one more chance without an
infinite retry loop. Independent network waiting is overlapped, while all
money-changing paper-account operations retain one authoritative order.

The safety gates remain intact: a candidate still needs fresh same-station
evidence, an active and accepting CLOB market, executable ask-side depth,
spread and fee checks, expected return, and available exposure.

## Verification

Keep regression tests for all of these cases:

- seven changed city events all bypass the four-event probe cap;
- an urgent station event jumps ahead of an ordinary queue;
- urgent insertion evicts ordinary work when the queue is full;
- a failed urgent evaluation is retried once with urgency preserved;
- an existing backlog drains without repeated coalescing delay;
- high-departure and low-rebound changes alter the station-state key;
- final book reads overlap instead of running serially;
- one slow prefetch does not delay a ready token;
- a timed-out old response cannot overwrite a newer fallback book;
- the full local test suite passes before deployment.

## Prevention Checklist

- Do not reuse bounded sampling helpers for non-repeatable change events.
- Make delivery guarantees explicit for every background event source.
- Track urgent processed, waiting, lag, and dropped counts in runner status.
- Keep queue limits and retry limits finite.
- Never publish a background response after its generation was invalidated.
- Parallelize independent reads, not account or ledger mutations.
- Measure last-city completion lag, not only first-batch dispatch time.
