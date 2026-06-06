---
title: Separate forecast freshness from WebSocket stream health
date: 2026-06-01
last_updated: 2026-06-03
category: logic-errors
module: weather_bot.probability, weather_bot.realtime_orderbook, weather_bot.live_paper_runner, weather_bot.portfolio, weather_bot.paper, weather_bot.dashboard
problem_type: logic_error
component: background_job
symptoms:
  - "The dashboard could keep loading while an expired Open-Meteo response remained in memory."
  - "The main service could remain active after the WebSocket receiver thread stopped."
  - "A quote-only or trade-only stream message could make order-book freshness look newer than executable depth."
  - "A stale WebSocket stream could leave new-entry and held-position exit decisions without an operator-readable pause reason."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [forecast-cache, websocket, dashboard, stale-data, observability, paper-trading]
---

# Separate forecast freshness from WebSocket stream health

## Problem
A reachable dashboard and a live main process did not prove that the paper bot
still had fresh trading inputs. The in-memory forecast cache bypassed its TTL,
and the WebSocket receiver had no domain-health snapshot that could expose a
dead thread or an old order book.

## Symptoms
- Open-Meteo `forecast_cache.json` timestamps could be old while memory still
  returned the cached response.
- `systemd` could report the Python process as active after the background
  WebSocket receiver stopped.
- Any stream event could look like a useful order-book refresh even when bids
  and asks had not changed.

## What Didn't Work
- Checking only whether the dashboard HTML loaded proved only HTTP service
  liveness.
- Checking only whether the main Python process was active missed a dead
  background thread.
- Treating every WebSocket message as a book refresh confused trade-only and
  tick-size-only messages with order-book updates.
- Treating `best_bid_ask` as a freshness source after separating it from
  executable depth still let quote-only updates make the stream look healthier
  than the tradable bid/ask level data.

## Solution
Apply the same TTL check to memory and disk forecast entries. Record safe
forecast health metadata: the last real fetch attempt, last successful forecast
timestamp, recent failure reason, cache age, stale warning, and disk-save error.

Record WebSocket health separately: receiver-thread liveness, reconnect count,
last incoming message, last executable order-book depth update, stale-book age,
operator-readable status reason, and the recent stream error. Refresh
`paper_runner_status.json` every few seconds while the runner continues its
market-discovery/stream cycle.

Only `book` snapshots and `price_change` level updates refresh the executable
order-book timestamp. A `best_bid_ask` refresh proves only quote freshness; it
must not refresh the usable depth clock:

```python
return str(message.get("event_type") or "") in {
    "book",
    "price_change",
}
```

When WebSocket health is stale or dead, turn that into one shared pricing block
reason. New entries get `entry_bankroll=0` and an operator-readable SKIP. Held
positions keep their existing paper state, but exit evaluation pauses and logs
`HOLD_STREAM_UNHEALTHY` instead of closing from stale cached bids.

If the WebSocket receiver thread is dead and there are tokens to subscribe, the
runner may rebuild a new WebSocket stream. That is a WebSocket recovery path,
not a REST polling fallback.

## Why This Works
The dashboard now answers three different questions with three different
signals:

- Is the main paper service still reporting status?
- Is the forecast recent enough to reuse?
- Is the real-time receiver thread alive, and are order-book price updates
  still arriving?
- Is executable order-book depth fresh enough to support entry and exit
  decisions?

Separating those questions prevents one healthy layer from hiding a failure in
another layer.

## Prevention
- Test memory-cache expiry separately from disk-cache expiry.
- Treat cache persistence errors as visible diagnostics even when the current
  in-memory response remains usable.
- Track background-thread health explicitly; process liveness is not enough.
- Keep `last_message_at` separate from `last_book_at`.
- Add a regression test proving that a trade-only message does not refresh the
  usable order-book timestamp.
- Add a regression test proving that `best_bid_ask` updates reference prices
  but does not refresh executable-depth health.
- When WebSocket health is stale/dead, assert both sides of the safety rule:
  new entries SKIP and held-position exit evaluation pauses.

## Related Issues
- [Runner heartbeat and wall-clock cadence for long paper bot cycles](./runner-heartbeat-cadence-status-2026-05-25.md)
- [Best-bid-ask messages are not executable order-book depth](./best-bid-ask-indicative-not-depth.md)
- [Entry bankroll zero must skip before return estimate](./entry-bankroll-zero-must-skip-before-return-estimate.md)
- [Install runtime dependencies before starting a systemd service](../workflow-issues/install-runtime-dependencies-before-service-start-2026-05-26.md)
- [Realtime orderbook requirements are not polling requirements](../workflow-issues/realtime-orderbook-requirement-not-polling-2026-05-26.md)
