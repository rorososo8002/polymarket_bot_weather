---
title: Paper fees must flow through accounting
date: 2026-06-03
last_updated: 2026-06-03
category: logic-errors
module: weather_bot.paper, weather_bot.portfolio, weather_bot.live_paper_runner
problem_type: logic_error
component: service_object
symptoms:
  - "`PaperBroker` opened fewer shares after fees, but a candidate could still report gross `size_usd / p_exec` shares."
  - "Portfolio scenario PnL could use more shares than the paper position actually held."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, fees, portfolio, size-shares, accounting]
---

# Paper Fees Must Flow Through Accounting

## 1. What The Problem Was

The entry filter modeled the official weather taker fee, but `PaperBroker`
opened and closed paper positions at gross prices. The dashboard and new-entry
bankroll calculation also valued open positions before the exit fee.

For example, a `$10` YES entry at `0.50` bought `20` paper shares even though
the modeled entry fee was `$0.25`. A close at `0.60` then added the full `$12`
gross proceeds instead of subtracting the exit fee.

The later drift was subtler: `PaperBroker` had started buying fewer shares, but
`EdgeResult.size_shares` could still mean gross `size_usd / p_exec` shares. The
portfolio scenario table then used the larger number, so the "if this bucket
wins" payoff could be higher than the broker's actual paper position.

## 2. Why It Was A Problem

The strategy looked more profitable in paper trading than its own entry filter
claimed. This is dangerous because paper results guide later strategy choices.
An optimistic simulator can make a weak strategy look ready for promotion.

`size_usd` is the money budget. `size_shares` is the actual number of shares the
bot owns after paying entry fees. If those two fields disagree about fees, the
bot is comparing a real cost against an imaginary payout.

## 3. How It Was Fixed

`size_usd` now means the all-in paper-entry budget, including the entry fee.
The broker buys fewer shares so price plus fee fits inside that budget.

Normal closes and partial closes add after-fee proceeds to paper cash.
`available_entry_bankroll()` and the dashboard use the same conservative
after-exit-fee liquidation value. Settlement payouts stay binary `0` or `1`,
where the official fee curve is zero.

The entry-share formula is now shared through `fee_adjusted_entry_shares()`:

```text
actual_shares = size_usd / (p_exec + fee_per_share)
```

`live_paper_runner.evaluate_market()` puts that actual share count into
`EdgeResult.size_shares`, and `PaperBroker.open_position()` uses the same helper
when it records the paper position. Portfolio scenario PnL therefore uses the
same held quantity that the broker actually opens.

## 4. What To Check Next Time

- Test a full round trip with a non-zero fee rate.
- Check entry shares, cash after open, cash after close, and realized PnL.
- Check that `EdgeResult.size_shares` is lower than `size_usd / p_exec` when the
  fee rate is non-zero.
- Check that portfolio scenario PnL and broker-opened `PaperPosition.shares`
  use the same fee-adjusted share count.
- Check partial-close proceeds separately.
- Check that portfolio liquidation bankroll and dashboard market value use the
  same after-fee value.
- Treat risk-cap budgets as all-in amounts so adding fees does not silently
  exceed a cap.

## 5. What This Project Must Be Especially Careful About

This project uses paper results to decide whether a weather strategy deserves
more experiments. Fee-aware entry filtering is not enough by itself. Every
operator-visible and strategy-visible money path must use the same accounting
contract.

Official reference:
https://docs.polymarket.com/trading/fees
