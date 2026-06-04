---
title: Final Order Depth Must Follow Entry Sizing
date: 2026-06-05
category: logic-errors
module: weather_bot.live_paper_runner, weather_bot.edge
problem_type: logic_error
component: service_object
symptoms:
  - "`_side_result()` could ask the order book for max-single-market depth before calculating the actual paper order size."
  - "A $10 executable candidate could be skipped because $100 of ask depth was unavailable."
  - "Final VWAP changes were not guaranteed to refresh edge, fees, shares, and expected return before entry."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, orderbook, liquidity, sizing, vwap, fail-closed]
---

# Final Order Depth Must Follow Entry Sizing

## 1. What The Problem Was

`_side_result()` checked ask-side liquidity with:

```python
target_usd = max(settings.min_order_usd, bankroll_before_entry * settings.max_single_market_fraction)
```

That target is a risk ceiling, not necessarily the actual order. On a $1,000
paper bankroll with a 10% max-single-market cap, the first liquidity check asked
whether $100 could be bought. If `ENTRY_FRACTION` later produced a $10 order,
the bot had already rejected the candidate.

`liquidity` means the real buyable or sellable depth in the order book. A good
price is not enough; the bot must also prove enough shares are available for
the order it is actually about to paper-trade.

## 2. Why It Was A Problem

The paper bot is supposed to test whether the strategy can make money under
realistic execution limits. Requiring depth for a larger order than the one the
bot would place is not realistic caution; it changes the experiment.

The bad sequence was:

1. Ask whether the market can absorb the maximum allowed order.
2. Skip if that large depth is missing.
3. Only afterward calculate the smaller actual `size_usd`.

That can miss thin but valid weather-market opportunities. It also hides the
true blocker in SKIP diagnostics: the market might have enough depth for the
planned paper order but not for the maximum possible order.

## 3. How It Was Fixed

The entry evaluator now separates a price probe from the final order check:

1. Probe executable ask depth with `MIN_ORDER_USD`.
2. Use that `p_exec` plus the taker fee to calculate `edge` and `size_usd`.
3. Recheck executable ask depth for final `size_usd`.
4. If final VWAP differs from the probe VWAP, recalculate edge, fees, shares,
   and expected return from the final price.
5. If final `size_usd` cannot be absorbed, return SKIP with zero size.

`p_exec` is the VWAP, the average price the bot expects after walking through
the ask levels. `size_usd` is the all-in paper budget for this one entry. These
two values must be tied to the same executable order size before recording a
candidate.

Focused regression tests cover:

- a $1,000 bankroll with 10% max-single-market cap where a $10 order survives
  even though $100 depth is unavailable;
- a final $20 order that still skips when only $10 depth exists;
- a final order that walks from a $0.40 best ask into $0.50 depth and therefore
  recalculates `p_exec` to $0.45 and edge to match.

## 4. What To Check Next Time To Prevent The Same Mistake

- When a helper accepts a target order size, confirm whether the value is a
  ceiling, a probe, or the actual intended order.
- Test the case where `MIN_ORDER_USD` is executable but the maximum cap is not.
- Test the opposite case where the minimum probe is executable but the final
  computed size is not.
- If a final VWAP changes after walking the book, assert the logged edge and
  expected-return fields use the final VWAP.
- Keep SKIP results at `size_usd=0` and `size_shares=0` whenever final
  executable depth is missing.

## 5. What This Project Must Be Especially Careful About

This project must never record paper fills from unavailable order-book depth.
Paper trading is not real-money execution, but it is the evidence ledger for
whether live trading could ever be justified.

The safe rule is:

```text
probe small to estimate price
size the actual order
recheck final executable depth
reprice if depth changes the VWAP
skip if the final order cannot really fit
```

That keeps the bot less over-conservative than the old max-depth check while
still preserving the fail-closed rule.

## Related Issues

- [Best-bid-ask messages are not executable order-book depth](./best-bid-ask-indicative-not-depth.md)
- [VWAP Slippage Edge Contract](./vwap-slippage-edge-contract-2026-05-25.md)
- [Entry Bankroll Zero Must Skip Before Return Estimate](./entry-bankroll-zero-must-skip-before-return-estimate.md)
