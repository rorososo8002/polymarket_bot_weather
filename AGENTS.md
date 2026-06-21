# AGENTS.md

## Purpose

This repository is a paper-only Polymarket temperature-market validation bot.
Its job is to prove whether paper PnL is realistic, replayable, and supported by
executable prices, fees, official settlement-station evidence, and auditable
ledgers.

Answer in Korean unless the user asks otherwise. Explain technical items for a
beginner: what they do, why they exist, what risk they prevent, what changed,
and how to verify the change.

## Paper-Only Boundary

Never add or enable wallets, private keys, signing, real orders, redemption,
claims, copy trading, `LiveBroker`, hidden live paths, or private user-data
collection. A live-trading request requires a separate safety project and
`docs/live-trading-safety-plan.md`.

Temperature markets only. Unsupported market types and unknown, stale,
malformed, suspicious, conflicting, or unverified evidence fail closed before
signal calculation, order-book subscription, or paper-trade logging.

## Mandatory Fresh-Task Read Set

For non-trivial implementation, debugging, deployment, strategy, trading-risk,
server, or workflow work, read once at task start:

```text
AGENTS.md
docs/active/current-task.md
docs/production-decisions.md
```

For strategy-validation work also read:

```text
docs/strategy-validation-roadmap.md
```

`docs/active/current-task.md` is the only active-work card. Continue only from
its `Next Action` when `Status: active`; otherwise use the user's latest
request. Use `docs/codex/known-good-commands.md` before inventing pytest, VPS,
or SSH command variants.

Search `docs/solutions/` only for a matching repeated bug, review finding,
workflow correction, or prevention rule. Do not bulk-read it.

## Strategy And Execution Contract

The source of truth is `docs/production-decisions.md`.

```text
default mode: hybrid_observation_edge
allowed families: lock_only, intraday_observation_edge,
                  abnormal_official_station_mispricing
invalid entries: forecast-only, wrong-station, endDate-only,
                 best-ask/midpoint/missing-depth fake fills
```

New entries require final CLOB tradability, executable ask-side VWAP, spread
and fee gates, expected return, exposure room, and fresh same-station evidence.
Exits use executable bid-side VWAP. No bid depth means HOLD, not a fake close.
HKO decimal markets remain `needs_audit` until settlement evidence proves the
bucket mapping.

## Ledgers And Runtime Data

`paper_state.json` is the paper account book, `paper_trades.csv` is the
execution receipt ledger, and `paper_decisions.csv` is the strategy evidence
ledger. Old rows must remain readable when newer evidence columns are absent.

Do not bulk-read ledgers, raw snapshots, caches, logs, `.git/objects`, archives,
downloaded packages, or deploy bundles. Prefer sizes, counts, headers, tails,
grouped summaries, and focused `rg`.

Runtime needs the two checked-in files under `strategy_data/`; the local
`.station_residual_cache.zip` exists only for residual-profile rebuild audits.

## Workflow And Cleanup

- Think before coding and state assumptions that affect results.
- Preserve unrelated user changes; never reset or overwrite them.
- Write focused failing tests before behavior changes, then run focused and
  broad verification.
- Use specific diffs and snippets instead of full-repository dumps.
- Run git mutations serially. Do not stage or commit unless asked.
- Reuse existing files and folders. Do not create a new directory for every
  attempt, test, review, or handoff.
- Pytest scratch data belongs only in `.pytest-tmp` and must be deleted when
  pytest exits. Remove `__pycache__`, extracted tool packages, temporary
  scripts, and one-off archives immediately after their task.
- Temporary implementation plans belong under `docs/active/` or
  `docs/superpowers/plans/` only while active. Delete them after verified
  completion and reset `docs/active/current-task.md` to `Status: none`.
- After a non-trivial Superpowers review cycle, run the compound learning check
  only for a durable prevention lesson; otherwise say no new lesson was needed.

## Main Code

```text
stations.py / settlement_precision.py / strategy_profiles.py
station_signal.py / nowcast.py / residual_probability.py
polymarket_client.py / realtime_orderbook.py / edge.py / portfolio.py
paper.py / exit_policy.py / live_paper_runner.py
analyze_paper.py / dashboard.py
```
