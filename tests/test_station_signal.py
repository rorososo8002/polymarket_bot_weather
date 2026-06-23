from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

import weather_bot.station_signal as station_signal_module
from weather_bot.config import Settings
from weather_bot.nowcast import StationNowcastObservation
from weather_bot.residual_probability import ResidualProbabilityEstimate
from weather_bot.settlement_precision import settlement_precision_profile_for_station
from weather_bot.station_signal import estimate_station_signal
from weather_bot.stations import TRADING_READY_STATION_MAP


def _residual_estimate(
    *,
    raw: float,
    yes: float,
    no: float,
    usable: bool = True,
    reason_code: str = "RESIDUAL_PROBABILITY_OK",
    sample_days: int = 120,
    profile_key: str = "RKSI|month:06|0930|high|C",
) -> ResidualProbabilityEstimate:
    return ResidualProbabilityEstimate(
        usable=usable,
        raw_probability=raw,
        conservative_yes_probability=yes,
        conservative_no_probability=no,
        successes=round(raw * sample_days) if usable else 0,
        sample_days=sample_days if usable else 0,
        profile_key=profile_key if usable else "",
        profile_scope="month" if usable else "",
        reason_code=reason_code,
        reason="fixture residual estimate",
    )


class FakeResidualProfileStore:
    def __init__(
        self,
        estimate: ResidualProbabilityEstimate,
        *,
        monitoring_start_local_minute: int = 0,
        movement_probability: float = 0.35,
    ) -> None:
        self.estimate = estimate
        self.calls: list[dict] = []
        self.monitoring_start_local_minute = monitoring_start_local_minute
        self.remaining_movement_probability = movement_probability

    def formation_window(self, **_kwargs):
        return {
            "monitoring_start_local_minute": self.monitoring_start_local_minute,
            "first_final_high_local_minute": {"q25": 750.0, "median": 810.0, "q75": 870.0},
            "first_final_low_local_minute": {"q25": 60.0, "median": 180.0, "q75": 300.0},
            "occurrence_sample_days": 150,
            "station_timezone": "Asia/Seoul",
        }

    def movement_probability(self, **_kwargs):
        return self.remaining_movement_probability

    def estimate_bucket(self, **kwargs):
        self.calls.append(kwargs)
        return self.estimate


class ExactTemperatureProvider:
    def __init__(
        self,
        *,
        observed_high_c: float | None = None,
        observed_low_c: float | None = None,
        station_id: str | None = None,
        freshness_seconds: int | None = 120,
        source: str = "official-station-fixture",
        daily_extremes_complete: bool = True,
        data_block_reason: str = "",
    ) -> None:
        self.observed_high_c = observed_high_c
        self.observed_low_c = observed_low_c
        self.station_id = station_id
        self.freshness_seconds = freshness_seconds
        self.source = source
        self.daily_extremes_complete = daily_extremes_complete
        self.data_block_reason = data_block_reason
        self.calls = 0

    def observed_temperature_extremes_so_far(self, station, *, target_date, now=None):
        self.calls += 1
        observed_at = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)
        return StationNowcastObservation(
            station_id=self.station_id or station.station_id,
            station_name=station.station_name,
            observed_high_c=self.observed_high_c,
            observed_low_c=self.observed_low_c,
            observed_at=observed_at,
            high_observed_at=observed_at if self.observed_high_c is not None else None,
            low_observed_at=observed_at if self.observed_low_c is not None else None,
            source=self.source,
            source_url="https://example.test/observations",
            settlement_source_url="https://example.test/settlement",
            freshness_seconds=self.freshness_seconds,
            unavailable_reason="",
            raw_observation_count=8,
            update_cadence="fixture",
            daily_extremes_complete=self.daily_extremes_complete,
            data_block_reason=self.data_block_reason,
        )


def _estimate_seoul_high(observed_high_c: float, **kwargs):
    return estimate_station_signal(
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=observed_high_c),
        now=datetime(2026, 6, 19, 14, 30, tzinfo=timezone.utc),
        **kwargs,
    )


