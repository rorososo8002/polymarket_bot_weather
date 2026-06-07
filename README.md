# Polymarket Weather Bot

Paper-only Polymarket weather-market bot for temperature markets.

The project measures a strategy with live public data, but it never connects a
wallet, signs orders, sends live orders, or redeems markets on chain.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
copy .env.example .env
live-paper-bot
```

Run tests from the repository root:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

The root `conftest.py` keeps pytest temporary files under `.pytest-tmp/`.
Routine local, VPS, SSH, and dashboard commands live in
`docs/codex/known-good-commands.md`.

## Operating Boundary

- Paper trading only. Real wallets, private keys, live orders, copy trading,
  and private data collection are out of scope unless a separate live-trading
  safety project is explicitly approved.
- Temperature markets only. Rain, snow, precipitation, wind, humidity, and
  every other non-temperature weather market must fail closed before forecast,
  order-book subscription, or paper trade logging.
- `STATION_MAP` is the registered city/station registry. `TRADING_READY_STATION_MAP`
  is the actual execution universe. Karachi stays registered but excluded until
  station-rule evidence is reconciled.
- Missing, stale, malformed, unsupported, suspicious, or conflictful data means
  skip. The bot must not guess.
- Use the Polymarket CLOB WebSocket market stream for executable order books.
  Do not silently replace realtime streaming with polling.

## Architecture

```text
Polymarket weather discovery
  -> supported city/date/temperature parser
  -> trading-ready station gate
  -> exact-date Open-Meteo ensemble forecast
  -> optional same-station nowcast
  -> CLOB WebSocket executable order-book cache
  -> fee-aware YES/NO edge and expected-return filter
  -> city-date portfolio selector
  -> PaperBroker accounting, exits, settlement, and ledgers
  -> dashboard and paper report
```

## Main Files

```text
src/weather_bot/stations.py           station registry and trading-ready subset
src/weather_bot/weather_client.py     weather-question parser
src/weather_bot/probability.py        Open-Meteo probability model and forecast cache
src/weather_bot/nowcast.py            same-station observed high/low providers
src/weather_bot/polymarket_client.py  Polymarket Gamma and CLOB public data
src/weather_bot/realtime_orderbook.py CLOB WebSocket order-book cache
src/weather_bot/edge.py               executable price, fee, and edge math
src/weather_bot/portfolio.py          city-date risk budget and leg selection
src/weather_bot/paper.py              paper broker, state, trades, settlement
src/weather_bot/live_paper_runner.py  main paper loop and realtime orchestration
src/weather_bot/dashboard.py          read-only operator dashboard API/server
src/weather_bot/analyze_paper.py      paper-performance report
```

## Runtime Ledgers

These files are generated at runtime and are intentionally ignored by git:

```text
paper_state.json              current paper account book
paper_trades.csv              executed paper-action receipt ledger
paper_decisions.csv           strategy-decision evidence ledger
paper_event_portfolios.jsonl  city-date portfolio-selection diagnostics
paper_raw_snapshots.jsonl     bounded error/debug evidence, not an account book
forecast_cache.json           forecast answer cache, not an API call ledger
forecast_request_log.jsonl    real Open-Meteo request ledger
paper_runner_status.json      current runner heartbeat/status
runtime/                      deployment/runtime data directory
```

For a fresh local experiment, delete ignored runtime ledgers only when you
intentionally want a new paper-performance window. Do not mix old ledger rows
with a new bankroll.

## Documentation Map

```text
AGENTS.md                             repository operating constitution
docs/active/current-task.md           only default unfinished-work handoff card
docs/production-decisions.md          active safety/trading/runtime rules
docs/production-implementation-plan.md strategy and architecture contract
docs/codex/known-good-commands.md     verified local/VPS command shapes
docs/codex/runtime-data.md            safe handling of large runtime files
docs/live-trading-safety-plan.md      required read before any live-trading work
docs/solutions/                       durable mistake-prevention notes
```

## Important Defaults

```text
BANKROLL_USD=100
ENTRY_FRACTION=0.10
MIN_ORDER_USD=10.00
MIN_NET_EDGE=0.05
ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.06
WEATHER_TAKER_FEE_RATE=0.05
MAX_TOTAL_EXPOSURE_FRACTION=0.90
MAX_CITY_EXPOSURE_FRACTION=0.20
MAX_EVENT_DATE_EXPOSURE_FRACTION=0.10
LARGE_BANKROLL_EVENT_DATE_EXPOSURE_FRACTION=0.05
MAX_EVENT_PORTFOLIO_LEGS=2
FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60
FORECAST_CACHE_TTL_SECONDS=2400
STREAM_CYCLE_INTERVAL_SECONDS=2400
ORDERBOOK_STREAM_ENABLED=true
RAW_SNAPSHOTS_MODE=error
```

## Verification

Before trusting a change:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

Dashboard or VPS changes require the extra deployment and status checks listed
in `AGENTS.md` and `docs/codex/known-good-commands.md`.
