---
title: One-shot station changes must bypass bounded probe limits
date: 2026-07-17
last_updated: 2026-07-17
category: logic-errors
module: "weather_bot.live_paper_runner, weather_bot.polymarket_client"
problem_type: logic_error
component: background_job
symptoms:
  - "Only the first four city events were reevaluated after one official-station refresh changed several stations."
  - "Station-driven candidates waited behind ordinary order-book updates or disappeared after one evaluator exception."
  - "Later cities reached final REST price checks only after the executable ask had already moved near 0.99."
  - "Production urgent completion lag remained 21-23 seconds while four-event normal batches were in flight."
  - "After queue fixes, per-token REST books and evaluations without executable asks still kept urgent work near 5 seconds."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [official-station, urgent-queue, urgent-latency, batch-orderbook, executable-ask, wake-on-book-return, signal-eligibility, fail-closed]
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
- A deadline does not bound latency when the timed-out request is immediately
  repeated by a serialized fallback with a 15-second timeout.
- Giving urgent work queue priority is insufficient when an already running
  ordinary batch contains four non-preemptible events.
- Abandoning a timed-out future without invalidation lets its older response
  arrive later and overwrite a newer book.
- Sending many individual `/book` requests concurrently still spends one HTTP
  round trip per token and scales poorly when a station event has many buckets.
- Computing official signals before checking for an executable ask wastes the
  expensive part of evaluation on markets that cannot be entered.
- Dropping ask-less markets permanently would be fast but wrong: a later book
  update must be able to wake them again.
- Watching every price update, including YES-leaning or low-confidence signals,
  turns ordinary book noise back into expensive strategy work.
- A two-leg portfolio search can multiply every candidate pair by 50 allocation
  sizes per leg, making one city consume minutes on a small VPS.

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
- keeps ordinary book work in one-event batches and does not mix
  ordinary work into a waiting urgent batch;
- promotes an already queued ordinary event when its station changes;
- evicts one ordinary event for an urgent event when the bounded queue is full;
- preserves urgency through one bounded retry after an evaluator exception;
- waits for coalescing only on the first idle-to-active batch and drains an
  existing backlog without another delay;
- accepts up to 64 events in the default batch, covering the current city set.

Ordinary WebSocket updates evaluate only the markets whose books actually
changed, plus held positions that need exit evidence. They no longer trigger a
full sibling-market catch-up merely because another bucket has no cached signal.
Station changes use a separate path that queues every supported NO token in the
affected event, so the full city ladder is still reevaluated when the official
temperature changes.

Quiet markets also have explicit timer wakeups. Formation monitoring start,
city-month q75, and station-local 16:00 crossings are urgent. A residual
30-minute-bin transition is queued as ordinary preemptible work. Therefore a
time-only eligibility change does not depend on another book or METAR update.

## Prune Work Before Signal Evaluation

The price watcher now keeps only held-position tokens and new-entry NO tokens
whose current official signal has enough confidence and prefers NO. Held
positions remain watched regardless of entry eligibility because exit evidence
must not be lost. Station changes and timer thresholds still enqueue directly,
so narrowing the price watcher cannot hide a newly eligible market.

For a station or timer wakeup, the runner first collects every required token
and uses Polymarket's official `POST /books` endpoint. The endpoint accepts up
to 500 tokens per request, replacing dozens of per-token `/book` round trips
with one batch response.

After that response, a new-entry market proceeds only when its allowed side has
a non-crossed book with an executable ask. Missing or ask-less tokens are added
to `wake_when_book_returns`; a later WebSocket book update wakes the market and
tries again. This is a temporary defer, not a permanent exclusion. Held markets
bypass the entry-ask filter so bid-side exit evaluation continues.

Signals are then prepared only for price-ready markets. Before detailed edge
and portfolio evaluation, known signals are filtered again: production new
entries require an official station signal, confidence at least 0.50, and a NO
preference under the NO-only policy.

On process start, the first official-station refresh only establishes the
comparison baseline. Treating a missing previous key as a change creates a
false all-city urgent burst, which defeats normal-batch preemption immediately
after every deployment.

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
number is checked before publishing a result into the shared cache. A failed or
timed-out prefetch marks its condition and token as unavailable for that
evaluation. The final serialized stage fails closed immediately instead of
repeating the same network request with a 15-second timeout. The next genuine
market or station update clears the marker and tries a new bounded prefetch.

