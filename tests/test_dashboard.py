from __future__ import annotations

import csv
import json
import threading
import urllib.error
import urllib.request
from http import HTTPStatus

import pytest

from weather_bot import dashboard as dashboard_module
from weather_bot.config import Settings
from weather_bot.dashboard import HTML, _read_csv, build_dashboard_payload


DASHBOARD_TEST_TOKEN = "a8f4c2d9e1b7a6c5f0d3e9b1a2c4d6f8"


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def _start_test_dashboard(monkeypatch, dashboard_host: str):
    monkeypatch.setattr(
        dashboard_module,
        "build_dashboard_payload",
        lambda _settings, auth_required=False: {
            "ok": True,
            "security": {"auth_required": auth_required},
        },
    )
    server = dashboard_module.ThreadingHTTPServer(("127.0.0.1", 0), dashboard_module.DashboardHandler)
    server.settings = Settings(dashboard_host=dashboard_host, dashboard_token=DASHBOARD_TEST_TOKEN)
    server.dashboard_token = DASHBOARD_TEST_TOKEN
    server.dashboard_query_token_allowed = not dashboard_module._is_public_dashboard_host(dashboard_host)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


def _stop_test_dashboard(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _get_status(url: str, headers: dict[str, str] | None = None) -> int:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_read_csv_uses_tail_without_loading_entire_file(tmp_path):
    path = tmp_path / "large.csv"
    rows = [{"ts": f"2026-05-24T10:{idx:02d}:00+00:00", "side": "SKIP", "note": str(idx)} for idx in range(120)]
    write_csv(path, rows)

    tail = _read_csv(path, limit=3)

    assert [row["note"] for row in tail] == ["117", "118", "119"]


def test_dashboard_payload_uses_official_station_monitoring_instead_of_forecast(tmp_path):
    state_path = tmp_path / "state.json"
    decisions_path = tmp_path / "decisions.csv"
    nowcast_log = tmp_path / "station_nowcast_request_log.jsonl"
    skip_log = tmp_path / "paper_skip_diagnostics.jsonl"
    state_path.write_text(json.dumps({"cash_usd": 100.0, "positions": []}), encoding="utf-8")
    write_csv(
        decisions_path,
        [
            {
                "ts": "2099-06-19T00:20:00+00:00",
                "market_id": "m-seoul-23",
                "question": "Will the highest temperature in Seoul be 23°C on June 19?",
                "side": "NO",
                "city": "seoul",
                "station_id": "RKSI",
                "signal_source": "ensemble+official-nowcast-lock",
                "reason": "official station lock",
                "note": (
                    "observed_high_c=24.0; observed_at=2099-06-19T00:19:00+00:00; "
                    "nowcast_source=aviationweather-metar; official_nowcast_lock=strong_no; "
                    "next_displayed_integer_c=24.0; entry_size_fraction_override=0.50"
                ),
            }
        ],
    )
    nowcast_log.write_text(
        json.dumps(
            {
                "city": "seoul",
                "station_id": "RKSI",
                "station_name": "Incheon Intl Airport Station",
                "status": "success",
                "requested_at": "2099-06-19T00:19:00+00:00",
                "observed_high_c": 24.0,
                "observed_low_c": 20.1,
                "source": "aviationweather-metar",
            }
        ),
        encoding="utf-8",
    )
    skip_log.write_text(
        json.dumps(
            {
                "ts": "2099-06-19T00:21:00+00:00",
                "market_id": "m-seoul-23",
                "city": "seoul",
                "side": "SKIP",
                "reason_code": "SKIP_WIDE_SPREAD",
                "reason": "spread too wide",
                "station_id": "RKSI",
                "signal_source": "ensemble+official-nowcast-lock",
                "note": "observed_high_c=24.0; official_nowcast_lock=strong_no",
            }
        ),
        encoding="utf-8",
    )

    payload = build_dashboard_payload(
        Settings(
            bankroll_usd=100.0,
            state_path=str(state_path),
            decisions_csv_path=str(decisions_path),
            station_nowcast_request_log_path=str(nowcast_log),
            skip_diagnostics_jsonl_path=str(skip_log),
        )
    )

    scanner = payload["scanner"]
    assert "forecast" not in payload["health"]
    assert "latest_forecast_at" not in scanner
    assert "per_city_forecast" not in scanner
    assert payload["health"]["station"]["status"] == "HEALTHY"
    assert scanner["latest_station_at"] == "2099-06-19T00:19:00+00:00"
    assert scanner["station_observations"][0]["station_id"] == "RKSI"
    assert scanner["station_signals"][0]["lock_strength"] == "strong_no"
    assert scanner["station_signals"][0]["observed_high_c"] == pytest.approx(24.0)
    assert scanner["station_signals"][0]["settlement_boundary_c"] == pytest.approx(24.0)
    assert scanner["station_signals"][0]["allocation_fraction"] == pytest.approx(0.50)
    assert scanner["recent_skips"][0]["reason_code"] == "SKIP_WIDE_SPREAD"


def test_dashboard_payload_explains_official_nowcast_entry_only_skips_in_korean(tmp_path):
    state_path = tmp_path / "state.json"
    skip_log = tmp_path / "paper_skip_diagnostics.jsonl"
    state_path.write_text(json.dumps({"cash_usd": 100.0, "positions": []}), encoding="utf-8")
    skip_log.write_text(
        json.dumps(
            {
                "ts": "2099-06-19T04:51:00+00:00",
                "market_id": "m-chengdu-39",
                "question": "Will the highest temperature in Chengdu be 39°C or higher on June 19?",
                "side": "SKIP",
                "city": "chengdu",
                "station_id": "ZUUU",
                "reason_code": "SKIP",
                "reason": (
                    "official-station-entry-only: non-lock entry blocked; "
                    "waiting for same-station settlement-lock evidence [temperature]"
                ),
                "note": "observed_high_c=2.0; observed_at=2099-06-19T04:51:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    payload = build_dashboard_payload(
        Settings(
            state_path=str(state_path),
            skip_diagnostics_jsonl_path=str(skip_log),
        )
    )

    skip = payload["scanner"]["recent_skips"][0]
    assert skip["reason_ko"].startswith("공식 관측소 잠금 신호 없이는 진입하지 않도록 막았습니다.")
    assert "정산에 쓰이는 같은 공식 관측소" in skip["reason_ko"]
    assert "non-lock entry blocked" not in skip["reason_ko"]
    assert skip["station_name"] == "Chengdu Shuangliu International Airport Station"


def test_dashboard_payload_lists_supported_official_station_registry_with_provider_floors(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"cash_usd": 100.0, "positions": []}), encoding="utf-8")

    payload = build_dashboard_payload(Settings(state_path=str(state_path)))

    registry = payload["scanner"]["station_registry"]
    assert len(registry) == 49
    seoul = next(row for row in registry if row["city"] == "seoul")
    assert seoul["station_id"] == "RKSI"
    assert seoul["station_name"] == "Incheon Intl Airport Station"
    assert seoul["nowcast_min_real_request_interval_seconds"] == 60
    assert seoul["nowcast_call_rule_ko"].startswith("AWC METAR 공식 API는")
    assert seoul["display_station_references"][0]["station_id"] == "108"
    assert "표시용 참고" in seoul["display_station_references"][0]["usage_ko"]
    hong_kong = next(row for row in registry if row["city"] == "hong kong")
    assert hong_kong["nowcast_min_real_request_interval_seconds"] == 600
    assert "10분" in hong_kong["nowcast_call_rule_ko"]


def test_dashboard_html_is_official_station_first():
    assert "최근 예보 갱신" not in HTML
    assert "예보 상태 (Open-Meteo)" not in HTML
    assert "도시별 예보 호출 기록" not in HTML
    assert "cityForecastCard" not in HTML
    assert "forecastHealth.cache_ttl_seconds" not in HTML
    assert "관측소 감시" in HTML
    assert "정산 경계" in HTML
    assert "진입 비중" in HTML
    assert "최근 스킵" in HTML
    assert "공식 관측소 목록" in HTML
    assert "skip.reason_ko" in HTML
    assert "stationRegistryCard" in HTML
    assert 'id="r-city-entries"' in HTML
    assert "function cityEntryCard(row)" in HTML


def test_dashboard_template_displays_probability_calibration_and_executable_size():
    assert "function probabilityAuditLine(row)" in HTML
    assert "원확률" in HTML
    assert "보정확률" in HTML
    assert "신뢰등급" in HTML
    assert "표본" in HTML
    assert "요청금액" in HTML
    assert "체결가능" in HTML
    assert "${probabilityAuditLine(p)}" in HTML
    assert "${probabilityAuditLine(signal)}" in HTML


def test_dashboard_template_displays_station_clock_formation_rollover_and_clob_state():
    assert "관측소 현지 날짜" in HTML
    assert "관측소 현지 시각" in HTML
    assert "전략 관찰" in HTML
    assert "최종 최고 형성" in HTML
    assert "최종 최저 형성" in HTML
    assert "추가 움직임 확률" in HTML
    assert "자정 초기화" in HTML
    assert "자료 차단 이유" in HTML
    assert "CLOB 주문" in HTML
    assert "전략 허용 근거" in HTML


def test_dashboard_station_evidence_preserves_formation_and_rollover_fields():
    evidence = dashboard_module._official_station_evidence(
        {
            "note": (
                "station_local_date=2026-06-22; station_local_time=03:30; "
                "formation_monitoring_status=started; monitoring_start_local_minute=780; "
                "first_final_high_local_minute_q25=750; first_final_high_local_minute_median=810; "
                "first_final_high_local_minute_q75=870; remaining_movement_probability=0.35; "
                "midnight_reset_status=verified; data_block_reason=; "
                "daily_extremes_complete=true; daily_extremes_status=complete; "
                "clob_accepting_orders=true; clob_enable_order_book=true; "
                "strategy_allowed_reason=residual probability passed"
            )
        }
    )

    assert evidence["station_local_date"] == "2026-06-22"
    assert evidence["station_local_time"] == "03:30"
    assert evidence["formation_monitoring_status"] == "started"
    assert evidence["remaining_movement_probability"] == "0.35"
    assert evidence["midnight_reset_status"] == "verified"
    assert evidence["daily_extremes_complete"] == "true"
    assert evidence["daily_extremes_status"] == "complete"
    assert evidence["clob_accepting_orders"] == "true"
    assert evidence["strategy_allowed_reason"] == "residual probability passed"


def test_dashboard_nowcast_status_keeps_last_success_when_latest_call_fails(tmp_path):
    state_path = tmp_path / "state.json"
    nowcast_log = tmp_path / "station_nowcast_request_log.jsonl"
    state_path.write_text(json.dumps({"cash_usd": 100.0, "positions": []}), encoding="utf-8")
    nowcast_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "city": "hong kong",
                        "status": "success",
                        "requested_at": "2026-06-16T00:10:00+00:00",
                        "status_code": 200,
                        "station_id": "HKO",
                        "station_name": "Hong Kong Observatory",
                    }
                ),
                json.dumps(
                    {
                        "city": "hong kong",
                        "status": "error",
                        "requested_at": "2026-06-16T00:23:00+00:00",
                        "error": "ConnectionError",
                        "station_id": "HKO",
                        "station_name": "Hong Kong Observatory",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    payload = build_dashboard_payload(
        Settings(
            bankroll_usd=100.0,
            state_path=str(state_path),
            station_nowcast_request_log_path=str(nowcast_log),
        )
    )

    hong_kong = next(row for row in payload["scanner"]["station_observations"] if row["city"] == "hong kong")
    assert hong_kong["status"] == "error"
    assert hong_kong["error"] == "ConnectionError"
    assert hong_kong["last_success_at"] == "2026-06-16T00:10:00+00:00"
    assert hong_kong["last_failure_at"] == "2026-06-16T00:23:00+00:00"
    assert hong_kong["last_failure_error"] == "ConnectionError"


def test_dashboard_refuses_public_host_with_empty_token(monkeypatch):
    class FailingServer:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("public dashboard must refuse startup before binding")

    monkeypatch.setattr(dashboard_module, "ThreadingHTTPServer", FailingServer)

    with pytest.raises(ValueError, match="DASHBOARD_TOKEN"):
        dashboard_module.run_dashboard(Settings(dashboard_host="0.0.0.0", dashboard_token=""))


@pytest.mark.parametrize("token", ["abc", "123456", "short-dashboard-token"])
def test_dashboard_refuses_public_host_with_short_token(monkeypatch, token):
    class FailingServer:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("public dashboard must refuse startup before binding")

    monkeypatch.setattr(dashboard_module, "ThreadingHTTPServer", FailingServer)

    with pytest.raises(ValueError, match="at least 32 characters"):
        dashboard_module.run_dashboard(Settings(dashboard_host="0.0.0.0", dashboard_token=token))


@pytest.mark.parametrize("host", ["0.0.0.0", "::"])
@pytest.mark.parametrize("token", ["placeholder", "changeme", "secret", "token", "password"])
def test_dashboard_refuses_public_host_with_placeholder_token(monkeypatch, host, token):
    class FailingServer:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("public dashboard must refuse startup before binding")

    monkeypatch.setattr(dashboard_module, "ThreadingHTTPServer", FailingServer)

    with pytest.raises(ValueError, match="placeholder"):
        dashboard_module.run_dashboard(Settings(dashboard_host=host, dashboard_token=token))


def test_dashboard_allows_localhost_without_token(monkeypatch):
    class Served(Exception):
        pass

    created_servers = []

    class DummyServer:
        def __init__(self, server_address, handler_cls):
            self.server_address = server_address
            self.handler_cls = handler_cls
            created_servers.append(self)

        def serve_forever(self):
            raise Served

    monkeypatch.setattr(dashboard_module, "ThreadingHTTPServer", DummyServer)

    with pytest.raises(Served):
        dashboard_module.run_dashboard(Settings(dashboard_host="localhost", dashboard_token=""))

    assert created_servers[0].server_address == ("localhost", 8787)
    assert created_servers[0].dashboard_token == ""


def test_dashboard_allows_127_localhost_without_token(monkeypatch):
    class Served(Exception):
        pass

    created_servers = []

    class DummyServer:
        def __init__(self, server_address, handler_cls):
            self.server_address = server_address
            self.handler_cls = handler_cls
            created_servers.append(self)

        def serve_forever(self):
            raise Served

    monkeypatch.setattr(dashboard_module, "ThreadingHTTPServer", DummyServer)

    with pytest.raises(Served):
        dashboard_module.run_dashboard(Settings(dashboard_host="127.0.0.1", dashboard_token=""))

    assert created_servers[0].server_address == ("127.0.0.1", 8787)
    assert created_servers[0].dashboard_token == ""


@pytest.mark.parametrize("host", ["0.0.0.0", "::"])
def test_dashboard_allows_public_host_with_real_token(monkeypatch, host):
    class Served(Exception):
        pass

    created_servers = []

    class DummyServer:
        def __init__(self, server_address, handler_cls):
            self.server_address = server_address
            self.handler_cls = handler_cls
            created_servers.append(self)

        def serve_forever(self):
            raise Served

    monkeypatch.setattr(dashboard_module, "ThreadingHTTPServer", DummyServer)

    with pytest.raises(Served):
        dashboard_module.run_dashboard(
            Settings(dashboard_host=host, dashboard_token="a8f4c2d9e1b7a6c5f0d3e9b1a2c4d6f8")
        )

    assert created_servers[0].server_address == (host, 8787)
    assert created_servers[0].dashboard_token == "a8f4c2d9e1b7a6c5f0d3e9b1a2c4d6f8"


@pytest.mark.parametrize("dashboard_host", ["0.0.0.0", "::"])
def test_public_dashboard_api_status_rejects_query_token_and_accepts_header(monkeypatch, dashboard_host):
    server, thread, base_url = _start_test_dashboard(monkeypatch, dashboard_host)
    try:
        assert _get_status(f"{base_url}/api/status") == HTTPStatus.FORBIDDEN
        assert _get_status(f"{base_url}/api/status?token={DASHBOARD_TEST_TOKEN}") == HTTPStatus.FORBIDDEN
        assert (
            _get_status(
                f"{base_url}/api/status",
                headers={"X-Dashboard-Token": DASHBOARD_TEST_TOKEN},
            )
            == HTTPStatus.OK
        )
    finally:
        _stop_test_dashboard(server, thread)


@pytest.mark.parametrize("dashboard_host", ["localhost", "127.0.0.1"])
def test_local_dashboard_api_status_keeps_query_token_first_load_compatibility(monkeypatch, dashboard_host):
    server, thread, base_url = _start_test_dashboard(monkeypatch, dashboard_host)
    try:
        assert _get_status(f"{base_url}/api/status") == HTTPStatus.FORBIDDEN
        assert _get_status(f"{base_url}/api/status?token={DASHBOARD_TEST_TOKEN}") == HTTPStatus.OK
        assert (
            _get_status(
                f"{base_url}/api/status",
                headers={"X-Dashboard-Token": DASHBOARD_TEST_TOKEN},
            )
            == HTTPStatus.OK
        )
    finally:
        _stop_test_dashboard(server, thread)


def test_dashboard_logs_redact_url_query_token(capsys):
    handler = object.__new__(dashboard_module.DashboardHandler)
    handler.address_string = lambda: "127.0.0.1"

    handler.log_message("%s", "GET /api/status?token=super-secret-token&range=ALL HTTP/1.1")

    output = capsys.readouterr().out
    assert "super-secret-token" not in output
    assert "token=<redacted>" in output


def test_dashboard_html_sends_api_token_by_header_not_query_string():
    assert '"/api/status" + q' not in HTML
    assert '?token=" + encodeURIComponent(token)' not in HTML
    assert '"X-Dashboard-Token": token' in HTML


def test_dashboard_payload_summarizes_state_trades_and_decisions(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    raw_path = tmp_path / "raw.jsonl"
    state_path.write_text(
        json.dumps(
            {
                "cash_usd": 950.0,
                "realized_pnl_usd": 0.0,
                "positions": [
                    {
                        "position_id": "p1",
                        "market_id": "m1",
                        "question": "Seoul 2025.05.24 21C or higher?",
                        "token_id": "yes",
                        "side": "YES",
                        "entry_price": 0.5,
                        "shares": 100.0,
                        "cost_usd": 50.0,
                        "opened_at": "2026-05-24T10:00:00+00:00",
                        "last_mark_price": 0.6,
                        "metadata": {"city": "seoul", "date_hint": "may 24", "station_id": "RKSI"},
                    }
                ],
                "stats": {"temperature": {"wins": 1, "losses": 1, "pnl": 4.0}},
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-24T10:00:00+00:00",
                "action": "OPEN",
                "market_id": "m1",
                "slug": "seoul-21c",
                "question": "Seoul 2025.05.24 21C or higher?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "100",
                "price": "0.5",
                "cash_delta_or_pnl": "-50",
                "reason": "entry",
            },
            {
                "ts": "2026-05-24T11:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m2",
                "slug": "nyc-90f",
                "question": "NYC 90F?",
                "market_type": "temperature",
                "side": "NO",
                "token_id": "no",
                "shares": "20",
                "price": "0.7",
                "cash_delta_or_pnl": "4",
                "reason": "take profit",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-24T10:00:00+00:00",
                "market_id": "m1",
                "slug": "seoul-21c",
                "question": "Seoul 2025.05.24 21C or higher?",
                "market_type": "temperature",
                "side": "YES",
                "p_true": "0.7",
                "p_exec": "0.5",
                "net_edge": "0.1",
                "size_usd": "50",
                "size_shares": "100",
                "entry_fraction": "0.05",
                "probability_stop_threshold": "0.60",
                "model_fair_price": "0.64",
                "target_exit_price": "0.60",
                "market_heat_score": "-0.1",
                "reason": "edge ok",
                "note": (
                    "Singapore Changi Airport Station [WSSS] target_date=2026-06-12; "
                    "mean=83.7F; observed_high_c=29.0; "
                    "observed_at=2026-06-11T18:30:00+00:00; nowcast_source=aviationweather-metar"
                ),
            },
            {
                "ts": "2026-05-24T10:01:00+00:00",
                "market_id": "m3",
                "slug": "skip",
                "question": "Bad market",
                "market_type": "temperature",
                "side": "SKIP",
                "p_true": "0.5",
                "p_exec": "",
                "net_edge": "-999",
                "size_usd": "0",
                "size_shares": "0",
                "entry_fraction": "",
                "probability_stop_threshold": "",
                "model_fair_price": "",
                "target_exit_price": "",
                "market_heat_score": "",
                "reason": "confidence too low",
                "note": "official station observation unavailable: rate limited",
            },
        ],
    )
    settings = Settings(
        bankroll_usd=1000.0,
        state_path=str(state_path),
        trades_csv_path=str(trades_path),
        decisions_csv_path=str(decisions_path),
        raw_snapshots_path=str(raw_path),
    )

    payload = build_dashboard_payload(settings, auth_required=True)

    assert payload["security"]["auth_required"] is True
    assert payload["summary"]["cash"] == 950.0
    assert payload["summary"]["market_value"] == pytest.approx(58.8)
    assert payload["summary"]["equity"] == pytest.approx(1008.8)
    assert payload["summary"]["total_pnl"] == pytest.approx(8.8)
    assert payload["summary"]["wins"] == 1
    assert payload["summary"]["losses"] == 1
    assert payload["scanner"]["decisions"] == 2
    assert "forecast_unavailable" not in payload["scanner"]
    assert payload["scanner"]["skips"] == 1
    assert payload["scanner"]["entries"] == 1
    assert payload["scanner"]["decision_totals_exact"] is True
    assert payload["scanner"]["decision_totals_scope"] == "full"
    assert payload["bot"]["last_event_at"] == "2026-05-24T11:00:00+00:00"
    assert payload["bot"]["scan_interval_seconds"] == 2400
    assert payload["bot"]["orderbook_mode"] == "websocket"
    assert payload["positions"][0]["unrealized_pnl"] == pytest.approx(8.8)
    assert "forecast_c" not in payload["positions"][0]
    assert payload["positions"][0]["nowcast_high_c"] == pytest.approx(29.0)
    assert payload["positions"][0]["nowcast_low_c"] is None
    assert payload["positions"][0]["station_id"] == "RKSI"
    assert payload["positions"][0]["station_name"] == "Incheon Intl Airport Station"
    assert payload["positions"][0]["bucket_label"] == "21°C 이상"
    assert payload["positions"][0]["display_title"] == "Seoul · May 24 · 21°C 이상 · YES"
    city_entry = next(row for row in payload["city_entries"] if row["city"] == "seoul")
    assert city_entry["open_count"] == 1
    assert city_entry["open_entry_usd"] == pytest.approx(50.0)
    assert city_entry["open_market_value_usd"] == pytest.approx(58.8)
    assert city_entry["open_unrealized_pnl"] == pytest.approx(8.8)
    assert city_entry["latest_entry_at"] == "2026-05-24T10:00:00+00:00"
    assert city_entry["positions"][0]["cost_usd"] == pytest.approx(50.0)
    assert city_entry["recent_trades"][0]["entry_amount_usd"] == pytest.approx(50.0)
    assert "events" not in payload
    assert "recent_decisions" not in payload
    assert "pressure" not in payload
    assert "realized_results" in payload


def test_dashboard_position_exposes_probability_calibration_audit_fields(tmp_path):
    state_path = tmp_path / "state.json"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(
        json.dumps(
            {
                "cash_usd": 60.0,
                "realized_pnl_usd": 0.0,
                "positions": [
                    {
                        "position_id": "p-audit",
                        "market_id": "m-audit",
                        "question": "Will the highest temperature in Seoul be 23C today?",
                        "token_id": "yes",
                        "side": "YES",
                        "entry_price": 0.5,
                        "shares": 80.0,
                        "cost_usd": 40.0,
                        "opened_at": "2026-06-22T00:00:00+00:00",
                        "last_mark_price": 0.5,
                        "metadata": {
                            "city": "seoul",
                            "station_id": "RKSI",
                            "raw_selected_side_probability": 0.97,
                            "selected_side_probability": 0.96,
                            "probability_tier": "95",
                            "calibration_sample_days": 1460,
                            "calibration_profile_key": "RKSI|month=6|minute=900|high|C",
                            "calibration_status": "RESIDUAL_PROBABILITY_OK",
                            "requested_size_usd": 50.0,
                            "executable_size_usd": 40.0,
                            "event_cap_override_fraction": 0.5,
                            "expected_net_return_pct": 0.7,
                            "entry_fee_usdc": 1.0,
                            "station_audit": {
                                "station_local_date": "2026-06-22",
                                "strategy_allowed_reason": "formation monitoring started",
                            },
                        },
                    }
                ],
                "stats": {},
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-06-22T00:00:00+00:00",
                "market_id": "m-audit",
                "question": "Will the highest temperature in Seoul be 23C today?",
                "side": "YES",
                "raw_selected_side_probability": "0.97",
                "selected_side_probability": "0.96",
                "probability_tier": "95",
                "calibration_sample_days": "1460",
                "calibration_profile_key": "RKSI|month=6|minute=900|high|C",
                "calibration_status": "RESIDUAL_PROBABILITY_OK",
                "requested_size_usd": "50",
                "executable_size_usd": "40",
                "event_cap_override_fraction": "0.5",
                "expected_net_return_pct": "0.7",
                "entry_fee_usdc": "1",
            }
        ],
    )

    payload = build_dashboard_payload(
        Settings(
            bankroll_usd=100.0,
            state_path=str(state_path),
            decisions_csv_path=str(decisions_path),
            trades_csv_path=str(tmp_path / "trades.csv"),
        )
    )

    position = payload["positions"][0]
    assert position["raw_selected_side_probability"] == pytest.approx(0.97)
    assert position["selected_side_probability"] == pytest.approx(0.96)
    assert position["probability_tier"] == "95"
    assert position["calibration_sample_days"] == 1460
    assert position["calibration_profile_key"] == "RKSI|month=6|minute=900|high|C"
    assert position["calibration_status"] == "RESIDUAL_PROBABILITY_OK"
    assert position["requested_size_usd"] == pytest.approx(50.0)
    assert position["executable_size_usd"] == pytest.approx(40.0)
    assert position["event_cap_override_fraction"] == pytest.approx(0.5)
    assert position["expected_net_return_pct"] == pytest.approx(0.7)
    assert position["entry_fee_usdc"] == pytest.approx(1.0)
    assert position["station_local_date"] == "2026-06-22"


def test_dashboard_station_signals_include_calibrated_residual_observation(tmp_path):
    state_path = tmp_path / "state.json"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(
        json.dumps({"cash_usd": 100.0, "realized_pnl_usd": 0.0, "positions": [], "stats": {}}),
        encoding="utf-8",
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-06-22T00:00:00+00:00",
                "market_id": "m-residual",
                "question": "Will the highest temperature in Seoul be 23C today?",
                "side": "YES",
                "city": "seoul",
                "station_id": "RKSI",
                "signal_source": "official-station-residual-high-yes",
                "reason": "YES edge=0.20",
                "raw_selected_side_probability": "0.97",
                "selected_side_probability": "0.96",
                "probability_tier": "95",
                "calibration_sample_days": "1460",
                "calibration_profile_key": "RKSI|month=6|minute=900|high|C",
                "calibration_status": "RESIDUAL_PROBABILITY_OK",
                "requested_size_usd": "50",
                "executable_size_usd": "40",
                "event_cap_override_fraction": "0.5",
            }
        ],
    )

    payload = build_dashboard_payload(
        Settings(
            bankroll_usd=100.0,
            state_path=str(state_path),
            decisions_csv_path=str(decisions_path),
            trades_csv_path=str(tmp_path / "trades.csv"),
        )
    )

    signal = payload["scanner"]["station_signals"][0]
    assert signal["market_id"] == "m-residual"
    assert signal["selected_side_probability"] == pytest.approx(0.96)
    assert signal["probability_tier"] == "95"
    assert signal["calibration_status"] == "RESIDUAL_PROBABILITY_OK"


def test_dashboard_scanner_counts_all_decisions_not_just_recent_tail(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    raw_path = tmp_path / "raw.jsonl"
    state_path.write_text(json.dumps({"cash_usd": 1000.0, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-24T10:00:00+00:00",
                "action": "OPEN",
                "market_id": "m1",
                "slug": "seed",
                "question": "Seed trade",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "1",
                "price": "0.5",
                "cash_delta_or_pnl": "-0.5",
                "reason": "seed",
            },
        ],
    )
    rows = []
    for idx in range(805):
        rows.append(
            {
                "ts": f"2026-05-24T10:{idx % 60:02d}:00+00:00",
                "market_id": f"skip-{idx}",
                "slug": "skip",
                "question": "Skipped market",
                "market_type": "temperature",
                "side": "SKIP",
                "p_true": "0.5",
                "p_exec": "",
                "net_edge": "-999",
                "size_usd": "0",
                "size_shares": "0",
                "entry_fraction": "",
                "probability_stop_threshold": "",
                "model_fair_price": "",
                "target_exit_price": "",
                "market_heat_score": "",
                "reason": "edge below",
                "note": "",
            }
        )
    rows.append(
        {
            "ts": "2026-05-24T11:00:00+00:00",
            "market_id": "entry-1",
            "slug": "yes",
            "question": "Entry market",
            "market_type": "temperature",
            "side": "YES",
            "p_true": "0.7",
            "p_exec": "0.5",
            "net_edge": "0.1",
            "size_usd": "50",
            "size_shares": "100",
            "entry_fraction": "0.05",
            "probability_stop_threshold": "0.6",
            "model_fair_price": "0.64",
            "target_exit_price": "0.60",
            "market_heat_score": "-0.1",
            "reason": "edge ok",
            "note": "",
        }
    )
    write_csv(decisions_path, rows)
    settings = Settings(
        state_path=str(state_path),
        trades_csv_path=str(trades_path),
        decisions_csv_path=str(decisions_path),
        raw_snapshots_path=str(raw_path),
    )

    payload = build_dashboard_payload(settings)

    assert payload["scanner"]["decisions"] == 806
    assert payload["scanner"]["skips"] == 805
    assert payload["scanner"]["entries"] == 1
    assert payload["scanner"]["decision_totals_exact"] is True
    assert payload["scanner"]["decision_totals_scope"] == "full"
    assert "recent_decisions" not in payload


def test_dashboard_scanner_totals_include_appended_decisions(tmp_path):
    state_path = tmp_path / "state.json"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 1000.0, "positions": []}), encoding="utf-8")
    rows = [
        {
            "ts": "2026-05-24T10:00:00+00:00",
            "side": "SKIP",
            "reason": "edge below",
            "note": "",
        }
    ]
    write_csv(decisions_path, rows)
    settings = Settings(state_path=str(state_path), decisions_csv_path=str(decisions_path))

    first_payload = build_dashboard_payload(settings)

    with decisions_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writerow(
            {
                "ts": "2026-05-24T10:01:00+00:00",
                "side": "YES",
                "reason": "edge ok",
                "note": "no station fallback available",
            }
        )
    second_payload = build_dashboard_payload(settings)

    assert first_payload["scanner"]["decisions"] == 1
    assert second_payload["scanner"]["decisions"] == 2
    assert second_payload["scanner"]["skips"] == 1
    assert second_payload["scanner"]["entries"] == 1
    assert "forecast_unavailable" not in second_payload["scanner"]


