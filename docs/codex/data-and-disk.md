# Data, Disk, and Observability Guide

## Philosophy

Record only what you need to **improve the strategy** or **diagnose a failure**.
Everything else is noise that wastes disk and makes analysis harder.

### Keep (forever, compressed monthly)
- `paper_trades.csv` — every OPEN / CLOSE / PARTIAL_CLOSE / SETTLED action
- `paper_state.json` — current account book (overwritten, not appended)

### Keep (7 days rolling)
- `forecast_request_log.jsonl` — Open-Meteo call receipts; useful for rate-limit audits
- `station_nowcast_request_log.jsonl` — METAR call receipts

### Archive on rotation (logrotate 100 MB, keep 5 compressed)
- `paper_decisions.csv` — OPEN / CLOSE / HOLD actions only (SKIP suppressed by default)
- `paper_event_portfolios.jsonl` — event-portfolio selections (only when trades selected)

### Discard (never useful)
- SKIP rows in `paper_decisions.csv` — "didn't trade because conditions not met";
  >95% of all writes; zero analytical value. Suppressed via
  `DECISIONS_LOG_SKIP_ENABLED=false` (default).

## Disk Bomb Risk Table

| File | Growth rate (no guard) | Guard |
|------|------------------------|-------|
| `paper_decisions.csv` | ~6 GB / 9 h (with SKIP) | SKIP suppressed + logrotate 100 MB |
| `paper_event_portfolios.jsonl` | ~1.2 GB / 9 h (all evals) | write-only-on-trade + logrotate 100 MB |
| `paper_trades.csv` | ~50 MB / 9 h | logrotate 100 MB |
| `paper_raw_snapshots.jsonl` | mode=error only | 100 MB internal cap |
| journalctl | unbounded | `SystemMaxUse=50M` in journald.conf |
| syslog | ~35 MB | standard logrotate (OS default) |

## Logrotate Configuration

Applied at `/etc/logrotate.d/polymarket-weather-bot`:

```
size 100M     # rotate when file exceeds 100 MB
rotate 5      # keep 5 compressed archives
compress      # gzip
copytruncate  # truncate in-place (bot keeps file handle open)
olddir /opt/polymarket-weather-bot/data/archive
```

Triggered hourly by `/etc/cron.d/polymarket-logrotate`.

## Journald Configuration

`/etc/systemd/journald.conf.d/polymarket.conf`:
```
[Journal]
SystemMaxUse=50M
```

Reload: `sudo systemctl restart systemd-journald`

## Weekly Performance Report

A cron script runs every Monday 00:00 UTC
(`/etc/cron.d/polymarket-weekly-report`) and writes to
`/opt/polymarket-weather-bot/data/weekly_report_YYYYMMDD.txt`.

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
           paper_raw_snapshots.jsonl forecast_rate_limit_state.json \
           forecast_cache.json paper_event_portfolios.jsonl
sudo systemctl start polymarket-weather-bot
```

Large files should be compressed to `data/archive/` before deletion.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DECISIONS_LOG_SKIP_ENABLED` | `false` | Write SKIP rows to paper_decisions.csv |
| `PORTFOLIO_LOG_SKIP_ENABLED` | `false` | Write portfolio rows when no trade selected |
| `RAW_SNAPSHOTS_MODE` | `error` | When to write raw snapshots (error/always/never) |
| `RAW_SNAPSHOTS_MAX_BYTES` | 104857600 | Max size before raw snapshot rotation |
