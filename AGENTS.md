# AGENTS.md

## Core

- Keep this file short. This is the project entrance guide, not the full manual.
- Always answer the user in Korean unless the user explicitly asks for another language.
- You must have the explanatory ability of Jeong Seung-je, one of Korea's top
  mathematics instructors.
  Whenever you list difficult things such as developer terminology, commands,
  fields, setting values, status values, API names, feature names, and so on,
  always add supplementary explanations.
  For example, when mentioning `settlement EV`, make an effort to help the user
  understand by explaining what it is used to judge, why it is used, and in what
  ways it is necessary or useful.

  Explain what it is and how it actually works.
  Explain where it is used.
  Explain what becomes better when it exists.
  Explain why it is needed for this project.

  Adding these supplementary explanations will greatly help the user understand
  the project, so you must make a deliberate effort to do this.
- Use a safe default when one is clear. Do not stop for unnecessary questions.
- Before security, money, deployment, server, wallet, API key, production, or
  configuration changes, explain the change, benefit, risk, verification, and
  rollback.
- For public exposure decisions, explain that anyone who knows or finds the URL
  may access it, including automated scanners.

## Fresh Chat And Handoff

Mandatory fresh-chat read set for non-trivial implementation, debugging,
production, deployment, strategy, trading-risk, server, or workflow work:

1. `AGENTS.md` (this entrance guide; if it was not already loaded)
2. `docs/active/current-task.md`
3. `docs/production-decisions.md`

- `docs/active/current-task.md` is the active task card. It is the only default
  place for unfinished work. If it says `Status: active`, continue from its
  `Next Action`. If it says `Status: none`, start from the user's latest
  request and use the conditional reads below.
- `docs/production-decisions.md` is the active rule book. It keeps current
  safety, trading, runtime, and handoff rules.
- Do not redesign from scratch unless the user explicitly asks for a redesign.
- Do not reimplement completed work.
- If code and docs disagree, record the drift before continuing.
- Keep `docs/active/current-task.md` replace-only. Do not append completion
  history to it. Use it only for unfinished work that a future chat may need to
  resume.
- When work is complete, set `docs/active/current-task.md` back to
  `Status: none` unless there is a real unfinished follow-up. Completion
  evidence belongs in git commits, tests, `docs/archive/`, or `docs/solutions/`.
- Keep bot strategy, goals, risk direction, and implementation contracts in
  `docs/production-implementation-plan.md`, but read it only when the task
  touches strategy, risk, forecast, order books, portfolio, accounting,
  settlement, or runner behavior.
- Keep important active decisions, rejected options, risks, and reasons in
  `docs/production-decisions.md`. Keep it as a compact decision ledger:
  current rule, why it exists, and operational consequence.
- Move old chronological detail to `docs/archive/` or reusable lessons to
  `docs/solutions/`. Do not turn `docs/active/current-task.md`,
  `docs/production-progress.md`, or `docs/production-decisions.md` into a work
  diary.
- Keep `docs/production-progress.md` as an optional compact project board, not
  the default resume source.
- Do not update handoff docs for tiny typo fixes, simple explanations, or
  read-only investigation unless the finding affects future implementation.

Conditional reads:

- Strategy, trading-risk, forecast, order-book, portfolio, paper-accounting,
  settlement, or runner behavior:
  `docs/production-implementation-plan.md`
- Routine local pytest or VPS/SSH command:
  `docs/codex/known-good-commands.md`
- VPS/server/dashboard deployment work:
  `docs/codex/known-good-commands.md`, then `docs/codex/vps-dashboard.md` and
  `docs/codex/ssh-powershell.md` as needed
- Runtime/log work:
  `docs/codex/runtime-data.md`
- Live-trading planning or implementation:
  `docs/live-trading-safety-plan.md`
- Repeated bug, workflow correction, or prevention-rule work:
  relevant entries under `docs/solutions/`

## Safety And Weather Bot Rules

- Never print, open, copy, commit, or expose private keys, wallet keys, API
  keys, tokens, secrets, or seed phrases.
- Keep paper trading intact unless the user explicitly asks for a separate
  live-trading safety project.
- For live-trading planning or implementation, read
  `docs/live-trading-safety-plan.md`. Keep live execution separate from the
  paper-strategy upgrade work.
- Do not connect real wallets, send real orders, or enable live trading without
  explicit live-trading approval and risk explanation.
- Execute the paper strategy only on temperature markets. Rain, snow,
  precipitation, wind, humidity, and every other non-temperature weather market
  is unsupported and must not reach forecast probability calculation,
  order-book subscription, or paper trade logging.
- Trading code must fail closed. Missing, stale, suspicious, unsupported, or
  invalid data means skip, not guess.
- Trade only cities listed in `src/weather_bot/stations.py`.
- Treat `STATION_MAP` as the single source of truth for supported cities and
  official weather-station mapping.
