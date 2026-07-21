from __future__ import annotations

import csv
import gzip
import io
import math
import re
from typing import Any


AWC_CURRENT_CACHE_MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
AWC_CURRENT_CACHE_MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024


def _number(value: str) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_awc_current_metars(
    compressed_csv: bytes,
    *,
    station_ids: set[str],
) -> list[dict[str, Any]]:
    """Read the official one-minute AWC cache and keep mapped stations only."""

    wanted = {str(station_id).strip().upper() for station_id in station_ids}
    if len(compressed_csv) > AWC_CURRENT_CACHE_MAX_COMPRESSED_BYTES:
        return []
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed_csv)) as archive:
            decompressed = archive.read(AWC_CURRENT_CACHE_MAX_DECOMPRESSED_BYTES + 1)
        if len(decompressed) > AWC_CURRENT_CACHE_MAX_DECOMPRESSED_BYTES:
            return []
        decoded = decompressed.decode("utf-8")
        records = csv.DictReader(io.StringIO(decoded))
    except (EOFError, OSError, UnicodeDecodeError, csv.Error):
        return []

    parsed: list[dict[str, Any]] = []
    try:
        for record in records:
            station_id = str(record.get("station_id") or "").strip().upper()
            if station_id not in wanted:
                continue
            raw = str(record.get("raw_text") or "").strip()
            raw_match = re.search(r"\b(?:METAR|SPECI)\s+(?:COR\s+)?([A-Z]{4})\b", raw.upper())
            observed_at = str(record.get("observation_time") or "").strip()
            temp_c = _number(str(record.get("temp_c") or ""))
            dewp_c = _number(str(record.get("dewpoint_c") or ""))
            if (
                raw_match is None
                or raw_match.group(1) != station_id
                or not observed_at
                or temp_c is None
                or not -100.0 <= temp_c <= 70.0
            ):
                continue
            parsed.append(
                {
                    "icaoId": station_id,
                    "obsTime": observed_at,
                    "temp": temp_c,
                    "dewp": dewp_c,
                    "rawOb": raw,
                    "wxString": str(record.get("wx_string") or ""),
                }
            )
    except csv.Error:
        return []
    return parsed
