# Strategy Validation Roadmap

This roadmap exists to make the paper strategy honest before any live-trading project is considered.

Do not use this file as the live task queue. The live task queue is `docs/active/current-task.md`.

Long-window evidence and live-discussion gates are defined in `docs/paper-validation-runbook.md`.

---

## 1. North Star

The bot should answer one question:

```text
Did the paper strategy make money using prices, liquidity, fees, official station evidence, and ledgers that could survive a real audit?
```

It should not answer:

```text
Could we make the backtest or paper PnL look bigger by assuming fills?
```

Paper PnL is useful only when:

```text
entry used executable ask VWAP
exit used executable bid VWAP
fees/spread/slippage were included
same-station official evidence was fresh
market rules were clear
tradability was verified
ledger replay is possible
```

---

## 2. Current Strategic Upgrade

The current upgrade replaces a too-inactive lock-only posture with:

```text
hybrid_observation_edge
```

This includes:

```text
lock_only
intraday_observation_edge
abnormal_official_station_mispricing tag
```

The strategy may become more active, but every new entry still needs:

```text
CLOB tradability
executable ask depth
official station evidence
90%+ side probability for intraday edge
fee-aware positive edge
expected net return
portfolio room
ledger tags
```

---

## 3. P0 Gates

The bot is not ready for live-trading planning until these are all true.

1. New entries do not rely on `endDate` as a hard order cutoff.
2. Final pre-trade checks CLOB `accepting_orders`, `enable_order_book`, `active`, `closed`, and actual order-book depth.
3. Entry uses final ask-side executable VWAP.
4. Exit uses final bid-side executable VWAP.
5. No fake `CLOSE` when bid depth is absent.
6. Partial liquidity becomes scaled entry, `PARTIAL_CLOSE`, or a hold blocker.
7. Settlement precision profile exists per station/source.
8. HKO decimal markets are `needs_audit` until historical settlement audit proves bucket mapping.
9. Daily-high and daily-low use correct opposite nowcast directions.
10. Intraday strategy uses official same-station observations, not generic forecasts.
11. Strategy mode and signal family are recorded in ledgers.
12. Report separates `lock_only`, `intraday_observation_edge`, and `price_anomaly` performance.
13. Old ledgers remain readable.
14. All focused tests pass or failures are clearly explained as environment-only.

---

## 4. P1 Gates

1. Historical settlement audit table exists for HKO and other decimal/ambiguous sources.
2. 24-72 hour paper run shows strategy-mode PnL, skip reasons, and liquidity blockers.
3. Station/month/direction formation windows are compared with residual probabilities.
4. Abnormal price entries are reviewed separately from normal intraday entries.
5. HKO `needs_audit` positions do not dominate risk.

---

## 5. P2 Gates

1. Multiple-day paper report confirms entries are not just one-day noise.
2. Skip reason distribution is stable and understandable.
3. Dashboard shows the active strategy mode and tradability skip counts.
4. Runtime files stay bounded and are not bulk-read by agents.
5. A separate live-trading safety review can be considered only after the user explicitly asks.

---

## 6. Durable Implementation Sequence

This sequence is durable guidance. For the current run, follow the temporary implementation order file.

### Phase A - Documentation Alignment

Goal:

```text
Make AGENTS.md, current-task, production decisions, and roadmap agree before code changes.
```

Required outcome:

```text
No doc says endDate is the hard trading cutoff.
No doc says lock-only is the active default.
Docs require accepting_orders and executable depth before new paper entry.
Docs define settlement precision profile and HKO needs_audit behavior.
Docs require step-by-step user confirmation.
```

### Phase B - Tradability Metadata

Goal:

```text
Preserve and verify Polymarket tradability fields.
```

Main files:

```text
src/weather_bot/models.py
src/weather_bot/polymarket_client.py
src/weather_bot/live_paper_runner.py
```

Required outcome:

```text
RawMarket carries active/closed/archived/accepting_orders/enable_order_book/ready/funded/end_date_iso.
Final pre-trade can fetch CLOB tradability by condition_id.
No entry when accepting_orders is false or unknown at final pre-trade.
```

### Phase C - Settlement Precision

Goal:

```text
Separate Wunderground-style whole-degree markets from HKO decimal markets.
```

Main files:

```text
src/weather_bot/settlement_precision.py
src/weather_bot/station_signal.py
src/weather_bot/stations.py
```

Required outcome:

```text
whole_degree_source_display_band works as [N.0, N+1.0).
HKO one-decimal range-containing is needs_audit until proven.
Unknown precision blocks entries.
```

### Phase D - Intraday Observation Edge

Goal:

```text
Increase trade activity using official station evidence at city-local high/low formation windows.
```

Main files:

```text
src/weather_bot/strategy_profiles.py
src/weather_bot/station_signal.py
src/weather_bot/live_paper_runner.py
```

Required outcome:

```text
Verified station/month/direction monitoring_start_local_minute gates ordinary entries.
Final high/low formation q25/median/q75 and remaining-movement probability are auditable.
Exact-bucket entries wait for the matching q75 unless the observation has already made the bucket impossible.
HKO midnight carryover and same-day monotonicity violations fail closed.
AWC METAR daily extremes require a persistent, uninterrupted station-local midnight handoff.
Latest-only or continuity-gapped METAR evidence blocks probability calculation.
CLOB-provided close time can block a high strategy that cannot reach its formation window.
Intraday side probability must be at least 0.90.
Probability from 0.90 to below 0.95 targets 30% of bankroll.
Probability at or above 0.95 targets 50% of bankroll.
Executable depth, fee-aware edge, and bounded VWAP impact may reduce or block those targets.
VWAP impact and same-observation sibling re-entry fail closed.
```

### Phase E - Ledger And Report Tags

Goal:

```text
Make performance auditable by strategy mode and signal family.
```

Main files:

```text
src/weather_bot/models.py
src/weather_bot/paper.py
src/weather_bot/analyze_paper.py
```

Required outcome:

```text
strategy_mode, signal_family, price_anomaly, settlement_precision_confidence are recorded.
Old ledgers remain readable.
Reports separate strategy-mode PnL.
```

### Phase F - Test And Cleanup

Goal:

```text
Verify focused behavior and remove the temporary instruction file after completion.
```

Required outcome:

```text
Focused tests pass.
Full pytest is attempted.
Temporary implementation order is deleted only after all steps complete.
current-task.md is reset to Status: none.
```

---

## 7. Validation Workflow

For future strategy changes, run focused tests for the touched behavior first, then the full suite. Keep temporary execution orders under `docs/active/` only while they are active, and reset `docs/active/current-task.md` to `Status: none` after verified completion.
