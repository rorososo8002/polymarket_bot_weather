from __future__ import annotations

from .edge import clamp_probability


def shrink_probability(p_true: float, gamma: float = 0.65) -> float:
    """Shrink model probability toward 0.5 to reduce overconfidence."""
    p = clamp_probability(p_true)
    if not 0 <= gamma <= 1:
        raise ValueError("gamma must be between 0 and 1")
    return 0.5 + gamma * (p - 0.5)


def fractional_kelly_binary(
    p_true: float,
    p_eff: float,
    fractional_kelly: float,
    max_fraction: float,
    gamma: float = 0.65,
) -> float:
    """Fractional Kelly for binary YES-like share.

    Full Kelly for a share bought at p_eff with win probability p is:
        f* = (p - p_eff) / (1 - p_eff)

    Returns bankroll fraction, clipped to [0, max_fraction].
    """
    if not 0 < p_eff < 1:
        return 0.0
    if fractional_kelly <= 0:
        return 0.0
    if max_fraction <= 0:
        return 0.0
    p_adj = shrink_probability(p_true, gamma=gamma)
    f_raw = (p_adj - p_eff) / (1.0 - p_eff)
    f = fractional_kelly * f_raw
    return max(0.0, min(max_fraction, f))
