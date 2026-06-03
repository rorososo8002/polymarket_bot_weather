---
title: Require Polymarket rule evidence before station trading
date: 2026-06-03
last_updated: 2026-06-03
category: logic-errors
module: weather_bot.stations
problem_type: logic_error
component: service_object
symptoms:
  - "A city could be registered with station coordinates before the original Polymarket rule wording was stored."
  - "Paper trading could treat a supported city as executable even when rule evidence was missing or conflicted with the registry."
  - "README or handoff docs could describe the 41-city registry as the executable trading universe and accidentally invite Karachi back into paper trading."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [polymarket, weather-stations, rule-evidence, paper-trading, fail-closed]
---

# Require Polymarket Rule Evidence Before Station Trading

## 1. What The Problem Was

The weather bot had a useful 41-city `STATION_MAP`, but some cities still lacked
stored Polymarket rule evidence. A station coordinate can be correct for
forecasting while still being unproven for Polymarket settlement. Karachi also
showed a sharper problem: the found Polymarket source names Masroor Airbase
Station, but its Wunderground source URL uses `OPKC` while the registry used
`OPMR`.

## 2. Why It Was A Problem

Paper trading is supposed to test whether the strategy can make money using the
same information that resolves the market. If the bot forecasts the wrong
station, the paper result may look profitable while measuring a different
question. That contaminates the evidence base and can hide risk.

## 3. How It Was Fixed

The code now separates "registered city" from "trading-ready city":

- `STATION_MAP` keeps all registered station metadata.
- Stored Polymarket rule URLs and station wording are attached to station rows.
- `station_is_trading_ready()` returns true only for rows with verified rule
  evidence.
- `TRADING_READY_STATION_MAP` is the subset used by discovery and probability
  estimation.
- Karachi remains registered but is excluded with `rule_station_id_conflict`
  until the `OPKC` versus `OPMR` conflict is resolved from a primary source.
- README and production handoff docs must describe the same split: `STATION_MAP`
  is the registered 41-city observation-station list, while
  `TRADING_READY_STATION_MAP` is the current 40-city paper-trading execution
  list.

## 4. What To Check Next Time To Prevent The Same Mistake

- Before adding or enabling a city, store the official Polymarket market-rule URL
  and the exact settlement-station wording.
- Check that the rule source URL's station code matches the registry station
  code.
- Add a regression test proving a supported city without rule evidence is not
  mapped for trading.
- Review discovery filters and probability lookup together so one cannot use the
  broad registry while the other uses the verified subset.
- When editing README or production handoff docs, search for old wording such as
  "trade only the 41" or "41-city allowlist" and rewrite it so registration and
  paper execution stay separate.

## 5. What This Project Must Be Especially Careful About

For this bot, the city name is not enough. The market settles on the named
station, so the strategy must fail closed when the rule evidence is missing,
stale, unsupported, or contradictory. A smaller verified trading universe is
better than a larger guessed one.
