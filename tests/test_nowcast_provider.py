from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from weather_bot.nowcast import AviationWeatherMetarNowcastProvider, DEFAULT_NOWCAST_SOURCES
from weather_bot.stations import STATION_MAP, station_audit_rows


FIXTURES = Path(__file__).parent / "fixtures" / "nowcast"


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload

    @property
    def text(self):
        if isinstance(self._payload, str):
            return self._payload
        return json.dumps(self._payload)


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_text_fixture(name: str):
    return (FIXTURES / name).read_text(encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_aviationweather_provider_default_cache_ttl_matches_provider_floor():
    provider = AviationWeatherMetarNowcastProvider(http_get=lambda *_args, **_kwargs: FakeResponse({}))

    assert provider.cache_ttl_seconds == 60


def provider_for(
    payload,
    *,
    freshness_seconds: int = 5400,
    cache_ttl_seconds: int = 0,
    request_log_path: Path | None = None,
):
    calls = []

    def fake_get(url, *, params, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        return FakeResponse(payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        freshness_seconds=freshness_seconds,
        cache_ttl_seconds=cache_ttl_seconds,
        request_log_path=request_log_path,
    )
    return provider, calls


def metar_sequence_provider(payloads: list[list[dict[str, object]]], *, state_path: Path):
    remaining = iter(payloads)

    def fake_get(url, *, params, timeout, headers):
        return FakeResponse(next(remaining))

    return AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=0,
        metar_daily_extremes_state_path=state_path,
    )


def test_aviationweather_latest_only_is_not_a_complete_daily_extreme(tmp_path):
    provider = metar_sequence_provider(
        [[
            {
                "icaoId": "RJTT",
                "obsTime": "2026-06-23T06:00:00.000Z",
                "temp": 23.0,
                "dewp": 22.0,
                "wxString": "-RA",
                "rawOb": "RJTT 230600Z 18005KT 9999 FEW020 23/18 Q1010",
            }
        ]],
        state_path=tmp_path / "metar_daily_extremes_state.json",
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 6, 5, tzinfo=timezone.utc),
    )

    assert observation.observed_high_c == 23.0
    assert observation.observed_low_c == 23.0
    assert observation.latest_temp_c == 23.0
    assert observation.latest_dewpoint_c == 22.0
    assert observation.latest_weather == "-RA"
    assert observation.daily_extremes_complete is False
    assert observation.data_block_reason == "metar-daily-extremes-baseline-missing"


def test_aviationweather_midnight_handoff_builds_persistent_daily_extremes(tmp_path):
    state_path = tmp_path / "metar_daily_extremes_state.json"
    provider = metar_sequence_provider(
        [
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T14:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221400Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T15:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221500Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
        ],
        state_path=state_path,
    )

    previous = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 22, 14, 5, tzinfo=timezone.utc),
    )
    midnight = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 15, 5, tzinfo=timezone.utc),
    )

    assert previous.daily_extremes_complete is False
    assert midnight.daily_extremes_complete is True
    assert midnight.observed_high_c == 20.0
    assert midnight.observed_low_c == 20.0

    restarted = metar_sequence_provider(
        [[{
            "icaoId": "RJTT",
            "obsTime": "2026-06-22T16:00:00.000Z",
            "temp": 19.0,
            "rawOb": "RJTT 221600Z 18005KT 9999 FEW020 19/18 Q1010",
        }]],
        state_path=state_path,
    )
    continued = restarted.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 16, 5, tzinfo=timezone.utc),
    )

    assert continued.daily_extremes_complete is True
    assert continued.observed_high_c == 20.0
    assert continued.observed_low_c == 19.0


