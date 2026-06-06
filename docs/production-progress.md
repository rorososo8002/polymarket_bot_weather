# Production Progress

This file is the current handoff board. Keep it short. Historical process
detail belongs in `docs/archive/`, and durable prevention rules belong in
`docs/solutions/`.

## Completed

- This is a live-data paper-trading service. It does not send real wallet
  orders, connect private keys, or enable live trading.
- The active paper strategy is temperature-only and fail-closed. It executes
  only for the 40 `TRADING_READY_STATION_MAP` cities with stored official
  station-rule evidence. Karachi remains registered but excluded until the
  `OPMR`/`OPKC` conflict is reconciled.
- Core hardening is implemented: exact weather-event discovery, active/open
  entry filtering, explicit YES/NO token mapping, CLOB WebSocket order books,
  executable-depth freshness, defensive order-book parsing, same-station
  nowcast, fee-aware entry/exit math, settlement runners, and paper-accounting
  transaction guards.
- Real Open-Meteo forecast HTTP calls are globally serialized and drip-fed:
  one real request finishes or times out, then at least
  `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60` passes before the next real
  request starts. `FORECAST_CACHE_TTL_SECONDS=2400` is the default forecast
  answer freshness window. Cache hits do not count as calls.
- The WebSocket receiver is lightweight. It updates the order-book cache and
  sends event work to a bounded coalescer/worker; strategy evaluation and
  `paper_decisions.csv` writes do not run inline on the receiver thread.
- WebSocket `price_change` messages are treated as deltas only. They cannot
  create executable token depth until the current stream cache has first seen a
  `book` snapshot for that token.
- Paper ledgers are protected. `paper_state.json` is the account book,
  `paper_trades.csv` is the execution ledger, and `paper_decisions.csv` is the
  strategy judgment ledger. Do not delete, truncate, or rotate them just to
  reduce token usage or disk pressure.
- Runtime diagnostics are bounded by default. `paper_raw_snapshots.jsonl` is
  error-only unless debug mode is deliberately enabled, rotates over 100MB, and
  can suspend under dangerous disk pressure without deleting paper ledgers.
- Dashboard and report readers are large-ledger aware: CSV readers stream rows
  or use bounded tails/caches, and dashboard scanner totals disclose whether
  counts are exact full-ledger totals or recent-tail estimates.
- The active Oracle VPS paper experiment was reset on 2026-06-05 UTC at the
  operator's request. Treat performance after that reset as a new experiment
  window starting from 200 USD cash and zero positions.
- Historical completion details moved to
  `docs/archive/production-handoff-history-2026-06-07.md`. Reusable mistake
  prevention rules remain in `docs/solutions/`.

## In Progress

- No active code or deployment work is currently in progress in this handoff.

## Next Work

1. Do not feed shadow research into strategy execution until enough resolved
   paired public signals accumulate. A paper-only A/B experiment is the next
   possible step only after the thresholds in
   `docs/production-implementation-plan.md` are met.
2. Before judging profitability, remember the 2026-06-05 UTC paper reset and
   the boundary between pre-fix gross-fee accounting and post-fix fee-aware
   accounting. Do not mix old and new performance windows.
3. Build or run a paper-only SKIP diagnosis report before changing thresholds,
   risk caps, or data-source assumptions. Use
   `docs/codex/skip-diagnostics.md` to classify account-safety, minimum-order,
   liquidity, weather/parser, and strategy-threshold blockers.
4. If full-history reports become too slow on very large ledgers, add an
   explicit operator option such as `--since` or `--max-rows`; do not silently
   change the default full-history report meaning.
5. For local pytest, VPS/SSH, deployment, and dashboard verification, start
   with `docs/codex/known-good-commands.md` instead of inventing command
   shapes.
6. After any future dashboard change, deploy it to the Oracle VPS after local
   verification and commit, restart the affected service, and verify both HTML
   and authenticated `/api/status`. For settlement or runner behavior changes,
   also restart `polymarket-weather-bot`.
7. Automatic copy trading, wallet connection, live orders, private data
   collection, and live deployment remain prohibited unless the user explicitly
   approves a separate live-trading safety pass.

## For The Next AI

> Do not redesign from scratch. Continue from this document's 'In Progress' and 'Next Work' sections. Do not reimplement completed items. If the code and documents disagree, record the drift before continuing.

- First read `AGENTS.md`, this file,
  `docs/production-implementation-plan.md`, and
  `docs/production-decisions.md`.
- Use `TRADING_READY_STATION_MAP` for execution candidates. `STATION_MAP` is
  the registry, not proof that a city may trade.
- Preserve the paper-only boundary, settlement-runner path, exit-trigger
  separation, WebSocket streaming requirement, and shadow-research isolation.
- Repeated SKIPs are research signals, not the end of the investigation.
- Keep AWC METAR nowcast bulk-prefetched. Do not reintroduce per-station AWC
  HTTP requests during one refresh.
