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


def provider_for(payload, *, freshness_seconds: int = 5400):
    calls = []

    def fake_get(url, *, params, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        return FakeResponse(payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        freshness_seconds=freshness_seconds,
        cache_ttl_seconds=0,
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
    assert calls[0]["params"]["ids"] == "RKSI"
    assert calls[0]["params"]["format"] == "json"


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
    assert calls[0]["params"]["ids"] == "KLGA"


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


def hko_provider_for(payload: str, *, freshness_seconds: int = 5400):
    calls = []

    def fake_get(url, *, params=None, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        return FakeResponse(payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        freshness_seconds=freshness_seconds,
        cache_ttl_seconds=0,
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
    assert observation.freshness_seconds == 900
    assert observation.source == "hko-maxmin-since-midnight"
    assert "latest_since_midnight_maxmin.csv" in calls[0]["url"]


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
