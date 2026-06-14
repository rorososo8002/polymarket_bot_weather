from __future__ import annotations

import re
from typing import Any

from .event_dates import event_date_window_from_hint
from .models import MarketRuleProvenance, ParsedWeatherQuestion, RawMarket
from .stations import TRADING_READY_STATION_MAP
from .weather_client import parse_weather_question


DESCRIPTION_KEYS = (
    "description",
    "marketDescription",
    "market_description",
    "longDescription",
    "long_description",
)
RESOLUTION_RULE_KEYS = (
    "resolutionRules",
    "resolution_rules",
    "rules",
    "resolutionCriteria",
    "resolution_criteria",
    "resolutionDetails",
    "resolution_details",
)
RESOLUTION_SOURCE_KEYS = (
    "resolutionSource",
    "resolution_source",
    "resolutionSourceUrl",
    "resolution_source_url",
    "resolutionUrl",
    "resolution_url",
    "settlementSource",
    "settlement_source",
    "source",
)
EVENT_DESCRIPTION_KEYS = ("description", "longDescription", "long_description")
EVENT_RESOLUTION_RULE_KEYS = ("resolutionRules", "resolution_rules", "rules")
EVENT_SOURCE_KEYS = ("resolutionSource", "resolution_source", "resolutionUrl", "resolution_url")


def build_market_rule_provenance(
    *,
    market_id: str,
    question: str,
    slug: str | None,
    event_slug: str | None,
    raw: dict[str, Any] | None,
    event: dict[str, Any] | None = None,
) -> MarketRuleProvenance:
    raw = raw or {}
    event = event or {}
    title_parsed = parse_weather_question(question)
    description = _first_text(raw, DESCRIPTION_KEYS) or _first_text(event, EVENT_DESCRIPTION_KEYS)
    resolution_rules_text = _first_text(raw, RESOLUTION_RULE_KEYS) or _first_text(event, EVENT_RESOLUTION_RULE_KEYS)
    resolution_source = _first_text(raw, RESOLUTION_SOURCE_KEYS) or _first_text(event, EVENT_SOURCE_KEYS)
    rule_text = _combined_text(description, resolution_rules_text)
    rule_parsed = parse_weather_question(rule_text) if rule_text else None
    rule_unit = _explicit_unit(rule_text)
    if rule_unit is None and rule_parsed is not None and rule_parsed.threshold_unit != "UNKNOWN":
        rule_unit = rule_parsed.threshold_unit
    rule_station_id = _station_id_from_text(rule_text)
    title_station = TRADING_READY_STATION_MAP.get((title_parsed.city or "").lower())
    title_station_id = title_station.station_id if title_station is not None else None
    date_hint = title_parsed.date_hint or (rule_parsed.date_hint if rule_parsed is not None else None)
    date_window = event_date_window_from_hint(
        date_hint,
        title_station.timezone if title_station is not None else "auto",
        source_texts=(question, slug or "", event_slug or "", rule_text),
    )
    condition_type = _condition_type(title_parsed)
    unit = title_parsed.threshold_unit if title_parsed.threshold_unit in {"F", "C"} else (rule_unit or "UNKNOWN")
    threshold_value = title_parsed.threshold_original if condition_type in {"upper_threshold", "lower_threshold"} else None
    return MarketRuleProvenance(
        market_id=market_id,
        question=question,
        slug=slug,
        event_slug=event_slug,
        description=description,
        resolution_source=resolution_source,
        resolution_rules_text=resolution_rules_text,
        city=title_parsed.city,
        event_date_local=date_window.event_date_local.isoformat() if date_window is not None else date_hint,
        event_timezone=date_window.event_timezone if date_window is not None else (title_station.timezone if title_station is not None else None),
        event_start_utc=date_window.event_start_utc.isoformat() if date_window is not None else None,
        event_end_utc=date_window.event_end_utc.isoformat() if date_window is not None else None,
        station_id=rule_station_id or title_station_id,
        unit=unit,  # type: ignore[arg-type]
        condition_type=condition_type,
        exact_value=title_parsed.threshold_original if condition_type == "exact" else None,
        range_low=title_parsed.temperature_range_lower_original if condition_type == "range" else None,
        range_high=title_parsed.temperature_range_upper_original if condition_type == "range" else None,
        threshold_value=threshold_value,
        mismatch_reason=_mismatch_reason(
            title=title_parsed,
            title_text=question,
            rule=rule_parsed,
            rule_text=rule_text,
            rule_unit=rule_unit,
            rule_station_id=rule_station_id,
            title_station_id=title_station_id,
        ),
    )


def market_rule_mismatch_reason(market: RawMarket) -> str | None:
    provenance = market.rule_provenance
    if provenance is None and market.raw:
        provenance = build_market_rule_provenance(
            market_id=market.market_id,
            question=market.question,
            slug=market.slug,
            event_slug=market.event_slug,
            raw=market.raw,
        )
    if provenance is None or not provenance.mismatch_reason:
        return None
    return provenance.mismatch_reason


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " ".join(part for part in (_stringify(item) for item in value) if part)
    if isinstance(value, dict):
        return " ".join(part for part in (_stringify(item) for item in value.values()) if part)
    return str(value).strip()


