---
title: Dashboard links must use Polymarket event slugs
date: 2026-06-03
category: logic-errors
module: weather_bot.dashboard, weather_bot.paper
problem_type: logic_error
component: service_object
severity: medium
symptoms:
  - "Open Positions cards linked to Polymarket 404 pages."
  - "Weather condition suffixes such as -25corbelow and -30corhigher were appended to event URLs."
root_cause: wrong_api
resolution_type: code_fix
tags: [dashboard, polymarket, event-slug, market-slug, open-positions]
---

# Dashboard links must use Polymarket event slugs

## Problem

Open-position cards on the dashboard built clickable Polymarket URLs from the
market slug. For weather markets, that can produce a URL such as:

```text
https://polymarket.com/ko/event/highest-temperature-in-beijing-on-june-4-2026-25corbelow
```

That URL is wrong because Polymarket's event page lives at the event slug:

```text
https://polymarket.com/ko/event/highest-temperature-in-beijing-on-june-4-2026
```

For a beginner: `event_slug` is the "event hall address." The condition-specific
market slug is more like a "seat or option label inside that event hall." If the
dashboard puts the seat label into the building address, the browser goes to a
place that does not exist.

## Symptoms

- Clicking Beijing or Toronto open-position cards opened a Polymarket 404 page.
- The broken URLs ended with condition text such as `-25corbelow` or
  `-30corhigher`.
- The base event URL without that condition suffix loaded correctly.

## What Didn't Work

- Treating `slug` as a universal Polymarket page slug was not enough. In this
  project, a position's `slug` can describe the specific binary market, while
  the public event page needs the parent event slug.
- A display-only fix in JavaScript would have been brittle. Old and new paper
  positions both need a stable URL contract in the Python payload.

## Solution

Persist the parent Polymarket event slug when a paper position opens:

```python
"event_slug": market.event_slug,
```

Preserve that metadata when live paper positions are hydrated back into market
objects:

```python
event_slug=pos.metadata.get("event_slug"),
```

Then build dashboard links from `event_slug` first. If old runtime state has
only a condition-specific market slug, strip the terminal weather-condition
suffix before constructing the event URL.

The dashboard test should cover both paths:

- A normal `event_slug` should link directly to
  `https://polymarket.com/ko/event/{event_slug}`.
- A legacy weather slug ending in a condition suffix should link to the base
  event slug, not the condition-specific slug.

## Why This Works

Polymarket separates the parent event page from the individual outcome markets.
The dashboard is a navigation surface, so it should send the operator to the
event page that actually exists. Storing `event_slug` removes guesswork for new
positions. The suffix-stripping fallback keeps old `paper_state.json` positions
usable without editing runtime history.

`paper_state.json` is the paper account ledger: it remembers cash, open
positions, average entry prices, and position metadata. Because it is the basis
for performance verification, the dashboard must read it carefully and avoid
guessing when there is an explicit metadata field available.

## Prevention

- When a UI link points to an external service, test the exact URL shape, not
  just that an anchor tag exists.
- Keep `event_slug` and `slug` separate in tests. `event_slug` means the parent
  Polymarket event page; `slug` may mean a specific market or condition.
- Add a legacy-state test whenever runtime metadata changes, because old
  `paper_state.json` files may not have the new field yet.
- For this project, a wrong dashboard link is not just cosmetic. It slows down
  manual verification of open paper positions and can make a healthy position
  look suspicious because the operator lands on a 404 page.

## Related Issues

- [Separate SKIP diagnostics from trades and settle exact closed markets](./dashboard-trades-and-closed-market-settlement.md)
- [Subscribe open position tokens even after market discovery rolls forward](./open-position-stream-subscription-drift.md)