def test_incomplete_metar_daily_extremes_block_signal_before_probability_calculation() -> None:
    store = FakeResidualProfileStore(
        _residual_estimate(raw=0.99, yes=0.97, no=0.01)
    )
    signal = estimate_station_signal(
        "Will the highest temperature in Manila be 32C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(
            observed_high_c=32.0,
            source="aviationweather-metar",
            daily_extremes_complete=False,
            data_block_reason="metar-daily-extremes-baseline-missing",
        ),
        now=datetime(2026, 6, 19, 7, 0, tzinfo=timezone.utc),
        residual_profile_store=store,
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.confidence == 0.0
    assert signal.source == "official-station-unavailable"
    assert "metar-daily-extremes-baseline-missing" in signal.note
    assert store.calls == []


def test_whole_celsius_high_exact_bucket_23_9_still_inside_23() -> None:
    signal = _estimate_seoul_high(23.9)

    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert "official_nowcast_lock=" not in signal.note
    assert signal.nowcast["settlement_precision_confidence"] == "verified"
    assert signal.nowcast["settlement_precision_bucket_model"] == "whole_degree_source_display_band"


def test_whole_celsius_high_exact_bucket_24_0_breaks_23() -> None:
    signal = _estimate_seoul_high(24.0)

    assert signal.p_true == pytest.approx(0.0)
    assert signal.entry_size_fraction_override == pytest.approx(0.50)
    assert "official_nowcast_lock=strong_no" in signal.note
    assert "official-station-lock" in signal.source


def test_whole_celsius_low_exact_bucket_22_9_breaks_23() -> None:
    signal = estimate_station_signal(
        "Will the lowest temperature in Seoul be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_low_c=22.9),
        now=datetime(2026, 6, 19, 14, 30, tzinfo=timezone.utc),
    )

    assert signal.p_true == pytest.approx(0.0)
    assert signal.entry_size_fraction_override == pytest.approx(0.50)
    assert "official_nowcast_lock=strong_no" in signal.note
    assert "observed_low_c=22.9 < displayed_bucket_lower_c=23.0" in signal.note


def test_near_close_inside_bucket_does_not_use_fixed_yes_lock_without_residual_profile() -> None:
    signal = _estimate_seoul_high(23.2)

    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert "official_nowcast_lock=base_yes" not in signal.note
    assert "official_nowcast_lock=strong_yes" not in signal.note


def test_hko_decimal_profile_marks_needs_audit() -> None:
    profile = settlement_precision_profile_for_station(TRADING_READY_STATION_MAP["hong kong"])

    assert profile.unit == "C"
    assert profile.reporting_precision == "0.1C"
    assert profile.bucket_model == "one_decimal_range_containing"
    assert profile.confidence == "needs_audit"

    signal = estimate_station_signal(
        "Will the highest temperature in Hong Kong be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=23.2),
        now=datetime(2026, 6, 19, 14, 30, tzinfo=timezone.utc),
    )
    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert signal.nowcast["settlement_precision_confidence"] == "needs_audit"
    assert "settlement_precision_confidence=needs_audit" in signal.note


def test_unknown_precision_blocks_confident_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ExactTemperatureProvider(observed_high_c=24.0)
    station = replace(TRADING_READY_STATION_MAP["seoul"], reporting_precision="UNKNOWN")
    monkeypatch.setitem(station_signal_module.TRADING_READY_STATION_MAP, "seoul", station)

    signal = estimate_station_signal(
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(),
        observation_provider=provider,
        now=datetime(2026, 6, 19, 14, 30, tzinfo=timezone.utc),
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.confidence == pytest.approx(0.0)
    assert signal.source == "unsupported-settlement-precision"
    assert "settlement_precision_confidence=blocked" in signal.note
    assert provider.calls == 0


def test_residual_high_exact_96_percent_forces_fifty_percent_target() -> None:
    store = FakeResidualProfileStore(
        _residual_estimate(raw=0.97, yes=0.96, no=0.02)
    )

    signal = estimate_station_signal(
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=23.4),
        now=datetime(2026, 6, 19, 6, 30, tzinfo=timezone.utc),
        residual_profile_store=store,
        concentrated_sizing_eligible_by_station={"RKSI": False},
    )

    assert signal.source == "official-station-residual-high-yes"
    assert signal.p_true == pytest.approx(0.97)
    assert signal.raw_probability == pytest.approx(0.97)
    assert signal.conservative_yes_probability == pytest.approx(0.96)
    assert signal.conservative_no_probability == pytest.approx(0.02)
    assert signal.selected_side_probability == pytest.approx(0.96)
    assert signal.entry_size_fraction_override == pytest.approx(0.50)
    assert signal.probability_tier == "95"
    assert signal.event_cap_override_fraction == pytest.approx(0.50)
    assert signal.calibration_sample_days == 120
    assert signal.calibration_profile_key == "RKSI|month:06|0930|high|C"
    assert signal.calibration_status == "RESIDUAL_PROBABILITY_OK"
    assert store.calls == [
        {
            "station_id": "RKSI",
            "month": 6,
            "local_minute": 930,
            "direction": "high",
            "observed_extreme": pytest.approx(23.4),
            "bucket_type": "exact",
            "bucket_lower": 23.0,
            "bucket_upper": 24.0,
            "unit": "C",
        }
    ]
    assert signal.nowcast["formation_monitoring_status"] == "started"
    assert signal.nowcast["remaining_movement_probability"] == pytest.approx(0.35)
    assert signal.nowcast["station_local_date"] == "2026-06-19"
    assert signal.nowcast["station_local_time"] == "15:30"