def _first_text(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = _stringify(data.get(key))
        if text:
            return text
    return ""


def _combined_text(*parts: str) -> str:
    return "\n".join(part for part in parts if part.strip())


def _explicit_unit(text: str) -> str | None:
    if not text:
        return None
    if re.search(r"(\bfahrenheit\b|\bdegrees?\s*f\b|\bdeg\s*f\b|[\u00b0\u00ba\u02da]\s*f\b)", text, re.IGNORECASE):
        return "F"
    if re.search(r"(\bcelsius\b|\bcentigrade\b|\bdegrees?\s*c\b|\bdeg\s*c\b|[\u00b0\u00ba\u02da]\s*c\b|\u2103)", text, re.IGNORECASE):
        return "C"
    return None


def _station_id_from_text(text: str) -> str | None:
    lowered = text.lower()
    if not lowered:
        return None
    for station in TRADING_READY_STATION_MAP.values():
        candidates = {
            station.station_id,
            station.nowcast_station_id,
            station.station_name,
            station.polymarket_rule_station_text,
        }
        for candidate in candidates:
            normalized = str(candidate or "").strip().lower()
            if normalized and normalized in lowered:
                return station.station_id
    return None


def _metric_exposed(text: str) -> str | None:
    if re.search(r"\b(lowest|minimum|min(?:imum)?|overnight\s+low|low\s+temperature)\b", text, re.IGNORECASE):
        return "min"
    if re.search(r"\b(highest|maximum|max(?:imum)?|high\s+temperature)\b", text, re.IGNORECASE):
        return "max"
    return None


def _condition_type(parsed: ParsedWeatherQuestion) -> str | None:
    if parsed.variable != "temperature" or parsed.threshold_f is None or parsed.operator is None:
        return None
    if parsed.temperature_bucket == "exact":
        return "exact"
    if parsed.temperature_bucket == "range":
        return "range"
    if parsed.operator == ">=":
        return "upper_threshold"
    if parsed.operator == "<=":
        return "lower_threshold"
    return parsed.temperature_bucket


def _condition_values(parsed: ParsedWeatherQuestion) -> tuple[float | None, float | None, float | None]:
    condition_type = _condition_type(parsed)
    if condition_type == "exact":
        return parsed.threshold_original, None, None
    if condition_type == "range":
        return None, parsed.temperature_range_lower_original, parsed.temperature_range_upper_original
    if condition_type in {"upper_threshold", "lower_threshold"}:
        return parsed.threshold_original, None, None
    return None, None, None


def _same_number(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return abs(float(left) - float(right)) <= 1e-9


def _mismatch_reason(
    *,
    title: ParsedWeatherQuestion,
    title_text: str,
    rule: ParsedWeatherQuestion | None,
    rule_text: str,
    rule_unit: str | None,
    rule_station_id: str | None,
    title_station_id: str | None,
) -> str:
    if not rule_text:
        return ""
    title_city = (title.city or "").lower()
    if rule is not None and rule.city and title_city and rule.city.lower() != title_city:
        return f"city mismatch: title={title_city} rule={rule.city.lower()}"
    exposed_metric = _metric_exposed(rule_text)
    if exposed_metric and exposed_metric != title.temperature_metric:
        return f"temperature direction mismatch: title={title.temperature_metric} rule={exposed_metric}"
    if rule_unit and title.threshold_unit in {"F", "C"} and rule_unit != title.threshold_unit:
        return f"unit mismatch: title={title.threshold_unit} rule={rule_unit}"
    if rule_station_id and title_station_id and rule_station_id != title_station_id:
        return f"station mismatch: title={title_station_id} rule={rule_station_id}"
    if rule is not None and rule.threshold_f is not None and rule.operator is not None:
        title_condition = _condition_type(title)
        rule_condition = _condition_type(rule)
        if rule_condition and title_condition and rule_condition != title_condition:
            return f"bucket shape mismatch: title={title_condition} rule={rule_condition}"
        title_value, title_low, title_high = _condition_values(title)
        rule_value, rule_low, rule_high = _condition_values(rule)
        if not _same_number(title_value, rule_value):
            return f"threshold value mismatch: title={title_value} rule={rule_value}"
        if not _same_number(title_low, rule_low) or not _same_number(title_high, rule_high):
            return f"range value mismatch: title={title_low}-{title_high} rule={rule_low}-{rule_high}"
    if rule is not None and rule.date_hint and title.date_hint and rule.date_hint != title.date_hint:
        return f"date mismatch: title={title.date_hint} rule={rule.date_hint}"
    title_metric = _metric_exposed(title_text)
    rule_metric = _metric_exposed(rule_text)
    if title_metric and rule_metric and title_metric != rule_metric:
        return f"temperature direction mismatch: title={title_metric} rule={rule_metric}"
    return ""
