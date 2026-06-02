# Weather Bot Strategy Upgrade Roadmap

Created: 2026-05-31 Asia/Seoul

## Purpose

This roadmap splits the weather-bot upgrade into small, reviewable paper-trading
projects. Run one numbered phase per fresh chat. Do not start the next phase
until the current phase has tests, updated documentation, and a short handoff.

The goal is to improve risk-adjusted paper returns without adding live-wallet
execution. The bot must remain fail-closed: unknown markets, unknown stations,
stale or missing forecasts, invalid sentinels, and unavailable observations are
skips rather than guesses.

## Shared Rules For Every Phase

- Start by reading `AGENTS.md`, `docs/production-implementation-plan.md`,
  `docs/production-progress.md`, `docs/production-decisions.md`, and the
  situation-specific files under `docs/codex/`.
- Preserve existing user changes. Never reset, revert, or delete unrelated work.
- Keep the bot paper-only. Never print, open, copy, commit, or expose secrets.
- Use `src/weather_bot/stations.py` and `STATION_MAP` as the supported-city source
  of truth.
- Keep the Open-Meteo forecast refresh default at no more than once every 30
  minutes.
- Keep CLOB WebSocket streaming as the default order-book path. Do not silently
  replace it with polling.
- Use test-driven development for behavior changes: add a failing focused test,
  implement the smallest correct fix, run focused tests, then run the broad
  suite.
- Update production docs when code behavior changes. Run a compound learning
  check for durable debugging or workflow lessons.
- Do not deploy automatically. After local verification, explain deployment
  benefits, risks, verification, and rollback, then ask for explicit approval.

## Phase 0: Preserve And Verify The Current Baseline

### Goal

Review the existing uncommitted work before strategy changes begin. The current
worktree already contains WebSocket subscription and diagnostic improvements.
Do not discard them.

### Required Work

- Inspect `git status`, targeted diffs, and new documentation files.
- Confirm that open positions remain subscribed to the WebSocket stream even
  when discovery moves to newer markets.
- Confirm that invalid liquidity evaluations explain why both sides were
  rejected.
- Run focused tests for the touched runner and hardening files, then the full
  suite.
- Update docs only where the current behavior and docs disagree.
- Report whether the baseline is coherent and ready to commit. Do not commit
  unless the user explicitly approves it in that chat.

### Completion Gate

- Existing local work is understood and preserved.
- Focused tests and the full local test suite have results recorded.
- Any remaining uncommitted files are explained.

## Phase 1: Forecast Freshness And WebSocket Health

### Goal

Make stale forecasts and dead WebSocket streams visible. A dashboard that still
loads must not be mistaken for a healthy trading service.

### Required Work

- Fix the in-memory Open-Meteo cache so TTL applies to memory entries as well as
  disk entries.
- Stop silently swallowing cache-persistence errors. Record safe diagnostic
  metadata without exposing secrets.
- Track the last forecast attempt, last successful refresh, last failure reason,
  cache age, and whether data is stale.
- Track WebSocket thread health, reconnect count, last message time, and stale
  book age. Surface thread death clearly in runner status and dashboard status.
- Add dashboard warnings when forecast age exceeds the allowed freshness window.
- Use read-only VPS checks when needed. The fixed SSH identity file is
  documented in `AGENTS.md`; never print its contents.

### Completion Gate

- Tests cover TTL expiry, persistence failure reporting, stale forecast status,
  and WebSocket health failure.
- Dashboard and status JSON clearly distinguish healthy, stale, and failed
  states.
- Any VPS deployment remains an explicit approval step.

Local completion note: implemented and verified on 2026-06-01. Deployment still
requires explicit approval.

## Phase 2: Executable Net-Return Entry Filter

### Goal

Reject thin trades such as buying at `0.88` for an expected exit near `0.92`
when fees, spread, and slippage leave too little profit.

### Required Work

- Replace the fixed approximate fee assumption with the Polymarket weather fee
  formula and make the calculation explicit and testable.
- Estimate entry and expected-exit costs from executable prices, spread,
  slippage, and applicable fees.
- Add an entry-only expected net-return filter. Default hypothesis:
  `ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.06`.
- Keep the existing edge calculation as a separate condition. A large model edge
  must not bypass a poor executable return.
