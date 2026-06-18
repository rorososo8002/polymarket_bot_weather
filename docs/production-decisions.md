# Production Decisions

This is the active paper-bot rule book; historical notes belong in focused `docs/solutions/` entries.

## Current Phase: Strategy Validation First

- Current work is paper-only strategy validation; live discussion requires `docs/paper-validation-runbook.md` gates plus a safety project.
- Do not build wallet, private-key, signing, real-order, redemption, claim/copy-trading, or `LiveBroker` behavior in this phase.
- Trust paper PnL only from executable ask/bid depth, fees, spread, slippage, stale-data fail-closed behavior, official station nowcast, and replayable ledgers.
- Advanced dashboards, calibration/optimizer views, heatmaps, and live trading stay deferred until `docs/strategy-validation-roadmap.md` P0 gates pass.

## Execution Boundary

- Paper-only execution is the boundary: no keys, wallet, signing, live orders, redemption, copy trading, or private data without a live-safety pass.
- Public dashboard exposure requires a real `DASHBOARD_TOKEN` with at least 32 characters. Public `/api/status` must accept the token only through `X-Dashboard-Token`; URL query tokens leak through logs, history, and shares.
- Boolean, numeric, integer, and choice settings fail closed at startup when malformed or outside safe ranges.

## Market Universe And Station Evidence

- `STATION_MAP` registers 49 cities; paper execution uses only the 48-city `TRADING_READY_STATION_MAP`. Karachi stays excluded until evidence is fixed.
- Execute temperature markets only; non-temperature markets must not reach station-signal calculation, order-book subscription, or paper trade logging.
- Unknown, stale, malformed, unsupported, suspicious, missing, or conflictful data means skip.
- Market title parsing is not enough rule evidence. Preserve question, available rule/source text, station, unit, bucket shape, and station-local date window; title/rule conflicts mean skip.
- Gamma discovery normalizes rule evidence into market metadata; city, high/low, unit, bucket, date, or explicit-station conflicts fail before station evaluation with `SKIP_RULE_MISMATCH`.
- Recurring temperature events may expose one title plus labels such as `28°C`; discovery must synthesize binary questions from title+label. Grouped event-level rules may describe station/source/precision/UI toggles rather than each bucket, so compare bucket shape/value only when rule text explicitly states the outcome condition.
- Station metadata must keep unit, precision, same-station support, confidence grade, verification date, and confidence level explicit.
- Only station confidence grades A/B may enter `TRADING_READY_STATION_MAP`; grades C/D, unsupported providers, inferred, nearby, or unverifiable sources remain excluded. Karachi stays excluded until station evidence is reconciled.
- Market metadata must carry the station-local event date plus UTC start/end window. Station observations must match that local date exactly; nearby dates are not substitutes.
- Temperature bucket settlement uses centralized millifahrenheit boundaries.
  Exact settlement means the displayed value only. Whole-degree Celsius exact
  strategy uses source-display band `[N.0C, N+1.0C)`: `23.7C` is still `23C`;
  `24.0C` breaks it.
- `pre_station_tradeability_gate` rejects markets before station-signal work when they
  are not temperature-shaped, not trading-ready, or missing required date
  evidence. Undated markets always fail closed.
- Entry evidence comes from official settlement-station observations, not
  external weather-model calls. Freshness is judged from station observation
  timestamps and provider floors.

## Realtime Order Books

- Use the Polymarket CLOB WebSocket market stream by default. Do not silently
  replace realtime streaming with polling.
- Zero streamable temperature tokens is WAITING/no-market, not WebSocket failure.
  Show executable-depth failure only for a real token or concrete WS error.
- Realtime startup starts WebSocket after the temperature token set is known,
  then attaches official-station observation signals from the evaluator path.
- Polymarket category-page discovery may parse many event slugs, but detailed
  `/events/slug/...` fetches are capped at 80 per discovery cycle and lower
  explicit `max_pages * page_size` budgets must be honored.
