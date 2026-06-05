---
title: Empty decision CSV must still receive its header
date: 2026-06-05
category: logic-errors
module: weather_bot.paper
problem_type: logic_error
component: service_object
symptoms:
  - "An existing 0-byte `paper_decisions.csv` could receive a data row before the CSV header."
  - "Dashboard or report readers could mistake the first decision row for column names."
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [paper-trading, csv-ledger, decision-log, headers, testing]
---

# Empty decision CSV must still receive its header

## 1. What The Problem Was

`paper_decisions.csv` is the strategy judgment log. It records why the bot
entered, skipped, or rejected a market, along with the forecast probability,
executable price, edge, sizing, and reason text.

The decision logger checked only whether the file existed before deciding
whether to write the CSV header. If an operator, deployment, or cleanup process
created an empty `paper_decisions.csv`, the next decision row was appended
without column names.

## 2. Why It Was A Problem

A CSV header is the column-name row. Readers such as dashboards and reports use
that first row to understand what each later cell means.

If the first row is actual decision data instead of:

```text
ts,market_id,slug,question,market_type,side,...
```

then a CSV reader can treat the timestamp, market id, question, and side from
that first decision as the column names. After that, later rows may be parsed
under nonsense keys, making the strategy evidence hard to trust.

For this project, that matters because paper trading is the experiment. The
judgment log is not disposable cache; it is evidence used to debug SKIPs, audit
entries, and compare the strategy over time.

## 3. How It Was Fixed

`PaperBroker.log_decision()` now treats an existing 0-byte decision CSV the same
way it treats a missing file:

```python
exists = self.decisions_csv_path.exists() and self.decisions_csv_path.stat().st_size > 0
```

That means:

- Missing file: write the header, then the decision row.
- Existing empty file: write the header, then the decision row.
- Existing non-empty file: append only the decision row, without duplicating the
  header.

The decision column list was also named as `DECISION_CSV_FIELDNAMES` so the
header contract lives in one obvious place in `paper.py`.

## 4. What To Check Next Time To Prevent The Same Mistake

- When appending to a ledger-style CSV, check both file existence and file size
  before deciding the header already exists.
- Add a test that creates a 0-byte CSV before calling the append function.
- Assert that the first parsed row is the header and the second parsed row is
  the data row.
- Compare similar ledger writers. In this project, `paper_trades.csv` already
  used the safer `exists() and stat().st_size > 0` pattern.

## 5. What This Project Must Be Especially Careful About

Do not treat runtime CSV files as ordinary scratch files. `paper_decisions.csv`
is the strategy judgment log, `paper_trades.csv` is the execution ledger, and
`paper_state.json` is the paper account book.

For these files, small format mistakes can corrupt later interpretation even if
paper trading itself keeps running. Tests should cover fresh files, empty files,
and existing non-empty files whenever a ledger append path changes.

Related docs:

- [Brier score must use OPEN entry probability](brier-score-must-use-open-entry-probability.md)
- [Avoid full decision-log scans in runtime readers](../performance-issues/dashboard-large-decision-log-initial-scan.md)
- [Rotate raw snapshots without truncating paper ledgers](../workflow-issues/rotate-raw-snapshots-without-truncating-ledgers.md)
