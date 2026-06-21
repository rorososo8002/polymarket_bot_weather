from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Literal


SCHEMA_VERSION = 1
MIN_SAMPLE_DAYS = 60

Direction = Literal["high", "low"]
TemperatureUnit = Literal["C", "F"]
BucketType = Literal["exact", "lower_tail", "upper_tail"]


def wilson_lower_bound(successes: int, total: int, z: float = 1.645) -> float:
    """Return the one-sided Wilson lower confidence bound for a proportion."""
    if not _is_int(successes) or not _is_int(total):
        raise ValueError("successes and total must be finite integer counts")
    if total <= 0 or successes < 0 or successes > total:
        raise ValueError("counts must satisfy 0 <= successes <= total and total > 0")
    if not _is_finite_number(z) or z <= 0:
        raise ValueError("z must be a positive finite number")

    proportion = successes / total
    z_squared = z * z
    denominator = 1.0 + z_squared / total
    center = proportion + z_squared / (2.0 * total)
    margin = z * math.sqrt(
        (proportion * (1.0 - proportion) + z_squared / (4.0 * total)) / total
    )
    return max(0.0, (center - margin) / denominator)


@dataclass(frozen=True)
class ResidualProbabilityEstimate:
    usable: bool
    raw_probability: float
    conservative_yes_probability: float
    conservative_no_probability: float
    successes: int
    sample_days: int
    profile_key: str
    profile_scope: str
    reason_code: str
    reason: str


@dataclass(frozen=True)
class ResidualProfile:
    key: str
    station_id: str
    scope: str
    local_minute: int
    direction: Direction
    unit: TemperatureUnit
    sample_days: int
    histogram: tuple[tuple[float, int], ...]

    @property
    def profile_scope(self) -> str:
        return self.scope.split(":", 1)[0]


