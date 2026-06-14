# Paper Validation Runbook

## Purpose

This runbook defines when paper results are evidence and when they are only a
short, lucky run. It does not authorize live trading.

The project stays paper-only until the gates below pass and the user explicitly
starts a separate live-trading safety project. Do not add wallet connection,
private keys, signing, real orders, redemption, claims, copy trading, or
`LiveBroker` while running this validation.

## Experiment Version

Every material strategy, parser, forecast, sizing, portfolio, settlement, or
risk-rule change starts a new experiment version.

Record the version label in the daily notes or report folder before judging the
next window. Do not mix pre-change and post-change results into one claim.

## Evidence Files

- `paper_state.json` is the paper account book: cash, open positions, and
  realized PnL.
- `paper_trades.csv` is the execution receipt ledger: `OPEN`, `ADD`,
  `PARTIAL_CLOSE`, `CLOSE`, and `SETTLED`.
- `paper_decisions.csv` is the strategy decision ledger: YES, NO, HOLD, and
  configured diagnostic rows.
- `paper_event_portfolios.jsonl` is the event-portfolio selection audit.
- `daily_report_YYYYMMDD.txt` and the minimum performance report are the first
  operator summaries to review.

## Daily Routine

1. Confirm the bot is still paper-only and running.
2. Review the latest daily report and minimum performance report.
3. Check ledger warnings, replay warnings, stale-data blocks, no-liquidity
   blockers, settlement anomalies, and nowcast anomalies.
4. Confirm open positions still have bid-depth exit evidence or a clear hold
   blocker.
5. Record any strategy or configuration change as a new experiment version.

## Live-Discussion Gates

All gates must pass before discussing live execution. Passing these gates still
does not permit live trading; it only allows a separate safety project to be
planned.

1. Run length: at least 30 days of continuous paper-only operation.
2. Sample size: enough decision rows to cover the active strategy shape. The
   default minimum is 300 non-SKIP decision rows.
3. Trade sample: enough open/close trades to judge execution quality. The
   default minimum is 30 executed `OPEN` or `ADD` rows and 20 executed
   `CLOSE`, `PARTIAL_CLOSE`, or `SETTLED` rows.
4. Trusted PnL: bid/ask-depth net PnL after fees is positive over the window.
5. Reference check: the midpoint/reference gap is reviewed. If reference PnL is
   positive but bid/ask-depth net PnL is not, the gate fails.
6. Liquidity check: no-liquidity exit blockers are counted and explained. A
   high or unexplained no-liquidity rate fails the gate.
7. Stale-data check: stale forecasts, stale nowcast, and stale order books do
   not create fake entries, fake closes, or trusted PnL.
8. Shape check: exact, range, threshold, daily-high, and daily-low results are
   separated before claiming strategy quality.
9. Ledger check: `paper_state.json` and `paper_trades.csv` replay consistently
   with no unresolved journal or warning.
10. Test check: core tests pass locally before the window is summarized.
11. Boundary check: paper-only remains intact: no keys, wallet, signing, real
    orders, redemption, claims, copy trading, or `LiveBroker`.

## Failure Handling

If any gate fails, do not tune toward live trading. Classify the failure first:
liquidity, stale data, market-rule evidence, station evidence, settlement,
portfolio overlap, sizing, drawdown, or ledger integrity.

After a material fix, start a new experiment version and restart the 30 days.

## Core Tests

Before summarizing a validation window, run the known-good local test command
from `docs/codex/known-good-commands.md`:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

For a narrow documentation-only update, `tests/test_workflow_defaults.py` is
the minimum focused test.
