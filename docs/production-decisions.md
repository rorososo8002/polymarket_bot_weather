# Production Decisions

## 2026-05-26: Trade Only Verified Polymarket Weather Stations

Decision: The bot trades only the 41 cities currently mapped in `src/weather_bot/stations.py`.

Why: Weather-market edge can disappear if the bot forecasts the wrong station. Previous defaults used city or common climate stations that differed from Polymarket rules, such as Seoul ASOS vs Incheon Intl Airport, Heathrow vs London City Airport, and NYC Central Park vs LaGuardia.

Consequence: A weather-shaped Polymarket market is skipped unless its parsed city is in `STATION_MAP`.

## 2026-05-26: Remove City-Centroid Trading Fallback

Decision: `estimate_weather_probability()` returns `unsupported-station` with zero confidence when no verified settlement station exists.

Why: City-centroid forecasts are useful for exploration but unsafe for production trading. Unknown settlement stations must fail closed.

Consequence: Parser support and trading support use the same verified station set. Unmapped cities are not treated as supported bot cities unless they are added to `STATION_MAP`.

## 2026-05-26: Forecast Cache Refresh Is 30 Minutes

Decision: Default forecast cache TTL is 1800 seconds.

Why: Forecast data is slow-moving compared with order books. Pulling forecasts every fast loop wastes API quota and increases rate-limit risk.

Consequence: Order-book stream evaluations reuse cached Open-Meteo ensemble responses until the cache expires.

## 2026-05-26: Order Books Use The CLOB WebSocket Stream

Decision: Default order-book monitoring uses the Polymarket CLOB WebSocket market channel.

Why: The user requirement is real-time order-book monitoring. A REST loop is still polling and can miss the intended execution behavior.

Consequence: `run_forever()` enters `run_realtime_forever()` by default. Market discovery and forecasts refresh every 30 minutes, while WebSocket `book`, `price_change`, `best_bid_ask`, and tick-size events update the order-book cache and trigger paper-trade evaluation.

## 2026-05-26: Keep Paper Trading As The Execution Boundary

Decision: No private keys, signing, or live order submission were added.

Why: The repository is explicitly structured as a live-data paper-trading bot. Moving to real-money execution requires separate safety work.

Consequence: "Trade" in this implementation means paper open/close through `PaperBroker`. Live execution remains a future explicit project.

## 2026-05-26: Probability Stop Replaces Fixed Price Stop

Decision: The bot no longer uses a fixed entry-price percentage stop. It records `probability_stop_threshold` at entry and closes when the current side probability falls below that threshold.

Why: Weather-market risk is driven more by forecast probability deterioration than by a fixed token-price move. A static price stop can fire on thin bid/ask noise even when the forecast thesis is unchanged.

Consequence: `PROBABILITY_STOP_DROP_THRESHOLD=0.10` is the default. YES positions compare current `p_true` to entry-side probability; NO positions compare `1 - p_true`. Decision logs use `probability_stop_threshold` instead of a price stop column.