def test_city_month_high_is_blocked_before_profile_monitoring_start() -> None:
    store = FakeResidualProfileStore(
        _residual_estimate(raw=0.97, yes=0.96, no=0.02),
        monitoring_start_local_minute=13 * 60,
    )

    signal = estimate_station_signal(
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=23.4),
        now=datetime(2026, 6, 19, 3, 30, tzinfo=timezone.utc),
        residual_profile_store=store,
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.source == "official-station-formation-window"
    assert signal.nowcast["formation_monitoring_status"] == "before_start"
    assert signal.nowcast["monitoring_start_local_minute"] == 780
    assert store.calls == []


def test_city_month_high_waits_for_q75_after_profile_monitoring_start() -> None:
    store = FakeResidualProfileStore(
        _residual_estimate(raw=0.97, yes=0.96, no=0.02),
        monitoring_start_local_minute=13 * 60,
    )

    signal = estimate_station_signal(
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=23.4),
        now=datetime(2026, 6, 19, 4, 0, tzinfo=timezone.utc),
        residual_profile_store=store,
    )

    assert signal.source == "official-station-formation-q75"
    assert signal.nowcast["formation_monitoring_status"] == "started"
    assert signal.nowcast["data_block_reason"] == "formation-q75-not-reached"
    assert store.calls == []


def test_city_month_low_changes_from_before_to_after_monitoring_start() -> None:
    estimate = _residual_estimate(
        raw=0.98,
        yes=0.97,
        no=0.01,
        profile_key="RKSI|month:06|0180|low|C",
    )
    store = FakeResidualProfileStore(estimate, monitoring_start_local_minute=3 * 60)
    provider = ExactTemperatureProvider(observed_low_c=20.0)

    before = estimate_station_signal(
        "Will the lowest temperature in Seoul be 20C or below today?",
        settings=Settings(),
        observation_provider=provider,
        now=datetime(2026, 6, 18, 17, 30, tzinfo=timezone.utc),
        residual_profile_store=store,
    )
    after = estimate_station_signal(
        "Will the lowest temperature in Seoul be 20C or below today?",
        settings=Settings(),
        observation_provider=provider,
        now=datetime(2026, 6, 18, 18, 0, tzinfo=timezone.utc),
        residual_profile_store=store,
    )

    assert before.source == "official-station-formation-window"
    assert before.nowcast["formation_monitoring_status"] == "before_start"
    assert after.source == "official-station-residual-low-yes"
    assert after.nowcast["formation_monitoring_status"] == "started"


def test_hko_carryover_observation_cannot_create_strong_no() -> None:
    provider = ExactTemperatureProvider(observed_high_c=33.1)
    original = provider.observed_temperature_extremes_so_far

    def blocked(station, *, target_date, now=None):
        return replace(
            original(station, target_date=target_date, now=now),
            unavailable_reason="hko-midnight-reset-pending",
            midnight_reset_status="pending_previous_day_match",
        )

    provider.observed_temperature_extremes_so_far = blocked
    signal = estimate_station_signal(
        "Will the highest temperature in Hong Kong be 32C today?",
        settings=Settings(),
        observation_provider=provider,
        now=datetime(2026, 6, 21, 16, 10, tzinfo=timezone.utc),
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert signal.source == "official-station-unavailable"
    assert "hko-midnight-reset-pending" in signal.note


def test_hko_reset_verified_strong_no_is_capped_because_settlement_needs_audit() -> None:
    signal = estimate_station_signal(
        "Will the highest temperature in Hong Kong be 32C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=33.1),
        now=datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc),
    )

    assert signal.source == "official-station-lock-strong_no"
    assert signal.p_true == pytest.approx(0.03)
    assert signal.entry_size_fraction_override == pytest.approx(0.125)
    assert "hko_needs_audit_probability_cap=0.97" in signal.note
    assert "hko_needs_audit_multiplier=0.25" in signal.note