def test_dashboard_large_decision_file_skips_initial_full_scan(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 1000.0, "positions": []}), encoding="utf-8")
    rows = [
        {
            "ts": f"2026-05-24T09:{idx:02d}:00+00:00",
            "side": "YES",
            "reason": "old entry signal",
            "note": "",
        }
        for idx in range(5)
    ]
    rows.extend(
        {
            "ts": f"2026-05-24T10:{idx % 60:02d}:00+00:00",
            "side": "SKIP",
            "reason": "edge below",
            "note": "",
        }
        for idx in range(5000)
    )
    write_csv(decisions_path, rows)

    def fail_full_scan(*_args, **_kwargs):
        raise AssertionError("large decision files should not block the dashboard with a full scan")

    monkeypatch.setattr(dashboard_module, "MAX_INITIAL_DECISION_TOTAL_SCAN_BYTES", 1)
    monkeypatch.setattr(dashboard_module, "_scan_decision_totals", fail_full_scan)

    payload = build_dashboard_payload(Settings(state_path=str(state_path), decisions_csv_path=str(decisions_path)))

    assert payload["scanner"]["decisions"] == 5000
    assert payload["scanner"]["skips"] == 5000
    assert payload["scanner"]["entries"] == 0
    assert payload["scanner"]["decision_totals_exact"] is False
    assert payload["scanner"]["decision_totals_scope"] == "recent_tail"


