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
- AWC METAR:
  - Poll bulk API at most once per minute; one response covers supported ICAO
    stations.
  - Request official `hours=4` only as restart bridge. Persistent station
    history supplies the full local-day high/low.
  - 400-row response means likely truncated; block entry.
  - METAR reports may be 30-90 minutes old; polling every minute can return the
    same station report.
  - Keep two station-local dates and first timestamps for daily high/low.
    Restart gap, missing baseline, date regression, or incomplete day blocks.
  - Seoul RKSI and Busan RKPK use this AWC path.
- HKO:
  - Poll official since-midnight max/min at most once per 10 minutes.
  - Block after midnight until reset is proven. Same prior-day pair is not reset.
  - After reset, same-day high decrease or low increase blocks HKO.
  - Restart without prior-day baseline fails closed.
  - Do not invent fixed allow-after times.

## 3. Formation And Probability

- Use residual profile + manifest by station/month/direction:
  `monitoring_start_local_minute`, high/low q25/median/q75 formation times,
  remaining movement probability, sample count.
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
- Fresh REST helper snapshots with executable bid/ask depth may refresh per-token
  freshness and wake exit evaluation, but dashboard must label them REST helper
  depth, not millisecond WebSocket depth.
- Held-position tokens must stay subscribed and be checked first even if market
  discovery omits the market or returns incomplete YES/NO token pair.

## 6. Sizing

Default mode: `hybrid_observation_edge`.

Allowed signal families:

```text
lock_only
intraday_observation_edge
abnormal_official_station_mispricing
```

Sizing targets before liquidity/edge/cash cuts:

- Non-lock residual probability entries are capped at 20% of bankroll per
  ordinary city exposure, even above 90% or 95%.
- Only verified lock-only exact high NO can override this and use up to all
  remaining cash when executable VWAP and net-return gates pass.

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
- Correlated city-date buckets share one risk budget.
- A closed position cannot rotate into a sibling bucket with the same station
  observation timestamp; require newer evidence.
- `SKIP` means no valid new probability. Never complement it into an exit signal.
- Drawdown breakers block entries only; exits and settlements continue.

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
