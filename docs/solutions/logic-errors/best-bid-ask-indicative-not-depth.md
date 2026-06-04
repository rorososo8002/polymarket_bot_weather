---
title: Best-bid-ask messages are not executable order-book depth
date: 2026-06-03
last_updated: 2026-06-04
category: logic-errors
module: weather_bot.realtime_orderbook, weather_bot.edge, weather_bot.models, weather_bot.live_paper_runner, weather_bot.paper
problem_type: logic_error
component: background_job
symptoms:
  - "`best_bid_ask` messages could create a bid or ask level with size 1.0."
  - "A quote-only stream update could look like executable paper-trading liquidity."
  - "`OrderBook.best_bid` and `OrderBook.best_ask` could still return indicative prices before executable depth."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, orderbook, websocket, liquidity, vwap, fail-closed]
---

# Best-bid-ask messages are not executable order-book depth

## Problem
The realtime order-book cache treated Polymarket `best_bid_ask` stream messages
as if they proved one share of depth at the quoted best bid and best ask. A
best-price quote says where the top of the market is, but it does not prove how
many shares can actually be bought or sold there.

## Symptoms
- `best_bid_ask` alone created `OrderLevel(price, size=1.0)` entries.
- After a real snapshot, `best_bid_ask` could move the top level and reuse the
  old level size at a new price.
- Very small executable checks could pass against a quote-only update even
  though no snapshot or level-size update confirmed available shares.

## What Didn't Work
- Reusing the previous top-level size for a new best price looked convenient,
  but it turned a price reference into invented liquidity.
- Treating `book.best_bid` and `book.best_ask` as both reference prices and
  executable level prices blurred two different questions: "What is the quoted
  top price?" and "How much can the bot actually trade?"
- Fixing the stream cache was not enough while shared best-price properties
  still preferred indicative quotes. That left liquidity filters, `YES+NO` ask
  checks, spread audits, and position marks vulnerable to the same confusion.

## Solution
Keep indicative best bid/ask prices separate from executable depth:

```python
OrderBook(
    token_id=token_id,
    bids=current.bids,
    asks=current.asks,
    indicative_best_bid=bid,
    indicative_best_ask=ask,
)
```

Then calculate executable prices only from positive-size `bids` and `asks`
that came from `book` snapshots or `price_change` updates:

```python
def _best_executable_price(levels: list[OrderLevel]) -> float | None:
    for level in levels:
        if level.size > 0:
            return level.price
    return None
```

The `OrderBook` model should also make the executable/reference split visible
in its property names:

```python
@property
def best_bid(self) -> float | None:
    for level in self.bids:
        if level.size > 0:
            return level.price
    return None

@property
def reference_best_bid(self) -> float | None:
    return self.indicative_best_bid if self.indicative_best_bid is not None else self.best_bid
```

Held-position marking must also fail closed. If there is no executable bid
depth, keep the previous mark and log `HOLD_NO_LIQUIDITY` rather than marking
PnL from an indicative `best_bid_ask` quote.

Regression tests now cover both important cases:

- `best_bid_ask` alone preserves reference prices but leaves executable depth
  empty.
- `best_bid_ask` after a snapshot updates reference prices without moving the
  snapshot-confirmed bid/ask levels.
- Better indicative prices do not rescue abnormal executable `YES+NO` ask sums
  or wide executable spreads.
- A quote-only held-position book does not update mark price or unrealized PnL.

## Why This Works
Paper trading should mimic what a real order could do without sending real
orders. A `book` snapshot is like a full photo of the current market depth, and
`price_change` is like a signed update saying a specific price level now has a
specific size. Those messages can support executable VWAP calculations.

`best_bid_ask` is only a top-price hint. Keeping it outside the level lists
lets dashboards and sanity checks still see the current quoted best price, while
entry sizing, liquidity filters, and exits fail closed unless actual depth is
known.

## Prevention
- When adding new market-data message types, decide whether each field proves
  price, size, or both.
- Tests should assert both the reference price and the executable level list.
- Shared model properties must not hide whether a value is executable or
  indicative. Use executable-only names for trading math and explicit
  reference/indicative names for display.
- `executable_buy_price()` and `executable_sell_price()` should use confirmed
  positive-size levels.
- In trading code, a missing or quote-only order book must mean skip, not guess.

## Related Issues
- [VWAP Slippage Edge Contract](./vwap-slippage-edge-contract-2026-05-25.md)
- [Separate forecast freshness from WebSocket stream health](./explicit-forecast-and-websocket-health.md)
- [Realtime orderbook requirements are not polling requirements](../workflow-issues/realtime-orderbook-requirement-not-polling-2026-05-26.md)
