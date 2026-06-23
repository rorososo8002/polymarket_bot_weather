import pytest

from weather_bot.edge import (
    ObservationSizingTier,
    observation_edge_entry_fraction,
    observation_probability_tier,
    observation_selected_side_probability,
)
from weather_bot.models import EdgeResult, WeatherSignal


@pytest.mark.parametrize(
    ("probability", "expected_tier", "expected_fraction", "expected_override"),
    [
        (0.799999, None, None, None),
        (0.80, "80", 0.10, None),
        (0.899999, "80", 0.10, None),
        (0.90, "90", 0.30, 0.30),
        (0.949999, "90", 0.30, 0.30),
        (0.95, "95", 0.50, 0.50),
        (1.0, "95", 0.50, 0.50),
    ],
)
def test_observation_probability_tier_boundaries(
    probability,
    expected_tier,
    expected_fraction,
    expected_override,
):
    tier = observation_edge_entry_fraction(
        probability,
        tier_80_probability=0.80,
        tier_90_probability=0.90,
        tier_95_probability=0.95,
        tier_80_fraction=0.10,
        tier_90_fraction=0.30,
        tier_95_fraction=0.50,
    )

    if expected_tier is None:
        assert tier is None
        return

    assert tier == ObservationSizingTier(
        probability_tier=expected_tier,
        entry_fraction=expected_fraction,
        event_cap_override_fraction=expected_override,
    )


def test_yes_tier_uses_separate_conservative_yes_probability():
    yes_probability = observation_selected_side_probability(
        "YES",
        conservative_yes_probability=0.91,
        conservative_no_probability=0.97,
    )

    assert yes_probability == pytest.approx(0.91)
    assert observation_edge_entry_fraction(yes_probability) == ObservationSizingTier("90", 0.30, 0.30)


def test_no_tier_uses_separate_conservative_no_probability():
    signal = WeatherSignal(
        0.04,
        0.9,
        "test",
        "test",
        raw_probability=0.04,
        conservative_yes_probability=0.01,
        conservative_no_probability=0.91,
        raw_selected_side_probability=0.96,
        selected_side_probability=0.91,
    )

    no_probability = observation_selected_side_probability(
        "NO",
        conservative_yes_probability=signal.conservative_yes_probability,
        conservative_no_probability=signal.conservative_no_probability,
    )
    tier = observation_probability_tier(no_probability)

    assert signal.raw_probability == 0.04
    assert no_probability == pytest.approx(0.91)
    assert no_probability != pytest.approx(1.0 - signal.raw_probability)
    assert tier == ObservationSizingTier("90", 0.30, 0.30)


def test_weather_signal_and_edge_result_probability_metadata_defaults_are_backward_compatible():
    signal = WeatherSignal(0.04, 0.9, "test", "test")
    result = EdgeResult("NO", 0.04, 0.20, 0.10, 10.0, 50.0, "test")

    for item in (signal, result):
        assert item.raw_probability is None
        assert item.conservative_yes_probability is None
        assert item.conservative_no_probability is None
        assert item.raw_selected_side_probability is None
        assert item.selected_side_probability is None
        assert item.calibration_sample_days == 0
        assert item.calibration_profile_key == ""
        assert item.calibration_status == ""
        assert item.probability_tier == ""
        assert item.event_cap_override_fraction is None


def test_observation_edge_entry_fraction_returns_structured_configured_tier():
    tier = observation_edge_entry_fraction(
        0.97,
        tier_80_probability=0.81,
        tier_90_probability=0.91,
        tier_95_probability=0.96,
        tier_80_fraction=0.11,
        tier_90_fraction=0.26,
        tier_95_fraction=0.49,
    )

    assert tier == ObservationSizingTier("95", 0.49, 0.49)


def test_old_fixed_probability_arguments_are_not_part_of_the_tier_api():
    with pytest.raises(TypeError):
        observation_edge_entry_fraction(
            0.97,
            min_side_probability=0.90,
            strong_side_probability=0.97,
            abnormal_entry_fraction=0.35,
        )
