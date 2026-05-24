from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_systemd_service_runs_live_paper_bot_from_venv():
    service = ROOT / "deploy" / "systemd" / "polymarket-weather-bot.service"

    text = service.read_text(encoding="utf-8")

    assert "ExecStart=/opt/polymarket-weather-bot/.venv/bin/live-paper-bot" in text
    assert "EnvironmentFile=/etc/polymarket-weather-bot/live-paper.env" in text
    assert "Restart=always" in text
    assert "ReadWritePaths=/opt/polymarket-weather-bot" in text


def test_vps_env_example_keeps_runtime_state_under_data_dir():
    env_example = ROOT / "deploy" / "systemd" / "live-paper.env.example"

    text = env_example.read_text(encoding="utf-8")

    assert "STATE_PATH=/opt/polymarket-weather-bot/data/paper_state.json" in text
    assert "RAW_SNAPSHOTS_PATH=/opt/polymarket-weather-bot/data/paper_raw_snapshots.jsonl" in text
    assert "POLYMARKET_PRIVATE_KEY" not in text


def test_vps_deployment_doc_warns_that_service_is_paper_only():
    doc = ROOT / "docs" / "VPS_LIVE_PAPER.md"

    text = doc.read_text(encoding="utf-8")

    assert "paper only" in text.lower()
    assert "systemctl enable --now polymarket-weather-bot" in text
    assert "journalctl -u polymarket-weather-bot -f" in text
