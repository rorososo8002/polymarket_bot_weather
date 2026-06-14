# Strategy Validation Bot Evolution Plan

Status: complete
Created: 2026-06-14
Owner: next AI only after the user explicitly asks to execute this plan
Source inputs:

- user attachment: 50-point current strategy explanation
- user attachment: 60-point outside advice about next improvements
- current repository code and documents

## 0. What This Plan Is

This is the explicit execution plan for the next AI when the user asks for it.

The user wants the bot to keep improving like a serious strategy-validation
system, not like a passive code generator. That means the next AI must inspect
the current code, accept only advice that improves realistic paper validation,
reject advice that creates fake confidence, and keep documents clean enough
that future chats do not repeat completed work.

Do not treat this plan as a permanent rule book or default backlog.
`docs/active/current-task.md` is the only automatic resume card. This plan is
read when the user explicitly says to execute the strategy-validation gap plan.

## 1. Non-Negotiable Boundaries

- Current phase is paper-only strategy validation.
- Do not add wallet connection, private keys, signing, real orders, redemption,
  claims, copy trading, or `LiveBroker`.
- Do not restore hidden half-step exact buckets such as `28.5C-29.5C`.
- Do not use midpoint PnL as trusted performance.
- Do not pretend a close happened when bid depth was not executable.
- Do not expand cities without verified station and rule evidence.
- Do not reset runtime ledgers unless the user explicitly asks for a fresh
  paper experiment.

## 2. Advice Triage

### Already Protected

Do not reimplement these from scratch. Verify and preserve them with tests when
nearby code changes:

1. Temperature-only market filtering and trading-ready city gating.
2. Polymarket CLOB WebSocket as the realtime order-book source.
3. `book` snapshot before `price_change` executable-depth updates.
4. Ask-side executable VWAP for entry and bid-side executable VWAP for exit.
5. Fee-aware paper entry, close, partial close, and liquidation value.
6. No fake `CLOSE` when executable bid depth is absent.
7. Exact buckets no longer use hidden half-step intervals.
8. Range buckets preserve displayed inclusive endpoints.
9. Daily-high and daily-low nowcast use opposite observed-value directions.
10. `paper_state.json` and `paper_trades.csv` startup replay fail closed on
    mismatch.
11. Open-Meteo cache, rate-limit cooldown, and no retry bombing.

### Accept Now

These directly reduce fake paper performance and should become near-term work:

1. Store market rule provenance from Gamma discovery.
2. Compare title parsing with description/resolution-rule parsing.
3. Skip markets when title and rule text disagree.
4. Store station-local event date windows.
5. Add station metadata quality grades and reporting precision.
6. Use integer-scaled or Decimal-style temperature comparisons where float
   edge cases can change the result.
7. Tighten final pre-trade checks for freshness, spread, depth, exposure, and
   still-positive edge.
8. Make spread guardrails explicit and configurable, such as
   `SKIP_WIDE_SPREAD`.
9. Improve decision/trade/report fields so decisions are replayable.
10. Save raw evidence snapshots on `OPEN`, `CLOSE`, `PARTIAL_CLOSE`, and
    `SETTLED`, not only on errors.
11. Expand the minimum performance report around executable-depth PnL,
    liquidity blockers, stale-data blockers, signal source, city, market shape,
    and high/low direction.
12. Show exit liquidity on dashboard open-position cards.
13. Add confidence scoring, forecast freshness penalty, and drawdown circuit
    breakers.
14. Add a paper-validation runbook and live-readiness gates.

### Accept Later

These are useful, but only after P0 validation evidence exists:

1. Exact-bucket statistical probability using historical forecast-error
   distributions.
2. City, season, market-type, and time-to-settlement calibration.
3. Full Brier/LogLoss dashboard views.
4. Region exposure optimizer.
5. Complex complementary-leg payoff optimizer.
6. Large fake-market simulator.
7. Dynamic nowcast TTL near boundaries.
8. 30-day formal strategy report.

### Reject Or Keep Forbidden

These must not enter the project in this phase:

1. Live keys or wallet code inside the paper bot.
2. Any live trading path before a separate safety project.
3. City expansion based only on opportunity count.
4. Retry bombing external APIs after 429 or repeated failures.
5. Raw order-book dumps on every normal tick.
6. Midpoint or best-quote-only fills.
7. Manual edits to `paper_state.json` to "fix" results.

## 3. Current Code Reading

This repository already has important foundations:

- `src/weather_bot/polymarket_client.py` discovers weather markets and keeps
  raw Gamma rows in `RawMarket.raw`.
- `src/weather_bot/stations.py` has station metadata, rule URLs, station text,
  and `TRADING_READY_STATION_MAP`; Karachi remains excluded.
