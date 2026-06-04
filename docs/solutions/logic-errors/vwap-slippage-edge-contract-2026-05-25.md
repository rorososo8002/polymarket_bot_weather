# VWAP Slippage Edge Contract

## 1. What Went Wrong

`executable_buy_price()` returns an ask-side VWAP, not just the best ask. VWAP
means volume-weighted average price: the average price the bot would actually
pay after consuming the available order-book depth for the intended size.

Because entry spread and entry slippage are already inside that VWAP, subtracting
entry slippage again from expected return double-counts the same cost.

The executable net-return work also needed to remove the fixed
`estimated_fee_per_share=0.02`. The Polymarket weather taker fee depends on
price, so a fixed estimate can overstate or understate cost depending on the
entry price.

## 2. Why It Mattered

- Thin trades such as `0.88 -> 0.92` can look profitable before realistic costs.
- Double-counting VWAP slippage can reject good candidates.
- A high entry price should not be banned automatically if conservative
  settlement-hold math still leaves enough return.

## 3. How It Was Fixed

The official weather fee curve was separated into testable functions. Paper mode
uses the official weather-category default fee rate of `0.05`, and USDC fees are
rounded to five decimal places according to the docs.

Entry evaluation now separates two cost paths:

1. Normal exit path: keep entry VWAP as the executed price, then subtract
   conservative future exit spread, observed slippage, and exit taker fee.
2. Settlement-hold path: use conservative settlement EV after model and
   settlement uncertainty. Because this path does not exit through the order
   book, it does not include exit spread, exit slippage, or taker exit fee.

The existing `net_edge` condition remains, with an additional expected net-return
filter that defaults to `6%`.

## 4. What To Check Next Time

- Before adding a cost item, confirm whether it is already included in VWAP.
- Keep entry costs separate from future exit costs.
- Test the official fee function, category rate, and rounding rule.
- Keep regression tests for `0.88 -> 0.92` rejection and high-price settlement
  candidates that still pass.
- For any future live project, fetch market-specific CLOB fee parameters instead
  of relying only on category defaults.

## 5. Project-Specific Caution

This repository is still a paper bot. The change made paper entry filtering more
realistic; it did not add live orders. Keep expected gross return, cost, net
return, path, and rejection reason in the decision log so paper results can be
audited later.
