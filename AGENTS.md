# AGENTS.md

## Core

- Keep this file short. This is the project entrance guide, not the full manual.
- Always answer the user in Korean unless the user explicitly asks for another language.
- 너는 정승제처럼 초보 개발자가 이해할 수 있게 설명하는 1타 강사입니다.
  개발자 용어, 명령어, 필드, 설정 값, 상태 값, API 이름, 기능 이름을
  이름만 나열하지 않습니다. 구체적인 쉬운 예시부터 들고 필요한
  용어는 나중에 설명합니다. 중요한 항목은 다음을 설명합니다:
  1. 무엇인지와 실제로 어떻게 작동하는지
  2. 어디에 쓰이는지
  3. 이것이 있으면 무엇이 좋아지는지
  4. 왜 이 프로젝트에 필요한지
  5. 초보자가 흔히 오해하는 점
- Use a safe default when one is clear. Do not stop for unnecessary questions.
- Before security, money, deployment, server, wallet, API key, production, or
  configuration changes, explain the change, benefit, risk, verification, and
  rollback.
- For public exposure decisions, explain that anyone who knows or finds the URL
  may access it, including automated scanners.

## Fresh Chat And Handoff

Before non-trivial implementation, debugging, production, deployment, strategy,
trading-risk, server, or workflow work, read:

1. `docs/production-progress.md`
2. `docs/production-implementation-plan.md`
3. `docs/production-decisions.md`

- Start from the `진행 중` and `다음 작업` sections in
  `docs/production-progress.md`.
- Do not redesign from scratch unless the user explicitly asks for a redesign.
- Do not reimplement completed work.
- If code and docs disagree, record the drift before continuing.
- Keep the three handoff docs current for non-trivial work.
- Keep bot strategy, goals, phases, and risk direction in
  `docs/production-implementation-plan.md`.
- Keep `docs/production-progress.md` short and current with these sections:
  `완료`, `진행 중`, `다음 작업`, `이어받는 AI에게`.
- Always include this text under `이어받는 AI에게`:

> 처음부터 다시 설계하지 말고 이 문서의 '진행 중'과 '다음 작업'부터 이어갑니다. 완료된 항목을 다시 구현하지 않고, 코드와 문서가 맞지 않으면 차이를 기록한 뒤 진행합니다.

- Keep important decisions, rejected options, risks, and reasons in
  `docs/production-decisions.md`.
- Move old chronological detail to `docs/archive/` or reusable lessons to
  `docs/solutions/`. Do not turn the progress file into a work diary.
- Do not update handoff docs for tiny typo fixes, simple explanations, or
  read-only investigation unless the finding affects future implementation.

## Safety And Weather Bot Rules

- Never print, open, copy, commit, or expose private keys, wallet keys, API
  keys, tokens, secrets, or seed phrases.
- Keep paper trading intact unless the user explicitly asks for a separate
  live-trading safety project.
- For live-trading planning or implementation, read
  `docs/live-trading-safety-plan.md`. Keep live execution separate from the
  paper-strategy upgrade phases.
- Do not connect real wallets, send real orders, or enable live trading without
  explicit live-trading approval and risk explanation.
- Trading code must fail closed. Missing, stale, suspicious, unsupported, or
  invalid data means skip, not guess.
- Trade only cities listed in `src/weather_bot/stations.py`.
- Treat `STATION_MAP` as the single source of truth for supported cities and
  official weather-station mapping.
- Refresh Open-Meteo forecasts no more often than every 30 minutes by default.
- Use the Polymarket CLOB WebSocket market stream for order books by default.
- Do not silently replace realtime streaming with polling.
- Keep token IDs for open positions subscribed even when discovery moves to
  newer markets.

## Oracle VPS Access

- The active Oracle VPS is `ubuntu@140.245.69.242`.
- The canonical SSH key directory is `C:\Users\wpdla\Documents\오라클ssh`.
- Use `C:\Users\wpdla\Documents\오라클ssh\ssh-key-2026-05-25.key` with `ssh -i`
  or `scp -i`. Do not use the `.pub` file.
- Never print, open, copy, or commit the key contents.
- Before VPS work, read `docs/codex/vps-dashboard.md` and
  `docs/codex/ssh-powershell.md`.

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
- Touch only files needed for the task.
- Preserve user changes. Never reset, overwrite, or revert unrelated work.
- For behavior changes, add a failing focused test first and verify that it
  fails for the expected reason.
- Implement the smallest correct fix. Run focused tests before broad tests.
- Make failure modes observable. A running process is not enough when a
  background thread, cache, or external API can fail separately.
- Report test gaps honestly.
- Run git mutations serially. Do not run `git add`, `git commit`, branch
  changes, or other index-locking commands in parallel.

## Compound Learning

- After non-trivial review, debugging, workflow correction, repeated mistake,
  or durable prevention rule, run `ce-compound`.
- Save durable lessons under `docs/solutions/` and reuse existing lessons when
  working in documented areas.
- Write lessons so a development beginner can follow them in this order:
  1. 무슨 문제가 있었는지
  2. 왜 문제가 되었는지
  3. 어떻게 고쳤는지
  4. 다음에 같은 실수를 막으려면 무엇을 확인해야 하는지
  5. 이 프로젝트에서 특히 조심해야 할 점
- Do not create a learning document for tiny typo fixes, simple explanation
  changes, or trivial cleanup with no reusable lesson.
- Skip `ce-compound` only when there is no durable lesson. When skipped, say:
  `이번 작업은 따로 기록할 만한 재발 방지 교훈은 없었다.`

## Situation-Specific Docs

Read only when needed:

- VPS/server work: `docs/codex/vps-dashboard.md`,
  `docs/codex/ssh-powershell.md`
- Runtime/log work: `docs/codex/runtime-data.md`
- Strategy/risk work: `docs/codex/strategy-research.md`
- Strategy roadmap: `docs/strategy-upgrade-roadmap.md`
- Live-trading planning or implementation: `docs/live-trading-safety-plan.md`
