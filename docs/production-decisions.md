# Production Decisions

## 2026-05-26: Trade Only Verified Polymarket Weather Stations

Decision: The bot trades only the 41 cities currently mapped in `src/weather_bot/stations.py`.

Why: Weather-market edge can disappear if the bot forecasts the wrong station. Previous defaults used city or common climate stations that differed from Polymarket rules, such as Seoul ASOS vs Incheon Intl Airport, Heathrow vs London City Airport, and NYC Central Park vs LaGuardia.

Consequence: A weather-shaped Polymarket market is skipped unless its parsed city is in `STATION_MAP`.

## 2026-05-26: Remove City-Centroid Trading Fallback

Decision: `estimate_weather_probability()` returns `unsupported-station` with zero confidence when no verified settlement station exists.

Why: City-centroid forecasts are useful for exploration but unsafe for production trading. Unknown settlement stations must fail closed.

Consequence: Parser support and trading support are intentionally different. The parser may identify Austin or Dubai, but the strategy will not trade them unless they are added to `STATION_MAP`.

## 2026-05-26: Forecast Cache Refresh Is 30 Minutes

Decision: Default forecast cache TTL is 1800 seconds.

Why: Forecast data is slow-moving compared with order books. Pulling forecasts every fast loop wastes API quota and increases rate-limit risk.

Consequence: Fast order-book cycles reuse cached Open-Meteo ensemble responses until the cache expires.

## 2026-05-26: Fast Order-Book Polling Uses HTTP For Now

Decision: Default order-book polling interval is 5 seconds, implemented through the existing `run_forever()` loop.

Why: This is the smallest safe change that makes order-book checks frequent without introducing websocket state management or live execution risk.

Consequence: This is near-real-time polling, not true streaming. A future pass should separate market/forecast refresh from order-book-only checks, then consider CLOB websocket monitoring.

## 2026-05-26: Keep Paper Trading As The Execution Boundary

Decision: No private keys, signing, or live order submission were added.

Why: The repository is explicitly structured as a live-data paper-trading bot. Moving to real-money execution requires separate safety work.

Consequence: "Trade" in this implementation means paper open/close through `PaperBroker`. Live execution remains a future explicit project.
