---
title: Filter Out-Of-Scope Markets Before Forecasting
date: 2026-06-04
category: docs/solutions/best-practices
module: Weather paper strategy
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - "A market type is disabled or removed from the trading strategy"
  - "A discovery path can return markets that should not reach model scoring"
tags: [forecasting, market-filtering, paper-trading, cost-control]
---

# Filter Out-Of-Scope Markets Before Forecasting

## What the problem was
The bot used to let rain and snow markets move deep enough through the pipeline
that they could be parsed, forecasted, and then skipped later by the runner.
That is like accepting a test sheet, grading every question, and only then
throwing the sheet away because the subject was not in scope.

## Why it was a problem
Forecast calls are external work. They consume API request budget, write noisy
forecast evidence, and make paper-performance logs harder to understand. A
market that the strategy will never trade is not useful model data.

## How it was fixed
The strategy was changed to temperature-only. Rain, snow, precipitation, and
other non-temperature markets are filtered before probability estimation:

- Discovery reads temperature category pages only.
- The parser no longer classifies rain or snow as supported market variables.
- Open-Meteo requests include only `temperature_2m_max` and
  `temperature_2m_min`.
- Batch and realtime runners filter non-temperature markets before forecast
  probability calculation or WebSocket subscription.

## What to check next time to prevent the same mistake
When removing or disabling a market type, check the earliest boundary first:

- Does discovery still fetch that category page?
- Can the parser still mark that question as supported?
- Can probability estimation still request external forecast variables for it?
- Can the runner still subscribe its tokens or log late SKIPs for it?

Add tests that fail if the removed market type reaches the probability
estimator.

## What this project must be especially careful about
This project measures a paper-trading strategy. The ledgers are the experiment,
not just logs. If an out-of-scope market still reaches forecast scoring, the
bot spends calls and creates misleading evidence for a market it never meant to
trade. Filter before scoring, not after scoring.
