# AGENTS.md

## Purpose

Paper-only Polymarket temperature-market validation bot. The goal is credible
paper PnL: executable prices, fees, official settlement-station evidence, and
auditable ledgers.

Answer in Korean. Explain developer terms in beginner language.

## Token Budget Rules

- Do not bulk-read files. Prefer `rg`, file sizes, headers/tails, and focused
  snippets.
- Do not read `readme2.md`. It is an archive note, not operating context.
- Do not bulk-read ledgers, logs, caches, raw snapshots, `.git/objects`,
  archives, deploy bundles, downloaded packages, or `docs/solutions/`.
- Search `docs/solutions/` only for the exact repeated bug or prevention rule.
- Read long docs only when the task specifically needs them:
  - `docs/live-trading-safety-plan.md`: only for live-trading safety discussion.
  - `docs/station-registry-audit.md`: only for station registry/audit work.
  - `docs/paper-validation-runbook.md`: only for experiment-readiness reports.
  - `docs/strategy-validation-roadmap.md`: only for multi-day validation/reporting.
  - `docs/codex/runtime-data.md`: only for cleanup/archive/runtime-data work.

## Fresh Task Read Set

For non-trivial coding, debugging, deployment, server, or strategy work, read:

```text
AGENTS.md
docs/active/current-task.md
docs/production-decisions.md
```

Before inventing shell, pytest, SSH, cleanup, or deployment commands, read the
matching section of:

```text
docs/codex/known-good-commands.md
```

If `docs/active/current-task.md` says `Status: active`, continue from its
`Next Action`; otherwise follow the user’s latest request.

## Paper-Only Boundary

Never add wallets, private keys, signing, real orders, claims, redemption,
copy trading, `LiveBroker`, hidden live paths, or private user-data collection.
Temperature markets only. Unknown, stale, malformed, conflicting, unsupported,
or unverified evidence fails closed.

## Trading Contract

Source of truth: `docs/production-decisions.md`.

Allowed families:

```text
lock_only
intraday_observation_edge
abnormal_official_station_mispricing
```

Invalid entries:

```text
forecast-only
wrong-station
endDate-only
best-ask/midpoint/missing-depth fake fills
```

New entries require final CLOB tradability, executable ask-side VWAP, spread and
fee gates, expected return, exposure room, and fresh same-station evidence.
Exits require executable bid-side VWAP. No bid depth means HOLD, not fake close.

## Ledgers

- `paper_state.json`: 종이계좌 장부. 현금, 보유 포지션, 평균 진입가의 기준.
- `paper_trades.csv`: 종이 체결 영수증.
- `paper_decisions.csv`: 왜 들어갔고 왜 안 들어갔는지 남기는 판단 장부.

Old rows must remain readable when newer columns are absent. Never delete or
truncate those files unless the user explicitly approves an experiment reset.

## Workflow

- Preserve unrelated user changes. Never reset.
- Write focused failing tests before behavior changes.
- Run focused tests, then full local pytest for meaningful code changes.
- Deploy transactionally: backup, copy, remote pytest, restart services only
  after success.
- Remove temporary scripts after use.
- Stage/commit only when asked or when the user asked for completed changes to
  be committed.

After a non-trivial repeated mistake or durable workflow lesson, consider
`ce-compound`; skip it when no new durable lesson is needed.
