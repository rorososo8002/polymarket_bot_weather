---
title: Held-position exits need token-level WebSocket freshness
date: 2026-06-06
category: logic-errors
module: weather_bot.realtime_orderbook, weather_bot.paper
problem_type: logic_error
component: background_job
symptoms:
  - "One token's fresh executable order-book update could make the overall WebSocket stream look healthy."
  - "A different held token could still have stale executable depth while close evaluation continued."
  - "`best_bid_ask` reference updates could not safely prove the held token had sellable depth."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, websocket, orderbook, token-id, freshness, fail-closed]
---

# Held-position exits need token-level WebSocket freshness

## Problem
The paper bot already separated executable order-book depth from indicative
`best_bid_ask` quotes, but WebSocket health still had one global executable
book clock. That global clock proves the market stream is alive, not that every
held `token_id` has a fresh sellable order book.

`token_id` is the Polymarket asset ID for one side of a market, such as YES or
NO. If a Seoul YES position is held, a fresh New York NO update should not make
the Seoul YES position look safe to mark or close.

## Symptoms
- Overall WebSocket health could be fresh because some token received a recent
  `book` snapshot or `price_change`.
- A held position's own token could be quiet for longer than the stale window.
- Exit evaluation could continue for that quiet token because only the global
  stream clock was checked.

## What Didn't Work
- Checking only `last_book_at` was useful for stream-level liveness, but it was
  too broad for held-position exit safety.
- Reading `best_bid_ask` as backup freshness was not valid because it carries
  reference prices, not executable size.
- Treating all stream tokens as one pool hid per-position risk when only one
  token in that pool was actually updating.

## Solution
Keep the existing global WebSocket health, and add a second clock keyed by
`token_id`:

```python
self._last_book_at_by_token[token_id] = now
```

Refresh that token clock only when a `book` snapshot or valid `price_change`
actually updates executable depth for that token. Do not refresh it from
`best_bid_ask`, `last_trade_price`, or tick-size-only messages.

Then, before `maybe_close_positions()` marks or closes a held position, ask the
stream for that position's own token health:

```python
token_health = stream.token_health_snapshot(pos.token_id)
token_block_reason = websocket_pricing_block_reason(token_health)
```

If the overall stream is stale or dead, all held exits still pause. If the
overall stream is fresh but one held token is stale, only that position logs
`HOLD_STREAM_UNHEALTHY`; positions with fresh executable depth can still be
marked or closed normally.

## Why This Works
The global WebSocket clock answers, "Is the telephone line receiving usable
order-book data at all?" The token-level clock answers, "Has the specific
product I hold received fresh executable depth?"

Those are different questions. A fresh update for token A says nothing about
whether token B has a current bid that can absorb a paper close. Separating the
clocks keeps paper PnL evidence closer to what a real sell path could have
done, without adding live trading or real orders.

## Prevention
- For held-position marking or exits, check both stream-level health and the
  position's own `token_id` executable-depth freshness.
- Keep `best_bid_ask` as display/reference data only; it must not refresh
  executable freshness.
- Add tests with two tokens: one fresh and one stale. The fresh position should
  remain eligible for normal evaluation while the stale position pauses.
- When adding new stream message types, explicitly decide whether they prove
  executable size for a specific token.

## Related Issues
- [Best-bid-ask messages are not executable order-book depth](./best-bid-ask-indicative-not-depth.md)
- [Separate forecast freshness from WebSocket stream health](./explicit-forecast-and-websocket-health.md)
- [Realtime orderbook levels must parse safely](./realtime-orderbook-levels-must-parse-safely.md)
