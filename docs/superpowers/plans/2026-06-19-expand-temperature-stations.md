# Expand Temperature Settlement Stations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register eight verified Polymarket temperature settlement stations, make them paper-execution eligible, and keep forecast API use below the daily limit.

**Architecture:** Keep `STATION_MAP` as the complete settlement-station registry and let the existing evidence checks derive `TRADING_READY_STATION_MAP`. Add only official rule evidence and AWC-verified station metadata. Increase the forecast answer-cache lifetime from three hours to four hours so the larger execution universe remains inside the Open-Meteo allowance.

**Tech Stack:** Python 3.12, dataclasses, pytest, Open-Meteo forecast cache, Aviation Weather Center METAR, systemd on Oracle VPS.

---

### Task 1: Record The Active Contract

**Files:**
- Modify: `docs/active/current-task.md`
- Modify: `AGENTS.md`
- Modify: `docs/production-decisions.md`
- Modify: `docs/production-implementation-plan.md`
- Modify: `docs/station-registry-audit.md`

- [ ] **Step 1: Mark the task active**

Replace the current-task card with the eight-city registration objective, the 49/48 target counts, the four-hour forecast cache, and the focused-test next action.

- [ ] **Step 2: Update durable operating rules**

Record this exact execution contract:

```text
STATION_MAP: 49 registered cities
TRADING_READY_STATION_MAP: 48 cities
Excluded: Karachi
FORECAST_CACHE_TTL_SECONDS: 14400
Budget: 48 x 6 x 31 = 8928 units/day
AWC real-request floor: 60 seconds
```

- [ ] **Step 3: Expand the station audit table**

Add Austin/KAUS, Denver/KBKF, Houston/KHOU, Kuala Lumpur/WMKK,
Lucknow/VILK, Mexico City/MMMX, San Francisco/KSFO, and Sao Paulo/SBGR
with the exact coordinates, timezones, settlement units, AWC state, and
Polymarket rule evidence from the approved design.

- [ ] **Step 4: Commit the documentation contract**

```powershell
git add AGENTS.md docs/active/current-task.md docs/production-decisions.md docs/production-implementation-plan.md docs/station-registry-audit.md
git commit -m "docs: define 49-city station registry"
```

Expected: one documentation-only commit; no runtime files staged.

### Task 2: Write Failing Registry And Budget Tests

**Files:**
- Modify: `tests/test_station_registry.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_deployment_files.py`
- Modify: `tests/test_dashboard.py`
- Modify: `tests/test_probability_ensemble.py`
- Modify: `tests/test_nowcast_provider.py`

- [ ] **Step 1: Add the expected station metadata**

Add a parameterized registry test using this data:

```python
EXPECTED_NEW_STATIONS = {
    "austin": ("KAUS", 30.1831, -97.6806, "America/Chicago", "fahrenheit", "1F"),
    "denver": ("KBKF", 39.7130, -104.7580, "America/Denver", "fahrenheit", "1F"),
    "houston": ("KHOU", 29.6458, -95.2821, "America/Chicago", "fahrenheit", "1F"),
    "kuala lumpur": ("WMKK", 2.7470, 101.7140, "Asia/Kuala_Lumpur", "celsius", "1C"),
    "lucknow": ("VILK", 26.7610, 80.8890, "Asia/Kolkata", "celsius", "1C"),
    "mexico city": ("MMMX", 19.4360, -99.0720, "America/Mexico_City", "celsius", "1C"),
    "san francisco": ("KSFO", 37.6196, -122.3656, "America/Los_Angeles", "fahrenheit", "1F"),
    "sao paulo": ("SBGR", -23.4320, -46.4690, "America/Sao_Paulo", "celsius", "1C"),
}
```

Assert each city is registered, trading-ready, uses enabled METAR, has
grade A confidence, and carries a Polymarket rule URL and station wording.

- [ ] **Step 2: Change count and source expectations**

Assert 49 registered cities, 48 trading-ready cities, 47 METAR sources,
one HKO source, and no unavailable source in the execution subset.

- [ ] **Step 3: Add the budget assertion**

```python
def test_default_forecast_budget_batch_mode():
    assert Settings.forecast_cache_ttl_seconds == 14400
    batches_per_day = 86400 // Settings.forecast_cache_ttl_seconds
    assert 48 * batches_per_day * 31 == 8928
    assert 48 * batches_per_day * 31 < 10000
```

Change all three deployment example assertions from `10800` to `14400`.

- [ ] **Step 4: Prove AWC bulk coverage**

Instantiate `AviationWeatherMetarNowcastProvider`, call its bulk station-ID
selector, and assert all eight new ICAO IDs are included. Add one accepted
matching-row case and one rejected mismatched-`icaoId` case using an in-memory
AWC JSON payload.

- [ ] **Step 5: Run the focused tests and confirm failure**

