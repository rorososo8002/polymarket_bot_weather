from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from weather_bot.nowcast import AviationWeatherMetarNowcastProvider, DEFAULT_NOWCAST_SOURCES
from weather_bot.stations import STATION_MAP, station_audit_rows


FIXTURES = Path(__file__).parent / "fixtures" / "nowcast"


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload

    @property
    def text(self):
        if isinstance(self._payload, str):
            return self._payload
        return json.dumps(self._payload)


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_text_fixture(name: str):
    return (FIXTURES / name).read_text(encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def provider_for(
    payload,
    *,
    freshness_seconds: int = 5400,
    cache_ttl_seconds: int = 0,
    request_log_path: Path | None = None,
):
    calls = []

    def fake_get(url, *, params, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        return FakeResponse(payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        freshness_seconds=freshness_seconds,
        cache_ttl_seconds=cache_ttl_seconds,
        request_log_path=request_log_path,
    )
    return provider, calls


def test_aviationweather_provider_returns_fresh_station_high_from_fixture():
    provider, calls = provider_for(load_fixture("aviationweather_rksi_fresh.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.observed_high_c == 26.7
    assert observation.observed_at.isoformat() == "2026-06-02T08:00:00+00:00"
    assert observation.high_observed_at.isoformat() == "2026-06-02T08:00:00+00:00"
    assert observation.freshness_seconds == 1800
    assert observation.source == "aviationweather-metar"
    assert observation.unavailable_reason == ""
    assert observation.raw_observation_count == 3
    requested_ids = set(calls[0]["params"]["ids"].split(","))
    assert "RKSI" in requested_ids
    assert "HKO" not in requested_ids
    assert calls[0]["params"]["format"] == "json"


def test_aviationweather_provider_returns_high_and_low_from_one_cached_fetch():
    provider, calls = provider_for(load_fixture("aviationweather_rksi_fresh.json"), cache_ttl_seconds=900)

    high_observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )
    low_observation = provider.observed_low_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 31, tzinfo=timezone.utc),
    )

    assert high_observation.observed_high_c == 26.7
    assert low_observation.observed_low_c == 18.0
    assert low_observation.low_observed_at.isoformat() == "2026-06-02T00:00:00+00:00"
    assert len(calls) == 1


def test_aviationweather_provider_prefetches_multiple_metar_stations_once_per_refresh():
    payload = [
        {
            "icaoId": "KLGA",
            "obsTime": "2026-06-02T10:00:00.000Z",
            "temp": 19.4,
            "rawOb": "KLGA 021000Z 21008KT 10SM FEW050 19/14 A2992 RMK T01940140",
        },
        {
            "icaoId": "KLGA",
            "obsTime": "2026-06-02T18:00:00.000Z",
            "temp": 25.6,
            "rawOb": "KLGA 021800Z 22009KT 10SM FEW050 26/14 A2991",
        },
        {
            "icaoId": "KATL",
            "obsTime": "2026-06-02T11:00:00.000Z",
            "temp": 21.1,
            "rawOb": "KATL 021100Z 23005KT 10SM FEW030 21/16 A3002 RMK T02110160",
        },
        {
            "icaoId": "KATL",
            "obsTime": "2026-06-02T18:00:00.000Z",
            "temp": 29.4,
            "rawOb": "KATL 021800Z 24007KT 10SM FEW040 29/17 A2999 RMK T02940170",
        },
    ]
    provider, calls = provider_for(payload, cache_ttl_seconds=900)
    now = datetime(2026, 6, 2, 18, 30, tzinfo=timezone.utc)

    nyc_observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["nyc"],
        target_date=date(2026, 6, 2),
        now=now,
    )
    atlanta_observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["atlanta"],
        target_date=date(2026, 6, 2),
        now=now,
    )

    assert nyc_observation.observed_high_c == 25.6
    assert nyc_observation.observed_low_c == 19.4
    assert atlanta_observation.observed_high_c == 29.4
    assert atlanta_observation.observed_low_c == 21.1
    assert len(calls) == 1
    requested_ids = set(calls[0]["params"]["ids"].split(","))
    assert {"KLGA", "KATL", "RKSI"}.issubset(requested_ids)


