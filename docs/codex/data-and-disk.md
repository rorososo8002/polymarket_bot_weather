# Data, Disk, and Observability Guide

## Philosophy

Record only what you need to **improve the strategy** or **diagnose a failure**.
Everything else is noise that wastes disk and makes analysis harder.

### Keep (forever, compressed monthly)
- `paper_trades.csv` — every OPEN / CLOSE / PARTIAL_CLOSE / SETTLED action
- `paper_state.json` — current account book (overwritten, not appended)
  **Rule:** websocket health keys (`last_websocket_health`,
  `last_websocket_token_health`) are excluded from position metadata when
  saving. They are diagnostic-only and must not persist to disk. Storing a
  health snapshot that includes `subscribed_book_ts` (hundreds of token IDs)
  caused paper_state.json to grow unboundedly each cycle.

### Keep (7 days rolling)
- `station_nowcast_request_log.jsonl` — official-station observation call receipts

### Archive on rotation
- `paper_raw_snapshots.jsonl` — 100 MB internal cap plus logrotate
- `paper_skip_diagnostics.jsonl` — 100 MB internal cap plus logrotate
- `station_nowcast_request_log.jsonl` — 10 MB logrotate
- `paper_event_portfolios.jsonl` — 10 MB logrotate, only selections by default

### Delete automatically after 100 MB
- `data/archive/` diagnostic archives only. `runtime_cleanup` deletes the
  oldest known diagnostic archive files once their combined size exceeds
  100 MB.
- Never delete active account ledgers automatically: `paper_state.json`,
  `paper_trades.csv`, and `paper_decisions.csv` are validation evidence, not
  disposable cache.

### Keep Bounded
- SKIP rows in `paper_decisions.csv` stay suppressed via
  `DECISIONS_LOG_SKIP_ENABLED=false` (default). Continuous SKIP investigation
  uses `paper_skip_diagnostics.jsonl`, which is diagnostic-only and capped at
  100 MB before rotation/archive pruning.

## Disk Bomb Risk Table

| File | Growth rate (no guard) | Guard |
|------|------------------------|-------|
| `paper_decisions.csv` | ~6 GB / 9 h (with SKIP) | SKIP suppressed by default; use bounded `paper_skip_diagnostics.jsonl` |
| `paper_skip_diagnostics.jsonl` | high-volume when enabled | active file rotates at 100 MB; diagnostic archives pruned |
| `paper_event_portfolios.jsonl` | ~1.2 GB / 9 h (all evals) | write-only-on-trade by default; for investigations use skip logging with logrotate 10 MB plus archive pruning |
| `paper_trades.csv` | ~50 MB / 9 h | executed trades only; do not rotate until replay is archive-aware |
| `paper_raw_snapshots.jsonl` | mode=error only | 100 MB internal cap |
| journalctl | unbounded | `SystemMaxUse=50M` in journald.conf |
| syslog | ~35 MB | standard logrotate (OS default) |

## Logrotate Configuration

Applied at `/etc/logrotate.d/polymarket-weather-bot`:

```
size 100M or 10M  # per-file risk
rotate 5          # bounded compressed archives
compress          # zstd
olddir /opt/polymarket-weather-bot/data/archive
```

Triggered hourly by `/etc/cron.d/polymarket-logrotate`.

## Runtime Cleanup

`runtime_cleanup` is the second guard after logrotate. Logrotate moves
high-volume diagnostics into `data/archive/`; cleanup keeps that archive under
the operator budget.

Recommended VPS command:

```bash
sudo -u polymarket /opt/polymarket-weather-bot/.venv/bin/python -m weather_bot.runtime_cleanup \
  --data-dir /opt/polymarket-weather-bot/data \
  --max-archive-bytes 104857600
```

It deletes only known diagnostic archive names:

- `paper_raw_snapshots*`
- `paper_skip_diagnostics*`
- `station_nowcast_request_log*`
- `paper_event_portfolios*`

Use `--dry-run` before changing the cron rule if you need to inspect what would
be deleted.

## Journald Configuration

`/etc/systemd/journald.conf.d/polymarket.conf`:
```
[Journal]
SystemMaxUse=50M
```

Reload: `sudo systemctl restart systemd-journald`

## Daily Performance Report

A cron script runs every day at 00:00 UTC
(`/etc/cron.d/polymarket-daily-report`) and writes to
`/opt/polymarket-weather-bot/data/daily_report_YYYYMMDD.txt`.

Report sections:
- Bankroll delta and total P&L
- Win rate (closed positions only)
- City-level breakdown (wins / losses / net P&L)
- Bucket-type breakdown (exact / range / lower_tail / upper_tail)
- Largest single win and loss
- Open positions summary

## Paper Reset Procedure

All runtime files live under `/opt/polymarket-weather-bot/data/`, not the app root.

```bash
sudo systemctl stop polymarket-weather-bot
cd /opt/polymarket-weather-bot/data
sudo rm -f paper_state.json paper_trades.csv paper_decisions.csv \
           paper_raw_snapshots.jsonl paper_event_portfolios.jsonl \
           paper_skip_diagnostics.jsonl station_nowcast_request_log.jsonl \
           paper_runner_status.json
sudo find archive -type f -delete
sudo systemctl start polymarket-weather-bot
```

Large diagnostic files should be compressed to `data/archive/` before deletion.
Do not manually delete or rotate `paper_state.json` or `paper_trades.csv`
unless you are intentionally resetting the paper experiment with the bot
stopped.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DECISIONS_LOG_SKIP_ENABLED` | `false` | Write SKIP rows to paper_decisions.csv |
| `SKIP_DIAGNOSTICS_ENABLED` | `true` | Write compact SKIP rows to bounded diagnostic JSONL |
| `SKIP_DIAGNOSTICS_JSONL_PATH` | `paper_skip_diagnostics.jsonl` | SKIP diagnostic file |
| `SKIP_DIAGNOSTICS_MAX_BYTES` | `104857600` | Max active SKIP diagnostic size before rotation |
| `SKIP_DIAGNOSTICS_ARCHIVE_MAX_BYTES` | `104857600` | Max SKIP diagnostic archive budget |
| `PORTFOLIO_LOG_SKIP_ENABLED` | `false` | Write portfolio rows when no trade selected |
| `RAW_SNAPSHOTS_MODE` | `error` | When to write raw snapshots (error/always/never) |
| `RAW_SNAPSHOTS_MAX_BYTES` | 104857600 | Max size before raw snapshot rotation |
