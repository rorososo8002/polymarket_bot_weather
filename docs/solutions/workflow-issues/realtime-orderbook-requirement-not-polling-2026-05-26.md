---
title: Realtime orderbook requirements are not polling requirements
date: 2026-05-26
category: workflow-issues
module: weather_bot.live_paper_runner
problem_type: workflow_issue
component: development_workflow
severity: high
applies_when:
  - "A trading requirement says realtime, live, stream, watch, or monitor continuously"
  - "A proposed implementation uses a short REST interval to approximate realtime behavior"
tags: [realtime, orderbook, websocket, trading, requirements]
---

# Realtime orderbook requirements are not polling requirements

## Context
The bot requirement was to protect Open-Meteo forecast calls while monitoring Polymarket order books in real time. A short REST interval was still described as order-book monitoring, which violated the requirement and confused the production docs.

## Guidance
Treat realtime trading data requirements as stream requirements unless the user explicitly accepts polling. For this bot, the default long-running path must use the Polymarket CLOB WebSocket market channel, maintain an in-memory order-book cache, and trigger evaluation from WebSocket updates.

Configuration examples and handoff docs should show only the realtime path:

```text
ORDERBOOK_STREAM_ENABLED=true
ORDERBOOK_STREAM_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
# market-discovery/WebSocket rebuild cycle, not forecast HTTP cadence
FORECAST_REFRESH_INTERVAL_SECONDS=7200
```

If someone tries to disable the stream in `run_forever()`, fail closed instead of silently falling back to a REST loop.

## Why This Matters
Trading behavior changes materially when prices are observed by event stream instead of interval sampling. A short interval can still miss price moves, create stale decisions, and mislead the next agent into thinking the requirement was satisfied.

## When to Apply
- The user separates slow data refresh from realtime market monitoring.
- The domain is order books, quotes, fills, or other time-sensitive market data.
- The docs or examples mention a short interval as a substitute for realtime.

## Examples
Before:

```text
Forecast calls are protected by cache/request pacing; order books use a short REST interval.
```

After:

```text
Forecast calls are protected by cache/request pacing; order books are monitored through WebSocket events.
```

## Related
- `docs/production-decisions.md`
- `docs/production-implementation-plan.md`
- `docs/production-progress.md`
