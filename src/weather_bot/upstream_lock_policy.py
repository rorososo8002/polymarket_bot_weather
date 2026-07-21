from __future__ import annotations

from math import isfinite
from typing import Any


UPSTREAM_LOCK_PAPER_MODE = "upstream_lock_paper"
UPSTREAM_LOCK_PAPER_SIGNAL_FAMILY = "upstream_lock_paper"
UPSTREAM_LOCK_PAPER_EXACT_NO_TIER = "upstream_2c_exact_no"
UPSTREAM_LOCK_PAPER_PRECISE_EXACT_NO_TIER = "upstream_1c_exact_no"
UPSTREAM_LOCK_PAPER_MAX_ENTRY_PRICE = 0.85
UPSTREAM_LOCK_PAPER_MIN_BUCKET_DISTANCE_C = 2.0
UPSTREAM_LOCK_PAPER_ENTRY_FRACTION = 0.10
UPSTREAM_LOCK_PAPER_EVENT_CAP_FRACTION = 0.05
UPSTREAM_LOCK_PAPER_SETTLEMENT_UNCERTAINTY_FLOOR = 0.04
UPSTREAM_LOCK_PAPER_NOWCAST_SOURCES = frozenset(
    {"aviationweather-metar", "kma-aviation-metar", "kma-official-public-metars"}
)
# 2026-07-06..20: 45 RKSI/RKPK WU labels, 43 full one-degree breaks
# (28 high, 15 low), zero false NOs. Keep this exception on the precise public
# KMA source only; rounded AWC/KMA API rows retain the two-degree floor.
KMA_PUBLIC_ONE_DEGREE_STATIONS = frozenset({"RKSI", "RKPK"})


def required_upstream_bucket_distance_c(
    *,
    source: str,
    station_id: str,
    temperature_metric: str | None,
    temperature_bucket: str | None,
    threshold_unit: str | None,
) -> float:
    """Return the audited minimum; callers must not trust payload values."""
    if (
        source == "kma-official-public-metars"
        and station_id.upper() in KMA_PUBLIC_ONE_DEGREE_STATIONS
        and temperature_metric in {"max", "min"}
        and temperature_bucket == "exact"
        and threshold_unit == "C"
    ):
        return 1.0
    return UPSTREAM_LOCK_PAPER_MIN_BUCKET_DISTANCE_C


def upstream_lock_paper_exact_no_tier(
    *,
    source: str,
    station_id: str,
    temperature_metric: str | None,
    temperature_bucket: str | None,
    threshold_unit: str | None,
) -> str:
    """Keep one-degree KMA evidence separate from rounded two-degree evidence."""
    required_distance_c = required_upstream_bucket_distance_c(
        source=source,
        station_id=station_id,
        temperature_metric=temperature_metric,
        temperature_bucket=temperature_bucket,
        threshold_unit=threshold_unit,
    )
    if required_distance_c == 1.0:
        return UPSTREAM_LOCK_PAPER_PRECISE_EXACT_NO_TIER
    return UPSTREAM_LOCK_PAPER_EXACT_NO_TIER


def upstream_settlement_probabilities(settings: Any) -> tuple[float, float]:
    """Return conservative YES/NO settlement probabilities for paper sizing.

    The two-degree observation is a physical bucket break at the upstream
    station, not proof that Weather Underground will publish the same integer.
    Keep at least four percentage points of settlement uncertainty even when a
    local experiment sets the ordinary model margins to zero.
    """
    model_margin = float(getattr(settings, "model_error_margin", 0.0) or 0.0)
    resolution_margin = float(getattr(settings, "resolution_error_margin", 0.0) or 0.0)
    combined = model_margin + resolution_margin
    if not isfinite(combined):
        combined = UPSTREAM_LOCK_PAPER_SETTLEMENT_UNCERTAINTY_FLOOR
    yes_probability = min(
        0.49,
        max(UPSTREAM_LOCK_PAPER_SETTLEMENT_UNCERTAINTY_FLOOR, combined),
    )
    return yes_probability, 1.0 - yes_probability
