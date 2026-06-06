---
title: Numeric Settings Must Fail Closed
date: 2026-06-03
last_updated: 2026-06-06
category: docs/solutions/logic-errors
module: weather_bot.config
problem_type: logic_error
component: tooling
symptoms:
  - "MIN_ORDER_USD could be negative without startup rejection."
  - "WEATHER_TAKER_FEE_RATE could be negative or above 1 without startup rejection."
  - "MAX_TOTAL_EXPOSURE_FRACTION could be above 1 without startup rejection."
  - "DASHBOARD_PORT could be 0 or above 65535 without startup rejection."
  - "Shadow research integer limits could be negative without startup rejection."
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags: [config, settings, validation, fail-closed, paper-trading, risk, dashboard, shadow]
---

# Numeric Settings Must Fail Closed

## Problem

The paper bot could start with unsafe numeric configuration values. Examples
include a negative minimum order, a negative or above-1 fee rate, an exposure
fraction above 1, zero bankroll, zero forecast cache TTL, zero stream-cycle
interval, zero WebSocket stale window, an invalid dashboard port, or negative
shadow research collection limits.

## Why It Was A Problem

Paper trading is the experiment used to judge whether the strategy may be
profitable. If the experiment starts with impossible numbers, the result no
longer measures the strategy.

Think of `paper_state.json` as the paper account book and `Settings` as the
starting rule sheet. If the rule sheet says a minimum order can be `-1`, or the
total exposure cap can be `2.0`, the account book can record performance under
rules that cannot exist in real execution.

## How It Was Fixed

`Settings.__post_init__` now validates the important numeric ranges as soon as
the settings object is created. This catches both direct test construction and
environment-loaded settings from `load_settings()`.

The validation rules are:

- Money and runtime windows that must represent a real positive budget or
  interval must be greater than 0.
- Risk fractions must stay between 0 and 1.
- `WEATHER_TAKER_FEE_RATE` must stay between 0 and 1.
- Non-negative knobs such as `SETTLEMENT_RUNNER_MIN_EV_MARGIN_USD` and
  `SHADOW_MIN_TRADE_USDC` must be at least 0.
- `DASHBOARD_PORT` must be an integer from 1 to 65535.
- Shadow research integer limits must match their meaning:
  `SHADOW_MAX_MARKETS`, `SHADOW_MAX_TRADES_PER_MARKET`, and
  `SHADOW_COMPARE_WINDOW_SECONDS` must be positive integers, while
  `SHADOW_MAX_ROWS` may be 0 to intentionally keep no shadow rows.
- Non-numeric env values now raise `ValueError` with the setting name.

Focused tests cover direct `Settings(...)` construction and env-loaded
`load_settings()` failures for:

- `MIN_ORDER_USD=-1`
- `WEATHER_TAKER_FEE_RATE=-0.01`
- `WEATHER_TAKER_FEE_RATE=1.5`
- `MAX_TOTAL_EXPOSURE_FRACTION=2.0`
- zero bankroll, refresh, TTL, and stale-window values
- `DASHBOARD_PORT=0` and `DASHBOARD_PORT=70000`
- negative shadow research integer limits, while preserving
  `SHADOW_MAX_ROWS=0`

## What To Check Next Time

- When adding a numeric setting, decide which bucket it belongs to:
  positive, non-negative, ratio from 0 to 1, rate from 0 to 1, or positive
  integer. If the setting is a server port, use an explicit 1-to-65535 port
  range. If 0 has an intentional meaning, document it and validate only against
  negative values.
- Add the new field to the matching validation tuple in `weather_bot.config`.
- Add a focused config test that proves a bad env value raises `ValueError`
  with the setting name in the message.
- Avoid using impossible config values in unrelated tests. If a test only needs
  to block trading, prefer a valid upper-bound value like `1.0` instead of
  `999.0`.

## What This Project Must Be Especially Careful About

This project uses paper trading to evaluate money and risk decisions. Settings
such as `MIN_ORDER_USD`, `MAX_TOTAL_EXPOSURE_FRACTION`, and
`WEATHER_TAKER_FEE_RATE` are not cosmetic defaults. `DASHBOARD_PORT` controls
whether the operator dashboard can start correctly, and shadow research limits
control which public samples are studied. They define the measuring instrument.
If the measuring instrument is wrong, the reported profit or loss cannot be
trusted.

## Related

- `docs/solutions/logic-errors/boolean-env-values-must-be-explicit.md`
- `docs/solutions/logic-errors/paper-fees-must-flow-through-accounting.md`
- `docs/solutions/logic-errors/atomic-paper-state-write-fail-closed-load.md`
