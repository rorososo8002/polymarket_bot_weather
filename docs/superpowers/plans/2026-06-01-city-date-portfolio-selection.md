# City-Date Portfolio Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add conservative event-level paper portfolio selection without multiplying correlated city-date risk.

**Architecture:** Add a focused `portfolio.py` policy module. The runner evaluates event candidates, delegates selection to that module, opens only selected legs, and writes reconstructible event-level JSONL decisions. `PaperBroker` remains the final risk backstop.

**Tech Stack:** Python dataclasses, JSONL runtime logs, existing pytest suite.

---

### Task 1: Risk Budget Policy

**Files:**
- Create: `src/weather_bot/portfolio.py`
- Modify: `src/weather_bot/config.py`
- Test: `tests/test_portfolio.py`

- [x] Write failing tests for the `$1,000` transition, executable liquidation basis, and fail-closed missing books.
- [x] Run `& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q tests/test_portfolio.py`.
- [x] Implement the minimum reference-bankroll and adaptive-cap policy.
- [x] Re-run the focused tests.

### Task 2: Event Candidate Selection

**Files:**
- Modify: `src/weather_bot/portfolio.py`
- Modify: `src/weather_bot/models.py`
- Test: `tests/test_portfolio.py`

- [x] Write failing tests for one-leg selection, complementary two-leg selection, correlated blocking, contradiction blocking, and cap sharing.
- [x] Run the focused portfolio tests and confirm expected failures.
- [x] Implement deterministic event-level selection with a maximum of two legs.
- [x] Re-run the focused tests.

### Task 3: Runner And Broker Integration

**Files:**
- Modify: `src/weather_bot/live_paper_runner.py`
- Modify: `src/weather_bot/paper.py`
- Test: `tests/test_portfolio.py`
- Test: `tests/test_realtime_runner.py`

- [x] Write failing integration tests showing event-level opening and event-log reconstruction.
- [x] Run focused tests and confirm expected failures.
- [x] Route cycle and realtime updates through event selection and keep broker caps as final backstops.
- [x] Re-run focused tests.

### Task 4: Dashboard And Configuration Explanation

**Files:**
- Modify: `src/weather_bot/dashboard.py`
- Modify: `.env.example`
- Modify: `deploy/systemd/live-paper.env.example`
- Modify: `docs/dashboard-build-spec.md`
- Test: `tests/test_dashboard.py`
- Test: `tests/test_config.py`
- Test: `tests/test_deployment_files.py`

- [x] Write failing tests for dashboard event-level explanation and new configuration defaults.
- [x] Run focused tests and confirm expected failures.
- [x] Add bounded JSONL dashboard reading and operator-facing explanation.
- [x] Re-run focused tests.

### Task 5: Production Handoff And Verification

**Files:**
- Modify: `docs/production-implementation-plan.md`
- Modify: `docs/production-progress.md`
- Modify: `docs/production-decisions.md`
- Modify: `docs/codex/strategy-research.md`

- [x] Document the Phase 4 rule and Phase 5 handoff.
- [x] Run focused tests.
- [x] Run `& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q`.
- [x] Run the compound learning check and record a durable lesson when needed.
