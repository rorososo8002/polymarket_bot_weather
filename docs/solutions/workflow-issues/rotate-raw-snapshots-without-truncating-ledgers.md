---
title: Rotate raw snapshots without truncating paper ledgers
date: 2026-06-04
last_updated: 2026-06-08
category: workflow-issues
module: vps_runtime_data
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "VPS runtime data grows enough to threaten disk capacity"
  - "Cleaning up paper bot data without destroying account or strategy evidence"
  - "Resetting the paper experiment only after the operator explicitly asks for a new bankroll window"
  - "Adding automatic cleanup for diagnostic raw snapshots"
  - "Recovering when the disk is already too full to upload a remote script"
tags: [vps, runtime-data, logrotate, disk, paper-trading, raw-snapshots, syslog, reset]
---

# Rotate raw snapshots without truncating paper ledgers

## 1. What The Problem Was

The Oracle VPS root disk first reached 84% usage because
`paper_raw_snapshots.jsonl` had grown to 18GB. The bot still needed to keep
paper-trading evidence, but leaving the raw diagnostic file unbounded would
eventually fill the server disk.

On 2026-06-04, the same server later reached 100% root disk usage. The largest
files were a new 18.7GB `paper_raw_snapshots.jsonl`, a 16GB
`paper_decisions.csv`, and oversized system logs: `/var/log/syslog` at 6.5GB
plus `/var/log/syslog.1` at 681MB.

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

`syslog` is the operating-system log. It is useful for recent operational
debugging, but it is not the paper account book, not the trade ledger, and not
the strategy evidence ledger. When the disk is already at 100%, clearing
oversized system logs can be the first step that creates enough free space to
archive project diagnostics safely.

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
past 100MB, keeps only recent raw archives with `maxage 7`, and uses zstd
compression.

The code-level prevention now starts earlier than logrotate:

- Normal raw decision snapshots are off by default with
  `RAW_SNAPSHOTS_MODE=error`.
- `RAW_SNAPSHOTS_MODE=debug` may be used only for a bounded investigation.
- Active raw snapshots rotate in-process over `RAW_SNAPSHOTS_MAX_BYTES`
  (`104857600`, or 100MB, by default) into compressed `data/archive/` files.
- Old raw archives are pruned after `RAW_SNAPSHOTS_RETENTION_DAYS` days.
- If disk pressure is dangerous, raw snapshot writes suspend and
  `paper_runner_status.json` gets a `raw_snapshot_storage` warning.
- `paper_decisions.csv` and `paper_event_portfolios.jsonl` store compact
  summaries for new rows instead of raw payloads or full candidate maps.

During the 2026-06-04 emergency cleanup, the disk was too full for `scp` to
upload even a small remote script to `/tmp`. The working order was:

1. Delete the rotated oversized `/var/log/syslog.1`.
2. Truncate the active `/var/log/syslog` without deleting the file itself.
3. Re-check `df -h /` to confirm enough free space for a script and compressed
   output.
4. Stop `polymarket-weather-bot`, move the active raw snapshot into
   `data/archive/`, recreate an empty `paper_raw_snapshots.jsonl` owned by
   `polymarket`, compress the archived raw snapshot with zstd, and restart the
   bot.

That second cleanup reduced root disk usage from 100% to 50%. The new archive
file was:

```text
/opt/polymarket-weather-bot/data/archive/paper_raw_snapshots.20260604T115423Z.jsonl.zst
```

The active `paper_raw_snapshots.jsonl` was recreated as a 0-byte writable file,
and both `polymarket-weather-bot` and `polymarket-weather-dashboard` were
verified active afterward.

On 2026-06-05, the operator explicitly asked to discard old runtime evidence
and start a fresh 200 USD paper experiment. That is a different operation from
emergency cleanup. The safe reset order was:

1. Stop both `polymarket-weather-bot` and `polymarket-weather-dashboard`.
2. Delete old paper runtime files only after the operator-approved reset
   boundary was clear.