@dataclass(frozen=True)
class ResidualProfileStore:
    profiles: Mapping[str, ResidualProfile]
    schema_version: int = SCHEMA_VERSION
    source: str = ""
    generation_years: tuple[int, ...] = ()
    concentrated_sizing_eligible_by_station: Mapping[str, bool] = MappingProxyType({})
    min_sample_days: int = MIN_SAMPLE_DAYS
    load_reason_code: str = ""
    load_reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "profiles", MappingProxyType(dict(self.profiles)))
        object.__setattr__(self, "generation_years", tuple(self.generation_years))
        object.__setattr__(
            self,
            "concentrated_sizing_eligible_by_station",
            MappingProxyType(dict(self.concentrated_sizing_eligible_by_station)),
        )

    @classmethod
    def from_path(cls, path: str | Path) -> "ResidualProfileStore":
        try:
            profile_path = Path(path)
            profile_bytes = profile_path.read_bytes()
            text = profile_bytes.decode("utf-8")
        except OSError as exc:
            return cls._load_failure(
                "SKIP_RESIDUAL_PROFILE_MISSING",
                f"Residual profile file is unavailable: {exc}",
            )

        try:
            payload = json.loads(text, parse_constant=_reject_non_finite_json)
            schema_version, source, years, profiles = _validate_profile_payload(payload)
        except _ProfileValidationError as exc:
            return cls._load_failure(exc.reason_code, str(exc))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return cls._load_failure(
                "SKIP_RESIDUAL_PROFILE_MALFORMED",
                f"Residual profile JSON is malformed: {exc}",
            )

        return cls(
            profiles=profiles,
            schema_version=schema_version,
            source=source,
            generation_years=years,
            concentrated_sizing_eligible_by_station=_load_concentrated_sizing_eligibility(
                profile_path,
                profile_bytes,
            ),
        )

    @classmethod
    def _load_failure(cls, reason_code: str, reason: str) -> "ResidualProfileStore":
        return cls(
            profiles={},
            schema_version=0,
            load_reason_code=reason_code,
            load_reason=reason,
        )

    def estimate_exact_bucket(
        self,
        *,
        station_id: str,
        month: int,
        local_minute: int,
        direction: Direction,
        observed_extreme: float,
        bucket_lower: float,
        bucket_upper: float,
        unit: TemperatureUnit,
    ) -> ResidualProbabilityEstimate:
        return self.estimate_bucket(
            station_id=station_id,
            month=month,
            local_minute=local_minute,
            direction=direction,
            observed_extreme=observed_extreme,
            bucket_type="exact",
            bucket_lower=bucket_lower,
            bucket_upper=bucket_upper,
            unit=unit,
        )

    def estimate_bucket(
        self,
        *,
        station_id: str,
        month: int,
        local_minute: int,
        direction: Direction,
        observed_extreme: float,
        bucket_type: BucketType,
        unit: TemperatureUnit,
        bucket_lower: float | None = None,
        bucket_upper: float | None = None,
    ) -> ResidualProbabilityEstimate:
        if self.load_reason_code:
            return _unusable(self.load_reason_code, self.load_reason)

        request_error = _validate_estimate_request(
            station_id=station_id,
            month=month,
            local_minute=local_minute,
            direction=direction,
            observed_extreme=observed_extreme,
            bucket_type=bucket_type,
            bucket_lower=bucket_lower,
            bucket_upper=bucket_upper,
            unit=unit,
        )
        if request_error is not None:
            return request_error

        profile, unavailable = self._find_usable_profile(
            station_id=station_id,
            month=month,
            local_minute=local_minute,
            direction=direction,
            unit=unit,
        )
        if profile is None:
            return unavailable

        successes = sum(
            count
            for residual, count in profile.histogram
            if _residual_matches_bucket(
                residual=residual,
                observed_extreme=observed_extreme,
                direction=direction,
                bucket_type=bucket_type,
                bucket_lower=bucket_lower,
                bucket_upper=bucket_upper,
            )
        )
        raw_probability = successes / profile.sample_days
        no_successes = profile.sample_days - successes
        return ResidualProbabilityEstimate(
            usable=True,
            raw_probability=raw_probability,
            conservative_yes_probability=wilson_lower_bound(successes, profile.sample_days),
            conservative_no_probability=wilson_lower_bound(no_successes, profile.sample_days),
            successes=successes,
            sample_days=profile.sample_days,
            profile_key=profile.key,
            profile_scope=profile.profile_scope,
            reason_code="RESIDUAL_PROBABILITY_OK",
            reason="Probability estimated from independent same-station residual days.",
        )

    def _find_usable_profile(
        self,
        *,
        station_id: str,
        month: int,
        local_minute: int,
        direction: Direction,
        unit: TemperatureUnit,
    ) -> tuple[ResidualProfile | None, ResidualProbabilityEstimate]:
        season = _season_for_month(month)
        requested_scopes = (f"month:{month:02d}", f"season:{season}")
        thin_profiles: list[ResidualProfile] = []

        for scope in requested_scopes:
            key = _profile_key(station_id, scope, local_minute, direction, unit)
            profile = self.profiles.get(key)
            if profile is None:
                continue
            if profile.sample_days >= self.min_sample_days:
                return profile, _unusable("", "")
            thin_profiles.append(profile)

        if thin_profiles:
            profile = thin_profiles[0]
            return None, _unusable(
                "SKIP_RESIDUAL_PROFILE_THIN",
                f"Residual profile has {profile.sample_days} days; {self.min_sample_days} are required.",
                profile,
            )

        if self._has_other_unit_profile(
            station_id=station_id,
            scopes=requested_scopes,
            local_minute=local_minute,
            direction=direction,
            unit=unit,
        ):
            return None, _unusable(
                "SKIP_RESIDUAL_PROFILE_UNIT_MISMATCH",
                "Residual profile unit does not match the requested market unit.",
            )

        return None, _unusable(
            "SKIP_RESIDUAL_PROFILE_MISSING",
            "No matching station, month or season, local-time, direction, and unit profile exists.",
        )

    def _has_other_unit_profile(
        self,
        *,
        station_id: str,
        scopes: tuple[str, str],
        local_minute: int,
        direction: Direction,
        unit: TemperatureUnit,
    ) -> bool:
        return any(
            profile.station_id == station_id
            and profile.scope in scopes
            and profile.local_minute == local_minute
            and profile.direction == direction
            and profile.unit != unit
            for profile in self.profiles.values()
        )