- `src/weather_bot/weather_client.py` parses temperature questions and keeps
  exact buckets exact.
- `src/weather_bot/probability.py` has ensemble probability, bias support,
  forecast cache/rate-limit behavior, and same-station nowcast adjustment.
- `src/weather_bot/realtime_orderbook.py` tracks executable WebSocket depth and
  rejects incomplete depth.
- `src/weather_bot/edge.py` has executable buy/sell VWAP and fee math.
- `src/weather_bot/paper.py` has atomic paper state, journal protection,
  replay checks, fees, and partial closes.
- `src/weather_bot/live_paper_runner.py` has final sizing, liquidity checks,
  expected return checks, and held-position exits.
- `src/weather_bot/analyze_paper.py` has a basic report and Brier seed, but it
  is not yet enough for full strategy-validation proof.
- `src/weather_bot/dashboard.py` shows account/dashboard state but does not yet
  make exit liquidity central enough.

## 4. Ordered Work Plan

### 1. Market Rule Provenance And Mismatch Gate

Why:

The bot currently parses the market question. That is necessary, but it is not
enough. The actual settlement rule may contain station, source, timezone, or
unit details that the title hides.

Build:

- Extend the discovered market metadata contract so important Gamma fields from
  `RawMarket.raw` are normalized into audit-friendly fields.
- Extract and store at least: `market_id`, `question`, `slug`, `event_slug`,
  `description`, `resolution_source`, `resolution_rules_text`, `city`,
  `event_date_local`, `event_timezone`, `station_id`, `unit`,
  `condition_type`, `exact_value`, `range_low`, `range_high`,
  `threshold_value`.
- Add a rule-consistency check: title parse and rule/description parse must
  agree on city, temperature direction, unit, bucket shape, and station evidence
  when the source text exposes those fields.
- If they disagree, return `SKIP_RULE_MISMATCH`.

Likely files:

- `src/weather_bot/models.py`
- `src/weather_bot/polymarket_client.py`
- `src/weather_bot/weather_client.py`
- `src/weather_bot/live_paper_runner.py`
- `tests/test_hardening.py`
- `tests/test_parser.py`

Acceptance:

- A market with title `29C` but description using Fahrenheit is skipped.
- A market with a different station in rule text is skipped.
- Existing valid temperature markets still pass.

### 2. Station Metadata Quality Grades

Why:

`same-station nowcast` is not one uniform quality level. Exact same official
source is stronger than same ICAO station through another provider.

Build:

- Add explicit station fields for `temperature_unit`, `reporting_precision`,
  `same_station_nowcast_supported`, `nowcast_confidence_grade`,
  `last_verified_at`, and `confidence_level`.
- Keep trading enabled only for acceptable grades. Default should be A/B
  allowed, C/D skipped.
- Keep Karachi excluded until station evidence is reconciled.

Likely files:

- `src/weather_bot/stations.py`
- `tests/test_station_registry.py`
- `docs/production-decisions.md`

Acceptance:

- Trading-ready cities expose a confidence grade.
- A city without acceptable station confidence cannot enter
  `TRADING_READY_STATION_MAP`.

### 3. Local Event Date Windows

Why:

Weather settlement is by the city/station's local date, not the server's date.
If this is wrong, daily high/low can shift by one day.

Build:

- Normalize `event_date_local`, `event_timezone`, `event_start_utc`, and
  `event_end_utc`.
- Ensure forecast rows, nowcast target dates, and settlement windows use the
  station-local date.
- Add tests for New York, Seoul, and one southern-hemisphere or Pacific
  timezone case.

Likely files:

- `src/weather_bot/weather_client.py`
- `src/weather_bot/probability.py`
- `src/weather_bot/nowcast.py`
- `src/weather_bot/live_paper_runner.py`
- `tests/test_parser.py`
- `tests/test_nowcast_provider.py`
- `tests/test_probability_ensemble.py`

Acceptance:

- A New York June 15 market evaluates June 15 in New York local time.
- Station-local yesterday is allowed only in the documented post-close window.

### 4. Temperature Comparison Precision

Why:

Exact/range logic is already philosophically correct: exact means the displayed
value and range means displayed inclusive endpoints. The remaining risk is
float comparison near boundaries.

Build:

- Keep the existing exact/range behavior. Do not restore half-step buckets.
- Centralize temperature comparison helpers.
- Use integer-scaled units or Decimal-style comparison for exact/range/threshold
  checks.
- Keep original unit and internal comparison unit visible in logs or metadata.
- Convert C/F once at ingest or comparison-boundary creation, not repeatedly.

Likely files:

