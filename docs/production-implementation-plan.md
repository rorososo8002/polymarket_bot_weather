# Production Implementation Summary

## Goal

Run a conservative paper-trading bot for Polymarket weather markets using only
verified settlement stations, realtime executable order books, and reproducible
paper accounting.

## Non-Negotiable Rules

- Keep execution paper-only unless live trading is explicitly approved through
  `docs/live-trading-safety-plan.md`.
- Execute the paper strategy only on temperature markets for the 40
  `TRADING_READY_STATION_MAP` cities. `STATION_MAP` is the registry; it is not
  proof that a city may trade.
- Skip unsupported cities, unsupported question shapes, stale data, missing
  order books, suspicious values, invalid parsed data, inactive markets, closed
  new-entry candidates, and unprovable YES/NO token mappings.
- Forecast target dates must match exactly. Nearby Open-Meteo dates are not
  substitutes.
- Real Open-Meteo forecast HTTP calls are globally serialized and drip-fed by
  `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60`. Cache hits do not count as calls.
- Use the Polymarket CLOB WebSocket stream for order books. Do not silently
  replace realtime streaming with polling.
- Treat WebSocket `price_change` messages as deltas only. They may update
  executable depth only after that token has received an initial `book`
  snapshot in the current stream cache.
- Keep held-position token IDs subscribed until the position closes or settles.
- WebSocket receiver callbacks must stay lightweight: update cache, enqueue
  event work, and leave strategy evaluation to the bounded worker.
- `paper_state.json`, `paper_trades.csv`, and `paper_decisions.csv` are
  evidence ledgers. Do not delete, truncate, or rotate them for token savings.

## Architecture

```text
weather event discovery
  -> supported-city and temperature-only parser
  -> station-rule and trading-ready gate
  -> exact-date forecast plus optional same-station nowcast
  -> CLOB WebSocket executable order-book cache
  -> fee-aware YES/NO VWAP edge and expected net-return filter
  -> city-date portfolio selector
  -> PaperBroker risk checks, opens, exits, settlements, and ledgers
  -> dashboard, runner status, reports, and shadow research artifacts
```

Shadow research is separate:

```text
supported weather markets -> bounded public Data API rows
  -> shadow_external_signals.jsonl
  -> paired timing/side/outcome comparison with paper_decisions.csv
  -> shadow_signal_report.md
```

Shadow data is never an execution input by default.

## Code Map

```text
src/weather_bot/stations.py           station registry and trading-ready subset
src/weather_bot/weather_client.py     question parser
src/weather_bot/probability.py        Open-Meteo ensemble probability
src/weather_bot/nowcast.py            same-station observed high/low providers
src/weather_bot/polymarket_client.py  Gamma discovery and REST book parsing
src/weather_bot/realtime_orderbook.py CLOB WebSocket order-book cache
src/weather_bot/edge.py               VWAP, fee, slippage, net-return math
src/weather_bot/risk.py               probability shrinkage and Kelly sizing
src/weather_bot/portfolio.py          city-date portfolio budget selector
src/weather_bot/paper.py              paper broker, accounting, atomic state, exits, logs
src/weather_bot/exit_policy.py        close/hold trigger rules
src/weather_bot/live_paper_runner.py  main paper loop and realtime orchestration
src/weather_bot/dashboard.py          read-only operator dashboard
src/weather_bot/shadow_signals.py     public external-signal research only
src/weather_bot/analyze_paper.py      paper performance report
```

## Strategy Contract

`DECISION YES` and `DECISION NO` are strategy judgments, not guaranteed opens.
The broker may still block entry for exposure, same-market hedge protection,
missing token IDs, low confidence, invalid prices, insufficient liquidity,
abnormal YES+NO ask sums, weak net return, stale dependencies, or account
safety.

Entry must satisfy both:

```text
net_edge > configured threshold
expected_net_return >= ENTRY_MIN_EXPECTED_NET_RETURN_PCT
```

`p_exec` is executable ask-side VWAP. It already includes spread/slippage
through the actual book price, so do not subtract those twice.

`size_usd` is the all-in paper-entry budget. The broker buys fewer shares than
`size_usd / p_exec` so entry notional plus fee stays inside the budget. Normal
and partial closes add only after-fee proceeds to paper cash. Dashboard market
value and liquidation bankroll also use after-exit-fee value.

Entry liquidity checks follow actual order sizing:

1. Probe executable ask depth with at least `MIN_ORDER_USD`.
2. Compute fee-aware edge and actual `size_usd`.
3. Recheck executable ask depth for that final `size_usd`.
4. Recalculate edge, fee, shares, and expected return if final VWAP changes.
5. Scale down only when the confirmed executable amount still meets
   `MIN_ORDER_USD`; otherwise skip.

