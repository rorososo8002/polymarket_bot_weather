# Production Handoff History Archive

This archive stores process history removed from the default fresh-chat handoff
on 2026-06-07. It is not part of the mandatory read set. Use it only when a
question needs old deployment, verification, or cleanup context.

## Why This Exists

The default handoff files had become a work diary. Future agents were spending
context on old completed steps before reaching the current task. The active
handoff now keeps unfinished work in one task card, active rules in one compact
decision ledger, and strategy details as conditional reference material.

Think of the split this way:

- `docs/active/current-task.md` is the unfinished-work card.
- `docs/production-progress.md` is an optional compact board.
- `docs/production-decisions.md` is the active rule book.
- `docs/production-implementation-plan.md` is the strategy contract, read when
  strategy/risk/runtime behavior is in scope.
- `docs/solutions/` is the reusable mistake-prevention notebook.
- `docs/archive/` is the process-history warehouse.

## Historical Completion Themes

- Baseline paper-strategy hardening was completed across station validation,
  exact weather-event discovery, forecast and WebSocket health, fee-aware entry
  filtering, city-date portfolio selection, same-station nowcast, settlement
  runners, dashboard/report readers, and shadow public-signal research.
- Paper execution moved to a temperature-only universe. Rain, snow,
  precipitation, wind, humidity, and other non-temperature weather markets were
  removed from forecast, subscription, and paper-trade paths.
- `TRADING_READY_STATION_MAP` became the execution universe. `STATION_MAP`
  remained the registry, while Karachi stayed excluded because official rule
  evidence conflicted with the stored station code.
- Realtime order-book handling was hardened so `best_bid_ask` stays indicative,
  executable depth comes only from `book` and `price_change`, malformed levels
  fail closed, and held positions require token-level executable freshness.
- The WebSocket receiver was decoupled from strategy evaluation. A bounded
  coalescer/worker now handles paper evaluation and decision-ledger writes.
- Paper accounting became fee-aware end to end. Entry size, share count,
  liquidation value, exit triggers, and dashboard PnL all use after-fee values.
- `paper_state.json` and `paper_trades.csv` were paired with a transaction
  journal so account state and execution rows cannot silently drift apart.
- Reports and dashboard readers were changed to stream rows, use bounded tails,
  or cache incremental totals rather than materializing giant ledgers.
- Runtime raw diagnostics were bounded by default. Normal raw snapshots became
  error-only, debug snapshots became opt-in, and rotation/suspension protects
  disk without truncating source ledgers.

## Historical VPS And Runtime Notes

- On 2026-06-03 UTC, the Oracle VPS archived an 18GB
  `paper_raw_snapshots.jsonl` diagnostic file to compressed `data/archive/`,
  recreated a fresh active raw snapshot file, installed raw-snapshot logrotate,
  and reduced root disk use from 84% to 48%.
- On 2026-06-04 UTC, emergency disk cleanup reduced root disk use from 100% to
  50%. Cleanup removed oversized system logs and rebuildable caches, archived
  the active 18.7GB raw snapshot file, and preserved `paper_decisions.csv`
  because it is strategy evidence.
- On 2026-06-05 UTC, the operator approved a fresh paper-only VPS experiment.
  Old runtime evidence was cleared from the active runtime path, a new
  `paper_state.json` started with 200 USD cash and zero positions, and root
  disk use dropped from 100% to 14%. Future profitability comparisons must
  treat this as a new experiment window.
- Forecast request logging was added so Open-Meteo usage reviews count real
  HTTP attempts from `forecast_request_log.jsonl` instead of overwritten cache
  entries. Station nowcast request logging was added for AWC/HKO observation
  attempts.
- Open-Meteo 429 handling was split between daily quota cooldowns and
  short concurrent-request cooldowns. `ReadTimeout` handling was changed to a
  per-forecast-key temporary miss to avoid duplicate immediate requests.
- The Open-Meteo forecast cadence changed to a global one-request-at-a-time
  drip-feed with `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60` and
  `FORECAST_CACHE_TTL_SECONDS=2400`.
- Recent deployments verified the dashboard with header-token `/api/status`,
  not public query-token authentication. Public bare and query-token API calls
  should return 403 when the dashboard is public.

## Historical Decision Ledger Notes

The old `docs/production-decisions.md` contained a long chronological
`Compact Ledger`. Its still-active rules were folded into the current compact
decision categories:

- execution boundary
- market universe and forecasts
- realtime order books
- strategy, risk, and accounting
- nowcast and research
- runtime data and handoff hygiene

When a future task needs the deeper "why did we do that?" explanation, search
`docs/solutions/` first. If no durable lesson exists there, use git history or
this archive as secondary context.

## Handoff Maintenance Rule

Do not add long chronological process detail back to the default handoff files.
At the end of future non-trivial work:

1. Put only unfinished work in `docs/active/current-task.md`.
2. Put only active rules in `docs/production-decisions.md`.
3. Put strategy contracts in `docs/production-implementation-plan.md`.
4. Put reusable prevention lessons in `docs/solutions/`.
5. Put old process history in `docs/archive/`.

The quick test is: "Would the next agent likely make a dangerous mistake today
if this line were absent?" If no, keep it out of the default read set.