- Avoid a blanket ban on high entry prices: a high-priced position may still be
  sensible when settlement value leaves enough conservative net return.

### Completion Gate

- Tests demonstrate why `0.88 -> 0.92` is skipped and why sufficiently profitable
  high-price settlement candidates can still pass.
- Decision logs state the expected gross return, estimated costs, expected net
  return, and rejection reason.

Local completion note: implemented and verified on 2026-06-01. Deployment still
requires explicit approval.

## Phase 3: Exact Temperature Buckets And Event-Based Discovery

### Goal

Analyze the actual multi-bucket weather event. Do not assume every market means
only `temperature >= threshold`.

### Required Work

- Support exact buckets such as `26°C`, as well as lower-tail and upper-tail
  buckets.
- Estimate mutually consistent bucket probabilities from the ensemble
  distribution.
- Group markets by event, city, and date before deciding which candidates to
  evaluate.
- Replace the misleading assumption that `MAX_MARKETS=41` covers all supported
  cities. A single city-date event can contain many binary markets.
- Ensure discovery coverage is measured by events and supported cities, not
  merely by a binary-market counter.

### Completion Gate

- Parser, probability, discovery, and runner tests cover exact, lower-tail, and
  upper-tail buckets.
- Production docs explain the event-based scan model and remove outdated
  `MAX_MARKETS=41` claims where they are no longer correct.

Local completion note: implemented and verified on 2026-06-01. Deployment still
requires explicit approval.

## Phase 4: City-Date Portfolio Selection

### Goal

Allow more than one useful position for a city-date event without accidentally
making several highly correlated bets look independent.

### Required Work

- Evaluate candidate combinations at the event level, not one market at a time.
- Preserve a conservative city-date exposure cap during the first paper phase.
  Split that budget across selected legs instead of multiplying risk.
- Allow complementary bucket positions only when portfolio expected value
  improves after costs.
- Reject contradictory same-market positions and uncontrolled same-direction
  concentration.
- Add event-level logging so the dashboard can explain total exposure, selected
  legs, scenario PnL, and why additional legs were rejected.
- Treat any later exposure-cap increase as a separate evidence-based decision
  after enough resolved paper trades exist.

### Completion Gate

- Tests cover one-leg selection, complementary combinations, correlated-risk
  blocking, and cap enforcement.
- Paper logs can reconstruct the event-level decision.

Local completion note: implemented on 2026-06-01 and revised on 2026-06-02.
The revised paper selector normalizes event probabilities, compares distinct
bucket `YES+YES`, `YES+NO`, and `NO+NO` combinations, requires at least `$10`
per opened leg, caps one city's dates at 20%, and caps total paper exposure at
90%. Deployment still requires explicit approval.

## Phase 5: Settlement-Station Nowcast

### Goal

Use the official settlement station's observed temperature progress to improve
same-day decisions. A forecast alone is not enough near settlement.

### Required Work

- Research the official observation source and update cadence for each supported
  settlement-station type before coding.
- Add observation providers behind explicit station mappings. Do not substitute
  city-center weather or guessed values.
- Record observed high-so-far, observation timestamp, source, freshness, and
  unavailable reason.
- Skip nowcast-dependent logic when observations are missing, stale, or
  unverified.
- Start with a small verified station pilot if a reliable source cannot yet
  cover all 41 cities. Document unsupported stations clearly.

### Completion Gate

- Provider tests use fixtures and cover fresh, stale, malformed, and unavailable
  observations.
- The paper strategy can explain whether a decision used forecast-only or
  forecast-plus-nowcast evidence.

Local completion note: implemented on 2026-06-02. The first pass was a
Seoul/RKSI pilot, then source checks expanded same-station observations to
39 ICAO stations through Aviation Weather Center METAR plus Hong Kong/HKO
through Hong Kong Observatory's maximum/minimum temperature since midnight CSV.
Karachi/OPMR remains forecast-only because AWC did not return recent OPMR METAR
data. The bot records observed high-so-far, observation timestamps, source URLs,
freshness, raw observation count, and unavailable reasons. Missing, stale,
malformed, future-date, or unmapped nowcast keeps the decision forecast-only and
skips nowcast-dependent logic. `docs/station-registry-audit.md` lists all
41 cities, their Open-Meteo station coordinates, nowcast provider status, and
the current rule-evidence gap. Deployment still requires explicit approval.

