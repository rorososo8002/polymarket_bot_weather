from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weather_bot.config import Settings
from weather_bot.nowcast import StationNowcastObservation
from weather_bot.station_signal import estimate_station_signal


class ExactHighProvider:
    def __init__(self, observed_high_c: float) -> None:
        self.observed_high_c = observed_high_c

    def observed_temperature_extremes_so_far(self, station, *, target_date, now=None):
        return StationNowcastObservation(
            station_id=station.station_id,
            station_name=station.station_name,
            observed_high_c=self.observed_high_c,
            observed_at=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
            high_observed_at=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
            source="aviationweather-metar",
            source_url="https://aviationweather.gov/api/data/metar",
            settlement_source_url="https://www.wunderground.com/history/daily/hk/hong-kong/VHHH",
            freshness_seconds=120,
            unavailable_reason="",
            raw_observation_count=8,
            update_cadence="fixture",
        )


@pytest.mark.parametrize(
    ("observed_high_c", "expected_p_true", "expected_fraction", "expected_lock"),
    [
        (23.9, 0.5, None, ""),
        (24.0, 0.0, 0.50, "strong_no"),
        (23.4, 0.94, 0.20, "base_yes"),
        (23.2, 0.985, 0.50, "strong_yes"),
    ],
)
def test_exact_celsius_daily_high_station_lock_uses_source_display_integer(
    observed_high_c: float,
    expected_p_true: float,
    expected_fraction: float | None,
    expected_lock: str,
) -> None:
    now = datetime(2026, 6, 19, 14, 30, tzinfo=timezone.utc)

    signal = estimate_station_signal(
        "Will the highest temperature in Hong Kong be 23C today?",
        settings=Settings(),
        observation_provider=ExactHighProvider(observed_high_c),
        now=now,
    )

    assert signal.p_true == pytest.approx(expected_p_true)
    assert signal.entry_size_fraction_override == expected_fraction
    if expected_lock:
        assert f"official_nowcast_lock={expected_lock}" in signal.note
        assert "official-station-lock" in signal.source
    else:
        assert "official_nowcast_lock=" not in signal.note
        assert "official-station-lock" not in signal.source
