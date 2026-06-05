---
title: Keep inactive and closed markets out of new paper entries
date: 2026-06-06
category: logic-errors
module: weather market discovery
problem_type: logic_error
component: service_object
symptoms:
  - "Weather category slug discovery could return inactive or closed markets as new paper-entry candidates."
  - "String API values such as false could be interpreted as true by Python bool conversion."
  - "Closed markets needed for settlement could be confused with markets eligible for new entry."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [polymarket, discovery, paper-trading, settlement, boolean-parsing, fail-closed]
---

# Keep inactive and closed markets out of new paper entries

## Problem

Polymarket market discovery has two different jobs that must not be mixed:
finding new paper-entry candidates and fetching old markets to settle already
held paper positions.

An inactive or closed market is not a real new-entry opportunity. It can still
be useful as a final answer sheet for an existing paper position, but letting it
enter the buy-candidate path makes paper results measure a non-buyable market.

## Symptoms

- `/events` discovery requested `active=true` and `closed=false`, but the
  weather category page path fetched event slugs and parsed every weather market
  inside `/events/slug/...`.
- `_parse_weather_event()` did not reject rows where `active=False` or
  `closed=True`.
- `_parse_market()` used `bool(row.get("closed", False))`, so a string such as
  `"false"` became `True` because non-empty strings are truthy in Python.
- `_open_position_if_needed()` did not have a final guard against inactive or
  closed `RawMarket` values.

## What Didn't Work

- Relying only on `/events` query parameters was incomplete because category
  slug discovery bypassed that API filter.
- Relying on Python's generic `bool(...)` conversion was unsafe for external API
  data. It answers "is there any value?" rather than "does this value mean
  false?"
- Blocking all closed markets globally would have broken settlement, because
  closed markets are needed to determine whether an existing paper position won
  or lost.

## Solution

Add an explicit API boolean parser and use it at the market boundary:

```python
def parse_api_bool(value: Any, *, default: bool) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_API_BOOL_VALUES:
            return True
        if normalized in FALSE_API_BOOL_VALUES:
            return False
    return None
```

Then make discovery and final entry agree on the same rule:

```python
def _is_new_entry_candidate(row: dict[str, Any]) -> bool:
    active = parse_api_bool(row.get("active"), default=True)
    closed = parse_api_bool(row.get("closed"), default=False)
    return active is True and closed is False
```

`_parse_weather_event()` applies this before returning markets from both
paginated discovery and category slug expansion. `_open_position_if_needed()`
also returns early when `not market.active or market.closed`, so even a bad
upstream caller cannot open a new paper position on a closed or inactive market.

Settlement remains separate. `get_market()` can still parse closed markets, and
`maybe_settle_resolved_positions()` can still use closed markets to close
already-held paper positions when a clear YES/NO winner exists.

## Why This Works

Think of `active` as "can this worksheet still be solved?" and `closed` as "has
this worksheet already been graded?" A new paper trade needs a worksheet that is
still open. Settlement needs the graded answer sheet for a worksheet the bot
already submitted.

The fix preserves that split:

- New-entry discovery accepts only markets proven active and not closed.
- Unknown explicit boolean values fail closed instead of becoming buyable.
- Closed markets are still allowed in the settlement path, where they serve as
  outcome evidence rather than entry candidates.

## Prevention

- When an external API field is boolean-like, parse explicit true/false aliases
  instead of using `bool(value)`.
- Add tests for both real booleans and string booleans whenever API rows can
  contain values such as `"true"`, `"false"`, `"1"`, or `"0"`.
- Keep candidate filters at the earliest discovery boundary and repeat critical
  guards at the final action boundary.
- Test settlement separately from entry filtering so a closed-market entry fix
  does not accidentally remove closed-market settlement evidence.

## Related Issues

- [Filter out-of-scope markets before forecasting](../best-practices/filter-out-of-scope-markets-before-forecasting.md)
- [Separate SKIP diagnostics from trades and settle exact closed markets](dashboard-trades-and-closed-market-settlement.md)
- [Reject unknown boolean environment values](boolean-env-values-must-be-explicit.md)