def test_aviationweather_counts_repeated_high_integer_bucket_confirmations(tmp_path):
    state_path = tmp_path / "metar_daily_extremes_state.json"
    provider = metar_sequence_provider(
        [
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T14:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221400Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T15:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221500Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T16:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221600Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
        ],
        state_path=state_path,
    )

    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 22, 14, 5, tzinfo=timezone.utc),
    )
    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 15, 5, tzinfo=timezone.utc),
    )
    confirmed = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 16, 5, tzinfo=timezone.utc),
    )

    assert confirmed.observed_high_c == 20.0
    assert confirmed.high_bucket_confirmations == 2


def test_aviationweather_tracks_high_plateau_and_drop_times(tmp_path):
    state_path = tmp_path / "metar_daily_extremes_state.json"
    provider = metar_sequence_provider(
        [
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T14:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221400Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T15:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221500Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T16:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221600Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
        ],
        state_path=state_path,
    )

    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 22, 14, 5, tzinfo=timezone.utc),
    )
    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 15, 5, tzinfo=timezone.utc),
    )
    dropped = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 16, 5, tzinfo=timezone.utc),
    )

    assert dropped.observed_high_c == 21.0
    assert dropped.high_observed_at.isoformat() == "2026-06-22T15:00:00+00:00"
    assert dropped.high_last_observed_at.isoformat() == "2026-06-22T15:00:00+00:00"
    assert dropped.high_drop_observed_at.isoformat() == "2026-06-22T16:00:00+00:00"


def test_aviationweather_tracks_low_plateau_and_rise_times(tmp_path):
    state_path = tmp_path / "metar_daily_extremes_state.json"
    provider = metar_sequence_provider(
        [
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T14:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221400Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T15:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221500Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T16:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221600Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T17:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221700Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
        ],
        state_path=state_path,
    )

    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 22, 14, 5, tzinfo=timezone.utc),
    )
    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 15, 5, tzinfo=timezone.utc),
    )
    before_rise = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 16, 5, tzinfo=timezone.utc),
    )
    risen = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 17, 5, tzinfo=timezone.utc),
    )

    assert before_rise.observed_low_c == 20.0
    assert before_rise.low_rise_observed_at is None
    assert risen.observed_low_c == 20.0
    assert risen.low_observed_at.isoformat() == "2026-06-22T15:00:00+00:00"
    assert risen.low_last_observed_at.isoformat() == "2026-06-22T16:00:00+00:00"
    assert risen.low_rise_observed_at.isoformat() == "2026-06-22T17:00:00+00:00"


def test_aviationweather_observation_gap_invalidates_daily_extremes(tmp_path):
    state_path = tmp_path / "metar_daily_extremes_state.json"
    provider = metar_sequence_provider(
        [
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T14:00:00.000Z",
                "temp": 21.0,
                "rawOb": "RJTT 221400Z 18005KT 9999 FEW020 21/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T15:00:00.000Z",
                "temp": 20.0,
                "rawOb": "RJTT 221500Z 18005KT 9999 FEW020 20/18 Q1010",
            }],
            [{
                "icaoId": "RJTT",
                "obsTime": "2026-06-22T17:00:00.000Z",
                "temp": 19.0,
                "rawOb": "RJTT 221700Z 18005KT 9999 FEW020 19/18 Q1010",
            }],
        ],
        state_path=state_path,
    )

    provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 22, 14, 5, tzinfo=timezone.utc),
    )
    complete = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 15, 5, tzinfo=timezone.utc),
    )
    gapped = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 23),
        now=datetime(2026, 6, 22, 17, 5, tzinfo=timezone.utc),
    )

    assert complete.daily_extremes_complete is True
    assert gapped.daily_extremes_complete is False
    assert gapped.data_block_reason == "metar-observation-gap"
    assert gapped.observed_high_c == 20.0
    assert gapped.observed_low_c == 19.0