def test_aviationweather_provider_rejects_bulk_row_without_station_id():
    payload = [
        {
            "obsTime": "2026-06-02T18:00:00.000Z",
            "temp": 25.6,
            "rawOb": "KLGA 021800Z 22009KT 10SM FEW050 26/14 A2991",
        }
    ]
    provider, _calls = provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["nyc"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 18, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_low_c is None
    assert observation.unavailable_reason == "malformed-observation-payload"


def test_aviationweather_request_log_records_external_fetch_not_cache_hit(tmp_path):
    request_log_path = tmp_path / "station_nowcast_request_log.jsonl"
    provider, calls = provider_for(
        load_fixture("aviationweather_rksi_fresh.json"),
        cache_ttl_seconds=900,
        request_log_path=request_log_path,
    )

    provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )
    provider.observed_low_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 31, tzinfo=timezone.utc),
    )

    rows = read_jsonl(request_log_path)

    assert len(calls) == 1
    assert len(rows) == 1
    assert rows[0]["city"] == "bulk-metar"
    assert rows[0]["station_id"] == "METAR_BULK"
    assert rows[0]["station_name"] == "Aviation Weather Center METAR bulk prefetch"
    assert rows[0]["request_mode"] == "awc_metar_bulk_cache"
    assert rows[0]["trigger_city"] == "seoul"
    assert rows[0]["trigger_station_id"] == STATION_MAP["seoul"].station_id
    assert STATION_MAP["seoul"].station_id in rows[0]["requested_station_ids"]
    assert rows[0]["source"] == "aviationweather-metar"
    assert rows[0]["status"] == "success"
    assert rows[0]["status_code"] == 200
    assert rows[0]["cache_miss_reason"] == "empty-cache"
    assert rows[0]["requested_at"] == "2026-06-02T08:30:00+00:00"


def test_aviationweather_bulk_request_floor_is_five_minutes_even_when_station_cache_is_short(tmp_path):
    request_log_path = tmp_path / "station_nowcast_request_log.jsonl"
    provider, calls = provider_for(
        load_fixture("aviationweather_rksi_fresh.json"),
        cache_ttl_seconds=60,
        request_log_path=request_log_path,
    )

    provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )
    provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 32, tzinfo=timezone.utc),
    )
    provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 36, tzinfo=timezone.utc),
    )

    rows = read_jsonl(request_log_path)
    assert len(calls) == 2
    assert len(rows) == 2
    assert rows[0]["requested_at"] == "2026-06-02T08:30:00+00:00"
    assert rows[1]["requested_at"] == "2026-06-02T08:36:00+00:00"


