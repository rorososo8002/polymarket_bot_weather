# Official Station Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace forecast-oriented dashboard data and UI with official-station strategy evidence.

**Architecture:** Keep the existing Python payload builder and static HTML/CSS/JS dashboard. Parse compact official-lock evidence already stored in decision notes, aggregate station request logs and bounded skip diagnostics, and render those fields without changing trading or accounting behavior.

**Tech Stack:** Python 3.12, pytest, static HTML/CSS/JavaScript, systemd on Oracle VPS

---

### Task 1: Protect The New Payload Contract

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `src/weather_bot/dashboard.py`

- [ ] Add failing tests asserting forecast keys are absent and station health,
  station signals, observations, and recent skips are present.
- [ ] Run `python -m pytest -q tests/test_dashboard.py` and confirm the new tests fail.
- [ ] Add compact note parsing, station-health aggregation, and bounded skip-log
  loading in `dashboard.py`.
- [ ] Run the focused dashboard tests and confirm they pass.

### Task 2: Replace Forecast UI With Station Evidence

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `src/weather_bot/dashboard_template.py`

- [ ] Add failing HTML assertions that forecast labels/functions are absent and
  official-station, settlement-boundary, allocation, and blocker labels exist.
- [ ] Run the focused test and confirm the new assertions fail.
- [ ] Replace forecast health/call panels and badges with station health,
  official-lock signal cards, station observation cards, and skip cards.
- [ ] Run the focused dashboard tests and confirm they pass.

### Task 3: Verify, Commit, And Deploy

**Files:**
- Modify: `docs/active/current-task.md`

- [ ] Run the complete local pytest suite.
- [ ] Reset the active task card to `Status: none`.
- [ ] Review `git diff --stat`, `git diff --name-only`, and focused touched-file diffs.
- [ ] Commit the dashboard change.
- [ ] Deploy one archive to `/opt/polymarket-weather-bot`, run remote pytest once,
  restart bot and dashboard once, and preserve the current paper ledger.
- [ ] Verify service state, dashboard HTML 200, bare API 403, query-token API
  403, and header-authenticated API 200.
- [ ] Capture one desktop and one mobile screenshot and inspect for overflow,
  missing content, and unreadable state labels.
