---
title: Use space-free journalctl time filters over PowerShell SSH
date: 2026-05-29
category: workflow-issues
module: vps_operations
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - "Collecting recent service logs from the Oracle VPS through Windows PowerShell and ssh"
  - "Counting dashboard or bot log lines with journalctl"
  - "The command includes a relative journalctl time such as '-30 min'"
tags: [powershell, ssh, journalctl, vps, dashboard, token-redaction]
---

# Use space-free journalctl time filters over PowerShell SSH

## Context

Windows PowerShell, `ssh`, the remote shell, and `journalctl` each parse
arguments. A command such as `journalctl --since '-30 min'` can arrive on the
VPS as two separate words, so `journalctl` treats `min` as a match filter and
fails with an error like:

```text
Failed to add match 'min': Invalid argument
```

This happened while measuring dashboard request volume. The underlying check was
valid, but the time argument shape caused avoidable delay.

## Guidance

Use relative time arguments with no spaces when running `journalctl` through
PowerShell SSH:

```powershell
ssh -i $key ubuntu@140.245.69.242 sudo journalctl -u polymarket-weather-dashboard --since=-30min --no-pager
ssh -i $key ubuntu@140.245.69.242 sudo journalctl -u polymarket-weather-bot --since=-2h --no-pager
```

When dashboard logs are involved, do not print raw lines unless they have been
redacted first. Dashboard URLs can include `?token=...`, and that token is a
secret. Prefer counting lines locally:

```powershell
$logs = ssh -i $key ubuntu@140.245.69.242 sudo journalctl -u polymarket-weather-dashboard --since=-30min --no-pager
$status = @($logs | Where-Object { $_ -match 'GET /api/status' })
$status.Count
```

If sample lines must be shown, replace `token=...` with `token=<redacted>`
before returning them to the user.

## Why This Matters

The user expects VPS diagnostics to be fast and concrete. A quote-related
failure wastes time and creates the false impression that the server check is
harder than it is. The space-free `--since=-30min` form keeps the command simple
and avoids cross-shell quoting problems.

## When To Apply

- Checking whether the dashboard is being hit too often.
- Counting recent bot logs without reading full multi-GB runtime files.
- Investigating CPU or disk usage caused by live dashboard refreshes.
- Any Windows PowerShell command that sends `journalctl` through `ssh`.

## Related

- [Verify public dashboard API access before sharing the URL](./verify-public-dashboard-api-before-sharing-url.md)
- [Verify VPS code, environment, and API health before trusting the dashboard](./verify-vps-code-env-and-api-health-2026-05-26.md)
