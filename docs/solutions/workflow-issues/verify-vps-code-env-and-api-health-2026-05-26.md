---
title: Verify VPS code, environment, and API health before trusting the dashboard
date: 2026-05-26
category: workflow-issues
module: deployment
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "A dashboard is reachable but shows no entries or only SKIP decisions"
  - "Local production rules changed and a VPS service may still run older code"
tags: [vps, dashboard, deployment-drift, api-rate-limit, paper-trading]
---

# Verify VPS code, environment, and API health before trusting the dashboard

## Context

The dashboard was reachable and the systemd service was active, but it showed no entries. The service was running an older deployment: the VPS env still had `MAX_MARKETS=40`, old scan cadence settings, and a code version without the 41-city station module. Separately, Open-Meteo returned `Daily API request limit exceeded`, so the bot had no valid ensemble forecast data for strategy evaluation.

## Guidance

When the dashboard looks empty, check three layers before concluding the bot is broken or healthy:

```bash
systemctl status polymarket-weather-bot --no-pager
cat /opt/polymarket-weather-bot/data/paper_runner_status.json
grep -E 'ORDERBOOK|FORECAST|MAX_MARKETS' /etc/polymarket-weather-bot/live-paper.env
cd /opt/polymarket-weather-bot
.venv/bin/python - <<'PY'
from weather_bot.config import Settings, load_settings
from weather_bot.stations import STATION_MAP, SUPPORTED_CITY_COUNT
s = load_settings()
print(len(STATION_MAP), SUPPORTED_CITY_COUNT, Settings.max_markets, s.max_markets, s.orderbook_stream_enabled)
PY
tail -n 20 /opt/polymarket-weather-bot/data/paper_decisions.csv
```

Dashboard health means the HTTP UI is alive. Bot health means runner status, code defaults, env values, decision files, and external API status all match expectations.

## Why This Matters

An active service can still be running old code or safely skipping all entries due to an upstream API limit. For this bot, missing ensemble forecast data means the evaluation is not valid strategy data.

## When to Apply

- After changing the station registry, scan count, forecast cadence, or WebSocket path
- After deploying to VPS
- Whenever scanner counts rise but entries remain at zero
- Whenever recent decisions mention Open-Meteo rate limits

## Examples

Healthy latest deployment indicators:

```text
station_count=41
loaded_max=41
ORDERBOOK_STREAM_ENABLED=true
FORECAST_CACHE_TTL_SECONDS=7200
FORECAST_RATE_LIMIT_STATE_PATH=/opt/polymarket-weather-bot/data/forecast_rate_limit_state.json
phase=streaming
message=websocket streaming 82 tokens across 41 markets
```

External API blocked indicator:

```text
Open-Meteo rate limited: Daily API request limit exceeded. Please try again tomorrow.
source=forecast-unavailable
```

## Related

- [Production status](../../production-progress.md)
- [Install runtime dependencies before starting the paper service](./install-runtime-dependencies-before-service-start-2026-05-26.md)
