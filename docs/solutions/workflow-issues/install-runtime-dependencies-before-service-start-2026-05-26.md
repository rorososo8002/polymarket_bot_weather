---
title: Install runtime dependencies before starting the paper service
date: 2026-05-26
last_updated: 2026-06-07
category: workflow-issues
module: weather_bot.live_paper_runner
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "Starting the local or VPS paper bot after dependency or checkout changes"
  - "A Python service reaches its outer status but a background worker thread fails"
tags: [service-start, dependencies, websocket-client, paper-trading, verification]
---

# Install runtime dependencies before starting the paper service

## Context

The paper bot reached the `phase=streaming` status, but the WebSocket order-book thread crashed because the current Python environment did not have `websocket-client` installed. The dependency was already declared in `pyproject.toml`, but the editable install had not been refreshed in the runtime environment.

On 2026-06-07 the same symptom was found again in `runtime/live-paper-bot.err.log`. The local `pyproject.toml` declaration was correct and `C:\Users\wpdla\Python312\python.exe -c "import websocket"` succeeded, but the runner still needed a code-level guard so a future missing runtime dependency is visible in `paper_runner_status.json`, not only in stderr.

## Guidance

Before starting the service from a fresh checkout, changed dependency set, or different Python interpreter, run:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pip install -e .
```

Then start the bot and verify both the process and domain status:

```powershell
$env:PYTHONPATH='src'
python -m weather_bot.live_paper_runner
Get-Content paper_runner_status.json -Raw
Get-Content runtime\live-paper-bot.err.log -Tail 80
```

The status file should move beyond discovery into `streaming`, and stderr should not contain `ModuleNotFoundError` from the WebSocket thread.

The runner should also fail fast before launching the background WebSocket
thread if `import websocket` is unavailable. In that case
`paper_runner_status.json` should include:

```json
{
  "phase": "error",
  "failed_phase": "websocket_start",
  "websocket": {
    "thread_alive": false,
    "stale": true,
    "last_error": "websocket-client import failed: ModuleNotFoundError: No module named 'websocket'"
  }
}
```

## Why This Matters

The main Python process can keep running even when a background thread dies. A process-level check alone can falsely suggest the service is healthy, while order-book streaming is actually unavailable. Domain health for this bot means the runner status, logs, and paper output files are advancing.

For this paper bot, WebSocket is the real-time order-book telephone line. If
that line cannot even be imported, the strategy must not guess from stale or
missing prices. It should stop the paper stream startup and make the reason
operator-readable.

## When to Apply

- After adding or changing dependencies in `pyproject.toml`
- Before local service starts outside an activated virtual environment
- Before VPS service restarts after pulling a new checkout
- Whenever `paper_runner_status.json` says `streaming` but decision files stop advancing
- Whenever stderr contains `ModuleNotFoundError: No module named 'websocket'`
  or the dashboard WebSocket panel reports a failed receiver thread

## Examples

The failure looked like:

```text
ModuleNotFoundError: No module named 'websocket'
RuntimeError: Install websocket-client to use real-time Polymarket orderbook streaming.
```

The fix was:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pip install -e .
```

The prevention test is:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest tests/test_realtime_runner.py tests/test_realtime_orderbook.py -q
```

After reinstalling, the service reached:

```json
{
  "phase": "streaming",
  "message": "websocket streaming 82 tokens across 41 markets",
  "markets_total": 41
}
```

## Related

- [VPS live paper runbook](../../VPS_LIVE_PAPER.md)
- [Production status](../../production-progress.md)
