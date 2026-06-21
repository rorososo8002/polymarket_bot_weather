from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weather_bot.residual_probability import ResidualProfileStore
from weather_bot.residual_profile_builder import (
    HistoricalTemperatureObservation,
    ProfileBuildError,
    build_residual_profiles,
    iter_global_hourly_observations,
    load_station_history_catalog,
    main,
    parse_global_hourly_observations,
    publish_residual_profiles,
)
from weather_bot.stations import StationMeta


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ncei"
CATALOG_PATH = FIXTURE_DIR / "isd-history-small.csv"
HOURLY_PATH = FIXTURE_DIR / "rksi-global-hourly-small.csv"


def _stations() -> dict[str, StationMeta]:
    return {
        "seoul": StationMeta(
            city="seoul",
            station_id="RKSI",
            station_name="Incheon International Airport",
            latitude=37.46,
            longitude=126.44,
            timezone="Asia/Seoul",
        )
    }


def _observations() -> list[HistoricalTemperatureObservation]:
    catalog = load_station_history_catalog(CATALOG_PATH)
    return list(iter_global_hourly_observations(HOURLY_PATH, catalog, station_ids={"RKSI"}))


def test_historical_temperature_observation_is_frozen() -> None:
    observation = HistoricalTemperatureObservation(
        station_id="RKSI",
        observed_at=datetime(2021, 6, 1, tzinfo=timezone.utc),
        temperature_c=23.4,
    )

    with pytest.raises(FrozenInstanceError):
        observation.temperature_c = 99.0  # type: ignore[misc]


def test_station_history_catalog_maps_exact_icao_station() -> None:
    catalog = load_station_history_catalog(CATALOG_PATH)

    assert catalog["RKSI"].ncei_station_id == "47111099999"
    assert catalog["RKSS"].ncei_station_id == "47110099999"


