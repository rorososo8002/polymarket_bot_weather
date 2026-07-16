from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import statistics
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from .residual_probability import ResidualProfileStore
from .stations import StationMeta, TRADING_READY_STATION_MAP


SCHEMA_VERSION = 1
PROFILE_SOURCE = "NCEI Global Hourly official same-station residual builder"
VALID_TMP_QUALITY_CODES = frozenset({"1", "5"})
MISSING_TMP_TENTHS = 9999
MIN_PLAUSIBLE_TEMPERATURE_C = -90.0
MAX_PLAUSIBLE_TEMPERATURE_C = 60.0


@dataclass(frozen=True)
class HistoricalTemperatureObservation:
    station_id: str
    observed_at: datetime
    temperature_c: float


@dataclass(frozen=True)
class NceiStationMapping:
    icao: str
    ncei_station_id: str
    usaf: str
    wban: str
    station_name: str


@dataclass(frozen=True)
class _DayTrajectory:
    station_id: str
    month: int
    season: str
    high_residuals: Mapping[int, float]
    low_residuals: Mapping[int, float]
    first_final_high_local_minute: int
    first_final_low_local_minute: int


@dataclass
class _BinSummary:
    high: float
    low: float


@dataclass
class _DailySummary:
    station_id: str
    month: int
    season: str
    bins: dict[int, _BinSummary]
    final_high: float
    final_low: float
    first_final_high_local_minute: int
    first_final_low_local_minute: int


class ProfileBuildError(RuntimeError):
    """Raised when historical profile generation must fail closed."""


