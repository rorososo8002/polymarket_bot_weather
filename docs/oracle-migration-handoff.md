# Oracle Migration Handoff

Date: 2026-05-28 Asia/Seoul

## Current State

- Oracle is the only active deployment target.
- Oracle SSH target: `ubuntu@140.245.69.242`.
- Canonical private key file: `ssh-key-2026-05-25.key` in the Oracle SSH
  directory under `C:\Users\wpdla\Documents`.
- Legacy pre-Oracle VPS helpers, keys, and runtime paths must not be used.
- Do not connect to retired pre-Oracle hosts while diagnosing the bot, dashboard, or deployment.
- If `127.0.0.1:8787` does not load, check the Oracle SSH tunnel and Oracle dashboard service only.
- The bot remains paper-only. Do not add private keys or live-wallet execution.

## Local Repo State To Preserve

There are uncommitted local changes that should be reviewed before committing:

- Forecast HTTP calls now use one-at-a-time Open-Meteo drip-feed defaults:
  - `STREAM_CYCLE_INTERVAL_SECONDS=2400`
  - `FORECAST_CACHE_TTL_SECONDS=2400`
  - `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60`
- Dashboard CSV loading was patched to read only recent tail rows instead of loading full runtime CSV files into memory.
- Runtime/log reading rule was added to `AGENTS.md`: use recent lines or targeted filters by default.
- Some unused code was removed from probability/weather client modules.
- Relevant tests were updated and `python -m pytest -q` passed after the local changes.

Do not copy old pre-Oracle runtime files to Oracle. Start the Oracle paper run from clean runtime data.

## Next Work

1. Read `docs/codex/known-good-commands.md`, then SSH into Oracle using its
   verified preflight and the fixed target and private key above.
2. Prepare the server if a rebuild is required:
   - install Python, git, venv, systemd dependencies
   - add 2GB swap if the instance has 1GB RAM
   - open dashboard port only if needed
3. Deploy the current repo to Oracle.
4. Create Oracle env files with safe production defaults:
   - `DISCOVERY_MAX_PAGES=8`
   - `DISCOVERY_PAGE_SIZE=100`
   - `ORDERBOOK_STREAM_ENABLED=true`
   - `ORDERBOOK_STREAM_STALE_SECONDS=60`
   - `RUNNER_HEALTH_STATUS_INTERVAL_SECONDS=5`
   - `STREAM_CYCLE_INTERVAL_SECONDS=2400`
   - `FORECAST_CACHE_TTL_SECONDS=2400`
   - `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60`
   - `FORECAST_RATE_LIMIT_STATE_PATH=/opt/polymarket-weather-bot/data/forecast_rate_limit_state.json`
   - paper trading only
5. Start services on Oracle:
   - `polymarket-weather-bot`
   - `polymarket-weather-dashboard`
6. Verify before trusting the dashboard:
   - services are active
   - dashboard status shows `scan_interval_seconds=2400`
   - bot reaches `phase=streaming`
   - message shows token, binary-market, event, and city coverage, for example
     `websocket streaming 82 tokens across 41 markets, 7 events, 4 cities`
   - dashboard `Forecast Health` shows a recent successful forecast or an explicit
     stale warning
   - dashboard `WebSocket Health` shows a live receiver thread and a recent
     order-book update
   - decisions either use real forecast data or fail closed with `forecast-unavailable`

## Important Guardrails

- Do not use deterministic or fake forecast fallback for strategy validation.
- If Open-Meteo is unavailable, do not trade from guessed data.
- If a new forecast provider is added, record the provider in decision logs so results are not mixed with Open-Meteo results.
- Keep Open-Meteo calls cached and counted. Add request/429 counters before relying on a longer run.
- Keep the 41-city station registry, but use `TRADING_READY_STATION_MAP` as the
  trading universe so cities without rule evidence or with station-code conflicts
  are excluded.
