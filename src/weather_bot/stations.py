from __future__ import annotations

from dataclasses import dataclass, replace

FORECAST_SOURCE = "open-meteo-ensemble"
FORECAST_LOCATION_SOURCE = "settlement_station_coordinates"
DEFAULT_STATION_VERIFICATION_STATUS = "verified_from_existing_registry"
DEFAULT_RULE_EVIDENCE_STATUS = "needs_rule_source_url"
DEFAULT_NOWCAST_PROVIDER_STATUS = "provider_enabled"
DEFAULT_TEMPERATURE_UNIT = "celsius"
DEFAULT_REPORTING_PRECISION = "1C"
DEFAULT_STATION_LAST_VERIFIED_AT = "2026-06-14"
RULE_EVIDENCE_TRADING_READY_STATUS = "verified_rule_source"
RULE_EVIDENCE_STATION_ID_CONFLICT_STATUS = "rule_station_id_conflict"
ACCEPTABLE_NOWCAST_CONFIDENCE_GRADES = frozenset({"A", "B"})
CONFIDENCE_LEVEL_BY_GRADE = {
    "A": "high",
    "B": "medium",
    "C": "low",
    "D": "blocked",
}


@dataclass(frozen=True)
class StationMeta:
    city: str
    station_id: str
    station_name: str
    latitude: float
    longitude: float
    timezone: str = "auto"
    elevation_m: float | None = None
    note: str = ""
    forecast_source: str = FORECAST_SOURCE
    forecast_location_source: str = FORECAST_LOCATION_SOURCE
    station_verification_status: str = DEFAULT_STATION_VERIFICATION_STATUS
    rule_evidence_status: str = DEFAULT_RULE_EVIDENCE_STATUS
    polymarket_rule_url: str = ""
    polymarket_rule_station_text: str = ""
    temperature_unit: str = DEFAULT_TEMPERATURE_UNIT
    reporting_precision: str = DEFAULT_REPORTING_PRECISION
    same_station_nowcast_supported: bool = True
    nowcast_confidence_grade: str = "A"
    last_verified_at: str = DEFAULT_STATION_LAST_VERIFIED_AT
    confidence_level: str = "high"
    nowcast_source_type: str = "metar"
    nowcast_station_id: str = ""
    nowcast_provider_status: str = DEFAULT_NOWCAST_PROVIDER_STATUS


def _confidence_level_for_grade(grade: str) -> str:
    return CONFIDENCE_LEVEL_BY_GRADE.get(grade.upper(), "blocked")


def _station(
    city: str,
    station_id: str,
    station_name: str,
    latitude: float,
    longitude: float,
    timezone: str,
    elevation_m: float | None = None,
    nowcast_source_type: str = "metar",
    nowcast_provider_status: str = DEFAULT_NOWCAST_PROVIDER_STATUS,
    nowcast_station_id: str | None = None,
    temperature_unit: str = DEFAULT_TEMPERATURE_UNIT,
    reporting_precision: str = DEFAULT_REPORTING_PRECISION,
    same_station_nowcast_supported: bool | None = None,
    nowcast_confidence_grade: str | None = None,
    last_verified_at: str = DEFAULT_STATION_LAST_VERIFIED_AT,
    confidence_level: str | None = None,
) -> StationMeta:
    supports_same_station = (
        nowcast_provider_status == DEFAULT_NOWCAST_PROVIDER_STATUS
        if same_station_nowcast_supported is None
        else same_station_nowcast_supported
    )
    resolved_grade = (
        nowcast_confidence_grade
        or ("A" if supports_same_station else "D")
    ).upper()
    return StationMeta(
        city=city,
        station_id=station_id,
        station_name=station_name,
        latitude=latitude,
        longitude=longitude,
        timezone=timezone,
        elevation_m=elevation_m,
        note="Verified from Polymarket weather resolution rules.",
        temperature_unit=temperature_unit,
        reporting_precision=reporting_precision,
        same_station_nowcast_supported=supports_same_station,
        nowcast_confidence_grade=resolved_grade,
        last_verified_at=last_verified_at,
        confidence_level=confidence_level or _confidence_level_for_grade(resolved_grade),
        nowcast_source_type=nowcast_source_type,
        nowcast_station_id=nowcast_station_id or station_id,
        nowcast_provider_status=nowcast_provider_status,
    )


