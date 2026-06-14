from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import math
import re

from .models import ParsedWeatherQuestion
from .stations import CITY_COORDS

WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
NON_TEMPERATURE_WEATHER_RE = re.compile(
    r"\b(?:"
    r"rain|rains|raining|rainfall|precip|precipitation|"
    r"snow|snows|snowing|snowfall|sleet|hail|"
    r"wind|winds|windy|humidity|humid"
    r")\b",
    re.IGNORECASE,
)
TEMPERATURE_CONTEXT_RE = re.compile(
    r"\b(?:temperature|temperatures|temp|highest|lowest|maximum|minimum|max|min|fahrenheit|celsius)\b",
    re.IGNORECASE,
)
TEMPERATURE_COMPARISON_UNIT = "millifahrenheit"
_TEMPERATURE_MILLIFAHRENHEIT_SCALE = Decimal("1000")
_TEMPERATURE_MILLIFAHRENHEIT_QUANT = Decimal("1")


def temperature_f_to_millif(value_f: float) -> int:
    """Scale Fahrenheit to an integer comparison unit."""
    if not math.isfinite(value_f):
        raise ValueError(f"Temperature must be finite for {TEMPERATURE_COMPARISON_UNIT} comparison.")
    return int(
        (Decimal(str(value_f)) * _TEMPERATURE_MILLIFAHRENHEIT_SCALE).quantize(
            _TEMPERATURE_MILLIFAHRENHEIT_QUANT,
            rounding=ROUND_HALF_UP,
        )
    )


def temperature_compare_f(left_f: float, right_f: float) -> int:
    """Compare Fahrenheit values using the centralized bucket precision."""
    if math.isnan(left_f) or math.isnan(right_f):
        raise ValueError("Temperature comparison does not accept NaN.")
    if math.isfinite(left_f) and math.isfinite(right_f):
        left_millif = temperature_f_to_millif(left_f)
        right_millif = temperature_f_to_millif(right_f)
        return (left_millif > right_millif) - (left_millif < right_millif)
    if left_f == right_f:
        return 0
    return 1 if left_f > right_f else -1


def temperature_gt_f(left_f: float, right_f: float) -> bool:
    return temperature_compare_f(left_f, right_f) > 0


def temperature_gte_f(left_f: float, right_f: float) -> bool:
    return temperature_compare_f(left_f, right_f) >= 0


def temperature_lt_f(left_f: float, right_f: float) -> bool:
    return temperature_compare_f(left_f, right_f) < 0


def temperature_lte_f(left_f: float, right_f: float) -> bool:
    return temperature_compare_f(left_f, right_f) <= 0


@dataclass(frozen=True)
class _TemperatureThreshold:
    threshold_f: float | None
    threshold_unit: str
    operator: str | None
    bucket: str
    range_lower_f: float | None = None
    range_upper_f: float | None = None
    range_lower_original: float | None = None
    range_upper_original: float | None = None
    range_inclusive: bool = False


@dataclass(frozen=True)
class TemperatureBucketInterval:
    lower_f: float
    upper_f: float
    lower_inclusive: bool
    upper_inclusive: bool
    original_unit: str = "F"
    comparison_unit: str = TEMPERATURE_COMPARISON_UNIT

    def as_tuple(self) -> tuple[float, float]:
        return self.lower_f, self.upper_f

    def contains_f(self, value_f: float) -> bool:
        if not math.isfinite(value_f):
            return False
        lower_ok = (
            temperature_gte_f(value_f, self.lower_f)
            if self.lower_inclusive
            else temperature_gt_f(value_f, self.lower_f)
        )
        upper_ok = (
            temperature_lte_f(value_f, self.upper_f)
            if self.upper_inclusive
            else temperature_lt_f(value_f, self.upper_f)
        )
        return lower_ok and upper_ok


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _unit_label(unit_text: str) -> str:
    unit_text = unit_text.lower()
    if unit_text in {"c", "celsius", "\u2103", "\ub3c4"}:
        return "C"
    if unit_text == "unknown":
        return "UNKNOWN"
    return "F"


