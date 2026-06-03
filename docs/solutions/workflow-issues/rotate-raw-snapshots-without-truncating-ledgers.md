---
title: Rotate raw snapshots without truncating paper ledgers
date: 2026-06-04
category: workflow-issues
module: vps_runtime_data
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "VPS runtime data grows enough to threaten disk capacity"
  - "Cleaning up paper bot data without destroying account or strategy evidence"
  - "Adding automatic cleanup for diagnostic raw snapshots"
tags: [vps, runtime-data, logrotate, disk, paper-trading, raw-snapshots]
---

# Rotate raw snapshots without truncating paper ledgers

## 1. What The Problem Was

The Oracle VPS root disk reached 84% usage because
`paper_raw_snapshots.jsonl` had grown to 18GB. The bot still needed to keep
paper-trading evidence, but leaving the raw diagnostic file unbounded would
eventually fill the server disk.

## 2. Why It Was A Problem

Runtime data is not all the same kind of data. `paper_state.json` is the paper
account book: it records current cash, open positions, and position state.
`paper_trades.csv` is the execution ledger: it records what the paper account
actually opened, closed, or settled. `paper_decisions.csv` is the strategy
evidence ledger: it explains why signals were opened, skipped, or rejected.

`paper_raw_snapshots.jsonl` is different. It is detailed diagnostic evidence.
It is useful when debugging market payloads, but it is not the account book and
not the execution ledger. Treating all four files as equally disposable would
damage the paper experiment. Treating all four as equally unbounded would risk
filling the VPS disk.

## 3. How It Was Fixed

Stop the bot before moving the active raw snapshot, then restart it with a fresh
empty `paper_raw_snapshots.jsonl`. Compress only the old raw snapshot into
`data/archive/`:

```text
/opt/polymarket-weather-bot/data/archive/paper_raw_snapshots.20260603T115820Z.jsonl.zst
```

The 18GB raw snapshot compressed to 136MB, and root disk usage dropped from 84%
to 48%.

Then install logrotate for the raw snapshot only:

```text
/etc/logrotate.d/polymarket-weather-bot-runtime
```

That rule moves `paper_raw_snapshots.jsonl` to `data/archive/` whenever it grows
past 1GB, keeps 14 compressed rotations, and uses zstd compression.

## 4. What To Check Next Time

- Check disk pressure with `df -h /` before and after cleanup.
- Check runtime file sizes with a size summary, not full file reads.
- Rotate or compress `paper_raw_snapshots.jsonl` only after stopping the bot or
  using a rotation method that safely creates a new active file.
- Validate logrotate with `sudo logrotate -d /etc/logrotate.d/polymarket-weather-bot-runtime`.
- Confirm the bot is active again and that `paper_runner_status.json` updates
  after restart.

## 5. What This Project Must Be Especially Careful About

Do not delete, truncate, or rotate `paper_state.json`, `paper_trades.csv`, or
`paper_decisions.csv` with the same rule as raw snapshots. Those files are the
paper account book and strategy evidence. If they grow too large, build
archive-aware readers or explicit export jobs first, then preserve enough
history to keep settlement, performance review, and debugging meaningful.