class _ResidualProfileAccumulator:
    def __init__(
        self,
        *,
        stations: Mapping[str, StationMeta],
        years: tuple[int, ...],
        interval_minutes: int,
        min_sample_days: int,
    ) -> None:
        if not years:
            raise ValueError("years must not be empty")
        if interval_minutes <= 0 or (24 * 60) % interval_minutes:
            raise ValueError("interval_minutes must divide a station-local day")
        if min_sample_days <= 0:
            raise ValueError("min_sample_days must be positive")

        self.generation_years = tuple(sorted(set(years)))
        self.interval_minutes = interval_minutes
        self.min_sample_days = min_sample_days
        self.station_by_id = {
            station.station_id.upper(): station
            for station in stations.values()
            if station.station_id
        }
        self._daily_summaries: dict[tuple[str, date], _DailySummary] = {}

    def add_observation(self, observation: HistoricalTemperatureObservation) -> None:
        station_id = observation.station_id.upper()
        station = self.station_by_id.get(station_id)
        if station is None or not math.isfinite(observation.temperature_c):
            return

        try:
            local_observed_at = _as_utc(observation.observed_at).astimezone(
                ZoneInfo(station.timezone)
            )
        except ZoneInfoNotFoundError:
            return

        if local_observed_at.year not in self.generation_years:
            return
        temperature = _temperature_for_station(observation.temperature_c, station)
        if temperature is None:
            return

        key = (station_id, local_observed_at.date())
        local_minute = _minute_of_day(local_observed_at)
        bin_minute = _bin_start_minute(local_observed_at, self.interval_minutes)
        summary = self._daily_summaries.get(key)
        if summary is None:
            summary = _DailySummary(
                station_id=station_id,
                month=local_observed_at.month,
                season=_season_for_month(local_observed_at.month),
                bins={},
                final_high=temperature,
                final_low=temperature,
                first_final_high_local_minute=local_minute,
                first_final_low_local_minute=local_minute,
            )
            self._daily_summaries[key] = summary
        _add_temperature_to_daily_summary(summary, bin_minute, local_minute, temperature)

    def to_payload(
        self,
        *,
        incomplete_station_years: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        residual_counts: dict[tuple[str, str, int, str, str], Counter[float]] = defaultdict(
            Counter
        )
        occurrence_times: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(
            lambda: {"high": [], "low": []}
        )

        for summary in sorted(
            self._daily_summaries.values(),
            key=lambda item: (item.station_id, item.month, min(item.bins)),
        ):
            station = self.station_by_id[summary.station_id]
            trajectory = _build_day_trajectory_from_summary(summary)
            if trajectory is None:
                continue

            unit = _unit_for_station(station)
            if unit is None:
                continue

            scopes = (
                f"month:{trajectory.month:02d}",
                f"season:{trajectory.season}",
            )
            for scope in scopes:
                occurrence_times[(summary.station_id, scope)]["high"].append(
                    trajectory.first_final_high_local_minute
                )
                occurrence_times[(summary.station_id, scope)]["low"].append(
                    trajectory.first_final_low_local_minute
                )
                for local_minute, residual in trajectory.high_residuals.items():
                    residual_counts[
                        (summary.station_id, scope, local_minute, "high", unit)
                    ][residual] += 1
                for local_minute, residual in trajectory.low_residuals.items():
                    residual_counts[
                        (summary.station_id, scope, local_minute, "low", unit)
                    ][residual] += 1

        profiles: dict[str, dict[str, object]] = {}
        for (station_id, scope, local_minute, direction, unit), counts in sorted(
            residual_counts.items()
        ):
            sample_days = sum(counts.values())
            if sample_days < self.min_sample_days:
                continue
            key = _profile_key(station_id, scope, local_minute, direction, unit)
            profiles[key] = {
                "station_id": station_id,
                "scope": scope,
                "local_minute": local_minute,
                "direction": direction,
                "unit": unit,
                "sample_days": sample_days,
                "histogram": _histogram_from_counter(counts),
                "metadata": _profile_metadata(
                    station=self.station_by_id[station_id],
                    scope=scope,
                    direction=direction,
                    occurrence=occurrence_times[(station_id, scope)],
                    interval_minutes=self.interval_minutes,
                ),
            }

        return {
            "schema_version": SCHEMA_VERSION,
            "source": PROFILE_SOURCE,
            "generation_years": list(self.generation_years),
            "profiles": profiles,
            "incomplete_station_years": incomplete_station_years or [],
        }


def load_station_history_catalog(path: str | Path) -> dict[str, NceiStationMapping]:
    return parse_station_history_catalog(Path(path).read_text(encoding="utf-8"))


def parse_station_history_catalog(text: str) -> dict[str, NceiStationMapping]:
    rows = csv.DictReader(io.StringIO(text))
    mappings: dict[str, NceiStationMapping] = {}

    for row in rows:
        icao = (row.get("ICAO") or "").strip().upper()
        usaf = (row.get("USAF") or "").strip()
        wban = (row.get("WBAN") or "").strip()
        if not icao or not usaf or not wban:
            continue

        ncei_station_id = f"{usaf.zfill(6)}{wban.zfill(5)}"
        mapping = NceiStationMapping(
            icao=icao,
            ncei_station_id=ncei_station_id,
            usaf=usaf.zfill(6),
            wban=wban.zfill(5),
            station_name=(row.get("STATION NAME") or "").strip(),
        )
        existing = mappings.get(icao)
        if existing is not None and existing.ncei_station_id == mapping.ncei_station_id:
            raise ProfileBuildError(
                f"duplicate NCEI station-history mapping for ICAO {icao}: "
                f"{mapping.ncei_station_id}"
            )
        if existing is not None:
            raise ProfileBuildError(
                f"conflicting NCEI station-history mapping for ICAO {icao}: "
                f"{existing.ncei_station_id} vs {mapping.ncei_station_id}"
            )
        mappings[icao] = mapping

    return mappings


def iter_global_hourly_observations(
    path: str | Path,
    catalog: Mapping[str, NceiStationMapping],
    *,
    station_ids: Iterable[str],
) -> Iterable[HistoricalTemperatureObservation]:
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        yield from _iter_global_hourly_observation_rows(
            csv.DictReader(file),
            catalog,
            station_ids=station_ids,
        )


def parse_global_hourly_observations(
    text: str,
    catalog: Mapping[str, NceiStationMapping],
    *,
    station_ids: Iterable[str],
) -> Iterable[HistoricalTemperatureObservation]:
    yield from _iter_global_hourly_observation_rows(
        csv.DictReader(io.StringIO(text)),
        catalog,
        station_ids=station_ids,
    )


def iter_ghcnh_hourly_observations(
    path: str | Path,
    *,
    station_id: str,
    ghcnh_station_id: str,
) -> Iterable[HistoricalTemperatureObservation]:
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        yield from _iter_ghcnh_hourly_observation_rows(
            csv.DictReader(file, delimiter="|"),
            station_id=station_id,
            ghcnh_station_id=ghcnh_station_id,
        )


def _iter_ghcnh_hourly_observation_rows(
    rows: Iterable[Mapping[str, str]],
    *,
    station_id: str,
    ghcnh_station_id: str,
) -> Iterable[HistoricalTemperatureObservation]:
    expected_ghcnh_id = ghcnh_station_id.strip().upper()
    target_station_id = station_id.strip().upper()
    for row in rows:
        if (row.get("STATION") or "").strip().upper() != expected_ghcnh_id:
            continue
        observed_at = _parse_global_hourly_date(row.get("DATE"))
        temperature_c = _parse_ghcnh_temperature_c(
            row.get("temperature"),
            row.get("temperature_Quality_Code"),
        )
        if observed_at is None or temperature_c is None:
            continue
        yield HistoricalTemperatureObservation(
            station_id=target_station_id,
            observed_at=observed_at,
            temperature_c=temperature_c,
        )


def _iter_global_hourly_observation_rows(
    rows: Iterable[Mapping[str, str]],
    catalog: Mapping[str, NceiStationMapping],
    *,
    station_ids: Iterable[str],
) -> Iterable[HistoricalTemperatureObservation]:
    wanted = {station_id.upper() for station_id in station_ids}
    ncei_to_icao = {
        mapping.ncei_station_id: icao
        for icao, mapping in catalog.items()
        if icao.upper() in wanted
    }

    for row in rows:
        ncei_station_id = (row.get("STATION") or "").strip()
        station_id = ncei_to_icao.get(ncei_station_id)
        if station_id is None:
            continue

        observed_at = _parse_global_hourly_date(row.get("DATE"))
        temperature_c = _parse_tmp_celsius(row.get("TMP"))
        if observed_at is None or temperature_c is None:
            continue

        yield HistoricalTemperatureObservation(
            station_id=station_id,
            observed_at=observed_at,
            temperature_c=temperature_c,
        )


def build_residual_profiles(
    observations: Iterable[HistoricalTemperatureObservation],
    stations: Mapping[str, StationMeta],
    *,
    years: tuple[int, ...],
    interval_minutes: int = 30,
    min_sample_days: int = 60,
) -> dict[str, object]:
    accumulator = _ResidualProfileAccumulator(
        stations=stations,
        years=years,
        interval_minutes=interval_minutes,
        min_sample_days=min_sample_days,
    )
    for observation in observations:
        accumulator.add_observation(observation)
    return accumulator.to_payload()


def publish_residual_profiles(
    *,
    output_path: str | Path,
    stations: Mapping[str, StationMeta],
    years: tuple[int, ...],
    catalog_path: str | Path | None = None,
    hourly_paths: Mapping[tuple[str, int], str | Path] | None = None,
    ghcnh_paths: Mapping[tuple[str, int], tuple[str, str | Path]] | None = None,
    catalog_url: str | None = None,
    hourly_url_template: str | None = None,
    http_get: Callable[..., Any] = requests.get,
    interval_minutes: int = 30,
    min_sample_days: int = 60,
) -> dict[str, object]:
    if catalog_path is not None:
        catalog = load_station_history_catalog(catalog_path)
    elif catalog_url:
        catalog = parse_station_history_catalog(_download_text(catalog_url, http_get=http_get))
    else:
        raise ProfileBuildError("station-history catalog path or URL is required")

    accumulator = _ResidualProfileAccumulator(
        stations=stations,
        years=years,
        interval_minutes=interval_minutes,
        min_sample_days=min_sample_days,
    )
    incomplete: list[dict[str, object]] = []
    hourly_paths = hourly_paths or {}
    ghcnh_paths = ghcnh_paths or {}

    for station in sorted(stations.values(), key=lambda item: item.station_id):
        station_id = station.station_id.upper()
        mapping = catalog.get(station_id)
        if mapping is None:
            incomplete.append(
                {
                    "station_id": station_id,
                    "year": None,
                    "reason": "missing_station_catalog_mapping",
                }
            )
            continue
        for year in years:
            ghcnh_input = ghcnh_paths.get((station_id, year))
            if ghcnh_input is not None:
                ghcnh_station_id, ghcnh_path = ghcnh_input
                added = _add_observations_to_accumulator(
                    iter_ghcnh_hourly_observations(
                        ghcnh_path,
                        station_id=station_id,
                        ghcnh_station_id=ghcnh_station_id,
                    ),
                    accumulator,
                )
                if added == 0:
                    raise ProfileBuildError(
                        f"no valid GHCNh observations for {station_id} {year} "
                        f"from {ghcnh_path}"
                    )
                continue
            hourly_path = hourly_paths.get((station_id, year))
            if hourly_path is not None:
                _add_observations_to_accumulator(
                    iter_global_hourly_observations(
                        hourly_path,
                        catalog,
                        station_ids={station_id},
                    ),
                    accumulator,
                )
                continue

            if hourly_url_template:
                url = hourly_url_template.format(
                    station_id=station_id,
                    ncei_station_id=mapping.ncei_station_id,
                    year=year,
                )
                _add_observations_to_accumulator(
                    _iter_downloaded_global_hourly_observations(
                        url,
                        catalog,
                        station_ids={station_id},
                        http_get=http_get,
                    ),
                    accumulator,
                )
                continue

            incomplete.append(
                {"station_id": station_id, "year": year, "reason": "missing_hourly_file"}
            )

    payload = accumulator.to_payload(incomplete_station_years=incomplete)
    _write_profiles_atomically(payload, Path(output_path))
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build compact station residual profiles from official NCEI Global Hourly CSVs."
    )
    parser.add_argument("--years", required=True, help="Comma-separated complete local years.")
    parser.add_argument("--catalog", required=True, help="Path to the ISD station-history CSV.")
    parser.add_argument("--output", required=True, help="Compact profile JSON output path.")
    parser.add_argument(
        "--hourly",
        action="append",
        default=[],
        metavar="STATION:YEAR:PATH",
        help="Local Global Hourly CSV path for one ICAO station and year.",
    )
    parser.add_argument(
        "--ghcnh",
        action="append",
        default=[],
        metavar="STATION:YEAR:GHCNH_ID:PATH",
        help="Local GHCNh PSV path and exact GHCNh station ID for one ICAO station and year.",
    )
    parser.add_argument(
        "--stations",
        choices=("trading-ready",),
        default="trading-ready",
        help="Station universe to profile.",
    )
    parser.add_argument("--interval-minutes", type=int, default=30)
    parser.add_argument("--min-sample-days", type=int, default=60)
    args = parser.parse_args(argv)

    try:
        years = tuple(int(part.strip()) for part in args.years.split(",") if part.strip())
        hourly_paths = _parse_hourly_path_args(args.hourly)
        ghcnh_paths = _parse_ghcnh_path_args(args.ghcnh)
        publish_residual_profiles(
            output_path=args.output,
            stations=TRADING_READY_STATION_MAP,
            years=years,
            catalog_path=args.catalog,
            hourly_paths=hourly_paths,
            ghcnh_paths=ghcnh_paths,
            interval_minutes=args.interval_minutes,
            min_sample_days=args.min_sample_days,
        )
    except (ProfileBuildError, ValueError) as exc:
        parser.exit(2, f"station-residual-profiles: {exc}\n")
    return 0