def _load_concentrated_sizing_eligibility(
    profile_path: Path,
    profile_bytes: bytes,
) -> dict[str, bool]:
    """Load the fail-closed station eligibility map from the verified sibling manifest."""
    manifest_path = profile_path.with_name(f"{profile_path.stem}.manifest.json")
    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            parse_constant=_reject_non_finite_json,
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    if not isinstance(manifest, dict):
        return {}
    expected_hash = manifest.get("profile_artifact_sha256")
    if not isinstance(expected_hash, str):
        return {}
    if hashlib.sha256(profile_bytes).hexdigest() != expected_hash.lower():
        return {}
    stations = manifest.get("stations")
    if not isinstance(stations, dict):
        return {}

    eligibility: dict[str, bool] = {}
    for station_payload in stations.values():
        if not isinstance(station_payload, dict):
            return {}
        station_id = station_payload.get("station_id")
        eligible = station_payload.get("concentrated_sizing_eligible")
        if not isinstance(station_id, str) or not station_id.strip() or not isinstance(eligible, bool):
            return {}
        eligibility[station_id.strip()] = eligible
    return eligibility


class _ProfileValidationError(ValueError):
    def __init__(self, reason_code: str, reason: str) -> None:
        super().__init__(reason)
        self.reason_code = reason_code


def _validate_profile_payload(
    payload: object,
) -> tuple[int, str, tuple[int, ...], dict[str, ResidualProfile]]:
    if not isinstance(payload, Mapping):
        raise _malformed("Residual profile root must be an object.")

    schema_version = payload.get("schema_version")
    if not _is_int(schema_version) or schema_version != SCHEMA_VERSION:
        raise _ProfileValidationError(
            "SKIP_RESIDUAL_PROFILE_SCHEMA_MISMATCH",
            f"Unsupported residual profile schema version: {schema_version!r}.",
        )

    source = payload.get("source")
    if not isinstance(source, str) or not source.strip():
        raise _malformed("Residual profile source provenance must be a non-empty string.")

    raw_years = payload.get("generation_years")
    if not isinstance(raw_years, list) or not raw_years:
        raise _malformed("Residual profile generation_years must be a non-empty list.")
    if any(not _is_int(year) or year < 1900 or year > 9999 for year in raw_years):
        raise _malformed("Residual profile generation years must be valid integer years.")
    years = tuple(raw_years)
    if years != tuple(sorted(set(years))):
        raise _malformed("Residual profile generation years must be unique and ascending.")

    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, Mapping):
        raise _malformed("Residual profile entries must be an object keyed by profile key.")

    profiles: dict[str, ResidualProfile] = {}
    for key, raw_profile in raw_profiles.items():
        profile = _validate_profile(key, raw_profile)
        if profile.key in profiles:
            raise _malformed(f"Duplicate residual profile key: {profile.key}.")
        profiles[profile.key] = profile
    return schema_version, source.strip(), years, profiles


