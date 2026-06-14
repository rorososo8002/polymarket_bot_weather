# AGENTS.md

## Core

- Always answer the user in Korean unless the user explicitly asks otherwise.
- Explain like a top Korean math instructor. The user is a beginner, so do not
  just list developer terms. When you mention a developer term, command, file,
  function, concept, setting, state, API, or bug, explain it in this order:
  1. 이게 뭐에 쓰이는 건가?
  2. 왜 이걸 써야 하는가? / 왜 이렇게 만들어졌는가?
  3. 이게 어떤 문제를 일으켰는가? 문제가 없으면 어떤 위험을 막는가?
  4. 왜 그 문제가 생겼는가? 근본 원인은 무엇인가?
  5. 왜 기존 방법이 필요 없었는가? / 더 나은 방법은 무엇인가?
- Example depth:
  - Bad: `paper_state.json이 커졌습니다. 수정했습니다.`
  - Good: `paper_state.json은 종이계좌 장부입니다. 현금, 보유 포지션,
    평균 진입가처럼 성과 검증의 기준이 되는 값을 저장합니다. 그래서
    웹소켓 상태 같은 순간적인 진단 데이터를 여기에 계속 저장하면 안 됩니다.
    그런 데이터는 몇 분 뒤 의미가 없어지는 임시 상태인데, 장부에 넣으면
    파일이 커지고 성과 검증 기준까지 흐려집니다. 더 나은 방법은 웹소켓
    상태는 메모리나 진단 로그에만 두고, paper_state.json에는 계좌 상태만
    남기는 것입니다.`
- Use the safe default when one is clear. Do not stop for unnecessary questions.
- Before security, money, deployment, server, wallet, API key, production, or
  configuration changes, explain the change, benefit, risk, verification, and
  rollback.
- For public exposure decisions, explain that anyone who knows or discovers the
  URL may access it, including automated scanners.

## Project North Star

- The long-term goal is a profitable Polymarket temperature-market bot that may
  later be connected to live trading.
- The current phase is not live trading. The current phase is **strategy
  validation first**: make paper results realistic enough that they are useful
  evidence before any real money path is considered.
- A paper profit number is not trusted unless it uses executable order-book
  depth, fees, spread, slippage, stale-data fail-closed behavior, official
  settlement-station nowcast, and replayable ledgers.
- Do not add private keys, wallet connection, signing, real orders, redemption,
  claim, copy trading, or a `LiveBroker` path unless the user explicitly starts
  a separate live-trading safety project.

## Agent Research Mandate

- Act like a senior strategy-validation researcher, not a passive patch writer.
  When the user gives advice, attachments, or a new idea, compare it against
  the code, tests, ledgers, and project rules before accepting it.
- Use expert initiative. Do not wait for the user to name every missing
  detail. Form hypotheses about what would make this bot more honest,
  profitable, and production-ready, then test those hypotheses against code,
  ledgers, reports, and focused experiments before turning them into rules.
- Sort outside advice into: already protected, adopt now, adopt later, reject,
  or needs more evidence. Do not copy advice into docs just because it sounds
  useful.
- Prefer changes that make paper results more honest, replayable, and closer to
  real execution: executable depth, fees, liquidity, station evidence, timezone
  correctness, stale-data fail-closed behavior, and audit-ready reports.
- A "better strategy" must prove itself with tests, logs, reports, or a bounded
  paper experiment. Do not treat a more complex model as better until it
  improves validation evidence.
- Keep what works and discard what does not. When an experiment, model, rule,
  or document pattern performs worse than the current approach, record the
  lesson and avoid reintroducing it.
- Preserve completed good work. If a rule or feature already exists, cite it
  and protect it with tests instead of reimplementing it.
- Think forward. If the requested change exposes a nearby strategy-validation
  gap, fix or document the smallest useful next step instead of blindly
  stopping at the literal patch.
- Keep permanent docs clean. Stable rules belong in `AGENTS.md` or
  `docs/production-decisions.md`; active work belongs in
  `docs/active/current-task.md`; one-shot handoff prompts belong in
  `docs/active/new-chat-task-prompts.md`; reusable lessons belong in
  `docs/solutions/`.

## Mandatory fresh-chat read set

For non-trivial implementation, debugging, production, deployment, strategy,
trading-risk, server, or workflow work, read these first:

1. `AGENTS.md`
2. `docs/active/current-task.md`
3. `docs/production-decisions.md`

`docs/active/current-task.md` is the only default unfinished-work card. If it
says `Status: active`, continue from its `Next Action`. If it says
`Status: none`, start from the user's latest request and read only the relevant
conditional documents below.

Keep `docs/production-progress.md` as an optional compact project board, not
the default resume source.

Conditional reads:

- Strategy, trading-risk, forecast, order book, portfolio, paper accounting,
  settlement, nowcast, runner behavior, or performance validation:
  `docs/production-implementation-plan.md` and
  `docs/strategy-validation-roadmap.md`
