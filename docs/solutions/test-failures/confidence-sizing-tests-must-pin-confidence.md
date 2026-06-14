---
title: Pin confidence in tests that are not testing confidence sizing
date: 2026-06-14
category: test-failures
module: weather_bot portfolio tests
problem_type: test_failure
component: testing_framework
symptoms:
  - "Portfolio focused tests expected old order sizes after confidence-based sizing was added"
  - "Partial-fill reason text changed from the intended size to a confidence-discounted size"
root_cause: test_isolation
resolution_type: test_fix
severity: medium
tags: [confidence, sizing, pytest, portfolio, paper-trading]
---

# Pin confidence in tests that are not testing confidence sizing

## Problem

After confidence-based sizing was added, several portfolio tests still created
`WeatherSignal(..., confidence=0.90, ...)` while expecting the old full order
size. The tests were meant to verify order-book depth, repricing, fees, and
event portfolio behavior, not confidence discounts.

## Symptoms

- `test_evaluate_market_reprices_edge_when_final_order_walks_the_book` expected
  `$20.00` but got `$17.00`.
- `test_evaluate_market_scales_final_order_down_to_available_depth` expected
  `partial_fill=$10.00/$20.00` but the reason showed
  `partial_fill=$10.00/$17.00`.
- `test_run_cycle_opens_two_profitable_no_legs_for_same_event` opened no
  positions because the confidence-discounted order fell below `MIN_ORDER_USD`.

## What Didn't Work

- Treating the failures as portfolio optimizer regressions would have been the
  wrong diagnosis. The optimizer was reacting correctly to the new sizing input.
- Changing production sizing back to ignore confidence would have removed the
  intended safety behavior from strategy validation.

## Solution

When a test is not about confidence sizing, set signal confidence to `1.0` so
the test isolates the behavior it claims to cover:

```python
signal = WeatherSignal(0.80, 1.0, "test", "test", parse_weather_question(raw_market.question))
```

Keep dedicated confidence tests in `tests/test_risk.py` or targeted runner
tests where lower confidence is the behavior under test.

## Why This Works

`confidence` is a sizing multiplier, not another probability. A value below
`1.0` intentionally lowers the paper order size. Tests for liquidity depth,
final VWAP repricing, fee-adjusted shares, or portfolio selection should not
implicitly test confidence at the same time.

## Prevention

- In tests for non-confidence behavior, use `confidence=1.0` explicitly.
- In tests for confidence behavior, assert the multiplier and the reduced size.
- If a test failure shows a clean ratio like `$17` instead of `$20`, check
  `confidence_size_multiplier` before changing portfolio or liquidity code.

## Related Issues

- [Final Order Depth Must Follow Entry Sizing](../logic-errors/final-order-depth-must-follow-sizing.md)
- [Portfolio scenario probabilities must be coherent](../logic-errors/portfolio-scenario-probabilities-must-be-coherent.md)
