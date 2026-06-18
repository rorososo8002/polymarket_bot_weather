---
title: Exact Celsius NO entries need source-display probability
date: 2026-06-16
last_updated: 2026-06-18
category: logic-errors
module: weather_bot.live_paper_runner, weather_bot.probability
problem_type: logic_error
component: service_object
symptoms:
  - "The bot opened a 23C exact-bucket NO while the forecast mean was close to the selected Celsius value."
  - "A later same-station nowcast entered the displayed exact bucket and triggered a loss-making NO close."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, exact-bucket, celsius, entry-guard, precision-risk]
---

# Exact Celsius NO entries need source-display probability

## Problem

An Amsterdam exact Celsius market opened a `NO` entry because the old
probability model counted only ensemble members exactly equal to the displayed
`23C` value. That was internally consistent with the exact-bucket settlement
rule, but strategically unsafe when the settlement source itself displays
whole-degree Celsius values.

## Symptoms

- Entry reason showed `vote=0.000` and a strong NO edge.
- The same market note also showed a forecast mean near the selected bucket,
  such as `mean=73.1F` or `mean=74.6F` for a `23C` exact bucket.
- The held NO later closed on `nowcast_bucket_lock_risk` when observed station
  high entered the displayed exact bucket.

## What Didn't Work

- Widening exact buckets into hidden half-step intervals is not acceptable.
  Existing rules intentionally keep exact buckets as displayed-value matches.
- Relying only on `p_true=0.0` also failed. Zero exact-member votes said no
  ensemble member matched the exact decimal value; they did not estimate the
  chance that the official source would display the selected whole-degree
  Celsius value.
- Treating an Open-Meteo decimal forecast such as `23.7C` as the settlement
  observation also failed. The settlement source can expose only an integer
  Celsius display, so the target is not the model decimal itself.

## Old Strategy

The previous strategy was:

```text
P(bucket_c) = P(raw_forecast_decimal_c exactly equals bucket_c)
```

Then, because that probability was too often near zero, exact Celsius NO was
blocked whenever the forecast mean sat within `1.0C` of the selected bucket.
That avoided some bad NO entries, but it was too blunt: it blocked adjacent
NO candidates even when the market price overestimated that adjacent bucket.

## New Strategy

The current strategy is:

```text
P(bucket_c) = P(source_displayed_integer_c == bucket_c)
NO edge = (1 - P(bucket_c)) - executable_NO_price - fee - risk_margins
```

For a whole-degree `23C` bucket, the probability model estimates the chance
that the settlement source displays `23C`. This is a probability model for a
whole-degree display source, not a hidden settlement range. Current evidence
shows the display bucket should be modeled as `[23.0C, 24.0C)`: `23.7C` still
supports `23C`, while `24.0C` breaks it.

Exact Celsius NO is now blocked only when the forecast mean maps to the same
displayed integer bucket. In the Amsterdam example, a mean near `23.1C` maps
to the `23C` modal bucket, so `23NO` is blocked. `22NO`, `24NO`, or tail NO
can still be considered, but only if executable depth, after-fee edge,
expected net return, and portfolio limits all pass.

The active paper thresholds were also made less conservative for validation:
`MIN_NET_EDGE=0.08` and `ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.04`. This should
increase paper sample count while still rejecting negative after-fee expected
value.

## Regression Shape

Keep settlement exactness literal, but model whole-degree display probability
for exact Celsius forecast odds. Then apply a modal-bucket NO guard.

The focused regression test should use the full entry evaluator, not only the
probability helper:

```python
def test_exact_celsius_no_entry_skips_only_forecast_mean_modal_bucket():
    ...
    result, per_side = evaluate_market(market, signal, client, settings, 200.0, "temperature")

    assert per_side["NO"].side == "SKIP"
    assert per_side["NO"].net_edge > settings.min_net_edge
    assert "SKIP_EXACT_CELSIUS_MODAL_NO" in per_side["NO"].reason
    assert result.side == "SKIP"
```

## Why This Works

The settlement rule remains exact displayed value. The probability model now
matches the source format better: if the official source reports whole-degree
Celsius, the forecast probability should answer whether that official display
will show the selected integer.

That removes the false `p_true=0.0` confidence that caused the Amsterdam NO
entry while avoiding the overly broad `within 1.0C` block that suppressed
adjacent NO opportunities.

## Settlement Investigation

A 2026-06-10 through 2026-06-17 Gamma sweep found 391 closed temperature
events: 265 Wunderground Celsius-source events, 91 Fahrenheit-range events,
and 35 Celsius events where Gamma did not expose a Wunderground source URL.
The Wunderground Celsius event text states that the source uses whole-degree
Celsius precision.

For Amsterdam on 2026-06-16, Gamma resolved `23C` YES to `1/0`. The
Weather.com historical observation endpoint for `EHAM:9:NL` showed a metric
daily max of `23C` at 13:55Z and 14:55Z, while the same endpoint in imperial
units showed `73F`. That means the actionable target is the settlement source's
displayed integer Celsius value, not an Open-Meteo decimal forecast value.

The strategy formula now estimates:

```text
P(source_displayed_integer_c == bucket_c)
```

not:

```text
P(raw_forecast_decimal_c exactly equals bucket_c)
```

The old exact-member-only formula is retired for whole-degree Celsius exact
buckets.

## Prevention

- Separate settlement semantics from probability modeling. Settlement exactness
  stays literal; forecast probability must still mirror the source display
  precision.
- When exact Celsius NO looks attractive, compare the market NO price with the
  source-display integer probability, not exact decimal equality.
- Test the modal bucket and an adjacent bucket together so the code proves it
  blocks `23NO` near a `23C` modal forecast without blocking every nearby NO.
- For Wunderground Celsius markets, replay the official displayed integer
  outcome before changing entry thresholds. Do not infer settlement from
  Open-Meteo decimal forecasts alone.
- Deploy runner-behavior changes to the VPS and verify `phase=streaming`,
  WebSocket freshness, and forecast worker errors after restart.

## Related Issues

- [Exact and range bucket nowcast must flag held NO exit risk](exact-range-nowcast-bucket-lock-risk.md)
- [Temperature range buckets must preserve both endpoints](temperature-range-buckets-must-preserve-endpoints.md)
- [Held-position exit evidence must not depend on entry bankroll](../best-practices/held-position-exit-evidence-must-not-depend-on-entry-bankroll.md)