_STATION_MAP_BASE: dict[str, StationMeta] = {
    "amsterdam": _station("amsterdam", "EHAM", "Amsterdam Airport Schiphol Station", 52.3086, 4.7639, "Europe/Amsterdam", -3),
    "ankara": _station("ankara", "LTAC", "Esenboga Intl Airport Station", 40.1281, 32.9951, "Europe/Istanbul", 953),
    "atlanta": _station("atlanta", "KATL", "Hartsfield-Jackson International Airport Station", 33.6407, -84.4277, "America/New_York", 313),
    "beijing": _station("beijing", "ZBAA", "Beijing Capital International Airport Station", 40.0801, 116.5846, "Asia/Shanghai", 35),
    "buenos aires": _station("buenos aires", "SAEZ", "Minister Pistarini Intl Airport Station", -34.8222, -58.5358, "America/Argentina/Buenos_Aires", 20),
    "busan": _station("busan", "RKPK", "Gimhae Intl Airport Station", 35.1795, 128.9382, "Asia/Seoul", 2),
    "cape town": _station("cape town", "FACT", "Cape Town International Airport Station", -33.9700, 18.6021, "Africa/Johannesburg", 46),
    "chengdu": _station("chengdu", "ZUUU", "Chengdu Shuangliu International Airport Station", 30.5785, 103.9471, "Asia/Shanghai", 495),
    "chicago": _station("chicago", "KORD", "Chicago O'Hare Intl Airport Station", 41.9742, -87.9073, "America/Chicago", 204),
    "chongqing": _station("chongqing", "ZUCK", "Chongqing Jiangbei International Airport Station", 29.7192, 106.6417, "Asia/Shanghai", 416),
    "dallas": _station("dallas", "KDAL", "Dallas Love Field Station", 32.8471, -96.8518, "America/Chicago", 148),
    "guangzhou": _station("guangzhou", "ZGGG", "Guangzhou Baiyun International Airport Station", 23.3924, 113.2988, "Asia/Shanghai", 15),
    "helsinki": _station("helsinki", "EFHK", "Helsinki Vantaa Airport Station", 60.3172, 24.9633, "Europe/Helsinki", 55),
    "hong kong": _station(
        "hong kong",
        "HKO",
        "Hong Kong Observatory",
        22.3022,
        114.1744,
        "Asia/Hong_Kong",
        32,
        nowcast_source_type="hko_maxmin_since_midnight",
        nowcast_provider_status="provider_enabled",
        reporting_precision="0.1C",
    ),
    "istanbul": _station("istanbul", "LTFM", "Istanbul Airport", 41.2613, 28.7419, "Europe/Istanbul", 99),
    "jeddah": _station("jeddah", "OEJN", "King Abdulaziz International Airport Station", 21.6796, 39.1565, "Asia/Riyadh", 15),
    "karachi": _station(
        "karachi",
        "OPMR",
        "Masroor Airbase Station",
        24.8936,
        66.9388,
        "Asia/Karachi",
        16,
        nowcast_source_type="metar_unavailable",
        nowcast_provider_status="provider_unavailable",
    ),
    "london": _station("london", "EGLC", "London City Airport Station", 51.5053, 0.0553, "Europe/London", 6),
    "los angeles": _station("los angeles", "KLAX", "Los Angeles International Airport Station", 33.9416, -118.4085, "America/Los_Angeles", 38),
    "madrid": _station("madrid", "LEMD", "Adolfo Suarez Madrid-Barajas Airport Station", 40.4983, -3.5676, "Europe/Madrid", 610),
    "manila": _station("manila", "RPLL", "Ninoy Aquino International Airport Station", 14.5086, 121.0196, "Asia/Manila", 23),
    "miami": _station("miami", "KMIA", "Miami Intl Airport Station", 25.7959, -80.2870, "America/New_York", 3),
    "milan": _station("milan", "LIMC", "Malpensa Intl Airport Station", 45.6306, 8.7281, "Europe/Rome", 234),
    "moscow": _station("moscow", "UUWW", "Vnukovo International Airport", 55.5915, 37.2615, "Europe/Moscow", 209),
    "munich": _station("munich", "EDDM", "Munich Airport Station", 48.3538, 11.7861, "Europe/Berlin", 448),
    "nyc": _station("nyc", "KLGA", "LaGuardia Airport Station", 40.7769, -73.8740, "America/New_York", 6),
    "panama city": _station("panama city", "MPMG", "Marcos A. Gelabert Intl Airport Station", 8.9733, -79.5556, "America/Panama", 9),
    "paris": _station("paris", "LFPB", "Paris-Le Bourget Airport Station", 48.9694, 2.4414, "Europe/Paris", 67),
    "qingdao": _station("qingdao", "ZSQD", "Qingdao Jiaodong International Airport Station", 36.3619, 120.0881, "Asia/Shanghai", 11),
    "seattle": _station("seattle", "KSEA", "Seattle-Tacoma International Airport Station", 47.4502, -122.3088, "America/Los_Angeles", 131),
    "seoul": _station(
        "seoul",
        "RKSI",
        "Incheon Intl Airport Station",
        37.4602,
        126.4407,
        "Asia/Seoul",
        7,
        nowcast_source_type="metar",
        nowcast_provider_status="provider_enabled",
    ),
    "shanghai": _station("shanghai", "ZSPD", "Shanghai Pudong International Airport Station", 31.1443, 121.8083, "Asia/Shanghai", 4),
    "shenzhen": _station("shenzhen", "ZGSZ", "Shenzhen Bao'an International Airport Station", 22.6393, 113.8107, "Asia/Shanghai", 4),
    "singapore": _station("singapore", "WSSS", "Singapore Changi Airport Station", 1.3644, 103.9915, "Asia/Singapore", 7),
    "taipei": _station("taipei", "RCSS", "Taipei Songshan Airport Station", 25.0697, 121.5525, "Asia/Taipei", 5),
    "tel aviv": _station("tel aviv", "LLBG", "Ben Gurion International Airport", 32.0055, 34.8854, "Asia/Jerusalem", 41),
    "tokyo": _station("tokyo", "RJTT", "Tokyo Haneda Airport Station", 35.5494, 139.7798, "Asia/Tokyo", 6),
    "toronto": _station("toronto", "CYYZ", "Toronto Pearson Intl Airport Station", 43.6777, -79.6248, "America/Toronto", 173),
    "warsaw": _station("warsaw", "EPWA", "Warsaw Chopin Airport Station", 52.1657, 20.9671, "Europe/Warsaw", 110),
    "wellington": _station("wellington", "NZWN", "Wellington Intl Airport Station", -41.3272, 174.8053, "Pacific/Auckland", 13),
    "wuhan": _station("wuhan", "ZHHH", "Wuhan Tianhe International Airport Station", 30.7838, 114.2081, "Asia/Shanghai", 34),
}


