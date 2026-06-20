from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path

import pytest

from weather_bot.residual_probability import (
    ResidualProbabilityEstimate,
    ResidualProfileStore,
    wilson_lower_bound,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "residual_profiles" / "minimal_profiles.json"


def _fixture_payload() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _write_payload(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _estimate_high(store: ResidualProfileStore, **overrides: object) -> ResidualProbabilityEstimate:
    arguments: dict[str, object] = {
        "station_id": "RKSI",
        "month": 6,
        "local_minute": 14 * 60 + 30,
        "direction": "high",
        "observed_extreme": 23.4,
        "bucket_lower": 23.0,
        "bucket_upper": 24.0,
        "unit": "C",
    }
    arguments.update(overrides)
    return store.estimate_exact_bucket(**arguments)


def test_wilson_lower_bound_discounts_small_samples() -> None:
    assert wilson_lower_bound(19, 20, z=1.645) < 0.95


def test_wilson_lower_bound_can_clear_ninety_five_with_enough_clean_days() -> None:
    assert wilson_lower_bound(60, 60, z=1.645) >= 0.95


@pytest.mark.parametrize(
    ("successes", "total", "z"),
    [
        (-1, 20, 1.645),
        (21, 20, 1.645),
        (1, 0, 1.645),
        (1.5, 20, 1.645),
        (1, 20.5, 1.645),
        (1, 20, 0.0),
        (1, 20, math.inf),
        (math.nan, 20, 1.645),
    ],
)
def test_wilson_lower_bound_rejects_invalid_values(
    successes: float,
    total: float,
    z: float,
) -> None:
    with pytest.raises(ValueError):
        wilson_lower_bound(successes, total, z=z)


def test_profile_store_preserves_validated_provenance_and_is_immutable() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)
    estimate = _estimate_high(store)

    assert store.schema_version == 1
    assert store.source == "test official same-station daily residuals"
    assert store.generation_years == (2021, 2022, 2023, 2024, 2025)
    with pytest.raises(TypeError):
        store.profiles["new"] = store.profiles[estimate.profile_key]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        estimate.usable = False  # type: ignore[misc]


def test_profile_store_fails_closed_when_station_month_time_is_missing() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)

    estimate = _estimate_high(store, station_id="UNKNOWN")

    assert estimate.usable is False
    assert estimate.reason_code == "SKIP_RESIDUAL_PROFILE_MISSING"


def test_high_exact_bucket_uses_remaining_warming() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)

    estimate = _estimate_high(store)

    assert estimate.usable is True
    assert estimate.raw_probability == pytest.approx(0.70)
    assert estimate.successes == 70
    assert estimate.sample_days == 100
    assert estimate.profile_scope == "month"
    assert estimate.reason_code == "RESIDUAL_PROBABILITY_OK"


def test_high_at_upper_boundary_makes_lower_exact_bucket_impossible() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)

    estimate = _estimate_high(store, observed_extreme=24.0)

    assert estimate.usable is True
    assert estimate.raw_probability == 0.0
    assert estimate.successes == 0


def test_high_exact_bucket_keeps_values_just_below_upper_boundary(
    tmp_path: Path,
) -> None:
    payload = _fixture_payload()
    payload["profiles"] = {
        "RKSI|month:06|0870|high|C": {
            "station_id": "RKSI",
            "scope": "month:06",
            "local_minute": 870,
            "direction": "high",
            "unit": "C",
            "sample_days": 60,
            "histogram": [
                [0.0, 57],
                [0.5999999999995, 1],
                [0.6, 1],
                [0.6000000000005, 1],
            ],
        }
    }
    store = ResidualProfileStore.from_path(_write_payload(tmp_path, payload))

    estimate = _estimate_high(store)

    assert estimate.usable is True
    assert estimate.successes == 58


def test_low_exact_bucket_uses_remaining_cooling_and_includes_lower_boundary() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)

    estimate = store.estimate_exact_bucket(
        station_id="RKSI",
        month=6,
        local_minute=6 * 60 + 30,
        direction="low",
        observed_extreme=8.4,
        bucket_lower=8.0,
        bucket_upper=9.0,
        unit="C",
    )

    assert estimate.usable is True
    assert estimate.raw_probability == pytest.approx(0.70)
    assert estimate.successes == 70


