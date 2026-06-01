# Strategy Research And Trading Rules

Read this file only for strategy changes, probability modeling, trading behavior, market discovery, risk, or realtime orderbook work.

## Mission

- Improve risk-adjusted paper-trading returns over time, not merely service uptime.
- Treat every strategy change as a research-backed hypothesis. State the motivating market behavior, math, empirical evidence, or paper and finance reference.
- Prefer expected value, calibration, Kelly or fractional Kelly, liquidity, slippage, and drawdown-aware reasoning over ad hoc thresholds.
- Use paper-trading data to study entries, exits, spreads, forecast error, station bias, market type, city, date horizon, and time to resolution.
- Document strategy rules so a fresh AI can reimplement them from the repo alone.
- When a trade behaves unexpectedly, update both code and production docs with the prevention rule before continuing strategy work.

## Production Guardrails

- Trade only cities listed in `src/weather_bot/stations.py`.
- Treat `STATION_MAP` as the single source of truth for Polymarket weather settlement stations.
- Unknown markets and unknown stations are skips, not guesses or city-centroid fallbacks.
- Refresh forecast data through the Open-Meteo cache no more often than every 30 minutes by default.
- Apply the forecast TTL to memory and disk cache entries alike. A reachable
  dashboard is not proof of fresh forecast data; inspect the last successful
  forecast time and cache age.
- If Open-Meteo is rate-limited or ensemble data is unavailable, do not treat deterministic fallback as real forecast evidence. Mark strategy evaluation invalid or skipped.
- Realtime orderbook requirements mean the CLOB WebSocket stream by default. Do not silently replace realtime monitoring with polling.
- Treat WebSocket process liveness, receiver-thread liveness, incoming-message
  freshness, and actual order-book price freshness as separate checks.
- Keep token IDs for open positions in stream subscriptions even if market discovery has rolled forward to newer markets.
- Invalid edge sentinels such as `-999` are missing-data markers, not real negative edge. They must not trigger edge-faded exits.
- Keep model `net_edge` and executable expected net return as separate entry
  gates. Use the official weather taker-fee curve. Entry VWAP already embeds
  entry spread and slippage; do not subtract them twice. Evaluate conservative
  settlement value separately so high prices are judged by remaining return,
  not rejected by a blanket cap.
- Never add private keys, live-wallet execution, or real orders unless the user explicitly asks for that separate safety project.
