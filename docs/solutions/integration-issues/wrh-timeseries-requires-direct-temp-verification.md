---
title: NOAA WRH timeseries markets require direct Temp verification
date: 2026-07-08
category: integration-issues
module: weather_bot
problem_type: integration_issue
component: service_object
symptoms:
  - "A Moscow high exact NO was opened from AWC METAR evidence even though the market resolved from weather.gov/wrh/timeseries."
  - "Polymarket priced the same bucket as YES after entry, showing the bot had treated a non-settlement feed as settlement proof."
root_cause: logic_error
resolution_type: code_fix
severity: critical
tags: [wrh-timeseries, awc-metar, settlement-source, lock-only-no]
---

# NOAA WRH timeseries markets require direct Temp verification

## Problem

The bot treated an AWC/METAR station observation as enough proof for a market
whose rule text resolved from NOAA WRH timeseries. That is unsafe for
`lock_only` sizing because those entries can use nearly all remaining paper
cash.

## Symptoms

- Moscow July 7 `20°C NO` opened from AWC/METAR `UUWW` after the bot saw a
  same-day high above the exact bucket.
- The Polymarket rule source was `weather.gov/wrh/timeseries?site=UUWW`, with
  resolution based on the WRH `Temp` table.
- The market later priced as if `20°C YES` was correct, exposing that the bot
  had used a related station feed, not the settlement feed.

## What Didn't Work

- Checking only that the station ID matched. `UUWW` in AWC and `UUWW` in WRH
  are not interchangeable unless the exact WRH settlement table is verified.
- Applying the normal exact high NO rule before checking the market's
  resolution source. That let the bot look at price/liquidity even though the
  evidence was not eligible.

## Solution

Treat any market whose provenance mentions `weather.gov/wrh/timeseries` as
unsupported until a direct WRH `Temp` verifier exists.

- `market_rules.py` now returns a source-conflict reason for WRH timeseries.
- `evaluate_market()` checks rule mismatches before fetching order books, so
  WRH markets fail closed before price temptation.
- Regression tests verify that an AWC lock-only Moscow signal is skipped and
  never calls order-book fetching.

## Why This Works

Strong NO is only safe when the evidence is the same source Polymarket uses for
settlement. AWC/METAR can still be useful for supported markets, but it is not
the WRH `Temp` table. Failing closed prevents a related-but-wrong feed from
becoming concentrated settlement evidence.

## Prevention

- Before adding concentrated `lock_only` sizing for any city, check the market
  rule provenance, not just station ID and observed temperature.
- Add a source-conflict regression test whenever a new settlement feed is
  discovered.
- If direct settlement-source verification does not exist, skip the market
  before subscribing or fetching books.

## Related Issues

- [Wunderground ZGSZ source mismatch](./wunderground-zgsz-source-mismatch.md)
