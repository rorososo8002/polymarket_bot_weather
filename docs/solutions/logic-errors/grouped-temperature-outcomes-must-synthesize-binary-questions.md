---
title: Grouped temperature outcomes must synthesize binary questions
date: 2026-06-15
category: logic-errors
module: weather_bot.polymarket_client, weather_bot.market_rules
problem_type: logic_error
component: service_object
symptoms:
  - "The live runner reported 0 streamable temperature tokens while Polymarket displayed many active temperature outcome markets."
  - "Forecast worker counters stayed at zero because no market reached forecast scheduling."
  - "Dashboard WebSocket health showed WAITING with no streamable temperature tokens."
root_cause: wrong_api
resolution_type: code_fix
severity: high
tags: [polymarket, discovery, temperature-markets, grouped-outcomes, websocket]
---

# Grouped Temperature Outcomes Must Synthesize Binary Questions

## Problem

Polymarket can expose recurring temperature markets as one event title plus
separate outcome labels. For example, the event title may be `Highest
temperature in Seoul on June 15?` while each tradable row carries an outcome
label such as `28C`, `22C or below`, or `32C or higher`.

The paper bot originally expected each Gamma market row's `question` to already
contain the city, date, and temperature condition. When the condition moved to
the outcome label, discovery discarded otherwise valid temperature rows before
forecasting or WebSocket subscription.

## Symptoms

- `paper_runner_status.json` showed `0 tokens across 0 markets`.
- `forecast_worker.processed_task_count` stayed at `0`.
- No Open-Meteo forecast request was attempted because no market was scheduled.
- This looked like a conservative strategy skip, but it happened before the
  strategy had any market to evaluate.

## What Didn't Work

- Treating `WAITING` as proof of normal no-market behavior hid the upstream
  discovery failure.
- Looking only at `markets_total` after pre-forecast filtering could not tell
  whether discovery found nothing or whether all candidates were rejected by
  validation gates.

## Solution

Normalize grouped temperature rows before applying the normal parser.

`PolymarketClient._normalized_weather_question()` now keeps already-binary
questions unchanged. If the row question lacks a temperature condition, it reads
the event title and grouped outcome label, then synthesizes a binary question:

```text
event title: Highest temperature in Seoul on June 15?
outcome:     28C
synthetic:   Will the highest temperature in Seoul be 28C on June 15?
```

The same path handles tail labels such as `22C or below` and `32C or higher`.
After synthesis, the existing parser, rule provenance, station gates, token
mapping, forecast scheduler, and WebSocket subscription logic remain unchanged.

The runner status also carries discovery-stage counters:

```text
raw_discovered_markets
temperature_markets
pre_forecast_skipped
pre_forecast_skip_reasons
stream_markets
stream_tokens
```

These counters distinguish "no markets discovered" from "markets found but
entry gates rejected them."

## Follow-Up Fix

Grouped temperature events also reuse one event-level description for every
outcome row. That description may explain the settlement station, source,
provider precision, or UI unit toggle. It is not always an individual YES
condition for `28C`, `29C`, and so on.

The rule validator must therefore keep station, city, date, metric, and
explicit settlement-unit checks strict, while comparing bucket shape/value only
when the rule text explicitly says the outcome resolves YES or NO under that
specific condition. Otherwise generic text like "temperature range that
contains" or "toggle between Fahrenheit and Celsius" can make every grouped
outcome fail with false `SKIP_RULE_MISMATCH` reasons.

## Why This Works

The root cause was not that the strategy became too conservative. The market
shape changed before the strategy stage: event-level title and outcome-level
condition were split across separate fields. Combining those two fields restores
the binary question shape expected by the existing validation pipeline, and
scoping bucket comparison to explicit outcome rules avoids false rejections
without weakening station or settlement-source checks.

## Prevention

- Keep a regression test where `question` is only the event title and
  `groupItemTitle` carries the temperature condition.
- Check `discovery.raw_discovered_markets`, `discovery.temperature_markets`,
  and `discovery.pre_forecast_skip_reasons` before tuning entry thresholds.
- Keep grouped-event rule tests with provider precision numbers and UI unit
  toggle text so the validator does not treat generic event text as a bucket.
- Do not loosen strategy edge, spread, fee, liquidity, or station rules just
  because `stream_tokens` is zero; first prove discovery expanded grouped
  outcomes correctly.

## Related Issues

- [Bound category slug discovery before stream startup](../performance-issues/bound-category-slug-discovery-before-stream-startup.md)
- [Discover weather events before binary markets](./discover-weather-events-before-binary-markets.md)
- [Runner status concurrent writes](./runner-status-concurrent-writes.md)
