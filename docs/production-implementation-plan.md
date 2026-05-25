# Production Implementation Summary

## Goal

Run a conservative paper-trading bot for Polymarket weather markets using only verified settlement stations.

## Non-Negotiable Rules

- Trade only the 41 cities in `src/weather_bot/stations.py`.
- Use `STATION_MAP` as the single source of truth for settlement stations.
- Skip any weather market whose parsed city is not in `STATION_MAP`.
- Refresh Open-Meteo forecast data no more often than every 30 minutes by default.
- Monitor order books through the Polymarket CLOB WebSocket market stream by default.
- Keep paper-trading behavior intact unless live execution is explicitly requested.

## Architecture

```text
Polymarket discovery
  -> parse weather question against supported city registry
  -> reject unmapped cities and unsupported question shapes
  -> estimate station-based weather probability
  -> stream CLOB order-book updates
  -> compute executable YES/NO VWAP edge
  -> apply risk, exposure, and probability-stop rules
  -> open/close paper positions
  -> write state, decisions, trades, raw snapshots, and runner heartbeat
```

## Code Map

```text
src/weather_bot/stations.py           41-city station registry and supported count
src/weather_bot/weather_client.py     question parser backed by station coordinates
src/weather_bot/polymarket_client.py  Gamma discovery and CLOB REST book parsing
src/weather_bot/realtime_orderbook.py CLOB WebSocket order-book cache
src/weather_bot/probability.py        station-based Open-Meteo ensemble model
src/weather_bot/live_paper_runner.py  paper loop and realtime stream orchestration
src/weather_bot/paper.py              paper broker, logs, settlements, exits
src/weather_bot/exit_policy.py        probability stop and profit exits
src/weather_bot/dashboard.py          local read-only dashboard
```

## Runtime Defaults

```text
ORDERBOOK_STREAM_ENABLED=true
ORDERBOOK_STREAM_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
FORECAST_REFRESH_INTERVAL_SECONDS=1800
FORECAST_CACHE_TTL_SECONDS=1800
MAX_MARKETS=41
ENABLE_PRECIPITATION_MARKETS=false
ALLOW_DETERMINISTIC_FALLBACK_TRADES=false
REQUIRE_DATE_HINT_FOR_TRADE=true
```

`MAX_MARKETS=41` matches the current station registry. In code, the default is derived from `SUPPORTED_CITY_COUNT`; environment files keep the explicit value so deployment config is easy to inspect.

## Verification

```powershell
$env:PYTHONPATH='src'
$env:TMP=(Resolve-Path '.pytest-tmp-all').Path
$env:TEMP=$env:TMP
python -m pytest -q
```

For a local paper service start on Windows, run:

```powershell
$env:PYTHONPATH='src'
python -m weather_bot.live_paper_runner
```

For VPS deployment, use `docs/VPS_LIVE_PAPER.md`.

## Source Notes

- Station choices come from Polymarket weather rule text and resolution sources.
- Hong Kong uses the Hong Kong Observatory daily extract.
- Live wallet execution is intentionally absent and requires a separate production-safety design.
