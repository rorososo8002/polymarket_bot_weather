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

## Oracle SSH Key Lookup

Use the private key only as an SSH identity file. Never open, print, copy, or
commit its contents. Do not use the adjacent `.pub` file.

Do not hand-type the Korean Oracle SSH directory path from logs. In Codex or
PowerShell output, that path can appear mojibaked or can be blocked by the
sandbox. Discover the directory object under `Documents`, then join the known
key filename:

```powershell
$sshDir = Get-ChildItem -LiteralPath "$env:USERPROFILE\Documents" -Directory |
  Where-Object { $_.Name -like '*ssh*' } |
  Select-Object -First 1
$key = Join-Path $sshDir.FullName 'ssh-key-2026-05-25.key'
$oracle = 'ubuntu@140.245.69.242'
Test-Path -LiteralPath $key -PathType Leaf
```

If sandboxing reports access denied for the key directory, request one
escalated `scp` or `ssh` command. Do not keep trying path spellings. The key
contents must not be printed.

## Oracle SSH Preflight

After running the key lookup block:

```powershell
ssh -i $key $oracle date
```

`Test-Path` checks whether the key file exists without reading its contents.
The `date` command is a harmless first SSH request. If this fails, inspect that
error before attempting longer remote commands. Run repeated SSH checks
serially when they use this same Windows identity file.

## Oracle Interactive Session

Use an interactive session for multi-step remote work. This avoids fragile
nested quoting between Windows PowerShell and the remote Linux shell.

After running the key lookup block:

```powershell
ssh -i $key $oracle
```

After login, run remote pytest from the application directory:

```bash
cd /opt/polymarket-weather-bot
sudo -u polymarket .venv/bin/python -m pytest -q
```

## Oracle Service Logs

For a bounded recent log check from local PowerShell, first run the key lookup
block, then:

```powershell
ssh -i $key $oracle sudo journalctl -u polymarket-weather-bot --since=-30min --no-pager
```

Use `--since=-30min` or `--since=-2h`. Avoid relative time expressions with
spaces because nested shell quoting becomes fragile.

## Oracle SCP Shape

Use this shape when a specific bounded file transfer is required. First run the
key lookup block, then:

```powershell
scp -i $key '.\path\to\file' "${oracle}:/tmp/"
```

Do not copy private keys, secrets, or large runtime files casually.

## Oracle Remote Script Shape

Use this shape for complex VPS changes that contain quotes, parentheses,
braces, semicolons, JSON, `sed`, `python -c`, or multi-step shell logic.

Do not keep retrying long inline commands such as
`ssh ... "set -e; sed ...; if ...; then ..."`. Windows PowerShell, `ssh`, and
the remote Linux shell each parse quotes differently, so those commands are
fragile. Write the remote logic into a small local `.sh` file, copy it to
`/tmp`, and run only `bash /tmp/name.sh` through SSH.

Example local script path:

```text
.deploy_tmp/update_forecast_env_1800.sh
```

Copy and run it after the key lookup block:

```powershell
scp -i $key .deploy_tmp\update_forecast_env_1800.sh "${oracle}:/tmp/update_forecast_env_1800.sh"
ssh -i $key $oracle bash /tmp/update_forecast_env_1800.sh
```

The script should be narrow and auditable: one job, explicit paths, no private
key contents, no dashboard token printing, and no unrelated runtime-file
deletion. Delete local `.deploy_tmp` artifacts after the operation so they do
not become accidental commit noise.

## Dashboard Reachability

The canonical dashboard host is the Oracle VPS:

```powershell
curl.exe -i http://140.245.69.242:8787/
curl.exe -i http://140.245.69.242:8787/api/status
```

The second request may correctly return `403` when token protection is enabled.
Do not print the real dashboard token in logs, docs, commits, or final answers.

## Paper Runtime Files

All paper runtime files live under `data/`, **not** the app root:

```
/opt/polymarket-weather-bot/data/paper_state.json
/opt/polymarket-weather-bot/data/paper_trades.csv
/opt/polymarket-weather-bot/data/paper_decisions.csv
/opt/polymarket-weather-bot/data/paper_raw_snapshots.jsonl
/opt/polymarket-weather-bot/data/forecast_rate_limit_state.json
/opt/polymarket-weather-bot/data/forecast_cache.json
/opt/polymarket-weather-bot/data/paper_event_portfolios.jsonl
```

To reset the paper account (stop bot first, then delete from `data/`, then restart):

```bash
sudo systemctl stop polymarket-weather-bot
cd /opt/polymarket-weather-bot/data
sudo rm -f paper_state.json paper_trades.csv paper_decisions.csv \
           paper_raw_snapshots.jsonl forecast_rate_limit_state.json \
           forecast_cache.json paper_event_portfolios.jsonl
sudo systemctl start polymarket-weather-bot
```

Archived (compressed) old files are stored under `data/archive/`.

## Disk and Log Rotation

Logrotate is configured to auto-compress files over 100 MB hourly:

```
/etc/logrotate.d/polymarket-weather-bot   ← config
/etc/cron.d/polymarket-logrotate          ← hourly cron
```

To check current disk usage:

```powershell
ssh -i $key $oracle df -h /opt/polymarket-weather-bot
ssh -i $key $oracle "ls -lh /opt/polymarket-weather-bot/data/"
```

## Related Detail

- `docs/codex/ssh-powershell.md`
- `docs/codex/vps-dashboard.md`
- `docs/solutions/workflow-issues/pytest-temp-permission-2026-05-26.md`
- `docs/solutions/workflow-issues/run-remote-pytest-from-app-cwd.md`
- `docs/solutions/workflow-issues/verify-ssh-key-file-before-tightening-permissions.md`
