---
title: Entry Stop Guard for Wide Bid Ask Spreads
date: 2026-05-24
category: logic-errors
module: weather_bot.live_paper_runner
problem_type: logic_error
component: service_object
symptoms:
  - "Paper trades closed almost immediately with losses far beyond the configured 10 percent stop"
  - "Entry used ask-side VWAP while mark and exits used bid-side VWAP"
  - "Low-priced contracts passed the absolute spread filter even when the bid was already below the stop price"
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, stop-loss, vwap, liquidity, spread]
---

# Entry Stop Guard for Wide Bid Ask Spreads

## Problem
The paper bot opened several weather positions at ask-side VWAP, then immediately marked them to bid-side exit VWAP below the stop price. The stop-loss direction was correct, but entry allowed markets where the position was already underwater past the stop on an executable exit basis.

## Symptoms
- The dashboard showed six fast stop-loss closes and about -$116 realized PnL.
- Example: Austin opened near `0.1299`, had a stop near `0.1169`, then closed at exit VWAP near `0.0800`.
- The absolute spread filter allowed `0.13 ask / 0.08 bid` because the spread was only `0.05`, even though that is a large relative and stop-relevant gap.

## What Didn't Work
- Checking only whether `stop_loss_price = entry * (1 - stop_loss_pct)` was reversed did not explain the behavior. The stop formula was correct.
- The old liquidity checks (`spread <= 0.20`, ask range, bid notional) were too coarse for low-priced binary contracts.

## Solution
Add a stop-aware entry guard in `src/weather_bot/live_paper_runner.py`: after computing the proposed entry size, calculate the executable sell VWAP for that same share count. If the exit VWAP is unavailable or already at or below the stop price, skip the trade.

```python
exit_vwap, _slip = executable_sell_price(book, shares)
stop_price = max(0.01, p_exec * (1.0 - settings.stop_loss_pct))
if exit_vwap is None or exit_vwap <= stop_price:
    return EdgeResult("SKIP", signal.p_true, p_exec, edge, 0.0, 0.0, guard_reason)
```

The regression test is `test_entry_skips_when_exit_vwap_is_already_below_stop_loss`, which reproduces the `0.13 ask / 0.08 bid` shape and expects a `stop guard` skip.

## Why This Works
Paper entry pays the ask, but immediate liquidation receives the bid. A risk guard based only on entry price cannot protect against the initial bid/ask gap. Comparing proposed exit VWAP to the stop price makes the entry decision use the same executable price basis that exit logic uses.

## Prevention
- For any paper or live trading strategy, validate entry against executable exit price for the proposed size, not only against ask-side entry edge.
- Low-priced binary contracts need stop-aware or relative-spread checks; absolute spread caps are not enough.
- Keep regression tests around concrete order-book shapes that caused bad trades, especially when entry and exit use different sides of the book.

## Related Issues
- [Weather discovery false positives](weather-discovery-false-positives-2026-05-24.md)
- [VPS live paper runbook](../../VPS_LIVE_PAPER.md)
