---
title: Report ledgers must skip missing current columns before legacy fallback
date: 2026-06-11
category: logic-errors
module: scripts.daily_report
problem_type: logic_error
component: reporting
symptoms:
  - "Daily report PnL could read as 0 for legacy trade rows."
  - "A fallback helper returned 0 before checking older realized PnL columns."
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [paper-trading, reporting, ledger, backward-compatibility, pnl]
---

# Report ledgers must skip missing current columns before legacy fallback

## 1. What The Problem Was

`paper_trades.csv` is the execution receipt ledger. New rows use
`cash_delta_or_pnl` as the realized PnL/cash movement column, but older rows may
only have `realized_pnl_usd` or `net_pnl_usd`.

`scripts/daily_report.py` was updated to prefer `cash_delta_or_pnl`, but the
first fallback helper returned `0` when the current column was missing. That
meant it never checked the legacy columns.

## 2. Why It Was A Problem

Reports are evidence. If a legacy `CLOSE`, `PARTIAL_CLOSE`, or `SETTLED` row
has real PnL in an older column, reporting it as `$0.00` makes paper strategy
performance look wrong.

The account book and receipt ledger may still be valid, but the report reader
silently loses old evidence.

## 3. How It Was Fixed

The fallback helper now skips missing or empty fields and only returns after it
successfully parses a present value:

```python
for field in ("cash_delta_or_pnl", "realized_pnl_usd", "net_pnl_usd"):
    raw_value = row.get(field)
    if raw_value in (None, ""):
        continue
    return float(raw_value)
```

Regression coverage now checks both the current `cash_delta_or_pnl` column and
a legacy `realized_pnl_usd`-only trade file.

## 4. What To Check Next Time

- When adding backward-compatible readers, missing current columns must
  continue to older columns instead of returning a neutral value.
- Tests should cover both the newest ledger header and at least one legacy
  header shape.
- Reports may stream full ledgers, but they must preserve the ledger meaning
  across historical column names.

## Related

- [Paper state/trade ledger transaction journal](./paper-state-trade-ledger-transaction-journal.md)
- [Dashboard trades and closed-market settlement](./dashboard-trades-and-closed-market-settlement.md)