@pytest.mark.parametrize(
    ("conservative_probability", "expected_tier", "expected_fraction", "expected_override"),
    [
        (0.944, "90", 0.30, 0.30),
        (0.90, "90", 0.30, 0.30),
        (0.84, "80", 0.10, None),
    ],
)
def test_residual_high_exact_maps_conservative_probability_to_tiers(
    conservative_probability: float,
    expected_tier: str,
    expected_fraction: float,
    expected_override: float | None,
) -> None:
    store = FakeResidualProfileStore(
        _residual_estimate(raw=0.96, yes=conservative_probability, no=0.03)
    )

    signal = estimate_station_signal(
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=23.4),
        now=datetime(2026, 6, 19, 6, 30, tzinfo=timezone.utc),
        residual_profile_store=store,
        concentrated_sizing_eligible_by_station={"RKSI": True},
    )

    assert signal.selected_side_probability == pytest.approx(conservative_probability)
    assert signal.probability_tier == expected_tier
    if expected_fraction is None:
        assert signal.entry_size_fraction_override is None
    else:
        assert signal.entry_size_fraction_override == pytest.approx(expected_fraction)
    assert signal.event_cap_override_fraction == expected_override


def test_residual_high_exact_before_city_month_q75_is_blocked() -> None:
    store = FakeResidualProfileStore(
        _residual_estimate(raw=0.94, yes=0.90, no=0.04),
        movement_probability=0.34,
    )

    signal = estimate_station_signal(
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=23.4),
        now=datetime(2026, 6, 19, 5, 0, tzinfo=timezone.utc),
        residual_profile_store=store,
    )

    assert signal.confidence == 0.0
    assert signal.entry_size_fraction_override is None
    assert signal.nowcast["data_block_reason"] == "formation-q75-not-reached"
    assert store.calls == []


def test_residual_below_80_percent_skips_instead_of_using_fixed_guess() -> None:
    store = FakeResidualProfileStore(_residual_estimate(raw=0.89, yes=0.799, no=0.04))

    signal = estimate_station_signal(
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=23.4),
        now=datetime(2026, 6, 19, 6, 30, tzinfo=timezone.utc),
        residual_profile_store=store,
        concentrated_sizing_eligible_by_station={"RKSI": True},
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert signal.selected_side_probability == pytest.approx(0.799)
    assert signal.calibration_status == "RESIDUAL_PROBABILITY_OK"
    assert "below observation tier" in signal.note


