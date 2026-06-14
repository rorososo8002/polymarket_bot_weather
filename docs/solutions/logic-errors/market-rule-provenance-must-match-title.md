---
title: Market rule provenance must match the market title
date: 2026-06-14
last_updated: 2026-06-14
category: logic-errors
module: weather_bot.polymarket_client, weather_bot.market_rules, weather_bot.event_dates, weather_bot.probability, weather_bot.live_paper_runner
problem_type: logic_error
component: service_object
symptoms:
  - "A temperature market title could parse cleanly while Gamma description or resolution text exposed a conflicting unit or station."
  - "Forecast probability could be requested for a market whose actual settlement rule did not match the parsed title."
  - "Paper entries could gain fake confidence from title-only parsing."
  - "A date hint such as June 15 did not carry a normalized station-local UTC start/end window."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [polymarket, gamma, rule-provenance, timezone, fail-closed, strategy-validation]
---

# Market Rule Provenance Must Match The Market Title

## Problem

Polymarket weather titles are necessary, but they are not complete settlement
evidence. A title can say `29C` while the description or resolution rules expose
Fahrenheit, a different station, a different city, or a different bucket shape.

For this paper bot, that would contaminate strategy validation. The bot is not
trying to prove that a title parser can trade. It is trying to prove that paper
results match executable markets with real settlement rules.

## Symptoms

- Gamma market rows already carried raw fields such as description and
  resolution text, but the normalized market contract did not expose them.
- `pre_forecast_tradeability_gate()` blocked non-temperature, unsupported-city,
  and undated markets, but it did not yet block title/rule conflicts.
- A market could reach forecast fetching even when available rule text exposed
  a different unit or settlement station.

## What Didn't Work

- Keeping only `RawMarket.raw` was not enough. It preserved the original payload,
  but every later caller would have to remember how to search it.
- Relying only on `parse_weather_question(market.question)` was not enough.
  That parses the title, not the settlement evidence.
- Rejecting all markets without rule text would be too aggressive because some
  legacy or sparse Gamma rows still need to pass when no conflicting evidence is
  exposed.

## Solution

Add a normalized provenance object to `RawMarket` and build it during Gamma
discovery:

```python
@dataclass(frozen=True)
class RawMarket:
    ...
    raw: dict[str, Any] | None = None
    rule_provenance: MarketRuleProvenance | None = None
```

The provenance builder extracts compact audit fields from Gamma question,
description, resolution rules, source URL, event slug, parsed condition, date
hint, station, unit, station timezone, and the station-local UTC event window.
Then the pre-forecast gate checks whether exposed rule text conflicts with the
title:

```python
if rule_mismatch := market_rule_mismatch_reason(market):
    return skip(
        "rule-mismatch",
        f"SKIP_RULE_MISMATCH: market title and rule text disagree before forecast. {rule_mismatch}",
        f"SKIP_RULE_MISMATCH: {rule_mismatch} [{market_type}]",
    )
```

Focused tests now cover:

- Gamma provenance normalization for a valid Seoul Celsius market.
- UTC start/end event-window normalization for New York, Seoul, and Wellington.
- Forecast and nowcast sharing the same station-local target date.
- `SKIP_RULE_MISMATCH` before forecast when rule text exposes Fahrenheit
  against a Celsius title.
- `SKIP_RULE_MISMATCH` before forecast when rule text exposes a different
  station ID.
- Valid matching rule provenance still passing.

## Why This Works

`RawMarket.rule_provenance` is a compact "settlement evidence card." It keeps
the original Gamma payload available, but it also gives downstream code a
stable contract for the facts that matter.

The pre-forecast gate is the right place for the mismatch check because it sits
before Open-Meteo calls, order-book evaluation, and paper trade logging. If the
market rule is conflicted there, the safest and cheapest action is to skip.

## Prevention

- New market-discovery work should preserve normalized rule provenance, not only
  raw Gamma JSON.
- Preserve `event_date_local`, `event_timezone`, `event_start_utc`, and
  `event_end_utc` together. The local date is the market's exam date; the UTC
  window is how code compares it without server-timezone drift.
- When title and exposed rule text disagree on city, high/low direction, unit,
  bucket value, date hint, or explicit station, fail closed before forecast.
- Do not treat missing rule text as proof of a mismatch. Treat conflicting rule
  text as proof that the market is unsafe to evaluate.

## Related Issues

- `docs/solutions/logic-errors/temperature-range-buckets-must-preserve-endpoints.md`
- `docs/solutions/logic-errors/explicit-yes-no-token-mapping.md`
