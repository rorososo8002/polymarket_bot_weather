from __future__ import annotations

import re
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from .models import ParsedWeatherQuestion


CITY_COORDS: dict[str, tuple[float, float]] = {
    "seoul": (37.5665, 126.9780),
    "busan": (35.1796, 129.0756),
    "tokyo": (35.6762, 139.6503),
    "osaka": (34.6937, 135.5023),
    "beijing": (39.9042, 116.4074),
    "shanghai": (31.2304, 121.4737),
    "hong kong": (22.3193, 114.1694),
    "singapore": (1.3521, 103.8198),
    "bangkok": (13.7563, 100.5018),
    "manila": (14.5995, 120.9842),
    "sydney": (-33.8688, 151.2093),
    "melbourne": (-37.8136, 144.9631),
    "new york": (40.7789, -73.9692),
    "nyc": (40.7789, -73.9692),
    "chicago": (41.9950, -87.9336),
    "los angeles": (34.0522, -118.2437),
    "miami": (25.7617, -80.1918),
    "austin": (30.2672, -97.7431),
    "las vegas": (36.1699, -115.1398),
    "philadelphia": (39.9526, -75.1652),
    "boston": (42.3601, -71.0589),
    "washington dc": (38.9072, -77.0369),
    "washington": (38.9072, -77.0369),
    "dallas": (32.8998, -97.0403),
    "phoenix": (33.4484, -112.0740),
    "seattle": (47.6062, -122.3321),
    "denver": (39.8561, -104.6737),
    "atlanta": (33.6407, -84.4277),
    "orlando": (28.5383, -81.3792),
    "san francisco": (37.7749, -122.4194),
    "houston": (29.7604, -95.3698),
    "london": (51.5072, -0.1276),
    "paris": (48.8566, 2.3522),
    "berlin": (52.5200, 13.4050),
    "madrid": (40.4168, -3.7038),
    "rome": (41.9028, 12.4964),
    "amsterdam": (52.3676, 4.9041),
}

WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class OpenMeteoClient:
    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
    def forecast_daily(self, latitude: float, longitude: float, forecast_days: int = 7) -> dict[str, Any]:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "forecast_days": forecast_days,
        }
        resp = requests.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _extract_temp_threshold(q: str) -> tuple[float | None, str, str | None]:
    """Return threshold in Fahrenheit, original unit, and comparison operator."""
    high_words = (
        r"(?:\babove\b|\bover\b|\bat\s+least\b|\bexceed(?:s)?\b|\breach(?:es)?\b|"
        r"\bhit(?:s)?\b|\bhigher(?:\s+than)?\b|\bor\s+higher\b|>=|\bhigh\b|\uc774\uc0c1)"
    )
    low_words = (
        r"(?:\bbelow\b|\bunder\b|\bless\s+than\b|\bat\s+most\b|\blower\s+than\b|"
        r"<=|\blow(?:er)?\b|\bor\s+lower\b|\bor\s+less\b|\uc774\ud558|\ubbf8\ub9cc)"
    )
    degree = r"(?:\s*(?:\u00b0|\u00ba|\u02da|\uc9f8)\s*)?"
    unit_pattern = r"(?P<unit>f|c|fahrenheit|celsius|degrees?|degree|\u2103|\u2109|\ub3c4)"

    comparison_match = re.search(
        rf"(?P<op>{high_words}|{low_words})[^\d]{{0,30}}(?P<value>\d{{1,3}}(?:\.\d+)?)"
        rf"(?:{degree}{unit_pattern}\b)?",
        q,
        re.IGNORECASE,
    )
    if comparison_match:
        threshold_raw = float(comparison_match.group("value"))
        unit_text = (comparison_match.group("unit") or "").lower()
        operator = "<=" if re.search(low_words, comparison_match.group("op"), re.IGNORECASE) else ">="
    else:
        unit_match = re.search(
            rf"(?P<value>\d{{1,3}}(?:\.\d+)?){degree}{unit_pattern}\b",
            q,
            re.IGNORECASE,
        )
        if not unit_match:
            return None, "UNKNOWN", None
        threshold_raw = float(unit_match.group("value"))
        unit_text = (unit_match.group("unit") or "").lower()
        window = q[max(0, unit_match.start() - 40):unit_match.end() + 30]
        operator = "<=" if re.search(low_words, window, re.IGNORECASE) else ">="

    if unit_text in {"c", "celsius", "\u2103", "\ub3c4"}:
        return c_to_f(threshold_raw), "C", operator
    return threshold_raw, "F", operator


