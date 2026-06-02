# Production Progress

## Completed

- This is a live-data paper-trading service. It does not send real wallet
  orders, connect private keys, or enable live trading.
- Phases 0-7 are implemented and locally verified: baseline hardening,
  forecast/WebSocket health, fee-aware entry filtering, exact weather-event
  discovery, city-date portfolio selection, same-station nowcast, settlement
  runners, and shadow public-signal research.
- Current strategy guardrails are active: trade only the 41 `STATION_MAP`
  cities, refresh Open-Meteo forecasts every 30 minutes by default, use the
  Polymarket CLOB WebSocket stream, keep held token IDs subscribed, fail closed
  on stale or unsupported data, and preserve paper-only execution.
- Station evidence is now gated separately from station registration: 40 cities
  have stored official Polymarket rule evidence and are trading-ready; Karachi
  is excluded because its found rule source conflicts with the current station
  code.
- Temperature nowcast uses same-station observed extrema: observed high-so-far
  for daily-high markets and observed low-so-far for daily-low markets. METAR
  and HKO providers derive high/low from one station-date response and cache it.
- Realtime order-book cache treats `best_bid_ask` as indicative best-price
  reference only. Executable bid/ask depth comes only from `book` snapshots or
  `price_change` level updates.
- Paper accounting is fee-aware end to end: `size_usd` is the all-in entry
  budget, closes add after-fee proceeds, and dashboard/liquidation values use
  after-exit-fee marks.
- Paper account state is persisted with a temp-file write followed by
  `os.replace`, and existing corrupt or invalid `paper_state.json` fails closed
  instead of starting from a fresh default account.
- Dashboard startup fails closed on public hosts such as `0.0.0.0` unless
  `DASHBOARD_TOKEN` is non-empty and non-placeholder. Browser API polling uses
  the token header instead of a token query string, and server logs redact
  token query values.
- Entry candidate `size_shares` now means the actual fee-adjusted shares bought
  with the all-in `size_usd` budget, so portfolio scenarios and broker-opened
  paper positions use the same held quantity.
- Phase 6 settlement runners recover principal first, then keep a bounded 25%
  runner only when conservative settlement value beats fee-adjusted sell-now
  value. Runner logs distinguish actual held shares from target runner shares.
- Phase 7 shadow research is separate from execution. Public trade rows are
  locally size-checked, deduplicated by full row identity, bounded by
  `SHADOW_MAX_ROWS`, and compared to bot entries only on paired resolved
  samples.
- Forecast target dates now require exact `daily.time` matches. If the target
  date is absent, the probability path returns `forecast-unavailable` with zero
  confidence instead of using a nearby forecast date.
- Local verification after the latest observed-extrema nowcast fix: focused
  nowcast/probability pytest and full `pytest -q`. Full result: `209 passed`.

## In Progress

- Phase 0-7 local work is complete with review hardening and fee-adjusted
  paper-share consistency.
- Station-rule evidence hardening is complete locally and remains paper-only.
- Phase 0-7 changes have not been automatically deployed to the Oracle VPS.
- Before any deployment, explain the change, benefit, risk, verification method,
  public exposure implications, and rollback method, then get explicit user
  approval.

## Next Work

1. Do not feed Phase 7 research into strategy execution until enough resolved
   paired public signals accumulate.
2. When comparing paper results, record the boundary between pre-fix gross-fee
   accounting and post-fix fee-adjusted accounting. Existing runtime files were
   not rewritten retroactively.
3. Later, run `shadow-signal-report --collect` only when bounded public data
   collection is intentional. Suggest a paper-only A/B experiment only if the
   report has at least 20 paired resolved rows and at least a
   five-percentage-point edge over matched bot entries.
4. Automatic copy trading, wallet connection, live orders, and private data
   collection remain prohibited.
5. Read `docs/strategy-upgrade-roadmap.md` only for roadmap or next-phase
   strategy planning. It is not part of the default fresh-chat handoff.
6. Before local pytest or VPS/SSH work, use the command shapes in
   `docs/codex/known-good-commands.md`.
7. Do not bypass `TRADING_READY_STATION_MAP`; `STATION_MAP` is the registry,
   while trading-ready means official rule evidence is stored and conflict-free.
8. For any public dashboard exposure, set a real long random `DASHBOARD_TOKEN`.
   Empty, placeholder, basic, default, or change-me style tokens now stop the
   dashboard before it binds to the public host.

## For The Next AI

> Do not redesign from scratch. Continue from this document's 'In Progress' and 'Next Work' sections. Do not reimplement completed items. If the code and documents disagree, record the drift before continuing.

- First read `AGENTS.md`, this file,
  `docs/production-implementation-plan.md`, and
  `docs/production-decisions.md`.
- Do not rebuild completed Phase 6 or Phase 7 work. Preserve the
  principal-recovery/settlement runner path in `src/weather_bot/paper.py`, exit
  trigger separation in `src/weather_bot/exit_policy.py`, and shadow research
  isolation in `src/weather_bot/shadow_signals.py`.
- Shadow signals are research-only. Do not add real orders, wallet connection,
  automatic copy trading, operations deployment, or private data collection.
- Use `TRADING_READY_STATION_MAP` for execution candidates. Karachi remains
  excluded until the `OPMR` registry entry is reconciled with the official
  Polymarket rule source that points to `OPKC`.