3. Recreate `data/paper_state.json` with 200 USD cash, zero positions, and
   zeroed paper stats before restarting the bot.
4. Recreate `paper_trades.csv` and `paper_decisions.csv` with current headers
   so future appends stay parseable.
5. Set `PORTFOLIO_DECISIONS_JSONL_PATH` under `data/` so portfolio diagnostics
   do not grow as a root-level project file.
6. Restart services, run remote pytest, verify dashboard HTML and protected
   `/api/status`, and then treat all later performance as a new experiment
   window.

That reset reduced root disk use from 100% to 14%. After restart, the bot was
paper-only and began trading from the fresh 200 USD bankroll.

On 2026-06-08, the operator again explicitly asked to remove previous
paper-trading data from the Oracle VPS before starting over. A reused dashboard
diagnostic script was the wrong first tool because it streamed large ledgers to
summarize them; the SSH command timed out while `paper_decisions.csv` was about
4.5GB and `paper_event_portfolios.jsonl` was about 868MB. The correction was to
kill the remote diagnostic process, switch to metadata-only discovery, and
delete only `paper*` files under `/opt/polymarket-weather-bot/data` after
stopping both services. The reset removed six files, restarted
`polymarket-weather-bot` and `polymarket-weather-dashboard`, verified the
dashboard page returned 200, verified bare `/api/status` returned the expected
403 with token protection, and verified header-authenticated `/api/status`
returned 200 with zero open positions.

## 4. What To Check Next Time

- Check disk pressure with `df -h /` before and after cleanup.
- Check runtime file sizes with a size summary, not full file reads.
- Do not reuse diagnostic scripts that parse full `paper_decisions.csv`,
  `paper_trades.csv`, or `paper_event_portfolios.jsonl` when the task is only a
  reset or cleanup. Use `find`/`stat` metadata, bounded tails, or small samples.
- If a remote diagnostic script times out, check for the still-running process
  with `pgrep -af`, stop that process, and switch to a bounded command shape
  before continuing.
- If the disk is already at 100%, a remote script upload can fail. Free a small,
  safe target first, such as oversized system logs, then use the remote-script
  pattern for the larger operation.
- Rotate or compress `paper_raw_snapshots.jsonl` only after stopping the bot or
  using a rotation method that safely creates a new active file.
- Check `paper_runner_status.json` for `raw_snapshot_storage.status=suspended`
  before assuming raw diagnostics are still being written.
- Keep `RAW_SNAPSHOTS_MODE=debug` time-bounded. Turn it back to `error` after
  the investigation.
- Validate logrotate with `sudo logrotate -d /etc/logrotate.d/polymarket-weather-bot-runtime`.
- Confirm the bot is active again and that `paper_runner_status.json` updates
  after restart.
- Run SSH checks serially from Windows PowerShell. Parallel SSH commands can
  temporarily fail to access the same private key file.
- If the operator requests a fresh bankroll reset, document the reset boundary
  in the handoff docs and never mix pre-reset and post-reset performance
  results.
- When a public dashboard has `DASHBOARD_TOKEN` set, a bare `/api/status` 403 is
  expected protection, not an SSH or operator-permission failure. Verify `/`
  separately from authenticated `/api/status` before explaining the URL.

## 5. What This Project Must Be Especially Careful About

Do not delete, truncate, or rotate `paper_state.json`, `paper_trades.csv`, or
`paper_decisions.csv` with the same rule as raw snapshots. Those files are the
paper account book and strategy evidence. If they grow too large, build
archive-aware readers or explicit export jobs first, then preserve enough
history to keep settlement, performance review, and debugging meaningful.

The only exception is an explicit operator-approved paper reset, such as
"start a new 200 USD experiment." In that case, deletion is not cache cleanup;
it is a new measurement window. Stop services first, recreate the account book
and CSV headers deliberately, record the reset date, and judge profitability
only from the post-reset data.
