from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


PRECIPITATION_ENV_SETTINGS = (
    "ENABLE_PRECIPITATION_MARKETS",
    "PRECIP_MIN_NET_EDGE",
    "PRECIP_ENTRY_FRACTION",
    "PRECIP_MIN_CONFIDENCE",
    "PRECIP_MAX_CONFIDENCE",
)


def _assert_precipitation_settings_absent(text: str) -> None:
    for setting in PRECIPITATION_ENV_SETTINGS:
        assert setting not in text


def test_local_env_example_exposes_settlement_runner_defaults():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "SETTLEMENT_RUNNER_ENABLED=true" in text
    assert "SETTLEMENT_RUNNER_MAX_FRACTION=1.00" in text
    assert "SETTLEMENT_RUNNER_MIN_EV_MARGIN_USD=0.00" in text
    assert "ORDERBOOK_REST_SNAPSHOT_ENABLED=true" in text
    assert "ORDERBOOK_REST_SNAPSHOT_INTERVAL_SECONDS=60" in text
    assert "FORECAST_" not in text
    assert "SKIP_DIAGNOSTICS_ENABLED=true" in text
    assert "SKIP_DIAGNOSTICS_JSONL_PATH=paper_skip_diagnostics.jsonl" in text
    assert "SKIP_DIAGNOSTICS_MAX_BYTES=10485760" in text
    assert "SKIP_DIAGNOSTICS_ARCHIVE_MAX_BYTES=20971520" in text
    assert "STATION_NOWCAST_CACHE_TTL_SECONDS=60" in text
    assert "STATION_NOWCAST_REQUEST_LOG_PATH=station_nowcast_request_log.jsonl" in text
    assert "HKO_ROLLOVER_STATE_PATH=hko_rollover_state.json" in text
    assert "BANKROLL_USD=1000\n" in text
    assert "SIZE_MODE=kelly" in text
    assert "FRACTIONAL_KELLY=0.25" in text
    assert "MAX_TOTAL_EXPOSURE_FRACTION=0.90" in text
    assert "DAILY_REALIZED_LOSS_LIMIT_FRACTION=0.50" in text
    assert "DAILY_UNREALIZED_LOSS_LIMIT_FRACTION=0.50" in text
    assert "LARGE_LOSS_THRESHOLD_FRACTION=0.50" in text


