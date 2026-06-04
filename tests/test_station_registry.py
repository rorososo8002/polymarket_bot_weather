from dataclasses import replace

from weather_bot.stations import (
    STATION_MAP,
    TRADING_READY_STATION_MAP,
    station_audit_rows,
    station_is_trading_ready,
)


def _row_for(city: str) -> dict[str, object]:
    return {row["city"]: row for row in station_audit_rows()}[city]


def test_station_audit_rows_cover_every_supported_city():
    rows = station_audit_rows()

    assert len(rows) == 41
    assert {row["city"] for row in rows} == set(STATION_MAP)


def test_station_audit_rows_explain_forecast_and_rule_evidence_status():
    for row in station_audit_rows():
        assert row["forecast_source"] == "open-meteo-ensemble"
        assert row["forecast_location_source"] == "settlement_station_coordinates"
        assert row["station_verification_status"] == "verified_from_existing_registry"
        if row["trading_ready"]:
            assert row["rule_evidence_status"] == "verified_rule_source"
            assert str(row["polymarket_rule_url"]).startswith("https://polymarket.com/")
            assert row["polymarket_rule_station_text"]
        else:
            assert row["rule_evidence_status"] != "verified_rule_source"


def test_station_without_rule_evidence_is_not_trading_ready():
    station = replace(
        STATION_MAP["seoul"],
        rule_evidence_status="needs_rule_source_url",
        polymarket_rule_url="",
        polymarket_rule_station_text="",
    )

    assert not station_is_trading_ready(station)


def test_trading_ready_map_only_contains_verified_rule_evidence():
    assert TRADING_READY_STATION_MAP
    assert set(TRADING_READY_STATION_MAP).issubset(STATION_MAP)
    assert all(station_is_trading_ready(station) for station in TRADING_READY_STATION_MAP.values())


def test_seoul_uses_enabled_metar_observation_provider():
    seoul = _row_for("seoul")

    assert seoul["station_id"] == "RKSI"
    assert seoul["nowcast_source_type"] == "metar"
    assert seoul["nowcast_station_id"] == "RKSI"
    assert seoul["nowcast_provider_status"] == "provider_enabled"


def test_hong_kong_is_not_treated_as_a_metar_candidate():
    hong_kong = _row_for("hong kong")

    assert hong_kong["station_id"] == "HKO"
    assert hong_kong["nowcast_source_type"] == "hko_maxmin_since_midnight"
    assert hong_kong["nowcast_station_id"] == "HKO"
    assert hong_kong["nowcast_provider_status"] == "provider_enabled"


def test_icao_stations_use_awc_metar_except_known_unavailable_station():
    for row in station_audit_rows():
        if row["city"] == "hong kong":
            continue

        assert row["nowcast_station_id"] == row["station_id"]
        assert len(str(row["station_id"])) == 4
        if row["city"] == "karachi":
            assert row["nowcast_source_type"] == "metar_unavailable"
            assert row["nowcast_provider_status"] == "provider_unavailable"
        else:
            assert row["nowcast_source_type"] == "metar"
            assert row["nowcast_provider_status"] == "provider_enabled"


def test_trading_ready_nowcast_sources_are_metar_bulk_plus_single_hko_csv():
    trading_ready_sources = [
        station.nowcast_source_type
        for station in TRADING_READY_STATION_MAP.values()
    ]

    assert trading_ready_sources.count("metar") == 39
    assert trading_ready_sources.count("hko_maxmin_since_midnight") == 1
    assert "metar_unavailable" not in trading_ready_sources