def _validate_profile(key: object, payload: object) -> ResidualProfile:
    if not isinstance(key, str) or not key:
        raise _malformed("Residual profile keys must be non-empty strings.")
    if not isinstance(payload, Mapping):
        raise _malformed(f"Residual profile {key!r} must be an object.")

    station_id = payload.get("station_id")
    scope = payload.get("scope")
    local_minute = payload.get("local_minute")
    direction = payload.get("direction")
    unit = payload.get("unit")
    sample_days = payload.get("sample_days")
    histogram = payload.get("histogram")

    if not isinstance(station_id, str) or not station_id.strip():
        raise _malformed(f"Residual profile {key!r} has an invalid station_id.")
    if not isinstance(scope, str) or not _valid_scope(scope):
        raise _malformed(f"Residual profile {key!r} has an invalid month or season scope.")
    if not _is_int(local_minute) or not 0 <= local_minute < 24 * 60 or local_minute % 30:
        raise _malformed(f"Residual profile {key!r} must use a local 30-minute bin.")
    if direction not in {"high", "low"}:
        raise _malformed(f"Residual profile {key!r} has an invalid direction.")
    if unit not in {"C", "F"}:
        raise _malformed(f"Residual profile {key!r} has an invalid temperature unit.")
    if not _is_int(sample_days) or sample_days < 0:
        raise _malformed(f"Residual profile {key!r} has an invalid sample-day count.")

    expected_key = _profile_key(station_id.strip(), scope, local_minute, direction, unit)
    if key != expected_key:
        raise _malformed(
            f"Residual profile key {key!r} does not match its station, scope, time, direction, and unit."
        )

    validated_histogram = _validate_histogram(key, histogram, sample_days)
    return ResidualProfile(
        key=key,
        station_id=station_id.strip(),
        scope=scope,
        local_minute=local_minute,
        direction=direction,
        unit=unit,
        sample_days=sample_days,
        histogram=validated_histogram,
    )


def _validate_histogram(
    key: str,
    histogram: object,
    sample_days: int,
) -> tuple[tuple[float, int], ...]:
    if not isinstance(histogram, list) or not histogram:
        raise _malformed(f"Residual profile {key!r} histogram must be a non-empty list.")

    values: list[tuple[float, int]] = []
    seen_residuals: set[float] = set()
    for item in histogram:
        if not isinstance(item, list) or len(item) != 2:
            raise _malformed(f"Residual profile {key!r} histogram entries must be [value, count].")
        residual, count = item
        if not isinstance(residual, (int, float)) or isinstance(residual, bool):
            raise _malformed(f"Residual profile {key!r} residuals must be numeric.")
        if not math.isfinite(residual):
            raise _ProfileValidationError(
                "SKIP_RESIDUAL_PROFILE_NONFINITE",
                f"Residual profile {key!r} contains a non-finite residual.",
            )
        residual_value = float(residual)
        if residual_value < 0:
            raise _malformed(f"Residual profile {key!r} contains a negative residual.")
        if not _is_int(count):
            raise _malformed(f"Residual profile {key!r} histogram counts must be integers.")
        if count < 0:
            raise _ProfileValidationError(
                "SKIP_RESIDUAL_PROFILE_NEGATIVE_COUNT",
                f"Residual profile {key!r} contains a negative histogram count.",
            )
        if residual_value in seen_residuals:
            raise _malformed(f"Residual profile {key!r} contains duplicate residual values.")
        seen_residuals.add(residual_value)
        values.append((residual_value, count))

    if sum(count for _, count in values) != sample_days:
        raise _malformed(
            f"Residual profile {key!r} histogram counts do not equal sample_days."
        )
    return tuple(sorted(values))


def _validate_estimate_request(
    *,
    station_id: object,
    month: object,
    local_minute: object,
    direction: object,
    observed_extreme: object,
    bucket_type: object,
    bucket_lower: object,
    bucket_upper: object,
    unit: object,
) -> ResidualProbabilityEstimate | None:
    if not isinstance(unit, str) or unit not in {"C", "F"}:
        return _unusable(
            "SKIP_RESIDUAL_PROFILE_UNIT_MISMATCH",
            "Requested market unit must be C or F.",
        )
    if (
        not isinstance(station_id, str)
        or not station_id
        or not _is_int(month)
        or not 1 <= month <= 12
        or not _is_int(local_minute)
        or not 0 <= local_minute < 24 * 60
        or local_minute % 30
        or not isinstance(direction, str)
        or direction not in {"high", "low"}
        or not isinstance(bucket_type, str)
        or bucket_type not in {"exact", "lower_tail", "upper_tail"}
    ):
        return _unusable(
            "SKIP_RESIDUAL_PROFILE_MALFORMED",
            "Residual probability request has invalid station, month, time, direction, or bucket type.",
        )
    if not _is_finite_number(observed_extreme):
        return _unusable(
            "SKIP_RESIDUAL_PROFILE_NONFINITE",
            "Observed temperature must be finite.",
        )

    if bucket_type == "exact":
        if not _is_finite_number(bucket_lower) or not _is_finite_number(bucket_upper):
            return _unusable(
                "SKIP_RESIDUAL_PROFILE_NONFINITE",
                "Exact bucket bounds must be finite.",
            )
        if bucket_lower >= bucket_upper:
            return _unusable(
                "SKIP_RESIDUAL_PROFILE_MALFORMED",
                "Exact bucket lower bound must be below its upper bound.",
            )
    elif bucket_type == "lower_tail" and not _is_finite_number(bucket_upper):
        return _unusable(
            "SKIP_RESIDUAL_PROFILE_NONFINITE",
            "Lower-tail upper bound must be finite.",
        )
    elif bucket_type == "upper_tail" and not _is_finite_number(bucket_lower):
        return _unusable(
            "SKIP_RESIDUAL_PROFILE_NONFINITE",
            "Upper-tail lower bound must be finite.",
        )
    return None


