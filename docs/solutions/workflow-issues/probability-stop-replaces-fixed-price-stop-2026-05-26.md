---
title: Probability stop replaces fixed price stops for weather markets
date: 2026-05-26
category: workflow-issues
module: weather_bot.exit_policy
problem_type: workflow_issue
component: development_workflow
severity: high
applies_when:
  - "A weather trading strategy exits on fixed token-price movement instead of forecast probability deterioration"
  - "Decision logs contain a price stop column that implies price is the primary thesis invalidation signal"
tags: [probability-stop, weather-markets, exit-policy, paper-trading]
---

# Probability stop replaces fixed price stops for weather markets

## Context
The bot previously treated a fixed token-price drop as the primary stop. That made the documentation and logs price-first even though the strategy thesis comes from station-based forecast probability.

## Guidance
For weather markets, record the side probability at entry and close when that side probability deteriorates beyond the configured threshold.

```text
YES side probability = p_true
NO side probability = 1 - p_true
probability_stop_threshold = entry_side_probability - PROBABILITY_STOP_DROP_THRESHOLD
```

Default:

```text
PROBABILITY_STOP_DROP_THRESHOLD=0.10
```

Decision logs should use `probability_stop_threshold`. Do not reintroduce a fixed entry-price stop unless there is a separate explicit design decision.

## Why This Matters
Weather-market risk changes when the forecast changes. A token can trade down because of thin liquidity or temporary spread noise even when the forecast thesis is intact. Probability stops align exits with the model input that created the edge.

## When to Apply
- The position was opened from a forecast-probability edge.
- New forecasts or realtime edge updates change the model probability.
- A handoff document or env example still describes price-drop stops for weather markets.

## Examples
Before:

```text
Exit because token price moved down by a fixed percentage.
```

After:

```text
probability_stop_threshold = entry_side_probability - 0.10
close when current_side_probability <= probability_stop_threshold
```

## Related
- `docs/production-decisions.md`
- `src/weather_bot/exit_policy.py`
