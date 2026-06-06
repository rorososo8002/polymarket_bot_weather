# Production Decisions

This file is the current decision ledger for fresh-chat handoff. Keep only
active rules here. Historical decision process belongs in `docs/archive/`;
durable prevention lessons belong in `docs/solutions/`.

## Execution Boundary

- Paper-only execution is the boundary. No private keys, wallet connection,
  signing, real orders, automatic copy trading, private data collection, or live
  deployment are allowed without explicit approval and a separate
  `docs/live-trading-safety-plan.md` pass.
- Public dashboard exposure requires a real `DASHBOARD_TOKEN` with at least 32
  characters and not an obvious example value. Public `/api/status` accepts the
  token only through `X-Dashboard-Token`; public `?token=...` API
  authentication is rejected because URLs leak through history, logs, copied
  links, and screen sharing.
- Boolean, numeric, integer, and choice environment settings fail closed at
  startup when malformed or outside safe ranges. A typo must stop the runner
  before it contaminates paper-performance evidence.

## Market Universe And Forecasts

- The bot registers only the 41 cities in `STATION_MAP`, but paper execution
  uses only `TRADING_READY_STATION_MAP`: 40 cities with stored official
  Polymarket rule evidence and no known station-code conflict. Karachi remains
  excluded because current registry/rule evidence conflicts.
- Execution is temperature-only. Rain, snow, precipitation, wind, humidity, and
  other non-temperature weather markets are outside the paper strategy and must
  not reach forecast probability calculation, order-book subscription, or paper
  trade logging.
- Unknown, stale, malformed, unsupported, suspicious, missing, or conflictful
  data means skip. The strategy must fail closed instead of guessing.
- Forecast rows must match the target market date exactly. Nearby forecast
  dates are not substitutes and produce `forecast-unavailable`.
- `pre_forecast_tradeability_gate` rejects markets before Open-Meteo when they
  are not temperature-shaped, not trading-ready, or missing required
  `date_hint` evidence. SKIP diagnostics are recorded without spending forecast
  API budget.
- `WEATHER_BIAS_JSON` is optional calibration evidence. Empty means neutral
  defaults; an explicit missing, unreadable, invalid, malformed, or non-numeric
  file produces `forecast-unavailable` with zero confidence.
- Real Open-Meteo forecast HTTP calls are globally drip-fed by
  `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60`: one real request must finish or
  timeout, then at least 60 seconds pass before the next real request starts.
  Cache hits do not count as calls. Values below 60 seconds fail startup.
- `forecast_cache.json` is an answer cache, not the call ledger. Real
  Open-Meteo attempts are recorded in `forecast_request_log.jsonl`. HTTP 429
  cooldowns are stored in `forecast_rate_limit_state.json`; daily quota and
  concurrent-request cooldowns are classified separately.
- Open-Meteo `ReadTimeout` is handled as a per-forecast-key temporary miss. Do
  not immediately retry the same key; other city/station/date keys may continue.

## Realtime Order Books

- Use the Polymarket CLOB WebSocket market stream by default. Do not silently
  replace realtime streaming with polling.
- `websocket-client` must be importable before the paper runner starts a market
  stream. A missing WebSocket dependency fails closed and records the import
  failure in runner status WebSocket health instead of hiding it in a background
  thread traceback.
- Keep token IDs for open positions subscribed even when discovery moves to
  newer markets.
- Discovery maps YES/NO token IDs only from explicit outcome labels. If
  `tokens` or `outcomes` cannot prove the YES and NO side for `clobTokenIds`,
  skip the market instead of trusting list order.
- Closed or inactive Polymarket markets are not new-entry candidates. Closed
  markets remain settlement evidence for already-held paper positions only.
- `best_bid_ask` messages are indicative reference quotes only. Executable
  bid/ask depth comes from `book` snapshots and `price_change` updates.
- A `price_change` is only a delta. It may update executable depth only after
  that token has already received an initial `book` snapshot in the current
  WebSocket cache.
- WebSocket freshness means executable-depth freshness. Stale or dead
  executable depth blocks new entries and pauses held-position exits with
  observable reasons. Held-position marking and close evaluation also require
  fresh executable depth for that position's own `token_id`.
- REST and WebSocket order-book levels share defensive numeric guards. Bad
  prices, bad sizes, malformed snapshots, zero executable size, negative values,
  non-finite values, and out-of-range token prices are ignored or fail closed.

## Strategy, Risk, And Accounting

- `DECISION YES` and `DECISION NO` are model/order-book judgments, not
  guaranteed opens. Broker exposure, hedge, confidence, liquidity, fee, and
  stale-data gates may still block entry.