def _rule(
    url: str,
    station_text: str,
    status: str = RULE_EVIDENCE_TRADING_READY_STATUS,
) -> dict[str, str]:
    return {
        "polymarket_rule_url": url,
        "polymarket_rule_station_text": station_text,
        "rule_evidence_status": status,
    }


_RULE_EVIDENCE: dict[str, dict[str, str]] = {
    "amsterdam": _rule(
        "https://polymarket.com/event/highest-temperature-in-amsterdam-on-april-30-2026",
        "highest temperature recorded at the Amsterdam Airport Schiphol Station",
    ),
    "ankara": _rule(
        "https://polymarket.com/pt/event/highest-temperature-in-ankara-on-march-26-2026/highest-temperature-in-ankara-on-march-26-2026-11c",
        "highest temperature recorded at the Esenboga Intl Airport Station",
    ),
    "atlanta": _rule(
        "https://polymarket.com/id/event/highest-temperature-in-atlanta-on-april-26-2026/highest-temperature-in-atlanta-on-april-26-2026-86-87f",
        "highest temperature recorded at the Hartsfield-Jackson International Airport Station",
    ),
    "beijing": _rule(
        "https://polymarket.com/event/highest-temperature-in-beijing-on-march-27-2026/highest-temperature-in-beijing-on-march-27-2026-17c",
        "highest temperature recorded at the Beijing Capital International Airport Station",
    ),
    "buenos aires": _rule(
        "https://polymarket.com/event/highest-temperature-in-buenos-aires-on-march-15-2026/highest-temperature-in-buenos-aires-on-march-15-2026-32corhigher",
        "highest temperature recorded at the Minister Pistarini Intl Airport Station",
    ),
    "busan": _rule(
        "https://polymarket.com/event/highest-temperature-in-busan-on-april-26-2026",
        "highest temperature recorded at the Gimhae Intl Airport Station",
    ),
    "cape town": _rule(
        "https://polymarket.com/event/highest-temperature-in-cape-town-on-may-6-2026/highest-temperature-in-cape-town-on-may-6-2026-18c",
        "highest temperature recorded at the Cape Town International Airport Station",
    ),
    "chengdu": _rule(
        "https://polymarket.com/event/highest-temperature-in-chengdu-on-may-14-2026/highest-temperature-in-chengdu-on-may-14-2026-33corhigher",
        "highest temperature recorded at the Chengdu Shuangliu International Airport Station",
    ),
    "chicago": _rule(
        "https://polymarket.com/event/highest-temperature-in-chicago-on-march-17-2026/highest-temperature-in-chicago-on-march-17-2026-22-23f",
        "highest temperature recorded at the Chicago O'Hare Intl Airport Station",
    ),
    "chongqing": _rule(
        "https://polymarket.com/event/highest-temperature-in-chongqing-on-march-22-2026/highest-temperature-in-chongqing-on-march-22-2026-20c",
        "highest temperature recorded at the Chongqing Jiangbei International Airport Station",
    ),
    "dallas": _rule(
        "https://polymarket.com/event/highest-temperature-in-dallas-on-march-26-2026/highest-temperature-in-dallas-on-march-26-2026-86-87f",
        "highest temperature recorded at the Dallas Love Field Station",
    ),
    "guangzhou": _rule(
        "https://polymarket.com/event/highest-temperature-in-guangzhou-on-may-14-2026/highest-temperature-in-guangzhou-on-may-14-2026-29c",
        "highest temperature recorded at the Guangzhou Baiyun International Airport Station",
    ),
    "helsinki": _rule(
        "https://polymarket.com/event/highest-temperature-in-helsinki-on-april-7-2026/highest-temperature-in-helsinki-on-april-7-2026-4c",
        "highest temperature recorded at the Helsinki Vantaa Airport Station",
    ),
    "hong kong": _rule(
        "https://polymarket.com/event/highest-temperature-in-hong-kong-on-march-27-2026",
        "highest temperature recorded by the Hong Kong Observatory",
    ),
    "istanbul": _rule(
        "https://polymarket.com/event/highest-temperature-in-istanbul-on-may-17-2026/highest-temperature-in-istanbul-on-may-17-2026-18c",
        "highest temperature recorded by NOAA at the Istanbul Airport",
    ),
    "jeddah": _rule(
        "https://polymarket.com/event/highest-temperature-in-jeddah-on-may-17-2026/highest-temperature-in-jeddah-on-may-17-2026-36c",
        "highest temperature recorded at the King Abdulaziz International Airport Station",
    ),
    "karachi": _rule(
        "https://polymarket.com/ru/event/highest-temperature-in-karachi-on-may-17-2026/highest-temperature-in-karachi-on-may-17-2026-37corhigher",
        "highest temperature recorded at the Masroor Airbase Station; source URL uses OPKC, while registry uses OPMR",
        RULE_EVIDENCE_STATION_ID_CONFLICT_STATUS,
    ),
    "london": _rule(
        "https://polymarket.com/event/highest-temperature-in-london-on-february-16-2026/highest-temperature-in-london-on-february-16-2026-4corbelow",
        "highest temperature recorded at the London City Airport Station",
    ),
    "los angeles": _rule(
        "https://polymarket.com/event/highest-temperature-in-los-angeles-on-may-24-2026/highest-temperature-in-los-angeles-on-may-24-2026-62-63f",
        "highest temperature recorded at the Los Angeles International Airport Station",
    ),
    "madrid": _rule(
        "https://polymarket.com/event/highest-temperature-in-madrid-on-may-27-2026/highest-temperature-in-madrid-on-may-27-2026-35c",
        "highest temperature recorded at the Adolfo Suarez Madrid-Barajas Airport Station",
    ),
    "manila": _rule(
        "https://polymarket.com/event/highest-temperature-in-manila-on-april-26-2026/highest-temperature-in-manila-on-april-26-2026-39c",
        "highest temperature recorded at the Ninoy Aquino International Airport Station",
    ),
    "miami": _rule(
        "https://polymarket.com/event/highest-temperature-in-miami-on-march-28-2026/highest-temperature-in-miami-on-march-28-2026-82-83f",
        "highest temperature recorded at the Miami Intl Airport Station",
    ),
    "milan": _rule(
        "https://polymarket.com/event/highest-temperature-in-milan-on-april-29-2026/highest-temperature-in-milan-on-april-29-2026-17c",
        "highest temperature recorded at the Malpensa Intl Airport Station",
    ),
    "moscow": _rule(
        "https://polymarket.com/id/event/highest-temperature-in-moscow-on-may-9-2026/highest-temperature-in-moscow-on-may-9-2026-16c",
        "highest temperature recorded by NOAA at the Vnukovo International Airport",
    ),
    "munich": _rule(
        "https://polymarket.com/event/highest-temperature-in-munich-on-may-23-2026/highest-temperature-in-munich-on-may-23-2026-27c",
        "highest temperature recorded at the Munich Airport Station",
    ),
    "nyc": _rule(
        "https://polymarket.com/event/highest-temperature-in-nyc-on-february-22-2026/highest-temperature-in-nyc-on-february-22-2026-36-37f",
        "highest temperature recorded at the LaGuardia Airport Station",
    ),
    "panama city": _rule(
        "https://polymarket.com/event/highest-temperature-in-panama-city-on-may-12-2026/highest-temperature-in-panama-city-on-may-12-2026-29c",
        "highest temperature recorded at the Marcos A. Gelabert Intl Airport Station",
    ),
    "paris": _rule(
        "https://polymarket.com/event/lowest-temperature-in-paris-on-may-7-2026/lowest-temperature-in-paris-on-may-7-2026-8c",
        "lowest temperature recorded at the Paris-Le Bourget Airport Station",
    ),
    "qingdao": _rule(
        "https://polymarket.com/event/highest-temperature-in-qingdao-on-may-29-2026/highest-temperature-in-qingdao-on-may-29-2026-26corbelow",
        "highest temperature recorded at the Qingdao Jiaodong International Airport Station",
    ),
    "seattle": _rule(
        "https://polymarket.com/event/highest-temperature-in-seattle-on-may-13-2026",
        "highest temperature recorded at the Seattle-Tacoma International Airport Station",
    ),
    "seoul": _rule(
        "https://polymarket.com/event/highest-temperature-in-seoul-on-march-27-2026/highest-temperature-in-seoul-on-march-27-2026-16corhigher",
        "highest temperature recorded at the Incheon Intl Airport Station",
    ),
    "shanghai": _rule(
        "https://polymarket.com/event/highest-temperature-in-shanghai-on-may-6-2026/highest-temperature-in-shanghai-on-may-6-2026-29corhigher",
        "highest temperature recorded at the Shanghai Pudong International Airport Station",
    ),
    "shenzhen": _rule(
        "https://polymarket.com/event/highest-temperature-in-shenzhen-on-may-20-2026/highest-temperature-in-shenzhen-on-may-20-2026-31corhigher",
        "highest temperature recorded at the Shenzhen Bao'an International Airport Station",
    ),
    "singapore": _rule(
        "https://polymarket.com/event/highest-temperature-in-singapore-on-march-17-2026/highest-temperature-in-singapore-on-march-17-2026-24corbelow",
        "highest temperature recorded at the Singapore Changi Airport Station",
    ),
    "taipei": _rule(
        "https://polymarket.com/zh-hant/event/highest-temperature-in-taipei-on-may-17-2026/highest-temperature-in-taipei-on-may-17-2026-31c",
        "highest temperature recorded at the Taipei Songshan Airport Station",
    ),
    "tel aviv": _rule(
        "https://polymarket.com/event/highest-temperature-in-tel-aviv-on-march-26-2026/highest-temperature-in-tel-aviv-on-march-26-2026-17c",
        "highest temperature recorded by NOAA at the Ben Gurion International Airport",
    ),
    "tokyo": _rule(
        "https://polymarket.com/event/highest-temperature-in-tokyo-on-march-12-2026/highest-temperature-in-tokyo-on-march-12-2026-13c",
        "highest temperature recorded at the Tokyo Haneda Airport Station",
    ),
    "toronto": _rule(
        "https://polymarket.com/vi/event/highest-temperature-in-toronto-on-march-25-2026/highest-temperature-in-toronto-on-march-25-2026-8c",
        "highest temperature recorded at the Toronto Pearson Intl Airport Station",
    ),
    "warsaw": _rule(
        "https://polymarket.com/event/highest-temperature-in-warsaw-on-may-11-2026/highest-temperature-in-warsaw-on-may-11-2026-27c",
        "highest temperature recorded at the Warsaw Chopin Airport Station",
    ),
    "wellington": _rule(
        "https://polymarket.com/es/event/highest-temperature-in-wellington-on-march-27-2026",
        "highest temperature recorded at the Wellington Intl Airport Station",
    ),
    "wuhan": _rule(
        "https://polymarket.com/event/highest-temperature-in-wuhan-on-may-27-2026/highest-temperature-in-wuhan-on-may-27-2026-29c",
        "highest temperature recorded at the Wuhan Tianhe International Airport Station",
    ),
}


