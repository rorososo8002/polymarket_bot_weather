from weather_bot.risk import fractional_kelly_binary, shrink_probability


def test_shrink_probability_toward_half():
    assert abs(shrink_probability(0.80, gamma=0.5) - 0.65) < 1e-9
    assert abs(shrink_probability(0.20, gamma=0.5) - 0.35) < 1e-9


def test_fractional_kelly_positive_edge():
    f = fractional_kelly_binary(
        p_true=0.70,
        p_eff=0.50,
        fractional_kelly=0.10,
        max_fraction=0.03,
        gamma=1.0,
    )
    assert 0 < f <= 0.03


def test_fractional_kelly_negative_edge_zero():
    f = fractional_kelly_binary(
        p_true=0.45,
        p_eff=0.50,
        fractional_kelly=0.10,
        max_fraction=0.03,
        gamma=1.0,
    )
    assert f == 0.0
