from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from .stations import StationMeta

SettlementUnit = Literal["C", "F", "UNKNOWN"]
ReportingPrecision = Literal["1C", "0.1C", "1F", "0.1F", "UNKNOWN"]
BucketModel = Literal[
    "whole_degree_source_display_band",
    "one_decimal_range_containing",
    "unknown",
]
PrecisionConfidence = Literal["verified", "needs_audit", "blocked"]


@dataclass(frozen=True)
class SettlementPrecisionProfile:
    city: str
    station_id: str
    source_type: str
    unit: SettlementUnit
    reporting_precision: ReportingPrecision
    bucket_model: BucketModel
    confidence: PrecisionConfidence
    note: str = ""


def settlement_precision_profile_for_station(station: StationMeta) -> SettlementPrecisionProfile:
    unit = _normalized_unit(station.temperature_unit)
    reporting_precision = _normalized_precision(station.reporting_precision)
    source_type = station.nowcast_source_type

    if reporting_precision in {"1C", "1F"} and reporting_precision[-1] == unit:
        return SettlementPrecisionProfile(
            city=station.city,
            station_id=station.station_id,
            source_type=source_type,
            unit=unit,
            reporting_precision=reporting_precision,
            bucket_model="whole_degree_source_display_band",
            confidence="verified",
            note="Integer label N uses the source-displayed band [N.0, N+1.0).",
        )

    if (
        station.station_id.upper() == "HKO"
        and source_type == "hko_maxmin_since_midnight"
        and unit == "C"
        and reporting_precision == "0.1C"
    ):
        return SettlementPrecisionProfile(
            city=station.city,
            station_id=station.station_id,
            source_type=source_type,
            unit=unit,
            reporting_precision=reporting_precision,
            bucket_model="one_decimal_range_containing",
            confidence="needs_audit",
            note="HKO decimal observations require settled-market bucket evidence.",
        )

    return SettlementPrecisionProfile(
        city=station.city,
        station_id=station.station_id,
        source_type=source_type,
        unit=unit,
        reporting_precision=reporting_precision,
        bucket_model="unknown",
        confidence="blocked",
        note="Settlement reporting precision is unsupported, unknown, or inconsistent with its unit.",
    )


def _normalized_unit(value: str) -> SettlementUnit:
    normalized = value.strip().upper()
    if normalized in {"C", "CELSIUS"}:
        return "C"
    if normalized in {"F", "FAHRENHEIT"}:
        return "F"
    return "UNKNOWN"


def _normalized_precision(value: str) -> ReportingPrecision:
    normalized = value.strip().upper()
    if normalized in {"1C", "0.1C", "1F", "0.1F"}:
        return cast(ReportingPrecision, normalized)
    return "UNKNOWN"
