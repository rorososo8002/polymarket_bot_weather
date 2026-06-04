# Polymarket Weather Bot

Live-data paper-trading bot for Polymarket weather markets.

This bot is intentionally conservative:

- It registers 41 Polymarket weather cities, but paper trading runs only on the
  40 trading-ready cities with conflict-free Polymarket rule evidence.
- It forecasts at the exact station used by the market rules, not a city center.
- It refreshes forecast data every 2 hours by default.
- It monitors Polymarket order books through the CLOB WebSocket market stream by default.
- It is paper-only. No private key is required and no real orders are sent.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
live-paper-bot
```

Run tests from the repository root on Windows:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

The root `conftest.py` automatically keeps pytest temporary files under
`.pytest-tmp/`. You do not need to set `TMP` or `TEMP` manually. Routine local
test and Oracle VPS commands are collected in
`docs/codex/known-good-commands.md`.

Build a public whale/external-signal shadow report without changing trading:

```powershell
shadow-signal-report
```

Add `--collect` only when you intentionally want to fetch a bounded public
Polymarket Data API sample into `shadow_external_signals.jsonl`.

## Production Policy

The bot must fail closed around settlement ambiguity.

`src/weather_bot/stations.py` is the single source of truth for station policy.
Its 41-city `STATION_MAP` is the registered observation-station list: it records
the cities and station metadata the project recognizes. `TRADING_READY_STATION_MAP`
is the actual paper-trading execution list: a city enters this list only when
stored official Polymarket rule evidence confirms the same station and no
station-code conflict is known.

Karachi remains registered in `STATION_MAP` as `OPMR` / Masroor Airbase Station,
but it is excluded from `TRADING_READY_STATION_MAP` because the current official
rule evidence source points to `OPKC` while the registry uses `OPMR`. Until that
evidence conflict is reconciled, the bot must not discover, price, or trade
Karachi paper markets. Market cities outside `STATION_MAP` are unsupported;
registered cities outside `TRADING_READY_STATION_MAP` are not executable.
Neither list is a discovery-result cutoff.

Current cadence:

```text
ORDERBOOK_STREAM_ENABLED=true
ORDERBOOK_STREAM_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
ORDERBOOK_STREAM_HEARTBEAT_SECONDS=10
ORDERBOOK_STREAM_RECONNECT_SECONDS=2
FORECAST_REFRESH_INTERVAL_SECONDS=7200
FORECAST_CACHE_TTL_SECONDS=7200
DISCOVERY_MAX_PAGES=8
DISCOVERY_PAGE_SIZE=100
```

Discovery expands every supported weather event linked from Polymarket weather
category pages. An event is one city-date question such as Seoul's highest
temperature on May 25. That event can contain many binary markets: `18°C or
below`, exact buckets such as `19°C`, and `28°C or higher`.

`DISCOVERY_MAX_PAGES` and `DISCOVERY_PAGE_SIZE` only bound the fallback Gamma
API pagination path. Think of them as limiting how many backup-result pages the
bot reads if the category page is unavailable. They do not reduce the 41-city
registered station list, the 40-city trading-ready execution list, or normal
category discovery after 41 events. Runner status reports the actual event,
city, market, and token coverage.

The order-book path is event-driven from the Polymarket CLOB WebSocket market channel. Forecast and market snapshots are refreshed every 2 hours by default; forecast requests should not run on every order-book update.

## Registered Station Registry

This table is `STATION_MAP`, the registered observation-station registry. It is
not the executable trading list. Paper trading uses `TRADING_READY_STATION_MAP`;
currently 40 of these 41 cities are trading-ready, and Karachi is excluded until
the `OPMR` registry entry is reconciled with the official rule evidence that
points to `OPKC`.

| City | Station ID | Settlement station | Paper trading status |
|---|---:|---|---|
| Amsterdam | EHAM | Amsterdam Airport Schiphol Station | Trading-ready |
| Ankara | LTAC | Esenboga Intl Airport Station | Trading-ready |
| Atlanta | KATL | Hartsfield-Jackson International Airport Station | Trading-ready |
| Beijing | ZBAA | Beijing Capital International Airport Station | Trading-ready |
| Buenos Aires | SAEZ | Minister Pistarini Intl Airport Station | Trading-ready |
| Busan | RKPK | Gimhae Intl Airport Station | Trading-ready |
| Cape Town | FACT | Cape Town International Airport Station | Trading-ready |
| Chengdu | ZUUU | Chengdu Shuangliu International Airport Station | Trading-ready |
| Chicago | KORD | Chicago O'Hare Intl Airport Station | Trading-ready |
| Chongqing | ZUCK | Chongqing Jiangbei International Airport Station | Trading-ready |
| Dallas | KDAL | Dallas Love Field Station | Trading-ready |
| Guangzhou | ZGGG | Guangzhou Baiyun International Airport Station | Trading-ready |
| Helsinki | EFHK | Helsinki Vantaa Airport Station | Trading-ready |
| Hong Kong | HKO | Hong Kong Observatory | Trading-ready |
| Istanbul | LTFM | Istanbul Airport | Trading-ready |
| Jeddah | OEJN | King Abdulaziz International Airport Station | Trading-ready |
| Karachi | OPMR | Masroor Airbase Station | Excluded: rule evidence points to OPKC while the registry uses OPMR |
| London | EGLC | London City Airport Station | Trading-ready |
| Los Angeles | KLAX | Los Angeles International Airport Station | Trading-ready |
| Madrid | LEMD | Adolfo Suarez Madrid-Barajas Airport Station | Trading-ready |
| Manila | RPLL | Ninoy Aquino International Airport Station | Trading-ready |
| Miami | KMIA | Miami Intl Airport Station | Trading-ready |
| Milan | LIMC | Malpensa Intl Airport Station | Trading-ready |
| Moscow | UUWW | Vnukovo International Airport | Trading-ready |
| Munich | EDDM | Munich Airport Station | Trading-ready |
| NYC | KLGA | LaGuardia Airport Station | Trading-ready |
| Panama City | MPMG | Marcos A. Gelabert Intl Airport Station | Trading-ready |
| Paris | LFPB | Paris-Le Bourget Airport Station | Trading-ready |
| Qingdao | ZSQD | Qingdao Jiaodong International Airport Station | Trading-ready |
| Seattle | KSEA | Seattle-Tacoma International Airport Station | Trading-ready |
| Seoul | RKSI | Incheon Intl Airport Station | Trading-ready |
| Shanghai | ZSPD | Shanghai Pudong International Airport Station | Trading-ready |
| Shenzhen | ZGSZ | Shenzhen Bao'an International Airport Station | Trading-ready |
| Singapore | WSSS | Singapore Changi Airport Station | Trading-ready |
| Taipei | RCSS | Taipei Songshan Airport Station | Trading-ready |
| Tel Aviv | LLBG | Ben Gurion International Airport | Trading-ready |
| Tokyo | RJTT | Tokyo Haneda Airport Station | Trading-ready |
| Toronto | CYYZ | Toronto Pearson Intl Airport Station | Trading-ready |
| Warsaw | EPWA | Warsaw Chopin Airport Station | Trading-ready |
| Wellington | NZWN | Wellington Intl Airport Station | Trading-ready |
| Wuhan | ZHHH | Wuhan Tianhe International Airport Station | Trading-ready |

## Data Flow

```text
Polymarket category/Gamma discovery
  -> fetch city-date weather events
  -> expand exact, lower-tail, and upper-tail binary markets
  -> parse registered 41-city weather question
  -> station lookup in STATION_MAP
  -> trading-ready gate in TRADING_READY_STATION_MAP
  -> Open-Meteo ensemble forecast at settlement station
  -> CLOB WebSocket book/price_change events
  -> cached YES/NO order-book VWAP
  -> executable net-return filter
  -> city-date portfolio selection with one shared correlated-risk budget
  -> risk, exposure, probability stop threshold
  -> PaperBroker open/close decision
  -> CSV, state JSON, event-portfolio JSONL, raw snapshot logs

