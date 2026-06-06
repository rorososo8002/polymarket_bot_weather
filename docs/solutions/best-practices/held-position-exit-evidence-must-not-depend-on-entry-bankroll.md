---
title: Held-position exit evidence must not depend on entry bankroll
date: 2026-06-07
category: docs/solutions/best-practices
module: live_paper_runner
problem_type: best_practice
component: service_object
severity: high
applies_when:
  - "Refreshing model probability or nowcast evidence for open paper positions"
  - "Changing entry-bankroll safety gates in `evaluate_market()`"
  - "Diagnosing open positions that show near-zero market prices but no probability-stop close"
tags: [paper-trading, held-exits, nowcast, entry-bankroll, probability-stop]
---

# Held-position exit evidence must not depend on entry bankroll

## Context

An open-position investigation found three near-zero `NO` positions whose latest
decision rows carried fresh forecast or nowcast notes, but the position exit
state still behaved as if the old entry probability was valid. The key clue was
the decision reason:

```text
entry_bankroll=$0.00; cannot price held token ... insufficient executable bid depth
```

`entry_bankroll` is the "new bet money" gate. It protects the bot from opening
more positions when the existing account cannot be valued safely. It is not the
same thing as exit evidence. Exit evidence is the current answer to: "Given what
we know now, should an already-held position be closed, partially closed, or
explicitly held because there is no executable bid?"

## Guidance

Keep these two paths separate:

- New entries should stay blocked when `available_entry_bankroll()` cannot
  safely price held positions.
- Held-position exits should still refresh weather probability, station nowcast,
  and per-side `latest_edges` whenever possible.
- If an exit signal fires but the book has no executable bid depth, log the real
  reason as "exit wanted, no liquidity" rather than falling back to a generic
  hold.

In practice, avoid making held-position exit evaluation depend on
`entry_bankroll > 0`. Either add an exit-only evaluation path or add an explicit
mode to `evaluate_market()` that still returns per-side edge evidence while
blocking only new entries.

Also be careful with exact temperature buckets. A market such as `30C` is not
automatically the same as `30C or higher`. For exact or range buckets, the
strategy needs a separate nowcast-risk rule for cases where the observed
settlement-station high/low is inside the held-against bucket late in the local
day, because a plain probability-stop rule may continue to treat the opposite
side as safe.

## Why This Matters

`paper_state.json` is the paper account book. If the open position's last model
fair price is stale, the account book can look calm while the market price has
already collapsed. That contaminates paper-performance evidence: the bot did
not simply lose a trade, it failed to record the risk state that should have
triggered a close attempt or a no-liquidity hold.

The same distinction protects honesty. If there is no executable bid, the paper
bot must not pretend it sold. But it should still record that the close signal
existed and that the blocker was liquidity or WebSocket freshness.

## When to Apply

- When editing `refresh_open_position_edges()` or realtime update evaluation.
- When `paper_decisions.csv` shows `entry_bankroll=$0.00` for markets that are
  already held.
- When `paper_trades.csv` repeatedly logs `HOLD_NO_LIQUIDITY` or
  `HOLD_STREAM_UNHEALTHY` while the weather evidence has materially changed.
- When a same-day station nowcast can invalidate or materially weaken the held
  side.

## Examples

Before:

```text
entry bankroll unsafe -> evaluate_market returns SKIP with no per-side edges
maybe_close_positions -> latest_edge missing -> falls back to entry probability
position remains a normal hold
```

After:

```text
entry bankroll unsafe -> new entries blocked
held exit refresh -> latest per-side probability/nowcast still recorded
maybe_close_positions -> probability stop or nowcast-risk exit can fire
no executable bid -> explicit no-liquidity exit blocker is logged
```

## Related

- [Entry bankroll zero must skip before return estimate](../logic-errors/entry-bankroll-zero-must-skip-before-return-estimate.md)
- [Probability stop replaces fixed price stop](../workflow-issues/probability-stop-replaces-fixed-price-stop-2026-05-26.md)
- [Realtime nowcast signals must refresh on the nowcast TTL](../logic-errors/realtime-nowcast-signal-refresh-must-follow-nowcast-ttl.md)
- [Token-level WebSocket freshness for held exits](../logic-errors/token-level-websocket-freshness-for-held-exits.md)
