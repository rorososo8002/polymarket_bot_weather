# Whale And External-Signal Shadow Research

Updated: 2026-06-02 Asia/Seoul

## Purpose

Phase 7 studies whether public trader behavior can improve paper returns. This
is research only. It does not connect a wallet, place orders, copy trades, or
change the live paper runner.

`shadow signal` means an outside signal stored beside our bot's paper decisions.
Think of it like a second scoreboard: it lets us ask, "Did a public trader act
before or after our model, did they point YES or NO, and after settlement who was
right?"

## Official Public Sources Checked

Use public official Polymarket APIs first:

- Gamma API events and markets: market/event discovery and resolved market
  metadata.
  https://docs.polymarket.com/market-data/overview
- Data API `/trades`: public market or wallet trade activity.
  https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets
- Data API `/activity`: public user activity with market and event filters.
  https://docs.polymarket.com/api-reference/core/get-user-activity
- Data API `/holders`: public top holders for one or more markets.
  https://docs.polymarket.com/api-reference/core/get-top-holders-for-markets

Do not use private messages, private groups, leaked data, credentials, or any
source that requires secrets. Public posts such as Twitter/X or blogs may be
entered manually in `shadow_public_notes.jsonl`, but the report must label them
as evidence or speculation.

## Files And Commands

Code:

```text
src/weather_bot/shadow_signals.py
tests/test_shadow_signals.py
```

Command:

```powershell
shadow-signal-report --collect
```

Without `--collect`, the command only reads existing local research files and
builds a report. With `--collect`, it fetches a bounded public sample and writes
`shadow_external_signals.jsonl`.

## Bounded Dataset

Default research limits:

```text
POLYMARKET_DATA_BASE=https://data-api.polymarket.com
SHADOW_SIGNALS_JSONL_PATH=shadow_external_signals.jsonl
SHADOW_PUBLIC_NOTES_JSONL_PATH=shadow_public_notes.jsonl
SHADOW_REPORT_PATH=shadow_signal_report.md
SHADOW_MAX_MARKETS=100
SHADOW_MAX_TRADES_PER_MARKET=100
SHADOW_MAX_ROWS=1000
SHADOW_MIN_TRADE_USDC=100.0
SHADOW_COMPARE_WINDOW_SECONDS=86400
```

`SHADOW_MAX_ROWS` is the retention guard. The JSONL file is deduplicated and
keeps the newest rows only. A transaction hash is not treated as a unique row
by itself because one transaction may contain multiple distinct market or
outcome rows. Setting the retention limit to `0` keeps no rows.
`SHADOW_MIN_TRADE_USDC` is the first "whale-like" filter: it does not prove a
trader is smart, it only says the trade was large enough to study. The
collector checks this minimum locally after parsing each returned row instead
of trusting only the remote API query filter.

Each public trade signal stores:

```text
source
evidence_level
wallet
condition_id
market_id
market_slug
event_slug
question
raw_side
outcome
implied_side
price
size
usdc_size
timestamp
observed_at
transaction_hash
later_outcome
```

When a closed Gamma market is available, `later_outcome` can be inferred from
binary `outcomes` and `outcomePrices`; the winning YES/NO side is the outcome
priced near `1.0`.

`implied_side` is the market direction after translating buy/sell:

- BUY YES -> YES
- BUY NO -> NO
- SELL YES -> NO
- SELL NO -> YES

That translation matters because a sale of NO has the same directional meaning
as increasing YES exposure.

## Paper Comparison

The report compares every shadow signal with the closest paper decision for the
same market slug or market id inside `SHADOW_COMPARE_WINDOW_SECONDS`.

It records:

- whether the external signal came before or after the bot decision
- whether the external side matched the bot side
- whether either side was correct after `later_outcome` is known
- whether the bot skipped while public activity existed

This is a research comparison, not an entry rule. The report shows every
resolved external signal for diagnosis, but promotion uses only paired rows
where the bot also made a scoreable `YES` or `NO` entry. A bot `SKIP` row is
useful evidence that public activity existed while our strategy abstained, but
it cannot be counted as a bot loss or used to inflate the public-signal
advantage. A strong-looking signal still needs a later paper-only experiment
before it can affect strategy.

## Public Posts

Manual public notes use JSONL:

```json
{"classification":"evidence","source_url":"https://example.com/post","claim":"Trader posted a filled weather order."}
{"classification":"speculation","source_url":"https://example.com/post2","claim":"The tone may imply a bullish weather view."}
```

Evidence is a directly checkable public claim. Speculation is interpretation.
The report counts them separately so a guessed narrative cannot masquerade as
measured performance.

## Promotion Rule

The current code deliberately defaults to caution:

- fewer than 20 paired resolved public-signal and bot-entry rows: do not promote
- public signals not at least 5 percentage points better than bot entries on
  that same paired sample: do not promote
- if public signals clear that bar, suggest a paper-only A/B experiment, not
  automatic copy trading

If future resolved data clears the bar, the report says:

> The research suggests this strategy may improve returns.
> Would you like to consider a strategy update?

Even then, the next step is paper-only experimentation.

## Current Conclusion

Phase 7 adds the shadow research structure and report generator. It does not yet
prove that whale or external signals improve returns, because the bounded sample
needs resolved outcomes before performance can be judged. Keep this as an
experiment queue until enough settled public signals exist.
