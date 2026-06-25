---
title: Moderate binary ask premium should not block side-specific entries
date: 2026-06-25
category: logic-errors
module: weather_bot.live_paper_runner
problem_type: logic_error
component: background_job
symptoms:
  - "High-probability Singapore residual signals skipped with `YES+NO ask sum abnormal 1.100 outside 1±0.05`."
  - "The bot calculated 31°C YES and 32°C NO as good candidates but never reached side-specific edge checks."
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [paper-trading, orderbook, binary-market, edge, singapore]
---

# Moderate binary ask premium should not block side-specific entries

## Problem
The paper bot skipped otherwise valid temperature entries when the executable
YES ask plus NO ask was more than five cents away from 1.00. Live Polymarket
binary books can have a wider combined ask because both sides include
market-maker spread.

## Symptoms
- Singapore June 25 had verified station evidence and residual probabilities
  above the 90% tier.
- The global `YES+NO ask sum` check returned `SKIP` before `_side_result()` could
  calculate the selected side's edge, spread, expected return, and executable
  size.

## What Didn't Work
- Treating `YES ask + NO ask ≈ 1.00` as an entry requirement. That is useful as a
  token-mapping sanity check, but it is too strict for thin live books.
- Looking only at the dashboard "no position" state. The skip diagnostics showed
  the model signal was present; the failure happened later in order-book
  validation.

## Solution
Keep the ask-sum check as a malformed-book guard, but make it broad enough that
normal live spread reaches the side-specific gates:

```python
if yes_no_sum < 0.60 or yes_no_sum > 1.40:
    return f"YES+NO ask sum abnormal {yes_no_sum:.3f} outside 0.60-1.40"
```

Then let the existing per-side checks decide whether to trade:

- selected-side executable VWAP
- selected-side bid/ask spread
- price impact
- conservative probability edge
- expected net return after fees

## Why This Works
The global ask-sum check should answer only: "Are these YES/NO token books so
malformed that the token pair may be wrong?" It should not answer: "Is the YES
side worth buying?" or "Is the NO side worth buying?" Those are side-specific
questions and already have stricter executable-price checks.

The regression test now proves both sides of the rule:

- `YES ask 0.88 + NO ask 0.26 = 1.14` does not globally block a high-edge YES.
- `YES ask 0.82 + NO ask 0.82 = 1.64` still fails closed as malformed.

## Prevention
- Use global binary-book sanity checks only for malformed token-pair detection.
- Put trading economics in side-specific tests so wide but executable books do
  not get rejected before edge calculation.

## Related Issues
- [Best-bid-ask messages are not executable order-book depth](best-bid-ask-indicative-not-depth.md)
