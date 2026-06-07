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
  account book. Normal raw decision snapshots are off by default:
  `RAW_SNAPSHOTS_MODE=error` saves only error evidence, while `debug` is for a
  bounded investigation. The bot rotates active raw snapshots over 100MB into
  compressed `data/archive/` files, keeps recent raw archives for 7 days by
  default, and suspends raw writes with a `paper_runner_status.json` warning
  when disk pressure is dangerous. The Oracle VPS logrotate rule is a matching
  safety net and must not include paper state, trade, or decision ledgers.
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
- Do not recreate a fresh `paper_state.json` just because the file is missing
  while `paper_trades.csv` already has executed actions. That means the account
  book may have been lost, not that the paper account is new.
- `paper_state.json.journal` is a paper-accounting transaction marker. It
  means `paper_state.json` and `paper_trades.csv` may have been interrupted
  mid-update. Do not delete it just to restart the bot; inspect the state and
  trade ledgers, reconcile the mismatch, then remove the marker only as part of
  that operator recovery.

## Trading Interpretation

- `YES` and `NO` rows in `paper_decisions.csv` are candidate decisions. Actual first entries are `OPEN` rows in `paper_trades.csv`; same-side paper add-ons are `ADD` rows and update an existing open position rather than creating a duplicate position.
- `OPEN`, `ADD`, `CLOSE`, and `PARTIAL_CLOSE` are the executed paper actions
  that must update both `paper_state.json` and `paper_trades.csv`. If either
  write fails, the bot leaves `paper_state.json.journal` and fails closed
  instead of making more paper trades from uncertain accounting.
- On startup, `paper_trades.csv` is replayed like a receipt ledger from
  `BANKROLL_USD`: `OPEN` creates a position, `ADD` increases the same position,
  `PARTIAL_CLOSE` reduces shares and cost basis proportionally, and
  `CLOSE`/`SETTLED` removes the position while applying realized PnL. Replayed
  cash, realized PnL, open-position identity, shares, cost basis, and average
  entry price must match `paper_state.json`; otherwise follow fail-closed
  recovery instead of deleting, truncating, or rewriting either ledger.
- When entries appear missing, check existing open positions and exposure caps before assuming the entry path is broken.
- Repeated valid signals for an already-held market should not create duplicate positions. Same-side add-ons are allowed only through the explicit `ADD` path after the add-on price/probability/budget gates pass; opposite-side same-market entries remain blocked.

## Dashboard Readers

- Preserve bounded reads and cached totals for multi-GB files. Never reintroduce full startup scans of `paper_decisions.csv` or raw snapshots.
- Dashboard live views should not push high-volume SKIP or candidate rows every refresh.
- `paper_trades.csv` may contain both executed paper actions and SKIP diagnostics. `Recent Trades` should show executed paper actions only: `OPEN`, `ADD`, `CLOSE`, `SETTLED`, and `PARTIAL_CLOSE`. Realized rows and realized equity points should still use only realized actions: `CLOSE`, `SETTLED`, and `PARTIAL_CLOSE`.
- Prefer operator-useful summaries, open positions, realized trades, bounded recent trades, cached totals, a moderate visible refresh interval, and a slower hidden-tab interval.

## Settlement Review

- A closed Polymarket binary market may have empty winner fields while `outcomePrices` carries the final payout. Treat exact YES/NO `1/0` or `0/1` prices as settlement evidence for paper accounting.
- Do not settle from ambiguous outcome prices. If the values are not exact binary payout prices, keep the paper position open until a clear winner field or exact payout prices appear.

## Analysis And Reports

- `paper_decisions.csv` and `paper_trades.csv` are paper-performance source ledgers, not disposable cache files. Do not truncate, rewrite, or delete them to make reports faster.
- Do not rewrite an existing `paper_trades.csv` just to add newer columns.
  New trade files use the current full header, but legacy headers should remain
  as evidence and report code must use backward-compatible fallbacks.
- If `paper_state.json` contains cash, realized PnL, or open positions that
  cannot be reproduced by replaying the executed accounting rows in
  `paper_trades.csv`, treat that as an obvious evidence mismatch. If
  `paper_state.json` is missing but `paper_trades.csv` already has executed
  accounting rows, treat that as a lost account book. Start from fail-closed
  recovery, not from a fresh account and not from a rewritten trade ledger.
- New `paper_decisions.csv` rows compact verbose question, reason, and note
  text so the strategy evidence ledger does not become a raw-data warehouse.
  New `paper_event_portfolios.jsonl` rows keep selected legs, rejection
  counts/samples, and worst scenario PnL rather than full candidate maps.
- Full-history reports may still scan every row when their meaning depends on all rows, but they should stream rows and keep only aggregate counters, market-level lookups, or bounded result sets in memory.
- `analyze_paper.py` keeps the existing full-history report meaning by streaming decision and trade rows instead of materializing whole CSV files.