```powershell
$env:PYTHONPATH='src'; python -m pytest -q tests/test_station_registry.py tests/test_config.py tests/test_deployment_files.py tests/test_dashboard.py tests/test_probability_ensemble.py tests/test_nowcast_provider.py
```

Expected: failures for missing cities, old 41/40 counts, and old 10,800-second
cache defaults.

### Task 3: Implement The Registry And Forecast Budget

**Files:**
- Modify: `src/weather_bot/stations.py`
- Modify: `src/weather_bot/config.py`
- Modify: `.env.example`
- Modify: `deploy/systemd/live-paper.env.example`
- Modify: `deploy/systemd/dashboard.env.example`
- Modify: `src/weather_bot/dashboard_template.py`
- Modify: `src/weather_bot/dashboard.py`

- [ ] **Step 1: Add the eight station records**

Use `_station(...)` with the approved station ID, name, coordinates, elevation,
timezone, settlement unit, precision, and `last_verified_at="2026-06-19"`.
The four Fahrenheit stations must explicitly set:

```python
temperature_unit="fahrenheit",
reporting_precision="1F",
```

The four Celsius stations use `temperature_unit="celsius"` and
`reporting_precision="1C"`.

- [ ] **Step 2: Add exact rule evidence**

For each city, add `_rule(...)` with its June 19, 2026 Polymarket event URL and
the exact named settlement-station phrase. Do not add a city to a separate
allowlist; the existing evidence predicate must decide readiness.

- [ ] **Step 3: Raise the forecast cache default**

Change the `Settings.forecast_cache_ttl_seconds` default and all three env
examples to `14400`. Keep `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=15`.

- [ ] **Step 4: Remove stale station-count comments**

Change dashboard comments that say 38 METAR stations to describe the dynamic
bulk set, because the displayed count already comes from runtime data.

- [ ] **Step 5: Run the focused tests**

```powershell
$env:PYTHONPATH='src'; python -m pytest -q tests/test_station_registry.py tests/test_config.py tests/test_deployment_files.py tests/test_dashboard.py tests/test_probability_ensemble.py tests/test_nowcast_provider.py
```

Expected: all focused tests pass.

- [ ] **Step 6: Commit implementation**

```powershell
git add src/weather_bot/stations.py src/weather_bot/config.py src/weather_bot/dashboard.py src/weather_bot/dashboard_template.py .env.example deploy/systemd/live-paper.env.example deploy/systemd/dashboard.env.example tests
git commit -m "feat: register eight settlement stations"
```

### Task 4: Verify The Complete Local System

**Files:**
- Modify if needed: only files directly implicated by a failing test

- [ ] **Step 1: Run the complete test suite**

```powershell
$env:PYTHONPATH='src'; python -m pytest -q
```

Expected: all tests pass with no network dependency.

- [ ] **Step 2: Inspect generated station counts**

```powershell
$env:PYTHONPATH='src'; python -c "from weather_bot.stations import STATION_MAP,TRADING_READY_STATION_MAP; print(len(STATION_MAP),len(TRADING_READY_STATION_MAP)); print([STATION_MAP[c].station_id for c in ('austin','denver','houston','kuala lumpur','lucknow','mexico city','san francisco','sao paulo')])"
```

Expected:

```text
49 48
['KAUS', 'KBKF', 'KHOU', 'WMKK', 'VILK', 'MMMX', 'KSFO', 'SBGR']
```

### Task 5: Deploy And Verify Production Paper Services

**Files:**
- Modify: `/etc/polymarket-weather-bot/live-paper.env` on the VPS
- Modify: `/etc/polymarket-weather-bot/dashboard.env` on the VPS if the setting exists
- Preserve: `/opt/polymarket-weather-bot/data/*`

- [ ] **Step 1: Use the documented deploy path**

Read `docs/codex/known-good-commands.md`, transfer the committed source once,
and use a rollback-capable remote script. Do not transfer `.git`,
`.tmp_investigation`, local caches, or runtime ledgers.

- [ ] **Step 2: Update the VPS cache setting**

Set `FORECAST_CACHE_TTL_SECONDS=14400` in the bot environment and the dashboard
environment when present. Do not change API tokens, wallet settings, or
paper-account files.

- [ ] **Step 3: Run remote tests and restart once**

Run the remote full pytest suite, restart `polymarket-weather-bot` and
`polymarket-weather-dashboard`, and verify both systemd units are active.

- [ ] **Step 4: Verify the live behavior**

Check:

```text
dashboard HTML: 200
/api/status without auth: 403
/api/status with query token: 403
/api/status with header token: 200
station_registry_count: 49
trading-ready count: 48
forecast cache TTL: 14400
all eight new station IDs present in the AWC bulk station set
```

- [ ] **Step 5: Close the task card**

Replace `docs/active/current-task.md` with `Status: none`, record any durable
forecast-budget lesson in the matching `docs/solutions/` note, and commit the
documentation closeout without redeploying for doc-only changes.