Same-market opposite-side entries are blocked. Same-side add-ons are allowed
only in paper mode when price has dropped enough, live probability remains
above the position stop, edge and expected return stay positive, and cash plus
exposure caps leave at least `MIN_ORDER_USD`.

## Weather And Discovery Contract

- A weather event is one city-date question; a market is one tradable binary
  result inside that event.
- Discovery expands supported temperature binary markets inside trading-ready
  weather-category events. It does not use the 41-city station registry as an
  event-count cutoff.
- Temperature range buckets such as `86-87F` or `22-23C` preserve displayed
  inclusive endpoints exactly.
- Same-station nowcast may adjust probability only when the provider is
  explicitly mapped to the same settlement station. Current trading-ready
  sources are AWC METAR for ICAO stations and HKO max/min CSV for Hong Kong.
- Nowcast target-date access is narrow: station local today, or station local
  yesterday only during the post-close freshness window for held-position exit
  and settlement-risk evidence.
- AWC METAR nowcast uses one bulk request for the enabled ICAO set, then each
  city filters its own station-date rows. HKO remains one official CSV request.
- Daily-high markets use observed high; daily-low markets use observed low. Do
  not cross-apply one metric to the other.
- Real observation HTTP attempts are counted in
  `station_nowcast_request_log.jsonl`; cache hits do not write rows.
- Missing, stale, malformed, future-date, unmapped, unsupported, or wrong
  station data remains forecast-only or fail-closed depending on context.
- `forecast_rate_limit_state.json` persists Open-Meteo cooldowns after HTTP
  429. Daily quota and concurrent-request cooldowns are distinct.
- `ReadTimeout` creates one logged real attempt and a temporary per-key miss; do
  not hammer the same key immediately.

## Portfolio And Risk Contract

Before new entries, calculate:

```text
cost_basis_bankroll = cash + open-position entry cost
liquidation_bankroll = cash + after-fee executable sell value of open positions
entry_bankroll = min(cost_basis_bankroll, liquidation_bankroll)
```

Unrealized profits do not increase new-entry sizing. Executable unrealized
losses reduce sizing immediately. If any held position cannot be valued from a
usable order book, new entries fail closed with an operator-readable SKIP.

Default portfolio limits:

```text
BANKROLL_USD=100
ENTRY_FRACTION=0.10
MIN_ORDER_USD=10.00
MAX_SINGLE_MARKET_FRACTION=0.10
MAX_EVENT_DATE_EXPOSURE_FRACTION=0.10
LARGE_BANKROLL_EVENT_DATE_EXPOSURE_FRACTION=0.05
EVENT_DATE_EXPOSURE_TRANSITION_USD=1000
MAX_EVENT_PORTFOLIO_LEGS=2
MAX_CITY_EXPOSURE_FRACTION=0.20
MAX_TOTAL_EXPOSURE_FRACTION=0.90
```

The active Oracle VPS experiment was reset on 2026-06-05 UTC with an operator
override of `BANKROLL_USD=200`, fresh `paper_state.json`, and empty paper
ledgers. Treat post-reset results as a new performance window.

For one city-date event, the selector compares one-leg and at-most-two-leg
`YES+YES`, `YES+NO`, and `NO+NO` combinations across non-overlapping buckets.
Same-market `YES+NO`, overlapping threshold positions, and third legs are
blocked. Allocation-size candidates are capped for large ranges while keeping
the minimum order, allowed maximum, and any affordable preferred size.

## Accounting And Exit Contract

`paper_state.json` is the paper account book. It is saved by complete temp-file
write followed by atomic replacement. Bad existing state fails closed instead of
starting a fresh guessed account.

Executed account changes are paired with `paper_trades.csv`. `OPEN`, `ADD`,
`CLOSE`, and `PARTIAL_CLOSE` write `paper_state.json.journal` before mutation
and clear it only after both the state save and trade row append finish. A
leftover journal or obvious state/trade mismatch stops startup for operator
reconciliation.

Open positions close only through:

- probability stop
- model-target take profit
- overheated take profit
- valid edge-faded exit
- nowcast bucket-lock risk for held NO exact/range buckets
- max holding time
- resolved settlement

Exit triggers use after-fee liquidation PnL. Evaluation failure sentinels such
as `net_edge=-999` with no executable `p_exec` are not exit signals.

If an actual exit signal fires but no executable close is available, the broker
keeps the blocker action instead of pretending to sell. No executable bid depth
logs `HOLD_NO_LIQUIDITY`; stale executable WebSocket depth logs
`HOLD_STREAM_UNHEALTHY`; the reason or metadata must preserve the original
`exit_trigger`.

Resolved settlement requires a proven YES/NO winner. Explicit winner fields are
preferred; closed binary `outcomePrices` are accepted only when YES/NO are
exactly `1/0` or `0/1`.