- `src/weather_bot/weather_client.py`
- `src/weather_bot/probability.py`
- `tests/test_parser.py`
- `tests/test_probability_ensemble.py`

Acceptance:

- `67.000F` and `68.000F` are inside `67F-68F`.
- `68.001F` is outside the high range.
- Exact `29C` does not become a hidden half-step range.

### 5. Final Pre-Trade Check And Spread Guard

Why:

`DECISION YES` is not a trade. It is only a model/order-book opinion. The broker
must recheck the market immediately before paper entry.

Build:

- Preserve existing final sizing and depth checks.
- Add or tighten one named final pre-trade check path before
  `PaperBroker.open_position()`.
- Recheck book freshness, executable ask depth, spread, expected net return,
  no opposing position, exposure caps, stale forecast, stale nowcast, and
  market rule clarity.
- Add configurable absolute and percentage spread limits.
- Produce clear SKIP reasons such as `SKIP_WIDE_SPREAD`,
  `SKIP_STREAM_UNHEALTHY`, `SKIP_NO_DEPTH`, and `SKIP_RULE_MISMATCH`.

Likely files:

- `src/weather_bot/live_paper_runner.py`
- `src/weather_bot/config.py`
- `.env.example`
- `tests/test_hardening.py`
- `tests/test_realtime_runner.py`

Acceptance:

- A wide spread market is skipped even when midpoint looks attractive.
- If the final VWAP changed enough to erase the edge, no position opens.

### 6. Replayable Decision And Evidence Snapshots

Why:

Later analysis must answer "why did the bot buy this?" without guessing.

Build:

- Ensure decision rows carry enough compact fields to replay the judgment:
  market id, token id, city, local event date, condition type, side, `p_true`,
  `p_exec`, best bid/ask, entry VWAP, expected net return, signal source,
  station evidence, order-book freshness, reason code, model version, and
  config version.
- Keep SKIP spam disabled by default, but aggregate skip reason counts.
- Save raw snapshots when real account events happen: `OPEN`, `CLOSE`,
  `PARTIAL_CLOSE`, and `SETTLED`.

Likely files:

- `src/weather_bot/paper.py`
- `src/weather_bot/live_paper_runner.py`
- `src/weather_bot/analyze_paper.py`
- `tests/test_paper_state_io.py`
- `tests/test_analyze_paper.py`

Acceptance:

- Existing old ledger rows remain readable.
- New trade/decision rows include compact replay evidence.
- Raw snapshots are not written for every tick.

### 7. Minimum Performance Report Upgrade

Why:

This is the central proof that paper PnL is honest. A paper profit number is
not trusted until this report separates executable-depth results from reference
quotes.

Build:

- Upgrade `src/weather_bot/analyze_paper.py`.
- Report trusted executable-depth net PnL separately from midpoint/reference
  PnL.
- Report no-liquidity holds, stale-data blocks, forecast-only vs
  nowcast-confirmed, exact/range/threshold, high/low direction, city, bucket,
  and ledger warnings.
- Keep the report stream-friendly for large ledgers.

Likely files:

- `src/weather_bot/analyze_paper.py`
- `tests/test_analyze_paper.py`
- `tests/test_daily_report.py`

Acceptance:

- The report makes clear which PnL is trusted and which is only a reference.
- The report can explain whether fake liquidity is inflating performance.

### 8. Dashboard Exit Liquidity View

Why:

For an open position, the important question is not only "am I up?" but "can I
sell now, and at what average price?"

Build:

- Add open-position fields for best bid, available exit depth, full-size exit
  VWAP, 50 percent exit VWAP, liquidity status, and exit blocker.
- Surface WebSocket health and freshness near open positions.
- Show bid-depth unrealized PnL separately from midpoint/reference PnL.

Likely files:

- `src/weather_bot/dashboard.py`
- `src/weather_bot/dashboard_template.py`
- `tests/test_dashboard.py`

Acceptance:

- Dashboard payload and card data distinguish sellable value from reference
  value.

### 9. Confidence Score And Forecast Freshness Penalty

Why:

`p_true` means "probability of YES." Confidence means "how much we trust that
probability." They are not the same.

Build:

- Define confidence from forecast freshness, nowcast freshness, station grade,
  ensemble spread, order-book freshness, liquidity, rule clarity, and time to
  settlement.
- Penalize old forecast signals without confusing forecast freshness with the
  separate nowcast TTL.
- Reduce sizing for forecast-only or low-confidence trades.

Likely files:

- `src/weather_bot/probability.py`
- `src/weather_bot/risk.py`
- `src/weather_bot/live_paper_runner.py`
- `tests/test_probability_ensemble.py`
- `tests/test_risk.py`

