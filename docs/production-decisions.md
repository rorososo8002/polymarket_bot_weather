# Production Decisions

Active rule book for the paper-only temperature bot. Keep this file short.
Move history, investigations, and long explanations to focused `docs/solutions/`
notes only when they prevent a repeated mistake.

## 1. Safety

- Paper-only. No wallet, private key, signing, real order, redemption, claim,
  copy trading, or hidden live path.
- Temperature markets only.
- Unknown, stale, malformed, conflicting, unsupported, wrong-station, or
  unverified evidence fails closed before signal, book subscription, or trade.
- Measurement day is settlement-station local `00:00` to just before next
  local `00:00`. Never use UTC date as a shortcut.

## 2. Official Observations

- Entries use the mapped official settlement station, not generic forecasts.
- Production `lock_only` entries use the Wunderground daily-history response
  that the market names as its resolution source. The request is made with the
  mapped station coordinates, every returned `obs_id` must match the registry
  station, response units must be explicit, physically impossible sentinel
  temperatures are rejected, and the local-day high/low is rebuilt from the
  full response each time. `lock_only` refuses to start without
  `WUNDERGROUND_API_KEY`; it must not look healthy while silently producing zero
  entries. Wrong-station rows, malformed data, stale data, or a source other
  than Wunderground blocks the entry.
- AWC/KMA METAR can remain useful monitoring evidence in non-production
  experiment modes, but it is not called 100% settlement evidence for a
  Wunderground market and cannot open a production `lock_only` position.
- Korean domestic METAR:
  - Seoul RKSI and Busan RKPK use the KMA direct METAR API first when
    `KMA_METAR_SERVICE_KEY` is configured. Check for newly published reports
    every 30 seconds while the runner checks its local cache every 5 seconds.
  - Give the direct KMA request at most 3 seconds. On timeout or malformed
    response, suppress another KMA attempt for one poll interval and use AWC so
    one slow domestic request cannot hold every following city.
  - If persisted history has a gap, apply AWC recovery rows chronologically
    before the newer KMA row. Wrapper ICAO, raw METAR ICAO, and success code
    must agree; conflicts fail closed.
  - Keep AWC as recovery and network-failure fallback. A missing, malformed, or
    wrong-station KMA report never becomes trade evidence by itself.
- AWC METAR:
  - Background monitoring polls the bulk API at most once per minute; one response covers supported ICAO
    stations.
  - Final entry validation discards derived per-station results only after the
    official one-request-per-minute floor has elapsed. Inside that floor it
    reuses the newest allowed bulk response instead of risking an API block;
    after the floor it performs one new official request for the selected batch.
    Network error, truncated response, or missing station data from that allowed
    real request blocks every affected entry, including lock NO. A failed response
    is unavailable evidence, never a reason to reuse an older successful response.
  - Request official `hours=4` only as restart bridge. Persistent station
    history supplies the full local-day high/low.
  - 400-row response means likely truncated; block entry.
  - METAR reports may be 30-90 minutes old; polling every minute can return the
    same station report.
  - Learn each station's next-report interval from recent actual observation
    timestamps. When that learned boundary has passed but the next report has
    not arrived, pause ordinary probability entries only; irreversible verified
    lock NO remains allowed. Do not invent a global 30/60-minute schedule.
  - Keep two station-local dates and first timestamps for daily high/low.
    Restart gap, missing baseline, date regression, or incomplete day blocks.
  - Seoul RKSI and Busan RKPK use this path when the KMA key is absent or its
    direct request fails.
- HKO:
  - Direct Wunderground station requests run independently. A completed changed
    city is released to the urgent evaluator immediately; it never waits for the
    slowest city. The separate HKO request also cannot delay a completed direct
    station decision or a concurrent station-cache read.
  - Poll official since-midnight max/min at most once per 10 minutes.
  - Block after midnight until reset is proven. Same prior-day pair is not reset.
  - After reset, same-day high decrease or low increase blocks HKO.
  - Restart without prior-day baseline fails closed.
  - Do not invent fixed allow-after times.
- NOAA WRH timeseries:
  - Markets resolving from `weather.gov/wrh/timeseries` need direct `Temp`
    table verification. AWC/METAR evidence alone is not settlement evidence, so
    these markets fail closed until a direct WRH verifier exists.

