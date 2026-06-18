# Strategy Research And Trading Rules

Read this file only for strategy changes, station-signal modeling, trading behavior, market discovery, risk, or realtime orderbook work.

## Mission

- Improve risk-adjusted paper-trading returns over time, not merely service uptime.
- Treat every strategy change as a research-backed hypothesis. State the motivating market behavior, math, empirical evidence, or paper and finance reference.
- Prefer expected value, calibration, Kelly or fractional Kelly, liquidity, slippage, and drawdown-aware reasoning over ad hoc thresholds.
- Use paper-trading data to study entries, exits, spreads, station evidence,
  market type, city, date horizon, and time to resolution.
- Document strategy rules so a fresh AI can reimplement them from the repo alone.
- When a trade behaves unexpectedly, update both code and production docs with the prevention rule before continuing strategy work.

## Production Guardrails

- Trade only cities listed in `src/weather_bot/stations.py`.
- Treat `STATION_MAP` as the single source of truth for Polymarket weather settlement stations.
- Unknown markets and unknown stations are skips, not guesses or city-centroid fallbacks.
- The current paper strategy is temperature-only. There is no environment
  switch that re-enables non-temperature weather markets.
- Paper-entry signals come from official settlement-station observations.
  A reachable dashboard is not proof of fresh station data; inspect the latest
  station observation timestamp, provider cooldown, and request-log evidence.
- If station observation data is missing, stale, malformed, or mapped to the
  wrong station/date, mark the strategy evaluation skipped.
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
- Current exact Celsius strategy is source-display aware. For an exact whole-C
  daily-high market, observed `23.9C` still belongs to the displayed `23C`
  bucket, while observed `24.0C` breaks the `23C` YES thesis and creates a
  strong NO lock. Near settlement, an observed value that remains safely inside
  the bucket may create a base or strong YES lock. Any entry still needs
  executable depth, after-fee edge, expected return, and portfolio approval.
- Discover weather events before binary submarkets. One city-date temperature
  event can contain lower-tail, exact, and upper-tail buckets. Expand every
  supported weather-category event found, compute bucket probabilities from
  shared non-overlapping boundaries, and report actual event, city, market, and
  token coverage. The 49-city station registry is not an event-count cutoff.
- Use settlement-station observations only from explicitly mapped same-station
  providers. Current providers derive observed high/low extrema from one
  station-date response when possible. Missing, stale, malformed, future-date,
  or unmapped observations skip new entries.
- Select entries at the city-date portfolio level. A small paper account below
  `$1,000` shares a 10% city-date budget across at most two complementary
  non-overlapping temperature buckets. At `$1,000` or more, shrink that shared
  budget to 5%. Normalize event probabilities to 100%, then compare
  `YES+YES`, `YES+NO`, `NO+NO`, one-leg, and no-entry outcomes. Require each
  opened leg to be at least `$10`, size entries with 1/4 fractional Kelly,
  cap one city's different dates at 20%, and cap total paper exposure at 90%.
- Size new entries from the smaller of cost-basis bankroll and executable
  liquidation bankroll. Do not let unrealized profits increase risk. If a
  whole-stream order-book failure prevents reliable pricing, fail closed and
  pause new entries. If only one held token cannot be priced because it is
  illiquid, missing, or settling, value that held position at `$0` in
  `liquidation_bankroll` so it cannot inflate sizing, but do not block every
  unrelated new entry.
- Never add private keys, live-wallet execution, or real orders unless the user explicitly asks for that separate safety project.