def test_aviationweather_provider_supports_verified_icao_station_beyond_seoul():
    provider, calls = provider_for(load_fixture("aviationweather_klga_fresh.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["nyc"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 18, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.station_id == "KLGA"
    assert observation.observed_high_c == 25.6
    assert observation.source == "aviationweather-metar"
    requested_ids = set(calls[0]["params"]["ids"].split(","))
    assert "KLGA" in requested_ids


def test_aviationweather_provider_marks_stale_observations_unusable():
    provider, _calls = provider_for(load_fixture("aviationweather_rksi_stale.json"), freshness_seconds=3600)

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c == 24.0
    assert observation.freshness_seconds == 10800
    assert observation.unavailable_reason == "stale-observation"


def test_aviationweather_provider_marks_malformed_payload_unusable():
    provider, _calls = provider_for(load_fixture("aviationweather_rksi_malformed.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_at is None
    assert observation.unavailable_reason == "malformed-observation-payload"


def test_aviationweather_provider_fails_closed_on_future_observation():
    payload = [
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-06T15:40:00.000Z",
            "temp": 31.0,
            "rawOb": "RJTT 061540Z 19008KT 9999 FEW025 31/20 Q1008",
        }
    ]
    provider, _calls = provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 7),
        now=datetime(2026, 6, 6, 15, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_low_c is None
    assert observation.unavailable_reason == "future-observation"


def test_aviationweather_provider_fails_closed_when_target_date_is_not_today():
    provider, calls = provider_for(load_fixture("aviationweather_rksi_fresh.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 3),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.unavailable_reason == "target-date-not-today"
    assert calls == []


def test_aviationweather_provider_uses_fresh_yesterday_extremes_after_local_midnight():
    payload = [
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-05T15:10:00.000Z",
            "temp": 20.0,
            "rawOb": "RJTT 051510Z 18005KT 9999 FEW020 20/16 Q1010",
        },
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-06T06:00:00.000Z",
            "temp": 31.0,
            "rawOb": "RJTT 060600Z 19008KT 9999 FEW025 31/20 Q1008",
        },
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-06T14:50:00.000Z",
            "temp": 26.0,
            "rawOb": "RJTT 061450Z 16005KT 9999 FEW020 26/19 Q1009",
        },
    ]
    provider, calls = provider_for(payload, freshness_seconds=5400)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 15, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.station_id == "RJTT"
    assert observation.observed_high_c == 31.0
    assert observation.observed_low_c == 20.0
    assert observation.observed_at.isoformat() == "2026-06-06T14:50:00+00:00"
    assert observation.freshness_seconds == 2400
    assert observation.unavailable_reason == ""
    assert calls[0]["params"]["hoursBeforeNow"] >= 25


def test_aviationweather_bulk_cache_refetches_when_yesterday_needs_longer_lookback():
    yesterday_payload = [
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-06T06:00:00.000Z",
            "temp": 31.0,
            "rawOb": "RJTT 060600Z 19008KT 9999 FEW025 31/20 Q1008",
        },
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-06T14:50:00.000Z",
            "temp": 26.0,
            "rawOb": "RJTT 061450Z 16005KT 9999 FEW020 26/19 Q1009",
        },
    ]
    calls = []
    payloads = [[], yesterday_payload]

    def fake_get(url, *, params, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        return FakeResponse(payloads.pop(0))

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        freshness_seconds=5400,
        cache_ttl_seconds=900,
    )

    first = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 7),
        now=datetime(2026, 6, 6, 15, 5, tzinfo=timezone.utc),
    )
    second = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 15, 6, tzinfo=timezone.utc),
    )

    assert first.usable is False
    assert second.usable is True
    assert second.observed_high_c == 31.0
    assert len(calls) == 2
    assert calls[1]["params"]["hoursBeforeNow"] > calls[0]["params"]["hoursBeforeNow"]


def test_aviationweather_provider_fails_closed_when_yesterday_window_expired():
    provider, calls = provider_for([], freshness_seconds=3600)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 16, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_low_c is None
    assert observation.unavailable_reason == "target-date-post-close-window-expired"
    assert calls == []