- Entry decisions are fee-aware. `p_exec` is executable VWAP; `size_usd` is the
  all-in paper-entry budget; `size_shares` is the actual fee-adjusted share
  count. Entry ask-depth checks must use the final computed order size, and
  edge/fees/expected return must be recalculated if final VWAP changes.
- If final ask depth is short but at least `MIN_ORDER_USD` is executable, the
  runner may scale down the paper entry. Below minimum depth means skip, not a
  fake fill.
- Same-market opposite-side entries remain blocked. Same-side add-ons are
  allowed only when price, probability, edge, expected return, cash, and
  exposure caps all still pass.
- City-date weather buckets share one correlated-risk budget. At most two
  complementary legs are selected per event. Portfolio allocation-size
  candidates stay bounded so event selection cannot explode computationally.
- Event portfolio `scenario_probabilities` are normalized only when parsed
  temperature intervals are non-overlapping and exhaustive. Incomplete sets keep
  `other`; overlaps or impossible sums fail closed.
- Temperature range markets preserve displayed inclusive endpoints exactly,
  such as `86.0 <= temperature_f <= 87.0`.
- Exit decisions use after-fee liquidation PnL, not raw token-price movement.
  Probability stop, take profit, overheated profit, edge-faded exit, max hold,
  settlement, and nowcast bucket-lock risk are the allowed exit paths.
- If an exit signal fires but the close cannot execute, the broker logs the
  blocker (`HOLD_NO_LIQUIDITY` or `HOLD_STREAM_UNHEALTHY`) and preserves the
  original `exit_trigger`; it must not pretend to sell.
- Profit exits may recover principal and keep a bounded settlement runner only
  when conservative settlement value beats fee-adjusted sell-now value. Runners
  are rechecked and are not risk exemptions.
- Resolved paper settlement requires a proven binary winner. Explicit winner
  fields are preferred; exact closed-market YES/NO `outcomePrices` of `1/0` or
  `0/1` are accepted. Ambiguous closed prices are not guessed.
- `paper_state.json` is the account book, not a disposable cache. Existing
  corrupt, structurally invalid, or unsafe state fails closed instead of
  resetting.
- `paper_state.json` and `paper_trades.csv` are paired ledgers. `OPEN`, `ADD`,
  `CLOSE`, and `PARTIAL_CLOSE` use `paper_state.json.journal` and must fail
  closed if state and trade rows may disagree.

## Nowcast And Research

- Same-station nowcast is allowed only from explicitly mapped official sources.
  AWC METAR covers the ICAO stations; HKO covers Hong Kong. No nearby-station or
  city-center substitutions.
- Nowcast derives observed high-so-far and low-so-far from the same station-date
  response when possible. Daily-high markets use observed high; daily-low
  markets use observed low.
- The target date may be the station's local today, or local yesterday only
  during the post-close freshness window for already-held paper exit and
  settlement-risk evidence. This is not a new-entry booster.
- AWC METAR rows must carry an explicit station ID matching the requested
  settlement station. Rows without `icaoId` or `station_id` are invalid
  evidence.
- For held NO positions in exact/range buckets, same-station observed high/low
  inside the bucket creates an exit-only `nowcast_bucket_lock_risk` signal.
- Shadow/public external signals remain research-only. Promotion requires at
  least 20 paired resolved rows and at least a five-percentage-point edge over
  matched bot entries, then only a paper-only A/B experiment.
- Repeated SKIPs are research signals. Diagnose categories before changing
  thresholds, risk caps, or data-source assumptions.

## Runtime Data And Handoff Hygiene

- `docs/active/current-task.md` is the only default unfinished-work handoff
  card. It must be replace-only, short, and set back to `Status: none` when no
  work remains. Do not append completed history to it.
- `paper_decisions.csv` and `paper_trades.csv` are source ledgers. Reports may
  scan full history when that is the promised meaning, but must stream rows and
  keep only aggregates or bounded lookups in memory.
- `paper_raw_snapshots.jsonl` is diagnostic evidence, not a source ledger.
  Normal raw snapshots are disabled by default, error/debug snapshots rotate,
  and disk pressure may suspend raw writes. Do not apply raw-snapshot cleanup to
  `paper_state.json`, `paper_trades.csv`, or `paper_decisions.csv`.
- Dashboard trade-history panels treat SKIP rows as diagnostics, not executed
  trades. Dashboard scanner totals must disclose whether counts are exact full
  ledger totals or bounded recent-tail totals.
- `docs/codex/known-good-commands.md` is the command source. Fresh local pytest,
  Oracle SSH, remote pytest, and dashboard checks should start there.
- This file must stay compact. Historical ledger entries removed from the
  default handoff are summarized in
  `docs/archive/production-handoff-history-2026-06-07.md`.