def test_aviationweather_provider_returns_fresh_station_high_from_fixture():
    provider, calls = provider_for(load_fixture("aviationweather_rksi_fresh.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.observed_high_c == 26.7
    assert observation.observed_at.isoformat() == "2026-06-02T08:00:00+00:00"
    assert observation.high_observed_at.isoformat() == "2026-06-02T08:00:00+00:00"
    assert observation.freshness_seconds == 1800
    assert observation.source == "aviationweather-metar"
    assert observation.unavailable_reason == ""
    assert observation.raw_observation_count == 3
    requested_ids = set(calls[0]["params"]["ids"].split(","))
    assert "RKSI" in requested_ids
    assert "HKO" not in requested_ids
    assert calls[0]["params"]["format"] == "json"


def test_aviationweather_provider_returns_high_and_low_from_one_cached_fetch():
    provider, calls = provider_for(load_fixture("aviationweather_rksi_fresh.json"), cache_ttl_seconds=900)

    high_observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )
    low_observation = provider.observed_low_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 31, tzinfo=timezone.utc),
    )

    assert high_observation.observed_high_c == 26.7
    assert low_observation.observed_low_c == 18.0
    assert low_observation.low_observed_at.isoformat() == "2026-06-02T00:00:00+00:00"
    assert len(calls) == 1


def test_aviationweather_provider_prefetches_multiple_metar_stations_once_per_refresh():
    payload = [
        {
            "icaoId": "KLGA",
            "obsTime": "2026-06-02T10:00:00.000Z",
            "temp": 19.4,
            "rawOb": "KLGA 021000Z 21008KT 10SM FEW050 19/14 A2992 RMK T01940140",
        },
        {
            "icaoId": "KLGA",
            "obsTime": "2026-06-02T18:00:00.000Z",
            "temp": 25.6,
            "rawOb": "KLGA 021800Z 22009KT 10SM FEW050 26/14 A2991",
        },
        {
            "icaoId": "KATL",
            "obsTime": "2026-06-02T11:00:00.000Z",
            "temp": 21.1,
            "rawOb": "KATL 021100Z 23005KT 10SM FEW030 21/16 A3002 RMK T02110160",
        },
        {
            "icaoId": "KATL",
            "obsTime": "2026-06-02T18:00:00.000Z",
            "temp": 29.4,
            "rawOb": "KATL 021800Z 24007KT 10SM FEW040 29/17 A2999 RMK T02940170",
        },
    ]
    provider, calls = provider_for(payload, cache_ttl_seconds=900)
    now = datetime(2026, 6, 2, 18, 30, tzinfo=timezone.utc)

    nyc_observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["nyc"],
        target_date=date(2026, 6, 2),
        now=now,
    )
    atlanta_observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["atlanta"],
        target_date=date(2026, 6, 2),
        now=now,
    )

    assert nyc_observation.observed_high_c == 25.6
    assert nyc_observation.observed_low_c == 19.4
    assert atlanta_observation.observed_high_c == 29.4
    assert atlanta_observation.observed_low_c == 21.1
    assert len(calls) == 1
    requested_ids = set(calls[0]["params"]["ids"].split(","))
    assert {"KLGA", "KATL", "RKSI"}.issubset(requested_ids)


def test_aviationweather_bulk_request_includes_new_verified_station_ids():
    provider, _calls = provider_for([])

    station_ids = set(provider._awc_metar_bulk_station_ids())

    assert {"KAUS", "KBKF", "KHOU", "WMKK", "VILK", "MMMX", "KSFO", "SBGR"}.issubset(station_ids)


