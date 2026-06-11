# Production Decisions

This is the active rule book for operating or changing the paper bot. Keep it
compact; historical notes belong in focused `docs/solutions/` entries.

## Execution Boundary

- Paper-only execution is the boundary. No private keys, wallet connection,
  signing, live orders, redemption, copy trading, or private data collection
  without explicit approval and a separate live-trading safety pass.
- Public dashboard exposure requires a real `DASHBOARD_TOKEN` with at least 32
  characters. Public `/api/status` must accept the token only through
  `X-Dashboard-Token`; URL query tokens leak through logs, history, and shares.
- Boolean, numeric, integer, and choice settings fail closed at startup when
  malformed or outside safe ranges.

## Market Universe And Forecasts

- `STATION_MAP` registers 41 cities. Paper execution uses only
  `TRADING_READY_STATION_MAP`: 40 cities with stored official Polymarket rule
  evidence. Karachi remains excluded until station evidence is reconciled.
- Execute temperature markets only. Non-temperature weather markets must not
  reach forecast probability calculation, order-book subscription, or paper
  trade logging.
- Unknown, stale, malformed, unsupported, suspicious, missing, or conflictful
  data means skip.
- Forecast rows must match the target market date exactly. Nearby dates are not
  substitutes.
- `pre_forecast_tradeability_gate` rejects markets before Open-Meteo when they
  are not temperature-shaped, not trading-ready, or missing required date
  evidence. Undated markets always fail closed.
- `WEATHER_BIAS_JSON` is optional calibration evidence. Empty means neutral
  defaults; missing, unreadable, invalid, malformed, or non-numeric files
  produce `forecast-unavailable` with zero confidence.
- Open-Meteo real HTTP calls are protected by `FORECAST_CACHE_TTL_SECONDS=10800`
  and serialized by `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=15`. Budget:
  40 cities x 8 batches/day x 31 units = 9,920 units/day, under the 10,000
  daily limit.
- On non-rate-limit forecast failure, skip that city and move to the next one.
  Do not retry the same city within the same batch; retry after the cache TTL.
- On 429 rate limit, stop the batch and wait for the rate-limit cooldown before
  resuming. Do not hammer failed cities.
- `forecast_cache.json` is an answer cache, not the call ledger. Real attempts
  are recorded in `forecast_request_log.jsonl`.

## Realtime Order Books

- Use the Polymarket CLOB WebSocket market stream by default. Do not silently
  replace realtime streaming with polling.
- Realtime startup must not wait for every market forecast. Start WebSocket once
  the temperature token subscription set is known, then attach forecast and
  nowcast signals as they become ready.
- Missing or stale forecast signals block new entries and queue a refresh; they
  are not executable entry evidence.
- Forecast scheduling has two lanes: normal round-robin plus priority for held
  positions, near-close markets, nowcast-near-threshold markets, and live-price
  opportunities. Priority changes which eligible city gets the next request
  slot; it must not create duplicate, parallel, or burst Open-Meteo calls.
- Signal refresh targets are 40 minutes for general cities, 30 minutes for
  held-position cities, and 20 minutes for priority cities. These refresh
  in-memory signals and may reuse cached Open-Meteo answers; they must not force
  real HTTP calls before the 3-hour forecast cache expires.
- WebSocket callbacks must not make forecast HTTP calls.
- Keep token IDs for open positions subscribed even when discovery moves to
  newer markets.
- Discovery maps YES/NO token IDs only from explicit outcome labels. If tokens
  or outcomes cannot prove both sides, skip the market.
- `best_bid_ask` messages are indicative quotes only. Executable depth comes
  from `book` snapshots and valid `price_change` updates.
- A `price_change` may update executable depth only after that token already
  received a full-depth snapshot in the current stream cache. That snapshot may
  come from WebSocket `book` or the bounded REST `/book` verification path.
- Stale or dead executable depth blocks new entries and pauses held-position
  exits with observable reasons.
- REST order-book snapshots may seed or resync the WebSocket cache at a bounded
  interval. They are verification photos, not the realtime camera: WebSocket
  remains primary, REST snapshots must not trigger evaluations, and raw
  snapshots must not be written to runtime ledgers.

## Strategy, Risk, And Accounting