def test_dashboard_scanner_distinguishes_entry_signals_from_actual_opens(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 950.0, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-24T10:00:00+00:00",
                "action": "OPEN",
                "market_id": "m1",
                "slug": "held",
                "question": "Held market",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "100",
                "price": "0.5",
                "cash_delta_or_pnl": "-50",
                "reason": "entry",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": f"2026-05-24T10:0{idx}:00+00:00",
                "market_id": "m1",
                "slug": "held",
                "question": "Held market",
                "market_type": "temperature",
                "side": "YES",
                "p_true": "0.7",
                "p_exec": "0.5",
                "net_edge": "0.1",
                "size_usd": "50",
                "size_shares": "100",
                "entry_fraction": "0.05",
                "probability_stop_threshold": "0.6",
                "model_fair_price": "0.64",
                "target_exit_price": "0.60",
                "market_heat_score": "-0.1",
                "reason": "edge ok",
                "note": "",
            }
            for idx in range(3)
        ],
    )
    settings = Settings(
        state_path=str(state_path),
        trades_csv_path=str(trades_path),
        decisions_csv_path=str(decisions_path),
    )

    payload = build_dashboard_payload(settings)

    assert payload["scanner"]["entries"] == 3
    assert payload["scanner"]["entry_signals"] == 3
    assert payload["scanner"]["actual_opens"] == 1