def _residual_matches_bucket(
    *,
    residual: float,
    observed_extreme: float,
    direction: Direction,
    bucket_type: BucketType,
    bucket_lower: float | None,
    bucket_upper: float | None,
) -> bool:
    observed = _decimal(observed_extreme)
    movement = _decimal(residual)
    if direction == "high":
        final_extreme = observed + movement
        if bucket_type == "exact":
            assert bucket_lower is not None and bucket_upper is not None
            return _decimal(bucket_lower) <= final_extreme < _decimal(bucket_upper)
        if bucket_type == "lower_tail":
            assert bucket_upper is not None
            return final_extreme < _decimal(bucket_upper)
        assert bucket_lower is not None
        return final_extreme >= _decimal(bucket_lower)

    final_extreme = observed - movement
    if bucket_type == "exact":
        assert bucket_lower is not None and bucket_upper is not None
        return _decimal(bucket_lower) <= final_extreme < _decimal(bucket_upper)
    if bucket_type == "lower_tail":
        assert bucket_upper is not None
        return final_extreme < _decimal(bucket_upper)
    assert bucket_lower is not None
    return final_extreme >= _decimal(bucket_lower)


def _unusable(
    reason_code: str,
    reason: str,
    profile: ResidualProfile | None = None,
) -> ResidualProbabilityEstimate:
    return ResidualProbabilityEstimate(
        usable=False,
        raw_probability=0.0,
        conservative_yes_probability=0.0,
        conservative_no_probability=0.0,
        successes=0,
        sample_days=profile.sample_days if profile else 0,
        profile_key=profile.key if profile else "",
        profile_scope=profile.profile_scope if profile else "",
        reason_code=reason_code,
        reason=reason,
    )


def _profile_key(
    station_id: str,
    scope: str,
    local_minute: int,
    direction: str,
    unit: str,
) -> str:
    return f"{station_id}|{scope}|{local_minute:04d}|{direction}|{unit}"


def _valid_scope(scope: str) -> bool:
    if scope.startswith("month:"):
        value = scope.removeprefix("month:")
        return len(value) == 2 and value.isdigit() and 1 <= int(value) <= 12
    if scope.startswith("season:"):
        return scope.removeprefix("season:") in {"DJF", "MAM", "JJA", "SON"}
    return False


def _season_for_month(month: int) -> str:
    if month in {12, 1, 2}:
        return "DJF"
    if month in {3, 4, 5}:
        return "MAM"
    if month in {6, 7, 8}:
        return "JJA"
    return "SON"


def _reject_non_finite_json(value: str) -> None:
    raise _ProfileValidationError(
        "SKIP_RESIDUAL_PROFILE_NONFINITE",
        f"Residual profile JSON contains non-finite value {value}.",
    )


def _malformed(reason: str) -> _ProfileValidationError:
    return _ProfileValidationError("SKIP_RESIDUAL_PROFILE_MALFORMED", reason)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))
