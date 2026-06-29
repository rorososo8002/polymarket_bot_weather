# Known-Good Commands

Use these verified shapes before inventing a new pytest, SSH, or VPS command.
Run git mutations and pytest processes serially.

Token rule: read only the section that matches the operation. Do not read this
whole file just to run one known command.

## Local Tests

Full suite from the repository root:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

Focused example:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q tests/test_residual_probability.py
```

The root `conftest.py` owns `.pytest-tmp` and removes it after pytest. Do not run
parallel pytest processes against the same temporary directory.

Paper-only startup check:

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\wpdla\Python312\python.exe' -m weather_bot --dry-start
```

## Oracle Connection

Locate the key without printing it:

```powershell
$sshDir = Get-ChildItem -LiteralPath "$env:USERPROFILE\Documents" -Directory |
  Where-Object { $_.Name -like '*ssh*' } |
  Select-Object -First 1
$key = Join-Path $sshDir.FullName 'ssh-key-2026-05-25.key'
$oracle = 'ubuntu@140.245.69.242'
Test-Path -LiteralPath $key -PathType Leaf
```

Preflight:

```powershell
ssh -i $key $oracle date
```

If the sandbox denies the key or network, request one escalated `ssh`/`scp`
operation. Never print the private key.

## Remote Tests And Services

Interactive full test:

```powershell
ssh -i $key $oracle
```

```bash
cd /opt/polymarket-weather-bot
sudo -u polymarket .venv/bin/python -m pytest -q
```

Service and recent-log checks:

```powershell
ssh -i $key $oracle "systemctl is-active polymarket-weather-bot polymarket-weather-dashboard"
ssh -i $key $oracle sudo journalctl -u polymarket-weather-bot --since=-30min --no-pager
```

For multi-step deployment or inspection, write one short local `.sh` file under
`.deploy_tmp`, copy it, and run it. This avoids three layers of PowerShell/SSH/
Bash quoting:

```powershell
scp -i $key .deploy_tmp\task.sh "${oracle}:/tmp/task.sh"
ssh -i $key $oracle sudo bash /tmp/task.sh
```

The script must use `set -eu`, back up replaced files, restore on failed remote
pytest, restart both services only after success, and delete its `/tmp` copy.

## Dashboard

Public address: `http://140.245.69.242:8787`. When authentication is enabled,
send the configured token as `X-Dashboard-Token`; do not expose it in logs.

```powershell
curl.exe -I --max-time 15 http://140.245.69.242:8787/
```

## Runtime And Disk

Application: `/opt/polymarket-weather-bot`

Account/ledger files under `data/`:

- `paper_state.json`: current paper account book.
- `paper_trades.csv`: executed paper receipts.
- `paper_decisions.csv`: strategy evidence.

Never delete or truncate those three as routine cleanup. Archive a complete
experiment together before an explicitly approved reset. Diagnostics and raw
snapshots are bounded separately; see `docs/codex/runtime-data.md`.

Run the safe diagnostic-archive cleanup:

```powershell
ssh -i $key $oracle "cd /opt/polymarket-weather-bot && sudo -u polymarket PYTHONPATH=/opt/polymarket-weather-bot/src .venv/bin/python -m weather_bot.runtime_cleanup --data-dir data --max-archive-bytes 20971520"
```

Check capacity and file sizes:

```powershell
ssh -i $key $oracle df -h /opt/polymarket-weather-bot
ssh -i $key $oracle "ls -lh /opt/polymarket-weather-bot/data/"
```

## More Detail

- `docs/codex/ssh-powershell.md`: quoting and safe file transfer.
- `docs/codex/vps-dashboard.md`: systemd and dashboard deployment.
- `docs/codex/runtime-data.md`: ledgers, diagnostics, rotation, and bounded reads.
