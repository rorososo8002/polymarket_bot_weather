# Oracle Migration Handoff

Date: 2026-05-28 Asia/Seoul

## Current State

- Vultr VPS: `141.164.56.246`
- Vultr services are intentionally stopped and disabled:
  - `polymarket-weather-bot`
  - `polymarket-weather-dashboard`
- No `live-paper-bot` or `weather-dashboard` process should be running on Vultr.
- Reason for stopping: Open-Meteo daily limit was hit on the Vultr public IP. Do not restart Vultr services unless explicitly asked.
- The bot remains paper-only. Do not add private keys or live-wallet execution.

## Local Repo State To Preserve

There are uncommitted local changes that should be reviewed before committing:

- Forecast cadence restored to 30 minutes:
  - `FORECAST_REFRESH_INTERVAL_SECONDS=1800`
  - `FORECAST_CACHE_TTL_SECONDS=1800`
- Dashboard CSV loading was patched to read only recent tail rows instead of loading full runtime CSV files into memory.
- Runtime/log reading rule was added to `AGENTS.md`: use recent lines or targeted filters by default.
- Some unused code was removed from probability/weather client modules.
- Relevant tests were updated and `python -m pytest -q` passed after the local changes.

Do not copy old Vultr runtime files to Oracle. Start the Oracle paper run from clean runtime data.

## Next Work

1. Get the new Oracle VPS connection details:
   - public IP
   - SSH username
   - private key path
2. SSH into Oracle and prepare the server:
   - install Python, git, venv, systemd dependencies
   - add 2GB swap if the instance has 1GB RAM
   - open dashboard port only if needed
3. Deploy the current repo to Oracle.
4. Create Oracle env files with safe production defaults:
   - `MAX_MARKETS=41`
   - `ORDERBOOK_STREAM_ENABLED=true`
   - `FORECAST_REFRESH_INTERVAL_SECONDS=1800`
   - `FORECAST_CACHE_TTL_SECONDS=1800`
   - paper trading only
5. Start services on Oracle:
   - `polymarket-weather-bot`
   - `polymarket-weather-dashboard`
6. Verify before trusting the dashboard:
   - services are active
   - dashboard status shows `scan_interval_seconds=1800`
   - bot reaches `phase=streaming`
   - message shows `websocket streaming 82 tokens across 41 markets`
   - decisions either use real forecast data or fail closed with `forecast-unavailable`

## Important Guardrails

- Do not use deterministic or fake forecast fallback for strategy validation.
- If Open-Meteo is unavailable, do not trade from guessed data.
- If a new forecast provider is added, record the provider in decision logs so results are not mixed with Open-Meteo results.
- Keep Open-Meteo calls cached and counted. Add request/429 counters before relying on a longer run.
- Keep 41-city station registry as the trading universe unless the user explicitly changes it.
