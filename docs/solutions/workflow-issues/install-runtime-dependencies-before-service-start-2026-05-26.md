---
title: Install runtime dependencies before starting the paper service
date: 2026-05-26
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

## Why This Matters

The main Python process can keep running even when a background thread dies. A process-level check alone can falsely suggest the service is healthy, while order-book streaming is actually unavailable. Domain health for this bot means the runner status, logs, and paper output files are advancing.

## When to Apply

- After adding or changing dependencies in `pyproject.toml`
- Before local service starts outside an activated virtual environment
- Before VPS service restarts after pulling a new checkout
- Whenever `paper_runner_status.json` says `streaming` but decision files stop advancing

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
