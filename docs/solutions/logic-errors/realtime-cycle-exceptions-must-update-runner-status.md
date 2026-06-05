---
title: Realtime cycle exceptions must update runner status
date: 2026-06-06
category: logic-errors
module: weather_bot.live_paper_runner
problem_type: logic_error
component: background_job
symptoms:
  - "A realtime refresh-cycle exception could end the process before paper_runner_status.json recorded phase=error"
  - "The dashboard could keep showing an old discovery or streaming state after the bot had already failed"
  - "systemd Restart=always could restart the service without preserving the operator-visible failure reason"
root_cause: missing_workflow_step
resolution_type: code_fix
severity: high
tags: [runner-status, realtime, systemd, dashboard, paper-trading, backoff]
---

# Realtime cycle exceptions must update runner status

## Problem
`run_realtime_forever()` can fail while discovering markets, preparing forecasts,
starting the WebSocket stream, or writing status. Before this fix, some of those
exceptions could escape before `paper_runner_status.json` recorded an error.

`paper_runner_status.json` is the operator status board. It tells the dashboard
whether the bot is discovering, streaming, stale, or failed. If the file is not
updated on failure, the dashboard can show an old state while the paper bot is
actually gone or restarting.

## Symptoms
- A fake `PolymarketClient.discover_weather_markets()` raising `RuntimeError`
  escaped directly from `run_realtime_forever()`.
- The runner status file still showed the previous phase, such as discovering.
- The only recovery path was `systemd Restart=always`, which can restart the
  process but cannot explain the failed refresh cycle to the dashboard.

## What Didn't Work
- Relying on process supervision alone was incomplete. `systemd` can restart a
  process, but it does not write project-specific status fields such as
  `phase`, `failed_phase`, or the market-discovery error message.
- Existing stream tests stopped the infinite loop by raising from
  `stream.start()`. Once the runner correctly catches stream-start exceptions,
  tests also need to stop at the retry backoff boundary.

## Solution
Wrap one whole realtime refresh cycle in a `try`/`except` block. Keep a
`failed_phase` label current as the loop moves through discovery, market
preparation, forecast preparation, WebSocket startup, status updates, monitoring,
and stream shutdown.

On any normal `Exception`, stop any partially created stream, then write:

```python
write_runner_status(
    settings,
    "error",
    message=f"realtime refresh cycle failed during {failed_phase}: {exc}",
    failed_phase=failed_phase,
    error_type=exc.__class__.__name__,
    cycle_started_at=cycle_started_at,
)
```

After writing the status, sleep for a bounded backoff before retrying. The
backoff prevents a broken dependency from creating a tight infinite retry loop.

Add a regression test where `discover_weather_markets()` raises
`RuntimeError("gamma outage")`. The test patches `time.sleep()` to stop after
the error status is written, then asserts:

- `phase == "error"`
- `failed_phase == "market_discovery"`
- the message includes the original exception text

## Why This Works
The fix separates two responsibilities:

- `systemd Restart=always` is process supervision. It can restart the program.
- `paper_runner_status.json` is domain supervision. It explains what the bot was
  doing and why the paper strategy failed or paused.

The dashboard needs the second signal. A process can restart too fast for a
human to see the original terminal error, but the status file stays available
for the next dashboard read.

## Prevention
- Treat every long-running paper bot loop as one observable unit of work. If
  the unit fails, write a runner-status error before retrying or exiting.
- Include the original exception text in the operator message, but do not add
  secrets, private keys, wallet data, or live-trading state.
- When tests intentionally break an infinite loop, stop it at the backoff or
  sleep boundary after status is written, not at the exception source that the
  production code is supposed to catch.
- Keep this paper-only. Error handling must never add wallet connections, real
  orders, signing, or live execution.

## Related Issues
- [Runner heartbeat and wall-clock cadence for long paper bot cycles](./runner-heartbeat-cadence-status-2026-05-25.md)
