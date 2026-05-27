---
title: Run remote pytest from the app directory when using service users
date: 2026-05-28
category: workflow-issues
module: deployment verification
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - "Running pytest on the Oracle or VPS host as the polymarket service user"
  - "Using sudo -u from an SSH session whose current directory is another user's home"
tags: [oracle, pytest, sudo-user, deployment, verification]
---

# Run remote pytest from the app directory when using service users

## Context

During an Oracle dashboard deployment, the test suite printed passing/failing
progress but ended with `PermissionError: [Errno 13] Permission denied:
'/home/ubuntu'`. The command was launched from the SSH user's home directory
while the test process ran as the `polymarket` service user.

## Guidance

Set the remote working directory to the app path before switching to the service
user for verification:

```bash
cd /opt/polymarket-weather-bot && \
sudo -u polymarket /opt/polymarket-weather-bot/.venv/bin/python -m pytest -q
```

For one-line SSH checks, keep the `cd` inside the remote command:

```bash
ssh ubuntu@HOST "cd /opt/polymarket-weather-bot && sudo -u polymarket /opt/polymarket-weather-bot/.venv/bin/python -m pytest -q"
```

## Why This Matters

`pytest` restores the original start directory while shutting down. If that
directory is `/home/ubuntu`, the `polymarket` user may not have permission to
enter it, so the run can fail after doing useful work. Starting from
`/opt/polymarket-weather-bot` keeps setup, test execution, and shutdown inside a
directory owned by the service user.

## When to Apply

- After deploying code to Oracle or another VPS
- Before trusting server-side test results from `sudo -u polymarket`
- Any time a remote test run ends with a permission error pointing at another
  user's home directory

## Examples

Bad pattern:

```bash
sudo -u polymarket /opt/polymarket-weather-bot/.venv/bin/python -m pytest -q /opt/polymarket-weather-bot/tests
```

Good pattern:

```bash
cd /opt/polymarket-weather-bot && sudo -u polymarket .venv/bin/python -m pytest -q
```

## Related

- [Verify VPS code, environment, and API health before trusting the dashboard](./verify-vps-code-env-and-api-health-2026-05-26.md)
- [Use workspace temp dirs when pytest cannot scan Windows temp](./pytest-temp-permission-2026-05-26.md)