Production measurements exposed why both limits matter. Queue priority alone
still produced urgent completion lags of 23.015 and 21.586 seconds because a
running four-event ordinary batch could not be interrupted. Limiting ordinary
batches to one event reduces that non-preemptible window; the 64-event batch is
reserved for urgent fanout.

A second production sample still took 13.912 seconds for two urgent cities.
The evaluator preserved 22 per-market skip reasons, but opened and closed the
diagnostic JSONL file for every row. Realtime evaluation now buffers only those
non-accounting skip lines for the current evaluator batch and appends them in
one file write. Trade receipts and `paper_state.json` accounting remain
immediate and serialized.

After that batching change was deployed, the next two production urgent
completion samples fell to 6.078 and 4.414 seconds, with zero evaluator errors
and zero urgent drops. This is the relevant service-level measurement; the
aggregate processed-event count is only a throughput diagnostic.

Stage-level production measurements then exposed the remaining bottleneck. An
urgent update still took 5.432 seconds because 37 candidate books were fetched
individually and markets without asks continued downstream. Replacing those
requests with `POST /books` and pruning ineligible work produced final urgent
completion samples of 0.815 and 0.905 seconds. In the first final sample, 10
books arrived in one 0.518-second request and queue wait was 0.282 seconds.
Evaluator errors, dropped updates, and dropped urgent updates remained zero.

Cash, exposure, positions, `paper_state.json`, and CSV ledger writes remain
serialized. Before each event portfolio is applied, the runner recalculates
the current entry bankroll and exposure room.

The production strongest-NO profile defaults to one portfolio leg per city and
local date. This removes the quadratic two-leg allocation grid from the hot
path. Multi-leg paper experiments must opt in explicitly rather than slowing
every production city by default.

## Why This Works

The change fixes delivery before changing strategy thresholds. Every actual
station change reaches the evaluator, urgent evidence is not buried under
ordinary price noise, and a transient failure gets one more chance without an
infinite retry loop. Independent network waiting is overlapped, while all
money-changing paper-account operations retain one authoritative order.

The 1.5-second final-check deadline is now a real upper bound for that stage,
not the opening act of a slower serial retry. Missing final evidence still
blocks the paper entry; speed never substitutes a cached or invented price.

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
- candidate books use one official batch request rather than one request per
  token, with duplicate removal and 500-token chunking;
- ask-less new-entry markets stop before signal calculation and are registered
  to wake when their book becomes executable;
- YES-leaning, low-confidence, and non-official signals do not stay in the
  new-entry price watcher, while held positions do;
- one slow prefetch does not delay a ready token or trigger a serial retry;
- a timed-out old response cannot overwrite a newer book;
- the default ordinary batch contains exactly one event;
- many skip diagnostics are appended once per realtime evaluator batch without
  dropping any row;
- focused realtime tests and the full 801-test local and server suites pass;

## Prevention Checklist

- Do not reuse bounded sampling helpers for non-repeatable change events.
- Keep ordinary price batches at one event so a later urgent event waits for at
  most one already-running ordinary evaluation; reserve the full batch for
  urgent station work.
- Make delivery guarantees explicit for every background event source.
- Track urgent processed, waiting, lag, and dropped counts in runner status.
- Keep queue limits and retry limits finite.
- Never publish a background response after its generation was invalidated.
- Do not put a long serialized fallback behind a bounded concurrent deadline.
- Prefer an official batch API over many concurrent single-item requests when
  the same update fans out across many candidate tokens.
- Check executable ask availability before expensive signal and portfolio work.
- Every deferred market needs an explicit wake condition; defer-and-forget is
  another form of event loss.
- Keep new-entry price watching limited to eligible NO signals, but never apply
  that filter to held-position exits.
- Batch append-only diagnostic rows on the hot path; never defer account state
  or executed-trade ledger writes.
- Do not interpret initial state registration as a state transition.
- Parallelize independent reads, not account or ledger mutations.
- Measure last-city completion lag, not aggregate throughput or first-batch
  dispatch time. A statement such as "60 events in 40 seconds" does not prove
  that one urgent city completed promptly.
- Treat synthetic benchmarks as component checks, not proof of production
  latency. Persist queue, book-fetch, signal, market-evaluation, final-check,
  portfolio, and total timings, then verify them after deployment.
