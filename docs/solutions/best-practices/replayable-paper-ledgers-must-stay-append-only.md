---
title: Replayable paper ledgers must stay append-only
date: 2026-06-14
category: best-practices
module: weather_bot.paper
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - "Adding replay evidence fields to paper decision or trade ledgers"
  - "Changing CSV headers for paper-trading runtime evidence"
  - "Writing account-event raw snapshots from an accounting transaction path"
tags: [paper-trading, csv-ledger, replay-evidence, backward-compatibility, raw-snapshots]
---

# Replayable paper ledgers must stay append-only

## Context

The paper bot now records compact replay evidence on new decision and trade
rows: token, city, station-local event date, condition type, station evidence,
signal source, entry VWAP, expected net return, reason code, and model/config
version.

That makes later reports more honest because an analyst can answer "why did
the bot buy this?" without reconstructing the judgment from unrelated logs.
But these files are not ordinary export files. `paper_decisions.csv` is the
strategy judgment ledger, and `paper_trades.csv` is the execution receipt
ledger. Old rows are still evidence from the experiment window in which they
were written.

## Guidance

When adding replay fields to paper ledgers:

- Write the current full header only for new or empty CSV files.
- Append to legacy non-empty CSV files using their existing header.
- Ignore extra values when a legacy header does not expose the new columns.
- Keep readers backward-compatible and prefer structured new columns only when
  they are present.
- Add focused tests for both fresh-header writes and legacy-header appends.

Account-event raw snapshots follow the same safety rule. `OPEN`, `ADD`,
`CLOSE`, `PARTIAL_CLOSE`, and `SETTLED` should write compact diagnostic
snapshots by default, but snapshot write failure must not turn a completed
accounting row into a failed transaction. The account book and execution ledger
remain the source of truth; raw snapshots are supporting evidence.

## Why This Matters

Paper trading is the measurement layer. If a schema migration rewrites old CSV
headers or backfills old rows with guessed evidence, the bot can make an old
decision look more precise than it actually was. That is fake confidence.

The safer pattern is append-only evolution:

```text
old rows keep old evidence
new rows get richer evidence
readers understand both
```

That preserves the historical meaning of the experiment while still improving
future analysis.

## When To Apply

Apply this whenever a paper runtime file is both:

- used as evidence for strategy validation, and
- being extended with new fields or diagnostic companions.

This especially applies to `PaperBroker.log_decision()`,
`PaperBroker.log_trade()`, `analyze_paper.py`, daily reports, dashboard readers,
and tests that create legacy CSV fixtures.

## Examples

For decision rows, `log_decision()` should use the current
`DECISION_CSV_FIELDNAMES` for new files, but preserve an existing legacy header:

```python
fieldnames = _ensure_csv_columns(path, DECISION_CSV_FIELDNAMES)
writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
writer.writerow(new_row_with_replay_fields)
```

For reports, prefer stable new reason codes when they exist, then fall back to
older reason text:

```python
reason_code = (row.get("reason_code") or "").strip()
label = reason_code or _skip_label(row.get("reason", ""))
```

Related docs:

- [Paper State And Trade Ledger Updates Need A Transaction Journal](../logic-errors/paper-state-trade-ledger-transaction-journal.md)
- [Empty Decision CSV Must Still Receive Its Header](../logic-errors/empty-decision-csv-must-write-header.md)
- [Rotate Raw Snapshots Without Truncating Paper Ledgers](../workflow-issues/rotate-raw-snapshots-without-truncating-ledgers.md)