def test_low_exact_bucket_rejects_values_just_below_lower_boundary(
    tmp_path: Path,
) -> None:
    payload = _fixture_payload()
    payload["profiles"] = {
        "RKSI|month:06|0390|low|C": {
            "station_id": "RKSI",
            "scope": "month:06",
            "local_minute": 390,
            "direction": "low",
            "unit": "C",
            "sample_days": 60,
            "histogram": [
                [0.0, 57],
                [0.3999999999995, 1],
                [0.4, 1],
                [0.4000000000005, 1],
            ],
        }
    }
    store = ResidualProfileStore.from_path(_write_payload(tmp_path, payload))

    estimate = store.estimate_exact_bucket(
        station_id="RKSI",
        month=6,
        local_minute=6 * 60 + 30,
        direction="low",
        observed_extreme=8.4,
        bucket_lower=8.0,
        bucket_upper=9.0,
        unit="C",
    )

    assert estimate.usable is True
    assert estimate.successes == 59


def test_lower_and_upper_tails_share_the_exact_bucket_boundaries() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)
    common = {
        "station_id": "RKSI",
        "month": 6,
        "local_minute": 14 * 60 + 30,
        "direction": "high",
        "observed_extreme": 23.4,
        "unit": "C",
    }

    lower = store.estimate_bucket(bucket_type="lower_tail", bucket_upper=24.0, **common)
    upper = store.estimate_bucket(bucket_type="upper_tail", bucket_lower=24.0, **common)

    assert lower.raw_probability == pytest.approx(0.70)
    assert upper.raw_probability == pytest.approx(0.30)
    assert lower.raw_probability + upper.raw_probability == pytest.approx(1.0)


def test_month_profile_is_preferred_over_season_profile() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)

    estimate = _estimate_high(store)

    assert estimate.profile_key == "RKSI|month:06|0870|high|C"
    assert estimate.profile_scope == "month"
    assert estimate.raw_probability == pytest.approx(0.70)


def test_usable_season_profile_is_used_when_month_profile_is_missing() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)

    estimate = _estimate_high(store, month=7)

    assert estimate.usable is True
    assert estimate.profile_key == "RKSI|season:JJA|0870|high|C"
    assert estimate.profile_scope == "season"
    assert estimate.raw_probability == pytest.approx(0.25)


def test_usable_season_profile_replaces_a_statistically_thin_month(tmp_path: Path) -> None:
    payload = _fixture_payload()
    profiles = payload["profiles"]
    assert isinstance(profiles, dict)
    profiles["RKSI|month:07|0870|high|C"] = {
        "station_id": "RKSI",
        "scope": "month:07",
        "local_minute": 870,
        "direction": "high",
        "unit": "C",
        "sample_days": 20,
        "histogram": [[0.0, 20]],
    }
    store = ResidualProfileStore.from_path(_write_payload(tmp_path, payload))

    estimate = _estimate_high(store, month=7)

    assert estimate.usable is True
    assert estimate.profile_scope == "season"
    assert estimate.sample_days == 80


def test_statistically_thin_profile_fails_closed(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["profiles"] = {
        "THIN|month:06|0870|high|C": {
            "station_id": "THIN",
            "scope": "month:06",
            "local_minute": 870,
            "direction": "high",
            "unit": "C",
            "sample_days": 20,
            "histogram": [[0.0, 19], [1.0, 1]],
        }
    }
    store = ResidualProfileStore.from_path(_write_payload(tmp_path, payload))

    estimate = _estimate_high(store, station_id="THIN")

    assert estimate.usable is False
    assert estimate.sample_days == 20
    assert estimate.reason_code == "SKIP_RESIDUAL_PROFILE_THIN"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ({"schema_version": 999}, "SKIP_RESIDUAL_PROFILE_SCHEMA_MISMATCH"),
        ({"source": ""}, "SKIP_RESIDUAL_PROFILE_MALFORMED"),
    ],
)
def test_invalid_root_provenance_fails_closed(
    tmp_path: Path,
    mutation: dict[str, object],
    expected_code: str,
) -> None:
    payload = _fixture_payload()
    payload.update(mutation)
    store = ResidualProfileStore.from_path(_write_payload(tmp_path, payload))

    estimate = _estimate_high(store)

    assert estimate.usable is False
    assert estimate.reason_code == expected_code


def test_negative_histogram_count_fails_closed(tmp_path: Path) -> None:
    payload = _fixture_payload()
    profiles = payload["profiles"]
    assert isinstance(profiles, dict)
    profile = profiles["RKSI|month:06|0870|high|C"]
    assert isinstance(profile, dict)
    profile["histogram"] = [[0.0, 101], [0.3, -1]]
    store = ResidualProfileStore.from_path(_write_payload(tmp_path, payload))

    estimate = _estimate_high(store)

    assert estimate.usable is False
    assert estimate.reason_code == "SKIP_RESIDUAL_PROFILE_NEGATIVE_COUNT"