def _with_rule_evidence(city: str, station: StationMeta) -> StationMeta:
    evidence = _RULE_EVIDENCE.get(city)
    if evidence is None:
        return station
    return replace(
        station,
        rule_evidence_status=evidence["rule_evidence_status"],
        polymarket_rule_url=evidence["polymarket_rule_url"],
        polymarket_rule_station_text=evidence["polymarket_rule_station_text"],
        note="Verified from stored Polymarket weather resolution-rule evidence.",
    )


STATION_MAP: dict[str, StationMeta] = {
    city: _with_rule_evidence(city, station)
    for city, station in _STATION_MAP_BASE.items()
}


def station_is_trading_ready(station: StationMeta) -> bool:
    """Return True only when rule evidence and same-station confidence are usable."""
    return (
        station.rule_evidence_status == RULE_EVIDENCE_TRADING_READY_STATUS
        and station.polymarket_rule_url.startswith("https://polymarket.com/")
        and bool(station.polymarket_rule_station_text.strip())
        and station.same_station_nowcast_supported
        and station.nowcast_provider_status == DEFAULT_NOWCAST_PROVIDER_STATUS
        and station.nowcast_confidence_grade in ACCEPTABLE_NOWCAST_CONFIDENCE_GRADES
    )


TRADING_READY_STATION_MAP: dict[str, StationMeta] = {
    city: station
    for city, station in STATION_MAP.items()
    if station_is_trading_ready(station)
}

