# Strategy Validation Roadmap

This roadmap tracks evidence still needed to judge the paper strategy honestly.
It is not the current task list; use `docs/active/current-task.md` for that.
Long-window readiness gates are in `docs/paper-validation-runbook.md`.

## North Star

The question is: did the strategy make money with executable prices, real
liquidity, fees, official settlement-station observations, and replayable
ledgers? A profit created by midpoint fills, missing depth, wrong dates, or
incomplete observations is not a profit.

The active paper mode is `hybrid_observation_edge`, combining conservative
locks with station/month/direction residual probability. Every entry still
needs final CLOB tradability, ask-side VWAP, fresh complete same-station
evidence, positive fee-aware edge, expected return, exposure room, and ledger
evidence.

## Completed P0 Foundations

- Final pre-trade uses CLOB `accepting_orders`, `enable_order_book`, active and
  closed state; Gamma `endDate` is not the cutoff.
- Entry uses ask VWAP; exit uses bid VWAP. Missing exit depth means HOLD, not a
  fake close.
- Station-local date boundaries, DST, complete daily extrema, formation-time
  profiles, and no-future residual lookup are tested.
- HKO midnight rollover is monotonic and fail-closed; decimal settlement stays
  `needs_audit`.
- Strategy family, station evidence, price, fees, and SKIP reasons are auditable
  while old ledger rows remain readable.
- Runtime diagnostics rotate without deleting the paper account or ledgers.

## Open P1 Evidence Gates

1. Build a historical settlement audit for HKO and every ambiguous decimal
   source before upgrading precision confidence.
2. Run uninterrupted 24-72 hour paper windows and report PnL by strategy family,
   city, high/low direction, probability tier, skip reason, and liquidity block.
3. Compare predicted remaining-movement probability with the final official
   extreme; calibration must hold by station/month/direction rather than only in
   aggregate.
4. Review abnormal-price entries separately from normal residual entries.
5. Prove HKO `needs_audit` exposure cannot dominate bankroll risk.
6. Confirm station-history completeness and WebSocket freshness survive restarts
   and provider interruptions without false entries.

## Open P2 Evidence Gates

1. Accumulate multiple paper days so results are not one city or one-day noise.
2. Keep SKIP distributions stable and understandable; repeated unexplained
   reasons become investigation items, not thresholds to weaken blindly.
3. Reconcile `paper_state.json` with executed trade receipts and settlement
   outcomes after every experiment window.
4. Keep dashboard status, CLOB state, observation age, local formation status,
   rollover state, and block reason visible to a beginner.
5. Keep active diagnostics and archives bounded while preserving audit ledgers.

## Change Workflow

For every behavior change: write a failing regression test, make the smallest
fix, run focused tests, run full local pytest, deploy transactionally, run full
Oracle pytest, and verify both services. Record durable prevention lessons only
when a repeated or non-obvious mistake deserves a focused `docs/solutions/`
entry.

No roadmap item authorizes live trading. A separate live-trading safety project
may be discussed only after the paper gates pass and the user explicitly asks.