def test_aviationweather_provider_skips_station_without_verified_observation_source():
    provider, calls = provider_for(load_fixture("aviationweather_rksi_fresh.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["karachi"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.unavailable_reason == "nowcast-source-unmapped"
    assert calls == []


def hko_provider_for(
    payload: str,
    *,
    freshness_seconds: int = 5400,
    cache_ttl_seconds: int = 0,
    request_log_path: Path | None = None,
):
    calls = []

    def fake_get(url, *, params=None, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        return FakeResponse(payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        freshness_seconds=freshness_seconds,
        cache_ttl_seconds=cache_ttl_seconds,
        request_log_path=request_log_path,
    )
    return provider, calls


def test_hko_provider_returns_max_temperature_since_midnight_from_fixture():
    provider, calls = hko_provider_for(load_text_fixture("hko_maxmin_fresh.csv"))

    observation = provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.station_id == "HKO"
    assert observation.observed_high_c == 30.0
    assert observation.observed_at.isoformat() == "2026-06-02T03:30:00+00:00"
    assert observation.high_observed_at is None
    assert observation.observed_low_c == 27.6
    assert observation.freshness_seconds == 900
    assert observation.source == "hko-maxmin-since-midnight"
    assert "latest_since_midnight_maxmin.csv" in calls[0]["url"]


def test_hko_provider_uses_fresh_yesterday_extremes_after_local_midnight():
    payload = """Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),Minimum Air Temperature Since Midnight(degree Celsius)
202606062350,HK Observatory,30.2,27.1
"""
    provider, calls = hko_provider_for(payload, freshness_seconds=5400)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 16, 20, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.station_id == "HKO"
    assert observation.observed_high_c == 30.2
    assert observation.observed_low_c == 27.1
    assert observation.observed_at.isoformat() == "2026-06-06T15:50:00+00:00"
    assert observation.freshness_seconds == 1800
    assert observation.unavailable_reason == ""
    assert len(calls) == 1


def test_hko_request_log_records_external_fetch_not_cache_hit(tmp_path):
    request_log_path = tmp_path / "station_nowcast_request_log.jsonl"
    provider, calls = hko_provider_for(
        load_text_fixture("hko_maxmin_fresh.csv"),
        cache_ttl_seconds=900,
        request_log_path=request_log_path,
    )

    provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
    )
    provider.observed_low_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 46, tzinfo=timezone.utc),
    )

    rows = read_jsonl(request_log_path)

    assert len(calls) == 1
    assert len(rows) == 1
    assert rows[0]["city"] == "hong kong"
    assert rows[0]["station_id"] == STATION_MAP["hong kong"].station_id
    assert rows[0]["source"] == "hko-maxmin-since-midnight"
    assert rows[0]["status"] == "success"
    assert rows[0]["status_code"] == 200
    assert rows[0]["cache_miss_reason"] == "empty-cache"
    assert rows[0]["requested_at"] == "2026-06-02T03:45:00+00:00"


def test_hko_request_floor_is_ten_minutes_even_when_station_cache_is_short(tmp_path):
    request_log_path = tmp_path / "station_nowcast_request_log.jsonl"
    provider, calls = hko_provider_for(
        load_text_fixture("hko_maxmin_fresh.csv"),
        cache_ttl_seconds=60,
        request_log_path=request_log_path,
    )

    provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
    )
    provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 50, tzinfo=timezone.utc),
    )
    provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 56, tzinfo=timezone.utc),
    )

    rows = read_jsonl(request_log_path)
    assert len(calls) == 2
    assert len(rows) == 2
    assert rows[0]["requested_at"] == "2026-06-02T03:45:00+00:00"
    assert rows[1]["requested_at"] == "2026-06-02T03:56:00+00:00"


def test_hko_provider_marks_malformed_csv_unusable():
    provider, _calls = hko_provider_for(load_text_fixture("hko_maxmin_malformed.csv"))

    observation = provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.unavailable_reason == "malformed-observation-payload"


def test_hko_provider_fails_closed_on_future_observation():
    payload = """Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),Minimum Air Temperature Since Midnight(degree Celsius)
202606062350,HK Observatory,30.2,27.1
"""
    provider, _calls = hko_provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 15, 20, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_low_c is None
    assert observation.unavailable_reason == "future-observation"


def test_station_nowcast_audit_marks_enabled_and_skipped_sources():
    rows = {row["city"]: row for row in station_audit_rows()}

    assert rows["hong kong"]["nowcast_source_type"] == "hko_maxmin_since_midnight"
    assert rows["hong kong"]["nowcast_provider_status"] == "provider_enabled"
    assert rows["seoul"]["nowcast_provider_status"] == "provider_enabled"
    assert rows["nyc"]["nowcast_provider_status"] == "provider_enabled"
    assert rows["karachi"]["nowcast_provider_status"] == "provider_unavailable"


def test_default_nowcast_sources_match_enabled_station_registry():
    enabled_station_ids = {
        str(row["station_id"])
        for row in station_audit_rows()
        if row["nowcast_provider_status"] == "provider_enabled"
    }

    assert set(DEFAULT_NOWCAST_SOURCES) == enabled_station_ids
    assert "HKO" in DEFAULT_NOWCAST_SOURCES
    assert "OPMR" not in DEFAULT_NOWCAST_SOURCES
