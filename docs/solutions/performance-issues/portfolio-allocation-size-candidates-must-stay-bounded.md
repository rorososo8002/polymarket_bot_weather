---
title: Portfolio allocation size candidates must stay bounded
date: 2026-06-05
category: performance-issues
module: weather_bot.portfolio
problem_type: performance_issue
component: service_object
symptoms:
  - "`_allocation_sizes` could create one candidate for every dollar between the minimum order and a large order cap."
  - "Large paper bankrolls could make two-leg portfolio selection multiply thousands of size combinations."
  - "The optimizer could spend time comparing near-duplicate order sizes instead of reacting to moving weather markets."
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [paper-trading, portfolio, performance, sizing, bankroll]
---

# Portfolio Allocation Size Candidates Must Stay Bounded

## 1. What The Problem Was

`_allocation_sizes` is the order-size candidate table. It gives the portfolio
selector sizes to test, such as `$10`, `$11`, and `$12`.

The old version generated every whole-dollar size from the minimum order up to
the allowed limit. That is fine for the default small paper account, but a large
`BANKROLL_USD` or higher order cap could turn the table into tens of thousands
of candidates.

## 2. Why It Was A Problem

The event portfolio selector compares candidate legs and candidate sizes. For a
two-leg plan, the size grids multiply: 1,000 possible sizes on the left and
1,000 possible sizes on the right become 1,000,000 combinations.

That matters because weather markets move. The bot can have a good forecast and
a good price, but if portfolio selection burns time comparing tiny size
differences, the paper result stops measuring the strategy cleanly.

## 3. How It Was Fixed

`_allocation_sizes` now keeps dense one-dollar spacing only while the allowed
range is small enough. When the range is large, it creates at most 50 candidate
sizes by spacing them across the range.

The important anchors are still preserved:

- the minimum paper order
- the maximum allowed order for that candidate
- the preferred candidate size when it is still affordable

`select_event_portfolio` passes each candidate's original `size_usd` as the
preferred size. If that preferred size is above the current exposure cap, the
cap remains the maximum anchor because the larger size is not actually
available.

## 4. What To Check Next Time To Prevent The Same Mistake

- Add a large-bankroll test before changing optimizer loops.
- Assert the candidate list has a hard maximum length.
- Assert small-account ranges still keep one-dollar spacing.
- Assert the minimum, maximum, and preferred affordable size remain in the
  sparse grid.
- Include a decimal-anchor case so rounding does not accidentally break the
  candidate cap.

## 5. What This Project Must Be Especially Careful About

This bot is trying to validate paper-trading profitability, not win a math
contest by checking every possible cent of size. The optimizer needs enough
candidate sizes to make a good decision, but it must stay bounded so market
evaluation remains responsive.

The safe rule is:

```text
small bankroll -> dense size comparison is okay
large bankroll -> bounded sparse comparison
always keep minimum, maximum, and affordable preferred size
```

## Related Issues

- [Final order depth must follow entry sizing](../logic-errors/final-order-depth-must-follow-sizing.md)
- [Portfolio scenario probabilities must be coherent before normalization](../logic-errors/portfolio-scenario-probabilities-must-be-coherent.md)
