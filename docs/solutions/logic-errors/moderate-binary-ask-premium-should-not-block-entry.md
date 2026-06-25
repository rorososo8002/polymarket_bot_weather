---
title: Binary ask sums should not block side-specific entries
date: 2026-06-25
category: logic-errors
module: weather_bot.live_paper_runner
problem_type: logic_error
component: background_job
symptoms:
  - "High-probability Singapore residual signals skipped with `YES+NO ask sum abnormal`."
  - "The bot calculated good residual candidates but a global ask-sum guard could run before side-specific edge checks."
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [paper-trading, orderbook, binary-market, edge, singapore]
---

# Binary ask sums should not block side-specific entries

## Problem
The paper bot skipped otherwise valid temperature entries when the executable YES
ask plus NO ask looked "abnormal." That global sum is not the trading edge. A
live binary book can show a large combined ask because both sides include spread
or stale resting offers.

## Symptoms
- Singapore June 25 had verified station evidence and residual probabilities
  above the 90% tier.
- The global `YES+NO ask sum` check returned `SKIP` before `_side_result()`
  could calculate the selected side's edge, spread, expected return, and
  executable size.

## What Didn't Work
- Treating `YES ask + NO ask ≈ 1.00` as an entry requirement. It says little
  about whether the selected side is cheap versus the model probability.
- Looking only at the dashboard "no position" state. The skip diagnostics showed
  the model signal was present; the failure happened later in order-book
  validation.

## Solution
Remove the global ask-sum entry block. Once the explicit YES/NO token IDs are
known, entry should be decided by the selected side's executable economics:

```python
# evaluate_market() fetches both books, then lets _side_result() judge each side.
```

- selected-side executable VWAP
- selected-side bid/ask spread
- price impact
- conservative probability edge
- expected net return after fees

## Why This Works
The question "is YES worth buying?" depends on the YES ask, model probability,
fees, and exit/settlement estimate. The NO ask is not part of that trade unless
the bot is explicitly building a hedge.

The regression tests now prove both important cases:

- `YES ask 0.88 + NO ask 0.26 = 1.14` reaches side-specific edge checks.
- `YES ask 0.82 + NO ask 0.82 = 1.64` also reaches side-specific edge checks
  when the model says YES is still cheap.

## Prevention
- Put trading economics in side-specific tests so wide but executable books do
  not get rejected before edge calculation.
- Token-pair correctness belongs in explicit token mapping, not in combined ask
  arithmetic.

## Related Issues
- [Best-bid-ask messages are not executable order-book depth](best-bid-ask-indicative-not-depth.md)
