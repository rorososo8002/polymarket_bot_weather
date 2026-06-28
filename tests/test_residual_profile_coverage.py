from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from weather_bot.residual_probability import ResidualProfileStore
from weather_bot.residual_profile_builder import (
    HistoricalTemperatureObservation,
    build_residual_profiles,
)
from weather_bot.stations import TRADING_READY_STATION_MAP


PROFILE_PATH = Path("strategy_data/station_residual_profiles.json")
MANIFEST_PATH = Path("strategy_data/station_residual_profiles.manifest.json")
REQUESTED_PLAN_YEARS = [2021, 2022, 2023, 2024, 2025]
ACTUAL_GENERATION_YEARS = [2020, 2021, 2022, 2023, 2024]
ALLOWED_STATUSES = {
    "calibrated",
    "insufficient_history",
    "unsupported_archive_mapping",
    "precision_needs_audit",
}
AUDIT_REGIONS = {
    "asia_oceania",
    "europe_middle_east_africa",
    "americas",
}


def _load_json(path: Path) -> dict[str, object]:
    assert path.is_file(), f"required strategy artifact is missing: {path}"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _canonical_sha256(value: object) -> str:
    content = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _profiles_by_station(profile_payload: dict[str, object]) -> dict[str, dict[str, object]]:
    raw_profiles = profile_payload["profiles"]
    assert isinstance(raw_profiles, dict)
    grouped: dict[str, dict[str, object]] = defaultdict(dict)
    for key, profile in raw_profiles.items():
        assert isinstance(key, str)
        assert isinstance(profile, dict)
        station_id = profile["station_id"]
        assert isinstance(station_id, str)
        grouped[station_id][key] = profile
    return dict(grouped)


def test_artifact_and_manifest_cover_every_trading_ready_station_exactly_once() -> None:
    profile_payload = _load_json(PROFILE_PATH)
    manifest = _load_json(MANIFEST_PATH)

    assert profile_payload["schema_version"] == 1
    assert profile_payload["generation_years"] == ACTUAL_GENERATION_YEARS
    assert manifest["schema_version"] == 1
    assert manifest["requested_plan_years"] == REQUESTED_PLAN_YEARS
    assert manifest["actual_generation_years"] == ACTUAL_GENERATION_YEARS
    assert manifest["profile_artifact_sha256"] == _file_sha256(PROFILE_PATH)
    assert manifest["source_catalog"]["url"] == (
        "https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv"
    )
    assert manifest["source_catalog"]["sha256"]
    assert manifest["source_api_template"].startswith(
        "https://www.ncei.noaa.gov/"
    )

    stations = manifest["stations"]
    assert isinstance(stations, dict)
    assert set(stations) == set(TRADING_READY_STATION_MAP)

    seen_station_ids: set[str] = set()
    for city, station in TRADING_READY_STATION_MAP.items():
        entry = stations[city]
        assert isinstance(entry, dict)
        assert entry["city"] == city
        assert entry["station_id"] == station.station_id
        assert entry["status"] in ALLOWED_STATUSES
        assert entry["station_id"] not in seen_station_ids
        seen_station_ids.add(entry["station_id"])


def test_manifest_status_counts_keep_all_mapped_metar_stations_calibrated() -> None:
    manifest = _load_json(MANIFEST_PATH)

    assert manifest["station_status_counts"] == {
        "calibrated": 44,
        "insufficient_history": 0,
        "precision_needs_audit": 1,
        "unsupported_archive_mapping": 2,
    }


def test_hko_remains_precision_needs_audit_and_cannot_use_concentrated_sizing() -> None:
    manifest = _load_json(MANIFEST_PATH)
    stations = manifest["stations"]
    assert isinstance(stations, dict)
    hko = stations["hong kong"]
    assert isinstance(hko, dict)

    assert hko["station_id"] == "HKO"
    assert hko["status"] == "precision_needs_audit"
    assert hko["concentrated_sizing_eligible"] is False
    assert hko["profile_count"] == 0
    assert hko["reason"]


