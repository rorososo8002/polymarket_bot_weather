# Production Decisions

This is the active rule book for operating or changing the paper bot. Keep it
compact; historical notes belong in focused `docs/solutions/` entries.

## Current Phase: Strategy Validation First

- Current work is paper-only strategy validation; live discussion requires `docs/paper-validation-runbook.md` gates plus a safety project.
- Do not build wallet, private-key, signing, real-order, redemption,
  claim/copy-trading, or `LiveBroker` behavior in this phase.
- Trust paper PnL only from executable ask/bid depth, fees, spread, slippage,
  stale-data fail-closed behavior, official station nowcast, and replayable
  ledgers.
- Advanced dashboards, calibration/optimizer views, heatmaps, and live trading
  stay deferred until `docs/strategy-validation-roadmap.md` P0 gates pass.

## Execution Boundary

- Paper-only execution is the boundary: no keys, wallet, signing, live orders,
  redemption, copy trading, or private data without a live-safety pass.
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
- Market title parsing is not enough rule evidence. New provenance work must
  preserve the question plus available description/resolution text, source,
  station, unit, bucket shape, and station-local event date window. If title
  parsing and rule text conflict, skip the market instead of trading it.
- Gamma discovery normalizes available rule evidence into the market metadata
  contract. A title/rule conflict on city, high/low direction, unit, bucket
  shape, threshold/range value, date hint, or explicit settlement station must
  fail before forecast fetching with `SKIP_RULE_MISMATCH`.
- Treat the gap plan as explicit execution, not an always-on backlog. Fresh
  chats resume only from `docs/active/current-task.md`.
- Same-station evidence quality must stay explicit in station metadata:
  temperature unit, reporting precision, same-station support, confidence
  grade, verification date, and confidence level.
- Only station confidence grades A/B may enter `TRADING_READY_STATION_MAP`;
  grades C/D, unsupported providers, inferred, nearby, or unverifiable sources
  remain excluded. Karachi stays excluded until station evidence is reconciled.
- Market metadata must carry the station-local event date plus UTC start/end
  window. Forecast rows must match that local date exactly; nearby dates are
  not substitutes.
- Temperature bucket comparisons use centralized millifahrenheit boundaries.
  Decimal model members must match the displayed exact value to vote YES; do
  not round them into hidden half-step buckets.
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
- Polymarket category-page discovery may parse many event slugs, but detailed
  `/events/slug/...` fetches are capped at 80 per discovery cycle and lower
  explicit `max_pages * page_size` budgets must be honored.
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
- A new entry must survive a final pre-trade check: fresh executable book,
  enough ask depth, configured absolute/percentage spread limits, still-positive
  after-fee edge, no conflict with held positions, exposure room, rule clarity,
  and non-stale strategy inputs. A spread failure uses `SKIP_WIDE_SPREAD`.
- Entry decisions are fee-aware. `p_exec` is executable VWAP; `size_usd` is the
  all-in paper-entry budget; `size_shares` is the fee-adjusted share count.
- Sizing defaults: `SIZE_MODE=kelly`, `FRACTIONAL_KELLY=0.25`, `ENTRY_FRACTION=0.20`,
  `MAX_TOTAL_EXPOSURE_FRACTION=0.60`, `MAX_CITY_EXPOSURE_FRACTION=0.20`.
- Signal confidence is sizing evidence, not `p_true`: lower confidence scales
  entry size down; stale forecasts block new entries while held exits still run.
- In Kelly mode, `ENTRY_FRACTION` is a per-event cap, not the direct order size.
  Do not switch back to `fixed_fraction` without resetting the fraction and
  documenting the risk tradeoff.
- Same-market opposite-side entries remain blocked. Same-side add-ons are
  allowed only when price, probability, edge, expected return, cash, and
  exposure caps still pass.
- City-date weather buckets share one correlated-risk budget. At most two
  complementary non-overlapping legs are selected per event; hidden
  threshold-ladder overlap fails closed and logs keep a scenario payoff audit.
- Exit decisions use after-fee liquidation PnL, not raw token-price movement.
  Allowed exits: probability stop, take profit, overheated profit, edge faded,
  max hold, settlement, and nowcast bucket-lock risk.
