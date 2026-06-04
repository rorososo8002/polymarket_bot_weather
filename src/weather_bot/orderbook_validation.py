from __future__ import annotations

import math
from typing import Any


def finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def valid_orderbook_price(value: Any) -> float | None:
    price = finite_float(value)
    if price is None or not 0.0 < price < 1.0:
        return None
    return price


def valid_level_size(value: Any, *, allow_zero: bool = False) -> float | None:
    size = finite_float(value)
    if size is None:
        return None
    if allow_zero:
        return size if size >= 0.0 else None
    return size if size > 0.0 else None
