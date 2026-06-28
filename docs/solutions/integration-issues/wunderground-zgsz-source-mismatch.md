---
title: Block Shenzhen ZGSZ when Wunderground history resolves to a different source
date: 2026-06-28
category: integration-issues
module: weather_bot.market_rules
problem_type: integration_issue
component: service_object
symptoms:
  - "AWC METAR ZGSZ showed 29C while the Polymarket Shenzhen 28C market still priced as if 28C was viable."
  - "The Wunderground daily history API for ZGSZ returned obs_id 45035 and obs_name Lau Fau Shan instead of Shenzhen Bao'an METAR observations."
  - "Paper entry logic treated the METAR observation as settlement evidence for a market whose rule source was Wunderground history."
root_cause: wrong_api
resolution_type: code_fix
severity: high
tags: [polymarket, wunderground, zgsz, metar, settlement-source, fail-closed]
---

# Block Shenzhen ZGSZ When Wunderground History Resolves To A Different Source

## Problem

The bot saw AWC METAR `ZGSZ` report 29C for local June 28 and treated that as
proof that the Shenzhen 28C high-temperature bucket was irreversibly broken.
Polymarket's rule text, however, resolves from Wunderground's daily history URL,
and the Wunderground historical feed for `ZGSZ` was returning `obs_id` 45035 /
`obs_name` Lau Fau Shan with a lower observed high.

## Symptoms

- The paper dashboard showed Shenzhen 28C NO as an "official observation broke
  the bucket" entry even though Polymarket prices had not collapsed.
- AWC METAR and Wunderground history disagreed for the same apparent station
  code.
- The event rule URL looked station-specific, but the downstream history feed
  mapped to another observation source.

## What Didn't Work

- Trusting the station code alone did not work. `ZGSZ` in the market URL and
  `ZGSZ` in METAR are not sufficient if the settlement feed behind the
  Wunderground page maps to a different observation source.
- Treating a live market price as irrational did not work. In this case the
  market price was a useful warning that the settlement source might not match
  the bot's observation source.

## Solution

Add a known-source-conflict guard in `market_rules.py` so Shenzhen markets whose
rule text points at the Wunderground `cn/shenzhen/ZGSZ` daily-history source
fail closed before station signal calculation or paper entry:

```python
KNOWN_SOURCE_CONFLICTS = {
    "shenzhen": (
        "wunderground.com/history/daily/cn/shenzhen/zgsz",
        "source mismatch: Wunderground ZGSZ historical feed currently maps to Lau Fau Shan/45035, "
        "so AWC METAR ZGSZ cannot be used as settlement evidence",
    ),
}
```

The regression test builds a Shenzhen grouped event with the Wunderground ZGSZ
source and asserts that the pre-station gate returns `SKIP_RULE_MISMATCH` with
the Lau Fau Shan / 45035 explanation.

Shenzhen is also excluded from `TRADING_READY_STATION_MAP` until the
Wunderground historical source maps back to same-station `ZGSZ` observations.

## Why This Works

This bot is validating executable paper PnL against Polymarket settlement
rules, not merely against any plausible weather feed. When the rule source and
the observation feed disagree, the safe result is no new entry. Blocking the
known conflict preserves the paper ledger instead of creating apparently
profitable trades from non-settlement evidence.

## Prevention

- For same-day observation entries, verify that the source used for the signal
  is the same source named by the Polymarket rule text.
- If market prices stay high after an allegedly irreversible station break,
  inspect the settlement source before assuming the market is stale.
- Add city-specific fail-closed guards for verified source conflicts rather than
  silently substituting a cleaner weather API.
- Keep source-conflicted cities out of the trading-ready registry, not just out
  of one entry path.

## Related Issues

- [Require Polymarket rule evidence before station trading](../logic-errors/require-polymarket-rule-evidence-before-station-trading-2026-06-03.md)