def _parse_hourly_path_args(values: Iterable[str]) -> dict[tuple[str, int], Path]:
    paths: dict[tuple[str, int], Path] = {}
    for value in values:
        station_id, year_text, path_text = value.split(":", 2)
        paths[(station_id.upper(), int(year_text))] = Path(path_text)
    return paths


def _parse_ghcnh_path_args(
    values: Iterable[str],
) -> dict[tuple[str, int], tuple[str, Path]]:
    paths: dict[tuple[str, int], tuple[str, Path]] = {}
    for value in values:
        station_id, year_text, ghcnh_station_id, path_text = value.split(":", 3)
        paths[(station_id.upper(), int(year_text))] = (
            ghcnh_station_id.upper(),
            Path(path_text),
        )
    return paths


def _download_text(url: str, *, http_get: Callable[..., Any]) -> str:
    try:
        response = http_get(url, timeout=30.0)
    except Exception as exc:  # pragma: no cover - exercised with fake request exceptions
        raise ProfileBuildError(f"failed to download {url}: {exc}") from exc

    status_code = getattr(response, "status_code", None)
    if status_code != 200:
        raise ProfileBuildError(f"failed to download {url}: HTTP {status_code}")
    text = getattr(response, "text", None)
    if not isinstance(text, str):
        raise ProfileBuildError(f"failed to download {url}: response body is not text")
    return text