def test_dashboard_payload_builds_realized_trade_rows_for_operator_table(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 1035.0, "realized_pnl_usd": 14.0, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-29T10:00:00+00:00",
                "action": "OPEN",
                "market_id": "m-seoul",
                "slug": "seoul-27c",
                "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "100",
                "price": "0.21",
                "cash_delta_or_pnl": "-21",
                "reason": "target_exit=0.320",
            },
            {
                "ts": "2026-05-29T11:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m-seoul",
                "slug": "seoul-27c",
                "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "100",
                "price": "0.35",
                "cash_delta_or_pnl": "14",
                "reason": "take profit",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-29T10:00:01+00:00",
                "market_id": "m-seoul",
                "slug": "seoul-27c",
                "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "p_true": "0.70",
                "p_exec": "0.21",
                "net_edge": "0.20",
                "size_usd": "21",
                "size_shares": "100",
                "entry_fraction": "0.02",
                "probability_stop_threshold": "0.60",
                "model_fair_price": "0.40",
                "target_exit_price": "0.32",
                "market_heat_score": "0.1",
                "reason": "edge ok",
                "note": "station target_date=2026-05-29; >=27.0C/80.6F; members=82; vote=0.70; mean=86.0F; spread=2.0F",
            }
        ],
    )

    payload = build_dashboard_payload(
        Settings(state_path=str(state_path), trades_csv_path=str(trades_path), decisions_csv_path=str(decisions_path))
    )

    realized = payload["realized_results"][0]
    assert realized["date_hint"] == "may 29"
    assert realized["city"] == "seoul"
    assert "forecast_c" not in realized
    assert realized["threshold_c"] == 27.0
    assert realized["condition_label"] == "or higher"
    assert realized["expected_exit_price"] == 0.32
    assert realized["entry_price"] == 0.21
    assert realized["exit_price"] == 0.35
    assert realized["pnl"] == 14.0
    assert round(realized["roi"], 4) == round(14.0 / 21.0, 4)


