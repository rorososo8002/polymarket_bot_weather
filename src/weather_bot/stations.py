from __future__ import annotations

from dataclasses import dataclass


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


def _station(
    city: str,
    station_id: str,
    station_name: str,
    latitude: float,
    longitude: float,
    timezone: str,
    elevation_m: float | None = None,
) -> StationMeta:
    return StationMeta(
        city=city,
        station_id=station_id,
        station_name=station_name,
        latitude=latitude,
        longitude=longitude,
        timezone=timezone,
        elevation_m=elevation_m,
        note="Verified from Polymarket weather resolution rules.",
    )


STATION_MAP: dict[str, StationMeta] = {
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
    "hong kong": _station("hong kong", "HKO", "Hong Kong Observatory", 22.3022, 114.1744, "Asia/Hong_Kong", 32),
    "istanbul": _station("istanbul", "LTFM", "Istanbul Airport", 41.2613, 28.7419, "Europe/Istanbul", 99),
    "jeddah": _station("jeddah", "OEJN", "King Abdulaziz International Airport Station", 21.6796, 39.1565, "Asia/Riyadh", 15),
    "karachi": _station("karachi", "OPMR", "Masroor Airbase Station", 24.8936, 66.9388, "Asia/Karachi", 16),
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
    "seoul": _station("seoul", "RKSI", "Incheon Intl Airport Station", 37.4602, 126.4407, "Asia/Seoul", 7),
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

CITY_COORDS: dict[str, tuple[float, float]] = {
    city: (station.latitude, station.longitude)
    for city, station in STATION_MAP.items()
}
