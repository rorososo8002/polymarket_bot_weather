# VPS Live Paper Deployment

This deployment is **paper only**. It reads live Polymarket and Open-Meteo data,
simulates entries and exits locally, and writes CSV/JSONL state files. It does
not connect a wallet, sign orders, submit orders, or redeem resolved markets on
chain.

## Recommended Layout

```text
/opt/polymarket-weather-bot
  .venv/
  data/
  src/
  tests/
  pyproject.toml

/etc/polymarket-weather-bot/live-paper.env
/etc/systemd/system/polymarket-weather-bot.service
```

## 1. Create Service User

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin polymarket
sudo mkdir -p /opt/polymarket-weather-bot
sudo chown -R polymarket:polymarket /opt/polymarket-weather-bot
```

## 2. Upload Project

Copy this project to `/opt/polymarket-weather-bot`. Example from your local
machine:

```bash
scp -r polymarket_weather_bot_livepaper_v3/* root@YOUR_VPS_IP:/opt/polymarket-weather-bot/
ssh root@YOUR_VPS_IP "chown -R polymarket:polymarket /opt/polymarket-weather-bot"
```

If you use git later, clone or pull the repository into the same directory.

## 3. Install Python Environment

On the VPS:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip
sudo -u polymarket python3 -m venv /opt/polymarket-weather-bot/.venv
sudo -u polymarket /opt/polymarket-weather-bot/.venv/bin/pip install -e /opt/polymarket-weather-bot
sudo -u polymarket mkdir -p /opt/polymarket-weather-bot/data
```

## 4. Install Environment File

```bash
sudo mkdir -p /etc/polymarket-weather-bot
sudo cp /opt/polymarket-weather-bot/deploy/systemd/live-paper.env.example /etc/polymarket-weather-bot/live-paper.env
sudo chown root:root /etc/polymarket-weather-bot/live-paper.env
sudo chmod 0644 /etc/polymarket-weather-bot/live-paper.env
```

Edit paper bankroll and scan settings if needed:

```bash
sudo nano /etc/polymarket-weather-bot/live-paper.env
```

Do not add wallet private keys to this file.

## 5. Install systemd Service

```bash
sudo cp /opt/polymarket-weather-bot/deploy/systemd/polymarket-weather-bot.service /etc/systemd/system/polymarket-weather-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now polymarket-weather-bot
```

## 6. Monitor

```bash
systemctl status polymarket-weather-bot
journalctl -u polymarket-weather-bot -f
```

Paper output files:

```bash
sudo -u polymarket ls -lh /opt/polymarket-weather-bot/data
sudo -u polymarket tail -n 20 /opt/polymarket-weather-bot/data/paper_trades.csv
sudo -u polymarket tail -n 20 /opt/polymarket-weather-bot/data/paper_decisions.csv
sudo -u polymarket tail -n 5 /opt/polymarket-weather-bot/data/paper_raw_snapshots.jsonl
```

## 7. Stop, Restart, Update

```bash
sudo systemctl stop polymarket-weather-bot
sudo systemctl restart polymarket-weather-bot
sudo systemctl disable --now polymarket-weather-bot
```

After updating code:

```bash
sudo systemctl stop polymarket-weather-bot
sudo -u polymarket /opt/polymarket-weather-bot/.venv/bin/pip install -e /opt/polymarket-weather-bot
sudo -u polymarket /opt/polymarket-weather-bot/.venv/bin/python -m pytest -q /opt/polymarket-weather-bot
sudo systemctl start polymarket-weather-bot
```

## Readiness Checklist

Before letting it run unattended:

- `systemctl status polymarket-weather-bot` shows `active (running)`.
- `journalctl -u polymarket-weather-bot -n 100` has no repeated API errors.
- `/opt/polymarket-weather-bot/data/paper_decisions.csv` is growing.
- `/opt/polymarket-weather-bot/data/paper_raw_snapshots.jsonl` is growing.
- No private key or wallet secret exists anywhere in the service environment.
