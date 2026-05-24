from pathlib import Path
import tomllib


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


def test_dashboard_systemd_service_runs_dashboard_from_venv():
    service = ROOT / "deploy" / "systemd" / "polymarket-weather-dashboard.service"

    text = service.read_text(encoding="utf-8")

    assert "ExecStart=/opt/polymarket-weather-bot/.venv/bin/weather-dashboard" in text
    assert "EnvironmentFile=/etc/polymarket-weather-bot/dashboard.env" in text
    assert "Restart=always" in text
    assert "ReadWritePaths=/opt/polymarket-weather-bot" in text


def test_dashboard_env_requires_token_and_data_paths():
    env_example = ROOT / "deploy" / "systemd" / "dashboard.env.example"

    text = env_example.read_text(encoding="utf-8")

    assert "DASHBOARD_HOST=0.0.0.0" in text
    assert "DASHBOARD_PORT=8787" in text
    assert "DASHBOARD_TOKEN=change-me-long-random-token" in text
    assert "STATE_PATH=/opt/polymarket-weather-bot/data/paper_state.json" in text
    assert "POLYMARKET_PRIVATE_KEY" not in text


def test_vps_deployment_doc_warns_that_service_is_paper_only():
    doc = ROOT / "docs" / "VPS_LIVE_PAPER.md"

    text = doc.read_text(encoding="utf-8")

    assert "paper only" in text.lower()
    assert "systemctl enable --now polymarket-weather-bot" in text
    assert "systemctl enable --now polymarket-weather-dashboard" in text
    assert "does not\nuse Codex tokens" in text
    assert "journalctl -u polymarket-weather-bot -f" in text


def test_pytest_is_a_dev_dependency_not_runtime_dependency():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert not any(dep.startswith("pytest") for dep in data["project"]["dependencies"])
    assert any(dep.startswith("pytest") for dep in data["project"]["optional-dependencies"]["dev"])
