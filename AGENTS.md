# AGENTS.md

## Core

- Always answer the user in Korean unless the user explicitly asks otherwise.
- Explain like a top Korean math instructor: when you mention a developer term,
  command, file, field, setting, API, status, or feature, also explain what it
  is, where it is used, what improves when it exists, and why this project needs
  it.
- Use the safe default when one is clear. Do not stop for unnecessary questions.
- Before security, money, deployment, server, wallet, API key, production, or
  configuration changes, explain the change, benefit, risk, verification, and
  rollback.
- For public exposure decisions, explain that anyone who knows or discovers the
  URL may access it, including automated scanners.

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
  settlement, or runner behavior: `docs/production-implementation-plan.md`
- Routine local pytest or VPS/SSH command:
  `docs/codex/known-good-commands.md`
- VPS/server/dashboard deployment:
  `docs/codex/known-good-commands.md`, then
  `docs/codex/vps-dashboard.md` and `docs/codex/ssh-powershell.md` as needed
- Runtime/log investigation: `docs/codex/runtime-data.md`
- Live-trading planning or implementation: `docs/live-trading-safety-plan.md`
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
- Open-Meteo forecast HTTP calls use **batch mode**: cities are fetched sequentially
  with `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=15` gaps within a batch, then the bot
  waits until `FORECAST_CACHE_TTL_SECONDS=10800` (3 h) expires before the next
  batch. GFS updates every 6 h and takes 3-4 h to process, so a 3-h cache captures
  each new model run without wasting API units on unchanged data.
  Budget: 39 active cities × 8 batches/day × 31 units ≈ 9 672 units/day < 10 000 limit.
- On a non-rate-limit forecast failure, skip that city and move to the next one.
  Do not retry the same city within the same batch. The failure cooldown equals the
  cache TTL so the city is only retried in the next batch (~90 min later).
- On a 429 rate-limit response, stop the entire batch immediately and wait for the
  rate-limit cooldown to expire before resuming. Do not hammer failed cities.
- `FORECAST_CACHE_TTL_SECONDS=5400` is the forecast answer freshness window and
  the effective between-batch interval. `STREAM_CYCLE_INTERVAL_SECONDS=2400` is the
  market-discovery and WebSocket rebuild interval, not the forecast spacing.
- `STATION_NOWCAST_CACHE_TTL_SECONDS=300` (5 min) is the recommended nowcast
  cache TTL. The AWC METAR provider floor is also 5 min, so this keeps
  observation data fresh enough for timely exit decisions without hammering
  the source.
- Use the Polymarket CLOB WebSocket market stream for executable order books by
  default. Do not silently replace realtime streaming with polling.
- A WebSocket `price_change` delta must not create executable depth for a token
  until that token has received an initial `book` snapshot in the current
  stream cache.
- Keep token IDs for open positions subscribed even when discovery moves to
  newer markets.

## Runtime Data

- Runtime files are generated evidence, not source code. They are ignored by
  git and may be deleted only for an intentional fresh paper-experiment reset.
- `paper_state.json` is the paper account book. It stores cash, realized PnL,
  and open positions.
- `paper_trades.csv` is the execution receipt ledger. It records paper `OPEN`,
  `ADD`, `PARTIAL_CLOSE`, `CLOSE`, and `SETTLED` actions.
- `paper_decisions.csv` is the strategy-decision evidence ledger. It records
  YES/NO/SKIP judgments, including why the bot did or did not act.
- `paper_raw_snapshots.jsonl` is diagnostic evidence, not an account book.
  Normal raw snapshots are disabled by default except for errors.
- Do not open large runtime files in full. Use file sizes, counts, tails,
  filters, summaries, or small samples.

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
- **Before changing any code, update the relevant documentation first.**
  Code and docs must stay in sync. A future AI reading only the docs must
  reach the same behavior as the code.
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
- Keep active safety, trading, runtime, and handoff rules in
  `docs/production-decisions.md`.
- Keep strategy and implementation contracts in
  `docs/production-implementation-plan.md`.
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
