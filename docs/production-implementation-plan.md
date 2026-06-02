# Production Implementation Summary

## Goal

Run a conservative paper-trading bot for Polymarket weather markets using only
verified settlement stations and reproducible paper accounting.

## Non-Negotiable Rules

- Register only the 41 cities in `src/weather_bot/stations.py`.
- Treat `STATION_MAP` as the single source of truth for settlement-station
  metadata.
- Treat `TRADING_READY_STATION_MAP` as the execution universe. A city is
  trading-ready only when stored official Polymarket rule evidence confirms the
  settlement station and no station-code conflict is known.
- Skip unsupported cities, unsupported question shapes, stale data, missing
  order books, suspicious values, or invalid parsed data.
- Forecast dates must match the target market date exactly. If the target date
  is absent from the Open-Meteo daily forecast, skip as unavailable; do not use
  a nearest-date substitute.
- Refresh Open-Meteo forecasts no more often than every 30 minutes by default.
- Use the Polymarket CLOB WebSocket market stream for order books by default.
- Keep token IDs for open positions subscribed even when discovery moves to
  newer markets.
- Map Polymarket YES/NO token IDs only from explicit outcome labels. If
  `tokens` or `outcomes` cannot prove which `clobTokenIds` entry is YES and
  which is NO, skip the market instead of trusting list order.
- Treat `best_bid_ask` stream messages as indicative best-price references
  only. They must not create or move executable bid/ask depth.
- Persist `paper_state.json` through an atomic temp-file replace. Existing
  corrupt, unreadable, or structurally invalid paper state fails closed instead
  of starting a new default account.
- Public dashboard binding fails closed unless `DASHBOARD_TOKEN` is set to a
  non-placeholder value. Local development on `127.0.0.1` or `localhost` may
  still run without a token.
- Keep execution paper-only unless live trading is explicitly approved through
  `docs/live-trading-safety-plan.md`.

## Architecture

```text
weather event discovery
  -> supported-city parser
  -> settlement-station forecast and optional same-station nowcast
  -> CLOB WebSocket order-book cache
  -> fee-aware YES/NO VWAP edge and expected net-return filter
  -> city-date portfolio selector
  -> PaperBroker risk checks, opens, exits, settlements, and logs
  -> dashboard, runner status, reports, and shadow research artifacts
```

Shadow research is a separate public-data path:

```text
supported weather markets -> bounded public Data API rows
  -> shadow_external_signals.jsonl
  -> paired timing/side/outcome comparison with paper_decisions.csv
  -> shadow_signal_report.md
```

The shadow path is never an execution input by default.

## Code Map

