---
title: Exit triggers must use after-fee liquidation PnL
date: 2026-06-06
category: logic-errors
module: weather_bot.exit_policy, weather_bot.paper
problem_type: logic_error
component: service_object
symptoms:
  - "A token price could rise enough to satisfy `MIN_PROFIT_PCT` while the paper account still lost money after entry and exit fees."
  - "Edge-faded exits compared raw token-price movement instead of the after-fee liquidation loss limit."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, exit-policy, fees, pnl, liquidation]
---

# Exit Triggers Must Use After-Fee Liquidation PnL

## 1. What The Problem Was

`assess_exit()` used raw token-price movement for profit and edge-fade checks:

```text
raw_pnl = (mark_price - entry_price) / entry_price
```

That can say "profit" when the current executable sell value is still below
the paper position's all-in `cost_usd` after exit fees.

For example, a `0.50 -> 0.515` move is a raw 3% token-price gain. With the
weather taker fee on both entry and exit, the paper account can still be down
after liquidation. The old exit policy could close that as `take_profit`.

## 2. Why It Was A Problem

The bot is trying to validate whether weather-market predictions can produce
repeatable paper profit. A price-only exit rule is not the same as a money
exit rule.

`paper_state.json` is the account book. It stores the actual cost basis and
cash changes. If exit triggers ignore that book and use only raw token prices,
the strategy can look disciplined while locking in after-fee losses.

## 3. How It Was Fixed

`assess_exit()` now calculates liquidation PnL from the same accounting contract
used by `PaperBroker`:

```text
exit_fee = polymarket_taker_fee_usdc(shares, mark_price, fee_rate)
net_pnl_usd = shares * mark_price - exit_fee - cost_usd
net_pnl_pct = net_pnl_usd / cost_usd
```

The model-target take-profit trigger now requires:

```text
mark_price >= target_exit_price
net_pnl_pct >= MIN_PROFIT_PCT
```

The overheated take-profit trigger requires positive after-fee liquidation PnL,
and edge-faded exits compare the after-fee liquidation loss against
`EDGE_FADE_MAX_LOSS_PCT`.

Exit reasons now include both `net_pnl` and `raw_pnl` so an operator can see
the difference between price movement and account-book profit.

## 4. What To Check Next Time To Prevent The Same Mistake

- For every profit or loss exit, ask whether the rule is judging price movement
  or account-book money.
- Test non-zero fee rates, not only `WEATHER_TAKER_FEE_RATE=0`.
- Include a case where raw token PnL is positive but after-fee liquidation PnL
  is below the configured profit threshold.
- Include a case where edge fade would be allowed by raw PnL but blocked by
  after-fee loss.
- Treat invalid position cost or shares as fail-closed; do not guess a PnL.

## 5. What This Project Must Be Especially Careful About

Weather markets can have small edges and thin books. A few fee-blind exits can
turn a seemingly profitable paper strategy into a losing one.

The safe rule is:

```text
entry uses all-in cost
exit uses after-fee proceeds
profit triggers compare those two money values
```

## Related Issues

- [Paper fees must flow through accounting](paper-fees-must-flow-through-accounting.md)
- [Invalid edge sentinel must not trigger edge-faded exits](invalid-edge-sentinel-not-exit-signal.md)
- [Final Order Depth Must Follow Entry Sizing](final-order-depth-must-follow-sizing.md)
