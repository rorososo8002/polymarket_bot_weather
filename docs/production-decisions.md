# Production Decisions

This is the active rule book for the paper-only Polymarket temperature bot.
History belongs in a matching `docs/solutions/` note; active work belongs in
`docs/active/current-task.md`. Evidence and readiness gates are in
`docs/paper-validation-runbook.md`.

## 1. Safety Boundary

The current phase is `paper-only strategy validation`. Never add or enable a
wallet, private key, signing, real order, redemption, claim, copy trading, or a
hidden live path. Passing paper gates does not authorize live trading; it only
permits discussion of a separate live-trading safety project.

Paper PnL is credible only when entry uses executable ask depth, exit uses
executable bid depth, fees and slippage are included, official settlement-
station evidence is replayable, and uncertain inputs fail closed.

## 2. Supported Markets

- Temperature markets only.
- Use `TRADING_READY_STATION_MAP`; Karachi remains excluded until its station
  rule is reconciled.
- Verify question, rule/source, station, unit, bucket shape, station-local date,
  UTC window, outcome labels, and token IDs. Any conflict blocks the market.
- Unsupported, unknown, stale, malformed, missing, suspicious, or conflicting
  evidence must stop before signal calculation, book subscription, or trade
  logging.

The measurement day is the settlement station's local `00:00` through the
instant before its next local `00:00`. Never substitute a UTC calendar day.
DST zones must use timezone-aware local-day boundaries.

## 3. Official Observations

Entries come from the mapped official settlement station, not generic weather
forecasts. The realtime runner refreshes every trading-ready station on its
cache cadence even when no order-book price changes occur.

AWC METAR rules:

- Poll the bulk API no more than once per minute; one response covers supported
  ICAO stations.
- Request documented `hours=4` as a restart bridge. The persistent station
  history, not one response, supplies the complete local-day maximum/minimum.
- A response reaching the 400-row provider cap is incomplete and blocks entry.
- A station report may be up to 90 minutes old because routine METAR publication
  is commonly hourly; polling every minute often returns the same report.
- Persist two station-local dates and the first observation timestamp at which
  each daily high/low was reached. A restart gap, missing baseline, date
  regression, or incomplete day blocks entry.
- Seoul RKSI and Busan RKPK use this same AWC path.

HKO rules:

- Poll the official since-midnight max/min source no more than once per 10
  minutes; accept a row for at most 20 minutes.
- Persist the previous day's final max/min and two local dates of first-
  confirmed extreme timestamps.
- After midnight, block until the new pair is proven reset. An unchanged prior-
  day pair is not evidence of reset.
- After reset, a same-day high decrease or low increase blocks HKO immediately.
  A restart without the prior-day baseline fails closed.
- Do not invent a fixed allow-after clock time.

Provider failures must respect retry floors. Do not retry-bomb a source.

## 4. Formation And Residual Probability

Read the verified station/month/direction metadata from the residual profile
and its manifest:

- `monitoring_start_local_minute`
- high and low final-formation q25 / median / q75
- residual movement histogram and sample count

Before monitoring starts, ordinary residual entries are blocked. Exact-bucket
entries also wait for that direction's q75 formation minute. High exact
residual entries additionally require station-local 16:00, two observations in
the same integer high bucket, and a later lower observation confirming the high
has rolled over. A reset-verified observation that has already crossed an exact
bucket's irreversible boundary may produce strong NO earlier.

Profiles are stored on 30-minute checkpoints. When the current local minute has
no exact checkpoint, use the latest available checkpoint at or before now.
Never use a future checkpoint. Missing, thin, wrong-unit, or hash-mismatched
profiles fail closed.

The probability must combine the current complete local-day high/low with how
often that station, month, direction, and time historically moved farther.
"Current temperature" alone is never enough.

## 5. Settlement Precision And Buckets

Every station/source has an explicit unit, reporting precision, bucket model,
confidence, and note.

- Whole-degree display: integer `N` uses `[N.0, N+1.0)`.
- One-decimal display: use a range-containing interpretation only when audited
  rules or settlements support it.
- Unknown precision blocks entry.

HKO remains `one_decimal_range_containing / needs_audit`. Until historical
Polymarket outcomes are reconciled with HKO Absolute Daily Max/Min, do not treat
its result as 100% certain or give it concentrated size.