- `DECISION YES` and `DECISION NO` are model/order-book judgments, not
  guaranteed opens. Broker exposure, hedge, confidence, liquidity, fee, and
  stale-data gates may still block entry.
- Entry decisions are fee-aware. `p_exec` is executable VWAP; `size_usd` is the
  all-in paper-entry budget; `size_shares` is the fee-adjusted share count.
- Position sizing defaults:
  `SIZE_MODE=kelly`, `FRACTIONAL_KELLY=0.25`, `ENTRY_FRACTION=0.20`,
  `MAX_TOTAL_EXPOSURE_FRACTION=0.60`, `MAX_CITY_EXPOSURE_FRACTION=0.20`.
- In Kelly mode, `ENTRY_FRACTION` is a per-event cap, not the direct order size.
  Do not switch back to `fixed_fraction` without resetting the fraction and
  documenting the risk tradeoff.
- Same-market opposite-side entries remain blocked. Same-side add-ons are
  allowed only when price, probability, edge, expected return, cash, and
  exposure caps still pass.
- City-date weather buckets share one correlated-risk budget. At most two
  complementary legs are selected per event.
- Exit decisions use after-fee liquidation PnL, not raw token-price movement.
  Allowed exits: probability stop, take profit, overheated profit, edge faded,
  max hold, settlement, and nowcast bucket-lock risk.
- Nowcast bucket-lock risk blocks new entry as well as triggering exit. If it
  fires before order placement, convert the candidate to SKIP.
- If an exit signal fires but the close cannot execute, log the blocker and
  preserve the original `exit_trigger`; do not pretend to sell.
- Profit exits may recover principal and keep a bounded settlement runner only
  when conservative settlement value beats fee-adjusted sell-now value.
- Resolved paper settlement requires a proven binary winner. Ambiguous closed
  prices are not guessed.
- `paper_state.json` is the account book, not a disposable cache. Existing
  corrupt, structurally invalid, or unsafe state fails closed instead of reset.
- `paper_state.json` and `paper_trades.csv` are paired ledgers. Startup replays
  executed trade rows against `BANKROLL_USD` and fails closed on mismatch.

## Nowcast And Station Evidence

- Same-station nowcast is allowed only from explicitly mapped official sources:
  AWC METAR for ICAO stations and HKO for Hong Kong.
- Real AWC METAR bulk requests must be at least 5 minutes apart. Real HKO
  max/min requests must be at least 10 minutes apart. Cache hits do not write
  request-log rows.
- `STATION_NOWCAST_CACHE_TTL_SECONDS=300` is the recommended nowcast TTL. It
  matches the AWC METAR provider floor and keeps held-position exits timely.
- Forecast freshness and nowcast freshness are separate. The 5-minute nowcast
  TTL must never be used to declare a forecast signal stale.
- The target date may be station-local today, or station-local yesterday only
  during the post-close freshness window for held-position exit and settlement
  evidence.
- For daily-high threshold markets, `observed_high_c >= threshold_c` is held
  YES favorable evidence and held NO `nowcast_bucket_lock_risk`. Exact/range
  buckets must use parsed bucket boundaries.

## Runtime Data And Disk

- Runtime ledgers are ignored by git and live under `data/` in production.
  Delete or recreate them only for an intentional fresh paper experiment.
- `paper_decisions.csv` suppresses SKIP rows by default. Enable SKIP logging
  only for short debugging sessions.
- `paper_event_portfolios.jsonl` writes only when at least one trade is
  selected by default.
- `paper_raw_snapshots.jsonl` is diagnostic evidence, not a source ledger.
  Normal snapshots stay disabled except for errors.
- Do not apply diagnostic cleanup rules to `paper_state.json`,
  `paper_trades.csv`, or `paper_decisions.csv`.
- Reports may scan full ledger history when promised, but must stream rows and
  keep only aggregates or bounded lookups in memory.
- Individual market evaluation exceptions fail closed as observable
  diagnostics: write a `SKIP_ERROR` row, write an error raw snapshot, and keep
  runner-status error fields.
- Logrotate compresses high-volume diagnostic/request files, not core account
  ledgers. Current policy: raw snapshots at 100 MB; request logs and portfolio
  diagnostics at 10 MB; keep five archives under `data/archive/`.
- `docs/codex/known-good-commands.md` is the command source for local pytest,
  Oracle SSH, remote pytest, and dashboard checks.
