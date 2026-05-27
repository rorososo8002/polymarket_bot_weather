# Polymarket Weather Bot

Live-data paper-trading bot for Polymarket weather markets.

This bot is intentionally conservative:

- It trades only the 41 Polymarket weather cities with verified settlement stations.
- It forecasts at the exact station used by the market rules, not a city center.
- It refreshes forecast data every 30 minutes by default.
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

Run tests:

```powershell
$env:PYTHONPATH='src'
python -m pytest -q
```

If Windows blocks pytest's default temp directory, use a workspace temp path:

```powershell
New-Item -ItemType Directory -Force -Path '.pytest-tmp' | Out-Null
$env:PYTHONPATH='src'
$env:TMP=(Resolve-Path '.pytest-tmp').Path
$env:TEMP=$env:TMP
python -m pytest -q
```

## Production Policy

The bot must fail closed around settlement ambiguity.

`src/weather_bot/stations.py` is the single source of truth for supported cities. Market discovery, parsing, probability estimation, and the default scan count all use that same 41-city station set. If a market city is not in `STATION_MAP`, the bot should not discover, price, or trade it.

Current cadence:

```text
ORDERBOOK_STREAM_ENABLED=true
ORDERBOOK_STREAM_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
ORDERBOOK_STREAM_HEARTBEAT_SECONDS=10
ORDERBOOK_STREAM_RECONNECT_SECONDS=2
FORECAST_REFRESH_INTERVAL_SECONDS=1800
FORECAST_CACHE_TTL_SECONDS=1800
MAX_MARKETS=41
```

`MAX_MARKETS=41` is explicit in deployment env files for readability. In code, the default comes from `SUPPORTED_CITY_COUNT = len(STATION_MAP)`.

The order-book path is event-driven from the Polymarket CLOB WebSocket market channel. Forecast and market snapshots are refreshed every 30 minutes; forecast requests should not run on every order-book update.

## Verified City Set

| City | Station ID | Settlement station |
|---|---:|---|
| Amsterdam | EHAM | Amsterdam Airport Schiphol Station |
| Ankara | LTAC | Esenboga Intl Airport Station |
| Atlanta | KATL | Hartsfield-Jackson International Airport Station |
| Beijing | ZBAA | Beijing Capital International Airport Station |
| Buenos Aires | SAEZ | Minister Pistarini Intl Airport Station |
| Busan | RKPK | Gimhae Intl Airport Station |
| Cape Town | FACT | Cape Town International Airport Station |
| Chengdu | ZUUU | Chengdu Shuangliu International Airport Station |
| Chicago | KORD | Chicago O'Hare Intl Airport Station |
| Chongqing | ZUCK | Chongqing Jiangbei International Airport Station |
| Dallas | KDAL | Dallas Love Field Station |
| Guangzhou | ZGGG | Guangzhou Baiyun International Airport Station |
| Helsinki | EFHK | Helsinki Vantaa Airport Station |
| Hong Kong | HKO | Hong Kong Observatory |
| Istanbul | LTFM | Istanbul Airport |
| Jeddah | OEJN | King Abdulaziz International Airport Station |
| Karachi | OPMR | Masroor Airbase Station |
| London | EGLC | London City Airport Station |
| Los Angeles | KLAX | Los Angeles International Airport Station |
| Madrid | LEMD | Adolfo Suarez Madrid-Barajas Airport Station |
| Manila | RPLL | Ninoy Aquino International Airport Station |
| Miami | KMIA | Miami Intl Airport Station |
| Milan | LIMC | Malpensa Intl Airport Station |
| Moscow | UUWW | Vnukovo International Airport |
| Munich | EDDM | Munich Airport Station |
| NYC | KLGA | LaGuardia Airport Station |
| Panama City | MPMG | Marcos A. Gelabert Intl Airport Station |
| Paris | LFPB | Paris-Le Bourget Airport Station |
| Qingdao | ZSQD | Qingdao Jiaodong International Airport Station |
| Seattle | KSEA | Seattle-Tacoma International Airport Station |
| Seoul | RKSI | Incheon Intl Airport Station |
| Shanghai | ZSPD | Shanghai Pudong International Airport Station |
| Shenzhen | ZGSZ | Shenzhen Bao'an International Airport Station |
| Singapore | WSSS | Singapore Changi Airport Station |
| Taipei | RCSS | Taipei Songshan Airport Station |
| Tel Aviv | LLBG | Ben Gurion International Airport |
| Tokyo | RJTT | Tokyo Haneda Airport Station |
| Toronto | CYYZ | Toronto Pearson Intl Airport Station |
| Warsaw | EPWA | Warsaw Chopin Airport Station |
| Wellington | NZWN | Wellington Intl Airport Station |
| Wuhan | ZHHH | Wuhan Tianhe International Airport Station |

## Data Flow

```text
Polymarket category/Gamma discovery
  -> parse supported 41-city weather question
  -> station lookup in STATION_MAP
  -> Open-Meteo ensemble forecast at settlement station
  -> CLOB WebSocket book/price_change events
  -> cached YES/NO order-book VWAP
  -> edge, risk, exposure, probability stop threshold
  -> PaperBroker open/close decision
  -> CSV, state JSON, raw snapshot logs
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
confidence >= required confidence
date_hint present
station verified
exposure caps pass
probability stop threshold recorded
```

Default sizing is fixed-fraction paper sizing:

```text
entry_usd = current_bankroll * ENTRY_FRACTION
```

## Files

```text
src/weather_bot/stations.py           verified city/station allowlist
src/weather_bot/weather_client.py     question parser for supported cities
src/weather_bot/probability.py        Open-Meteo ensemble probability model
src/weather_bot/polymarket_client.py  Polymarket Gamma/CLOB public data
src/weather_bot/realtime_orderbook.py Polymarket CLOB WebSocket order-book cache
src/weather_bot/live_paper_runner.py  main paper-trading loop
src/weather_bot/paper.py              local paper broker and logs
src/weather_bot/edge.py               YES/NO executable edge
src/weather_bot/exit_policy.py        probability-stop, take-profit, overheat, edge-fade exits
src/weather_bot/dashboard.py          local status dashboard
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
BANKROLL_USD=1000
ENTRY_FRACTION=0.05
MIN_NET_EDGE=0.05
PROBABILITY_STOP_DROP_THRESHOLD=0.10
MODEL_ERROR_MARGIN=0.03
RESOLUTION_ERROR_MARGIN=0.01
MAX_TOTAL_EXPOSURE_FRACTION=0.30
MAX_CITY_EXPOSURE_FRACTION=0.08
MAX_EVENT_DATE_EXPOSURE_FRACTION=0.05
ENABLE_PRECIPITATION_MARKETS=false
```

Precipitation and snow markets remain disabled by default because they are noisier than temperature markets.

## Current Limitations

- Market discovery and forecast signal snapshots refresh on a 30-minute cycle.
- Forecasts are station-based but station-level bias calibration is still neutral.
- Execution is paper-only.

Next production step: add production monitoring around WebSocket reconnects, stale snapshots, and stream health before any live-wallet execution work.