def test_dashboard_realized_rows_are_latest_first_and_numeric_when_history_is_sparse(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 1008.0, "realized_pnl_usd": 8.0, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-29T11:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m-old",
                "slug": "old",
                "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "10",
                "price": "0.60",
                "cash_delta_or_pnl": "3",
                "reason": "take profit",
            },
            {
                "ts": "2026-05-30T11:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m-new",
                "slug": "new",
                "question": "Will the highest temperature in Seoul be 29°C or higher on May 30?",
                "market_type": "temperature",
                "side": "NO",
                "token_id": "no",
                "shares": "10",
                "price": "0.40",
                "cash_delta_or_pnl": "5",
                "reason": "settled",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-30T10:00:00+00:00",
                "market_id": "m-new",
                "slug": "new",
                "question": "Will the highest temperature in Seoul be 29°C or higher on May 30?",
                "market_type": "temperature",
                "side": "NO",
                "p_true": "0.30",
                "p_exec": "",
                "net_edge": "0.10",
                "size_usd": "4",
                "size_shares": "10",
                "entry_fraction": "",
                "probability_stop_threshold": "",
                "model_fair_price": "",
                "target_exit_price": "",
                "market_heat_score": "",
                "reason": "edge ok",
                "note": "",
            }
        ],
    )

    payload = build_dashboard_payload(
        Settings(state_path=str(state_path), trades_csv_path=str(trades_path), decisions_csv_path=str(decisions_path))
    )

    first = payload["realized_results"][0]
    assert first["market_id"] == "m-new"
    assert "forecast_c" not in first
    assert first["expected_exit_price"] == 0.4
    assert first["entry_price"] == 0.4
    assert first["exit_price"] == 0.4
    assert first["roi"] == 1.25


