# Runtime Data Review Rules

Read this file only for runtime logs, paper-trading data, dashboard readers, or investigations into bot behavior.

## Safe Reading

- Runtime outputs such as `paper_raw_snapshots.jsonl`, `paper_decisions.csv`, `paper_trades.csv`, `forecast_cache.json`, `forecast_request_log.jsonl`, `paper_state.json`, and `paper_runner_status.json` can become very large.
- Treat `runtime/live-paper-bot.restart.out.log`, `paper_raw_snapshots.jsonl`, and `paper_decisions.csv` as token-dangerous.
- Do not run bare `Get-Content`, `cat`, `type`, `more`, or unrestricted `python read_text()` against token-dangerous files.
- For normal health checks, recent errors, or "why is it not trading now" questions, inspect only the latest 100 lines by default.
- Increase the window only when needed, and state why.
- For older data, filter by time range, market, city, event type, or decision reason.
- Prefer counts, summaries, tails, targeted searches, and small samples over opening complete files.
- `paper_raw_snapshots.jsonl` is detailed diagnostic evidence, not the paper
  account book. It may be rotated and compressed when it grows large. The
  Oracle VPS uses `/etc/logrotate.d/polymarket-weather-bot-runtime`, matching
  `deploy/logrotate/polymarket-weather-bot-runtime`, to move raw snapshots over
  1GB into `data/archive/` and compress them with zstd.
- `forecast_cache.json` is a forecast result cache, not an API request ledger.
  It overwrites entries by location/model cache key, so it cannot reconstruct
  total Open-Meteo calls after the fact.
- `forecast_request_log.jsonl` is the Open-Meteo request ledger. It records real
  HTTP attempts only, including cache-miss reason, safe city/station metadata,
  rounded coordinates, and 429 rate-limit responses. The same VPS logrotate
  rule moves it to `data/archive/` over 10MB and compresses it with zstd.
- Do not delete `paper_state.json`, `paper_trades.csv`, or
  `paper_decisions.csv` as a cleanup shortcut. `paper_state.json` is the current
  paper account book, `paper_trades.csv` is the execution ledger, and
  `paper_decisions.csv` is the strategy evidence ledger.

## Trading Interpretation

- `YES` and `NO` rows in `paper_decisions.csv` are candidate decisions. Actual entries are `OPEN` rows in `paper_trades.csv`.
- When entries appear missing, check existing open positions and exposure caps before assuming the entry path is broken.
- Repeated valid signals for an already-held market should not create duplicate positions.

## Dashboard Readers

- Preserve bounded reads and cached totals for multi-GB files. Never reintroduce full startup scans of `paper_decisions.csv` or raw snapshots.
- Dashboard live views should not push high-volume SKIP or candidate rows every refresh.
- `paper_trades.csv` may contain both executed paper actions and SKIP diagnostics. `Recent Trades`, realized rows, and realized equity points should show executed paper actions only: `OPEN`, `CLOSE`, `SETTLED`, and `PARTIAL_CLOSE`.
- Prefer operator-useful summaries, open positions, realized trades, bounded recent trades, cached totals, a moderate visible refresh interval, and a slower hidden-tab interval.

## Settlement Review

- A closed Polymarket binary market may have empty winner fields while `outcomePrices` carries the final payout. Treat exact YES/NO `1/0` or `0/1` prices as settlement evidence for paper accounting.
- Do not settle from ambiguous outcome prices. If the values are not exact binary payout prices, keep the paper position open until a clear winner field or exact payout prices appear.

## Analysis And Reports

- `paper_decisions.csv` and `paper_trades.csv` are paper-performance source ledgers, not disposable cache files. Do not truncate, rewrite, or delete them to make reports faster.
- Full-history reports may still scan every row when their meaning depends on all rows, but they should stream rows and keep only aggregate counters, market-level lookups, or bounded result sets in memory.
- `analyze_paper.py` keeps the existing full-history report meaning by streaming decision and trade rows instead of materializing whole CSV files.
- `shadow_signals.py` keeps shadow research separate from execution and streams bot decision/trade CSV rows while comparing only the bounded signal set loaded from `shadow_external_signals.jsonl`.