- Routine local pytest or VPS/SSH command:
  `docs/codex/known-good-commands.md`
- VPS/server/dashboard deployment:
  `docs/codex/known-good-commands.md`, then
  `docs/codex/vps-dashboard.md` and `docs/codex/ssh-powershell.md` as needed
- Runtime/log investigation: `docs/codex/runtime-data.md`
- New-chat handoff or step-by-step delegation:
  `docs/active/new-chat-task-prompts.md`
- Live-trading planning or implementation:
  `docs/live-trading-safety-plan.md`
- Repeated bug, workflow correction, or prevention-rule work:
  relevant entries under `docs/solutions/`

Do not bulk-read `docs/solutions/`. Search or list only enough to identify a
matching lesson, then open the specific relevant entry. If no relevant repeated
bug, workflow correction, or prevention-rule issue is present, skip
`docs/solutions/` entirely.

Do not redesign from scratch unless the user explicitly asks for a redesign.
Do not reimplement completed work. If code and docs disagree, record the drift
before continuing.

## Operating Constitution

- Keep execution paper-only unless the user explicitly approves a separate
  live-trading safety project. Do not connect wallets, add private keys, sign
  orders, send real orders, redeem markets, or enable copy trading without that
  approval.
- Execute the paper strategy only on temperature markets. Rain, snow,
  precipitation, wind, humidity, and all other non-temperature weather markets
  must fail closed before forecast probability calculation, order-book
  subscription, or paper trade logging.
- Trade only cities listed in `src/weather_bot/stations.py`. Treat
  `STATION_MAP` as the station registry and `TRADING_READY_STATION_MAP` as the
  execution universe.
- Unknown, missing, stale, malformed, unsupported, suspicious, invalid, or
  conflictful data means skip, not guess.
- Open-Meteo forecast HTTP calls use cache-protected batch mode:
  trading-ready cities are evaluated one forecast key at a time with
  `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=15` gaps between real cache misses.
  The answer cache must keep `FORECAST_CACHE_TTL_SECONDS=10800` (3 h), so a
  full 40-city execution universe costs at most 40 x 8 batches/day x 31 units =
  9,920 units/day, just under the 10,000 unit limit.
- On a non-rate-limit forecast failure, skip that city and move to the next one.
  Do not retry the same city within the same batch. The failure cooldown equals
  the cache TTL so the city is only retried in the next batch.
- On a 429 rate-limit response, stop the entire batch immediately and wait for
  the rate-limit cooldown to expire before resuming. Do not hammer failed
  cities.
- Forecast freshness and nowcast freshness are different clocks. A forecast
  signal may be refreshed from the 3-h Open-Meteo answer cache while nowcast is
  refreshed every 5 min. Do not use `STATION_NOWCAST_CACHE_TTL_SECONDS` to mark
  forecast signals stale.
- Use the Polymarket CLOB WebSocket market stream for executable order books by
  default. Do not silently replace realtime streaming with polling.
- REST order-book snapshots are allowed only as a bounded verification/resync
  helper for the WebSocket cache. They must not replace WebSocket monitoring,
  must not write raw order books to disk, and must be rate-limited.
- A WebSocket `price_change` delta must not create executable depth for a token
  until that token has received an initial full-depth snapshot in the current
  stream cache. The full-depth snapshot may come from WebSocket `book` or the
  bounded REST `/book` verification/resync path.
- Keep token IDs for open positions subscribed even when discovery moves to
  newer markets.

## Strategy Validation Rules

- Entry must use real ask-side executable VWAP. `best ask` or midpoint is not
  enough to claim a fill.
- Exit must use real bid-side executable VWAP. If no executable bid depth
  exists, do not log a successful close.
- Partial liquidity must become `PARTIAL_CLOSE` or a scaled executable entry,
  not a fake full fill.
- If an exit signal fires but the close cannot execute, keep the position open,
  log the blocker, and preserve the original exit trigger.
- Whole-stream order-book failure blocks new entries. A single held token that
  cannot be priced because it is illiquid or settling is valued at $0 for
  entry-bankroll math instead of blocking every new market.
- Fees, spread, and slippage must be reflected before paper PnL is treated as
  useful validation evidence.
- Stale order books, stale forecasts, stale nowcast, unsupported station data,
  and ambiguous settlement data fail closed.
- Exact temperature buckets mean the displayed value only. Do not invent hidden
  half-step ranges such as `28.5C-29.5C`.
- Range buckets preserve the displayed inclusive endpoints.
- Threshold markets follow their actual rule wording: above, at or above,
  below, at or below, highest, or lowest are not interchangeable.
- Daily-high and daily-low markets have opposite nowcast risk directions.
  Highest-temperature YES is impossible only after the observed high exceeds
  the exact value or range upper endpoint. Lowest-temperature YES is impossible
  only after the observed low falls below the exact value or range lower
  endpoint.
- Official settlement-station nowcast matters more than generic weather feeds.
  Use same-station evidence only when the station mapping is explicit.