def test_local_env_example_does_not_expose_removed_precipitation_settings():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    _assert_precipitation_settings_absent(text)


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
    assert "BANKROLL_USD=1000\n" in text
    assert "PORTFOLIO_DECISIONS_JSONL_PATH=/opt/polymarket-weather-bot/data/paper_event_portfolios.jsonl" in text
    assert "SKIP_DIAGNOSTICS_ENABLED=true" in text
    assert "SKIP_DIAGNOSTICS_JSONL_PATH=/opt/polymarket-weather-bot/data/paper_skip_diagnostics.jsonl" in text
    assert "SKIP_DIAGNOSTICS_MAX_BYTES=10485760" in text
    assert "SKIP_DIAGNOSTICS_ARCHIVE_MAX_BYTES=20971520" in text
    assert "RAW_SNAPSHOTS_PATH=/opt/polymarket-weather-bot/data/paper_raw_snapshots.jsonl" in text
    assert "FORECAST_" not in text
    assert (
        "STATION_NOWCAST_REQUEST_LOG_PATH=/opt/polymarket-weather-bot/data/station_nowcast_request_log.jsonl"
        in text
    )
    assert "HKO_ROLLOVER_STATE_PATH=/opt/polymarket-weather-bot/data/hko_rollover_state.json" in text
    assert "STATION_NOWCAST_ENABLED=true" in text
    assert "STATION_NOWCAST_CACHE_TTL_SECONDS=60" in text
    assert "STATION_NOWCAST_FRESHNESS_SECONDS=5400" in text
    assert "PORTFOLIO_DECISIONS_JSONL_PATH=/opt/polymarket-weather-bot/data/paper_event_portfolios.jsonl" in text
    assert "ORDERBOOK_STREAM_STALE_SECONDS=60" in text
    assert "RUNNER_HEALTH_STATUS_INTERVAL_SECONDS=5" in text
    assert "STREAM_CYCLE_INTERVAL_SECONDS=2400" in text
    assert ("FORECAST_" + "REFRESH_INTERVAL_SECONDS") not in text
    assert "ORDERBOOK_REST_SNAPSHOT_ENABLED=true" in text
    assert "ORDERBOOK_REST_SNAPSHOT_INTERVAL_SECONDS=60" in text
    assert "DISCOVERY_MAX_PAGES=8" in text
    assert "DISCOVERY_PAGE_SIZE=100" in text
    assert "MAX_EVENTS" not in text
    assert "MAX_MARKETS" not in text
    assert "MIN_NET_EDGE=0.08" in text
    assert "ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.04" in text
    assert "WEATHER_TAKER_FEE_RATE=0.05" in text
    assert "SETTLEMENT_RUNNER_ENABLED=true" in text
    assert "SETTLEMENT_RUNNER_MAX_FRACTION=1.00" in text
    assert "SETTLEMENT_RUNNER_MIN_EV_MARGIN_USD=0.00" in text
    assert "BANKROLL_USD=1000\n" in text
    assert "SIZE_MODE=kelly" in text
    assert "FRACTIONAL_KELLY=0.25" in text
    assert "ENTRY_FRACTION=0.20" in text
    assert "MAX_SINGLE_MARKET_FRACTION=0.50" in text
    assert "MAX_TOTAL_EXPOSURE_FRACTION=0.90" in text
    assert "MAX_CITY_EXPOSURE_FRACTION=0.20" in text
    assert "MAX_EVENT_DATE_EXPOSURE_FRACTION=0.10" in text
    assert "LARGE_BANKROLL_EVENT_DATE_EXPOSURE_FRACTION=0.05" in text
    assert "OFFICIAL_NOWCAST_LOCK_ENABLED=true" in text
    assert "OFFICIAL_NOWCAST_ENTRY_ONLY=false" in text
    assert "STRATEGY_MODE=hybrid_observation_edge" in text
    assert "INTRADAY_OBSERVATION_EDGE_ENABLED=true" in text
    assert "INTRADAY_MIN_SIDE_PROBABILITY=0.90" in text
    assert "INTRADAY_STRONG_SIDE_PROBABILITY=0.97" in text
    assert "INTRADAY_ABNORMAL_MIN_NET_EDGE=0.20" in text
    assert "INTRADAY_HIGH_CONFIRM_LOCAL_HOUR" not in text
    assert "INTRADAY_LOW_CONFIRM_LOCAL_HOUR" not in text
    assert "INTRADAY_US_HIGH_DISABLED_BEFORE_LOCAL_HOUR" not in text
    assert "OFFICIAL_NOWCAST_LOCK_BASE_ENTRY_FRACTION=0.20" in text
    assert "OFFICIAL_NOWCAST_LOCK_STRONG_ENTRY_FRACTION=0.50" in text
    assert "OFFICIAL_NOWCAST_LOCK_NEAR_CLOSE_HOURS=3.0" in text
    assert "OFFICIAL_NOWCAST_LOCK_YES_BASE_BUFFER_C=0.50" in text
    assert "OFFICIAL_NOWCAST_LOCK_YES_STRONG_BUFFER_C=0.75" in text
    assert "EVENT_DATE_EXPOSURE_TRANSITION_USD=1000" in text
    assert "MAX_EVENT_PORTFOLIO_LEGS=2" in text
    assert "DAILY_REALIZED_LOSS_LIMIT_FRACTION=0.50" in text
    assert "DAILY_UNREALIZED_LOSS_LIMIT_FRACTION=0.50" in text
    assert "LARGE_LOSS_THRESHOLD_FRACTION=0.50" in text
    assert "MIN_ORDER_USD=10.00" in text
    assert "ESTIMATED_FEE_PER_SHARE" not in text
    assert "POLYMARKET_PRIVATE_KEY" not in text