def test_station_history_catalog_conflicting_duplicate_fails_closed(tmp_path: Path) -> None:
    catalog_path = tmp_path / "isd-history-conflict.csv"
    catalog_path.write_text(
        "\n".join(
            [
                "USAF,WBAN,STATION NAME,CTRY,STATE,ICAO,LAT,LON,ELEV(M),BEGIN,END",
                "471110,99999,INCHEON INTERNATIONAL AIRPORT,KS,,RKSI,37.46,126.44,7.0,20010101,20261231",
                "999999,99999,CONFLICTING STATION,KS,,RKSI,37.00,126.00,7.0,20010101,20261231",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProfileBuildError, match="conflicting"):
        load_station_history_catalog(catalog_path)


def test_station_history_catalog_duplicate_same_mapping_fails_closed(tmp_path: Path) -> None:
    catalog_path = tmp_path / "isd-history-duplicate.csv"
    catalog_path.write_text(
        "\n".join(
            [
                "USAF,WBAN,STATION NAME,CTRY,STATE,ICAO,LAT,LON,ELEV(M),BEGIN,END",
                "471110,99999,INCHEON INTERNATIONAL AIRPORT,KS,,RKSI,37.46,126.44,7.0,20010101,20261231",
                "471110,99999,INCHEON INTERNATIONAL AIRPORT COPY,KS,,RKSI,37.46,126.44,7.0,20010101,20261231",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProfileBuildError, match="duplicate"):
        load_station_history_catalog(catalog_path)


def test_global_hourly_parser_accepts_only_exact_station_and_rejects_bad_tmp_rows() -> None:
    observations = _observations()

    assert len(observations) == 10
    assert {observation.station_id for observation in observations} == {"RKSI"}
    assert {observation.temperature_c for observation in observations}.isdisjoint({30.0, 90.0})
    assert all(observation.observed_at.tzinfo == timezone.utc for observation in observations)


def test_global_hourly_parser_rejects_malformed_tmp_quality_fields() -> None:
    catalog = load_station_history_catalog(CATALOG_PATH)
    observations = list(
        parse_global_hourly_observations(
            "\n".join(
                [
                    "STATION,DATE,TMP",
                    '47111099999,2021-06-01T00:00:00,"+0230,1"',
                    '47111099999,2021-06-01T00:15:00,"+0235,5"',
                    '47111099999,2021-06-01T00:30:00,"+0240,X"',
                    '47111099999,2021-06-01T01:00:00,"+0250,1,extra"',
                ]
            ),
            catalog,
            station_ids={"RKSI"},
        )
    )

    assert [observation.temperature_c for observation in observations] == [23.0, 23.5]


def test_iter_global_hourly_observations_streams_file_without_read_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import weather_bot.residual_profile_builder as builder

    catalog = load_station_history_catalog(CATALOG_PATH)

    def fail_read_text(*args: object, **kwargs: object) -> str:
        raise AssertionError("hourly CSV rows must be streamed instead of read_text-loaded")

    monkeypatch.setattr(builder.Path, "read_text", fail_read_text)

    observations = list(iter_global_hourly_observations(HOURLY_PATH, catalog, station_ids={"RKSI"}))

    assert len(observations) == 10


def test_build_profiles_uses_station_local_days_residuals_and_occurrence_metadata() -> None:
    payload = build_residual_profiles(
        _observations(),
        _stations(),
        years=(2021,),
        min_sample_days=2,
    )
    profiles = payload["profiles"]
    assert isinstance(profiles, dict)

    high = profiles["RKSI|month:06|0870|high|C"]
    low = profiles["RKSI|month:06|0390|low|C"]
    season_high = profiles["RKSI|season:JJA|0870|high|C"]

    assert high["sample_days"] == 2
    assert high["histogram"] == [[2.0, 2]]
    assert low["sample_days"] == 2
    assert low["histogram"] == [[0.0, 2]]
    assert season_high["histogram"] == [[2.0, 2]]
    assert "RKSI|month:05|0000|high|C" not in profiles

    metadata = high["metadata"]
    assert metadata["monitoring_start_local_minute"] == 14 * 60 + 30
    assert metadata["first_final_high_local_minute"]["median"] == 15 * 60
    assert metadata["first_final_high_local_minute"]["q25"] == 15 * 60
    assert metadata["first_final_high_local_minute"]["q75"] == 15 * 60

    low_metadata = low["metadata"]
    assert low_metadata["monitoring_start_local_minute"] == 4 * 60 + 30
    assert low_metadata["first_final_low_local_minute"]["median"] == 6 * 60 + 30


def test_high_monitoring_start_uses_fixed_thirty_minutes_before_median_with_hour_bins() -> None:
    payload = build_residual_profiles(
        _observations(),
        _stations(),
        years=(2021,),
        interval_minutes=60,
        min_sample_days=2,
    )
    profiles = payload["profiles"]
    assert isinstance(profiles, dict)

    high = profiles["RKSI|month:06|0840|high|C"]

    assert high["metadata"]["first_final_high_local_minute"]["median"] == 15 * 60
    assert high["metadata"]["monitoring_start_local_minute"] == 14 * 60 + 30


def test_build_profiles_omits_statistically_thin_month_and_season_profiles() -> None:
    payload = build_residual_profiles(
        _observations(),
        _stations(),
        years=(2021,),
        min_sample_days=3,
    )

    assert payload["profiles"] == {}


def test_build_profiles_output_loads_in_residual_probability_store(tmp_path: Path) -> None:
    payload = build_residual_profiles(
        _observations(),
        _stations(),
        years=(2021,),
        min_sample_days=2,
    )
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = ResidualProfileStore.from_path(path)
    store = ResidualProfileStore(
        profiles=loaded.profiles,
        schema_version=loaded.schema_version,
        source=loaded.source,
        generation_years=loaded.generation_years,
        min_sample_days=2,
    )
    estimate = store.estimate_exact_bucket(
        station_id="RKSI",
        month=6,
        local_minute=14 * 60 + 30,
        direction="high",
        observed_extreme=23.0,
        bucket_lower=25.0,
        bucket_upper=26.0,
        unit="C",
    )

    assert estimate.usable is True
    assert estimate.raw_probability == 1.0
    assert estimate.profile_scope == "month"


@pytest.mark.parametrize("mode", ["timeout", "non_200"])
def test_download_failure_aborts_without_replacing_existing_profile(
    tmp_path: Path,
    mode: str,
) -> None:
    output_path = tmp_path / "profiles.json"
    existing = {"schema_version": 1, "source": "existing good profile", "generation_years": [2021], "profiles": {}}
    output_path.write_text(json.dumps(existing), encoding="utf-8")

    class Response:
        status_code = 503
        text = "service unavailable"

    def fake_get(url: str, *, timeout: float) -> Response:
        if mode == "timeout":
            raise TimeoutError("network timeout")
        return Response()

    with pytest.raises(ProfileBuildError):
        publish_residual_profiles(
            output_path=output_path,
            stations=_stations(),
            years=(2021,),
            catalog_url="https://example.invalid/isd-history.csv",
            hourly_url_template="https://example.invalid/{station_id}-{year}.csv",
            http_get=fake_get,
            min_sample_days=2,
        )

    assert json.loads(output_path.read_text(encoding="utf-8")) == existing


def test_publish_streams_downloaded_hourly_csv_rows_without_response_text(tmp_path: Path) -> None:
    output_path = tmp_path / "profiles.json"
    fixture_lines = HOURLY_PATH.read_text(encoding="utf-8").splitlines()
    calls: list[tuple[str, bool]] = []
    closed = False

    class StreamingResponse:
        status_code = 200

        @property
        def text(self) -> str:
            raise AssertionError("downloaded yearly CSV must be streamed, not text-loaded")

        def iter_lines(self, *, decode_unicode: bool) -> list[str]:
            assert decode_unicode is True
            return fixture_lines

        def close(self) -> None:
            nonlocal closed
            closed = True

    def fake_get(url: str, *, timeout: float, stream: bool = False) -> StreamingResponse:
        calls.append((url, stream))
        return StreamingResponse()

    payload = publish_residual_profiles(
        output_path=output_path,
        stations=_stations(),
        years=(2021,),
        catalog_path=CATALOG_PATH,
        hourly_url_template="https://example.invalid/{station_id}-{year}.csv",
        http_get=fake_get,
        min_sample_days=2,
    )

    assert calls == [("https://example.invalid/RKSI-2021.csv", True)]
    assert closed is True
    assert payload["profiles"]
    assert output_path.exists()


def test_missing_annual_station_file_marks_incomplete(tmp_path: Path) -> None:
    output_path = tmp_path / "profiles.json"

    payload = publish_residual_profiles(
        output_path=output_path,
        stations=_stations(),
        years=(2021,),
        catalog_path=CATALOG_PATH,
        hourly_paths={},
        min_sample_days=2,
    )

    assert payload["profiles"] == {}
    assert payload["incomplete_station_years"] == [
        {"station_id": "RKSI", "year": 2021, "reason": "missing_hourly_file"}
    ]
    assert output_path.exists()


def test_publish_uses_atomic_tmp_replace_only_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import weather_bot.residual_profile_builder as builder

    output_path = tmp_path / "profiles.json"
    replace_calls: list[tuple[Path, Path]] = []
    real_replace = builder.os.replace

    def replace_spy(src: str | Path, dst: str | Path) -> None:
        src_path = Path(src)
        dst_path = Path(dst)
        assert src_path.name.startswith(output_path.name)
        assert src_path.name.endswith(".tmp")
        assert dst_path == output_path
        replace_calls.append((src_path, dst_path))
        real_replace(src, dst)

    monkeypatch.setattr(builder.os, "replace", replace_spy)

    payload = publish_residual_profiles(
        output_path=output_path,
        stations=_stations(),
        years=(2021,),
        catalog_path=CATALOG_PATH,
        hourly_paths={("RKSI", 2021): HOURLY_PATH},
        min_sample_days=2,
    )

    assert replace_calls
    assert not replace_calls[0][0].exists()
    assert payload == json.loads(output_path.read_text(encoding="utf-8"))
    assert sorted(path.name for path in tmp_path.iterdir()) == ["profiles.json"]
    output_text = output_path.read_text(encoding="utf-8")
    assert "TMP" not in output_text
    assert "+0230,1" not in output_text


def test_publish_does_not_materialize_hourly_observations_before_building(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import weather_bot.residual_profile_builder as builder

    output_path = tmp_path / "profiles.json"
    real_add = builder._add_observations_to_accumulator
    checked_arguments = 0

    def add_spy(observations: object, *args: object, **kwargs: object) -> None:
        nonlocal checked_arguments
        checked_arguments += 1
        assert not isinstance(observations, list)
        real_add(observations, *args, **kwargs)

    monkeypatch.setattr(builder, "_add_observations_to_accumulator", add_spy)

    payload = publish_residual_profiles(
        output_path=output_path,
        stations=_stations(),
        years=(2021,),
        catalog_path=CATALOG_PATH,
        hourly_paths={("RKSI", 2021): HOURLY_PATH},
        min_sample_days=2,
    )

    assert checked_arguments == 1
    assert output_path.exists()
    assert payload["profiles"]


def test_invalid_payload_is_not_published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import weather_bot.residual_profile_builder as builder

    output_path = tmp_path / "profiles.json"
    existing = {"schema_version": 1, "source": "existing good profile", "generation_years": [2021], "profiles": {}}
    output_path.write_text(json.dumps(existing), encoding="utf-8")
    replace_calls: list[tuple[Path, Path]] = []

    def invalid_profiles(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "schema_version": 1,
            "source": "invalid",
            "generation_years": [2021],
            "profiles": {
                "RKSI|month:06|0870|high|C": {
                    "station_id": "RKSI",
                    "scope": "month:06",
                    "local_minute": 870,
                    "direction": "high",
                    "unit": "C",
                    "sample_days": 2,
                    "histogram": [[2.0, 3]],
                }
            },
        }

    monkeypatch.setattr(builder._ResidualProfileAccumulator, "to_payload", invalid_profiles)
    monkeypatch.setattr(builder.os, "replace", lambda src, dst: replace_calls.append((Path(src), Path(dst))))

    with pytest.raises(ProfileBuildError):
        publish_residual_profiles(
            output_path=output_path,
            stations=_stations(),
            years=(2021,),
            catalog_path=CATALOG_PATH,
            hourly_paths={("RKSI", 2021): HOURLY_PATH},
            min_sample_days=2,
        )

    assert replace_calls == []
    assert json.loads(output_path.read_text(encoding="utf-8")) == existing


def test_pyproject_registers_cli_entrypoint() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'station-residual-profiles = "weather_bot.residual_profile_builder:main"' in pyproject


def test_cli_exposes_main() -> None:
    assert callable(main)
