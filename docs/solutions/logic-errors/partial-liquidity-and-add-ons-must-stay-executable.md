---
title: Partial Liquidity And Add-Ons Must Stay Executable
date: 2026-06-05
category: logic-errors
module: weather_bot.live_paper_runner, weather_bot.portfolio, weather_bot.paper
problem_type: logic_error
component: service_object
symptoms:
  - "A profitable paper candidate could be skipped when the full target order was too large for ask depth even though a minimum-sized order was executable."
  - "Allowing repeated same-market entries without a special add-on path would either block every add-on or create duplicate paper positions."
  - "A price drop alone could tempt averaging down even when the probability thesis had already broken."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, liquidity, add-on, portfolio, risk, fail-closed]
---

# Partial Liquidity And Add-Ons Must Stay Executable

## 1. What The Problem Was

Thin weather-market order books often have enough ask depth for a small entry
but not for the full target size. If the bot treats "target size not fully
available" as an automatic SKIP, it misses valid small paper trades.

At the same time, simply allowing repeated entries in the same market is unsafe.
The paper account could accidentally hold duplicate positions or even both YES
and NO in the same binary market.

`liquidity` is the real buyable depth sitting in the order book. If a price
looks good but the shares are not actually there, the bot must not write the
trade into `paper_state.json`.

## 2. Why It Was A Problem

The paper bot is trying to test realistic profitability, not a fantasy fill.
Two opposite mistakes are both bad:

1. Being too conservative: skip a $10 executable opportunity because the $20
   target order cannot fully fit.
2. Being too aggressive: pretend to buy $20 when only $10 exists, or average
   down after the forecast probability has already crossed the stop line.

`paper_state.json` is the paper account book. It stores cash, shares, average
entry price, and open positions. If add-ons are logged as separate duplicate
positions, later exposure caps, exits, settlement, and dashboard numbers become
hard to trust.

## 3. How It Was Fixed

The entry path now scales down only when the smaller trade is real:

1. Calculate the intended `size_usd`.
2. Recheck confirmed ask depth for that size.
3. If the full size does not fit but at least `MIN_ORDER_USD` fits, reduce
   `size_usd` to the executable amount and log `partial_fill`.
4. If even `MIN_ORDER_USD` does not fit, return SKIP with zero size.

Add-ons use a separate same-side path:

1. Opposite-side same-market entries still fail closed.
2. Same-side add-ons require the executable price to be at least
   `ADD_TO_POSITION_DROP_PCT` below the existing average entry.
3. The current side probability must stay above
   `probability_stop_threshold`.
4. Edge, expected return, cash, city exposure, city-date exposure, and minimum
   order checks still apply.
5. A successful add-on updates the existing position's shares, cost, and
   average entry price, then writes an `ADD` row to `paper_trades.csv`.

`ADD` is not a real exchange order. It is a paper-trading ledger action that
means "the simulated account bought more of an already-held side."

## 4. What To Check Next Time To Prevent The Same Mistake

- When changing liquidity rules, test both "partial depth is enough" and
  "partial depth is still below minimum order."
- When changing same-market rules, test same-side add-on and opposite-side
  rejection separately.
- Do not use price drawdown alone as an add-on signal. Check the probability
  stop threshold first.
- After an add-on, verify that position count stays the same, cash decreases,
  cost and shares increase, and average entry price moves correctly.
- Dashboard and reports must treat `ADD` as paper trade activity, not realized
  PnL.

## 5. What This Project Must Be Especially Careful About

This project is paper-only, but the paper ledger is the evidence for whether
live trading could ever be considered later. Therefore paper fills must stay
executable, and add-ons must never hide broken-thesis averaging down.

The safe rule is:

```text
scale down only to real executable depth
skip below the minimum order
add only to the same side
never add below the probability stop
update the existing paper position, not a duplicate
```

## Related Issues

- [Final Order Depth Must Follow Entry Sizing](./final-order-depth-must-follow-sizing.md)
- [Correlated Event Budget Needs Broker Backstop](./correlated-event-budget-needs-broker-backstop.md)
- [Paper Fees Must Flow Through Accounting](./paper-fees-must-flow-through-accounting.md)
