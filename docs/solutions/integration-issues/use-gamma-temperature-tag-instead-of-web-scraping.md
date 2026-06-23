---
title: Use Gamma temperature tags instead of scraping Polymarket pages
date: 2026-06-23
category: integration-issues
module: weather_bot.polymarket_client
problem_type: integration_issue
component: service_object
symptoms:
  - "The runner reported zero temperature markets while current markets were visible on Polymarket."
  - "The station observation timestamp stopped advancing because no markets reached evaluation."
  - "Python requests received a Cloudflare challenge while curl and Gamma API calls succeeded."
root_cause: wrong_api
resolution_type: code_fix
severity: high
tags: [polymarket, gamma-api, cloudflare, market-discovery, temperature]
---

# Use Gamma temperature tags instead of scraping Polymarket pages

## Problem

The paper bot treated Polymarket's human-facing temperature page as its main
market index. Cloudflare challenged Python `requests`, the error was ignored,
and the bounded generic Gamma scan did not reach the temperature events.

## Symptoms

- The runner stayed alive but reported zero markets, tokens, and cities.
- Station calls stopped, leaving an old observation timestamp on the dashboard.
- The web response was `403` with `cf-mitigated: challenge` and a
  `Just a moment...` page.

## What Didn't Work

- Changing only the User-Agent did not pass Cloudflare's JavaScript challenge.
- Restarting the service repeated the same discovery path.
- Scanning only the first generic Gamma pages depended on global event ordering.

## Solution

Query the official Gamma `/events` endpoint with the `Daily Temperature` tag
ID `103040`, retaining the existing active/closed filters and bounded
pagination. Use category-page scraping only as a zero-result fallback.

If a Gamma page fails before any supported market is found, propagate the
error instead of converting it into an empty market universe. Partial results
remain usable only after at least one supported market was already found.

## Why This Works

Gamma is the machine-facing market data API and does not require Cloudflare's
browser challenge. The tag narrows the search to the intended market family,
so discovery no longer depends on where temperature events happen to appear in
the global event list.

## Prevention

- Test that successful tag discovery never calls the human-facing web page.
- Assert the exact tag, active/closed filters, limit, and offset parameters.
- Test that a later Gamma failure raises when no supported market was found.
- Verify production with nonzero markets, fresh station calls, and a streaming
  runner status after every discovery change.

## Related Issues

- [Bound category slug discovery before stream startup](../performance-issues/bound-category-slug-discovery-before-stream-startup.md)
