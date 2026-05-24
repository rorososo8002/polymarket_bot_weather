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
    high_words = r"above|over|at least|exceed|exceeds|reach|reaches|hit|hits|higher than|>=|이상"
    low_words = r"below|under|less than|at most|lower than|<=|이하|미만"
    degree = r"(?:[°º˚째]|도)?"
    unit_pattern = r"(c|f|celsius|fahrenheit|℃|℉|degrees|degree)?"

    match = re.search(
        rf"(?:{high_words}|{low_words})\s*\$?(\d{{1,3}}(?:\.\d+)?)\s*{degree}\s*{unit_pattern}",
        q,
        re.IGNORECASE,
    )
    if match:
        threshold_raw = float(match.group(1))
        unit_text = (match.group(2) or "").lower()
        prefix = match.group(0).lower()
        operator = "<=" if re.search(low_words, prefix, re.IGNORECASE) else ">="
    else:
        match = re.search(
            rf"(\d{{1,3}}(?:\.\d+)?)\s*{degree}\s*{unit_pattern}\s*(이상|이하|미만)?",
            q,
            re.IGNORECASE,
        )
        if not match or not (match.group(2) or match.group(3)):
            legacy_match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*�+", q)
            if legacy_match:
                threshold_raw = float(legacy_match.group(1))
                operator = "<=" if "�̸" in q or "����" in q else ">="
                return c_to_f(threshold_raw), "C", operator
            return None, "UNKNOWN", None
        threshold_raw = float(match.group(1))
        unit_text = (match.group(2) or "").lower()
        after_word = (match.group(3) or "").lower()
        tail = q[match.end():match.end() + 24].lower()
        operator = "<=" if after_word in {"이하", "미만"} or "or lower" in tail or "or less" in tail else ">="

    if unit_text in {"c", "celsius", "℃"}:
        return c_to_f(threshold_raw), "C", operator
    if unit_text in {"f", "fahrenheit", "℉", "degrees", "degree"}:
        return threshold_raw, "F", operator
    if "도" in match.group(0):
        return c_to_f(threshold_raw), "C", operator

    return threshold_raw, "F", operator


def _extract_precip_threshold(q: str) -> float | None:
    high_words = r"above|over|at least|exceed|exceeds|more than|greater than|이상|초과"
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
    if "rain" in q or "precip" in q or "wet" in q or "강수" in q or "비" in q:
        variable = "precipitation"
    elif "snow" in q or "눈" in q:
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
    if date_hint is None and ("today" in q or "오늘" in q):
        date_hint = "today"
    elif date_hint is None and ("tomorrow" in q or "내일" in q):
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