- Missing or stale official-station signals block new entries until the
  evaluator refreshes same-station nowcast evidence. WebSocket callbacks must
  not make weather HTTP calls.
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
- Planned 40-minute stream rebuilds must discard pending realtime evaluator work before stopping the old WebSocket stream. Old-window queue entries must not create `HOLD_STREAM_UNHEALTHY` rows after the receiver has been intentionally stopped.
- REST order-book snapshots may seed or resync the WebSocket cache at a bounded
  interval. They are verification photos, not the realtime camera: WebSocket
  remains primary, REST snapshots must not trigger evaluations, and raw
  snapshots must not be written to runtime ledgers.

## Strategy, Risk, And Accounting

- `DECISION YES` and `DECISION NO` are station-signal/order-book judgments, not
  guaranteed opens. Broker exposure, hedge, confidence, liquidity, fee, and
  stale-data gates may still block entry.
- A new entry must survive a final pre-trade check: fresh executable book,
  enough ask depth, configured absolute/percentage spread limits, still-positive
  after-fee edge, no conflict with held positions, exposure room, rule clarity,
  and non-stale official-station inputs. A spread failure uses `SKIP_WIDE_SPREAD`.
- Entry decisions are fee-aware: `p_exec` is executable VWAP, `size_usd` is the
  all-in paper budget, and `size_shares` is fee-adjusted. Defaults stay
  exploratory but positive-EV: `MIN_NET_EDGE=0.08`,
  `ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.04`.
- Active paper defaults are official-lock-only: `BANKROLL_USD=200`, `$10`
  minimum, 50% single-market, 50% city/date, 90% total.
- Official same-station settlement-lock entries override Kelly sizing to 20%
  for base near-close exact-bucket YES survival or 50% for strong lock signals.
  They still require depth, fees, spread, positive after-fee edge, expected
  return, portfolio caps, and final pre-trade recheck.
- Signal confidence is sizing evidence, not a license to guess. Non-lock or
  stale station signals block new entries while held exits and settlements
  still run.
- In Kelly mode, `ENTRY_FRACTION` is a per-event cap, not direct order size.
- Same-market opposite-side entries remain blocked. Same-side add-ons are
  allowed only when price, station-side probability, edge, expected return, cash, and
  exposure caps still pass.
- City-date buckets share one correlated-risk budget: at most two
  complementary non-overlapping legs; hidden overlap and exact dust fail closed.
- Exit decisions use after-fee liquidation PnL, not raw token-price movement.
  Allowed exits: probability stop, take profit, overheated profit, edge faded,
  max hold, settlement, and nowcast bucket-lock risk.
- Profit-taking exits require `MIN_PROFIT_PCT=0.08` after fees. Probability
  stop is defensive cleanup, not take-profit.
- Nowcast bucket-lock risk blocks new entry as well as triggering exit. If it
  fires before order placement, convert the candidate to SKIP.
- If an exit signal fires but the close cannot execute, log the blocker and
  preserve the original `exit_trigger`; do not pretend to sell.
- Whole-stream order-book failure blocks new entries. One missing/illiquid held
  token is worth $0 in `liquidation_bankroll`, not a global unrelated block.
- Drawdown circuit breakers block new entries only; held exits and settlements
  must continue. Active paper daily and large-loss stops are 50% of bankroll,
  not a fixed $20 stop.
- Profit exits may hold the full position as a settlement runner when
  conservative settlement value beats sell-now value; active default is
  `SETTLEMENT_RUNNER_MAX_FRACTION=1.00`.
- Resolved paper settlement requires a proven binary winner. Ambiguous closed
  prices are not guessed.
- `paper_state.json` is the account book, not a disposable cache. Existing
  corrupt, structurally invalid, or unsafe state fails closed instead of reset.
- `paper_state.json` and `paper_trades.csv` are paired ledgers. Startup replays
  executed trade rows against `BANKROLL_USD` and fails closed on mismatch.
- New decision/trade rows must carry compact replay evidence: token/city,
  station-local date, shape, station evidence, signal, VWAP, return, reason,
  and model/config version. Old ledger rows stay readable.
