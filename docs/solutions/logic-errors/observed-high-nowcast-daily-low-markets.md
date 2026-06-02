---
title: Do not use observed high nowcast for daily-low markets
date: 2026-06-03
last_updated: 2026-06-03
category: logic-errors
module: weather_bot.probability
problem_type: logic_error
component: service_object
symptoms:
  - "A lowest-temperature question could receive a fresh observed_high_so_far nowcast."
  - "A high daytime observation could force a daily-low lower-tail probability toward zero."
  - "Forecast-only daily-low evidence could be overwritten by unrelated daily-high evidence."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [weather-bot, probability, nowcast, daily-low, fail-closed]
---

# Do not use observed high nowcast for daily-low markets

## 1. What The Problem Was

The temperature probability path used `observed_high_so_far` for every
temperature market. That value means "the highest temperature observed so far
today." It is useful for daily-high questions, but it is not evidence for a
daily-low question.

For example, if Seoul already reached 30C today, that proves something about a
"highest temperature above 30C" market. It does not prove whether the lowest
temperature was below 15C earlier in the morning.

## 2. Why It Was A Problem

Nowcast is powerful because it lets the bot use real observations after part of
the day has happened. But the observation must answer the same question as the
market.

For daily-low markets, using today's observed high can push the model toward
the wrong side. A market like "Will the lowest temperature be 15C or below?"
could be forced toward NO just because the afternoon high was warm. That turns a
helpful correction into false confidence.

This matters for paper trading because probability drives edge, sizing, and
entry filtering. If the probability is corrected with the wrong observed value,
the bot may think a paper trade is safer or more profitable than it really is.

## 3. How It Was Fixed

The first fix made the nowcast gate check the parsed temperature metric before
reading the high-so-far provider:

```python
if parsed.temperature_metric == "min":
    return replace(
        signal,
        note=(
            f"{signal.note}; evidence=forecast-only; "
            "nowcast_unavailable=observed-low-provider-not-supplied"
        ),
    )
```

In plain words: if the question is about the daily minimum temperature, do not
call the observed-high provider. Keep the signal forecast-only until a separate
same-station observed-low provider is verified.

The follow-up fix added that separate low observation path without adding a
second provider call. `StationNowcastObservation` now carries both
`observed_high_c` and `observed_low_c`, and
`AviationWeatherMetarNowcastProvider.observed_temperature_extremes_so_far()`
derives both values from one station-date response:

```python
latest_at = max(observed_at for observed_at, _temp in observations)
high_at, high_c = max(observations, key=lambda item: item[1])
low_at, low_c = min(observations, key=lambda item: item[1])
```

In plain words: call the official station source once, then calculate both
today's highest observed temperature and today's lowest observed temperature
from that same response. Daily-high markets use the high value; daily-low
markets use the low value.

Focused regression tests now check that:

- A high-only provider still leaves daily-low markets forecast-only with
  `nowcast_unavailable=observed-low-provider-not-supplied`.
- A real observed-low provider can push a daily-low threshold crossing to
  `p_true == 1.0`.
- A high request followed by a low request for the same station-date uses the
  cached provider response instead of making another HTTP call.

## 4. What To Check Next Time

- Check both the station identity and the observed value type before applying a
  nowcast correction.
- Treat `observed_high_so_far` as daily-high evidence only.
- Do not apply high-temperature threshold shortcuts to daily-low, exact-low, or
  lower-tail minimum-temperature markets.
- Add paired tests whenever a parser field changes behavior:
  `temperature_metric="max"` should still allow observed-high nowcast, while
  `temperature_metric="min"` should use observed-low nowcast only when the
  same-station provider supplies it.
- Cache observed high and low together by station-date. Do not make separate
  external calls just because the market question changes from high to low.
- Keep the log note explicit. Operators should be able to see why a signal is
  forecast-only instead of guessing whether nowcast failed.

## 5. What This Project Must Be Especially Careful About

This bot must fail closed. Missing low-temperature nowcast should not be patched
with high-temperature data, nearby-station data, or city-center data.

Forecast-only is safer than a confident correction based on the wrong fact. When
observed-low nowcast exists, it must come from the same response family as the
verified settlement-station provider and must stay inside the normal freshness
and unmapped-source checks.

## Related Issues

- [Use same-station nowcast pilots](./use-same-station-nowcast-pilots.md)
- `src/weather_bot/probability.py`
- `tests/test_probability_ensemble.py`
