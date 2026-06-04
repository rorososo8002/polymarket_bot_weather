# Runtime Log Storage Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop VPS disk growth from high-volume paper runtime logs while preserving paper account and performance ledgers.

**Architecture:** Add explicit runtime-log controls to `Settings`, keep normal raw snapshot writes disabled by default, rotate raw diagnostic files in-process at a 100MB default into compressed archives, and record disk-pressure warnings in `paper_runner_status.json`. Keep `paper_state.json`, `paper_trades.csv`, and `paper_decisions.csv` as ledgers, but make new decision and event-portfolio rows compact summaries rather than raw payloads.

**Tech Stack:** Python standard library (`csv`, `gzip`, `json`, `os`, `pathlib`, `shutil`), existing pytest suite, existing `Settings`, `PaperBroker`, `EventPortfolioDecision`, and `runner_status` helpers.

---

### Task 1: Raw Snapshot Save Mode

**Files:**
- Modify: `tests/test_config.py`
- Modify: `tests/test_hardening.py`
- Modify: `src/weather_bot/config.py`
- Modify: `src/weather_bot/paper.py`
- Modify: `.env.example`
- Modify: `deploy/systemd/live-paper.env.example`

- [ ] Write failing tests that default `Settings.raw_snapshots_mode` is `error`, `PaperBroker.log_raw_snapshot("decision", ...)` writes no row by default, `log_raw_snapshot("error", ...)` writes one row, and `RAW_SNAPSHOTS_MODE=debug` writes normal decision rows.
- [ ] Run focused tests and confirm they fail because the settings field and mode gate do not exist yet.
- [ ] Add `raw_snapshots_mode` to `Settings`, load it from `RAW_SNAPSHOTS_MODE`, validate it against `off`, `error`, and `debug`, and gate `PaperBroker.log_raw_snapshot()`.
- [ ] Run the focused config and hardening tests until green.

### Task 2: Raw Snapshot Rotation And Retention

**Files:**
- Modify: `tests/test_hardening.py`
- Modify: `src/weather_bot/config.py`
- Modify: `src/weather_bot/paper.py`
- Modify: `deploy/logrotate/polymarket-weather-bot-runtime`
- Modify: `tests/test_deployment_files.py`

- [ ] Write failing tests using tiny byte and retention settings: after a raw snapshot write, an oversized active file is compressed into `archive/*.jsonl.gz`; old raw archives older than the configured retention days are removed.
- [ ] Run focused tests and confirm rotation helpers are missing.
- [ ] Add `raw_snapshots_max_bytes=104857600`, `raw_snapshots_retention_days=7`, in-process gzip archive rotation, and archive retention cleanup.
- [ ] Update the VPS logrotate fallback from `size 1G` to `size 100M` and add `maxage 7`.
- [ ] Run the focused hardening and deployment tests until green.

### Task 3: Compact Decision And Portfolio Rows

**Files:**
- Modify: `tests/test_hardening.py`
- Modify: `tests/test_portfolio.py`
- Modify: `tests/test_dashboard.py`
- Modify: `src/weather_bot/paper.py`
- Modify: `src/weather_bot/portfolio.py`
- Modify: `src/weather_bot/dashboard.py`

- [ ] Write failing tests that `paper_decisions.csv` truncates verbose `question`, `reason`, and `note` text to bounded summaries, and `paper_event_portfolios.jsonl` records selected legs plus rejection counts/samples instead of full verbose candidate details.
- [ ] Run focused tests and confirm current rows are too large or still use full rejection lists.
- [ ] Add compact text helpers for decision CSV text fields.
- [ ] Change `EventPortfolioDecision.to_log_payload()` to emit summary fields: counts, selected legs, bounded rejected samples, reason counts, and worst scenario PnL.
- [ ] Update dashboard rendering to read `rejected_legs_sample`, `rejected_reason_counts`, and `worst_scenario_pnl_usd` while remaining compatible with legacy rows.
- [ ] Run focused hardening, portfolio, and dashboard tests until green.

### Task 4: Disk Pressure Auto-Suspend

**Files:**
- Modify: `tests/test_hardening.py`
- Modify: `tests/test_runner_status.py`
- Modify: `src/weather_bot/config.py`
- Modify: `src/weather_bot/paper.py`
- Modify: `src/weather_bot/runner_status.py`
- Modify: `.env.example`
- Modify: `deploy/systemd/live-paper.env.example`

- [ ] Write failing tests that a monkeypatched dangerous `shutil.disk_usage()` prevents raw snapshot writes and leaves a `raw_snapshot_storage` warning in `paper_runner_status.json`.
- [ ] Run focused tests and confirm the warning helper and disk-pressure guard do not exist.
- [ ] Add disk-pressure settings with safe defaults, update runner status without clobbering the current phase, and suspend raw snapshot writes for the current broker instance when the active disk is dangerous.
- [ ] Run the focused hardening, runner-status, and config tests until green.

### Task 5: Handoff Docs And Full Verification

**Files:**
- Modify: `docs/production-progress.md`
- Modify: `docs/production-implementation-plan.md`
- Modify: `docs/production-decisions.md`
- Modify: `docs/codex/runtime-data.md`
- Reuse or update: `docs/solutions/workflow-issues/rotate-raw-snapshots-without-truncating-ledgers.md`

- [ ] Update handoff docs compactly with the new runtime-log contract.
- [ ] Run full `& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q`.
- [ ] Run a compound learning check. Record a durable lesson only if this work produced a new prevention rule beyond the existing raw-snapshot runtime-data lesson.