## Phase 6: Principal Recovery And Settlement Runner

### Goal

Avoid closing a strong low-cost weather position too early. Recover principal
when sensible, then keep a controlled remainder for settlement.

### Required Work

- Compare executable sell-now value after costs with conservative
  hold-to-settlement expected value.
- Add strategic partial closes rather than relying only on liquidity-limited
  partial fills.
- Model a principal-recovery tranche and a bounded runner tranche.
- Preserve probability-stop, invalid-sentinel, max-hold, liquidity, and
  observation-risk safeguards.
- Log each tranche decision so realized paper results can be reviewed.

### Completion Gate

- Tests cover principal recovery, runner retention, probability deterioration,
  settlement-risk reduction, and low-liquidity behavior.
- The policy remains configurable and paper-only.

Local completion note: implemented on 2026-06-02. Profit exits now compare
fee-adjusted sell-now value with conservative settlement expected value. When
settlement is at least as good, the broker sells a principal-recovery tranche,
caps the remaining settlement runner at 25% by default, and logs
`PARTIAL_CLOSE` plus `HOLD_RUNNER` tranche decisions. Probability stops, valid
edge-fade exits, max-hold exits, invalid-sentinel defense, low-liquidity limits,
and nowcast fail-closed behavior remain in force. Deployment still requires
explicit approval.

## Phase 7: Whale And External-Signal Shadow Research

### Goal

Study public trader behavior without blindly copying trades or changing
production execution.

### Required Work

- Use public and official Polymarket data sources where possible.
- Collect a bounded shadow dataset for relevant weather markets: wallet
  activity, market, side, price, timestamp, and later outcome.
- Do not copy-trade automatically and do not expand into private or secret data.
- Compare signal timing and results against the bot's own decisions.
- Produce a research report that states whether any signal is worth a later
  paper-only experiment.

### Completion Gate

- Shadow research is separate from execution.
- Data retention is bounded and documented.
- The report distinguishes evidence from speculation.

Local completion note: implemented on 2026-06-02. Added a separate
`src/weather_bot/shadow_signals.py` research module, `shadow-signal-report`
CLI, bounded `SHADOW_*` settings, public Polymarket Data API trade/activity/
holder client helpers, paper-decision timing/outcome comparison, manual public
note evidence/speculation separation, and `docs/shadow-signal-research.md`.
The paper runner does not consume these signals, no wallet/live execution was
added, and no automatic copy trading was added. Current conclusion remains
research-only until enough resolved matched public signals justify a later
paper-only experiment.

## Fresh-Chat Prompt Template

Copy one phase prompt at a time into a fresh chat:

```text
Read `AGENTS.md` and `docs/strategy-upgrade-roadmap.md` first.
In this chat, complete only Phase N from the roadmap.

Important rules:
- Never delete or revert existing changes.
- Do not add live-trading functionality; keep the project in paper-trading mode.
- Add tests first, then run focused tests and the full test suite after changes.
- If code behavior changes, update the production docs and progress docs.
- Do not automatically deploy to the VPS or change operations settings. If a
  deployment or operations change is needed, explain the change, benefit, risk,
  verification method, and rollback method clearly enough for a beginner, then
  wait for my approval.
- Before finishing, check whether there is durable learning to record, and write
  it under `docs/solutions/` when needed.

Use Phase N's Goal, Required Work, and Completion Gate as the source of truth.
At the end, briefly report changed files, test results, remaining risks, and the
handoff for the next Phase.
```

Replace `N` with the phase number. For best results, also paste the short
phase-specific instruction from the chat handoff.

## Research References

- Polymarket trading fees: https://docs.polymarket.com/trading/fees
- Polymarket negative-risk markets: https://docs.polymarket.com/advanced/neg-risk
- Polymarket trader leaderboard API: https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings
- Open-Meteo ensemble API: https://open-meteo.com/en/docs/ensemble-api
- Example multi-bucket Seoul event:
  https://polymarket.com/event/highest-temperature-in-seoul-on-may-31-2026

## Copy-Paste Prompts

The complete per-phase prompts are stored in
`docs/strategy-upgrade-chat-prompts.md`.
