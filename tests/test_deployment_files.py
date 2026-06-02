from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_local_env_example_exposes_settlement_runner_defaults():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "SETTLEMENT_RUNNER_ENABLED=true" in text
    assert "SETTLEMENT_RUNNER_MAX_FRACTION=0.25" in text
    assert "SETTLEMENT_RUNNER_MIN_EV_MARGIN_USD=0.00" in text


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
    assert "BANKROLL_USD=100\n" in text
    assert "PORTFOLIO_DECISIONS_JSONL_PATH=/opt/polymarket-weather-bot/data/paper_event_portfolios.jsonl" in text
    assert "RAW_SNAPSHOTS_PATH=/opt/polymarket-weather-bot/data/paper_raw_snapshots.jsonl" in text
    assert "STATION_NOWCAST_ENABLED=true" in text
    assert "STATION_NOWCAST_CACHE_TTL_SECONDS=900" in text
    assert "STATION_NOWCAST_FRESHNESS_SECONDS=5400" in text
    assert "PORTFOLIO_DECISIONS_JSONL_PATH=/opt/polymarket-weather-bot/data/paper_event_portfolios.jsonl" in text
    assert "ORDERBOOK_STREAM_STALE_SECONDS=60" in text
    assert "RUNNER_HEALTH_STATUS_INTERVAL_SECONDS=5" in text
    assert "DISCOVERY_MAX_PAGES=8" in text
    assert "DISCOVERY_PAGE_SIZE=100" in text
    assert "MAX_EVENTS" not in text
    assert "MAX_MARKETS" not in text
    assert "ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.06" in text
    assert "WEATHER_TAKER_FEE_RATE=0.05" in text
    assert "SETTLEMENT_RUNNER_ENABLED=true" in text
    assert "SETTLEMENT_RUNNER_MAX_FRACTION=0.25" in text
    assert "SETTLEMENT_RUNNER_MIN_EV_MARGIN_USD=0.00" in text
    assert "BANKROLL_USD=100\n" in text
    assert "ENTRY_FRACTION=0.10" in text
    assert "MAX_SINGLE_MARKET_FRACTION=0.10" in text
    assert "MAX_TOTAL_EXPOSURE_FRACTION=0.90" in text
    assert "MAX_CITY_EXPOSURE_FRACTION=0.20" in text
    assert "MAX_EVENT_DATE_EXPOSURE_FRACTION=0.10" in text
    assert "LARGE_BANKROLL_EVENT_DATE_EXPOSURE_FRACTION=0.05" in text
    assert "EVENT_DATE_EXPOSURE_TRANSITION_USD=1000" in text
    assert "MAX_EVENT_PORTFOLIO_LEGS=2" in text
    assert "MIN_ORDER_USD=10.00" in text
    assert "ESTIMATED_FEE_PER_SHARE" not in text
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
    assert "FORECAST_CACHE_TTL_SECONDS=1800" in text
    assert "ORDERBOOK_STREAM_STALE_SECONDS=60" in text
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
