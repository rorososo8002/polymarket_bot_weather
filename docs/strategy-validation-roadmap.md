# Strategy Validation Roadmap

Created: 2026-06-13 Asia/Seoul

## Purpose

This document is the handoff roadmap for the current project phase.

The long-term goal is a profitable Polymarket temperature-market bot that may
later be connected to live trading. The current goal is narrower: prove that
the paper bot's results are realistic enough to trust before adding any real
money path.

In plain terms: do not ask "can the bot make money live?" yet. First ask "is
the paper result honest, replayable, and based on prices we could actually
trade?"

## Current Scope

This phase includes:

- realistic paper entries from ask-side executable order-book depth
- realistic paper exits from bid-side executable order-book depth
- no fake close when bid depth is absent
- partial close and partial liquidity behavior
- fee, spread, and slippage reflected in paper PnL
- stale order books, stale forecasts, and stale nowcast failing closed
- exact, range, and threshold settlement logic that follows Polymarket text
- daily-high and daily-low nowcast risk handled in opposite directions
- official settlement-station nowcast evidence, not generic weather feeds
- local event date and timezone correctness
- compact validation logs that explain why entries, exits, skips, and holds
  happened
- a minimum performance report that separates realistic bid/ask-depth PnL from
  reference-only midpoint PnL

This phase excludes:

- wallet connection
- private keys
- signing
- real orders
- redeeming or claiming markets
- copy trading
- `LiveBroker`
- advanced dashboard redesigns
- Brier/LogLoss dashboards
- calibration charts
- region exposure optimizer
- complex portfolio heatmap
- a large simulator

## Definitions For Future Agents

`VWAP` means volume-weighted average price. It is the average price the bot gets
after consuming real order-book levels. This matters because buying 100 shares
may use several price levels, not only the best ask.

`ask depth` is the real sell-side liquidity available to buy from. A paper
entry is realistic only when it uses this depth.

`bid depth` is the real buy-side liquidity available to sell into. A paper exit
is realistic only when it uses this depth.

`best bid`, `best ask`, and midpoint are useful reference quotes, but they are
not proof that the whole order can execute.

`fail closed` means "skip or hold when evidence is missing." In a trading bot,
guessing is worse than doing nothing because it creates fake confidence and fake
PnL.

`paper_state.json` is the paper account book. It stores cash, open positions,
and realized PnL. It is not a cache for temporary diagnostics.

`paper_trades.csv` is the paper execution receipt ledger. A `CLOSE` row should
mean a close was executable in the paper model, not merely that an exit signal
appeared.

`paper_decisions.csv` is the decision ledger. It should explain why the bot
wanted YES, NO, HOLD, SKIP, or an exit.

## P0 Validation Gates

The bot is not ready for live-trading planning until all P0 gates are true.

1. Entry uses executable ask-side VWAP for the final order size.
2. Exit uses executable bid-side VWAP for the final close size.
3. No successful `CLOSE` is logged when executable bid depth is absent.
4. Partial liquidity becomes scaled entry, `PARTIAL_CLOSE`, or a hold blocker.
5. Fees, spread, and slippage are reflected before PnL is trusted.
6. Stale order books block new entries and pause exits with observable reasons.
7. Exact buckets are exact displayed values; no hidden half-step intervals.
8. Range buckets preserve displayed inclusive endpoints.
9. Threshold markets follow the exact rule wording.
10. Daily-high markets use observed high; daily-low markets use observed low.
11. Same-station nowcast is used only when official station evidence is mapped.
12. Event date windows are evaluated in the station-local timezone.
13. Market rule provenance is preserved: title, description/resolution text,
    station/source evidence, unit, bucket shape, and local event window are
    consistent before a market may trade.
14. A final pre-trade check revalidates executable depth, spread, stale data,
    edge, exposure, opposing positions, and rule clarity before paper entry.
15. Decision, trade, snapshot, and report outputs carry enough fields to audit
    the result later.
16. The minimum performance report shows realistic net PnL, no-liquidity rate,
    stale-data blocks, signal-type breakdown, market-shape breakdown, city
    breakdown, and high/low breakdown.

## Existing Code Surfaces

Use these files as the map before changing behavior:

```text
src/weather_bot/stations.py           station registry and trading-ready subset
src/weather_bot/weather_client.py     market question parser and bucket shape
src/weather_bot/probability.py        forecast probability and nowcast effects
src/weather_bot/nowcast.py            same-station observed high/low providers
src/weather_bot/realtime_orderbook.py executable WebSocket order-book cache
src/weather_bot/edge.py               VWAP, fees, slippage, expected return
src/weather_bot/paper.py              paper broker, account book, exits, ledgers
src/weather_bot/exit_policy.py        close/hold trigger rules
src/weather_bot/live_paper_runner.py  orchestration and realtime evaluation
src/weather_bot/analyze_paper.py      paper performance report
src/weather_bot/dashboard.py          read-only operator dashboard
```

Primary focused tests:

```text
tests/test_realtime_orderbook.py
tests/test_hardening.py
tests/test_parser.py
tests/test_probability_ensemble.py
tests/test_portfolio.py
tests/test_paper.py
tests/test_exit_policy.py
```

## Implementation Sequence

This section is the durable roadmap, not the live task queue.

Do not infer unfinished work only because a part still appears below. These
part descriptions stay here as the strategy-validation syllabus. The only
active queue is `docs/active/current-task.md`; keep completion evidence in
tests, commits, final responses, or focused `docs/solutions/` notes instead of
turning this roadmap into a diary.

