from __future__ import annotations

from typing import Any


UPSTREAM_LOCK_PAPER_MODE = "upstream_lock_paper"
UPSTREAM_LOCK_PAPER_SIGNAL_FAMILY = "upstream_lock_paper"
# Kept for replaying old paper-ledger rows written under the former two-degree rule.
UPSTREAM_LOCK_PAPER_EXACT_NO_TIER = "upstream_2c_exact_no"
UPSTREAM_LOCK_PAPER_PRECISE_EXACT_NO_TIER = "upstream_1c_exact_no"
UPSTREAM_LOCK_PAPER_MAX_ENTRY_PRICE = 0.92
UPSTREAM_LOCK_PAPER_MIN_BUCKET_DISTANCE_C = 1.0
UPSTREAM_LOCK_PAPER_ENTRY_FRACTION = 0.10
UPSTREAM_LOCK_PAPER_EVENT_CAP_FRACTION = 1.0
UPSTREAM_LOCK_PAPER_SETTLEMENT_UNCERTAINTY_FLOOR = 0.0
UPSTREAM_LOCK_PAPER_NOWCAST_SOURCES = frozenset(
    {"aviationweather-metar", "kma-aviation-metar", "kma-official-public-metars"}
)
def required_upstream_bucket_distance_c(
    *,
    source: str,
    station_id: str,
    temperature_metric: str | None,
    temperature_bucket: str | None,
    threshold_unit: str | None,
) -> float:
    """Require one full Celsius crossing for every accepted exact-C source."""
    return UPSTREAM_LOCK_PAPER_MIN_BUCKET_DISTANCE_C


def upstream_lock_paper_exact_no_tier(
    *,
    source: str,
    station_id: str,
    temperature_metric: str | None,
    temperature_bucket: str | None,
    threshold_unit: str | None,
) -> str:
    """Label new adjacent-boundary entries while retaining old replay labels."""
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


def upstream_settlement_probabilities(_settings: Any) -> tuple[float, float]:
    """Treat a crossed exact boundary as a certain NO in the paper strategy."""
    return 0.0, 1.0