def test_aviationweather_provider_accepts_matching_new_station_and_rejects_other_station():
    payload = [
        {
            "icaoId": "KAUS",
            "obsTime": "2026-06-19T20:00:00.000Z",
            "temp": 35.6,
            "rawOb": "KAUS 192000Z 17010KT 10SM FEW060 36/22 A2990",
        },
        {
            "icaoId": "KHOU",
            "obsTime": "2026-06-19T20:00:00.000Z",
            "temp": 34.4,
            "rawOb": "KHOU 192000Z 18008KT 10SM FEW050 34/24 A2991",
        },
    ]
    provider, _calls = provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["austin"],
        target_date=date(2026, 6, 19),
        now=datetime(2026, 6, 19, 20, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.observed_high_c == 35.6
    assert observation.raw_observation_count == 1


def test_aviationweather_provider_rejects_bulk_row_without_station_id():
    payload = [
        {
            "obsTime": "2026-06-02T18:00:00.000Z",
            "temp": 25.6,
            "rawOb": "KLGA 021800Z 22009KT 10SM FEW050 26/14 A2991",
        }
    ]
    provider, _calls = provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["nyc"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 18, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_low_c is None
    assert observation.unavailable_reason == "malformed-observation-payload"


def test_aviationweather_request_log_records_external_fetch_not_cache_hit(tmp_path):
    request_log_path = tmp_path / "station_nowcast_request_log.jsonl"
    provider, calls = provider_for(
        load_fixture("aviationweather_rksi_fresh.json"),
        cache_ttl_seconds=900,
        request_log_path=request_log_path,
    )

    provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )
    provider.observed_low_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 31, tzinfo=timezone.utc),
    )

    rows = read_jsonl(request_log_path)

    assert len(calls) == 1
    assert len(rows) == 1
    assert rows[0]["city"] == "bulk-metar"
    assert rows[0]["station_id"] == "METAR_BULK"
    assert rows[0]["station_name"] == "Aviation Weather Center METAR bulk prefetch"
    assert rows[0]["request_mode"] == "awc_metar_bulk_cache"
    assert rows[0]["trigger_city"] == "seoul"
    assert rows[0]["trigger_station_id"] == STATION_MAP["seoul"].station_id
    assert STATION_MAP["seoul"].station_id in rows[0]["requested_station_ids"]
    assert rows[0]["source"] == "aviationweather-metar"
    assert rows[0]["status"] == "success"
    assert rows[0]["status_code"] == 200
    assert rows[0]["cache_miss_reason"] == "empty-cache"
    assert rows[0]["requested_at"] == "2026-06-02T08:30:00+00:00"


def test_aviationweather_bulk_request_floor_is_one_minute_even_when_station_cache_is_short(tmp_path):
    request_log_path = tmp_path / "station_nowcast_request_log.jsonl"
    provider, calls = provider_for(
        load_fixture("aviationweather_rksi_fresh.json"),
        cache_ttl_seconds=60,
        request_log_path=request_log_path,
    )

    provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )
    provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, 30, tzinfo=timezone.utc),
    )
    provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 31, 1, tzinfo=timezone.utc),
    )

    rows = read_jsonl(request_log_path)
    assert len(calls) == 2
    assert len(rows) == 2
    assert rows[0]["requested_at"] == "2026-06-02T08:30:00+00:00"
    assert rows[1]["requested_at"] == "2026-06-02T08:31:01+00:00"