## 3. Formation And Probability

- Use residual profile + manifest by station/month/direction:
  `monitoring_start_local_minute`, high/low q25/median/q75 formation times,
  remaining movement probability, sample count.
- Preferred calibration years are complete local years 2021-2025. NCEI ended
  ISD in August 2025, so exact-ICAO stations use its official GHCNh successor
  for complete 2025 observations. Stations without an exact GHCNh ICAO mapping
  retain complete 2020-2024 profiles; never substitute a nearby station.
- Before monitoring start, ordinary residual entries are blocked.
- Exact-bucket entries wait for that direction’s q75.
- High exact residual entries also require local 16:00, two observations in the
  same integer high bucket, and later lower observation confirming rollover.
- Low exact residual YES entries require later higher observation confirming
  rollover.
- If same-day verified observation already breaks an exact bucket irreversibly,
  strong NO can happen earlier.
- Missing/thin/wrong-unit/hash-mismatched profiles fail closed.
- Current temperature alone is never enough.
- Recompute every intraday observation signal immediately before the paper fill;
  a recently created candidate is not a substitute for final station revalidation.
- Final station revalidation may reduce or cancel the portfolio-selected order,
  but it must never increase the already selected dollar allocation.

## 4. Precision And Buckets

- Whole-degree display: integer `N` means `[N.0, N+1.0)`.
- High exact `N`: `observed_high >= N+1.0` makes YES impossible.
- Low exact `N`: `observed_low < N` makes YES impossible.
- Tail markets must follow exact wording: above, at-or-above, below, and
  at-or-below are different.
- HKO decimal markets remain `needs_audit`; no 100% certainty or concentrated
  sizing until settlement audit proves bucket mapping.

## 5. CLOB And Execution

- Gamma `endDate` is not enough. Before entry require active market, not closed
  or archived, CLOB `accepting_orders=true`, `enable_order_book=true`, valid
  YES/NO tokens, fresh executable ask VWAP, spread/fee/edge/cash/exposure gates,
  and fresh complete station evidence.
- Entry price = executable ask-side VWAP.
- Exit price = executable bid-side VWAP.
- No bid depth = HOLD. Partial depth = partial close or HOLD. Never fake a zero
  close or midpoint fill.
- WebSocket market stream is primary. REST `/book` is bounded seed/resync helper.
  PING/PONG proves connection, not fresh executable depth.
- If the modeled entry side has no WebSocket snapshot or a crossed book, refresh
  only that side with REST. Recheck every finally selected entry with REST before
  the paper fill. A crossed book is never executable evidence.
- WebSocket subscribes only held positions and markets whose measurement date is
  the station's current local date. Future-date markets cannot have valid
  same-day station evidence yet and must not bloat the stream.
- Fresh REST helper snapshots with executable bid/ask depth may refresh per-token
  freshness and wake exit evaluation, but dashboard must label them REST helper
  depth, not millisecond WebSocket depth.
- Held-position tokens must stay subscribed and be checked first even if market
  discovery omits the market or returns incomplete YES/NO token pair.
- If the whole WebSocket is reconnecting, a fresh per-token REST helper snapshot
  may keep held-position exit evaluation running. It does not by itself reopen
  broad new-entry permissions.
- A changed official station report is an urgent evaluation trigger. Every
  affected same-day event must be queued; the normal four-event probe cap must
  never discard the fifth or later changed city. Urgent station events run
  ahead of ordinary order-book wakeups.
- Near each learned direct-report boundary, a cache age of exactly five seconds
  is expired, so a five-second runner poll produces a real five-second source
  retry rather than an accidental ten-second retry. One valid timestamp gap is
  enough to learn the next boundary; missing cadence still fails conservatively.
- Ordinary WebSocket price changes reevaluate only the changed market and held
  positions, in preemptible batches of one event. An official station
  change instead queues every supported NO token for each affected event and
  may use the full urgent batch. This prevents ordinary startup book noise from
  blocking a later station change.
- Quiet books still receive scheduled reevaluation. Crossing formation
  monitoring start, city-month q75, or station-local 16:00 is urgent; moving
  into a new residual 30-minute bin is a normal preemptible wakeup.
