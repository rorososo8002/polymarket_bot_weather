---
title: Filter Out-Of-Scope Markets Before Forecasting
date: 2026-06-04
last_updated: 2026-06-04
category: docs/solutions/best-practices
module: Weather paper strategy
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - "A market type is disabled or removed from the trading strategy"
  - "A discovery path can return markets that should not reach model scoring"
  - "A market is parseable but lacks required tradeability evidence such as date_hint"
tags: [forecasting, market-filtering, paper-trading, cost-control]
---

# Filter Out-Of-Scope Markets Before Forecasting

## What the problem was
The bot used to let rain and snow markets move deep enough through the pipeline
that they could be parsed, forecasted, and then skipped later by the runner.
That is like accepting a test sheet, grading every question, and only then
throwing the sheet away because the subject was not in scope.

The same leak can happen with temperature markets that are missing required
date evidence. If `REQUIRE_DATE_HINT_FOR_TRADE=true`, `evaluate_market()` will
eventually block `date_hint=None`, but the forecast call may already have been
made unless the runner checks tradeability before probability estimation.

## Why it was a problem
Forecast calls are external work. They consume API request budget, write noisy
forecast evidence, and make paper-performance logs harder to understand. A
market that the strategy will never trade is not useful model data.

## How it was fixed
The strategy was changed to temperature-only. Rain, snow, precipitation, wind,
humidity, and other non-temperature weather markets are filtered before
probability estimation:

- Discovery reads temperature category pages only.
- The parser marks non-temperature weather questions as unsupported market
  variables.
- Open-Meteo requests include only `temperature_2m_max` and
  `temperature_2m_min`.
- Batch and realtime runners filter non-temperature markets before forecast
  probability calculation or WebSocket subscription.
- `pre_forecast_tradeability_gate` now checks temperature shape, trading-ready
  city support, and required `date_hint` evidence before Open-Meteo fetching.
  A market that fails the gate records a SKIP diagnostic instead of spending a
  forecast request.

## What to check next time to prevent the same mistake
When removing or disabling a market type, check the earliest boundary first:

- Does discovery still fetch that category page?
- Can the parser still mark that question as supported?
- Can probability estimation still request external forecast variables for it?
- Can the runner still subscribe its tokens or log late SKIPs for it?
- Can a missing `date_hint` or missing rule-evidence city still reach
  `estimate_weather_probability()` and therefore Open-Meteo?

Add tests that fail if the removed market type reaches the probability
estimator or if an untradeable market makes an Open-Meteo HTTP request.

## What this project must be especially careful about
This project measures a paper-trading strategy. The ledgers are the experiment,
not just logs. `forecast_request_log.jsonl` is the real external forecast
attempt ledger, so every unnecessary request pollutes the API-usage picture. If
an out-of-scope or untradeable market still reaches forecast scoring, the bot
spends calls and creates misleading evidence for a market it never meant to
trade. Filter before scoring, not after scoring.
