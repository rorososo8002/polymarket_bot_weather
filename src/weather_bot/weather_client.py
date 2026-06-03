from __future__ import annotations

import re

from .models import ParsedWeatherQuestion
from .stations import CITY_COORDS

WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _extract_temp_threshold(q: str) -> tuple[float | None, str, str | None, str]:
    """Return threshold in Fahrenheit, original unit, operator, and bucket shape."""
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

    unit_match = re.search(
        rf"(?P<value>\d{{1,3}}(?:\.\d+)?){degree}{unit_pattern}\b",
        q,
        re.IGNORECASE,
    )
    if unit_match:
        threshold_raw = float(unit_match.group("value"))
        unit_text = (unit_match.group("unit") or "").lower()
        window = q[max(0, unit_match.start() - 40):unit_match.end() + 30]
        if re.search(low_words, window, re.IGNORECASE):
            operator = "<="
            bucket = "lower_tail" if re.search(r"\bor\s+(?:below|lower|less)\b|\uc774\ud558", window, re.IGNORECASE) else "threshold"
        elif re.search(high_words, window, re.IGNORECASE):
            operator = ">="
            bucket = "upper_tail" if re.search(r"\bor\s+(?:above|higher)\b|\uc774\uc0c1", window, re.IGNORECASE) else "threshold"
        else:
            operator = "=="
            bucket = "exact"
    else:
        comparison_match = re.search(
            rf"(?P<op>{high_words}|{low_words})[^\d]{{0,30}}(?P<value>\d{{1,3}}(?:\.\d+)?)",
            q,
            re.IGNORECASE,
        )
        if not comparison_match:
            return None, "UNKNOWN", None, "threshold"
        threshold_raw = float(comparison_match.group("value"))
        unit_text = ""
        operator = "<=" if re.search(low_words, comparison_match.group("op"), re.IGNORECASE) else ">="
        bucket = "threshold"

    if unit_text in {"c", "celsius", "\u2103", "\ub3c4"}:
        return c_to_f(threshold_raw), "C", operator, bucket
    return threshold_raw, "F", operator, bucket


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
    threshold_f = threshold_original = None
    threshold_unit: str = "UNKNOWN"
    operator = None
    temperature_metric = "max"
    temperature_bucket = "threshold"
    threshold_f, threshold_unit, operator, temperature_bucket = _extract_temp_threshold(q)
    if threshold_f is not None:
        threshold_original = round((threshold_f - 32.0) * 5.0 / 9.0, 6) if threshold_unit == "C" else threshold_f
    if re.search(r"\b(lowest|minimum|min(?:imum)?|overnight\s+low|low\s+temperature)\b", q):
        temperature_metric = "min"

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
        temperature_metric=temperature_metric,  # type: ignore[arg-type]
        temperature_bucket=temperature_bucket,  # type: ignore[arg-type]
    )