def test_europe_high_before_15_local_does_not_get_intraday_yes() -> None:
    signal = estimate_station_signal(
        "Will the highest temperature in Paris be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=23.2),
        now=datetime(2026, 6, 19, 12, 30, tzinfo=timezone.utc),
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert "official-station-intraday" not in signal.source


def test_europe_high_after_15_local_can_get_intraday_yes() -> None:
    signal = estimate_station_signal(
        "Will the highest temperature in Paris be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=23.2),
        now=datetime(2026, 6, 19, 13, 30, tzinfo=timezone.utc),
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert "official-station-intraday" not in signal.source


def test_us_high_before_15_local_does_not_get_intraday_yes() -> None:
    signal = estimate_station_signal(
        "Will the highest temperature in Austin be 80F today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=26.777778),
        now=datetime(2026, 6, 19, 19, 30, tzinfo=timezone.utc),
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert "official-station-intraday" not in signal.source


def test_lower_tail_low_observed_below_threshold_gets_strong_yes() -> None:
    store = FakeResidualProfileStore(
        _residual_estimate(
            raw=0.98,
            yes=0.97,
            no=0.01,
            profile_key="KAUS|month:06|0900|low|F",
        )
    )

    signal = estimate_station_signal(
        "Will the lowest temperature in Austin be 70F or below today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_low_c=20.555556),
        now=datetime(2026, 6, 19, 14, 0, tzinfo=timezone.utc),
        residual_profile_store=store,
        concentrated_sizing_eligible_by_station={"KAUS": True},
    )

    assert signal.parsed is not None
    assert signal.parsed.temperature_bucket == "lower_tail"
    assert signal.p_true == pytest.approx(0.98)
    assert signal.entry_size_fraction_override == pytest.approx(0.50)
    assert signal.event_cap_override_fraction == pytest.approx(0.50)
    assert signal.source == "official-station-residual-low-yes"
    assert store.calls[0]["direction"] == "low"
    assert store.calls[0]["observed_extreme"] == pytest.approx(69.0, abs=0.01)
    assert store.calls[0]["bucket_type"] == "lower_tail"
    assert store.calls[0]["bucket_upper"] == pytest.approx(70.0)


def test_low_exact_observed_below_lower_gets_strong_no() -> None:
    signal = estimate_station_signal(
        "Will the lowest temperature in Seoul be 23C today?",
        settings=Settings(
            strategy_mode="intraday_observation_edge",
            official_nowcast_lock_enabled=False,
        ),
        observation_provider=ExactTemperatureProvider(observed_low_c=22.9),
        now=datetime(2026, 6, 19, 6, 30, tzinfo=timezone.utc),
    )

    assert signal.p_true == pytest.approx(0.0)
    assert signal.entry_size_fraction_override == pytest.approx(0.25)
    assert signal.source == "official-station-intraday-low-strong-no"


def test_hko_needs_audit_cannot_use_residual_or_older_lock_sizing() -> None:
    store = FakeResidualProfileStore(
        _residual_estimate(
            raw=0.99,
            yes=0.98,
            no=0.01,
            profile_key="HKO|month:06|1630|high|C",
        )
    )

    signal = estimate_station_signal(
        "Will the highest temperature in Hong Kong be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=23.2),
        now=datetime(2026, 6, 19, 8, 30, tzinfo=timezone.utc),
        residual_profile_store=store,
        concentrated_sizing_eligible_by_station={"HKO": True},
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert signal.event_cap_override_fraction is None
    assert "settlement_precision_confidence=needs_audit" in signal.note
    assert store.calls == []


def test_residual_missing_profile_does_not_fall_back_to_fixed_probability() -> None:
    store = FakeResidualProfileStore(
        _residual_estimate(
            raw=0.0,
            yes=0.0,
            no=0.0,
            usable=False,
            reason_code="SKIP_RESIDUAL_PROFILE_MISSING",
        )
    )

    signal = estimate_station_signal(
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(observed_high_c=23.4),
        now=datetime(2026, 6, 19, 6, 30, tzinfo=timezone.utc),
        residual_profile_store=store,
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert signal.calibration_status == "SKIP_RESIDUAL_PROFILE_MISSING"
    assert "missing" in signal.note.lower()


def test_stale_observation_fails_closed_before_residual_store_call() -> None:
    store = FakeResidualProfileStore(_residual_estimate(raw=0.97, yes=0.96, no=0.02))

    signal = estimate_station_signal(
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(station_nowcast_freshness_seconds=60),
        observation_provider=ExactTemperatureProvider(
            observed_high_c=23.4,
            freshness_seconds=120,
        ),
        now=datetime(2026, 6, 19, 6, 30, tzinfo=timezone.utc),
        residual_profile_store=store,
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert "stale" in signal.note.lower()
    assert store.calls == []


def test_wrong_station_observation_fails_closed_before_residual_store_call() -> None:
    store = FakeResidualProfileStore(_residual_estimate(raw=0.97, yes=0.96, no=0.02))

    signal = estimate_station_signal(
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(),
        observation_provider=ExactTemperatureProvider(
            observed_high_c=23.4,
            station_id="WRONG",
        ),
        now=datetime(2026, 6, 19, 6, 30, tzinfo=timezone.utc),
        residual_profile_store=store,
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert "station mismatch" in signal.note.lower()
    assert store.calls == []


def test_lock_only_mode_does_not_emit_intraday_yes() -> None:
    signal = estimate_station_signal(
        "Will the highest temperature in Seoul be 23C today?",
        settings=Settings(strategy_mode="lock_only"),
        observation_provider=ExactTemperatureProvider(observed_high_c=23.2),
        now=datetime(2026, 6, 19, 7, 30, tzinfo=timezone.utc),
    )

    assert signal.p_true == pytest.approx(0.5)
    assert signal.entry_size_fraction_override is None
    assert "official-station-intraday" not in signal.source
