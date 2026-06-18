---
title: Exact Celsius locks must use station-display boundaries
date: 2026-06-16
last_updated: 2026-06-19
category: logic-errors
module: weather_bot.station_signal, weather_bot.live_paper_runner
problem_type: logic_error
component: service_object
symptoms:
  - "A new official-station lock signal was generated but the runner still treated it as a non-lock signal."
  - "Exact whole-C markets risked using stale model-era assumptions instead of station-display boundaries."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, exact-bucket, celsius, station-locks, signal-source]
---

# Exact Celsius locks must use station-display boundaries

## Problem

Whole-degree Celsius exact markets must follow the official settlement source's
display boundary. A `23C` daily-high market is still alive at `23.7C`, but a
recorded `24.0C` breaks the `23C` YES thesis. During the station-strategy
rewrite, the new `official-station-lock-*` source names were not initially
recognized by the runner's lock predicate.

## Symptoms

- `station_signal.py` produced source values such as
  `official-station-lock-strong_yes`.
- `evaluate_market()` still blocked the signal with the entry-only safety gate
  because `_is_official_nowcast_lock()` only recognized older lock names.
- Generic portfolio and realtime tests skipped before order-book evaluation
  even when their fixtures represented trusted station-lock signals.

## What Didn't Work

- Keeping only the old predicate names was not enough. A new signal source
  namespace must be added to every gate that decides whether a signal is
  tradeable.
- Treating `23.9C` as a NO trigger was wrong. Under source-display integer
  settlement, `23.9C` still displays as `23C`; the strong NO trigger starts at
  `24.0C` for a daily-high `23C` exact market.
- Leaving old model-language docs in place was unsafe because future agents
  could reintroduce model-era entry logic.

## Solution

Move the exact whole-C entry signal into `weather_bot.station_signal` and make
the runner recognize the new station-lock namespace:

```python
def _is_official_nowcast_lock(signal: WeatherSignal) -> bool:
    return (
        signal.entry_size_fraction_override is not None
        or "official_nowcast_lock=" in signal.note
        or "official-nowcast-lock" in signal.source
        or "official-station-lock" in signal.source
    )
```

Focused tests should cover both sides of the boundary:

```python
assert estimate_station_signal(... observed_high_c=23.9).source == "official-station-neutral"
assert estimate_station_signal(... observed_high_c=24.0).source == "official-station-lock-strong_no"
```

Realtime and portfolio tests that intentionally exercise downstream sizing or
liquidity math should mark their fixture signals as station locks, so they test
the intended downstream behavior instead of the entry-only gate.

## Why This Works

The strategy now has one active source of entry evidence: official
settlement-station observations. The signal generator owns the station-display
boundary math, and the runner's predicate owns the tradeability gate. Updating
both sides together prevents a valid station lock from being silently treated
as an ordinary low-trust signal.

## Prevention

- When introducing a new `WeatherSignal.source` namespace, update the predicate
  that decides whether the runner may trade it.
- Add tests that prove the exact source string is accepted by the entry-only
  gate.
- For exact whole-C markets, test `N.9C` and `N+1.0C` separately. They are not
  interchangeable.
- Keep docs focused on official station observations; do not describe removed
  model-entry paths as active strategy.