Acceptance:

- A stale forecast blocks new entry but does not stop held-position management.
- Wide ensemble spread lowers confidence and reduces size.

### 10. Drawdown Circuit Breaker

Why:

Exposure caps limit position size, but they do not stop a bot that is losing
repeatedly in one day.

Build:

- Add daily realized loss limit, daily unrealized loss limit, max consecutive
  losses, per-city/event loss cooldown, and cooldown after large loss.
- When tripped, disable new entries but keep managing open positions.

Likely files:

- `src/weather_bot/risk.py`
- `src/weather_bot/paper.py`
- `src/weather_bot/live_paper_runner.py`
- `src/weather_bot/config.py`
- `tests/test_risk.py`
- `tests/test_hardening.py`

Acceptance:

- `DAILY_LOSS_LIMIT_HIT` blocks new opens.
- Existing positions still receive exit/settlement management.

### 11. Portfolio Payoff Table And Hidden Overlap

Why:

Complementary legs must be judged by payoff across possible final outcomes,
not just by whether two text labels look compatible.

Build:

- Represent each leg's covered settlement outcomes.
- Calculate overlap ratio for threshold ladders.
- Build a payoff table for selected city-date legs.
- Reject hidden duplicates or combinations with unacceptable downside in one
  outcome.

Likely files:

- `src/weather_bot/portfolio.py`
- `tests/test_portfolio.py`

Acceptance:

- `29C or higher YES` and `30C or higher YES` are recognized as overlapping.
- A two-leg selection includes a scenario payoff audit.

### 12. Paper-Validation Runbook And Live Readiness Gates

Why:

Live trading must not be triggered by a few good paper days.

Build:

- Create `docs/paper-validation-runbook.md`.
- Define minimum live-discussion gates: 30 days paper, enough decisions,
  enough open/close trades, positive bid-depth PnL, midpoint/reference gap
  checked, no-liquidity failure rate checked, core tests passing, and
  paper-only boundary intact.
- Explain that strategy changes create a new experiment version.

Likely files:

- `docs/paper-validation-runbook.md`
- `docs/strategy-validation-roadmap.md`
- `docs/production-decisions.md`

Acceptance:

- A new AI can tell when paper results are evidence and when they are just a
  misleading short run.

## 5. Document Hygiene Work

Do this alongside the numbered work:

1. Keep `AGENTS.md` as stable behavior rules for future AI agents.
2. Keep `docs/production-decisions.md` as the compact active rule book.
3. Keep `docs/production-implementation-plan.md` as the code/architecture map.
4. Keep `docs/strategy-validation-roadmap.md` as the syllabus, not an active
   backlog.
5. Keep `docs/active/current-task.md` as the only default active task card.
6. Keep `docs/active/new-chat-task-prompts.md` as exactly one one-shot prompt.
7. Do not make future chats read completed execution plans by default.
8. Put reusable lessons in `docs/solutions/`; do not bulk-read that directory.

## 6. Focused Verification

Before changing behavior, run the smallest focused test for that area. After
cross-cutting changes, run:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

For document-only handoff changes, at minimum run:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q tests/test_workflow_defaults.py
```

## 7. New Chat Command

Give this to a new chat only when you want the new AI to execute this plan:

```text
AGENTS.md를 따라 진행해. 먼저 docs/active/current-task.md와 docs/production-decisions.md를 읽어. 이번에는 내가 명시적으로 docs/plans/2026-06-14-001-strategy-validation-gap-closure-plan.md 실행을 지시한다. 첨부 조언을 무조건 베끼지 말고, 코드와 테스트를 확인해서 이미 보호된 것, 지금 채택할 것, 나중에 할 것, 버릴 것을 구분해. 현재 단계는 paper-only strategy validation이며 실거래, 지갑, 개인키, LiveBroker는 금지다. 계획서의 첫 미완료 작업부터 순서대로 진행하고, 완료한 뒤에는 docs/active/current-task.md와 docs/active/new-chat-task-prompts.md가 끝난 일을 다시 가리키지 않게 정리해.
```

## 8. Completion Rule

When this plan is actively executed and then fully completed:

- set `Status: complete` at the top of this file
- set `docs/active/current-task.md` to `Status: none`
- set `docs/active/new-chat-task-prompts.md` to `Status: none`
- keep completed work out of default-read docs
- leave only stable rules in `AGENTS.md` and `docs/production-decisions.md`
- after local verification and commit, deploy the final paper-only bot to the
  Oracle VPS via `docs/codex/known-good-commands.md`, restart affected
  services, and verify dashboard HTML plus authenticated `/api/status` without
  printing secrets