def test_calibrated_metar_manifest_metadata_matches_profile_content() -> None:
    profile_payload = _load_json(PROFILE_PATH)
    manifest = _load_json(MANIFEST_PATH)
    stations = manifest["stations"]
    assert isinstance(stations, dict)
    grouped_profiles = _profiles_by_station(profile_payload)

    calibrated_station_ids: set[str] = set()
    for city, station in TRADING_READY_STATION_MAP.items():
        entry = stations[city]
        assert isinstance(entry, dict)
        if entry["status"] != "calibrated":
            assert station.station_id not in grouped_profiles
            assert entry["profile_count"] == 0
            assert entry["reason"]
            continue

        assert station.nowcast_source_type == "metar"
        calibrated_station_ids.add(station.station_id)
        station_profiles = grouped_profiles[station.station_id]
        scopes = {profile["scope"] for profile in station_profiles.values()}
        sample_days = [profile["sample_days"] for profile in station_profiles.values()]

        assert entry["source_years"] == ACTUAL_GENERATION_YEARS
        assert entry["timezone"] == station.timezone
        assert entry["unit"] == ("C" if station.temperature_unit == "celsius" else "F")
        assert entry["profile_count"] == len(station_profiles)
        assert entry["month_keys"]
        assert entry["season_keys"]
        assert entry["month_keys"] == sorted(
            scope for scope in scopes if str(scope).startswith("month:")
        )
        assert entry["season_keys"] == sorted(
            scope for scope in scopes if str(scope).startswith("season:")
        )
        assert entry["sample_day_counts"] == {
            "minimum": min(sample_days),
            "maximum": max(sample_days),
            "profile_count": len(sample_days),
        }
        assert len(entry["ncei_station_id"]) == 11
        assert entry["ncei_station_id"].isdigit()
        assert entry["ncei_station_name"]
        assert entry["ncei_record_begin"]
        assert entry["ncei_record_end"]
        assert entry["archive_mapping_method"].startswith("exact_icao_")
        assert entry["profile_content_sha256"] == _canonical_sha256(station_profiles)
        assert entry["concentrated_sizing_eligible"] is False
        assert entry["concentrated_sizing_reason"]

        source_files = entry["source_files"]
        assert [source_file["year"] for source_file in source_files] == (
            ACTUAL_GENERATION_YEARS
        )
        assert all(
            source_file["source_url"].startswith("https://www.ncei.noaa.gov/")
            for source_file in source_files
        )
        boundary_buffers = entry["boundary_buffer_files"]
        assert [item["date"] for item in boundary_buffers] == [
            "2019-12-31",
            "2025-01-01",
        ]
        assert all(
            item["source_url"].startswith("https://www.ncei.noaa.gov/")
            and item["sha256"]
            and item["valid_observations"] > 0
            for item in boundary_buffers
        )

    assert set(grouped_profiles) == calibrated_station_ids


def test_profile_schema_histograms_and_cdfs_are_internally_consistent() -> None:
    profile_payload = _load_json(PROFILE_PATH)
    store = ResidualProfileStore.from_path(PROFILE_PATH)

    assert store.load_reason_code == ""
    assert store.generation_years == tuple(ACTUAL_GENERATION_YEARS)
    assert PROFILE_PATH.stat().st_size <= 10 * 1024 * 1024

    profiles = profile_payload["profiles"]
    assert isinstance(profiles, dict)
    assert profiles
    for key, profile in profiles.items():
        assert isinstance(key, str)
        assert isinstance(profile, dict)
        histogram = profile["histogram"]
        assert isinstance(histogram, list)
        assert histogram

        cumulative = 0
        prior_cdf = 0.0
        prior_residual = -1.0
        for residual, count in histogram:
            assert residual >= 0
            assert residual > prior_residual
            assert isinstance(count, int)
            assert count >= 0
            cumulative += count
            cdf = cumulative / profile["sample_days"]
            assert prior_cdf <= cdf <= 1.0
            prior_residual = residual
            prior_cdf = cdf

        assert cumulative == profile["sample_days"]
        assert prior_cdf == 1.0


def test_published_profiles_start_at_each_audited_monitoring_window() -> None:
    profile_payload = _load_json(PROFILE_PATH)
    manifest = _load_json(MANIFEST_PATH)
    stations = manifest["stations"]
    assert isinstance(stations, dict)
    station_entries = {
        entry["station_id"]: entry
        for entry in stations.values()
        if isinstance(entry, dict) and entry["status"] == "calibrated"
    }
    profiles = profile_payload["profiles"]
    assert isinstance(profiles, dict)

    for profile in profiles.values():
        assert isinstance(profile, dict)
        entry = station_entries[profile["station_id"]]
        window_key = f"{profile['scope']}|{profile['direction']}"
        monitoring_start = entry["monitoring_windows"][window_key][
            "monitoring_start_local_minute"
        ]
        assert profile["local_minute"] >= monitoring_start