```text
src/weather_bot/stations.py           city/station allowlist
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

`DECISION YES` and `DECISION NO` are model/order-book judgments, not guaranteed
opens. The broker may still block entry for exposure, same-market hedge
protection, missing token IDs, low confidence, invalid prices, insufficient
liquidity, abnormal YES+NO ask sums, weak net return, or stale dependencies.

Entry must satisfy both:

```text
net_edge > configured threshold
expected_net_return >= ENTRY_MIN_EXPECTED_NET_RETURN_PCT
```

`p_exec` is the executable ask-side VWAP and already includes entry spread and
slippage. Do not subtract entry spread or entry slippage a second time.
`best_bid_ask` may update displayed/reference best bid and ask prices, but
`p_exec` must be computed only from confirmed order-book depth carried by
`book` snapshots or `price_change` updates.
`WEATHER_TAKER_FEE_RATE=0.05` follows the Polymarket formula:

```text
fee_usdc = shares * fee_rate * price * (1 - price)
```

`size_usd` is the all-in paper-entry budget. The broker buys fewer shares than
`size_usd / p_exec` so entry notional plus fee stays inside the budget. Normal
and partial closes add only after-fee proceeds to paper cash. Dashboard market
value and new-entry liquidation bankroll also use after-exit-fee value.
`EdgeResult.size_shares`, portfolio scenario PnL, and broker-opened paper
positions use this same fee-adjusted actual share count:
`size_usd / (p_exec + fee_per_share)`.
If the conservative new-entry bankroll is not positive, or if the calculated
order is below `MIN_ORDER_USD`, the live evaluator returns SKIP before
expected-return math. It must not call positive-share helpers with zero or
below-minimum entry sizes.

`paper_state.json` is the paper account book, not a disposable cache. State
saves write a complete temporary file first and then replace the live file with
`os.replace`. If an existing state file is corrupt JSON, unreadable, or has an
invalid account structure, `PaperBroker` refuses to start so the bot cannot
trade from guessed cash or hidden positions.

## Weather And Discovery Contract

- A weather `event` is one city-date question; a `market` is one tradable
  binary result inside that event.
- Discovery expands every supported binary market inside every supported
  weather-category event it finds. The 41-city station map is not an event-count
  cutoff.
- Temperature bucket probabilities use shared non-overlapping boundaries so one
  event's buckets sum to 100%.
- Same-day nowcast may adjust probability only when the provider is explicitly
  mapped to the same settlement station. Current sources: AWC METAR for 39 ICAO
  stations, HKO max/min CSV for Hong Kong, and forecast-only for OPMR/Karachi
  until a same-station provider is verified.
- Temperature nowcast derives observed high-so-far and observed low-so-far from
  one station-date response when the source provides enough observations. Use
  observed high only for daily-high markets and observed low only for daily-low
  markets; do not cross-apply one metric to the other.
- Missing, stale, malformed, future-date, unmapped, or unsupported nowcast data
  is not guessed. It remains forecast-only or fail-closed depending on context.
- Missing exact target-date forecasts are not guessed. Nearby forecast dates
  are not strategy data for the target city-date market.
- Rule evidence is also fail-closed. If a city lacks a stored Polymarket rules
  URL/station phrase, or if the found rule source conflicts with the registry,
  discovery and probability estimation exclude it from paper trading.
- YES/NO token mapping is fail-closed. `clobTokenIds` are tradable asset IDs,
  but they are not safe to interpret by position alone. Discovery must map them
  through explicit `tokens[].outcome` or the market `outcomes` field, and skip
  if the labels are missing, duplicated, malformed, or not exactly YES/NO.

Detailed station evidence lives in `docs/station-registry-audit.md`.

## Portfolio And Risk Contract

Before new entries, calculate:

```text
cost_basis_bankroll = cash + open-position entry cost
liquidation_bankroll = cash + after-fee executable sell value of open positions
entry_bankroll = min(cost_basis_bankroll, liquidation_bankroll)
```

Unrealized profits do not increase new-entry sizing. Executable unrealized
losses reduce sizing immediately. If any held position cannot be valued from a
usable order book, new entries fail closed.
The runner surfaces that as an operator-readable SKIP, not as an exception from
zero-share expected-return calculation.

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

For one city-date event, the selector compares one-leg and at-most-two-leg
`YES+YES`, `YES+NO`, and `NO+NO` combinations across non-overlapping buckets.
Same-market `YES+NO`, overlapping threshold positions, and third legs are
blocked. Event decisions are logged to `paper_event_portfolios.jsonl`.

## Exit Policy Contract

Open positions close only through:

- probability stop
- model-target take profit
- overheated take profit
- valid edge-faded exit
- max holding time
- resolved settlement

Profit-taking exits first evaluate the settlement-runner policy. The broker
compares fee-adjusted sell-now value with conservative hold-to-settlement value.
If settlement value is at least as good, it sells a principal-recovery tranche
and caps the remaining runner at `SETTLEMENT_RUNNER_MAX_FRACTION=0.25` by
default. Active runners are rechecked with fresh probability; they are not a
risk exemption.

Evaluation failure sentinels such as `net_edge=-999` with no executable
`p_exec` are not exit signals.

## SKIP Diagnostics Contract

SKIP is a safe decision, not a final explanation. Repeated SKIPs must be
classified before changing strategy thresholds or risk settings.

Use `docs/codex/skip-diagnostics.md` to separate:

- account-safety SKIPs, such as unpriceable held positions
- minimum-order or budget SKIPs
- market-liquidity SKIPs
- weather-data or parser SKIPs
- strategy-threshold SKIPs

A future paper-only skip diagnosis report should aggregate
`paper_decisions.csv`, `paper_event_portfolios.jsonl`, runner status, and
order-book health into counts by reason category. That report is for
investigation only; it must not feed live orders or automatic threshold changes.

## Shadow Research Contract

Phase 7 studies public external signals without copy trading. It may read
bounded public Polymarket Data API rows and manually classified public notes,
but it does not connect wallets, sign orders, submit orders, alter positions, or
feed signals into `live_paper_runner.py`.

Promotion requires:

```text
at least 20 paired resolved external-signal and bot-entry rows
external signal win rate >= matched bot entry win rate + 5 percentage points
next step is paper-only A/B experiment, never automatic copy trading
```

Bot `SKIP` rows remain diagnostics but cannot inflate public-signal advantage.
Detailed shadow rules live in `docs/shadow-signal-research.md`.

## Runtime Defaults

```text
ORDERBOOK_STREAM_ENABLED=true
ORDERBOOK_STREAM_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
ORDERBOOK_STREAM_STALE_SECONDS=60
RUNNER_HEALTH_STATUS_INTERVAL_SECONDS=5
FORECAST_REFRESH_INTERVAL_SECONDS=1800
FORECAST_CACHE_TTL_SECONDS=1800
STATION_NOWCAST_ENABLED=true
STATION_NOWCAST_CACHE_TTL_SECONDS=900
STATION_NOWCAST_FRESHNESS_SECONDS=5400
DISCOVERY_MAX_PAGES=8
DISCOVERY_PAGE_SIZE=100
WEATHER_TAKER_FEE_RATE=0.05
ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.06
SETTLEMENT_RUNNER_ENABLED=true
SETTLEMENT_RUNNER_MAX_FRACTION=0.25
SETTLEMENT_RUNNER_MIN_EV_MARGIN_USD=0.00
SHADOW_MAX_MARKETS=100
SHADOW_MAX_TRADES_PER_MARKET=100
SHADOW_MAX_ROWS=1000
SHADOW_MIN_TRADE_USDC=100.0
SHADOW_COMPARE_WINDOW_SECONDS=86400
ENABLE_PRECIPITATION_MARKETS=false
REQUIRE_DATE_HINT_FOR_TRADE=true
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8787
DASHBOARD_TOKEN=
```

`DISCOVERY_MAX_PAGES` and `DISCOVERY_PAGE_SIZE` only bound fallback Gamma
pagination. They do not reduce the verified 41-city allowlist and do not cap
normal category discovery.

For public dashboard hosts such as `0.0.0.0`, `DASHBOARD_TOKEN` must be a real
long random value rather than empty, placeholder, basic, default, or change-me
text. Query-string tokens are accepted for first-load compatibility but are
redacted from logs, stored in browser local storage, and subsequent API polling
uses the `X-Dashboard-Token` header.

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
- Strategy roadmap: `docs/strategy-upgrade-roadmap.md`
- Durable mistakes and prevention rules: `docs/solutions/`
