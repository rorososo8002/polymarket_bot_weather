---
title: Entry Bankroll Zero Must Skip Before Return Estimate
date: 2026-06-03
category: logic-errors
module: weather_bot.live_paper_runner, weather_bot.portfolio, weather_bot.edge
problem_type: logic_error
component: service_object
symptoms:
  - "`evaluate_market()` could pass zero shares into `estimate_executable_net_return()`."
  - "`run_cycle()` could catch `shares must be positive` instead of logging an operator-readable SKIP."
  - "A calculated order below `MIN_ORDER_USD` could become a SKIP without saying the minimum order blocked it."
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags: [paper-trading, entry-bankroll, fail-closed, minimum-order, expected-return]
---

# Entry Bankroll Zero Must Skip Before Return Estimate

## 1. What The Problem Was

`available_entry_bankroll()` correctly returns `entry_bankroll=0` when an open
paper position cannot be safely valued from executable order-book depth. That
zero means the bot does not trust the account basis for new entries.

The live evaluator still sent that zero bankroll through entry sizing. The
calculated paper order size became zero shares, and the expected-return helper
raised `shares must be positive`.

## 2. Why It Was A Problem

`entry_bankroll` is the conservative account amount the bot is allowed to use
for new paper entries. It is based on cash plus open-position cost basis, but it
is capped by the executable liquidation value of held positions.

If an existing position cannot be priced, the bot does not know whether the
account can safely afford another entry. Continuing into expected-return math
turns an accounting safety signal into a runtime exception. Worse, the operator
sees an error instead of the real trading decision: no new entry is allowed
because the held position could not be valued safely.

## 3. How It Was Fixed

`evaluate_market()` now returns an immediate `SKIP` when the entry bankroll is
not positive. The reason includes the operator-facing phrase:

```text
기존 포지션을 안전하게 평가할 수 없어 신규 진입 차단
```

`run_cycle()` and realtime updates pass the `EntryBankrollSnapshot.reason` into
`evaluate_market()`, so the skip can include the underlying cause, such as a
missing held token price or stale order-book stream.

`_side_result()` also stops before expected-return estimation when the
calculated order is below `MIN_ORDER_USD`. That prevents below-minimum entries
from producing misleading return diagnostics or non-zero SKIP sizes.

## 4. What To Check Next Time To Prevent The Same Mistake

- Add a focused test before changing entry math whenever a guard affects order
  size, share count, or expected return.
- Test `entry_bankroll <= 0` directly at `evaluate_market()`, not only through
  the portfolio optimizer.
- Test one full `run_cycle()` path with an unpriceable held position, because
  the cycle-level exception handler can hide evaluator errors.
- Assert SKIP rows have `size_usd=0`, `size_shares=0`, and an operator-readable
  reason.
- Check below-minimum orders before calling helpers that require positive
  shares.

## 5. What This Project Must Be Especially Careful About

This weather bot is paper-only, but its paper results are used to judge whether
the strategy is safe and useful. Fail-closed signals must stay visible as SKIPs,
not become exceptions and not become hidden zero-share calculations.

Any path that converts bankroll into shares should ask two questions before
expected-value math:

```text
Is the bankroll trusted and positive?
Is the calculated order at least MIN_ORDER_USD?
```

If either answer is no, the bot should skip the new entry and explain why.

## Related Issues

- [Paper fees must flow through accounting](./paper-fees-must-flow-through-accounting.md)
- [Best-bid-ask messages are not executable order-book depth](./best-bid-ask-indicative-not-depth.md)
- [Atomic Paper State Writes And Fail-Closed Loads](./atomic-paper-state-write-fail-closed-load.md)
