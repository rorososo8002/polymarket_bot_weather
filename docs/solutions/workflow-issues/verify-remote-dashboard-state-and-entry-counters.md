---
title: Verify remote dashboard state and entry counters before diagnosing paper entries
date: 2026-05-28
category: workflow-issues
module: weather_bot.dashboard
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "A local browser dashboard is served through an SSH tunnel"
  - "Local runtime files disagree with the dashboard's open-position count"
  - "The dashboard shows many YES/NO decisions but few actual OPEN trades"
tags: [dashboard, ssh-tunnel, paper-trading, runtime-data, observability]
---

# Verify remote dashboard state and entry counters before diagnosing paper entries

## Context

The browser dashboard at `127.0.0.1:8787` was reached through an SSH tunnel to the Oracle host, while the workspace also had local runtime files. Reading the local `paper_state.json` showed 9 open positions, but the live dashboard API and remote `/opt/polymarket-weather-bot/data/paper_state.json` showed 5 open positions.

The dashboard also displayed a large `진입 신호` count. That count came from cumulative decision rows whose side was `YES` or `NO`, not from actual `OPEN` trades. After the last open trade, every valid `YES` or `NO` decision was for one of the already-held markets, so the runner correctly skipped opening duplicates.

## Guidance

When a local dashboard and local runtime files disagree, first identify what the browser is actually connected to:

```powershell
netstat -ano | Select-String ':8787'
Get-CimInstance Win32_Process -Filter "ProcessId=<pid>" |
  Select-Object ProcessId,CommandLine
```

Then query the dashboard API and the remote files from the same host:

```powershell
$token = "<dashboard token>"
Invoke-RestMethod -Uri "http://127.0.0.1:8787/api/status" `
  -Headers @{ "X-Dashboard-Token" = $token }
```

```bash
cat /opt/polymarket-weather-bot/data/paper_runner_status.json
cat /opt/polymarket-weather-bot/data/paper_state.json
tail -n 80 /opt/polymarket-weather-bot/data/paper_trades.csv
tail -n 120 /opt/polymarket-weather-bot/data/paper_decisions.csv
```

For entry-count questions, distinguish these three counters:

- `actual_opens`: actual `OPEN` rows in `paper_trades.csv`.
- `open_positions`: currently held positions in `paper_state.json`.
- `entry_signals`: decision rows with side `YES` or `NO`; these include repeated signals for markets already held.

If `entry_signals` is high but `actual_opens` is flat, aggregate decisions since the last open and separate held from non-held market ids. In the May 28 investigation, there were 498,693 decisions after the last open, 102,494 `YES`/`NO` decisions, and 0 valid `YES`/`NO` decisions for non-held markets.

## Why This Matters

Local runtime files can be stale or from a different run when the dashboard is tunneled from a remote host. Treating them as authoritative can lead to the wrong conclusion about position count, cash, exposure, or whether the bot is stuck.

Decision rows are also not the same as trades. A held market can keep producing valid edge decisions for hours; `PaperBroker.has_any_position()` intentionally prevents opening a duplicate position in the same market. The dashboard must show actual opens separately so a large signal count does not look like missed trades.

## When to Apply

- A browser dashboard is available on `127.0.0.1` but may be backed by SSH port forwarding.
- Open-position counts differ between local files and the UI.
- The scanner shows many `YES`/`NO` rows, but `paper_trades.csv` has no new `OPEN` rows.
- Recent decisions say `No valid side evaluated` and the operator needs the concrete liquidity or edge reason.

## Examples

The corrected dashboard now exposes both fields:

```json
{
  "scanner": {
    "actual_opens": 6,
    "entry_signals": 114120
  },
  "summary": {
    "open_positions": 5
  }
}
```

The runner now expands opaque no-side skips with per-side reasons:

```text
edge below 5.00% [temperature]. No valid side evaluated.
Phase 2 이전 로그 예시:
YES liquidity filter: extreme ask=0.013 outside 0.08~0.92 [temperature] |
NO liquidity filter: extreme ask=0.995 outside 0.08~0.92 [temperature]
```

## Related

- [Verify VPS code, environment, and API health before trusting the dashboard](./verify-vps-code-env-and-api-health-2026-05-26.md)
- [Runner heartbeat and wall-clock cadence for long paper bot cycles](../logic-errors/runner-heartbeat-cadence-status-2026-05-25.md)
- [Invalid edge sentinels are not exit signals](../logic-errors/invalid-edge-sentinel-not-exit-signal.md)
