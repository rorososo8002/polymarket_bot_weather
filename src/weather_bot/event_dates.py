from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


_MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_NAME_RE = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?"
)
_MONTH_DAY_YEAR_RE = re.compile(
    rf"\b(?P<month>{_MONTH_NAME_RE})[.\s_-]+(?P<day>\d{{1,2}})"
    rf"(?:st|nd|rd|th)?(?:[,\s_-]+(?P<year>\d{{4}}))?\b",
    re.IGNORECASE,
)
_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True)
class EventDateWindow:
    event_date_local: date
    event_timezone: str
    event_start_utc: datetime
    event_end_utc: datetime


def event_date_window_from_hint(
    date_hint: str | None,
    timezone_name: str,
    *,
    now: datetime | None = None,
    source_texts: tuple[str, ...] = (),
) -> EventDateWindow | None:
    local_date = _event_date_from_hint(date_hint, timezone_name, now=now, source_texts=source_texts)
    if local_date is None:
        return None

    event_timezone = timezone_name if timezone_name and timezone_name != "auto" else "UTC"
    zone = _zone(event_timezone)
    local_start = datetime.combine(local_date, time.min, tzinfo=zone)
    local_end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=zone)
    return EventDateWindow(
        event_date_local=local_date,
        event_timezone=event_timezone,
        event_start_utc=local_start.astimezone(timezone.utc),
        event_end_utc=local_end.astimezone(timezone.utc),
    )


def _event_date_from_hint(
    date_hint: str | None,
    timezone_name: str,
    *,
    now: datetime | None,
    source_texts: tuple[str, ...],
) -> date | None:
    hint = (date_hint or "").lower().strip()
    local_today = _local_today(timezone_name, now)
    if hint in {"today", "\uc624\ub298"}:
        return local_today
    if hint in {"tomorrow", "\ub0b4\uc77c"}:
        return local_today + timedelta(days=1)
    if hint in _WEEKDAY_INDEX:
        days_ahead = (_WEEKDAY_INDEX[hint] - local_today.weekday()) % 7
        return local_today + timedelta(days=days_ahead)

    month_day = _month_day_from_text(hint)
    if month_day is not None:
        month, day = month_day
        explicit = _explicit_date_for_month_day(month, day, source_texts)
        if explicit is not None:
            return explicit
        try:
            candidate = date(local_today.year, month, day)
        except ValueError:
            return None
        if candidate < local_today - timedelta(days=3):
            try:
                candidate = date(local_today.year + 1, month, day)
            except ValueError:
                return None
        return candidate

    return _first_explicit_date(source_texts)


def _zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name if timezone_name and timezone_name != "auto" else "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _local_today(timezone_name: str, now: datetime | None) -> date:
    zone = _zone(timezone_name)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(zone).date()


def _month_number(value: str) -> int | None:
    return _MONTH_NAMES.get(value[:3].lower())


def _month_day_from_text(text: str) -> tuple[int, int] | None:
    match = _MONTH_DAY_YEAR_RE.search(text or "")
    if not match:
        return None
    month = _month_number(match.group("month"))
    if month is None:
        return None
    return month, int(match.group("day"))


def _explicit_date_for_month_day(month: int, day: int, source_texts: tuple[str, ...]) -> date | None:
    for text in source_texts:
        for match in _MONTH_DAY_YEAR_RE.finditer(text or ""):
            year = match.group("year")
            if year is None:
                continue
            parsed_month = _month_number(match.group("month"))
            parsed_day = int(match.group("day"))
            if parsed_month == month and parsed_day == day:
                try:
                    return date(int(year), parsed_month, parsed_day)
                except ValueError:
                    return None
    return None


def _first_explicit_date(source_texts: tuple[str, ...]) -> date | None:
    for text in source_texts:
        for match in _MONTH_DAY_YEAR_RE.finditer(text or ""):
            year = match.group("year")
            if year is None:
                continue
            month = _month_number(match.group("month"))
            if month is None:
                continue
            try:
                return date(int(year), month, int(match.group("day")))
            except ValueError:
                return None
    return None
