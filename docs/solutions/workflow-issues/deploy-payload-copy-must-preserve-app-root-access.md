---
title: Deploy payload copy must preserve app root access
date: 2026-06-06
category: workflow-issues
module: deployment, oracle-vps
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "Deploying a local payload directory to `/opt/polymarket-weather-bot` with `cp -a`"
tags: [deployment, vps, permissions, systemd, paper-trading]
---

# Deploy Payload Copy Must Preserve App Root Access

## Context

During the paper-only exit-policy deploy, the payload directory had restrictive
`700` permissions. The remote script copied it with:

```bash
sudo cp -a "$PAYLOAD"/. "$APP"/
```

Because `cp -a` preserves metadata, the app root
`/opt/polymarket-weather-bot` also became `700`. The systemd services run as
`polymarket`, so `sudo -u polymarket .venv/bin/python ...` failed with
`Permission denied`.

## Guidance

After copying a payload with archive-preserving flags, immediately restore the
app root access expected by the services:

```bash
sudo chown polymarket:polymarket /opt/polymarket-weather-bot
sudo chmod 755 /opt/polymarket-weather-bot
```

Then chown only the deployed code, tests, and docs as needed. Do not recursively
rewrite runtime ledgers just to fix a deploy payload mistake.

## Why This Matters

The bot and dashboard systemd units both use:

```text
User=polymarket
WorkingDirectory=/opt/polymarket-weather-bot
```

The app root is the front door. If that door is owned by another user and set
to `700`, the service user cannot enter the directory, even when `.venv` itself
has executable permissions.

## When to Apply

- A deploy script copies a local bundle into `/opt/polymarket-weather-bot`.
- Remote tests fail with `sudo: unable to execute .venv/bin/python: Permission denied`.
- `systemctl` services are configured with `User=polymarket`.

## Examples

Before rerunning tests or restarting services, verify:

```bash
stat -c "%A %U:%G %n" /opt/polymarket-weather-bot
sudo -u polymarket test -x /opt/polymarket-weather-bot/.venv/bin/python
```

Expected app-root posture:

```text
drwxr-xr-x polymarket:polymarket /opt/polymarket-weather-bot
```

## Related

- `docs/codex/known-good-commands.md`
- `docs/codex/ssh-powershell.md`
