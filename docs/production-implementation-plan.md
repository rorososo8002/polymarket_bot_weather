# Production Implementation Summary

## Goal

Run a conservative paper-trading bot for Polymarket weather markets using only
verified settlement stations and reproducible paper accounting.

## Non-Negotiable Rules

- Register only the 41 cities in `src/weather_bot/stations.py`.
- Execute the paper strategy only on temperature markets. Rain, snow,
  precipitation, and other non-temperature markets are outside the current
  experiment and are excluded before forecast probability calculation.
- Treat `STATION_MAP` as the single source of truth for registered
  settlement-station metadata, not as proof that a city may be traded.
- Treat `TRADING_READY_STATION_MAP` as the paper-trading execution universe. A
  city is trading-ready only when stored official Polymarket rule evidence
  confirms the settlement station and no station-code conflict is known.
  Current count: 40 trading-ready cities. Karachi remains registered in
  `STATION_MAP`, but excluded from execution until the `OPMR` registry entry is
  reconciled with the official rule evidence that points to `OPKC`.
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
- Treat WebSocket freshness as executable-depth freshness. Only `book`
  snapshots and `price_change` updates refresh the usable order-book clock;
  indicative `best_bid_ask` messages do not. Stale or dead WebSocket health
  blocks new entries and pauses held-position exit evaluation until executable
  WebSocket depth resumes. A dead receiver thread may rebuild the WebSocket
  stream, but the bot must not silently fall back to REST polling.
- Parse `book` snapshots and `price_change` level updates defensively. A level
  with non-numeric, non-finite, negative, or out-of-range price/size is ignored,
  and malformed whole-message shapes fail closed instead of replacing the
  executable order book.
- Persist `paper_state.json` through an atomic temp-file replace. Existing
  corrupt, unreadable, structurally invalid, or position-field invalid paper
  state fails closed instead of starting a new default account.
- Public dashboard binding fails closed unless `DASHBOARD_TOKEN` is at least
  32 characters and not an obvious weak example value. Local development on
  `127.0.0.1` or `localhost` may still run without a token.
- Boolean environment settings accept only explicit true/false aliases. Unknown
  values fail startup with `ValueError` instead of silently becoming `False`.
- Numeric paper-money, risk, fee, and runtime-cadence settings are validated
  when `Settings` is created. Money amounts and runtime intervals that must
  represent a real positive budget/window must be greater than 0; risk
  fractions and the weather taker fee rate must stay between 0 and 1.
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

Paper-report readers treat `paper_decisions.csv` and `paper_trades.csv` as
source ledgers. Full-history analysis may scan every row to preserve its
meaning, but it must stream rows and keep only aggregates, market-level lookup
state, or bounded research samples in memory.

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
Those executable levels must have finite prices in the valid Polymarket token
range and finite non-negative sizes. Bad levels are discarded; a malformed
snapshot shape does not overwrite the previous executable book.
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
`os.replace`. If an existing state file is corrupt JSON, unreadable, has an
invalid account structure, or contains an invalid position field, `PaperBroker`
refuses to start so the bot cannot trade from guessed cash or hidden positions.
Position `side` must be `YES` or `NO`; `shares` must be finite and positive;
the persisted average entry price field `entry_price` must be between 0 and 1;
`cost_usd` must be non-negative; `market_id` and `token_id` must be non-empty;
and `metadata` must be a JSON object when present.

## Weather And Discovery Contract

- A weather `event` is one city-date question; a `market` is one tradable
  binary result inside that event.
- Discovery expands every supported temperature binary market inside every
  trading-ready weather-category event it finds. The 41-city station registry
  is not an event-count cutoff, and the executable universe is the 40-city
  `TRADING_READY_STATION_MAP` subset after the temperature-only filter.
- Temperature bucket probabilities use shared non-overlapping boundaries so one
  event's buckets sum to 100%.
- Same-day nowcast may adjust probability only when the provider is explicitly
  mapped to the same settlement station. Current trading-ready sources: AWC
  METAR for 39 ICAO stations and HKO max/min CSV for Hong Kong. Karachi/OPMR
  remains registered metadata only and is excluded from paper trading until its
  `OPMR`/`OPKC` rule-evidence conflict is resolved.
- Temperature nowcast derives observed high-so-far and observed low-so-far from
  one station-date response when the source provides enough observations. Use
  observed high only for daily-high markets and observed low only for daily-low
  markets; do not cross-apply one metric to the other.
- Real AWC METAR and HKO max/min HTTP attempts are counted in
  `station_nowcast_request_log.jsonl`. Cache hits do not write rows, so the log
  measures external observation usage rather than in-memory reuse.
- Missing, stale, malformed, future-date, unmapped, or unsupported nowcast data
  is not guessed. It remains forecast-only or fail-closed depending on context.
- Missing exact target-date forecasts are not guessed. Nearby forecast dates
  are not strategy data for the target city-date market.
- `WEATHER_BIAS_JSON` is optional forecast calibration data. If it is empty,
  the bot uses conservative neutral defaults. If it is explicitly set, the file
  must be readable valid JSON shaped as station IDs to numeric variable bias
  values in Fahrenheit. Missing, invalid, malformed, or non-numeric explicit
  bias files produce `forecast-unavailable` with zero confidence instead of an
  uncorrected forecast.
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

Resolved settlement requires a proven YES/NO winner. Explicit winner fields
such as `winningOutcome` are preferred. If those fields are absent on a closed
binary market, `outcomePrices` may be used only when the YES and NO prices are
exactly `1/0` or `0/1`. Ambiguous prices are not guessed and leave the paper
position open for later evidence.

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
WEATHER_BIAS_JSON=
STATION_NOWCAST_ENABLED=true
STATION_NOWCAST_CACHE_TTL_SECONDS=900
STATION_NOWCAST_FRESHNESS_SECONDS=5400
STATION_NOWCAST_REQUEST_LOG_PATH=station_nowcast_request_log.jsonl
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
REQUIRE_DATE_HINT_FOR_TRADE=true
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8787
DASHBOARD_TOKEN=
```

`DISCOVERY_MAX_PAGES` and `DISCOVERY_PAGE_SIZE` only bound fallback Gamma
pagination. They do not reduce the registered 41-city station registry, the
40-city trading-ready execution subset, or normal category discovery.

For public dashboard hosts such as `0.0.0.0` or `::`, `DASHBOARD_TOKEN` must be
a real random value with at least 32 characters rather than empty, short,
placeholder, basic, default, change-me, secret, token, password, abc, or 123456
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
