from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any


KMA_PUBLIC_METAR_HEADER_SCHEMA = (
    "공항명",
    "utc",
    "kst",
    "종류",
    "관측시각(utc)",
    "풍향(˚)",
    "풍속/g(kt)",
    "시정(m)",
    "일기현상",
    "구름(운량okta/운고100ft)",
    "기온(˚c)",
    "기압(hpa)",
    "강수량(㎜)",
    "신적설(㎝)",
)


class _KmaTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_tag = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []
            self._cell_tag = tag.lower()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if (
            lowered in {"td", "th"}
            and lowered == self._cell_tag
            and self._row is not None
            and self._cell is not None
        ):
            value = " ".join("".join(self._cell).split())
            if lowered == "th":
                self.headers.append(value)
            else:
                self._row.append(value)
            self._cell = None
            self._cell_tag = ""
        elif lowered == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def _report_time(value: str, reference: datetime) -> datetime | None:
    match = re.fullmatch(r"(\d{2})(\d{2})(\d{2})Z", value.strip())
    if match is None:
        return None
    day, hour, minute = (int(part) for part in match.groups())
    current = reference.astimezone(timezone.utc)
    candidates: list[datetime] = []
    for month_offset in (-1, 0, 1):
        month_index = current.year * 12 + current.month - 1 + month_offset
        year, zero_based_month = divmod(month_index, 12)
        try:
            candidates.append(
                datetime(
                    year,
                    zero_based_month + 1,
                    day,
                    hour,
                    minute,
                    tzinfo=timezone.utc,
                )
            )
        except ValueError:
            continue
    plausible = [candidate for candidate in candidates if candidate <= current + timedelta(minutes=5)]
    if not plausible:
        return None
    return min(plausible, key=lambda candidate: abs((current - candidate).total_seconds()))


def parse_kma_public_metar_html(
    html: str,
    *,
    station_id: str,
    reference: datetime,
) -> list[dict[str, Any]]:
    """Convert the official KMA public METAR table into provider rows."""

    parser = _KmaTableParser()
    parser.feed(str(html or ""))
    normalized_headers = tuple(
        "".join(header.split()).casefold() for header in parser.headers
    )
    if normalized_headers != KMA_PUBLIC_METAR_HEADER_SCHEMA:
        return []
    expected_station_id = station_id.strip().upper()
    parsed: list[dict[str, Any]] = []
    for cells in parser.rows:
        station_match = re.search(r"\(([A-Z]{4})\)", cells[0].upper()) if cells else None
        if station_match is None or station_match.group(1) != expected_station_id:
            continue
        # The official page itself labels special reports "METARSCIAL";
        # accept that exact site value but reject every unknown schema value.
        if len(cells) < 16 or cells[3].strip().upper() not in {
            "METAR",
            "METARSCIAL",
            "SPECI",
        }:
            return []
        utc_clock = re.fullmatch(r"(\d{2}):(\d{2})", cells[1].strip())
        local_clock = re.fullmatch(r"(\d{2}):(\d{2})", cells[2].strip())
        observed_at = _report_time(cells[4], reference)
        try:
            temp_c = float(cells[14])
        except (TypeError, ValueError):
            return []
        if (
            observed_at is None
            or utc_clock is None
            or local_clock is None
            or (int(utc_clock.group(1)), int(utc_clock.group(2)))
            != (observed_at.hour, observed_at.minute)
            or (int(local_clock.group(1)), int(local_clock.group(2)))
            != ((observed_at.hour + 9) % 24, observed_at.minute)
            or not math.isfinite(temp_c)
            or not -100.0 <= temp_c <= 70.0
        ):
            return []
        parsed.append(
            {
                "icaoId": expected_station_id,
                "rawOb": (
                    f"KMA-OFFICIAL-WEB {expected_station_id} {cells[4]} "
                    f"TEMP={temp_c:g}C"
                ),
                "reportTime": observed_at.isoformat(),
                "temp": temp_c,
                "wxString": cells[8],
            }
        )
    return sorted(parsed, key=lambda row: row["reportTime"])
