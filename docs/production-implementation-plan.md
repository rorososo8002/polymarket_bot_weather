# Production Implementation Plan

## Strategy

The bot targets Polymarket weather markets only when the settlement station is explicit and mapped locally. It should avoid resolution ambiguity first, then look for executable edge using live YES/NO order books, conservative fees, model margin, and resolution margin.

The current execution mode remains paper trading. Production readiness means the paper bot behaves like a live strategy would: station-gated market discovery, 30-minute forecast refreshes, real-time order-book stream handling, exposure caps, stop guards, and durable logs.

## Core Rules

- Trade only the 41 verified cities in `src/weather_bot/stations.py`.
- Use the Polymarket rule station, not the city center.
- Skip any weather market whose parsed city is not in `STATION_MAP`.
- Cache forecast API responses for 30 minutes by default.
- Monitor Polymarket order books through the CLOB WebSocket market stream by default.
- Continue using `PaperBroker` until live wallet execution is explicitly requested.

## Implemented Plan

1. Add a single station registry:
   - `src/weather_bot/stations.py`
   - Contains 41 verified city/station records from current Polymarket weather rules.
   - Exposes `CITY_COORDS` for parsing and `STATION_MAP` for trading gates.

2. Wire station gating through the bot:
   - `weather_client.py` parses only verified station cities.
   - `polymarket_client.py` rejects markets outside `STATION_MAP`.
   - `probability.py` returns `unsupported-station` if a city somehow lacks a verified Polymarket station.

3. Refresh forecasts on the correct cadence:
   - `FORECAST_CACHE_TTL_SECONDS=1800`
   - `FORECAST_REFRESH_INTERVAL_SECONDS=1800`
   - `OpenMeteoEnsembleClient.from_settings()` continues to use the cache TTL so repeated order-book stream evaluations reuse forecast data.

4. Monitor order books in real time:
   - `ORDERBOOK_STREAM_ENABLED=true`
   - `ORDERBOOK_STREAM_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market`
   - `run_forever()` enters the WebSocket-backed realtime runner by default.
   - WebSocket `book`, `price_change`, `best_bid_ask`, and tick-size events update the in-memory order-book cache.
   - Each relevant order-book event re-evaluates executable YES/NO VWAP against cached 30-minute forecast signals.

5. Preserve paper-trading risk controls:
   - Existing edge thresholds, exposure caps, stop guards, and exit policies remain active.
   - No private-key or live order path is added in this change.

## Next Production Shape

The current architecture separates slow market/forecast refresh from order-book monitoring inside one long-running process. Market discovery and station-based forecast signals refresh every 30 minutes. Between refreshes, the CLOB WebSocket market stream updates the in-memory order-book cache and triggers event-driven paper-trade evaluation.

The next architecture step is operational hardening: stream stale-snapshot alarms, reconnect metrics, startup snapshot coverage checks, and a clear kill-switch before any live-wallet execution path is considered.

## Verification Plan

- Focused tests:
  - Station allowlist count and known station IDs.
  - Unverified cities are not parsed as supported trading cities.
  - Discovery rejects weather-shaped markets outside the verified station set.
  - Settings load 30-minute forecast refresh and WebSocket order-book stream controls.
  - WebSocket book and price-change messages update cached YES/NO order books.
  - `run_forever()` uses realtime WebSocket mode by default.

- Broad tests:
  - Run the full pytest suite with `PYTHONPATH=src`.
  - Run a dry start if available.

## Source Notes

- Polymarket weather event descriptions and `resolutionSource` fields were used to identify stations.
- Hong Kong is resolved by the Hong Kong Observatory Daily Extract rather than a Wunderground airport station.
- The public CLAUDE.md guidance linked by the user was not copied verbatim; `AGENTS.md` contains a local 65-line rewrite.
