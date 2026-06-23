---
title: Reuse fresh station evidence at final pre-trade
date: 2026-06-23
category: logic-errors
module: weather_bot.live_paper_runner
problem_type: logic_error
component: service_object
symptoms:
  - "A station signal passed initial evaluation but was blocked seconds later with confidence 0."
  - "The final pre-trade path fetched the same external station provider again within the station cache TTL."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [station-signal, final-pre-trade, cache-ttl, paper-trading, reliability]
---

# Reuse fresh station evidence at final pre-trade

## Problem

The runner calculated a valid same-station signal, then fetched the external
station provider again only a few seconds later during final pre-trade checks.
A transient provider failure could therefore erase a signal that was still
fresh and block an otherwise valid paper order.

## Symptoms

- The initial decision had full station confidence, but the final check logged
  `SKIP_FINAL_STATION_SIGNAL` with confidence 0.
- Provider request logs showed two station lookups inside one cache lifetime.

## What Didn't Work

- Retrying the provider at every stage did not make the evidence safer. It
  introduced another network failure point without obtaining meaningfully
  newer weather data.
- Removing all final checks would have been unsafe because CLOB tradability,
  executable depth, fees, and expected return can change immediately.

## Solution

Reuse the decision's station signal while its `decision_ts` age is within
`station_nowcast_cache_ttl_seconds`. Refetch and revalidate station evidence
only after that freshness window expires.

Keep the final CLOB accepting-orders check, executable ask-side VWAP, spread,
fee, expected-return, and exposure checks on every order attempt.

## Why This Works

Station observations update much more slowly than the order book. Reusing a
signal only inside its defined cache lifetime preserves the same evidence
freshness contract while removing a redundant external dependency call.
Market execution data is still refreshed at the last possible moment.

## Prevention

- Test that a fresh decision opens even when a replacement station estimator
  would fail if called.
- Test that an old or missing `decision_ts` still triggers a new station
  lookup and fails closed when the evidence has weakened.
- Verify provider request logs contain no second station call between a fresh
  decision and its paper fill.
