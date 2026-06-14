---
title: Temperature range buckets must preserve both endpoints
date: 2026-06-05
last_updated: 2026-06-14
category: logic-errors
module: weather_bot.weather_client, weather_bot.probability, weather_bot.portfolio
problem_type: logic_error
component: service_object
symptoms:
  - "A market such as 86-87F was parsed as an exact 87F bucket."
  - "The YES probability was calculated from a condition different from the market text."
  - "Portfolio interval checks risked using widened ranges instead of the displayed settlement range."
  - "Float artifacts near endpoints could make a boundary value look just outside the displayed range."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [weather-bot, parser, probability, portfolio, range-bucket]
---

# Temperature range buckets must preserve both endpoints

## 1. What The Problem Was

Polymarket weather markets can ask for a temperature range such as `86-87F`,
`62-63F`, or `22-23C`. The parser previously looked for one number followed by
a temperature unit, so `86-87F` was read as the exact `87F` bucket.

`temperature_bucket` is the label that tells the probability code what shape
the market has. Think of it as the problem type. `87F` is one kind of problem,
while `86-87F` is a range problem. If the parser labels a range problem as an
exact problem, every later calculation is reading the wrong question.

## 2. Why It Was A Problem

`p_true` is the bot's YES probability. If the market price is 40 cents and
`p_true` is 60%, the bot sees the YES token as underpriced. If `p_true` is
computed from the wrong bucket, the paper strategy can record a fake edge.

For `86-87F`, the condition is exactly:

```text
86.0 <= temperature_f <= 87.0
```

It is not `87F exact`, and it is not a widened helper interval such as
`[85.5F, 87.5F)`. If the bot widens or shrinks this range, the forecast
probability, edge math, and portfolio scenarios grade the wrong event.

## 3. How It Was Fixed

The parser now detects range-shaped temperature text before it tries the older
single-number pattern. A parsed range stores:

- `temperature_bucket = "range"`
- `temperature_range_lower_f`
- `temperature_range_upper_f`
- `temperature_range_lower_original`
- `temperature_range_upper_original`
- `temperature_range_inclusive`

The probability code applies the lower/upper pair directly. For Fahrenheit
ranges, it compares forecast Fahrenheit values against the displayed Fahrenheit
endpoints. For Celsius ranges, it converts the displayed Celsius endpoints with
only the official formula `F = C * 9 / 5 + 32`, then compares against those
converted values without rounding.

The portfolio interval code uses the same displayed range interval, so scenario
tables and complementary-leg checks do not widen or shrink the market condition.

The current implementation also centralizes endpoint comparison in
`TemperatureBucketInterval.contains_f()`. The boundary values are still stored
in Fahrenheit for compatibility, but comparison uses `millifahrenheit`, an
integer scale where `68.000F` becomes `68000`. This keeps values like
`68.00000000000001F` from being treated as above a displayed `68F` endpoint,
while a real `68.001F` remains outside the range. The interval metadata exposes
the original market unit and the internal comparison unit so logs and tests can
show which ruler was used.

## 4. What To Check Next Time

- Add a parser test first for any new Polymarket question shape.
- Check that parsed fields preserve the real market shape, not only a single
  threshold number.
- Add a probability test with fake ensemble members at each endpoint.
- Add just-below and just-above boundary values such as `85.999F` and
  `87.001F`.
- Include a binary-float artifact case such as `68.00000000000001F`, and a real
  outside value such as `68.001F`.
- Add a portfolio interval test when a market shape can overlap another bucket.
- Check whether a helper name or comment mentions rounding or half-step
  expansion; range and exact markets must not use that behavior unless the
  current Polymarket resolution text explicitly says so.
- Run the focused parser/probability/portfolio tests before the full pytest
  suite.

## 5. What This Project Must Be Especially Careful About

Weather-market wording is part of the trading evidence. If a parser guesses or
collapses a market shape, paper trading can look profitable for the wrong
reason. Temperature buckets must stay consistent across parser output,
probability intervals, and portfolio complementarity checks.

Exact buckets must preserve the displayed settlement value too. For a displayed
`29C` exact bucket, do not invent a hidden `28.5C-29.5C` interval. Daily-high
nowcast below the exact value is not decisive because the high can still rise,
but nowcast above the exact value already makes held YES impossible.

Keep Celsius/Fahrenheit conversion at the parser or boundary-construction
edge. After that, probability votes, nowcast risk, and portfolio checks should
reuse the centralized temperature interval helper instead of writing new float
comparisons in each module.

## Related

- [Observed high nowcast must not affect daily-low markets](observed-high-nowcast-daily-low-markets.md)
- [Weather discovery false positives](weather-discovery-false-positives-2026-05-24.md)