Current protected baseline as of 2026-06-14:

- executable bid/ask VWAP, fees, partial close, and no-fake-close behavior are
  already code-and-test protected
- exact buckets no longer use hidden half-step intervals
- range buckets preserve displayed inclusive endpoints
- daily-high/daily-low nowcast risk direction is protected by tests
- Open-Meteo cache, 429 cooldown, and no retry bombing are protected by tests

Future work should first verify this baseline, then fill the remaining gaps in
rule provenance, station-local windows, replayable evidence, reporting,
confidence, drawdown, and live-readiness gates.

### Part 1 - Audit And Drift Report

Goal: compare current code against the P0 gates before making broad changes.

Output:

- a short drift note in the final response
- docs updated first if behavior and docs disagree
- focused characterization tests for any behavior that already works and must
  not regress

Do not redesign strategy thresholds in this part.

### Part 2 - Execution Realism Hardening

Goal: make entries and exits realistic.

Required behavior:

- entry probes ask depth with the minimum order
- entry recalculates final executable VWAP after final size is known
- exit probes bid depth before writing `CLOSE`
- no bid depth produces a hold blocker, not a fake close
- partial bid depth produces `PARTIAL_CLOSE` when allowed
- fees flow through share count, proceeds, cash, and report value

Key files:

```text
src/weather_bot/edge.py
src/weather_bot/paper.py
src/weather_bot/realtime_orderbook.py
src/weather_bot/live_paper_runner.py
```

### Part 3 - Settlement, Nowcast, And Date Correctness

Goal: make market-rule and station evidence impossible to misread.

Required behavior:

- exact Celsius/Fahrenheit bucket means the displayed value only
- range bucket means displayed inclusive endpoints
- threshold market follows its own above/below/inclusive wording
- daily-high YES risk checks observed high
- daily-low YES risk checks observed low
- official same-station nowcast can trigger exits when it makes the position
  impossible or near-impossible under the market rule
- generic or wrong-station weather data cannot trigger confident exits
- target dates use station-local date windows

Key files:

```text
src/weather_bot/weather_client.py
src/weather_bot/probability.py
src/weather_bot/nowcast.py
src/weather_bot/live_paper_runner.py
```

### Part 4 - Validation Logging Contract

Goal: make every important paper result explainable later without writing huge
logs.

Required fields or equivalent evidence:

- market id, event slug, city, target date, and market shape
- selected side, probability, executable entry VWAP, executable exit VWAP
- best bid, best ask, spread, depth status, and stale status
- fee rate, size, shares, cash effect, and after-fee proceeds
- signal source: forecast-only, nowcast-confirmed, or settlement evidence
- station evidence grade and observed high/low when used
- reason code for YES, NO, SKIP, HOLD, CLOSE, PARTIAL_CLOSE, or SETTLED

Do not turn full SKIP logging back on by default. If SKIP detail is needed,
prefer bounded diagnostics, grouped counters, or short debug sessions.

### Part 5 - Minimum Performance Report

Goal: produce the smallest report that can answer "is this paper result worth
trusting?"

The report must show:

- bid/ask-depth net PnL
- midpoint/reference PnL separately labeled as reference only
- no-liquidity hold rate
- stale-data block counts
- forecast-only vs nowcast-confirmed performance
- exact/range/threshold performance
- daily-high vs daily-low performance
- city breakdown
- bucket or threshold breakdown
- ledger consistency warnings

Do not build advanced visual dashboards before this report is correct.

### Part 6 - Paper Validation Runbook

Goal: define how to run and judge the paper experiment.

Detailed operator criteria live in `docs/paper-validation-runbook.md`.

Required run protocol:

- run paper-only for at least 30 days
- do not reset `paper_state.json` unless starting an intentional fresh
  experiment
- review daily report, minimum performance report, and ledger consistency
- record no-liquidity, stale-data, nowcast, and settlement anomalies
- separate forecast-only profit from nowcast-confirmed profit

Pass criteria before live planning:

- enough decisions and enough open/close trades for a meaningful sample
- bid/ask-depth net PnL is positive over the validation window
- midpoint/reference PnL gap is checked and not hiding fake performance
- no-liquidity exits are rare enough to tolerate or explicitly accounted for
- stale data does not create fake entries or fake closes
- exact/range/threshold results are separated
- daily-high and daily-low results are separated
- state and trade ledgers replay consistently
- core tests pass and the paper-only boundary is intact

### Part 7 - Future Live-Trading Safety Project

Start this only after the paper validation gates pass and the user explicitly
approves a live-trading safety project.

Use `docs/live-trading-safety-plan.md`. Do not implement live trading from this
roadmap alone.

## Deferred Until After P0

- Brier Score and LogLoss dashboards
- calibration charts
- region exposure optimizer
- complex portfolio heatmap
- large simulator
- 30-day formal strategy report
- `LiveBroker`
- private-key or wallet handling
- real order submission
- redemption or claim handling

## Completion Rule For Each Part

Each part must end with:

- docs updated before code behavior changes
- focused tests run for touched behavior
- broad tests run when the touched behavior affects shared strategy, accounting,
  or runner flow
- `docs/active/current-task.md` reset to `Status: none` if no unfinished work
  remains
- a concise Korean final response explaining what changed, what was verified,
  and what remains