- The first station refresh after process start establishes the comparison
  baseline and is not counted as a change event. Each completed direct station
  still queues one startup exact-NO scan immediately, so an irreversible bucket
  that already existed before restart is not hidden until the next report.
  Only a later state difference is counted as a changed-station event.

## 6. Sizing

Default deployed mode: `lock_only` with `NO_ONLY_NEW_ENTRIES=true`.
New entries are limited to exact-temperature NO whose YES outcome has already
become impossible in the directly queried Wunderground local-day history.
Probability-only NO, range/tail NO, YES, HKO `needs_audit`, and proxy-only
METAR locks are disabled. Legacy positions remain eligible for normal
executable bid-side exit handling.
The environment-loader fallback is also `lock_only`; omitting one environment
line must never re-enable probability trading. `PaperBroker`, the final
paper-ledger writer, independently rechecks the exact-NO evidence, the `0.90`
price ceiling, and the configured settlement-return floor before changing cash.
The default event portfolio limit is two open legs per city and local date.
The two legs must be distinct exact buckets for the same metric (both daily high
or both daily low), so both NO legs cannot lose in one settlement outcome. They
share one city-date budget; the first leg cannot reserve the whole budget before
the second is considered. Range, tail, mixed high/low, and a third leg remain
blocked.

Production new-entry signal family:

```text
lock_only
```

The probability families remain available only for explicit offline experiments;
they are not allowed by the deployed production mode.

Sizing targets before liquidity/edge/cash cuts:

- Non-lock residual probability entries are capped at 20% of bankroll per
  ordinary city exposure, even above 90% or 95%.
- Only a directly verified lock-only exact high or exact low NO can use the
  shared city-date concentrated budget. Executable ask-side VWAP must be at most
  `0.90`, including the final refresh; a smaller still-profitable amount may be
  used when depth shrinks instead of discarding the whole opportunity. HKO
  `needs_audit`, tail buckets, proxy-only METAR, and NOAA WRH timeseries without
  direct `Temp` verification stay excluded.

Final executable VWAP, fee-aware edge, complete observations, CLOB state, cash,
and single-market exposure gates may reduce or block a fill. HKO `needs_audit`
cannot use concentrated sizing.

Low exact NO weather risk:

- If current low is only 1°C above selected bucket and both precipitation is
  observed and dewpoint is within 1°C of selected bucket, block entry.
- If only one weather-risk flag exists, reduce probability and cap size to 5%.

## 7. Portfolio And Exit Safety

- Block opposite sides of the same market.
- Same-side add-ons must pass current price, probability, edge, cash, and
  exposure gates.
- Same-side add-ons normally require the price to improve. Exception: if the
  current side probability is at least 95% and executable edge gates pass, the
  add-on may proceed without waiting for a drawdown.
- Correlated city-date buckets share one risk budget.
- A closed position cannot rotate into a sibling bucket with the same station
  observation timestamp; require newer evidence.
- `SKIP` means no valid new probability. Never complement it into an exit signal.
- Loss of new-entry edge alone (`edge_faded`) is not an exit signal for an
  already-held position. Close only on executable take-profit, max holding time,
  explicit bucket-lock risk, or a real probability stop.
- Drawdown breakers block entries only; exits and settlements continue.
- Paper-validation defaults disable all four drawdown entry breakers (`0`):
  daily realized loss, unrealized loss, consecutive losses, and per-city loss
  cooldown. This is an evidence-gathering experiment; exits and settlements
  remain active, and ordinary exposure/price/evidence gates still apply.

## 8. Runtime Data

- `paper_state.json`: 종이계좌 장부.
- `paper_trades.csv`: 체결 영수증.
- `paper_decisions.csv`: 전략 판단 장부.
- Do not delete/truncate those three except after explicit experiment reset.
- Raw snapshots and diagnostics are bounded by runtime cleanup policy; inspect
  sizes/counts/tails, not full files.

## 9. Validation

Experiment-readiness reporting lives in `docs/paper-validation-runbook.md`;
read it only for readiness reports or experiment resets.

Behavior changes require:

1. Failing regression test first.
2. Focused tests.
3. Full local pytest.
4. Transactional Oracle deploy.
5. Full Oracle pytest.
6. Restart services only after tests pass.

Paper profit is credible only when PnL is separated by strategy family, city,
high/low direction, probability tier, skip reason, liquidity blocker, fees, and
executable bid/ask depth.
