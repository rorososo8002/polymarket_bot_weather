# Known-Good Commands

Use this file before inventing a new command variant. These commands are the
first path for routine local verification and Oracle VPS access. If a recorded
command fails, stop and inspect the concrete error before trying a different
shape.

## Local Windows Pytest

Run from the repository root:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

Run one focused file:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q tests/test_hardening.py
```

The root `conftest.py` automatically sends pytest temporary files to a
process-specific workspace folder such as `.pytest-tmp/pytest-12345`. This
avoids the Windows user-temp permission error without requiring manual `TMP`
or `TEMP` setup. A caller can still pass `--basetemp` explicitly when needed.

## Local Python Check

Use this when Python behavior looks suspicious or pytest prints no normal
summary:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' --version
```

## Oracle SSH Preflight

Use the private key only as an SSH identity file. Never open, print, copy, or
commit its contents. Do not use the adjacent `.pub` file.

```powershell
$key = 'C:\Users\wpdla\Documents\오라클ssh\ssh-key-2026-05-25.key'
$oracle = 'ubuntu@140.245.69.242'
Test-Path -LiteralPath $key
ssh -i $key $oracle date
```

`Test-Path` checks whether the key file exists without reading its contents.
The `date` command is a harmless first SSH request. If this fails, inspect that
error before attempting longer remote commands.

## Oracle Interactive Session

Use an interactive session for multi-step remote work. This avoids fragile
nested quoting between Windows PowerShell and the remote Linux shell.

```powershell
$key = 'C:\Users\wpdla\Documents\오라클ssh\ssh-key-2026-05-25.key'
$oracle = 'ubuntu@140.245.69.242'
ssh -i $key $oracle
```

After login, run remote pytest from the application directory:

```bash
cd /opt/polymarket-weather-bot
sudo -u polymarket .venv/bin/python -m pytest -q
```

## Oracle Service Logs

For a bounded recent log check from local PowerShell:

```powershell
$key = 'C:\Users\wpdla\Documents\오라클ssh\ssh-key-2026-05-25.key'
$oracle = 'ubuntu@140.245.69.242'
ssh -i $key $oracle sudo journalctl -u polymarket-weather-bot --since=-30min --no-pager
```

Use `--since=-30min` or `--since=-2h`. Avoid relative time expressions with
spaces because nested shell quoting becomes fragile.

## Oracle SCP Shape

Use this shape when a specific bounded file transfer is required:

```powershell
$key = 'C:\Users\wpdla\Documents\오라클ssh\ssh-key-2026-05-25.key'
$oracle = 'ubuntu@140.245.69.242'
scp -i $key '.\path\to\file' "${oracle}:/tmp/"
```

Do not copy private keys, secrets, or large runtime files casually.

## Dashboard Reachability

The canonical dashboard host is the Oracle VPS:

```powershell
curl.exe -i http://140.245.69.242:8787/
curl.exe -i http://140.245.69.242:8787/api/status
```

The second request may correctly return `403` when token protection is enabled.
Do not print the real dashboard token in logs, docs, commits, or final answers.

## Related Detail

- `docs/codex/ssh-powershell.md`
- `docs/codex/vps-dashboard.md`
- `docs/solutions/workflow-issues/pytest-temp-permission-2026-05-26.md`
- `docs/solutions/workflow-issues/run-remote-pytest-from-app-cwd.md`