def test_malformed_histogram_fails_closed(tmp_path: Path) -> None:
    payload = _fixture_payload()
    profiles = payload["profiles"]
    assert isinstance(profiles, dict)
    profile = profiles["RKSI|month:06|0870|high|C"]
    assert isinstance(profile, dict)
    profile["histogram"] = [[0.0, 99]]
    store = ResidualProfileStore.from_path(_write_payload(tmp_path, payload))

    estimate = _estimate_high(store)

    assert estimate.usable is False
    assert estimate.reason_code == "SKIP_RESIDUAL_PROFILE_MALFORMED"


def test_non_finite_histogram_value_fails_closed(tmp_path: Path) -> None:
    payload = _fixture_payload()
    profiles = payload["profiles"]
    assert isinstance(profiles, dict)
    profile = profiles["RKSI|month:06|0870|high|C"]
    assert isinstance(profile, dict)
    profile["histogram"] = [[math.nan, 100]]
    store = ResidualProfileStore.from_path(_write_payload(tmp_path, payload))

    estimate = _estimate_high(store)

    assert estimate.usable is False
    assert estimate.reason_code == "SKIP_RESIDUAL_PROFILE_NONFINITE"


def test_non_numeric_histogram_value_is_malformed(tmp_path: Path) -> None:
    payload = _fixture_payload()
    profiles = payload["profiles"]
    assert isinstance(profiles, dict)
    profile = profiles["RKSI|month:06|0870|high|C"]
    assert isinstance(profile, dict)
    profile["histogram"] = [["bad", 100]]
    store = ResidualProfileStore.from_path(_write_payload(tmp_path, payload))

    estimate = _estimate_high(store)

    assert estimate.usable is False
    assert estimate.reason_code == "SKIP_RESIDUAL_PROFILE_MALFORMED"


def test_requested_unit_must_match_profile_unit() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)

    estimate = _estimate_high(store, unit="F")

    assert estimate.usable is False
    assert estimate.reason_code == "SKIP_RESIDUAL_PROFILE_UNIT_MISMATCH"


def test_malformed_requested_unit_fails_closed_without_raising() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)

    estimate = _estimate_high(store, unit=[])

    assert estimate.usable is False
    assert estimate.reason_code == "SKIP_RESIDUAL_PROFILE_UNIT_MISMATCH"


def test_non_finite_estimation_input_fails_closed() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)

    estimate = _estimate_high(store, observed_extreme=math.inf)

    assert estimate.usable is False
    assert estimate.reason_code == "SKIP_RESIDUAL_PROFILE_NONFINITE"


def test_conservative_yes_and_no_are_calculated_separately() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)

    estimate = _estimate_high(store)

    assert estimate.conservative_yes_probability == pytest.approx(
        wilson_lower_bound(70, 100)
    )
    assert estimate.conservative_no_probability == pytest.approx(
        wilson_lower_bound(30, 100)
    )
    assert estimate.conservative_yes_probability <= estimate.raw_probability
    assert estimate.conservative_no_probability <= 1.0 - estimate.raw_probability
    assert estimate.conservative_no_probability != pytest.approx(
        1.0 - estimate.conservative_yes_probability
    )


def test_raw_probabilities_form_a_coherent_complete_bucket_ladder() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)
    common = {
        "station_id": "RKSI",
        "month": 6,
        "local_minute": 14 * 60 + 30,
        "direction": "high",
        "observed_extreme": 23.4,
        "unit": "C",
    }

    estimates = [
        store.estimate_bucket(bucket_type="lower_tail", bucket_upper=23.0, **common),
        store.estimate_bucket(
            bucket_type="exact", bucket_lower=23.0, bucket_upper=24.0, **common
        ),
        store.estimate_bucket(
            bucket_type="exact", bucket_lower=24.0, bucket_upper=25.0, **common
        ),
        store.estimate_bucket(bucket_type="upper_tail", bucket_lower=25.0, **common),
    ]

    assert sum(item.raw_probability for item in estimates) == pytest.approx(1.0)
    assert sum(item.conservative_yes_probability for item in estimates) < 1.0


def test_invalid_bucket_shape_fails_closed() -> None:
    store = ResidualProfileStore.from_path(FIXTURE_PATH)

    estimate = store.estimate_bucket(
        station_id="RKSI",
        month=6,
        local_minute=870,
        direction="high",
        observed_extreme=23.4,
        bucket_type="exact",
        bucket_lower=24.0,
        bucket_upper=23.0,
        unit="C",
    )

    assert estimate.usable is False
    assert estimate.reason_code == "SKIP_RESIDUAL_PROFILE_MALFORMED"