def _to_f(value: float, unit_label: str) -> float:
    return c_to_f(value) if unit_label == "C" else value


def _extract_temp_threshold(q: str) -> _TemperatureThreshold:
    """Return threshold in Fahrenheit, original unit, operator, and bucket shape."""
    high_words = (
        r"(?:\babove\b|\bover\b|\bat\s+least\b|\bexceed(?:s)?\b|\breach(?:es)?\b|"
        r"\bhit(?:s)?\b|\bhigher(?:\s+than)?\b|\bor\s+higher\b|>=|\bhigh\b|\uc774\uc0c1)"
    )
    low_words = (
        r"(?:\bbelow\b|\bunder\b|\bless\s+than\b|\bat\s+most\b|\blower\s+than\b|"
        r"<=|\blow(?:er)?\b|\bor\s+lower\b|\bor\s+less\b|\uc774\ud558|\ubbf8\ub9cc)"
    )
    degree = r"\s*(?:\u00b0|\u00ba|\u02da|\uc9f8)?\s*"
    unit_pattern = r"(?P<unit>f|c|fahrenheit|celsius|degrees?|degree|\u2103|\u2109|\ub3c4)"

    range_unit_pattern = r"f|c|fahrenheit|celsius|degrees?|degree|\u2103|\u2109|\ub3c4"
    range_match = None
    for match in re.finditer(
        rf"(?P<lower>\d{{1,3}}(?:\.\d+)?){degree}(?P<lower_unit>{range_unit_pattern})?"
        rf"\s*(?:-|–|—|\bto\b)\s*"
        rf"(?P<upper>\d{{1,3}}(?:\.\d+)?){degree}(?P<upper_unit>{range_unit_pattern})?\b",
        q,
        re.IGNORECASE,
    ):
        if match.group("lower_unit") or match.group("upper_unit"):
            range_match = match
            break
    if range_match:
        lower_raw = float(range_match.group("lower"))
        upper_raw = float(range_match.group("upper"))
        if lower_raw >= upper_raw:
            return _TemperatureThreshold(None, "UNKNOWN", None, "threshold")
        lower_unit = range_match.group("lower_unit")
        upper_unit = range_match.group("upper_unit")
        lower_unit_label = _unit_label(lower_unit) if lower_unit else None
        upper_unit_label = _unit_label(upper_unit) if upper_unit else None
        if lower_unit_label and upper_unit_label and lower_unit_label != upper_unit_label:
            return _TemperatureThreshold(None, "UNKNOWN", None, "threshold")
        unit_text = (upper_unit or lower_unit or "").lower()
        unit_label = _unit_label(unit_text)
        lower_f = _to_f(lower_raw, unit_label)
        upper_f = _to_f(upper_raw, unit_label)
        return _TemperatureThreshold(
            threshold_f=lower_f,
            threshold_unit=unit_label,
            operator="==",
            bucket="range",
            range_lower_f=lower_f,
            range_upper_f=upper_f,
            range_lower_original=lower_raw,
            range_upper_original=upper_raw,
            range_inclusive=True,
        )

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
            return _TemperatureThreshold(None, "UNKNOWN", None, "threshold")
        threshold_raw = float(comparison_match.group("value"))
        unit_text = "unknown"
        operator = "<=" if re.search(low_words, comparison_match.group("op"), re.IGNORECASE) else ">="
        bucket = "threshold"

    unit_label = _unit_label(unit_text)
    return _TemperatureThreshold(_to_f(threshold_raw, unit_label), unit_label, operator, bucket)


def _has_temperature_context(q: str, threshold_unit: str) -> bool:
    if threshold_unit in {"F", "C"}:
        return True
    return TEMPERATURE_CONTEXT_RE.search(q) is not None