def test_vps_env_example_does_not_expose_removed_precipitation_settings():
    env_example = ROOT / "deploy" / "systemd" / "live-paper.env.example"

    text = env_example.read_text(encoding="utf-8")

    _assert_precipitation_settings_absent(text)


def test_runtime_logrotate_rotates_high_volume_diagnostics_only():
    logrotate = ROOT / "deploy" / "logrotate" / "polymarket-weather-bot-runtime"

    text = logrotate.read_text(encoding="utf-8")

    assert "/opt/polymarket-weather-bot/data/paper_raw_snapshots.jsonl" in text
    assert "/opt/polymarket-weather-bot/data/paper_skip_diagnostics.jsonl" in text
    assert "/opt/polymarket-weather-bot/data/forecast_request_log.jsonl" not in text
    assert "/opt/polymarket-weather-bot/data/station_nowcast_request_log.jsonl" in text
    assert "/opt/polymarket-weather-bot/data/paper_event_portfolios.jsonl" in text
    assert "size 100M" in text
    assert "size 10M" in text
    assert text.count("size 10M") == 3
    assert text.count("rotate 5") == 4
    assert "maxage 7" in text
    assert "compresscmd /usr/bin/zstd" in text
    assert "/opt/polymarket-weather-bot/data/paper_state.json" not in text
    assert "/opt/polymarket-weather-bot/data/paper_trades.csv" not in text
    assert "/opt/polymarket-weather-bot/data/paper_decisions.csv" not in text


def test_runtime_cleanup_cron_prunes_diagnostic_archive_only():
    cron = ROOT / "deploy" / "cron" / "polymarket-runtime-cleanup"

    text = cron.read_text(encoding="utf-8")

    assert "weather_bot.runtime_cleanup" in text
    assert "--data-dir /opt/polymarket-weather-bot/data" in text
    assert "--max-archive-bytes 20971520" in text
    assert "paper_state.json" not in text
    assert "paper_trades.csv" not in text
    assert "paper_decisions.csv" not in text


def test_runtime_logrotate_cron_runs_hourly_with_private_state_file():
    cron = ROOT / "deploy" / "cron" / "polymarket-logrotate"

    text = cron.read_text(encoding="utf-8")

    assert "7 * * * * root" in text
    assert "/usr/sbin/logrotate" in text
    assert "/var/lib/logrotate/polymarket-weather-bot.status" in text
    assert "/etc/logrotate.d/polymarket-weather-bot" in text


def test_pyproject_exposes_runtime_cleanup_console_script():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["paper-runtime-cleanup"] == "weather_bot.runtime_cleanup:main"


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
    assert "DASHBOARD_TOKEN=\n" in text
    assert "refuses to start on public hosts" in text
    assert "long random token" in text
    assert "STATE_PATH=/opt/polymarket-weather-bot/data/paper_state.json" in text
    assert "BANKROLL_USD=1000\n" in text
    assert "FORECAST_" not in text
    assert "ORDERBOOK_STREAM_STALE_SECONDS=60" in text
    assert "POLYMARKET_PRIVATE_KEY" not in text


def test_vps_deployment_doc_warns_that_service_is_paper_only():
    doc = ROOT / "docs" / "codex" / "vps-dashboard.md"
    commands = ROOT / "docs" / "codex" / "known-good-commands.md"

    text = doc.read_text(encoding="utf-8")
    command_text = commands.read_text(encoding="utf-8")

    assert "paper-only" in text.lower()
    assert "polymarket-weather-dashboard" in text
    assert "X-Dashboard-Token" in text
    assert "journalctl -u polymarket-weather-bot" in command_text


def test_pytest_is_a_dev_dependency_not_runtime_dependency():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert not any(dep.startswith("pytest") for dep in data["project"]["dependencies"])
    assert any(dep.startswith("pytest") for dep in data["project"]["optional-dependencies"]["dev"])
