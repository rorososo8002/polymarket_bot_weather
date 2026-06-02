# Paper Fees Must Flow Through Accounting

## 1. What The Problem Was

The entry filter modeled the official weather taker fee, but `PaperBroker`
opened and closed paper positions at gross prices. The dashboard and new-entry
bankroll calculation also valued open positions before the exit fee.

For example, a `$10` YES entry at `0.50` bought `20` paper shares even though
the modeled entry fee was `$0.25`. A close at `0.60` then added the full `$12`
gross proceeds instead of subtracting the exit fee.

## 2. Why It Was A Problem

The strategy looked more profitable in paper trading than its own entry filter
claimed. This is dangerous because paper results guide later strategy choices.
An optimistic simulator can make a weak strategy look ready for promotion.

## 3. How It Was Fixed

`size_usd` now means the all-in paper-entry budget, including the entry fee.
The broker buys fewer shares so price plus fee fits inside that budget.

Normal closes and partial closes add after-fee proceeds to paper cash.
`available_entry_bankroll()` and the dashboard use the same conservative
after-exit-fee liquidation value. Settlement payouts stay binary `0` or `1`,
where the official fee curve is zero.

## 4. What To Check Next Time

- Test a full round trip with a non-zero fee rate.
- Check entry shares, cash after open, cash after close, and realized PnL.
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