def temperature_bucket_interval_bounds_f(parsed: ParsedWeatherQuestion) -> TemperatureBucketInterval | None:
    """Return comparison bounds for a parsed temperature bucket.

    Exact buckets preserve their displayed value exactly. Range buckets preserve
    their displayed endpoints exactly. Tail buckets use the displayed threshold
    directly.
    """
    if parsed.variable != "temperature" or parsed.threshold_f is None:
        return None

    if parsed.temperature_bucket == "exact":
        return TemperatureBucketInterval(
            parsed.threshold_f,
            parsed.threshold_f,
            True,
            True,
            original_unit=parsed.threshold_unit,
        )
    if parsed.temperature_bucket == "range":
        if parsed.temperature_range_lower_f is None or parsed.temperature_range_upper_f is None:
            return None
        return TemperatureBucketInterval(
            parsed.temperature_range_lower_f,
            parsed.temperature_range_upper_f,
            True,
            True,
            original_unit=parsed.threshold_unit,
        )
    if parsed.temperature_bucket == "lower_tail":
        return TemperatureBucketInterval(
            float("-inf"),
            parsed.threshold_f,
            False,
            True,
            original_unit=parsed.threshold_unit,
        )
    if parsed.temperature_bucket == "upper_tail":
        return TemperatureBucketInterval(
            parsed.threshold_f,
            float("inf"),
            True,
            False,
            original_unit=parsed.threshold_unit,
        )
    if parsed.operator == "<=":
        return TemperatureBucketInterval(
            float("-inf"),
            parsed.threshold_f,
            False,
            True,
            original_unit=parsed.threshold_unit,
        )
    if parsed.operator == ">=":
        return TemperatureBucketInterval(
            parsed.threshold_f,
            float("inf"),
            True,
            False,
            original_unit=parsed.threshold_unit,
        )
    return None


def rounded_temperature_bucket_interval_f(parsed: ParsedWeatherQuestion) -> tuple[float, float] | None:
    bounds = temperature_bucket_interval_bounds_f(parsed)
    return bounds.as_tuple() if bounds is not None else None


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
    temperature_range_lower_f = None
    temperature_range_upper_f = None
    temperature_range_lower_original = None
    temperature_range_upper_original = None
    temperature_range_inclusive = False
    is_non_temperature_weather = NON_TEMPERATURE_WEATHER_RE.search(q) is not None
    if not is_non_temperature_weather:
        parsed_threshold = _extract_temp_threshold(q)
        if (
            parsed_threshold.threshold_f is not None
            and parsed_threshold.operator is not None
            and _has_temperature_context(q, parsed_threshold.threshold_unit)
        ):
            threshold_f = parsed_threshold.threshold_f
            threshold_unit = parsed_threshold.threshold_unit
            operator = parsed_threshold.operator
            temperature_bucket = parsed_threshold.bucket
            temperature_range_lower_f = parsed_threshold.range_lower_f
            temperature_range_upper_f = parsed_threshold.range_upper_f
            temperature_range_lower_original = parsed_threshold.range_lower_original
            temperature_range_upper_original = parsed_threshold.range_upper_original
            temperature_range_inclusive = parsed_threshold.range_inclusive
        elif parsed_threshold.threshold_f is not None and parsed_threshold.operator is not None:
            variable = "unsupported"
    else:
        variable = "unsupported"
    if variable == "temperature" and threshold_f is not None:
        if temperature_bucket == "range":
            threshold_original = temperature_range_lower_original
        else:
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
    if is_non_temperature_weather:
        notes.append("non-temperature weather market unsupported by temperature-only strategy")
    elif variable == "unsupported":
        notes.append("temperature condition not parsed")
    elif variable == "temperature" and threshold_f is not None and operator is not None:
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
        temperature_range_lower_f=temperature_range_lower_f,
        temperature_range_upper_f=temperature_range_upper_f,
        temperature_range_lower_original=temperature_range_lower_original,
        temperature_range_upper_original=temperature_range_upper_original,
        temperature_range_inclusive=temperature_range_inclusive,
    )