CITY_COORDS: dict[str, tuple[float, float]] = {
    city: (station.latitude, station.longitude)
    for city, station in STATION_MAP.items()
}

SUPPORTED_CITY_COUNT = len(STATION_MAP)
TRADING_READY_CITY_COUNT = len(TRADING_READY_STATION_MAP)


def station_audit_rows() -> list[dict[str, object]]:
    """Return a compact, user-auditable view of forecast and nowcast coverage."""
    return [
        {
            "city": station.city,
            "station_id": station.station_id,
            "station_name": station.station_name,
            "latitude": station.latitude,
            "longitude": station.longitude,
            "timezone": station.timezone,
            "forecast_source": station.forecast_source,
            "forecast_location_source": station.forecast_location_source,
            "station_verification_status": station.station_verification_status,
            "rule_evidence_status": station.rule_evidence_status,
            "polymarket_rule_url": station.polymarket_rule_url,
            "polymarket_rule_station_text": station.polymarket_rule_station_text,
            "trading_ready": station_is_trading_ready(station),
            "temperature_unit": station.temperature_unit,
            "reporting_precision": station.reporting_precision,
            "same_station_nowcast_supported": station.same_station_nowcast_supported,
            "nowcast_confidence_grade": station.nowcast_confidence_grade,
            "last_verified_at": station.last_verified_at,
            "confidence_level": station.confidence_level,
            "nowcast_source_type": station.nowcast_source_type,
            "nowcast_station_id": station.nowcast_station_id,
            "nowcast_provider_status": station.nowcast_provider_status,
        }
        for station in STATION_MAP.values()
    ]