def test_aviationweather_provider_supports_verified_icao_station_beyond_seoul():
    provider, calls = provider_for(load_fixture("aviationweather_klga_fresh.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["nyc"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 18, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.station_id == "KLGA"
    assert observation.observed_high_c == 25.6
    assert observation.source == "aviationweather-metar"
    requested_ids = set(calls[0]["params"]["ids"].split(","))
    assert "KLGA" in requested_ids


def test_aviationweather_provider_marks_stale_observations_unusable():
    provider, _calls = provider_for(load_fixture("aviationweather_rksi_stale.json"), freshness_seconds=3600)

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c == 24.0
    assert observation.freshness_seconds == 10800
    assert observation.unavailable_reason == "stale-observation"


def test_aviationweather_provider_marks_malformed_payload_unusable():
    provider, _calls = provider_for(load_fixture("aviationweather_rksi_malformed.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_at is None
    assert observation.unavailable_reason == "malformed-observation-payload"


def test_aviationweather_provider_fails_closed_on_future_observation():
    payload = [
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-06T15:40:00.000Z",
            "temp": 31.0,
            "rawOb": "RJTT 061540Z 19008KT 9999 FEW025 31/20 Q1008",
        }
    ]
    provider, _calls = provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 7),
        now=datetime(2026, 6, 6, 15, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_low_c is None
    assert observation.unavailable_reason == "future-observation"


def test_aviationweather_provider_fails_closed_when_target_date_is_not_today():
    provider, calls = provider_for(load_fixture("aviationweather_rksi_fresh.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"],
        target_date=date(2026, 6, 3),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.unavailable_reason == "target-date-not-today"
    assert calls == []


def test_aviationweather_provider_uses_fresh_yesterday_extremes_after_local_midnight():
    payload = [
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-05T15:10:00.000Z",
            "temp": 20.0,
            "rawOb": "RJTT 051510Z 18005KT 9999 FEW020 20/16 Q1010",
        },
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-06T06:00:00.000Z",
            "temp": 31.0,
            "rawOb": "RJTT 060600Z 19008KT 9999 FEW025 31/20 Q1008",
        },
        {
            "icaoId": "RJTT",
            "obsTime": "2026-06-06T14:50:00.000Z",
            "temp": 26.0,
            "rawOb": "RJTT 061450Z 16005KT 9999 FEW020 26/19 Q1009",
        },
    ]
    provider, calls = provider_for(payload, freshness_seconds=5400)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 15, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.station_id == "RJTT"
    assert observation.observed_high_c == 31.0
    assert observation.observed_low_c == 20.0
    assert observation.observed_at.isoformat() == "2026-06-06T14:50:00+00:00"
    assert observation.freshness_seconds == 2400
    assert observation.unavailable_reason == ""
    assert calls[0]["params"]["hours"] == 4
    assert "hoursBeforeNow" not in calls[0]["params"]


def test_aviationweather_provider_blocks_a_response_at_the_400_row_limit():
    record = {
        "icaoId": "RJTT",
        "obsTime": "2026-06-06T06:00:00.000Z",
        "temp": 31.0,
        "rawOb": "RJTT 060600Z 19008KT 9999 FEW025 31/20 Q1008",
    }
    provider, _calls = provider_for([dict(record, receiptTime=index) for index in range(400)])

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 6, 5, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.unavailable_reason == "metar-response-row-limit"


def test_aviationweather_provider_fails_closed_when_yesterday_window_expired():
    provider, calls = provider_for([], freshness_seconds=3600)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 16, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_low_c is None
    assert observation.unavailable_reason == "target-date-post-close-window-expired"
    assert calls == []


def test_aviationweather_provider_skips_station_without_verified_observation_source():
    provider, calls = provider_for(load_fixture("aviationweather_rksi_fresh.json"))

    observation = provider.observed_high_so_far(
        STATION_MAP["karachi"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 8, 30, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.unavailable_reason == "nowcast-source-unmapped"
    assert calls == []


def hko_provider_for(
    payload: str,
    *,
    freshness_seconds: int = 5400,
    cache_ttl_seconds: int = 0,
    request_log_path: Path | None = None,
):
    calls = []

    def fake_get(url, *, params=None, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        return FakeResponse(payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        freshness_seconds=freshness_seconds,
        cache_ttl_seconds=cache_ttl_seconds,
        request_log_path=request_log_path,
    )
    return provider, calls


def hko_sequence_provider(payloads: list[str], *, state_path: Path):
    calls = []
    remaining = iter(payloads)

    def fake_get(url, *, params=None, timeout, headers):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        return FakeResponse(next(remaining))

    return AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        freshness_seconds=5400,
        cache_ttl_seconds=0,
        hko_rollover_state_path=state_path,
    ), calls


def hko_csv(timestamp: str, high_c: float, low_c: float) -> str:
    return (
        "Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),"
        "Minimum Air Temperature Since Midnight(degree Celsius)\n"
        f"{timestamp},HK Observatory,{high_c},{low_c}\n"
    )


def test_hko_provider_returns_max_temperature_since_midnight_from_fixture():
    provider, calls = hko_provider_for(load_text_fixture("hko_maxmin_fresh.csv"))

    observation = provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.station_id == "HKO"
    assert observation.observed_high_c == 30.0
    assert observation.observed_at.isoformat() == "2026-06-02T03:30:00+00:00"
    assert observation.high_observed_at is None
    assert observation.observed_low_c == 27.6
    assert observation.freshness_seconds == 900
    assert observation.source == "hko-maxmin-since-midnight"
    assert "latest_since_midnight_maxmin.csv" in calls[0]["url"]


def test_hko_observation_older_than_two_update_cycles_is_stale():
    provider, _calls = hko_provider_for(
        hko_csv("202606021130", 30.0, 27.6),
        freshness_seconds=5400,
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 51, tzinfo=timezone.utc),
    )

    assert observation.freshness_seconds == 1260
    assert observation.usable is False
    assert observation.unavailable_reason == "stale-observation"


def test_hko_midnight_carryover_331_is_blocked_until_292_reset_is_proven(tmp_path):
    provider, calls = hko_sequence_provider(
        [
            hko_csv("202606212350", 33.1, 28.0),
            hko_csv("202606220000", 33.1, 28.0),
            hko_csv("202606220330", 29.2, 28.6),
        ],
        state_path=tmp_path / "hko_rollover_state.json",
    )

    previous = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 21),
        now=datetime(2026, 6, 21, 15, 55, tzinfo=timezone.utc),
    )
    carryover = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 21, 16, 10, tzinfo=timezone.utc),
    )
    reset = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 21, 19, 35, tzinfo=timezone.utc),
    )

    assert previous.usable is False
    assert previous.unavailable_reason == "hko-rollover-baseline-missing"
    assert carryover.usable is False
    assert carryover.observed_high_c == 33.1
    assert carryover.unavailable_reason == "hko-midnight-reset-pending"
    assert carryover.midnight_reset_status == "pending_previous_day_match"
    assert reset.usable is True
    assert reset.observed_high_c == 29.2
    assert reset.observed_low_c == 28.6
    assert reset.midnight_reset_status == "verified"
    assert len(calls) == 3


