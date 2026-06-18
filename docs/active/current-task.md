# Current Task

Status: active

## Objective

Register the eight verified Polymarket temperature settlement stations missing
from the local registry and deploy the expanded paper-execution universe.

## Current Scope

- Add Austin/KAUS, Denver/KBKF, Houston/KHOU, Kuala Lumpur/WMKK,
  Lucknow/VILK, Mexico City/MMMX, San Francisco/KSFO, and Sao Paulo/SBGR.
- Target 49 registered cities and 48 trading-ready cities; Karachi remains
  excluded.
- Raise the Open-Meteo answer-cache TTL from 3 hours to 4 hours so
  48 x 6 x 31 = 8,928 daily units remains below the 10,000-unit limit.
- Keep paper-only execution and same-station fail-closed behavior.

## Next Action

Write and run focused failing tests for registry metadata, AWC bulk coverage,
dashboard counts, and the four-hour forecast budget.

## New Chat Prompt

Continue `docs/superpowers/plans/2026-06-19-expand-temperature-stations.md`
from the focused failing-test step.