- Normal SKIP spam stays suppressed by default, but reports should aggregate
  stable reason codes such as `SKIP_WIDE_SPREAD`.
## Nowcast And Station Evidence

- Same-station nowcast is allowed only from explicitly mapped official sources:
  AWC METAR for ICAO stations and HKO for Hong Kong.
- Real AWC METAR bulk requests must be at least 60 seconds apart; real HKO
  max/min requests stay at least 10 minutes apart. Cache hits do not write
  request-log rows, and the dashboard must show these provider floors.
- `STATION_NOWCAST_CACHE_TTL_SECONDS=60` is the recommended nowcast TTL; HKO stays protected by its 10-minute provider floor.
- Dashboard station views must expose the full supported settlement-station
  registry. Display-only alternates such as KMA Seoul ASOS 108 are reference
  context only unless Polymarket rules name that station.
- Dashboard views are official-station-first: show station/source, observations, time, settlement boundary, lock strength, 20%/50% allocation, skip reason, bid-depth PnL, exit liquidity, and WS freshness. The
  side badge must be the plain Polymarket outcome label `Yes` or `No`; do not
  add `Long`, `Short`, or `보유` because the bot only buys outcome tokens and
  those words imply a separate margin direction that does not exist here.
- The target date may be station-local today, or station-local yesterday only
  during the post-close freshness window for held-position exit and settlement
  evidence.
- For daily-high thresholds, `observed_high_c >= threshold_c` favors held YES
  and triggers held NO `nowcast_bucket_lock_risk`. Exact/range buckets use
  settlement text directly: exact is the displayed value only, range is the
  displayed inclusive endpoints; never widen exact to `28.5C-29.5C`.
- Exact Celsius station evidence uses source-display integer settlement. For a
  whole-degree `23C` bucket, the active strategy treats
  `23.0C <= official_value < 24.0C` as the displayed `23C` outcome and treats
  `24.0C` as the daily-high `23C` break point.
- For official same-station nowcast on daily-high exact Celsius markets:
  `observed_high_c >= bucket_c + 1.0` makes the exact-bucket YES impossible and
  creates a strong NO settlement-lock signal. If the station remains inside
  `[bucket_c, bucket_c + 1.0)` near the local event close, YES may become a
  settlement-lock signal: 20% when there is enough buffer to the next integer
  and 50% when the buffer is strong. The daily-low version is symmetric around
  the lower displayed integer.
- For daily-high exact/range held YES positions, same-station nowcast makes
  YES impossible only after the observed high is above the exact value or range
  upper endpoint; a lower observed high is not decisive. For daily-low
  exact/range held YES, same-station nowcast makes YES impossible only after
  observed low is below the exact value or range lower endpoint.

## Runtime Data And Disk

- Runtime ledgers are ignored by git and live under `data/`; recreate them only
  for an intentional fresh paper experiment.
- `paper_runner_status.json` is a concurrent heartbeat; writes must use
  collision-resistant temp paths before atomic replace.
- `paper_decisions.csv` suppresses SKIP rows by default. Continuous SKIP
  tracing belongs in bounded `paper_skip_diagnostics.jsonl`, not the decision
  ledger.
- `paper_event_portfolios.jsonl` writes only when at least one trade is
  selected by default. For bounded investigations, `PORTFOLIO_LOG_SKIP_ENABLED`
  may be enabled so zero-selection portfolio rows record compact rejection
  counts and samples. Pair this with archive pruning because skip portfolio
  diagnostics can grow quickly.
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
- Logrotate compresses diagnostics, not core ledgers: raw snapshots and SKIP
  diagnostics at 100 MB, request/portfolio logs at 10 MB, five archives under
  `data/archive/`. `runtime_cleanup` may delete only known diagnostic archives
  when their combined size exceeds 100 MB, never account or decision ledgers.
- `docs/codex/known-good-commands.md` is the source for pytest, SSH, and dashboard checks.