For a daily high exact `N`, `observed_high >= N+1.0` makes YES impossible. For
a daily low exact `N`, `observed_low < N` makes YES impossible. Tail markets
must follow their exact wording; above, at-or-above, below, and at-or-below are
not interchangeable.

## 6. Final CLOB Tradability

Gamma `endDate` is not proof that orders are accepted. Immediately before a new
paper entry require:

- market active, not closed, and not archived;
- CLOB `accepting_orders=true` and `enable_order_book=true`;
- valid YES/NO token IDs;
- fresh executable ask depth and final size-aware VWAP;
- spread, fee-aware edge, expected return, cash, and exposure gates;
- fresh complete same-station evidence.

Unknown tradability, disabled books, or rejected orders receive stable SKIP
reasons. If an actual CLOB close time precedes historical high formation, block
the high strategy and prefer a feasible low strategy. Do not infer this from
Gamma `endDate` alone.

## 7. Execution Realism

The CLOB WebSocket market stream is primary. REST `/book` is only a bounded
seed, verification, or resync helper. PING/PONG proves a socket exists, not that
its executable depth is fresh; rebuild a stale stream even if its thread lives.

- Entry price = ask-side executable VWAP for the final size.
- Exit price = bid-side executable VWAP for the final close size.
- No bid depth means HOLD, never a fake zero-price close.
- Partial depth means partial close or hold, never a fake full close.
- Indicative best prices and midpoints are not fills.

## 8. Strategy And Sizing

Default mode is `hybrid_observation_edge`. Allowed signal families are
`lock_only`, `intraday_observation_edge`, and
`abnormal_official_station_mispricing`.

- Below 90% calibrated selected-side probability: ordinary city exposure stays
  at or below 20% of bankroll.
- From 90% to below 95%: target 30% of bankroll for one exclusive city-date
  position.
- At or above 95%: target 50% of bankroll for one exclusive city-date position.

These are paper allocation targets, not permission to ignore liquidity. Final
VWAP impact, positive fee-aware edge, complete observations, CLOB status, cash,
and the 50% single-market ceiling may reduce or block a fill. HKO `needs_audit`
cannot use concentrated residual sizing.

Reuse a fresh cached official signal at final pre-trade and recalculate the
CLOB economics. Refetch the station only after its evidence TTL expires; do not
create a second provider-failure opportunity seconds after a valid fetch.

## 9. Portfolio And Exit Safety

- Opposite sides of the same market are blocked.
- Same-side add-ons must pass all current price, probability, edge, cash, and
  exposure gates.
- Correlated city-date buckets share one risk budget. Different NO buckets are
  not automatic diversification.
- Concentrated sizing requires one exclusive city-date position.
- A closed position cannot rotate into a sibling bucket using the identical
  station observation timestamp; require newer evidence.
- `SKIP` means no valid new probability. It is not NO and must never be
  complemented into an exit signal.
- Drawdown breakers block entries only; exits and settlements continue.

## 10. Ledgers And Runtime Evidence

`paper_state.json` is the paper account book: cash, positions, cost, and PnL.
`paper_trades.csv` is the execution receipt ledger. `paper_decisions.csv` is the
strategy evidence ledger. They are not disposable cache files, and corruption
or disagreement fails closed instead of silently creating a fresh account.

New rows retain strategy family, market identity, station/date evidence,
observed extremes, side probability, VWAP, spread, fees, expected return,
precision confidence, and stable reason. Old rows missing newer columns remain
readable.

High-volume raw snapshots and SKIP diagnostics are bounded, rotated, and safe
to delete only under the runtime cleanup policy in `docs/codex/runtime-data.md`.

## 11. Validation

The paper report must separate realistic net PnL, opens/closes/partial closes,
liquidity and tradability blockers, strategy family, city, high/low direction,
precision confidence, and HKO `needs_audit` exposure. A short profitable result
without these breakdowns is not evidence.

Behavior changes require a failing regression test first, focused tests, the
full local suite, and the full Oracle VPS suite before service restart. Keep the
paper-only boundary and the readiness gates in `docs/paper-validation-runbook.md`.