def test_seoul_and_each_required_region_have_an_audited_calibrated_station() -> None:
    profile_payload = _load_json(PROFILE_PATH)
    manifest = _load_json(MANIFEST_PATH)
    stations = manifest["stations"]
    audits = manifest["regional_audits"]
    assert isinstance(stations, dict)
    assert isinstance(audits, dict)

    assert TRADING_READY_STATION_MAP["seoul"].station_id == "RKSI"
    assert stations["seoul"]["station_id"] == "RKSI"
    assert stations["seoul"]["status"] == "calibrated"
    assert set(audits) == AUDIT_REGIONS

    grouped_profiles = _profiles_by_station(profile_payload)
    for region, audit in audits.items():
        assert region in AUDIT_REGIONS
        assert isinstance(audit, dict)
        city = audit["city"]
        station_id = audit["station_id"]
        assert stations[city]["status"] == "calibrated"
        assert stations[city]["station_id"] == station_id
        assert station_id in grouped_profiles
        assert audit["source_url"].startswith("https://www.ncei.noaa.gov/")
        assert audit["source_years"] == ACTUAL_GENERATION_YEARS
        assert audit["checked"] is True


def test_manifest_explains_why_incomplete_2025_was_replaced_with_2020() -> None:
    manifest = _load_json(MANIFEST_PATH)
    exclusions = manifest["year_exclusions"]
    assert isinstance(exclusions, list)
    assert exclusions == [
        {
            "year": 2025,
            "reason": "incomplete_official_station_record",
            "station_id": "RKSI",
            "ncei_station_id": "47113199999",
            "station_history_end": "2025-08-24",
            "last_observation_utc": "2025-08-24T21:30:00",
            "replacement_complete_year": 2020,
        }
    ]


def test_seoul_archive_mapping_is_official_rksi_not_seoul_ab() -> None:
    manifest = _load_json(MANIFEST_PATH)
    stations = manifest["stations"]
    assert isinstance(stations, dict)
    seoul = stations["seoul"]
    assert isinstance(seoul, dict)

    assert seoul["station_id"] == "RKSI"
    assert seoul["ncei_station_id"] == "47113199999"
    assert seoul["ncei_station_name"] == "INCHEON INTL"
    assert seoul["ncei_record_end"] == "2025-08-24"
    assert seoul["ncei_station_id"] != "47111099999"


def test_seoul_june_high_has_1430_or_later_profiles() -> None:
    profile_payload = _load_json(PROFILE_PATH)
    grouped_profiles = _profiles_by_station(profile_payload)
    seoul_profiles = grouped_profiles["RKSI"]

    june_high_minutes = {
        profile["local_minute"]
        for profile in seoul_profiles.values()
        if profile["scope"] == "month:06" and profile["direction"] == "high"
    }
    assert any(local_minute >= 14 * 60 + 30 for local_minute in june_high_minutes)


def test_builder_assigns_utc_midnight_observations_to_seoul_local_date() -> None:
    seoul = TRADING_READY_STATION_MAP["seoul"]
    observations = [
        HistoricalTemperatureObservation(
            station_id="RKSI",
            observed_at=datetime(2021, 5, 31, 14, 30, tzinfo=timezone.utc),
            temperature_c=20.0,
        ),
        HistoricalTemperatureObservation(
            station_id="RKSI",
            observed_at=datetime(2021, 5, 31, 15, 0, tzinfo=timezone.utc),
            temperature_c=21.0,
        ),
        HistoricalTemperatureObservation(
            station_id="RKSI",
            observed_at=datetime(2021, 6, 1, 0, 0, tzinfo=timezone.utc),
            temperature_c=22.0,
        ),
    ]

    payload = build_residual_profiles(
        observations,
        {"seoul": seoul},
        years=(2021,),
        min_sample_days=1,
    )
    profiles = payload["profiles"]
    assert isinstance(profiles, dict)

    assert "RKSI|month:05|1410|high|C" in profiles
    assert "RKSI|month:06|0000|high|C" in profiles
    assert profiles["RKSI|month:06|0000|high|C"]["sample_days"] == 1
