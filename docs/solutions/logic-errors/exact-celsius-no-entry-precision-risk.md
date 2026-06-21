---
title: Exact Celsius locks must use station-display boundaries
date: 2026-06-16
last_updated: 2026-06-21
category: logic-errors
module: weather_bot.station_signal, weather_bot.live_paper_runner
problem_type: logic_error
component: service_object
symptoms:
  - "A new official-station lock signal was generated but the runner still treated it as a non-lock signal."
  - "Exact whole-C markets risked using stale model-era assumptions instead of station-display boundaries."
  - "An intraday observation signal with a size override was incorrectly treated as a settlement lock."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, exact-bucket, celsius, station-locks, intraday-observation, signal-source]
---

# Exact Celsius locks must use station-display boundaries

## Problem

Whole-degree Celsius exact markets must follow the official settlement source's
display boundary. A `23C` daily-high market is still alive at `23.7C`, but a
recorded `24.0C` breaks the `23C` YES thesis. During the station-strategy
rewrite, the new `official-station-lock-*` source names were not initially
recognized by the runner's lock predicate.

The later intraday observation strategy introduced a second distinction:
an `entry_size_fraction_override` controls paper position size, but does not
prove that settlement is locked. Using that field as a lock marker would remove
model and resolution error margins from a 90% intraday signal.

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
- Treating every signal with `entry_size_fraction_override` as a lock worked
  only while locks were the sole strategy family. It became unsafe as soon as
  intraday observation signals also carried explicit size limits.
- During the residual-probability rewrite, merely filtering out the old fixed
  `0.90` / `0.96` / near-close YES paths at the caller was not enough. Dead
  helper code can be accidentally reconnected later, so removed strategy
  formulas should be deleted from the signal module, not just bypassed.

## Solution

Move the exact whole-C entry signal into `weather_bot.station_signal`, classify
locks by explicit evidence markers, and use a broader predicate only for the
official-station entry gate:

```python
def _is_official_nowcast_lock(signal: WeatherSignal) -> bool:
    return (
        "official_nowcast_lock=" in signal.note
        or "official-nowcast-lock" in signal.source
        or "official-station-lock" in signal.source
    )


def _is_intraday_observation_edge(signal: WeatherSignal) -> bool:
    return (
        "signal_family=intraday_observation_edge" in signal.note
        or "official-station-intraday-" in signal.source
        or "official-station-residual-" in signal.source
    )


def _is_official_station_entry_signal(signal: WeatherSignal) -> bool:
    return _is_official_nowcast_lock(signal) or _is_intraday_observation_edge(signal)
```

The entry-only safety gate uses `_is_official_station_entry_signal()`.
Fee-aware fair-value calculations use `_is_official_nowcast_lock()` so only a
true settlement lock receives zero model and resolution error margins.

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
as an ordinary low-trust signal. Keeping the lock predicate narrower than the
entry predicate also prevents probabilistic intraday signals from being
upgraded into certainty merely because they use a smaller position size.

## Prevention

- When introducing a new `WeatherSignal.source` namespace, update the predicate
  that decides whether the runner may trade it.
- Never use sizing metadata as evidence quality. A position-size override is
  not a settlement lock.
- Test that intraday station signals pass the official-station entry gate while
  still receiving normal model and resolution error margins.
- Add tests that prove the exact source string is accepted by the entry-only
  gate.
- For exact whole-C markets, test `N.9C` and `N+1.0C` separately. They are not
  interchangeable.
- Keep docs focused on official station observations; do not describe removed
  model-entry paths as active strategy.
- When replacing a strategy formula, grep the implementation for the old
  constants and source names after tests pass. Removed formulas should not
  remain as callable helper branches.