Public shadow research path:
  -> discover supported weather markets
  -> fetch bounded public Data API trades
  -> store shadow_external_signals.jsonl
  -> compare timing, side, and later outcome against paper_decisions.csv
  -> write shadow_signal_report.md
```

## Strategy Logic

YES edge:

```text
P_model_yes - P_exec_yes - fee - model_margin - resolution_margin
```

NO edge:

```text
(1 - P_model_yes) - P_exec_no - fee - model_margin - resolution_margin
```

Entry requires:

```text
net_edge > MIN_NET_EDGE
expected_net_return >= ENTRY_MIN_EXPECTED_NET_RETURN_PCT
confidence >= required confidence
date_hint present
trading-ready station rule evidence verified
exposure caps pass
probability stop threshold recorded
```

Default sizing is fixed-fraction paper sizing. The runner first calculates a
conservative reference bankroll:

```text
cost_basis_bankroll = cash + open-position entry cost
liquidation_bankroll = cash + executable sell value of open positions after exit fee
entry_bankroll = min(cost_basis_bankroll, liquidation_bankroll)
entry_usd = entry_bankroll * ENTRY_FRACTION  # all-in budget including entry fee
```

Below `$1,000`, at most two complementary city-date legs share one 10% event
budget. At `$1,000` or more, the shared event budget shrinks to 5%. One opened
leg must be at least `$10`, and one strong leg may use the full event budget.
The selector compares distinct-bucket `YES+YES`, `YES+NO`, and `NO+NO`
combinations after normalizing event probabilities. One city's different dates
share a 20% cap, total open paper exposure is capped at 90%, unrealized profits
do not increase new-entry sizing, and missing held-position valuation pauses
new entries.

`PaperBroker` charges the modeled taker fee to the paper wallet on entry,
partial close, and normal close. Dashboard market value and unrealized PnL use
the same conservative after-exit-fee liquidation value. Settlement payouts are
binary `0` or `1`, where the fee curve is zero.

On profit exits, strong low-cost positions can recover principal while keeping
a bounded settlement runner. The broker compares fee-adjusted sell-now value
with conservative settlement expected value, sells the principal-recovery
tranche when settlement is still worth holding, and caps the runner at 25% by
default. Probability stops, edge-fade exits, max-hold exits, settlement
resolution, and low-liquidity limits still override the runner.

## Files

```text
src/weather_bot/stations.py           registered station registry and trading-ready subset
src/weather_bot/weather_client.py     question parser for supported cities
src/weather_bot/probability.py        Open-Meteo ensemble probability model
src/weather_bot/polymarket_client.py  Polymarket Gamma/CLOB public data
src/weather_bot/realtime_orderbook.py Polymarket CLOB WebSocket order-book cache
src/weather_bot/portfolio.py          city-date portfolio selection and budget
src/weather_bot/live_paper_runner.py  main paper-trading loop
src/weather_bot/paper.py              local paper broker and logs
src/weather_bot/edge.py               YES/NO executable edge
src/weather_bot/exit_policy.py        probability-stop, take-profit, overheat, edge-fade exits
src/weather_bot/dashboard.py          local status dashboard
src/weather_bot/shadow_signals.py     public external-signal research only
```

Production handoff docs:

```text
AGENTS.md
docs/production-implementation-plan.md
docs/production-progress.md
docs/production-decisions.md
docs/oracle-migration-handoff.md
```

## Important Defaults

```text
BANKROLL_USD=100
ENTRY_FRACTION=0.10
MIN_NET_EDGE=0.05
ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.06
WEATHER_TAKER_FEE_RATE=0.05
SETTLEMENT_RUNNER_ENABLED=true
SETTLEMENT_RUNNER_MAX_FRACTION=0.25
SETTLEMENT_RUNNER_MIN_EV_MARGIN_USD=0.00
PROBABILITY_STOP_DROP_THRESHOLD=0.10
MODEL_ERROR_MARGIN=0.03
RESOLUTION_ERROR_MARGIN=0.01
MAX_SINGLE_MARKET_FRACTION=0.10
MIN_ORDER_USD=10.00
MAX_TOTAL_EXPOSURE_FRACTION=0.90
MAX_CITY_EXPOSURE_FRACTION=0.20
MAX_EVENT_DATE_EXPOSURE_FRACTION=0.10
LARGE_BANKROLL_EVENT_DATE_EXPOSURE_FRACTION=0.05
EVENT_DATE_EXPOSURE_TRANSITION_USD=1000
MAX_EVENT_PORTFOLIO_LEGS=2
ENABLE_PRECIPITATION_MARKETS=false
SHADOW_MAX_MARKETS=100
SHADOW_MAX_TRADES_PER_MARKET=100
SHADOW_MAX_ROWS=1000
SHADOW_MIN_TRADE_USDC=100.0
```

Precipitation and snow markets remain disabled by default because they are noisier than temperature markets.

## Current Limitations

- Market discovery and forecast signal snapshots refresh on a 30-minute cycle.
- Forecasts are station-based but station-level bias calibration is still neutral.
- Execution is paper-only.
- Whale and external-signal research is shadow-only until enough resolved public
  signals justify a separate paper-only experiment.
