---
title: Brier Score Must Use OPEN Entry Probability
date: 2026-06-04
category: logic-errors
module: weather_bot.analyze_paper, weather_bot.paper
problem_type: logic_error
component: service_object
symptoms:
  - "`analyze_paper.py` could grade a resolved market from a later `paper_decisions.csv` probability instead of the actual entry probability."
  - "The same `market_id` could have multiple YES/NO decisions before settlement, making Brier score drift after the trade was already opened."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, brier-score, entry-probability, csv-ledger, analysis]
---

# Brier Score Must Use OPEN Entry Probability

## 1. What The Problem Was

Brier score is the paper bot's probability grading sheet. If the bot entered a
market when `p_true` was `0.70`, the resolved result must be graded as a
`0.70` forecast.

`analyze_paper.py` instead used the latest entry decision stored in
`paper_decisions.csv` for each market. If the bot opened at `0.70` and later
updated the same market to `0.90`, the report could grade the settlement as a
`0.90` forecast. That is hindsight drift.

## 2. Why It Was A Problem

`paper_decisions.csv` is the judgment log. It records what the model thought at
each scan or realtime update.

`paper_trades.csv` is the execution ledger. It records what the paper account
actually opened, closed, partially closed, or settled.

Performance scoring should follow the execution ledger when possible. Otherwise
the report can make the strategy look better or worse than the decision the bot
actually acted on.

## 3. How It Was Fixed

`PaperBroker.open_position()` now writes structured entry metadata into the
`OPEN` row:

```text
entry_p_true
entry_side_probability
entry_net_edge
decision_ts
```

`entry_p_true` is the YES probability at entry time. `entry_side_probability`
is the probability that the side actually bought wins. For YES it equals
`entry_p_true`; for NO it equals `1 - entry_p_true`.

`analyze_paper.py` now streams `paper_trades.csv` and remembers the latest
scoreable `OPEN` entry probability by `market_id`. When a later close or
settlement row has `resolved winner=YES` or `resolved winner=NO`, Brier scoring
uses the `OPEN` probability first.

Old trade CSVs still work. If an `OPEN` row has no new entry columns, the
analysis falls back to the previous behavior: latest scoreable decision
probability from `paper_decisions.csv`.

## 4. What To Check Next Time To Prevent The Same Mistake

- Add a test where one `market_id` has an `OPEN` at one probability and a later
  decision at another probability.
- Make the resolved Brier expectation match the `OPEN` probability, not the
  later decision.
- Keep `paper_trades.csv` schema changes backward-compatible so old ledgers can
  still be analyzed.
- For any new performance metric, ask which ledger is the source of truth:
  judgment log, execution ledger, account state, or external settlement data.

## 5. What This Project Must Be Especially Careful About

This project uses paper results to judge whether the weather strategy has
profit potential. Any metric that can see future information after entry
contaminates that experiment.

For scoreable trade outcomes, prefer the actual `OPEN` row from
`paper_trades.csv`. Use `paper_decisions.csv` as fallback evidence only when
older files do not contain structured entry metadata.

Related docs:

- [Paper fees must flow through accounting](paper-fees-must-flow-through-accounting.md)
- [Dashboard trades and closed-market settlement](dashboard-trades-and-closed-market-settlement.md)
- [Dashboard large decision log initial scan](../performance-issues/dashboard-large-decision-log-initial-scan.md)
