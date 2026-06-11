---
title: Bound category slug discovery before stream startup
date: 2026-06-11
category: performance-issues
module: weather_bot.polymarket_client
problem_type: performance_issue
component: service_object
symptoms:
  - "Polymarket weather category pages exposed hundreds of event slugs."
  - "Discovery could fetch every `/events/slug/...` detail page before the WebSocket stream started."
  - "The paper bot could remain in the discovering phase for minutes even when Gamma API health was fine."
root_cause: unbounded_external_iteration
resolution_type: code_fix
severity: high
tags: [polymarket, discovery, websocket, startup, paper-trading, performance]
---

# Bound Category Slug Discovery Before Stream Startup

## Problem

Weather discovery has two intake paths:

- paginated Gamma `/events` API discovery
- Polymarket weather category pages that list event slugs

The category pages are useful because they can reveal weather events that the
plain API query misses. But a category page is a wide index, not a small API
page. It can contain hundreds of event slugs.

If the bot fetches every slug detail page before starting the CLOB WebSocket
stream, the realtime strategy starts late. That is dangerous because order-book
data is the price camera. A bot that spends minutes discovering markets before
subscribing is already looking at stale opportunities.

## Symptoms

- The VPS runner stayed in `discovering markets for websocket stream` for more
  than two minutes.
- Direct Gamma API health checks were fast.
- Direct Polymarket category page fetches were fast but returned very large
  HTML documents with hundreds of event links.
- A local smoke call to `discover_weather_markets(max_pages=1, page_size=5)`
  timed out because category-slug detail fetches ignored that small page
  budget.

## What Did Not Work

- Treating the 40 trading-ready city count as a discovery stop condition is
  wrong. One city can have many valid temperature markets, and discovery should
  not stop simply because it has seen enough city names.
- Removing category-page discovery entirely is also wrong. It can find valid
  events missed by the paginated API query.
- Letting category discovery fetch every slug is too expensive for a realtime
  bot.

## Solution

Keep category-page discovery, but bound its expensive detail fetches.

`discover_weather_markets(max_pages, page_size)` now computes a category slug
budget from the caller's explicit API page budget:

```text
category_slug_limit = min(80, max_pages * page_size)
```

Then `_discover_weather_markets_from_category_pages()` parses and de-duplicates
category slugs, but fetches details only up to that bounded limit.

The important rule is:

```text
category page parsing may be broad
slug detail fetching must be bounded
WebSocket startup must not wait behind an unbounded external loop
```

## Prevention

- Tests must prove `max_pages=1, page_size=5` limits category slug detail
  fetches to 5.
- Do not use station count as a market discovery limit.
- Keep a hard cap for category slug detail fetches even when production
  `DISCOVERY_MAX_PAGES` and `DISCOVERY_PAGE_SIZE` are large.
- If a future change increases the cap, verify VPS startup phase duration and
  WebSocket subscription timing, not just local unit tests.

## Related Issues

- [Decouple WebSocket receiving from strategy evaluation](./decouple-websocket-receiver-from-strategy-evaluation.md)
- [Keep inactive and closed markets out of new paper entries](../logic-errors/active-closed-markets-are-not-entry-candidates.md)
- [Discover weather events before binary markets](../logic-errors/discover-weather-events-before-binary-markets.md)
