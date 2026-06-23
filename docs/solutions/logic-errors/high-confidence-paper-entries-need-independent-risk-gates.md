---
title: High-confidence paper entries need independent risk gates
date: 2026-06-23
category: logic-errors
module: weather_bot station_signal, portfolio, risk, paper, live_paper_runner
problem_type: logic_error
component: service_object
symptoms:
  - "A station probability above 90% forced nearly half the paper account into one shallow market."
  - "Two nominally different NO buckets could lose together because payoff overlap ignored the traded side."
  - "A profitable city-date exit could be followed by a sibling-bucket re-entry using the same official observation."
  - "An exact-temperature entry could run before the city-month formation-time distribution reached its 75th percentile."
root_cause: logic_error
resolution_type: code_fix
severity: critical
tags: [paper-trading, position-sizing, payoff-overlap, vwap, formation-time, re-entry]
---

# High-confidence paper entries need independent risk gates

## Problem

Model confidence was being treated as permission to relax several unrelated
risk controls. A high station probability could force a 50% account order,
walk a thin ask book far above its displayed best price, or combine positions
whose names differed even though their losing outcomes overlapped.

## Symptoms

- A 97% signal used the 50% event allowance as a required order size instead
  of a ceiling.
- Complement checks compared bucket labels but not whether each position was
  YES or NO.
- A new sibling bucket could reuse the exact observation that had already
  produced and closed a trade.
- Exact-bucket signals could enter while the historical remaining-movement
  probability was still material.

## What Didn't Work

- Raising the city limit alone did not solve concentration risk; it only moved
  the same oversized order to a larger limit.
- Looking only at the best ask did not prove that the intended dollar amount
  was executable near that price.
- Treating different bucket names as diversification did not model their
  actual winning and losing temperature ranges.

## Solution

Keep the controls independent:

1. Above 90%, allow an event to use up to 50% of equity, but let fractional
   Kelly choose the actual size. The 50% value is a ceiling, never a command.
2. Compare YES/NO winning-temperature intervals before combining event legs.
   Reject any combination whose losing payoff outcomes overlap.
3. Reject an entry when intended-size ask VWAP moves beyond the configured
   absolute or percentage price-impact limit, both at evaluation and final
   pre-trade time.
4. For exact buckets, wait until the city, month, and direction profile reaches
   its final-formation 75th percentile unless the bucket is already physically
   impossible from a verified monotonic observation.
5. Store `station_observed_at` in the paper execution ledger and block another
   city-date entry that reuses the same official observation after a realized
   exit.

## Why This Works

Probability answers "how likely is the outcome?" It does not answer "how much
can this account safely risk?", "can that size actually be bought near the
quoted price?", or "do these positions fail together?" Applying a separate
gate to each question prevents one impressive probability from overruling
execution realism and portfolio safety.

## Prevention

- Regression-test sizing ceilings separately from Kelly sizing.
- Express event compatibility in payoff ranges, including the traded side.
- Test both the first book evaluation and the final pre-trade book refresh.
- Version station evidence in the execution ledger so re-entry checks survive
  process restarts.
- Preserve the strong-NO exception only when a verified monotonic observation
  has already made the exact bucket impossible.

## Related Issues

- [Final pre-trade checks must revalidate paper entries](final-pre-trade-check-must-revalidate-entry.md)
- [Reuse fresh station evidence at final pre-trade](reuse-fresh-station-evidence-at-final-pretrade.md)
- [VWAP slippage edge contract](vwap-slippage-edge-contract-2026-05-25.md)
