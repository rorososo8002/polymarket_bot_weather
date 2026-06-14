# Strategy Validation Documentation Plan

Status: ready for explicit use
Created: 2026-06-14 Asia/Seoul

## 1. Purpose

This document is the user-facing planning artifact for handing the project to a
new AI without losing the bot's strategy discipline.

The bot's long-term goal is profitable Polymarket temperature-market trading.
The current phase is not live trading. The current phase is to make paper
results honest enough to be useful evidence. That means the next AI must care
more about executable depth, fees, station evidence, stale-data failure, and
replayable ledgers than about adding flashy strategy complexity.

## 2. Current Baseline

Already protected by code or tests:

1. Temperature-only market filtering and trading-ready city gating.
2. WebSocket order books as the realtime source, with REST only as bounded
   verification/resync.
3. Executable ask-side VWAP for entry and executable bid-side VWAP for exit.
4. No successful `CLOSE` when executable bid depth is absent.
5. Partial close and fee-aware paper accounting.
6. Exact buckets use the displayed value only; hidden half-step buckets are
   removed.
7. Range buckets preserve displayed inclusive endpoints.
8. Highest-temperature and lowest-temperature nowcast risk move in opposite
   directions.
9. Open-Meteo calls are cache-protected and rate-limit failures fail closed.
10. `paper_state.json` and `paper_trades.csv` are paired ledgers, not casual
    caches.

Do not reimplement these from scratch. If work touches them, protect them with
focused tests.

## 3. Advice Triage

Adopt now:

1. Store market rule provenance from Gamma discovery.
2. Compare title parsing with description/resolution-rule parsing.
3. Skip rule mismatches with a clear reason such as `SKIP_RULE_MISMATCH`.
4. Store station-local event date windows.
5. Add station confidence grades and reporting precision.
6. Harden temperature comparisons with Decimal or integer-scaled helpers.
7. Tighten final pre-trade checks and configurable spread guards.
8. Add replayable decision fields and event evidence snapshots.
9. Upgrade the minimum report around executable-depth PnL and blockers.
10. Show exit liquidity on dashboard open-position cards.
11. Add confidence scoring, forecast freshness penalty, and drawdown breakers.
12. Write a paper-validation runbook with live-readiness gates.

Adopt later:

1. Exact-bucket historical error distributions.
2. City, season, market-type, and time-to-settlement calibration.
3. Brier/LogLoss dashboard views.
4. Region exposure optimizer.
5. Large simulator and richer payoff optimizer.
6. Dynamic nowcast TTL near boundary values.

Reject in this phase:

1. Wallet, private key, signing, real order, redemption, or `LiveBroker` work.
2. Midpoint or best-quote-only fills as trusted PnL.
3. City expansion without verified settlement-station evidence.
4. API retry bombing after 429 or repeated failures.
5. Restoring exact half-step buckets.
6. Resetting or hand-editing runtime ledgers to improve results.

## 4. Document Roles

`AGENTS.md` is the constitution. It should contain stable rules, research
posture, paper-only boundaries, and handoff hygiene. It should not become a
diary or checklist of temporary work.

`docs/production-decisions.md` is the active law book. It records compact
operational rules that affect how the paper bot may run or change.

`docs/production-implementation-plan.md` is the architecture map. It explains
the code surfaces and contracts that future changes must respect.

`docs/strategy-validation-roadmap.md` is the syllabus. It explains the whole
paper-validation phase, but it is not the live task queue.

`docs/plans/2026-06-14-001-strategy-validation-gap-closure-plan.md` is the
explicit execution plan. Use it only when the user says to execute the
strategy-validation gap plan.

`docs/active/current-task.md` is the only default unfinished-work card. It
should be `Status: none` unless there is real unfinished work.

`docs/active/new-chat-task-prompts.md` is a single-use prompt holder. It should
be `Status: none` unless the user explicitly wants one active handoff prompt.

## 5. Next AI Work Order

1. Market rule provenance and mismatch gate.
2. Station metadata quality grades.
3. Local event date windows.
4. Temperature comparison precision hardening.
5. Final pre-trade check and configurable spread guard.
6. Replayable decision and evidence snapshots.
7. Minimum performance report upgrade.
8. Dashboard exit liquidity view.
9. Confidence score and forecast freshness penalty.
10. Drawdown circuit breaker.
11. Portfolio payoff table and hidden overlap.
12. Paper-validation runbook and live readiness gates.

Each item must start by checking whether the behavior already exists. Completed
work is protected with tests, not reimplemented.

## 6. Copy-Paste Prompt

```text
AGENTS.md를 따라 진행해. 먼저 docs/active/current-task.md와 docs/production-decisions.md를 읽어. 이번에는 docs/strategy-validation-documentation-plan.md와 docs/plans/2026-06-14-001-strategy-validation-gap-closure-plan.md를 실행 기준으로 삼아 첫 미완료 작업부터 진행해. 첨부 조언을 무조건 베끼지 말고, 코드와 테스트를 확인해서 이미 보호된 것, 지금 채택할 것, 나중에 할 것, 버릴 것을 구분해. 현재 단계는 paper-only strategy validation이다. 실거래, 지갑, 개인키, 실주문, 상환, 카피트레이딩, LiveBroker는 금지다. 완료된 작업은 active 문서에 남겨두지 말고, 규칙은 AGENTS.md 또는 docs/production-decisions.md에, 구현 계약은 docs/production-implementation-plan.md에, 실행 계획 변경은 docs/plans/...에만 반영해.
```

## 7. Cleanup Rule

When a task is complete, remove it from default active handoff docs. Completion
evidence belongs in tests, commits, final responses, or focused
`docs/solutions/` entries. Do not make future agents reread completed work just
because it once mattered.
