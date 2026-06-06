---
title: Blocked exits must preserve the fired exit trigger
date: 2026-06-07
category: logic-errors
module: weather_bot.paper
problem_type: logic_error
component: service_object
symptoms:
  - "`paper_trades.csv` recorded `HOLD_NO_LIQUIDITY` without saying a probability stop had fired."
  - "Partial close rows preserved the human phrase but not the stable `exit_trigger` name."
  - "Stale WebSocket holds paused safely but did not keep the latest model exit signal in position metadata."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, exits, liquidity, websocket, probability-stop]
---

# Blocked exits must preserve the fired exit trigger

## Problem
`paper_trades.csv` is the paper execution ledger. It should say both what the
bot actually did and why it wanted to do it.

The bug was that `maybe_close_positions()` returned early when a held token had
no executable bid depth. That kept the safe action, `HOLD_NO_LIQUIDITY`, but it
skipped the exit assessment that would have recorded `probability_stop`,
`take_profit`, or `edge_faded`.

## Symptoms
- A position with fresh model evidence below its probability stop still logged
  a generic `HOLD_NO_LIQUIDITY` reason.
- Later analysis could not distinguish "no sell signal yet" from "sell signal
  fired, but no executable buyer existed."
- Partial close rows were executable and safe, but the reason did not include a
  stable `exit_trigger=<trigger>` field for later aggregation.
- WebSocket-stale holds correctly avoided fake prices, but the position state
  did not show the latest model/nowcast exit signal when one existed.

## What Didn't Work
- Treating `HOLD_NO_LIQUIDITY` as enough evidence was not enough. It explains
  the execution blocker, not the strategy signal.
- Using indicative `best_bid_ask` quotes would have made the ledger look more
  complete, but it would invent executable liquidity. That violates the project
  rule that only `book` and `price_change` depth can support paper fills.

## Solution
Keep the execution blocker and the exit assessment separate.

The safe action stays unchanged:

```text
no executable bid depth -> HOLD_NO_LIQUIDITY
stale executable WebSocket depth -> HOLD_STREAM_UNHEALTHY
```

But before writing the blocked row, run the exit assessment from the latest
model evidence and preserve the trigger when `should_close=True`:

```text
exit signal fired but no executable liquidity;
exit_trigger=probability_stop;
assessment=probability stop: ...;
no executable bid depth; indicative best_bid_ask ignored
```

Partial closes also include the stable trigger in their reason:

```text
PARTIAL(...): exit_trigger=probability_stop; probability stop: ...
```

For stale WebSocket depth, the broker still refuses to mark or sell from fake
prices. It stores the latest exit signal in position metadata instead:

```text
last_exit_signal_trigger=probability_stop
last_exit_signal_reason=probability stop: ...
last_exit_signal_blocker=websocket order book stream unhealthy: ...
```

## Why This Works
The exit assessment answers, "Should this position be closed according to the
strategy?" The executable bid book answers, "Can the position actually be sold
right now?"

Those are different questions. A correct paper bot must not turn a close signal
into a fake fill, but it also must not hide the close signal behind a generic
hold row. Preserving both facts keeps the paper ledger honest for future
performance analysis.

## Prevention
- When adding a new exit trigger, make sure blocked close paths still include
  a stable `exit_trigger=<trigger>` label.
- Keep `HOLD_NO_LIQUIDITY` and `HOLD_STREAM_UNHEALTHY` as non-executed actions;
  do not replace them with `CLOSE` unless executable bid depth can absorb the
  actual shares being sold.
- Add regression tests for three cases: no executable bid with a fired exit
  signal, partial close with the original trigger preserved, and WebSocket
  stale with the latest model signal stored in metadata.
- Never use `best_bid_ask` as executable depth. It is a reference quote, not a
  buyer with confirmed size.

## Related Issues
- [Held-position exit evidence must not depend on entry bankroll](../best-practices/held-position-exit-evidence-must-not-depend-on-entry-bankroll.md)
- [Held-position exits need token-level WebSocket freshness](./token-level-websocket-freshness-for-held-exits.md)
- [Best-bid-ask messages are not executable order-book depth](./best-bid-ask-indicative-not-depth.md)
