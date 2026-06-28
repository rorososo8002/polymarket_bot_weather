from __future__ import annotations

from dataclasses import dataclass

ASIA_HIGH_CITIES = frozenset(
    {
        "seoul",
        "busan",
        "tokyo",
        "beijing",
        "chengdu",
        "chongqing",
        "guangzhou",
        "hong kong",
        "kuala lumpur",
        "manila",
        "qingdao",
        "shanghai",
        "singapore",
        "taipei",
        "wuhan",
        "lucknow",
        "wellington",
    }
)

EUROPE_MIDEAST_AFRICA_CITIES = frozenset(
    {
        "amsterdam",
        "ankara",
        "cape town",
        "helsinki",
        "istanbul",
        "jeddah",
        "london",
        "madrid",
        "milan",
        "moscow",
        "munich",
        "paris",
        "tel aviv",
        "warsaw",
    }
)

AMERICAS_LOW_CITIES = frozenset(
    {
        "atlanta",
        "austin",
        "buenos aires",
        "chicago",
        "dallas",
        "denver",
        "houston",
        "los angeles",
        "mexico city",
        "miami",
        "nyc",
        "panama city",
        "san francisco",
        "sao paulo",
        "seattle",
        "toronto",
    }
)


@dataclass(frozen=True)
class CityStrategyProfile:
    region_group: str
    allow_high_intraday: bool
    allow_low_intraday: bool
    high_confirm_local_hour: int
    low_confirm_local_hour: int
    high_aggressive_yes_allowed: bool
    low_aggressive_yes_allowed: bool
    note: str = ""


def strategy_profile_for_city(
    city: str,
    *,
    high_confirm_local_hour: int = 15,
    low_confirm_local_hour: int = 8,
    us_high_disabled_before_local_hour: int = 15,
) -> CityStrategyProfile:
    normalized = city.strip().lower()
    if normalized in ASIA_HIGH_CITIES:
        return CityStrategyProfile(
            region_group="asia_india_oceania",
            allow_high_intraday=True,
            allow_low_intraday=False,
            high_confirm_local_hour=high_confirm_local_hour,
            low_confirm_local_hour=low_confirm_local_hour,
            high_aggressive_yes_allowed=True,
            low_aggressive_yes_allowed=False,
            note="High formation is preferred from the configured local confirmation hour.",
        )
    if normalized in EUROPE_MIDEAST_AFRICA_CITIES:
        return CityStrategyProfile(
            region_group="europe_mideast_africa",
            allow_high_intraday=True,
            allow_low_intraday=False,
            high_confirm_local_hour=max(15, high_confirm_local_hour),
            low_confirm_local_hour=low_confirm_local_hour,
            high_aggressive_yes_allowed=False,
            low_aggressive_yes_allowed=False,
            note="High YES is blocked before 15:00 local time.",
        )
    if normalized in AMERICAS_LOW_CITIES:
        return CityStrategyProfile(
            region_group="americas",
            allow_high_intraday=True,
            allow_low_intraday=True,
            high_confirm_local_hour=max(
                high_confirm_local_hour,
                us_high_disabled_before_local_hour,
            ),
            low_confirm_local_hour=low_confirm_local_hour,
            high_aggressive_yes_allowed=False,
            low_aggressive_yes_allowed=True,
            note="Low markets are preferred; high YES waits for the configured local hour.",
        )
    return CityStrategyProfile(
        region_group="unknown",
        allow_high_intraday=False,
        allow_low_intraday=False,
        high_confirm_local_hour=high_confirm_local_hour,
        low_confirm_local_hour=low_confirm_local_hour,
        high_aggressive_yes_allowed=False,
        low_aggressive_yes_allowed=False,
        note="City is not assigned to a validated intraday strategy region.",
    )
