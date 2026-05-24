---
title: Keep VWAP Slippage Out of Edge Math
date: 2026-05-25
category: logic-errors
module: weather_bot.edge
problem_type: logic_error
component: service_object
symptoms:
  - "A review finding called the slippage parameter dead code in yes_net_edge and no_net_edge"
  - "Subtracting slippage in the edge formula caused the existing no-double-count regression to fail"
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [vwap, slippage, edge, paper-trading, regression-test]
---

# Keep VWAP Slippage Out of Edge Math

## Problem
`yes_net_edge` and `no_net_edge` accepted a `slippage` argument but did not use it. Treating that as a missing subtraction looked plausible, but it would double-count execution cost because `p_exec` is already the VWAP fill price.

## Symptoms
- The focused edge tests passed only when slippage was subtracted.
- The full suite failed `test_vwap_slippage_is_not_subtracted_twice`, showing that the edge dropped from `0.05` to approximately zero when slippage was subtracted again.
- Live runner still needs the slippage value for audit output, but not for the expected-value equation.

## What Didn't Work
- Adding `- slippage` to the edge formula was the wrong fix. `executable_buy_price()` computes `p_exec` as ask-side VWAP, so the slippage versus best ask is already embedded in that price.

## Solution
Remove `slippage` from the `yes_net_edge` and `no_net_edge` function signatures. Keep the `slip` value returned by `executable_buy_price()` in `live_paper_runner.py` only for logging and diagnostics.

```python
edge = yes_net_edge(
    signal.p_true,
    p_exec,
    settings.estimated_fee_per_share,
    settings.model_error_margin,
    settings.resolution_error_margin,
)
```

## Why This Works
The edge equation should subtract execution price once. Since `p_exec` is already VWAP, a separate slippage subtraction charges the same order-book depth twice. Removing the parameter makes the API reflect that contract and prevents future callers from assuming slippage is an independent cost input.

## Prevention
- When a parameter looks unused, first identify whether another argument already contains the same economic cost.
- Keep regression tests around pricing-contract invariants such as "VWAP slippage is not subtracted twice."
- Prefer deleting misleading parameters over forcing them into formulas.

## Related Issues
- [Entry Stop Guard for Wide Bid Ask Spreads](entry-stop-guard-vwap-spread-2026-05-24.md)
