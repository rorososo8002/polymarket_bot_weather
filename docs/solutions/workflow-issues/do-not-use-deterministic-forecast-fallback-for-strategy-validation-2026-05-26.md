---
title: Do not use deterministic forecast fallback for strategy validation
date: 2026-05-26
category: workflow-issues
module: weather_bot.probability
problem_type: data_quality
component: paper_trading
severity: high
applies_when:
  - "Validating a weather trading strategy with paper-trading results"
  - "The ensemble forecast provider is unavailable, rate limited, or returning incomplete data"
tags: [paper-trading, forecast-quality, strategy-validation, open-meteo]
---

# Do not use deterministic forecast fallback for strategy validation

## Context

The paper bot exists to test whether the strategy works with the same class of forecast data it would rely on in production. A deterministic single-forecast fallback can produce plausible probabilities when the ensemble forecast fails, but those probabilities are not the intended model output.

If fallback-based paper trades are mixed into the result set, a two-week paper run can look profitable while measuring the wrong strategy.

## Guidance

When Open-Meteo ensemble data is unavailable:

- Do not call the deterministic Open-Meteo forecast endpoint as a substitute.
- Do not open paper trades from fallback probabilities.
- Return `source=forecast-unavailable` with neutral probability and zero confidence.
- Keep the record visible as an operational data issue, not as a valid strategy candidate.
- Count unavailable forecasts separately in the dashboard.

## Why This Matters

Paper-trading performance is only useful if the input data matches the intended strategy. Temporary forecast estimates contaminate the sample and can make an invalid strategy look valid.

## Verification

Add or keep tests proving that an ensemble failure does not call a deterministic forecast client:

```text
deterministic forecast fallback must not be called
source == forecast-unavailable
confidence == 0.0
```
