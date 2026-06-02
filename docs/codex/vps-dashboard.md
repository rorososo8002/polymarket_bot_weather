# VPS And Dashboard Rules

Read this file only for VPS, deployment, dashboard, systemd, public URL, or health-check tasks.

Start routine access with `docs/codex/known-good-commands.md`. Use the rules
below when the first command fails or when a task needs more detail.

## VPS Access

- The active Oracle VPS is `ubuntu@140.245.69.242`.
- The canonical local SSH key is named `ssh-key-2026-05-25.key` and lives in
  the Oracle SSH directory under `C:\Users\wpdla\Documents`.
- For Oracle VPS checks, resolve that private key path first and pass it to
  `ssh -i`.
- The adjacent `.pub` file is the public key and must not be passed to `ssh -i`.
- Never print, open, copy, or commit the key contents. Use it only as an identity file with `ssh -i` or `scp -i`.
- For remote pytest or verification, run from `/opt/polymarket-weather-bot` and use the service context when needed, for example `sudo -u polymarket /opt/polymarket-weather-bot/.venv/bin/python -m pytest -q`.
- After deploying or changing runtime dependencies, install the package in the VPS virtualenv before restarting services. Then verify with `systemctl status` and recent logs.

## Authoritative Runtime Source

- Before diagnosing live bot behavior, identify the authoritative runtime source.
- If a dashboard is reached through `127.0.0.1`, an SSH tunnel, or a public VPS URL, verify whether its data comes from local files or `/opt/polymarket-weather-bot/data` on the VPS.
- Do not treat dashboard liveness as bot correctness. Check `systemctl status`, `paper_runner_status.json`, recent `paper_decisions.csv`, recent `paper_trades.csv`, forecast cache freshness, and relevant env values.

## Dashboard Access

- The canonical dashboard access path is the public VPS URL `http://140.245.69.242:8787/`.
- Do not suggest `127.0.0.1:8787` unless a local SSH tunnel is explicitly requested and verified listening.
- Before saying the dashboard URL works, verify both `GET /` and `GET /api/status`.
- `GET /` should return 200. `GET /api/status` must either return 200 without a token or be clearly reported as token-protected with 403.
- Do not call the bare public dashboard URL usable when the API still requires `DASHBOARD_TOKEN`; the shell page can load while real dashboard data is blocked.
- The default secure mode is token-protected public access: use `http://140.245.69.242:8787/?token=<DASHBOARD_TOKEN>`, verify bare `/api/status` returns 403, and verify tokenized `/api/status?token=<DASHBOARD_TOKEN>` returns 200.
- Do not print the real `DASHBOARD_TOKEN` in logs, docs, commits, or final answers unless the user explicitly asks to display it. Reporting whether it exists, its length, and whether tokenized access works is acceptable.
- If the user wants public dashboard access without a token, explain the benefits, risks, exposure scope, verification, and rollback before requesting explicit approval to clear `DASHBOARD_TOKEN` in `/etc/polymarket-weather-bot/dashboard.env` and restart `polymarket-weather-dashboard`.
- After changing dashboard auth or host/port config, verify from the local machine with `curl.exe -i http://140.245.69.242:8787/` and `curl.exe -i http://140.245.69.242:8787/api/status`. Do not rely only on `systemctl status`.

## Resource Checks

- Report concrete VPS resource numbers instead of guessing from UI behavior.
- Use `uptime` and `nproc` for CPU load, `free -h` for memory, `df -h` and `du -h` for disk, `systemctl show` for service CPU and memory, `ps` for top processes, redacted `journalctl` for dashboard request counts, and bounded `stat` samples for data growth.
