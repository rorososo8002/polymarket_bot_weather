---
title: Realtime nowcast signals must refresh on the nowcast TTL
date: 2026-06-06
category: logic-errors
module: weather_bot.live_paper_runner
problem_type: logic_error
component: background_job
symptoms:
  - "Realtime paper evaluation could reuse the first `WeatherSignal` for too long while the stream kept running."
  - "`STATION_NOWCAST_CACHE_TTL_SECONDS` could expire without any new nowcast-backed probability calculation."
  - "Same-day observed high/low evidence could miss entry or exit decisions until the next large stream cycle."
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [realtime, nowcast, forecast-cache, ttl, paper-trading, weather-signal]
---

# Realtime nowcast signals must refresh on the nowcast TTL

## 1. What The Problem Was

`run_realtime_forever()` calculated a `WeatherSignal` for each streamed market
before starting the WebSocket monitoring loop. That signal was then reused while
the loop kept monitoring the WebSocket stream.

`WeatherSignal` is the market's probability answer sheet. It contains the model
probability, confidence, source note, parsed weather question, and any nowcast
payload. If that answer sheet is reused too long, the WebSocket prices can be
fresh while the observed-temperature evidence inside the signal is old.

## 2. Why It Was A Problem

Open-Meteo forecasts and same-day nowcast do not move on the same clock.

Open-Meteo is the big forecast answer sheet, so the bot protects API budget
with the forecast cache and one-at-a-time HTTP request throttle. Same-station
nowcast is the current-day observation evidence: the high-so-far or low-so-far
at the settlement station. That evidence can change while a cached forecast is
still valid.

The project already had `STATION_NOWCAST_CACHE_TTL_SECONDS=900`, meaning the
nowcast provider was ready to refresh observation evidence after 15 minutes. But
the realtime runner was holding the already-built `WeatherSignal`, so the
provider's shorter TTL could not matter until the large stream cycle restarted.

## 3. How It Was Fixed

The realtime runner now keeps a per-market timestamp for when each signal was
last calculated:

```python
signal_refreshed_at_by_market[market.market_id] = datetime.now(timezone.utc)
```

Before evaluating a touched WebSocket event, the runner checks whether the
market signal is older than `settings.station_nowcast_cache_ttl_seconds`. If it
is, the runner recalculates the `WeatherSignal` for that market before entry,
portfolio, or exit logic uses it.

The recalculation still passes the existing `OpenMeteoEnsembleClient`:

```python
signal = _call_probability_estimator(
    probability_estimator,
    market.question,
    settings=settings,
    ensemble_client=ensemble_client,
    observation_provider=observation_provider,
)
```

That detail matters. The same ensemble client keeps the Open-Meteo forecast
cache, so refreshing the probability signal after the nowcast TTL does not mean
forcing a fresh Open-Meteo HTTP call. It means the probability path can reuse the
forecast cache while asking the nowcast provider whether same-day observed
evidence has changed.

## 4. What To Check Next Time

- When a realtime loop caches a composite object, check the TTL of every data
  source inside that object.
- Do not assume a shorter provider cache TTL works unless the caller actually
  calls the provider again after that TTL.
- Test the realtime path, not only the lower-level probability function.
- Assert both sides of the cadence split: nowcast refreshes after its TTL, and
  Open-Meteo forecast HTTP calls stay protected by the forecast cache.
- Keep pre-forecast tradeability gates in front of forecast requests when
  refreshing signals for old or held markets.

## 5. What This Project Must Be Especially Careful About

This bot is paper-only, but the evidence must still be honest. A stale nowcast
can make the paper strategy look better or worse than it really is because the
bot may ignore that today's observed high/low has already crossed a market
threshold.

Never fix this by making Open-Meteo requests bursty. Forecasts are API-budgeted
evidence. The safer pattern is to separate the clocks:

- Open-Meteo forecast HTTP calls stay globally serialized with
  `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=15`.
- The forecast cache TTL is the forecast answer-sheet freshness window and
  defaults to `FORECAST_CACHE_TTL_SECONDS=10800`.
- Same-station nowcast-backed signals may refresh on
  `STATION_NOWCAST_CACHE_TTL_SECONDS`.
- Missing, stale, malformed, unsupported, or unmapped nowcast remains
  forecast-only or fail-closed according to the existing probability rules.

## Related

- [Separate forecast freshness from WebSocket stream health](./explicit-forecast-and-websocket-health.md)
- [Do not count weather cache entries as external API usage](../workflow-issues/do-not-count-forecast-cache-as-open-meteo-usage.md)
- [Prefetch AWC METAR stations in bulk](../best-practices/prefetch-awc-metar-stations-in-bulk.md)
- [Do not use observed high nowcast for daily-low markets](./observed-high-nowcast-daily-low-markets.md)
