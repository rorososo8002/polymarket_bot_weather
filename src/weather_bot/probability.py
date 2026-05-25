from __future__ import annotations

import json
import math
import os
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .config import Settings
from .models import ParsedWeatherQuestion, WeatherSignal
from .stations import STATION_MAP, StationMeta
from .weather_client import OpenMeteoClient, parse_weather_question


# ---------------------------------------------------------------------------
# 1) Station mapping
# ---------------------------------------------------------------------------
# Do not trade a weather market until the exact Polymarket settlement source is
# in `stations.py`.  The active registry intentionally contains only verified
# cities from the current weather resolution rules.


# Default model set.  If Open-Meteo changes a model id, override with:
# OPEN_METEO_ENSEMBLE_MODELS=gfs_seamless,ecmwf_ifs025,gem_global
DEFAULT_ENSEMBLE_MODELS = "gfs_seamless,ecmwf_ifs025"


# ---------------------------------------------------------------------------
# 2) Math helpers
# ---------------------------------------------------------------------------

def clamp_probability(x: float) -> float:
    return max(0.0, min(1.0, x))


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def probability_temperature_ge(threshold_f: float, forecast_max_f: float, sigma_f: float = 4.5) -> float:
    z = (threshold_f - forecast_max_f) / sigma_f
    return 1.0 - normal_cdf(z)


def _mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else float("nan")


def _stdev(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) >= 2 else 0.0


