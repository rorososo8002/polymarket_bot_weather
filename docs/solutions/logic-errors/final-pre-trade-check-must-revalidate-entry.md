---
title: Final pre-trade checks must revalidate paper entries
date: 2026-06-14
last_updated: 2026-06-20
category: logic-errors
module: weather_bot.live_paper_runner, weather_bot.config, weather_bot.paper
problem_type: logic_error
component: service_object
symptoms:
  - "`DECISION YES` or `DECISION NO` could be selected before the final paper-open point."
  - "The order book could change after candidate selection but before `PaperBroker.open_position()`."
  - "Wide spreads needed a named `SKIP_WIDE_SPREAD` reason for audit and aggregation."
  - "A provisional cheap price could be tagged as abnormal before final CLOB tradability and executable depth were rechecked."
  - "Insufficient ask depth used prose or `SKIP_NO_DEPTH` instead of the required `SKIP_NO_EXECUTABLE_DEPTH` aggregation key."
root_cause: missing_workflow_step
resolution_type: code_fix
severity: high
tags: [paper-trading, pre-trade-check, spread-guard, orderbook, price-anomaly, fail-closed, skip-reasons]
---

# Final pre-trade checks must revalidate paper entries

## Problem

`DECISION YES` and `DECISION NO` are strategy judgments, not guaranteed paper
opens. A candidate can pass evaluation, enter the city-date portfolio selector,
and then reach the broker after the executable order book has changed.

If the bot writes a paper `OPEN` from the earlier book, paper validation can
record a fill that would no longer be executable. That creates fake confidence
without adding any live-trading behavior.

## Symptoms

- Entry evaluation already checked executable ask VWAP and expected net return,
  but `_open_position_if_needed()` did not fetch the fresh book again before
  calling `PaperBroker.open_position()`.
- The spread limit was hardcoded as `0.20`, so experiments could not clearly
  document or tune the guardrail.
- Wide-spread failures used prose-only text, which made repeated failures hard
  to count from ledgers.

## What Didn't Work

- Relying only on `evaluate_market()` was not enough. It answers whether a
  candidate looked valid at evaluation time, not whether it is still valid at
  accounting time.
- Relying only on `PaperBroker.open_position()` was not enough. The broker owns
  account safety such as cash, exposure, and same-market conflicts; it should
  not be the only place where market microstructure is checked.
- Keeping the spread guard as an unnamed phrase made the ledger less useful.
  Strategy diagnosis needs stable reason codes such as `SKIP_WIDE_SPREAD`.
- Tagging an apparent price anomaly during the first evaluation was not enough.
  A stale or shallow best ask can look cheap without supporting the intended
  paper order.

## Solution

Add a named final pre-trade check before opening a paper position:

```python
final_result = _final_pre_trade_entry_result(
    market,
    signal,
    result,
    token_id,
    client,
    broker.settings,
    market_type,
)
```

That check fetches the fresh executable book, reruns the liquidity and spread
guards, recalculates executable ask VWAP, recomputes after-fee edge and expected
net return, and only then lets `PaperBroker.open_position()` record the paper
entry.

The spread guard is now configurable:

```text
MAX_ENTRY_SPREAD_ABS=0.20
MAX_ENTRY_SPREAD_PCT=1.00
```

`MAX_ENTRY_SPREAD_ABS` is the raw ask-minus-bid price gap. `MAX_ENTRY_SPREAD_PCT`
is that gap divided by the executable ask price. A failure logs a stable
`SKIP_WIDE_SPREAD` action instead of creating a paper position.

Executable-depth failures use `SKIP_NO_EXECUTABLE_DEPTH`. If the final order
book cannot be fetched at all, use `SKIP_TRADABILITY_UNKNOWN` because the bot
does not know whether depth exists. This keeps “known empty or insufficient
depth” separate from “CLOB verification failed.”

Price-anomaly metadata follows the same boundary. The first evaluation may use
the abnormal-price fraction to size a candidate, but `price_anomaly=true` and
`signal_family=abnormal_official_station_mispricing` are only finalized after
the CLOB tradability lookup, fresh ask-side VWAP, spread, fee-aware edge, and
expected-net-return checks all pass. Any final SKIP clears the anomaly flag.

## Why This Works

The fix separates two different moments:

- evaluation time: decide whether the model and current book make a candidate
  worth considering
- accounting time: decide whether the selected candidate is still executable
  enough to write into the paper account book

That distinction matters because the portfolio selector is allowed to choose a
candidate, but the paper ledger should only record fills that still pass the
current executable-book checks.

## Prevention

- Treat every `DECISION YES` and `DECISION NO` as provisional until the final
  pre-trade check passes.
- Keep spread failures named, configurable, and test-covered.
- Assert exact skip-reason keys in tests as well as human-readable explanation
  text; reporting depends on those stable keys.
- When adding future entry blockers, decide whether they belong in model
  evaluation, portfolio selection, broker account safety, or the final
  pre-trade gate.
- Treat abnormal-price classification as execution metadata, not as a raw
  signal property. Cheap-looking reference prices are not opportunities until
  the final intended size is executable.
- Never use this paper-only gate as a reason to add wallet, signing, live
  orders, redemption, copy trading, or `LiveBroker`.

## Related Issues

- `docs/solutions/logic-errors/best-bid-ask-indicative-not-depth.md`
- `docs/solutions/logic-errors/vwap-slippage-edge-contract-2026-05-25.md`
