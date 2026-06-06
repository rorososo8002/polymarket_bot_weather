---
title: Price-change deltas require a prior book snapshot
date: 2026-06-07
category: logic-errors
module: weather_bot.realtime_orderbook
problem_type: logic_error
component: background_job
symptoms:
  - "`price_change` could create an executable order book for a token before any `book` snapshot."
  - "Token-level WebSocket freshness could be refreshed from an incomplete delta-only book."
  - "Paper entry or exit checks could later treat partial market depth as complete executable evidence."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, websocket, orderbook, price-change, book-snapshot, liquidity, fail-closed]
---

# Price-change deltas require a prior book snapshot

## 1. What The Problem Was

The realtime order-book cache accepted a Polymarket `price_change` message even
when that token had never received a `book` snapshot in the current stream
cache. That allowed one changed bid or ask level to create a new executable
`OrderBook`.

A `book` snapshot is the full photo of the current order book. A `price_change`
is only a correction note written on top of that photo. If there is no photo
yet, the correction note is not enough evidence to know the full executable
market depth.

## 2. Why It Was A Problem

Paper trading should measure what the bot could have bought or sold from real
depth without sending real orders. If a delta-only book is treated as complete,
the bot may think liquidity exists, mark WebSocket depth as fresh, or evaluate
an entry/exit from partial evidence.

That breaks the project's fail-closed rule. Missing or incomplete market data
must mean skip, not guess.

## 3. How It Was Fixed

`OrderBookStreamCache` now remembers which token IDs have received an initial
`book` snapshot:

```python
self._snapshot_token_ids: set[str] = set()
```

When `_apply_book()` stores a valid snapshot, it marks the token as snapshot
backed. When `_apply_price_change()` sees a token without that mark, it ignores
the delta and returns no executable update.

A regression test covers the rule:

```python
def test_price_change_without_prior_book_snapshot_does_not_create_executable_depth():
    ...
    assert updated == set()
    with pytest.raises(KeyError, match="no websocket orderbook snapshot"):
        cache.get_order_book("yes")
```

## 4. What To Check Next Time

- Test stream message ordering, not only message parsing.
- For every delta-style market-data message, ask what full state it depends on.
- Assert that a pre-snapshot delta does not create a book, refresh executable
  freshness, or enqueue paper strategy evaluation.
- Keep `best_bid_ask`, `last_trade_price`, and pre-snapshot `price_change`
  outside executable level lists.

## 5. What This Project Must Be Careful About

This weather bot is paper-only, but paper results are the evidence for whether
the strategy deserves more work. Incomplete order-book evidence can make a
strategy look safer or more profitable than it is.

For this project, executable liquidity must come from a full `book` snapshot
plus later valid deltas. A delta cannot be the starting point.

## Related Issues

- [Best-bid-ask messages are not executable order-book depth](./best-bid-ask-indicative-not-depth.md)
- [Held-position exits need token-level WebSocket freshness](./token-level-websocket-freshness-for-held-exits.md)
- [Order-book levels must parse finite prices and sizes](./realtime-orderbook-levels-must-parse-safely.md)