def _extract_precip_threshold(q: str) -> float | None:
    high_words = r"above|over|at least|exceed|exceeds|more than|greater than"
    match = re.search(rf"(?:{high_words})\s*(\d+(?:\.\d+)?)\s*(mm|millimeter|inch|inches)\b", q, re.IGNORECASE)
    if match:
        value = float(match.group(1))
        unit = match.group(2).lower()
        return value * 25.4 if "inch" in unit else value

    match = re.search(r"(\d+(?:\.\d+)?)\s*(mm|inch|inches)\s*(?:of\s+)?(?:rain|rainfall|precipitation)", q, re.IGNORECASE)
    if match:
        value = float(match.group(1))
        unit = match.group(2).lower()
        return value * 25.4 if "inch" in unit else value
    return None


def parse_weather_question(question: str) -> ParsedWeatherQuestion:
    q = question.lower()
    city = None
    lat = lon = None
    for name in sorted(CITY_COORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", q):
            city = name
            lat, lon = CITY_COORDS[name]
            break

    variable = "temperature"
    if re.search(r"\b(rain|rainfall|precip(?:itation)?|wet)\b", q) or "\uac15\uc218" in q or "\ube44" in q:
        variable = "precipitation"
    elif re.search(r"\b(snow|snowfall)\b", q) or "\ub208" in q:
        variable = "snow"

    threshold_f = threshold_original = None
    threshold_unit: str = "UNKNOWN"
    operator = None
    if variable == "temperature":
        threshold_f, threshold_unit, operator = _extract_temp_threshold(q)
        if threshold_f is not None:
            threshold_original = (threshold_f - 32.0) * 5.0 / 9.0 if threshold_unit == "C" else threshold_f

    threshold_precip_mm: float | None = None
    if variable in {"precipitation", "snow"}:
        threshold_precip_mm = _extract_precip_threshold(q)

    date_hint = None
    month_match = re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b", q)
    if month_match:
        date_hint = month_match.group(0)
    else:
        weekday_match = re.search(r"\b(" + "|".join(WEEKDAY_NAMES) + r")\b", q)
        if weekday_match:
            date_hint = weekday_match.group(1)
    if date_hint is None and ("today" in q or "\uc624\ub298" in q):
        date_hint = "today"
    elif date_hint is None and ("tomorrow" in q or "\ub0b4\uc77c" in q):
        date_hint = "tomorrow"

    confidence = 0.0
    notes: list[str] = []
    if city:
        confidence += 0.35
    else:
        notes.append("city not parsed")
    if variable == "temperature" and threshold_f is not None and operator is not None:
        confidence += 0.45
    elif variable == "precipitation":
        confidence += 0.35
    else:
        notes.append("event condition not fully parsed")
    if date_hint:
        confidence += 0.05
    else:
        notes.append("exact event date not parsed; using 7-day forecast horizon")

    return ParsedWeatherQuestion(
        city=city,
        latitude=lat,
        longitude=lon,
        threshold_f=threshold_f,
        threshold_original=threshold_original,
        threshold_unit=threshold_unit,  # type: ignore[arg-type]
        operator=operator,
        variable=variable,
        date_hint=date_hint,
        confidence=min(confidence, 0.90),
        note="; ".join(notes),
        threshold_precip_mm=threshold_precip_mm,
    )
