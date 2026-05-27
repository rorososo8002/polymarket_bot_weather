# Production Status

## Current Operating Contract

- The bot is a live-data paper-trading service.
- The supported universe is exactly the 41 cities in `src/weather_bot/stations.py`.
- `STATION_MAP` is the settlement-station source of truth.
- `Settings.max_markets` defaults to `SUPPORTED_CITY_COUNT`, so the scan size follows the station registry.
- Forecasts use the mapped settlement station, not city-center coordinates.
- Forecast data refreshes every 30 minutes by default.
- Order books stream through the Polymarket CLOB WebSocket market channel by default.
- Execution is paper-only through `PaperBroker`; no wallet keys, signing, or live order submission are present.

## Implemented

- Station allowlist and station coordinates for 41 verified Polymarket weather cities.
- Parser gating so unsupported cities are not treated as tradable markets.
- Polymarket discovery gating so weather-shaped markets outside `STATION_MAP` are skipped.
- Probability estimation that returns `unsupported-station` for unmapped settlement stations.
- 30-minute Open-Meteo forecast cache TTL and refresh cadence.
- WebSocket-backed order-book cache and event-driven paper evaluations.
- Probability-based stop policy instead of fixed token-price stop loss.
- Edge-faded exits ignore invalid `net_edge=-999` sentinels when no executable
  `p_exec` exists, preventing close-and-reopen churn from transient invalid book
  evaluations.
- Exposure caps for market, city, and city-date concentration.
- Runner heartbeat file for dashboard-visible progress.
- VPS systemd examples for the paper bot and dashboard.
- Production docs now define decision events and exit rules as an executable
  handoff contract for future AI agents.

## Verification Focus

Run these before changing production behavior:

```powershell
$env:PYTHONPATH='src'
$env:TMP=(Resolve-Path '.pytest-tmp-all').Path
$env:TEMP=$env:TMP
python -m pytest -q
```

Important coverage already in the suite:

- `len(STATION_MAP) == 41`
- `Settings.max_markets == SUPPORTED_CITY_COUNT`
- unverified cities are not parsed or traded
- discovery rejects non-weather false positives
- realtime WebSocket mode is required by `run_forever()`
- forecast cache avoids repeated Open-Meteo calls
- unavailable ensemble forecasts are not treated as strategy data
- invalid edge sentinels do not trigger edge-faded exits

## Remaining Production Hardening

- Add stream health telemetry for reconnect count, stale book age, and startup snapshot coverage.
- Calibrate `PROBABILITY_STOP_DROP_THRESHOLD` after enough resolved paper trades exist.
- Add station-level forecast bias files after enough station evidence exists.
- Build a recurring paper-trade review loop that compares realized PnL by city,
  threshold distance, time-to-resolution, spread, slippage, and forecast error.
- Research and test sizing/exit upgrades using expected value, calibrated
  probability, fractional Kelly, liquidity-adjusted edge, and drawdown limits.
- Keep live-wallet execution out of scope until a separate key-isolation and kill-switch design is requested.

## Handoff

Start with `AGENTS.md`, `README.md`, and `docs/production-decisions.md`. Treat `src/weather_bot/stations.py` as the source of truth when code and docs disagree.
