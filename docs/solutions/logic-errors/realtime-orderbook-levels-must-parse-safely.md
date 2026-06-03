---
title: Realtime order-book levels must parse finite prices and sizes
date: 2026-06-03
category: logic-errors
module: weather_bot.realtime_orderbook
problem_type: logic_error
component: background_job
symptoms:
  - "`book` messages with price=`bad` could raise `ValueError`."
  - "`book` messages with size=`bad` could raise `ValueError`."
  - "`price_change` messages with malformed price or size could stop order-book updates."
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags: [paper-trading, orderbook, websocket, malformed-data, fail-closed, vwap]
---

# Realtime order-book levels must parse finite prices and sizes

## Problem
Polymarket WebSocket `book` snapshots and `price_change` updates are external
market data. The parser used `float(...)` directly on incoming `price` and
`size` fields, so one broken value such as `"bad"` could raise `ValueError`.

## Why This Was A Problem
The order book is this bot's executable price calculator. A `price` is the
price level where the bot could buy or sell, and a `size` is how many shares are
available at that price. If either number is broken, the bot must not guess.

Guessing would contaminate paper trading in two ways:

- It could create a fake entry or exit price.
- It could make the paper strategy look better or worse than the real market
  evidence supports.

## How It Was Fixed
Parse numeric fields through one guarded path:

```python
def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number
```

Then accept only valid executable order-book values:

- `price` must be finite and inside the valid token-price range.
- Snapshot `size` must be finite and positive.
- `price_change` `size` may be zero, because zero means remove that level.
- Negative, NaN, infinite, and non-numeric values are ignored.
- If a whole snapshot has the wrong shape, it does not replace the current book.

The fix stays inside `src/weather_bot/realtime_orderbook.py`. It does not touch
wallets, private keys, live orders, or real-money execution.

## What To Check Next Time
- Add a regression test with malformed `price`.
- Add a regression test with malformed `size`.
- Check that bad levels are ignored but good levels in the same message remain.
- Check that a malformed whole snapshot returns no update and does not overwrite
  the existing executable book.
- Run the focused realtime order-book tests before the full test suite.

## What This Project Must Be Especially Careful About
For this weather bot, paper trading is a measurement experiment. `paper_state`
is the account book, and the realtime order book is the executable-price input
to that book. Broken external data must therefore mean SKIP or ignored level,
not guessed liquidity.

This is the same family of rule as:

- Do not treat `best_bid_ask` as executable depth.
- Do not use stale WebSocket depth for entries or exits.
- Do not let malformed market data create a paper trade.

## Symptoms
- `ValueError: could not convert string to float: 'bad'`
- Non-finite values such as `nan` or `inf` could enter the cache.
- Negative prices or sizes could distort executable VWAP and liquidity checks.

## Related Issues
- [Best-bid-ask messages are not executable order-book depth](./best-bid-ask-indicative-not-depth.md)
- [Separate forecast freshness from WebSocket stream health](./explicit-forecast-and-websocket-health.md)
- [VWAP Slippage Edge Contract](./vwap-slippage-edge-contract-2026-05-25.md)
