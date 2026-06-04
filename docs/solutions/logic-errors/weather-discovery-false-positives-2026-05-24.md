---
title: Weather discovery must reject substring false positives
date: 2026-05-24
last_updated: 2026-06-04
category: docs/solutions/logic-errors
module: weather market discovery
problem_type: logic_error
component: service_object
symptoms:
  - Real Polymarket scans selected NHL, politics, Waymo, and sports markets as weather candidates.
  - Words inside unrelated terms matched weather or comparison cues, such as rain in Ukraine and over in governor.
  - Discovery paginated too deeply when few supported weather markets were available.
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [polymarket, weather-discovery, parser, live-data]
---

# Weather discovery must reject substring false positives

## Problem

The live paper cycle initially treated unrelated Polymarket questions as weather
markets. Examples included Carolina Hurricanes futures, New York governor
markets, Waymo launch markets, and city sports futures.

## Symptoms

- `Ukraine` matched the substring `rain`.
- `Carolina Hurricanes` matched `hurricane`.
- `governor` matched `over`.
- Year strings such as `2026` were interpreted as temperature thresholds.
- When the stricter filter found few candidates, discovery kept paginating until
  a deep Gamma page returned an HTTP error.

## What Didn't Work

- A broad `any(word in text for word in weather_words)` filter was too permissive
  because it matched substrings inside unrelated words.
- Temperature parsing without word boundaries treated ordinary prose as weather
  comparisons.
- Searching until the requested market count was filled assumed weather markets
  would appear soon in the general active-market feed.

## Solution

Use supported weather shapes instead of broad weather keywords:

- Require a parsed supported weather question: known city plus a temperature
  threshold/comparison. Rain, snow, precipitation, wind, humidity, and other
  non-temperature weather questions are now outside the paper strategy and must
  remain unsupported.
- Use whole-word comparison patterns so `over` does not match `governor` and
  `hit` does not match unrelated text.
- Preserve supported Korean/Celsius forms and `or lower` style comparisons.
- Bound Gamma discovery pagination and return partial results when later pages
  fail after at least one page was scanned.

Regression tests now cover the real false positives and the pagination failure
mode in `tests/test_hardening.py`.

## Why This Works

The strategy can only estimate supported weather events. Filtering by broad
topic words admits markets the probability model cannot price, creating noisy
paper logs and potential live-trading risk. Filtering by supported parse shape
aligns discovery with the model's actual capability.

## Prevention

- Add every live-data false positive to discovery tests before changing parser
  behavior.
- Treat words from market questions as language, not substrings.
- Keep external API pagination bounded so a sparse candidate set cannot turn one
  scan into a deep crawl.

## Related Issues

- [VPS live paper runbook](../../VPS_LIVE_PAPER.md)