- Nowcast bucket-lock risk blocks new entry as well as triggering exit. If it
  fires before order placement, convert the candidate to SKIP.
- If an exit signal fires but the close cannot execute, log the blocker and
  preserve the original `exit_trigger`; do not pretend to sell.
- Whole-stream order-book failure blocks new entries. One missing/illiquid held
  token is worth $0 in `liquidation_bankroll`, not a global unrelated block.
- Drawdown circuit breakers block new entries only; held exits and settlements
  must continue.
- Profit exits may recover principal and keep a bounded settlement runner only
  when conservative settlement value beats fee-adjusted sell-now value.
- Resolved paper settlement requires a proven binary winner. Ambiguous closed
  prices are not guessed.
- `paper_state.json` is the account book, not a disposable cache. Existing
  corrupt, structurally invalid, or unsafe state fails closed instead of reset.
- `paper_state.json` and `paper_trades.csv` are paired ledgers. Startup replays
  executed trade rows against `BANKROLL_USD` and fails closed on mismatch.
- New decision and trade rows must carry compact replay evidence: token/city,
  station-local date, market shape, station evidence, signal source, entry VWAP,
  expected net return, reason code, and model/config version. Existing old
  ledger rows stay readable and are not rewritten.
- Normal SKIP spam stays suppressed by default, but reports should aggregate
  stable reason codes such as `SKIP_WIDE_SPREAD`.

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
- Dashboard open-position cards must surface the latest same-station nowcast
  high/low value when the latest decision row contains observed station
  evidence. A missing nowcast badge means no usable observed value was present
  in the latest decision payload, not that the provider should be assumed idle.
- Dashboard open-position cards must separate reference mark PnL from bid-depth
  liquidation PnL and show bid depth, exit status/blocker, and WS freshness.
- The target date may be station-local today, or station-local yesterday only
  during the post-close freshness window for held-position exit and settlement
  evidence.
- For daily-high threshold markets, `observed_high_c >= threshold_c` is held
  YES favorable evidence and held NO `nowcast_bucket_lock_risk`. Exact/range
  buckets must use Polymarket settlement text directly: exact buckets are the
  displayed value only, and range buckets are the displayed inclusive
  endpoints. Do not widen exact buckets into half-step intervals such as
  `28.5C-29.5C`.
- For daily-high exact/range held YES positions, same-station nowcast makes
  YES impossible only after the observed high is above the exact value or range
  upper endpoint. A lower observed high is not decisive because the day's high
  can still rise. For daily-low exact/range held YES positions, same-station
  nowcast makes YES impossible only after the observed low is below the exact
  value or range lower endpoint.

## Runtime Data And Disk

- Runtime ledgers are ignored by git and live under `data/` in production.
  Delete or recreate them only for an intentional fresh paper experiment.
- `paper_decisions.csv` suppresses SKIP rows by default. Enable SKIP logging
  only for short debugging sessions.
- `paper_event_portfolios.jsonl` writes only when at least one trade is
  selected by default.
- `paper_raw_snapshots.jsonl` is diagnostic evidence, not a source ledger.
  Normal snapshots stay disabled except for errors.
- Actual account events (`OPEN`, `ADD`, `CLOSE`, `PARTIAL_CLOSE`, `SETTLED`)
  write compact raw evidence snapshots by default. Normal decisions and ticks
  still do not write raw snapshots unless debug mode is enabled.
- Do not apply diagnostic cleanup rules to `paper_state.json`,
  `paper_trades.csv`, or `paper_decisions.csv`.
- Minimum reports stream ledger rows and separate trusted executable-depth net
  PnL from reference-only PnL, liquidity/stale blockers, signal, shape, city,
  and high/low breakdowns.
- Individual market evaluation exceptions fail closed as observable
  diagnostics: write a `SKIP_ERROR` row, write an error raw snapshot, and keep
  runner-status error fields.
- Logrotate compresses diagnostics, not core ledgers: raw snapshots at 100 MB,
  request/portfolio logs at 10 MB, five archives under `data/archive/`.
- `docs/codex/known-good-commands.md` is the source for pytest, SSH, and dashboard checks.