- Open-Meteo forecast HTTP calls must be globally serialized and drip-fed by
  `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60` by default. One request must
  finish or timeout, then at least 60 seconds must pass before the next real
  Open-Meteo HTTP request starts. Cache hits do not count as calls.
- `FORECAST_CACHE_TTL_SECONDS=2400` is the default forecast answer-sheet
  freshness window. It lets the 40 trading-ready cities rotate roughly one at a
  time instead of keeping each successful forecast valid through a long
  multi-market window.
- `STREAM_CYCLE_INTERVAL_SECONDS=2400` is the market-discovery and WebSocket
  rebuild interval. Do not confuse it with the real Open-Meteo HTTP call
  spacing above.
- Use the Polymarket CLOB WebSocket market stream for order books by default.
- Do not silently replace realtime streaming with polling.
- A WebSocket `price_change` delta must not create executable depth for a token
  until that token has received an initial `book` snapshot in the current
  stream cache.
- Keep token IDs for open positions subscribed even when discovery moves to
  newer markets.

## Oracle VPS Access

- The active Oracle VPS is `ubuntu@140.245.69.242`.
- The canonical SSH key directory is the Oracle SSH directory under
  `C:\Users\wpdla\Documents`.
- Use the private key named `ssh-key-2026-05-25.key` in that Oracle SSH
  directory with `ssh -i`
  or `scp -i`. Do not use the `.pub` file.
- Never print, open, copy, or commit the key contents.
- Before VPS work, start with `docs/codex/known-good-commands.md`, then read
  `docs/codex/vps-dashboard.md` and `docs/codex/ssh-powershell.md` as needed.
- For complex VPS changes from Windows PowerShell, do not fight nested SSH
  quoting. Use the remote-script pattern in `docs/codex/known-good-commands.md`:
  create a small local `.sh`, `scp` it to `/tmp`, then run `ssh ... bash
  /tmp/script.sh`.

## Token Safety

- Do not open large runtime files in full. Use file sizes, counts, tails,
  filters, summaries, or small samples.
- Treat `runtime/`, `paper_raw_snapshots.jsonl`, `paper_decisions.csv`,
  `paper_trades.csv`, `forecast_cache.json`, `paper_state.json`, and
  `paper_runner_status.json` as token-dangerous by default.
- Do not delete runtime data, `.git/`, or `.antigravitycli/` just to reduce
  token usage.

## Workflow

- Think before coding. State assumptions when they affect the result.
- Before local pytest or VPS/SSH work, start with the matching command in
  `docs/codex/known-good-commands.md`. If it fails, inspect the concrete error
  before inventing a different command shape.
- Touch only files needed for the task.
- Preserve user changes. Never reset, overwrite, or revert unrelated work.
- For behavior changes, add a failing focused test first and verify that it
  fails for the expected reason.
- Implement the smallest correct fix. Run focused tests before broad tests.
- Make failure modes observable. A running process is not enough when a
  background thread, cache, or external API can fail separately.
- Report test gaps honestly.
- When dashboard code or dashboard UI is changed, deploy it to the Oracle VPS
  immediately after local verification and commit, then restart the affected
  service and verify the live dashboard HTML plus `/api/status`. If the change
  also affects paper-position metadata, settlement, or runner behavior, restart
  `polymarket-weather-bot` too.
- Run git mutations serially. Do not run `git add`, `git commit`, branch
  changes, or other index-locking commands in parallel.

## Compound Learning

- After non-trivial review, debugging, workflow correction, repeated mistake,
  or durable prevention rule, run `ce-compound`.
- Save durable lessons under `docs/solutions/` and reuse existing lessons when
  working in documented areas.
- `docs/solutions/` is a searchable knowledge store organized by category with
  YAML frontmatter such as `module`, `tags`, and `problem_type`; consult
  relevant entries when implementing or debugging in documented areas.
- Write lessons so a development beginner can follow them in this order:
  1. What the problem was
  2. Why it was a problem
  3. How it was fixed
  4. What to check next time to prevent the same mistake
  5. What this project must be especially careful about
- Do not create a learning document for tiny typo fixes, simple explanation
  changes, or trivial cleanup with no reusable lesson.
- Skip `ce-compound` only when there is no durable lesson. When skipped, say:
  `This work did not produce a durable prevention lesson worth recording.`

## Situation-Specific Docs

Read only when needed:

- Routine local pytest or VPS/SSH command: `docs/codex/known-good-commands.md`
- VPS/server work: `docs/codex/vps-dashboard.md`,
  `docs/codex/ssh-powershell.md`
- Runtime/log work: `docs/codex/runtime-data.md`
- Strategy/risk work: `docs/codex/strategy-research.md`
- Live-trading planning or implementation: `docs/live-trading-safety-plan.md`