def _today_for_timezone(timezone_name: str = "auto", now: datetime | None = None) -> date:
    if timezone_name and timezone_name != "auto":
        try:
            zone = ZoneInfo(timezone_name)
            current = now or datetime.now(zone)
            if current.tzinfo is None:
                current = current.replace(tzinfo=zone)
            else:
                current = current.astimezone(zone)
            return current.date()
        except Exception:
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            offset = _fallback_offset_hours(timezone_name, current)
            if offset is not None:
                zone = timezone(timedelta(hours=offset))
                return current.astimezone(zone).date()
    current = now or datetime.now()
    return current.date()


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    days_ahead = (weekday - first.weekday()) % 7
    return first + timedelta(days=days_ahead + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    days_back = (last.weekday() - weekday) % 7
    return last - timedelta(days=days_back)


def _fallback_offset_hours(timezone_name: str, now: datetime) -> int | None:
    current = now.astimezone(timezone.utc)
    current_date = current.date()
    us_dst_zones = {
        "America/New_York": (-5, -4),
        "America/Chicago": (-6, -5),
        "America/Los_Angeles": (-8, -7),
        "America/Denver": (-7, -6),
        "America/Toronto": (-5, -4),
    }
    if timezone_name in us_dst_zones:
        standard, daylight = us_dst_zones[timezone_name]
        starts = _nth_weekday(current.year, 3, 6, 2)
        ends = _nth_weekday(current.year, 11, 6, 1)
        return daylight if starts <= current_date < ends else standard

    european_dst_zones = {
        "Europe/London": (0, 1),
        "Europe/Paris": (1, 2),
        "Europe/Amsterdam": (1, 2),
        "Europe/Berlin": (1, 2),
        "Europe/Helsinki": (2, 3),
        "Europe/Madrid": (1, 2),
        "Europe/Rome": (1, 2),
        "Europe/Warsaw": (1, 2),
    }
    if timezone_name in european_dst_zones:
        standard, daylight = european_dst_zones[timezone_name]
        starts = _last_weekday(current.year, 3, 6)
        ends = _last_weekday(current.year, 10, 6)
        return daylight if starts <= current_date < ends else standard

    fixed_offsets = {
        "Africa/Johannesburg": 2,
        "America/Argentina/Buenos_Aires": -3,
        "America/Panama": -5,
        "America/Phoenix": -7,
        "Asia/Hong_Kong": 8,
        "Asia/Jerusalem": 2,
        "Asia/Karachi": 5,
        "Asia/Manila": 8,
        "Asia/Riyadh": 3,
        "Asia/Seoul": 9,
        "Asia/Shanghai": 8,
        "Asia/Singapore": 8,
        "Asia/Taipei": 8,
        "Asia/Tokyo": 9,
        "Europe/Istanbul": 3,
        "Europe/Moscow": 3,
        "Pacific/Auckland": 12,
    }
    return fixed_offsets.get(timezone_name)


def _lead_time_days(target: date, timezone_name: str = "auto", now: datetime | None = None) -> float:
    return max(0.0, (target - _today_for_timezone(timezone_name, now)).days)


def dynamic_sigma_f(member_values_f: list[float], lead_days: float, floor_f: float = 1.25, cap_f: float = 8.0) -> float:
    """Blend ensemble spread and time-to-event error.

    The floor prevents fake certainty when all members cluster tightly.
    The lead component acknowledges that day-5 forecasts deserve more error
    budget than same-day forecasts.
    """
    spread = _stdev(member_values_f)
    lead_component = 0.85 + 0.45 * math.sqrt(max(0.0, lead_days))
    sigma = math.sqrt(spread * spread + lead_component * lead_component)
    return max(floor_f, min(cap_f, sigma))


def blend_empirical_and_cdf(empirical_p: float, mean_f: float, threshold_f: float, sigma_f: float, operator: str) -> float:
    """Blend raw ensemble vote with a smooth CDF around the threshold.

    Raw vote is robust when there are many members; CDF reduces jumpiness when
    the threshold is near the ensemble mean or when only a few members are returned.
    """
    if operator == ">=":
        cdf_p = probability_temperature_ge(threshold_f, mean_f, sigma_f)
    else:
        cdf_p = normal_cdf((threshold_f - mean_f) / sigma_f)
    return clamp_probability(0.70 * empirical_p + 0.30 * cdf_p)


# ---------------------------------------------------------------------------
# 3) Bias correction
# ---------------------------------------------------------------------------
# Bias is additive in Fahrenheit: corrected_forecast = raw_forecast - bias.
# Example: if GFS/ECMWF at RKSI usually over-forecasts high temp by +1.2F,
# set {"RKSI": {"temperature_2m_max": 1.2}}.

DEFAULT_BIAS_F: dict[str, dict[str, float]] = {
    # Keep defaults conservative/neutral until you have backtest evidence.
    "KLGA": {"temperature_2m_max": 0.0, "temperature_2m_min": 0.0},
    "RKSI": {"temperature_2m_max": 0.0, "temperature_2m_min": 0.0},
    "EGLC": {"temperature_2m_max": 0.0, "temperature_2m_min": 0.0},
}


def load_bias_table() -> dict[str, dict[str, float]]:
    table = {k: dict(v) for k, v in DEFAULT_BIAS_F.items()}
    path = os.getenv("WEATHER_BIAS_JSON", "").strip()
    if not path:
        return table
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        for station_id, variables in raw.items():
            table.setdefault(station_id, {}).update({k: float(v) for k, v in variables.items()})
    except Exception:
        # Never crash trading loop because a calibration file is missing/bad.
        pass
    return table


def bias_for(station: StationMeta, variable: str) -> float:
    return float(load_bias_table().get(station.station_id, {}).get(variable, 0.0))


# ---------------------------------------------------------------------------
# 4) Open-Meteo Ensemble client and parser
# ---------------------------------------------------------------------------

class OpenMeteoEnsembleClient:
    def __init__(
        self,
        timeout: float = 20.0,
        cache_path: str | Path | None = None,
        cache_ttl_seconds: int = 21600,
    ) -> None:
        self.timeout = timeout
        self.base_url = os.getenv("OPEN_METEO_ENSEMBLE_BASE", "https://ensemble-api.open-meteo.com/v1/ensemble")
        self.models = os.getenv("OPEN_METEO_ENSEMBLE_MODELS", DEFAULT_ENSEMBLE_MODELS)
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.disabled_reason = ""
        self._cache: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenMeteoEnsembleClient":
        cache_path = settings.forecast_cache_path or str(Path(settings.state_path).with_name("forecast_cache.json"))
        return cls(cache_path=cache_path, cache_ttl_seconds=settings.forecast_cache_ttl_seconds)

    def _cache_key(self, latitude: float, longitude: float, timezone: str, forecast_days: int) -> str:
        return "|".join([
            f"{round(latitude, 4):.4f}",
            f"{round(longitude, 4):.4f}",
            timezone,
            str(int(forecast_days)),
            self.models,
        ])

    def _fresh_cached_data(self, cache_key: str) -> dict[str, Any] | None:
        if cache_key in self._cache:
            return self._cache[cache_key]
        if self.cache_ttl_seconds <= 0 or self.cache_path is None or not self.cache_path.exists():
            return None
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            entry = raw.get(cache_key) if isinstance(raw, dict) else None
            if not isinstance(entry, dict):
                return None
            created_at = datetime.fromisoformat(str(entry.get("created_at", "")).replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds()
            if age_seconds > self.cache_ttl_seconds:
                return None
            data = entry.get("data")
            if isinstance(data, dict):
                self._cache[cache_key] = data
                return data
        except Exception:
            return None
        return None

    def _store_cached_data(self, cache_key: str, data: dict[str, Any]) -> None:
        self._cache[cache_key] = data
        if self.cache_ttl_seconds <= 0 or self.cache_path is None:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            raw: dict[str, Any] = {}
            if self.cache_path.exists():
                try:
                    loaded = json.loads(self.cache_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        raw = loaded
                except Exception:
                    raw = {}
            raw[cache_key] = {
                "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "data": data,
            }
            self.cache_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception(lambda exc: not _is_rate_limited(exc)),
        reraise=True,
    )
    def forecast_daily_ensemble(
        self,
        latitude: float,
        longitude: float,
        timezone: str = "auto",
        forecast_days: int = 7,
    ) -> dict[str, Any]:
        cache_key = self._cache_key(latitude, longitude, timezone, forecast_days)
        cached = self._fresh_cached_data(cache_key)
        if cached is not None:
            return cached
        if self.disabled_reason:
            raise RuntimeError(f"ensemble disabled for cycle: {self.disabled_reason}")
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum",
            "models": self.models,
            "temperature_unit": "fahrenheit",
            "timezone": timezone,
            "forecast_days": forecast_days,
        }
        resp = requests.get(self.base_url, params=params, timeout=self.timeout)
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            if _is_rate_limited(exc):
                body = getattr(resp, "text", "") or str(exc)
                self.disabled_reason = f"Open-Meteo rate limited: {body[:160]}"
            raise
        data = resp.json()
        self._store_cached_data(cache_key, data)
        return data


def _is_rate_limited(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    return isinstance(exc, requests.HTTPError) and getattr(response, "status_code", None) == 429


WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _target_date_from_hint(
    parsed: ParsedWeatherQuestion,
    timezone_name: str = "auto",
    now: datetime | None = None,
) -> date:
    today = _today_for_timezone(timezone_name, now)
    hint = (parsed.date_hint or "").lower().strip()
    if hint in {"today", "오늘"}:
        return today
    if hint in {"tomorrow", "내일"}:
        return today + timedelta(days=1)
    if hint in WEEKDAY_INDEX:
        days_ahead = (WEEKDAY_INDEX[hint] - today.weekday()) % 7
        return today + timedelta(days=days_ahead)

    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", hint)
    if m:
        month_names = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        month = month_names.index(m.group(1)[:3]) + 1
        day = int(m.group(2))
        candidate = date(today.year, month, day)
        # Weather markets are near-term; if the date already passed, assume next year.
        if candidate < today - timedelta(days=3):
            candidate = date(today.year + 1, month, day)
        return candidate

    return today


def _date_index(daily: dict[str, Any], target: date) -> int:
    times = daily.get("time") or []
    target_s = target.isoformat()
    if target_s in times:
        return times.index(target_s)
    # fallback: nearest available date, not always index 0/last
    parsed_dates: list[date] = []
    for x in times:
        try:
            parsed_dates.append(date.fromisoformat(str(x)))
        except ValueError:
            pass
    if parsed_dates:
        return min(range(len(parsed_dates)), key=lambda i: abs((parsed_dates[i] - target).days))
    return 0


def _extract_member_values(daily: dict[str, Any], variable: str, idx: int, bias_f: float = 0.0) -> list[float]:
    values: list[float] = []
    for key, series in daily.items():
        if key == "time" or not isinstance(series, list):
            continue
        # Open-Meteo may return the base name and/or model/member-suffixed names.
        # Accept variable, variable_member01, variable_gfs_member02, etc.
        if key == variable or key.startswith(variable + "_") or variable in key:
            if idx < len(series) and series[idx] is not None:
                try:
                    values.append(float(series[idx]) - bias_f)
                except (TypeError, ValueError):
                    continue
    return values


def _station_for(parsed: ParsedWeatherQuestion) -> StationMeta | None:
    if parsed.city:
        return STATION_MAP.get(parsed.city.lower())
    return None


def _format_threshold(parsed: ParsedWeatherQuestion) -> str:
    if parsed.threshold_f is None:
        return "unknown"
    if parsed.threshold_unit == "C" and parsed.threshold_original is not None:
        return f"{parsed.threshold_original:.1f}C/{parsed.threshold_f:.1f}F"
    return f"{parsed.threshold_f:.1f}F"


def _temperature_daily_variable(parsed: ParsedWeatherQuestion) -> str:
    return "temperature_2m_min" if parsed.temperature_metric == "min" else "temperature_2m_max"


def _temperature_reference_label(parsed: ParsedWeatherQuestion) -> str:
    return "target forecast low" if parsed.temperature_metric == "min" else "target forecast high"


def _fallback_deterministic_probability(
    parsed: ParsedWeatherQuestion,
    settings: Settings,
    client: OpenMeteoClient | None,
    timezone_name: str = "auto",
) -> WeatherSignal:
    """Original simple model kept as a safe fallback when ensemble API fails."""
    if parsed.latitude is None or parsed.longitude is None:
        return WeatherSignal(0.5, 0.0, "fallback", "No coordinates for deterministic fallback.", parsed)

    client = client or OpenMeteoClient()
    target = _target_date_from_hint(parsed, timezone_name=timezone_name)
    forecast_days = max(3, min(16, int(_lead_time_days(target, timezone_name=timezone_name)) + 2))
    data = client.forecast_daily(parsed.latitude, parsed.longitude, forecast_days=forecast_days)
    daily = data.get("daily") or {}
    if parsed.variable == "temperature" and parsed.threshold_f is not None and parsed.operator is not None:
        variable = _temperature_daily_variable(parsed)
        series = daily.get(variable) or []
        idx = _date_index(daily, target)
        if idx >= len(series) or series[idx] is None:
            return WeatherSignal(0.5, 0.0, "fallback", "No deterministic temperature values returned.", parsed)
        reference = float(series[idx])
        if parsed.operator == ">=":
            p = probability_temperature_ge(parsed.threshold_f, reference, settings.default_temperature_sigma_f)
        else:
            p = normal_cdf((parsed.threshold_f - reference) / settings.default_temperature_sigma_f)
        ref_label = _temperature_reference_label(parsed)
        date_used = (daily.get("time") or [target.isoformat()])[
            min(idx, max(0, len(daily.get("time") or []) - 1))
        ]
        return WeatherSignal(
            p_true=clamp_probability(p),
            confidence=max(0.05, parsed.confidence * 0.60),
            source="open-meteo-deterministic-fallback",
            note=(
                f"{parsed.city}; target_date={date_used}; {_format_threshold(parsed)}; "
                f"{ref_label}={reference:.1f}F; fixed sigma={settings.default_temperature_sigma_f:.1f}F. "
                "Ensemble failed/unavailable."
            ),
            parsed=parsed,
        )
    if parsed.variable in {"precipitation", "snow"}:
        variable = "snowfall_sum" if parsed.variable == "snow" else "precipitation_sum"
        label = "snow" if parsed.variable == "snow" else "precip"
        unit = "cm" if parsed.variable == "snow" else "mm"
        threshold = parsed.threshold_precip_mm if parsed.threshold_precip_mm is not None else 0.1
        values = [float(x) for x in (daily.get(variable) or []) if x is not None]
        if not values:
            return WeatherSignal(0.5, 0.0, "fallback", f"No deterministic {label} values returned.", parsed)
        reference = max(values)
        p = 0.75 if reference >= threshold else 0.25
        return WeatherSignal(
            p_true=clamp_probability(p),
            confidence=max(0.05, parsed.confidence * 0.40),
            source="open-meteo-deterministic-fallback",
            note=f"{parsed.city}; {label}>={threshold:.1f}{unit}; max forecast={reference:.2f}{unit}. Ensemble failed/unavailable.",
            parsed=parsed,
        )
    return WeatherSignal(0.5, 0.0, "fallback", "Unsupported fallback market.", parsed)


# ---------------------------------------------------------------------------
# 5) 강수 앙상블 확률 모델
# ---------------------------------------------------------------------------

def _ensemble_precipitation_probability(
    parsed: ParsedWeatherQuestion,
    station: StationMeta,
    settings: Settings,
    ensemble_client: OpenMeteoEnsembleClient,
) -> WeatherSignal:
    """앙상블 기반 강수 확률 모델.

    전략:
    - Open-Meteo 앙상블 API의 precipitation_sum 멤버 데이터 사용.
    - 경험적 투표: threshold_mm 이상인 멤버 비율.
    - 강수는 정규분포가 아니라 CDF 블렌드 대신 80% 실험 투표 + 20% 기본율(0.5).
    - 신뢰도 상한을 기온 모델보다 낙게 설정 (예측 난이도 반영).
    """
    target = _target_date_from_hint(parsed, timezone_name=station.timezone)
    lead_days = _lead_time_days(target, timezone_name=station.timezone)
    # 파싱된 임계값이 없으면 0.1mm 기본값 (어떤 비든)
    is_snow = parsed.variable == "snow"
    threshold_amount = parsed.threshold_precip_mm if parsed.threshold_precip_mm is not None else 0.1
    variable = "snowfall_sum" if is_snow else "precipitation_sum"
    label = "snow" if is_snow else "precip"
    unit = "cm" if is_snow else "mm"

    data = ensemble_client.forecast_daily_ensemble(
        station.latitude,
        station.longitude,
        timezone=station.timezone,
        # 강수는 7일 이후 신뢰도 급낙 → 최대 7일
        forecast_days=max(3, min(7, int(lead_days) + 2)),
    )
    daily = data.get("daily") or {}
    idx = _date_index(daily, target)
    member_values = _extract_member_values(daily, variable, idx, bias_f=0.0)

    if len(member_values) < 4:
        raise ValueError(f"강수 앙상블 멤버 부족: {len(member_values)}개")

    # 경험적 투표 (threshold_mm 이상인 멤버 비율)
    votes = [x >= threshold_amount for x in member_values]
    empirical_p = sum(votes) / len(votes)

    # 확률 계산: 80% 실험적 투표 + 20% 기본율(0.5) 블렌드
    # (기온 대비 불확실성이 높아 기본율 쪽으로 더 많이 당김)
    p = clamp_probability(0.80 * empirical_p + 0.20 * 0.5)

    # 신뢰도 계산
    station_bonus = 0.08
    member_bonus = min(0.08, len(member_values) / 500.0)
    # 리드타임 페널티: 강수는 2일 이후부터 하루당 6%씩 하락
    lead_penalty = min(0.25, max(0.0, lead_days - 1) * 0.06)
    # 멤버 합의 보너스: 80% 이상 동의 시 +보너스
    agreement = abs(empirical_p - 0.5)  # 0=의견 반반, 0.5=만장일치
    agreement_bonus = min(0.10, agreement * 0.20)
    base_confidence = parsed.confidence * 0.70   # 기온 대비 기본 신뢰도 낙춤
    confidence = clamp_probability(
        base_confidence + station_bonus + member_bonus + agreement_bonus - lead_penalty
    )
    # 강수 신뢰도 하드 상한
    confidence = min(settings.precip_max_confidence, confidence)

    nonzero_count = sum(1 for v in member_values if v > 0.0)
    mean_precip = _mean(member_values)
    date_used = (daily.get("time") or [target.isoformat()])[
        min(idx, max(0, len(daily.get("time") or []) - 1))
    ]
    note = (
        f"{station.station_name} [{station.station_id}] target_date={date_used}; "
        f"{label}>={threshold_amount:.1f}{unit}; members={len(member_values)}; "
        f"vote={empirical_p:.3f}; nonzero={nonzero_count}; "
        f"mean={mean_precip:.2f}{unit}; lead={lead_days:.0f}days; "
        f"models={ensemble_client.models}. {station.note}"
    )
    return WeatherSignal(
        p_true=clamp_probability(p),
        confidence=confidence,
        source="open-meteo-ensemble-snow" if is_snow else "open-meteo-ensemble-precipitation",
        note=note,
        parsed=parsed,
    )


# ---------------------------------------------------------------------------
# 6) Main probability function used by live_paper_runner.py
# ---------------------------------------------------------------------------

def estimate_weather_probability(
    question: str,
    settings: Settings | None = None,
    client: OpenMeteoClient | None = None,
    ensemble_client: OpenMeteoEnsembleClient | None = None,
) -> WeatherSignal:
    """Estimate P(YES) for a Polymarket weather question.

    Temperature model:
    P_yes = 0.70 * ensemble_vote + 0.30 * NormalCDF(mean, dynamic_sigma)
    where each ensemble member is treated as one scenario and station/model bias
    is subtracted before threshold comparison.
    """
    settings = settings or Settings()
    parsed = parse_weather_question(question)
    if parsed.city is None or parsed.latitude is None or parsed.longitude is None:
        return WeatherSignal(0.5, 0.0, "fallback", f"Could not parse city. {parsed.note}", parsed)

    station = _station_for(parsed)
    if station is None:
        return WeatherSignal(
            0.5,
            0.0,
            "unsupported-station",
            f"{parsed.city} is not in the verified Polymarket settlement-station allowlist.",
            parsed,
        )

    if parsed.variable == "temperature" and parsed.threshold_f is not None and parsed.operator is not None:
        target = _target_date_from_hint(parsed, timezone_name=station.timezone)
        variable = _temperature_daily_variable(parsed)
        bias_f = bias_for(station, variable)
        ensemble_client = ensemble_client or OpenMeteoEnsembleClient.from_settings(settings)

        try:
            data = ensemble_client.forecast_daily_ensemble(
                station.latitude,
                station.longitude,
                timezone=station.timezone,
                forecast_days=max(3, min(16, int(_lead_time_days(target, timezone_name=station.timezone)) + 2)),
            )
            daily = data.get("daily") or {}
            idx = _date_index(daily, target)
            member_values = _extract_member_values(daily, variable, idx, bias_f=bias_f)
            if len(member_values) < 4:
                raise ValueError(f"Too few ensemble members parsed for {variable}: {len(member_values)}")

            if parsed.operator == ">=":
                votes = [x >= parsed.threshold_f for x in member_values]
            else:
                votes = [x <= parsed.threshold_f for x in member_values]
            empirical_p = sum(votes) / len(votes)
            mean_f = _mean(member_values)
            spread_f = _stdev(member_values)
            sigma_f = dynamic_sigma_f(member_values, _lead_time_days(target, timezone_name=station.timezone))
            p = blend_empirical_and_cdf(empirical_p, mean_f, parsed.threshold_f, sigma_f, parsed.operator)

            # Confidence: station mapped + enough members + low ambiguity.  Wider spread lowers confidence.
            station_bonus = 0.12
            member_bonus = min(0.18, len(member_values) / 250.0)
            spread_penalty = min(0.20, max(0.0, spread_f - 3.0) / 20.0)
            confidence = clamp_probability(parsed.confidence + station_bonus + member_bonus - spread_penalty)

            date_used = (daily.get("time") or [target.isoformat()])[min(idx, max(0, len(daily.get("time") or []) - 1))]
            note = (
                f"{station.station_name} [{station.station_id}] target_date={date_used}; "
                f"{parsed.operator}{_format_threshold(parsed)}; members={len(member_values)}; "
                f"vote={empirical_p:.3f}; mean={mean_f:.1f}F; spread={spread_f:.2f}F; "
                f"dynamic_sigma={sigma_f:.2f}F; bias={bias_f:+.2f}F; "
                f"models={ensemble_client.models}. {station.note}"
            )
            return WeatherSignal(p_true=clamp_probability(p), confidence=confidence, source="open-meteo-ensemble-station", note=note, parsed=parsed)
        except Exception as exc:  # noqa: BLE001
            fallback = _fallback_deterministic_probability(parsed, settings, client, station.timezone)
            return WeatherSignal(
                p_true=fallback.p_true,
                confidence=min(fallback.confidence, settings.deterministic_temperature_fallback_max_confidence),
                source=fallback.source,
                note=f"Ensemble model unavailable: {exc}. {fallback.note}",
                parsed=parsed,
            )

    if parsed.variable in {"precipitation", "snow"}:
        # 앙상블 기반 강수 확률 모델 (개선된 버전)
        try:
            return _ensemble_precipitation_probability(
                parsed, station, settings, ensemble_client or OpenMeteoEnsembleClient.from_settings(settings)
            )
        except Exception as exc:  # noqa: BLE001
            fallback = _fallback_deterministic_probability(parsed, settings, client)
            return WeatherSignal(
                p_true=fallback.p_true,
                confidence=min(fallback.confidence, 0.20),
                source=fallback.source,
                note=f"강수 앙상블 실패: {exc}. {fallback.note}",
                parsed=parsed,
            )

    return WeatherSignal(0.5, 0.0, "fallback", "Unsupported or weakly parsed weather market.", parsed)
