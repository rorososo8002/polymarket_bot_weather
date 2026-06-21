---
title: Risk caps must allow at least one minimum order
date: 2026-06-16
category: docs/solutions/best-practices
module: weather_bot.config, weather_bot.portfolio
problem_type: best_practice
component: background_job
severity: high
applies_when:
  - "Changing BANKROLL_USD, MIN_ORDER_USD, or exposure-fraction settings"
  - "Investigating why a paper bot has signals but opens no new positions"
tags: [paper-trading, sizing, risk-caps, minimum-order, portfolio]
---

# Risk caps must allow at least one minimum order

## Context

The paper bot can have valid signals, fresh station evidence, and executable books but
still open no positions when the risk cap for a city-date event is smaller than
`MIN_ORDER_USD`.

On the VPS investigation from 2026-06-16 KST, the safe public settings showed:

```text
BANKROLL_USD=200
MAX_EVENT_DATE_EXPOSURE_FRACTION=0.05
MIN_ORDER_USD=20.00
```

That gives one city-date event only `$200 * 0.05 = $10` of capacity, while the
minimum paper order is `$20`. The portfolio allocator correctly returns no
allocation sizes when the limit is below the minimum.

## Guidance

Before changing paper sizing settings, check the simple invariant:

```text
BANKROLL_USD * MAX_EVENT_DATE_EXPOSURE_FRACTION >= MIN_ORDER_USD
```

For the current paper bankroll:

```text
200 * 0.05 = 10  <  20
```

That combination cannot create a new event-level entry, no matter how good the
signal looks.

## Why This Matters

`MIN_ORDER_USD` is the smallest paper order the strategy is allowed to count as
realistic. The event exposure cap is the maximum budget shared by one city's
same-date temperature buckets. If the maximum budget is below the minimum order,
the bot has no legal order size.

This can look like a weak strategy or a quiet runner, but the real blocker is
configuration math.

## When To Apply

- When raising `MIN_ORDER_USD`.
- When lowering `MAX_EVENT_DATE_EXPOSURE_FRACTION`.
- When changing `BANKROLL_USD`.
- When dashboard positions stay flat even though WebSocket and station-signal health
  are green.

## Examples

Works:

```text
BANKROLL_USD=200
MAX_EVENT_DATE_EXPOSURE_FRACTION=0.10
MIN_ORDER_USD=20.00
event cap = $20
```

Blocks new event-level entries:

```text
BANKROLL_USD=200
MAX_EVENT_DATE_EXPOSURE_FRACTION=0.05
MIN_ORDER_USD=20.00
event cap = $10
```

## Related

- [Entry bankroll zero must skip before return estimate](../logic-errors/entry-bankroll-zero-must-skip-before-return-estimate.md)
- [Final order depth must follow sizing](../logic-errors/final-order-depth-must-follow-sizing.md)
- [Partial liquidity and add-ons must stay executable](../logic-errors/partial-liquidity-and-add-ons-must-stay-executable.md)
