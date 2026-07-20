from __future__ import annotations

from math import isfinite
from typing import Any


UPSTREAM_LOCK_PAPER_MODE = "upstream_lock_paper"
UPSTREAM_LOCK_PAPER_SIGNAL_FAMILY = "upstream_lock_paper"
UPSTREAM_LOCK_PAPER_EXACT_NO_TIER = "upstream_2c_exact_no"
UPSTREAM_LOCK_PAPER_MAX_ENTRY_PRICE = 0.85
UPSTREAM_LOCK_PAPER_MIN_BUCKET_DISTANCE_C = 2.0
UPSTREAM_LOCK_PAPER_ENTRY_FRACTION = 0.10
UPSTREAM_LOCK_PAPER_EVENT_CAP_FRACTION = 0.05
UPSTREAM_LOCK_PAPER_SETTLEMENT_UNCERTAINTY_FLOOR = 0.04
UPSTREAM_LOCK_PAPER_NOWCAST_SOURCES = frozenset(
    {"aviationweather-metar", "kma-aviation-metar"}
)


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
