# Runtime Data Rules

Read this only for VPS files, disk growth, dashboard readers, or paper-ledger
investigations.

## Account Evidence

- `paper_state.json` is the current paper account book: cash, positions, cost,
  and PnL. It is not a cache.
- `paper_trades.csv` is the execution receipt ledger. Actual entries are OPEN
  or ADD rows; CLOSE, PARTIAL_CLOSE, and SETTLED realize results.
- `paper_decisions.csv` is the strategy evidence ledger. YES/NO decisions are
  candidates, not proof that a trade occurred.
- `paper_state.json.journal` means an accounting write may have been
  interrupted. Fail closed and reconcile before trading.

Never delete, truncate, or rewrite those files to speed up a report. If state
is missing while receipts exist, that is a lost account book, not a fresh
account. Old ledger rows without newer columns must remain readable.

## Bounded Diagnostics

- `paper_raw_snapshots.jsonl`: raw investigation evidence, normally error-only;
  rotates at 100 MB.
- `paper_skip_diagnostics.jsonl`: detailed SKIP black box; rotates at 10 MB.
- `station_nowcast_request_log.jsonl`: provider-call diagnosis; rotates at
  10 MB.
- `paper_event_portfolios.jsonl`: selected portfolios by default; rotates at
  10 MB when investigation logging is enabled.
- Known diagnostic archives under `data/archive/` share a 20 MB cleanup budget.

The cleanup may delete only recognized diagnostic archives. It must never
delete active account/ledger files or an explicitly created experiment audit
folder. Logrotate moves bounded files; `weather_bot.runtime_cleanup` prunes the
oldest known diagnostic archives after rotation.

## Safe Inspection

Do not bulk-read ledgers, snapshots, caches, archives, or service logs. Start
with sizes, row counts, headers, tails, grouped reason counts, and targeted
searches. Full-history reports may stream every row but should retain only
aggregates or bounded samples in memory.

Useful runtime diagnosis:

```powershell
$env:PYTHONPATH='src'
python -m weather_bot.runtime_diagnostics --data-dir data --tail 500
```

On Oracle, run the same module as the `polymarket` service user from
`/opt/polymarket-weather-bot`.

Interpret SKIP correctly:

- stale/missing official data protects against false station extremes;
- residual/formation SKIPs protect against acting before the day develops;
- no ask, wide spread, disabled book, or unknown CLOB state means no executable
  entry;
- budget/exposure SKIPs protect the paper account;
- SKIP is “no valid new probability”, never the opposite trading side.

Investigate when the same safety SKIP repeats across three cycles, the service
is active but station/WebSocket freshness stays stale, or decisions remain
mostly SKIP after local formation windows should be open.

## Dashboard And Reports

Dashboard startup must use bounded reads and cached totals. Recent trades show
executed actions, not SKIP noise. Realized equity uses CLOSE, PARTIAL_CLOSE, and
SETTLED rows only. Closed binary markets may use exact outcome prices `1/0` or
`0/1` as settlement evidence when winner fields are empty.

Reports must reconcile account state against execution receipts and must not
turn `paper_decisions.csv` or `paper_trades.csv` into disposable caches.