def test_dashboard_realized_row_exposes_probability_stop_trigger(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 1000.34, "realized_pnl_usd": 0.34, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-06-18T01:08:20+00:00",
                "action": "OPEN",
                "market_id": "m-milan",
                "slug": "milan-34c",
                "question": "Will the highest temperature in Milan be 34°C on June 19?",
                "market_type": "temperature",
                "side": "NO",
                "token_id": "no",
                "shares": "33.39",
                "price": "0.59",
                "cash_delta_or_pnl": "-19.71",
                "reason": "entry: station_p=0.166, side=NO, p_exec=0.5900, target_exit=0.7272",
            },
            {
                "ts": "2026-06-18T06:23:00+00:00",
                "action": "CLOSE",
                "market_id": "m-milan",
                "slug": "milan-34c",
                "question": "Will the highest temperature in Milan be 34°C on June 19?",
                "market_type": "temperature",
                "side": "NO",
                "token_id": "no",
                "shares": "33.39",
                "price": "0.624",
                "cash_delta_or_pnl": "0.34",
                "reason": "exit_trigger=probability_stop; probability stop: side_probability 0.834->0.720 <= threshold=0.734 (drop=0.114); exit_fee=$0.3700 gross=$19.71 net=$19.34",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-06-18T06:20:00+00:00",
                "market_id": "m-milan",
                "slug": "milan-34c",
                "question": "Will the highest temperature in Milan be 34°C on June 19?",
                "market_type": "temperature",
                "side": "NO",
                "p_true": "0.280",
                "p_exec": "0.624",
                "net_edge": "0.02",
                "size_usd": "0",
                "size_shares": "0",
                "entry_fraction": "",
                "probability_stop_threshold": "0.734",
                "model_fair_price": "",
                "target_exit_price": "",
                "market_heat_score": "",
                "reason": "hold",
                "note": "station target_date=2026-06-19; ==34.0C/93.2F; mean=90.8F",
            }
        ],
    )

    payload = build_dashboard_payload(
        Settings(state_path=str(state_path), trades_csv_path=str(trades_path), decisions_csv_path=str(decisions_path))
    )

    realized = payload["realized_results"][0]
    assert realized["exit_trigger"] == "probability_stop"
    assert "forecast_c" not in realized
    assert "p_true" not in realized


def test_dashboard_template_explains_probability_stop_as_defensive_close():
    assert "방어청산" in HTML


def test_dashboard_realized_loss_displays_minus_sign():
    assert 'const pnlSign = isProfit ? "+" : "-";' in HTML
    assert "관측소 잠금 점수가 보유 방향과 반대로 약해져" in HTML
    assert "수익을 키우는 익절이 아니라" in HTML


def test_dashboard_realized_rows_survive_recent_skip_trade_noise(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 1008.0, "realized_pnl_usd": 8.0, "positions": []}), encoding="utf-8")
    rows = [
        {
            "ts": "2026-05-29T10:00:00+00:00",
            "action": "OPEN",
            "market_id": "m-seoul",
            "slug": "seoul-27c",
            "question": "Will the highest temperature in Seoul be 27째C or higher on May 29?",
            "market_type": "temperature",
            "side": "YES",
            "token_id": "yes",
            "shares": "100",
            "price": "0.21",
            "cash_delta_or_pnl": "-21",
            "reason": "target_exit=0.320",
        },
        {
            "ts": "2026-05-29T11:00:00+00:00",
            "action": "CLOSE",
            "market_id": "m-seoul",
            "slug": "seoul-27c",
            "question": "Will the highest temperature in Seoul be 27째C or higher on May 29?",
            "market_type": "temperature",
            "side": "YES",
            "token_id": "yes",
            "shares": "100",
            "price": "0.35",
            "cash_delta_or_pnl": "14",
            "reason": "take profit",
        },
    ]
    rows.extend(
        {
            "ts": f"2026-06-03T10:{idx % 60:02d}:00+00:00",
            "action": "SKIP_CITY_CAP",
            "market_id": f"m-skip-{idx}",
            "slug": "hong-kong-34c",
            "question": "Will the highest temperature in Hong Kong be 34째C or higher on June 4?",
            "market_type": "temperature",
            "side": "NO",
            "token_id": "no",
            "shares": "0",
            "price": "0.331",
            "cash_delta_or_pnl": "0",
            "reason": "SKIP_CITY_CAP: hong kong exposure=50.00+59.59 > limit=95.35",
        }
        for idx in range(900)
    )
    write_csv(trades_path, rows)
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-29T10:00:01+00:00",
                "market_id": "m-seoul",
                "slug": "seoul-27c",
                "question": "Will the highest temperature in Seoul be 27째C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "p_true": "0.70",
                "p_exec": "0.21",
                "net_edge": "0.20",
                "size_usd": "21",
                "size_shares": "100",
                "entry_fraction": "0.02",
                "probability_stop_threshold": "0.60",
                "model_fair_price": "0.40",
                "target_exit_price": "0.32",
                "market_heat_score": "0.1",
                "reason": "edge ok",
                "note": "station target_date=2026-05-29; >=27.0C/80.6F; members=82; vote=0.70; mean=86.0F; spread=2.0F",
            }
        ],
    )

    payload = build_dashboard_payload(
        Settings(state_path=str(state_path), trades_csv_path=str(trades_path), decisions_csv_path=str(decisions_path))
    )

    assert [row["action"] for row in payload["recent_trades"]] == ["CLOSE", "OPEN"]
    assert payload["realized_results"][0]["market_id"] == "m-seoul"
    assert payload["realized_results"][0]["pnl"] == 14.0