def test_hko_records_when_current_daily_extremes_were_first_confirmed(tmp_path):
    state_path = tmp_path / "hko_rollover_state.json"
    provider, _calls = hko_sequence_provider(
        [
            hko_csv("202606212350", 33.1, 28.0),
            hko_csv("202606220330", 29.2, 28.6),
            hko_csv("202606221200", 31.0, 28.6),
            hko_csv("202606221400", 31.0, 27.9),
        ],
        state_path=state_path,
    )
    station = STATION_MAP["hong kong"]
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 21), now=datetime(2026, 6, 21, 15, 55, tzinfo=timezone.utc)
    )
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 21, 19, 35, tzinfo=timezone.utc)
    )
    high_update = provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 22, 4, 5, tzinfo=timezone.utc)
    )
    low_update = provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 22, 6, 5, tzinfo=timezone.utc)
    )

    assert high_update.high_observed_at.isoformat() == "2026-06-22T04:00:00+00:00"
    assert high_update.low_observed_at.isoformat() == "2026-06-21T19:30:00+00:00"
    assert low_update.high_observed_at.isoformat() == "2026-06-22T04:00:00+00:00"
    assert low_update.low_observed_at.isoformat() == "2026-06-22T06:00:00+00:00"
    day = json.loads(state_path.read_text(encoding="utf-8"))["days"]["2026-06-22"]
    assert day == {
        "high_c": 31.0,
        "high_first_observed_at": "2026-06-22T04:00:00+00:00",
        "latest_observed_at": "2026-06-22T06:00:00+00:00",
        "low_c": 27.9,
        "low_first_observed_at": "2026-06-22T06:00:00+00:00",
    }


