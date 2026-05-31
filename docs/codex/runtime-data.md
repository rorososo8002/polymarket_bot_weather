# Runtime Data Review Rules

Read this file only for runtime logs, paper-trading data, dashboard readers, or investigations into bot behavior.

## Safe Reading

- Runtime outputs such as `paper_raw_snapshots.jsonl`, `paper_decisions.csv`, `paper_trades.csv`, `forecast_cache.json`, `paper_state.json`, and `paper_runner_status.json` can become very large.
- Treat `runtime/live-paper-bot.restart.out.log`, `paper_raw_snapshots.jsonl`, and `paper_decisions.csv` as token-dangerous.
- Do not run bare `Get-Content`, `cat`, `type`, `more`, or unrestricted `python read_text()` against token-dangerous files.
- For normal health checks, recent errors, or "why is it not trading now" questions, inspect only the latest 100 lines by default.
- Increase the window only when needed, and state why.
- For older data, filter by time range, market, city, event type, or decision reason.
- Prefer counts, summaries, tails, targeted searches, and small samples over opening complete files.

## Trading Interpretation

- `YES` and `NO` rows in `paper_decisions.csv` are candidate decisions. Actual entries are `OPEN` rows in `paper_trades.csv`.
- When entries appear missing, check existing open positions and exposure caps before assuming the entry path is broken.
- Repeated valid signals for an already-held market should not create duplicate positions.

## Dashboard Readers

- Preserve bounded reads and cached totals for multi-GB files. Never reintroduce full startup scans of `paper_decisions.csv` or raw snapshots.
- Dashboard live views should not push high-volume SKIP or candidate rows every refresh.
- Prefer operator-useful summaries, open positions, realized trades, bounded recent trades, cached totals, a moderate visible refresh interval, and a slower hidden-tab interval.
