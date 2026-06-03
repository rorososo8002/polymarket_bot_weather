---
title: Weather Bias JSON Must Fail Closed
date: 2026-06-03
category: logic-errors
module: weather_bot.probability
problem_type: logic_error
component: service_object
symptoms:
  - "An explicit WEATHER_BIAS_JSON path could point to a missing file and still fall back to zero bias."
  - "A broken bias JSON file could be swallowed and paper trading could continue from uncorrected forecasts."
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags: [weather-bias, forecast-quality, fail-closed, paper-trading, calibration]
---

# Weather Bias JSON Must Fail Closed

## Problem

`WEATHER_BIAS_JSON` is the forecast calibration table. It tells the probability
model how much a station or variable usually runs high or low, then the model
subtracts that Fahrenheit bias before judging a market threshold.

The old loader treated an explicit missing or broken file the same as no file:
it swallowed the error and continued with zero bias values.

## Why It Was A Problem

Paper trading is only useful when it measures the strategy the operator thinks
is running. If the operator explicitly configured a calibration file, the
experiment is "calibrated forecast plus paper execution." If that file is
missing or broken but the bot silently uses zero bias, the experiment becomes
"uncalibrated forecast plus paper execution" without saying so.

That contaminates the profit sample. A later win or loss cannot be compared
against the intended calibrated model because the input evidence was different.

## How It Was Fixed

`load_bias_table()` now keeps the old safe default only when
`WEATHER_BIAS_JSON` is empty. When the variable is set, the file must be:

- readable
- valid JSON
- a JSON object mapping station IDs to variable bias objects
- numeric and finite for every bias value

If the explicit file cannot be trusted, the loader raises
`WeatherBiasLoadError` with `WEATHER_BIAS_JSON` and the file path in the
message. `estimate_weather_probability()` catches that as a forecast data
failure and returns:

```text
source = forecast-unavailable
confidence = 0.0
p_true = 0.5
```

That is the same safe SKIP path used for unavailable ensemble forecasts. The
paper runner does not fetch order books or open a position from that signal.

## What To Check Next Time

- If a config file is optional, distinguish "not configured" from "configured
  but unusable."
- Empty `WEATHER_BIAS_JSON` may use neutral defaults.
- Explicit `WEATHER_BIAS_JSON` must fail closed when missing, unreadable,
  malformed, or non-numeric.
- Add focused tests for missing file, invalid JSON, valid file, and entry
  blocking before broad pytest.
- Make the operator message include the setting name so logs point to the
  cause immediately.

## What This Project Must Be Especially Careful About

Forecast calibration changes the measuring instrument for paper performance.
For this weather bot, the input evidence must match the strategy contract:
right station, right date, right forecast source, and right calibration file.
When any configured evidence is missing or broken, skip first and investigate
instead of guessing.

## Related

- [Forecast date must match the market date](./forecast-date-must-match-market-date.md)
- [Separate forecast freshness from WebSocket stream health](./explicit-forecast-and-websocket-health.md)
- [Do not use deterministic forecast fallback for strategy validation](../workflow-issues/do-not-use-deterministic-forecast-fallback-for-strategy-validation-2026-05-26.md)