def test_hko_keeps_only_the_latest_two_local_dates(tmp_path):
    state_path = tmp_path / "hko_rollover_state.json"
    provider, _calls = hko_sequence_provider(
        [
            hko_csv("202606212350", 33.1, 28.0),
            hko_csv("202606220330", 29.2, 28.6),
            hko_csv("202606230330", 29.0, 28.8),
            hko_csv("202606240330", 28.5, 29.0),
        ],
        state_path=state_path,
    )
    station = STATION_MAP["hong kong"]
    for target_date, now in [
        (date(2026, 6, 21), datetime(2026, 6, 21, 15, 55, tzinfo=timezone.utc)),
        (date(2026, 6, 22), datetime(2026, 6, 21, 19, 35, tzinfo=timezone.utc)),
        (date(2026, 6, 23), datetime(2026, 6, 22, 19, 35, tzinfo=timezone.utc)),
        (date(2026, 6, 24), datetime(2026, 6, 23, 19, 35, tzinfo=timezone.utc)),
    ]:
        provider.observed_temperature_extremes_so_far(station, target_date=target_date, now=now)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert sorted(state["days"]) == ["2026-06-23", "2026-06-24"]


def test_hko_restart_without_previous_day_baseline_fails_closed(tmp_path):
    provider, _calls = hko_sequence_provider(
        [hko_csv("202606220330", 29.2, 28.6)],
        state_path=tmp_path / "missing" / "hko_rollover_state.json",
    )

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 22),
        now=datetime(2026, 6, 21, 19, 35, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.unavailable_reason == "hko-rollover-baseline-missing"
    assert observation.midnight_reset_status == "blocked_baseline_missing"


def test_hko_same_day_high_decrease_is_blocked_after_reset(tmp_path):
    provider, _calls = hko_sequence_provider(
        [
            hko_csv("202606212350", 33.1, 28.0),
            hko_csv("202606220330", 29.2, 28.6),
            hko_csv("202606220400", 29.0, 28.5),
        ],
        state_path=tmp_path / "hko_rollover_state.json",
    )
    station = STATION_MAP["hong kong"]
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 21), now=datetime(2026, 6, 21, 15, 55, tzinfo=timezone.utc)
    )
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 21, 19, 35, tzinfo=timezone.utc)
    )
    observation = provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 21, 20, 5, tzinfo=timezone.utc)
    )

    assert observation.usable is False
    assert observation.unavailable_reason == "hko-same-day-high-decreased"


def test_hko_same_day_low_increase_is_blocked_after_reset(tmp_path):
    provider, _calls = hko_sequence_provider(
        [
            hko_csv("202606212350", 33.1, 28.0),
            hko_csv("202606220330", 29.2, 28.6),
            hko_csv("202606220400", 29.3, 28.7),
        ],
        state_path=tmp_path / "hko_rollover_state.json",
    )
    station = STATION_MAP["hong kong"]
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 21), now=datetime(2026, 6, 21, 15, 55, tzinfo=timezone.utc)
    )
    provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 21, 19, 35, tzinfo=timezone.utc)
    )
    observation = provider.observed_temperature_extremes_so_far(
        station, target_date=date(2026, 6, 22), now=datetime(2026, 6, 21, 20, 5, tzinfo=timezone.utc)
    )

    assert observation.usable is False
    assert observation.unavailable_reason == "hko-same-day-low-increased"


def test_hko_provider_uses_fresh_yesterday_extremes_after_local_midnight():
    payload = """Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),Minimum Air Temperature Since Midnight(degree Celsius)
202606062350,HK Observatory,30.2,27.1
"""
    provider, calls = hko_provider_for(payload, freshness_seconds=5400)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 16, 5, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.station_id == "HKO"
    assert observation.observed_high_c == 30.2
    assert observation.observed_low_c == 27.1
    assert observation.observed_at.isoformat() == "2026-06-06T15:50:00+00:00"
    assert observation.freshness_seconds == 900
    assert observation.unavailable_reason == ""
    assert len(calls) == 1


