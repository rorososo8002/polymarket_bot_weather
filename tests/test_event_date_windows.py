from __future__ import annotations

from datetime import date, datetime, timezone

from weather_bot.config import Settings
from weather_bot.event_dates import event_date_window_from_hint
from weather_bot.market_rules import build_market_rule_provenance
from weather_bot.nowcast import StationNowcastObservation
from weather_bot.probability import estimate_weather_probability


def test_event_date_window_normalizes_new_york_dst_day():
    window = event_date_window_from_hint(
        "june 15",
        "America/New_York",
        now=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        source_texts=("highest-temperature-in-nyc-on-june-15-2026",),
    )

    assert window is not None
    assert window.event_date_local == date(2026, 6, 15)
    assert window.event_timezone == "America/New_York"
    assert window.event_start_utc.isoformat() == "2026-06-15T04:00:00+00:00"
    assert window.event_end_utc.isoformat() == "2026-06-16T04:00:00+00:00"


def test_event_date_window_normalizes_seoul_local_day():
    window = event_date_window_from_hint(
        "june 15",
        "Asia/Seoul",
        now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        source_texts=("highest-temperature-in-seoul-on-june-15-2026",),
    )

    assert window is not None
    assert window.event_date_local == date(2026, 6, 15)
    assert window.event_start_utc.isoformat() == "2026-06-14T15:00:00+00:00"
    assert window.event_end_utc.isoformat() == "2026-06-15T15:00:00+00:00"


def test_event_date_window_normalizes_pacific_station_day():
    window = event_date_window_from_hint(
        "june 15",
        "Pacific/Auckland",
        now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        source_texts=("highest-temperature-in-wellington-on-june-15-2026",),
    )

    assert window is not None
    assert window.event_date_local == date(2026, 6, 15)
    assert window.event_start_utc.isoformat() == "2026-06-14T12:00:00+00:00"
    assert window.event_end_utc.isoformat() == "2026-06-15T12:00:00+00:00"


def test_rule_provenance_carries_station_local_date_window():
    provenance = build_market_rule_provenance(
        market_id="m1",
        question="Will NYC reach 90 F on June 15?",
        slug="nyc-90f",
        event_slug="highest-temperature-in-nyc-on-june-15-2026",
        raw={
            "description": "Resolves using the LaGuardia Airport Station.",
            "resolutionRules": "This market resolves using June 15 local time.",
        },
    )

    assert provenance.event_date_local == "2026-06-15"
    assert provenance.event_timezone == "America/New_York"
    assert provenance.event_start_utc == "2026-06-15T04:00:00+00:00"
    assert provenance.event_end_utc == "2026-06-16T04:00:00+00:00"


def test_forecast_and_nowcast_share_station_local_today_window():
    now_utc = datetime(2026, 6, 15, 1, 0, tzinfo=timezone.utc)
    seen: dict[str, object] = {}

    class FakeEnsembleClient:
        models = "fake"

        def forecast_daily_ensemble(self, *_args, **kwargs):
            seen["forecast_timezone"] = kwargs["timezone"]
            return {
                "daily": {
                    "time": ["2026-06-14"],
                    "temperature_2m_max": [91.0],
                    "temperature_2m_max_member01": [92.0],
                    "temperature_2m_max_member02": [93.0],
                    "temperature_2m_max_member03": [94.0],
                }
            }

    class RecordingNowcastProvider:
        def observed_high_so_far(self, station, *, target_date, now=None):
            seen["nowcast_station"] = station.station_id
            seen["nowcast_target_date"] = target_date
            seen["nowcast_now"] = now
            return StationNowcastObservation(
                station_id=station.station_id,
                station_name=station.station_name,
                observed_high_c=None,
                observed_at=None,
                high_observed_at=None,
                source="fixture",
                source_url="",
                settlement_source_url="",
                freshness_seconds=None,
                unavailable_reason="fixture-no-observation",
            )

    signal = estimate_weather_probability(
        "Will NYC reach 90 F today?",
        settings=Settings(),
        ensemble_client=FakeEnsembleClient(),
        observation_provider=RecordingNowcastProvider(),
        now=now_utc,
    )

    assert signal.source == "open-meteo-ensemble-station"
    assert "target_date=2026-06-14" in signal.note
    assert seen["forecast_timezone"] == "America/New_York"
    assert seen["nowcast_station"] == "KLGA"
    assert seen["nowcast_target_date"] == date(2026, 6, 14)
    assert seen["nowcast_now"] == now_utc