## Runtime Data Contract

Paper-report readers treat `paper_decisions.csv` and `paper_trades.csv` as
source ledgers. Full-history reports may scan every row, but they must stream
rows and keep only aggregates, market-level lookup state, or bounded research
samples in memory.

`paper_raw_snapshots.jsonl` is high-volume diagnostic evidence, not a source
ledger. Normal snapshots default to error-only, debug mode is bounded, active
raw snapshots rotate over 100MB into compressed `data/archive/`, and disk
pressure may suspend raw writes with a runner-status warning.

Dashboard scanner totals expose their counting scope:
`decision_totals_exact=true` means full-ledger totals, and
`decision_totals_scope=recent_tail` means large-file tail protection was used.

## SKIP Diagnostics Contract

SKIP is a safe decision, not a final explanation. Repeated SKIPs must be
classified before changing strategy thresholds or risk settings.

Use `docs/codex/skip-diagnostics.md` to separate:

- account-safety SKIPs
- minimum-order or budget SKIPs
- market-liquidity SKIPs
- weather-data or parser SKIPs
- strategy-threshold SKIPs

A future paper-only SKIP diagnosis report may aggregate `paper_decisions.csv`,
`paper_event_portfolios.jsonl`, runner status, and order-book health. It must
not feed live orders or automatic threshold changes.

## Shadow Research Contract

Shadow research studies public external signals without copy trading. It may
read bounded public Polymarket Data API rows and manually classified public
notes, but it does not connect wallets, sign orders, submit orders, alter
positions, or feed signals into `live_paper_runner.py`.

Promotion requires:

```text
at least 20 paired resolved external-signal and bot-entry rows
external signal win rate >= matched bot entry win rate + 5 percentage points
next step is paper-only A/B experiment, never automatic copy trading
```

## Runtime Defaults

```text
ORDERBOOK_STREAM_ENABLED=true
ORDERBOOK_STREAM_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
ORDERBOOK_STREAM_STALE_SECONDS=60
RUNNER_HEALTH_STATUS_INTERVAL_SECONDS=5
STREAM_CYCLE_INTERVAL_SECONDS=2400
FORECAST_CACHE_TTL_SECONDS=2400
FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60
FORECAST_RATE_LIMIT_STATE_PATH=forecast_rate_limit_state.json
WEATHER_BIAS_JSON=
STATION_NOWCAST_ENABLED=true
STATION_NOWCAST_CACHE_TTL_SECONDS=900
STATION_NOWCAST_FRESHNESS_SECONDS=5400
STATION_NOWCAST_REQUEST_LOG_PATH=station_nowcast_request_log.jsonl
RAW_SNAPSHOTS_MODE=error
RAW_SNAPSHOTS_MAX_BYTES=104857600
RAW_SNAPSHOTS_RETENTION_DAYS=7
RAW_SNAPSHOTS_MIN_FREE_BYTES=1073741824
RAW_SNAPSHOTS_MAX_DISK_USAGE_PCT=0.90
PORTFOLIO_DECISIONS_JSONL_PATH=paper_event_portfolios.jsonl
DISCOVERY_MAX_PAGES=8
DISCOVERY_PAGE_SIZE=100
WEATHER_TAKER_FEE_RATE=0.05
SIZE_MODE=fixed_fraction
ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.06
SETTLEMENT_RUNNER_ENABLED=true
SETTLEMENT_RUNNER_MAX_FRACTION=0.25
SETTLEMENT_RUNNER_MIN_EV_MARGIN_USD=0.00
SHADOW_MAX_MARKETS=100
SHADOW_MAX_TRADES_PER_MARKET=100
SHADOW_MAX_ROWS=1000
SHADOW_MIN_TRADE_USDC=100.0
SHADOW_COMPARE_WINDOW_SECONDS=86400
REQUIRE_DATE_HINT_FOR_TRADE=true
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8787
DASHBOARD_TOKEN=
```

For public dashboard hosts such as `0.0.0.0` or `::`, use a real random
`DASHBOARD_TOKEN` with at least 32 characters and send it through the
`X-Dashboard-Token` header.

## Verification

Use the known-good local command before inventing variants:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

The root `conftest.py` keeps pytest temp files under `.pytest-tmp/`. Routine
local, VPS, SSH, and dashboard commands live in
`docs/codex/known-good-commands.md`.

## Reference Docs

- Dashboard detail: `docs/dashboard-build-spec.md`
- Station audit: `docs/station-registry-audit.md`
- Shadow research detail: `docs/shadow-signal-research.md`
- Live trading safety: `docs/live-trading-safety-plan.md`
- Historical handoff archive:
  `docs/archive/production-handoff-history-2026-06-07.md`
- Durable mistakes and prevention rules: `docs/solutions/`
