---
title: Deploy payload copy must preserve app root access
date: 2026-06-06
last_updated: 2026-06-08
category: workflow-issues
module: deployment, oracle-vps
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "Deploying a local payload directory to `/opt/polymarket-weather-bot` with `cp -a`"
  - "Refreshing source, tests, docs, or deploy files on the Oracle VPS"
tags: [deployment, vps, permissions, systemd, paper-trading, pytest]
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

A later strategy deploy exposed two adjacent hazards:

- Copying over the app tree without removing stale `src`, `tests`, `docs`, and
  `deploy` directories left old remote tests behind, so remote pytest ran a
  different suite count than local pytest.
- Remote pytest failed before the product assertions mattered when
  `.pytest-tmp` did not exist, and one test wrote to the default
  `paper_event_portfolios.jsonl` in the app root instead of a `tmp_path`
  ledger.

## Guidance

When deploying a bounded local payload to `/opt/polymarket-weather-bot`, mirror
only the code and documentation trees that are safe to replace:

```bash
APP=/opt/polymarket-weather-bot
for path in src tests docs deploy; do
  target="$APP/$path"
  case "$target" in
    "$APP/src"|"$APP/tests"|"$APP/docs"|"$APP/deploy") sudo rm -rf "$target" ;;
    *) echo "refusing to remove $target"; exit 1 ;;
  esac
done
sudo cp -a "$PAYLOAD"/. "$APP"/
```

Do not delete or recreate `data`, `.venv`, `.git`, `runtime`, or root-level
paper ledgers during a normal code deploy. Those are runtime evidence or
environment state, not source payload.

After copying with archive-preserving flags, immediately restore the app root
access expected by the services:

```bash
sudo chown polymarket:polymarket /opt/polymarket-weather-bot
sudo chmod 755 /opt/polymarket-weather-bot
```

Then chown only the deployed code, tests, and docs as needed. Do not recursively
rewrite runtime ledgers just to fix a deploy payload mistake.

Run remote pytest from the app directory as the service user, and make sure the
workspace temp parent exists through the root `conftest.py` behavior:

```bash
cd /opt/polymarket-weather-bot
sudo -u polymarket .venv/bin/python -m pytest -q
```

Tests that instantiate `Settings` should point every writable runtime ledger at
`tmp_path`, including `portfolio_decisions_jsonl_path`, so a root-level
`paper_event_portfolios.jsonl` cannot affect the result.

## Why This Matters

The bot and dashboard systemd units both use:

```text
User=polymarket
WorkingDirectory=/opt/polymarket-weather-bot
```

The app root is the front door. If that door is owned by another user and set
to `700`, the service user cannot enter the directory, even when `.venv` itself
has executable permissions.

Remote verification is only trustworthy when it tests the same deployed tree as
local verification. Stale test files made the VPS run 463 tests while local ran
451. That mismatch is a flashing sign that the server tree is not a clean copy
of the intended payload.

Likewise, pytest fixture setup and runtime-ledger defaults can fail for
environment reasons that are unrelated to the strategy. Fixing those isolation
gaps prevents deployment from stalling on server-only filesystem leftovers.

## When to Apply

- A deploy script copies a local bundle into `/opt/polymarket-weather-bot`.
- Remote tests fail with `sudo: unable to execute .venv/bin/python: Permission denied`.
- `systemctl` services are configured with `User=polymarket`.
- Remote pytest runs a different test count than local pytest.
- Remote pytest fails before assertions due to `.pytest-tmp` setup or app-root
  runtime files.

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

Healthy deploy verification should show the local and remote pytest counts
match before services restart, for example:

```text
451 passed
service_state_bot=active
service_state_dashboard=active
```

## Related

- `docs/codex/known-good-commands.md`
- `docs/codex/ssh-powershell.md`
- [Run remote pytest from the app directory when using service users](./run-remote-pytest-from-app-cwd.md)
- [Use workspace temp dirs when pytest cannot scan Windows temp](./pytest-temp-permission-2026-05-26.md)
