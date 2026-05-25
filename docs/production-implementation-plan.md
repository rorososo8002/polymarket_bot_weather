# Production Implementation Plan

## Strategy

The bot targets Polymarket weather markets only when the settlement station is explicit and mapped locally. It should avoid resolution ambiguity first, then look for executable edge using live YES/NO order books, conservative fees, model margin, and resolution margin.

The current execution mode remains paper trading. Production readiness means the paper bot behaves like a live strategy would: station-gated market discovery, fresh-enough forecasts, fast order-book checks, exposure caps, stop guards, and durable logs.

## Core Rules

- Trade only the 41 verified cities in `src/weather_bot/stations.py`.
- Use the Polymarket rule station, not the city center.
- Skip any weather market whose parsed city is not in `STATION_MAP`.
- Cache forecast API responses for 30 minutes by default.
- Poll Polymarket order books every 5 seconds by default.
- Continue using `PaperBroker` until live wallet execution is explicitly requested.

## Implemented Plan

1. Add a single station registry:
   - `src/weather_bot/stations.py`
   - Contains 41 verified city/station records from current Polymarket weather rules.
   - Exposes `CITY_COORDS` for parsing and `STATION_MAP` for trading gates.

2. Wire station gating through the bot:
   - `weather_client.py` parses verified and known unverified cities.
   - `polymarket_client.py` rejects markets outside `STATION_MAP`.
   - `probability.py` returns `unsupported-station` for parsed cities without a verified Polymarket station.

3. Refresh forecasts on the correct cadence:
   - `FORECAST_CACHE_TTL_SECONDS=1800`
   - `FORECAST_REFRESH_INTERVAL_SECONDS=1800`
   - `OpenMeteoEnsembleClient.from_settings()` continues to use the cache TTL so repeated order-book cycles reuse forecast data.

4. Monitor order books quickly:
   - `ORDERBOOK_POLL_INTERVAL_SECONDS=5`
   - `run_forever()` sleeps using the order-book polling interval.
   - Each fast cycle re-evaluates executable YES/NO VWAP against cached forecasts.

5. Preserve paper-trading risk controls:
   - Existing edge thresholds, exposure caps, stop guards, and exit policies remain active.
   - No private-key or live order path is added in this change.

## Next Production Shape

The next architecture step is to separate slow market/forecast refresh from fast order-book polling inside one long-running process. The current version already avoids repeated forecast API calls through the 30-minute cache, but it still refreshes market discovery inside `run_cycle()`. A later production pass should keep a 30-minute signal snapshot in memory and run a tighter order-book-only loop between refreshes.

## Verification Plan

- Focused tests:
  - Station allowlist count and known station IDs.
  - Unverified cities are parsed but not mapped for trading.
  - Discovery rejects weather-shaped markets outside the verified station set.
  - Settings load 30-minute forecast and fast order-book intervals.

- Broad tests:
  - Run the full pytest suite with `PYTHONPATH=src`.
  - Run a dry start if available.

## Source Notes

- Polymarket weather event descriptions and `resolutionSource` fields were used to identify stations.
- Hong Kong is resolved by the Hong Kong Observatory Daily Extract rather than a Wunderground airport station.
- The public CLAUDE.md guidance linked by the user was not copied verbatim; `AGENTS.md` contains a local 65-line rewrite.