def test_hko_request_log_records_external_fetch_not_cache_hit(tmp_path):
    request_log_path = tmp_path / "station_nowcast_request_log.jsonl"
    provider, calls = hko_provider_for(
        load_text_fixture("hko_maxmin_fresh.csv"),
        cache_ttl_seconds=900,
        request_log_path=request_log_path,
    )

    provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
    )
    provider.observed_low_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 46, tzinfo=timezone.utc),
    )

    rows = read_jsonl(request_log_path)

    assert len(calls) == 1
    assert len(rows) == 1
    assert rows[0]["city"] == "hong kong"
    assert rows[0]["station_id"] == STATION_MAP["hong kong"].station_id
    assert rows[0]["source"] == "hko-maxmin-since-midnight"
    assert rows[0]["status"] == "success"
    assert rows[0]["status_code"] == 200
    assert rows[0]["cache_miss_reason"] == "empty-cache"
    assert rows[0]["requested_at"] == "2026-06-02T03:45:00+00:00"


def test_hko_request_floor_is_ten_minutes_even_when_station_cache_is_short(tmp_path):
    request_log_path = tmp_path / "station_nowcast_request_log.jsonl"
    provider, calls = hko_provider_for(
        load_text_fixture("hko_maxmin_fresh.csv"),
        cache_ttl_seconds=60,
        request_log_path=request_log_path,
    )

    provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
    )
    provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 50, tzinfo=timezone.utc),
    )
    provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 56, tzinfo=timezone.utc),
    )

    rows = read_jsonl(request_log_path)
    assert len(calls) == 2
    assert len(rows) == 2
    assert rows[0]["requested_at"] == "2026-06-02T03:45:00+00:00"
    assert rows[1]["requested_at"] == "2026-06-02T03:56:00+00:00"


def test_hko_provider_marks_malformed_csv_unusable():
    provider, _calls = hko_provider_for(load_text_fixture("hko_maxmin_malformed.csv"))

    observation = provider.observed_high_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 2),
        now=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.unavailable_reason == "malformed-observation-payload"


def test_hko_provider_fails_closed_on_future_observation():
    payload = """Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),Minimum Air Temperature Since Midnight(degree Celsius)
202606062350,HK Observatory,30.2,27.1
"""
    provider, _calls = hko_provider_for(payload)

    observation = provider.observed_temperature_extremes_so_far(
        STATION_MAP["hong kong"],
        target_date=date(2026, 6, 6),
        now=datetime(2026, 6, 6, 15, 20, tzinfo=timezone.utc),
    )

    assert observation.usable is False
    assert observation.observed_high_c is None
    assert observation.observed_low_c is None
    assert observation.unavailable_reason == "future-observation"


def test_station_nowcast_audit_marks_enabled_and_skipped_sources():
    rows = {row["city"]: row for row in station_audit_rows()}

    assert rows["hong kong"]["nowcast_source_type"] == "hko_maxmin_since_midnight"
    assert rows["hong kong"]["nowcast_provider_status"] == "provider_enabled"
    assert rows["seoul"]["nowcast_provider_status"] == "provider_enabled"
    assert rows["nyc"]["nowcast_provider_status"] == "provider_enabled"
    assert rows["karachi"]["nowcast_provider_status"] == "provider_unavailable"


def test_default_nowcast_sources_match_enabled_station_registry():
    enabled_station_ids = {
        str(row["station_id"])
        for row in station_audit_rows()
        if row["nowcast_provider_status"] == "provider_enabled"
    }

    assert set(DEFAULT_NOWCAST_SOURCES) == enabled_station_ids
    assert "HKO" in DEFAULT_NOWCAST_SOURCES
    assert "OPMR" not in DEFAULT_NOWCAST_SOURCES