def test_dashboard_open_positions_include_polymarket_link_without_forecast_weather(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(
        json.dumps(
            {
                "cash_usd": 979.0,
                "positions": [
                    {
                        "position_id": "p1",
                        "market_id": "m-seoul",
                        "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                        "token_id": "yes",
                        "side": "YES",
                        "entry_price": 0.21,
                        "shares": 100.0,
                        "cost_usd": 21.0,
                        "opened_at": "2026-05-29T10:00:00+00:00",
                        "last_mark_price": 0.30,
                        "metadata": {"city": "seoul", "date_hint": "may 29", "slug": "seoul-27c"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-29T10:00:00+00:00",
                "action": "OPEN",
                "market_id": "m-seoul",
                "slug": "seoul-27c",
                "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "100",
                "price": "0.21",
                "cash_delta_or_pnl": "-21",
                "reason": "entry",
            }
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-29T10:00:01+00:00",
                "market_id": "m-seoul",
                "slug": "seoul-27c",
                "question": "Will the highest temperature in Seoul be 27°C or higher on May 29?",
                "market_type": "temperature",
                "side": "YES",
                "p_true": "0.70",
                "p_exec": "0.21",
                "net_edge": "0.20",
                "size_usd": "21",
                "size_shares": "100",
                "entry_fraction": "0.02",
                "probability_stop_threshold": "0.60",
                "model_fair_price": "0.40",
                "target_exit_price": "0.32",
                "market_heat_score": "0.1",
                "reason": "edge ok",
                "note": "station target_date=2026-05-29; >=27.0C/80.6F; members=82; vote=0.70; mean=86.0F; spread=2.0F",
            }
        ],
    )

    payload = build_dashboard_payload(
        Settings(state_path=str(state_path), trades_csv_path=str(trades_path), decisions_csv_path=str(decisions_path))
    )

    position = payload["positions"][0]
    assert position["market_url"] == "https://polymarket.com/ko/event/seoul-27c"
    assert "forecast_c" not in position
    assert "p_true" not in position


def test_dashboard_open_position_uses_actual_question_and_event_link(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(
        json.dumps(
            {
                "cash_usd": 902.0,
                "positions": [
                    {
                        "position_id": "p-london-low",
                        "market_id": "m-london-low-22",
                        "question": "Will the lowest temperature in London be 22°C on June 27?",
                        "token_id": "yes",
                        "side": "YES",
                        "entry_price": 0.358,
                        "shares": 265.15,
                        "cost_usd": 98.0,
                        "opened_at": "2026-06-27T04:00:30+00:00",
                        "last_mark_price": 0.001,
                        "metadata": {
                            "city": "london",
                            "date_hint": "june 27",
                            "event_slug": "lowest-temperature-in-london-on-june-27-2026",
                            "slug": "lowest-temperature-in-london-on-june-27-2026-22c",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-06-27T04:00:30+00:00",
                "action": "OPEN",
                "market_id": "m-london-low-22",
                "slug": "lowest-temperature-in-london-on-june-27-2026-22c",
                "question": "Will the lowest temperature in London be 22°C on June 27?",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "265.15",
                "price": "0.358",
                "cash_delta_or_pnl": "-98",
                "reason": "entry",
            }
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-06-27T04:00:27+00:00",
                "market_id": "m-london-low-22",
                "slug": "lowest-temperature-in-london-on-june-27-2026-22c",
                "question": "Will the lowest temperature in London be 22°C on June 27?",
                "market_type": "temperature",
                "side": "YES",
                "p_true": "0.88",
                "p_exec": "0.358",
                "net_edge": "0.42",
                "size_usd": "98",
                "size_shares": "265.15",
                "entry_fraction": "0.10",
                "probability_stop_threshold": "0.72",
                "model_fair_price": "0.78",
                "target_exit_price": "0.66",
                "market_heat_score": "0.0",
                "reason": "edge ok",
                "note": "",
            }
        ],
    )

    payload = build_dashboard_payload(
        Settings(
            state_path=str(state_path),
            trades_csv_path=str(trades_path),
            decisions_csv_path=str(decisions_path),
        )
    )

    position = payload["positions"][0]
    assert position["event_title"] == "Will the lowest temperature in London be 22°C on June 27?"
    assert position["market_url"] == (
        "https://polymarket.com/ko/event/"
        "lowest-temperature-in-london-on-june-27-2026"
    )


def test_dashboard_open_position_uses_latest_station_decision_not_entry_snapshot(tmp_path):
    state_path = tmp_path / "state.json"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(
        json.dumps(
            {
                "cash_usd": 150.0,
                "positions": [
                    {
                        "position_id": "p-seoul",
                        "market_id": "m-seoul-23",
                        "question": "Will the highest temperature in Seoul be 23°C on June 19?",
                        "side": "YES",
                        "entry_price": 0.6,
                        "shares": 50.0,
                        "cost_usd": 30.0,
                        "opened_at": "2099-06-19T00:00:00+00:00",
                        "last_mark_price": 0.6,
                        "metadata": {"city": "seoul", "station_id": "RKSI"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2099-06-19T00:01:00+00:00",
                "market_id": "m-seoul-23",
                "question": "Will the highest temperature in Seoul be 23°C on June 19?",
                "side": "YES",
                "city": "seoul",
                "station_id": "RKSI",
                "note": "observed_high_c=23.2; official_nowcast_lock=base_yes; entry_size_fraction_override=0.20",
            },
            {
                "ts": "2099-06-19T00:10:00+00:00",
                "market_id": "m-seoul-23",
                "question": "Will the highest temperature in Seoul be 23°C on June 19?",
                "side": "HOLD",
                "city": "seoul",
                "station_id": "RKSI",
                "note": (
                    "observed_high_c=24.0; observed_at=2099-06-19T00:09:00+00:00; "
                    "official_nowcast_lock=strong_no; next_displayed_integer_c=24.0; "
                    "entry_size_fraction_override=0.50"
                ),
            },
        ],
    )

    payload = build_dashboard_payload(
        Settings(state_path=str(state_path), decisions_csv_path=str(decisions_path))
    )

    position = payload["positions"][0]
    assert position["nowcast_high_c"] == pytest.approx(24.0)
    assert position["station_lock_strength"] == "strong_no"
    assert position["station_allocation_fraction"] == pytest.approx(0.50)


def test_dashboard_open_position_link_points_to_weather_event_slug(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "cash_usd": 900.0,
                "positions": [
                    {
                        "position_id": "p1",
                        "market_id": "m-beijing",
                        "question": "Will the highest temperature in Beijing be 25°C or below on June 4?",
                        "token_id": "no",
                        "side": "NO",
                        "entry_price": 0.549,
                        "shares": 108.24,
                        "cost_usd": 59.43,
                        "opened_at": "2026-06-03T10:00:00+00:00",
                        "last_mark_price": 0.242,
                        "metadata": {
                            "city": "beijing",
                            "date_hint": "june 4",
                            "slug": "highest-temperature-in-beijing-on-june-4-2026-25corbelow",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = build_dashboard_payload(Settings(state_path=str(state_path)))

    position = payload["positions"][0]
    assert (
        position["market_url"]
        == "https://polymarket.com/ko/event/"
        "highest-temperature-in-beijing-on-june-4-2026"
    )


def test_dashboard_market_url_keeps_event_year_when_suffix_has_bucket():
    assert dashboard_module._polymarket_market_url(
        "lowest-temperature-in-tokyo-on-june-28-2026-21c"
    ) == (
        "https://polymarket.com/ko/event/"
        "lowest-temperature-in-tokyo-on-june-28-2026"
    )


def test_dashboard_summary_reports_profit_loss_without_external_weather_cache_time(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    state_path.write_text(json.dumps({"cash_usd": 1008.0, "realized_pnl_usd": 8.0, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-29T11:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m-win",
                "slug": "win",
                "question": "Win",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "10",
                "price": "0.70",
                "cash_delta_or_pnl": "12",
                "reason": "take profit",
            },
            {
                "ts": "2026-05-30T11:00:00+00:00",
                "action": "CLOSE",
                "market_id": "m-loss",
                "slug": "loss",
                "question": "Loss",
                "market_type": "temperature",
                "side": "NO",
                "token_id": "no",
                "shares": "10",
                "price": "0.20",
                "cash_delta_or_pnl": "-4",
                "reason": "stop",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-29T10:00:00+00:00",
                "market_id": "m1",
                "slug": "m1",
                "question": "Skipped",
                "market_type": "temperature",
                "side": "SKIP",
                "p_true": "0.5",
                "p_exec": "",
                "net_edge": "-999",
                "size_usd": "0",
                "size_shares": "0",
                "entry_fraction": "",
                "probability_stop_threshold": "",
                "model_fair_price": "",
                "target_exit_price": "",
                "market_heat_score": "",
                "reason": "edge below",
                "note": "",
            }
        ],
    )
    payload = build_dashboard_payload(
        Settings(
            state_path=str(state_path),
            trades_csv_path=str(trades_path),
            decisions_csv_path=str(decisions_path),
        )
    )

    assert payload["summary"]["realized_profit_usd"] == 12.0
    assert payload["summary"]["realized_loss_usd"] == 4.0
    assert "latest_forecast_at" not in payload["scanner"]
    assert payload["scanner"]["latest_station_at"] == ""


def test_dashboard_uses_korean_labels_and_tabbed_right_rail():
    assert "Cumulative candidate decisions" not in HTML
    assert "Forecast unavailable" not in HTML
    assert "Actual entries" not in HTML
    assert "YES/NO decisions" not in HTML
    assert "Open Positions" not in HTML
    assert "Recent Trades" not in HTML
    assert "Scanner Intelligence" not in HTML
    assert "보유 포지션" in HTML
    assert "총 진입 비용" in HTML
    assert "최근 관측 성공" in HTML
    assert "총 손익" in HTML
    assert "수익 현황" in HTML
    assert "손실 현황" in HTML
    assert "예보 상태 (Open-Meteo)" not in HTML
    assert "관측소 감시" in HTML
    assert "최근 체결" in HTML
    assert 'role="tablist"' in HTML
    assert 'id="scanner-panel"' in HTML
    assert 'id="trades-panel"' in HTML
    assert "Cumulative skips" not in HTML
    assert "매매가능현금" in HTML
    assert "NO FORECAST" not in HTML
    assert "Total Exposure" not in HTML
    assert "Recent Candidates" not in HTML
    assert "Event Stream" not in HTML
    assert 'data-range="1D"' in HTML
    assert 'id="chart-tooltip"' in HTML
    assert '"¢"' in HTML
    assert "YES 보유" not in HTML
    assert "NO 보유" not in HTML
    assert 'return "Yes";' in HTML
    assert 'return "No";' in HTML


def test_dashboard_payload_uses_runner_status_as_bot_heartbeat(tmp_path):
    state_path = tmp_path / "state.json"
    trades_path = tmp_path / "trades.csv"
    decisions_path = tmp_path / "decisions.csv"
    raw_path = tmp_path / "raw.jsonl"
    runner_status_path = tmp_path / "paper_runner_status.json"
    state_path.write_text(json.dumps({"cash_usd": 1000.0, "positions": []}), encoding="utf-8")
    write_csv(
        trades_path,
        [
            {
                "ts": "2026-05-24T10:00:00+00:00",
                "action": "OPEN",
                "market_id": "m1",
                "slug": "old",
                "question": "Old trade",
                "market_type": "temperature",
                "side": "YES",
                "token_id": "yes",
                "shares": "10",
                "price": "0.5",
                "cash_delta_or_pnl": "-5",
                "reason": "entry",
            },
        ],
    )
    write_csv(
        decisions_path,
        [
            {
                "ts": "2026-05-24T10:01:00+00:00",
                "market_id": "m2",
                "slug": "old-decision",
                "question": "Old decision",
                "market_type": "temperature",
                "side": "SKIP",
                "p_true": "0.5",
                "p_exec": "",
                "net_edge": "-999",
                "size_usd": "0",
                "size_shares": "0",
                "entry_fraction": "",
                "probability_stop_threshold": "",
                "model_fair_price": "",
                "target_exit_price": "",
                "market_heat_score": "",
                "reason": "old",
                "note": "",
            },
        ],
    )
    runner_status_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-05-24T10:05:00+00:00",
                "phase": "evaluating",
                "message": "evaluating 3/40",
                "markets_done": 3,
                "markets_total": 40,
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        bankroll_usd=1000.0,
        state_path=str(state_path),
        trades_csv_path=str(trades_path),
        decisions_csv_path=str(decisions_path),
        raw_snapshots_path=str(raw_path),
    )

    payload = build_dashboard_payload(settings)

    assert payload["bot"]["last_event_at"] == "2026-05-24T10:05:00+00:00"
    assert payload["bot"]["status"] == "STALE"
    assert payload["bot"]["phase"] == "evaluating"
    assert payload["bot"]["message"] == "evaluating 3/40"
    assert payload["bot"]["markets_done"] == 3
    assert payload["bot"]["markets_total"] == 40


def test_dashboard_payload_surfaces_station_and_websocket_health(tmp_path):
    state_path = tmp_path / "state.json"
    runner_status_path = tmp_path / "paper_runner_status.json"
    state_path.write_text(json.dumps({"cash_usd": 1000.0, "positions": []}), encoding="utf-8")
    runner_status_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-06-01T00:01:00+00:00",
                "phase": "stream_error",
                "message": "websocket thread stopped",
                "websocket": {
                    "thread_alive": False,
                    "reconnect_count": 3,
                    "last_message_at": "2026-06-01T00:00:30+00:00",
                    "last_book_at": "2026-06-01T00:00:20+00:00",
                    "stale_book_age_seconds": 40,
                    "stale": True,
                    "last_error": "RuntimeError: websocket stopped",
                },
                "discovery": {
                    "stream_tokens": 506,
                    "stream_markets": 253,
                    "stream_events": 23,
                    "stream_cities": 21,
                },
            }
        ),
        encoding="utf-8",
    )

    payload = build_dashboard_payload(Settings(state_path=str(state_path)))

    assert "forecast" not in payload["health"]
    assert payload["health"]["station"]["status"] == "WAITING"
    assert payload["health"]["websocket"]["status"] == "FAILED"
    assert payload["health"]["websocket"]["thread_alive"] is False
    assert payload["health"]["websocket"]["reconnect_count"] == 3
    assert payload["health"]["websocket"]["stream_tokens"] == 506
    assert payload["health"]["websocket"]["stream_markets"] == 253
    assert payload["bot"]["status"] == "FAILED"


def test_dashboard_health_waits_when_no_station_observation_or_stream_tokens(tmp_path):
    state_path = tmp_path / "state.json"
    runner_status_path = tmp_path / "paper_runner_status.json"
    state_path.write_text(json.dumps({"cash_usd": 200.0, "positions": []}), encoding="utf-8")
    runner_status_path.write_text(
        json.dumps(
            {
                "updated_at": "2099-06-14T00:01:00+00:00",
                "phase": "stream_waiting",
                "message": "no streamable temperature markets discovered",
                "markets_total": 0,
                "events_total": 0,
                "cities_total": 0,
                "websocket": {
                    "thread_alive": False,
                    "reconnect_count": 0,
                    "last_message_at": None,
                    "last_book_at": None,
                    "stale_book_age_seconds": None,
                    "stale": True,
                    "last_error": "",
                    "status_reason": "websocket receiver thread is not running",
                },
            }
        ),
        encoding="utf-8",
    )

    payload = build_dashboard_payload(Settings(state_path=str(state_path)))

    assert payload["health"]["station"]["status"] == "WAITING"
    assert payload["health"]["websocket"]["status"] == "WAITING"
    assert payload["health"]["websocket"]["status_reason"] == "no streamable temperature tokens"
    assert payload["bot"]["status"] == "WAIT"


def test_dashboard_open_positions_include_exit_liquidity_and_bid_depth_pnl(tmp_path):
    state_path = tmp_path / "state.json"
    runner_status_path = tmp_path / "paper_runner_status.json"
    state_path.write_text(
        json.dumps(
            {
                "cash_usd": 900.0,
                "positions": [
                    {
                        "position_id": "p-liq",
                        "market_id": "m-liq",
                        "question": "Will the highest temperature in Seoul be 27C or higher on June 14?",
                        "token_id": "yes",
                        "side": "YES",
                        "entry_price": 0.50,
                        "shares": 100.0,
                        "cost_usd": 50.0,
                        "opened_at": "2026-06-14T00:00:00+00:00",
                        "last_mark_price": 0.62,
                        "metadata": {
                            "city": "seoul",
                            "date_hint": "june 14",
                            "best_bid": 0.59,
                            "absorbable_shares": 40.0,
                            "can_fully_close": False,
                            "exit_slippage": 0.03,
                            "last_exit_trigger": "probability_stop",
                            "last_exit_blocker": "low_liquidity",
                            "last_exit_reason": "exit_trigger=probability_stop; low_liquidity",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    runner_status_path.write_text(
        json.dumps(
            {
                "updated_at": "2099-06-14T00:01:00+00:00",
                "phase": "streaming",
                "websocket": {
                    "thread_alive": True,
                    "last_book_at": "2099-06-14T00:00:50+00:00",
                    "stale_book_age_seconds": 10,
                    "stale": False,
                },
            }
        ),
        encoding="utf-8",
    )

    payload = build_dashboard_payload(Settings(state_path=str(state_path), weather_taker_fee_rate=0.02))

    position = payload["positions"][0]
    assert position["reference_market_value"] == pytest.approx(61.5288)
    assert position["reference_unrealized_pnl"] == pytest.approx(11.5288)
    assert position["exit_best_bid"] == pytest.approx(0.59)
    assert position["exit_available_shares"] == pytest.approx(40.0)
    assert position["exit_full_vwap"] is None
    assert position["exit_half_vwap"] is None
    assert position["exit_liquidity_status"] == "partial"
    assert position["exit_blocker"] == "low_liquidity"
    assert position["bid_depth_market_value"] == pytest.approx(23.40648)
    assert position["bid_depth_unrealized_pnl"] == pytest.approx(-26.59352)
    assert position["websocket_status"] == "HEALTHY"
    assert position["websocket_stale"] is False
    assert "exit_liquidity_status" in HTML
    assert "bid_depth_unrealized_pnl" in HTML


def test_dashboard_html_explains_health_warnings():
    assert "공식 관측소 수신 상태" in HTML
    assert "마지막 성공" in HTML
    assert "실시간 주문장 상태" in HTML
    assert "재연결" in HTML
    assert "마지막 주문장" in HTML


def test_dashboard_html_uses_station_age_from_api_payload():
    assert "const ttl = 10800;" not in HTML
    assert "stationHealth.age_seconds" in HTML
    assert "grid-template-columns: repeat(5, minmax(0, 1fr))" in HTML
    assert "white-space: nowrap" in HTML