def _iter_downloaded_global_hourly_observations(
    url: str,
    catalog: Mapping[str, NceiStationMapping],
    *,
    station_ids: Iterable[str],
    http_get: Callable[..., Any],
) -> Iterable[HistoricalTemperatureObservation]:
    yield from _iter_global_hourly_observation_rows(
        csv.DictReader(_iter_downloaded_text_lines(url, http_get=http_get)),
        catalog,
        station_ids=station_ids,
    )


def _iter_downloaded_text_lines(
    url: str,
    *,
    http_get: Callable[..., Any],
) -> Iterable[str]:
    try:
        response = http_get(url, timeout=30.0, stream=True)
    except Exception as exc:  # pragma: no cover - exercised with fake request exceptions
        raise ProfileBuildError(f"failed to download {url}: {exc}") from exc

    try:
        status_code = getattr(response, "status_code", None)
        if status_code != 200:
            raise ProfileBuildError(f"failed to download {url}: HTTP {status_code}")
        iter_lines = getattr(response, "iter_lines", None)
        if not callable(iter_lines):
            raise ProfileBuildError(f"failed to download {url}: response cannot stream lines")
        for line in iter_lines(decode_unicode=True):
            if line is None:
                continue
            if isinstance(line, bytes):
                yield line.decode("utf-8")
            elif isinstance(line, str):
                yield line
            else:
                raise ProfileBuildError(
                    f"failed to download {url}: streamed line is not text"
                )
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _write_profiles_atomically(payload: Mapping[str, object], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f"{output_path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    store = ResidualProfileStore.from_path(tmp_path)
    if store.load_reason_code:
        tmp_path.unlink(missing_ok=True)
        raise ProfileBuildError(
            f"refusing to publish invalid residual profile: {store.load_reason}"
        )
    os.replace(tmp_path, output_path)


def _add_observations_to_accumulator(
    observations: Iterable[HistoricalTemperatureObservation],
    accumulator: _ResidualProfileAccumulator,
) -> int:
    added = 0
    for observation in observations:
        accumulator.add_observation(observation)
        added += 1
    return added


def _add_temperature_to_daily_summary(
    summary: _DailySummary,
    bin_minute: int,
    local_minute: int,
    temperature: float,
) -> None:
    bin_summary = summary.bins.get(bin_minute)
    if bin_summary is None:
        summary.bins[bin_minute] = _BinSummary(high=temperature, low=temperature)
    else:
        bin_summary.high = max(bin_summary.high, temperature)
        bin_summary.low = min(bin_summary.low, temperature)

    if (
        temperature > summary.final_high
        or temperature == summary.final_high
        and local_minute < summary.first_final_high_local_minute
    ):
        summary.final_high = temperature
        summary.first_final_high_local_minute = local_minute
    if (
        temperature < summary.final_low
        or temperature == summary.final_low
        and local_minute < summary.first_final_low_local_minute
    ):
        summary.final_low = temperature
        summary.first_final_low_local_minute = local_minute


def _build_day_trajectory_from_summary(summary: _DailySummary) -> _DayTrajectory | None:
    if not summary.bins:
        return None

    running_high = -math.inf
    running_low = math.inf
    high_residuals: dict[int, float] = {}
    low_residuals: dict[int, float] = {}
    for local_minute, bin_summary in sorted(summary.bins.items()):
        running_high = max(running_high, bin_summary.high)
        running_low = min(running_low, bin_summary.low)
        high_residuals[local_minute] = _round_residual(summary.final_high - running_high)
        low_residuals[local_minute] = _round_residual(running_low - summary.final_low)

    return _DayTrajectory(
        station_id=summary.station_id,
        month=summary.month,
        season=summary.season,
        high_residuals=high_residuals,
        low_residuals=low_residuals,
        first_final_high_local_minute=summary.first_final_high_local_minute,
        first_final_low_local_minute=summary.first_final_low_local_minute,
    )


def _profile_metadata(
    *,
    station: StationMeta,
    scope: str,
    direction: str,
    occurrence: Mapping[str, list[int]],
    interval_minutes: int,
) -> dict[str, object]:
    high_stats = _minute_stats(occurrence["high"])
    low_stats = _minute_stats(occurrence["low"])
    if direction == "high":
        monitoring_start = min(14 * 60 + 30, int(high_stats["median"] - 30))
    else:
        monitoring_start = int(low_stats["median"] - 2 * 60)
    return {
        "station_timezone": station.timezone,
        "scope": scope,
        "occurrence_sample_days": len(occurrence[direction]),
        "monitoring_start_local_minute": max(0, monitoring_start),
        "first_final_high_local_minute": high_stats,
        "first_final_low_local_minute": low_stats,
    }


def _minute_stats(values: list[int]) -> dict[str, float]:
    sorted_values = sorted(values)
    return {
        "min": float(sorted_values[0]),
        "q25": _percentile(sorted_values, 0.25),
        "median": float(statistics.median(sorted_values)),
        "q75": _percentile(sorted_values, 0.75),
        "max": float(sorted_values[-1]),
    }


def _percentile(sorted_values: list[int], percentile: float) -> float:
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    lower_weight = upper - position
    upper_weight = position - lower
    return sorted_values[lower] * lower_weight + sorted_values[upper] * upper_weight


def _histogram_from_counter(counts: Counter[float]) -> list[list[float | int]]:
    return [[residual, counts[residual]] for residual in sorted(counts)]


def _parse_global_hourly_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _parse_tmp_celsius(value: str | None) -> float | None:
    if not value or "," not in value:
        return None
    parts = value.split(",")
    if len(parts) != 2:
        return None
    tenths_text, quality_code = parts
    quality_code = quality_code.strip()
    if quality_code not in VALID_TMP_QUALITY_CODES:
        return None
    try:
        tenths = int(tenths_text.strip())
    except ValueError:
        return None
    if abs(tenths) == MISSING_TMP_TENTHS:
        return None
    temperature_c = tenths / 10.0
    if not MIN_PLAUSIBLE_TEMPERATURE_C <= temperature_c <= MAX_PLAUSIBLE_TEMPERATURE_C:
        return None
    return temperature_c


def _parse_ghcnh_temperature_c(
    value: str | None,
    quality_code: str | None,
) -> float | None:
    if (quality_code or "").strip() not in VALID_TMP_QUALITY_CODES:
        return None
    try:
        temperature_c = float((value or "").strip())
    except ValueError:
        return None
    if not math.isfinite(temperature_c):
        return None
    if not MIN_PLAUSIBLE_TEMPERATURE_C <= temperature_c <= MAX_PLAUSIBLE_TEMPERATURE_C:
        return None
    return temperature_c


def _temperature_for_station(temperature_c: float, station: StationMeta) -> float | None:
    if station.temperature_unit == "celsius":
        return temperature_c
    if station.temperature_unit == "fahrenheit":
        return temperature_c * 9.0 / 5.0 + 32.0
    return None


def _unit_for_station(station: StationMeta) -> str | None:
    if station.temperature_unit == "celsius":
        return "C"
    if station.temperature_unit == "fahrenheit":
        return "F"
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bin_start_minute(value: datetime, interval_minutes: int) -> int:
    minute = _minute_of_day(value)
    return minute - (minute % interval_minutes)


def _minute_of_day(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _round_residual(value: float) -> float:
    return round(max(0.0, value), 6)


def _profile_key(
    station_id: str,
    scope: str,
    local_minute: int,
    direction: str,
    unit: str,
) -> str:
    return f"{station_id}|{scope}|{local_minute:04d}|{direction}|{unit}"


def _season_for_month(month: int) -> str:
    if month in {12, 1, 2}:
        return "DJF"
    if month in {3, 4, 5}:
        return "MAM"
    if month in {6, 7, 8}:
        return "JJA"
    return "SON"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