- Advanced dashboards, Brier/LogLoss views, region optimizers, complex
  portfolio heatmaps, and live trading are deferred until the P0 validation
  gates in `docs/strategy-validation-roadmap.md` are satisfied.

## Runtime Data

- Runtime files are generated evidence, not source code. They are ignored by
  git and may be deleted only for an intentional fresh paper-experiment reset.
- All runtime files live under `data/`, not the app root. Production path:
  `/opt/polymarket-weather-bot/data/<filename>`
- `paper_state.json` is the paper account book. It stores cash, realized PnL,
  and open positions.
- `paper_trades.csv` is the execution receipt ledger. It records paper `OPEN`,
  `ADD`, `PARTIAL_CLOSE`, `CLOSE`, and `SETTLED` actions.
- `paper_decisions.csv` records YES/NO/HOLD actions. SKIP rows are suppressed
  by default with `DECISIONS_LOG_SKIP_ENABLED=false`; enable only for short
  debugging sessions.
- `paper_event_portfolios.jsonl` records event-portfolio selections. Written
  only when at least one trade is selected by default.
- `paper_raw_snapshots.jsonl` is diagnostic evidence, not an account book.
  Normal raw snapshots are disabled by default except for errors.
- `daily_report_YYYYMMDD.txt` is the daily performance report generated every
  day at 00:00 UTC. It shows PnL, win rate, city breakdown, and bucket
  breakdown. Kept for 14 days, then auto-deleted.
- Do not open large runtime files in full. Use file sizes, counts, tails,
  filters, summaries, or small samples.

## Disk Management

- Logrotate compresses high-volume diagnostic/request files in `data/` hourly.
  `paper_raw_snapshots.jsonl` rotates at 100 MB; request logs and portfolio
  diagnostics rotate at 10 MB. Core account ledgers are not rotated until replay
  is archive-aware.
- journald is capped at 50 MB.
- See `docs/codex/data-and-disk.md` for the full disk risk table and reset
  procedure.

## Oracle VPS

- Active Oracle VPS: `ubuntu@140.245.69.242`
- Canonical SSH key directory: the Oracle SSH directory under
  `C:\Users\wpdla\Documents`
- Private key filename: `ssh-key-2026-05-25.key`
- Never print, open, copy, or commit the key contents.
- Before VPS work, start with `docs/codex/known-good-commands.md`.
- For complex VPS changes from Windows PowerShell, use the remote-script
  pattern in `docs/codex/known-good-commands.md`: create a small local `.sh`,
  `scp` it to `/tmp`, then run it with `ssh ... bash /tmp/script.sh`.

## Workflow

- Think before coding. State assumptions when they affect the result.
- Before changing any code, update the relevant documentation first. Code and
  docs must stay in sync. A future AI reading only the docs must reach the same
  behavior as the code.
- Touch only files needed for the task.
- Preserve user changes. Never reset, overwrite, or revert unrelated work.
- For behavior changes, add or update focused tests and verify the behavior.
- Run focused tests before broad tests.
- Make failure modes observable. A running process is not enough when a
  background thread, cache, or external API can fail separately.
- Before local pytest or VPS/SSH work, start with the matching command in
  `docs/codex/known-good-commands.md`. If it fails, inspect the concrete error
  before inventing a different command shape.
- When dashboard code or dashboard UI changes, deploy to the Oracle VPS after
  local verification and commit, restart the affected service, and verify both
  the live dashboard HTML and authenticated `/api/status`. If the change also
  affects paper-position metadata, settlement, or runner behavior, restart
  `polymarket-weather-bot` too.
- Run git mutations serially. Do not run `git add`, `git commit`, branch
  changes, or other index-locking commands in parallel.

## Handoff Hygiene

- Keep `docs/active/current-task.md` replace-only. Do not append completion
  history to it.
- When work is complete, set `docs/active/current-task.md` back to
  `Status: none` unless there is a real unfinished follow-up.
- Keep `docs/active/new-chat-task-prompts.md` as a single-use active prompt,
  not a backlog. When a prompted part is complete, remove that completed prompt
  and either replace it with exactly one next prompt or set it to `none`.
- Keep active safety, trading, runtime, and handoff rules in
  `docs/production-decisions.md`.
- Keep strategy and implementation contracts in
  `docs/production-implementation-plan.md`.
- Keep the paper-validation roadmap in `docs/strategy-validation-roadmap.md`.
- Keep reusable prevention lessons in `docs/solutions/`.
- Do not recreate old diary-style handoff docs. Completion evidence belongs in
  git commits, tests, or reusable solution notes.

## Compound Learning

- After non-trivial review, debugging, workflow correction, repeated mistake,
  or durable prevention-rule work, run `ce-compound`.
- Save durable lessons under `docs/solutions/` and reuse existing lessons when
  working in documented areas.
- Skip `ce-compound` only when there is no durable lesson. When skipped, say:
  `This work did not produce a durable prevention lesson worth recording.`
