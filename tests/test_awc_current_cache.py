import gzip
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import weather_bot.awc_current_cache as awc_cache_module
from weather_bot.awc_current_cache import parse_awc_current_metars
from weather_bot.nowcast import AviationWeatherMetarNowcastProvider
from weather_bot.stations import STATION_MAP


class _CacheResponse:
    status_code = 200

    def __init__(self, content: bytes, *, last_modified: str) -> None:
        self.content = content
        self.headers = {"Last-Modified": last_modified}

    def raise_for_status(self) -> None:
        return None


class _JsonResponse:
    status_code = 200

    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _HtmlResponse:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_parse_awc_current_metars_filters_and_validates_station_rows():
    csv_text = """raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string
\"METAR RKSI 211530Z 29003KT 7000 OVC002 23/23 Q1008\",RKSI,2026-07-21T15:30:00.000Z,23,23,BR
\"METAR RKPK 211500Z 18002KT 9999 SCT020 27/25 Q1009\",RKPK,2026-07-21T15:00:00.000Z,27,25,
\"METAR ZSPD 211530Z 18003MPS CAVOK 26/26 Q1007\",ZSPD,2026-07-21T15:30:00.000Z,26,26,
\"METAR RKPK 211530Z 18002KT CAVOK 28/25 Q1009\",RKSI,2026-07-21T15:30:00.000Z,28,25,
"""

    rows = parse_awc_current_metars(
        gzip.compress(csv_text.encode("utf-8")),
        station_ids={"RKSI", "RKPK"},
    )

    assert rows == [
        {
            "icaoId": "RKSI",
            "obsTime": "2026-07-21T15:30:00.000Z",
            "temp": 23.0,
            "dewp": 23.0,
            "rawOb": "METAR RKSI 211530Z 29003KT 7000 OVC002 23/23 Q1008",
            "wxString": "BR",
        },
        {
            "icaoId": "RKPK",
            "obsTime": "2026-07-21T15:00:00.000Z",
            "temp": 27.0,
            "dewp": 25.0,
            "rawOb": "METAR RKPK 211500Z 18002KT 9999 SCT020 27/25 Q1009",
            "wxString": "",
        },
    ]


def test_parse_awc_current_metars_fails_closed_on_bad_gzip_or_temperature():
    bad_temperature = gzip.compress(
        b"raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string\n"
        b"METAR RKSI 211530Z,RKSI,2026-07-21T15:30:00Z,999,,\n"
    )

    assert parse_awc_current_metars(b"not-gzip", station_ids={"RKSI"}) == []
    assert parse_awc_current_metars(bad_temperature, station_ids={"RKSI"}) == []


def test_parse_awc_current_metars_rejects_oversized_expansion(monkeypatch):
    monkeypatch.setattr(
        awc_cache_module,
        "AWC_CURRENT_CACHE_MAX_DECOMPRESSED_BYTES",
        32,
    )

    assert parse_awc_current_metars(
        gzip.compress(b"x" * 64),
        station_ids={"RKSI"},
    ) == []


def test_direct_current_groups_all_stations_and_respects_one_minute_floor(tmp_path):
    clock = [datetime(2026, 8, 1, 17, 0, 5, tzinfo=timezone.utc)]
    calls = []

    def fake_get(url, *, params, timeout, headers):
        calls.append((url, params, timeout, headers))
        return _JsonResponse(
            [
                {
                    "icaoId": "RJTT",
                    "obsTime": 1785603600,
                    "temp": 29,
                    "rawOb": "METAR RJTT 011700Z 20003KT CAVOK 29/27 Q1003",
                },
                {
                    "icaoId": "ZSPD",
                    "obsTime": 1785603600,
                    "temp": 30,
                    "rawOb": "METAR ZSPD 011700Z 11002MPS CAVOK 30/26 Q1007",
                },
            ]
        )

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        awc_direct_current_enabled=True,
        request_log_path=tmp_path / "requests.jsonl",
        clock=lambda: clock[0],
    )

    def fetch_current(_index):
        return provider._fetch_awc_direct_current(
            clock[0],
            trigger_station=STATION_MAP["tokyo"],
            target_date=date(2026, 8, 2),
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        concurrent_entries = list(executor.map(fetch_current, range(4)))
    first = concurrent_entries[0]
    clock[0] += timedelta(seconds=59)
    second = provider._fetch_awc_direct_current(clock[0])

    assert all(entry is first for entry in concurrent_entries)
    assert first is second
    assert len(calls) == 1
    assert calls[0][0].endswith("/api/data/metar")
    assert calls[0][1]["format"] == "json"
    assert calls[0][1]["hours"] == 1
    assert "RJTT" in calls[0][1]["ids"]
    assert "ZSPD" in calls[0][1]["ids"]
    assert first.payload[1]["temp"] == 30
    assert provider._awc_direct_current_next_request_at == datetime(
        2026, 8, 1, 17, 1, 5, tzinfo=timezone.utc
    )

    clock[0] += timedelta(seconds=1)
    third = provider._fetch_awc_direct_current(clock[0])

    assert third is not first
    assert len(calls) == 2
    request_row = json.loads(
        (tmp_path / "requests.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert request_row["returned_station_count"] == 2
    assert request_row["returned_station_ids"] == ["RJTT", "ZSPD"]
    assert "RKSI" in request_row["missing_station_ids"]


def test_direct_current_failure_or_missing_station_falls_back_to_current_cache():
    csv_text = """raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string
"METAR RJTT 211530Z 18009KT CAVOK 28/26 Q1008",RJTT,2026-07-21T15:30:00Z,28,26,
"""
    compressed = gzip.compress(csv_text.encode("utf-8"))
    now = datetime(2026, 7, 21, 15, 31, tzinfo=timezone.utc)

    for direct_result in ("error", "missing-station"):
        calls: list[str] = []

        def fake_get(url, **kwargs):
            del kwargs
            if url.endswith("/api/data/metar"):
                calls.append("direct")
                if direct_result == "error":
                    raise TimeoutError("direct current timeout")
                return _JsonResponse(
                    [{"icaoId": "ZSPD", "obsTime": "2026-07-21T15:30:00Z", "temp": 30.0}]
                )
            if url.endswith("/data/cache/metars.cache.csv.gz"):
                calls.append("cache")
                return _CacheResponse(
                    compressed,
                    last_modified="Tue, 21 Jul 2026 15:30:30 GMT",
                )
            raise AssertionError(f"unexpected history request: {url}")

        provider = AviationWeatherMetarNowcastProvider(
            http_get=fake_get,
            awc_direct_current_enabled=True,
            awc_current_cache_enabled=True,
            clock=lambda: now,
        )
        station = STATION_MAP["tokyo"]
        provider._accumulate_metar_daily_extremes(
            station,
            [
                (datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc), 27.0),
                (datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc), 27.0),
            ],
            date(2026, 7, 22),
            persist=False,
        )

        observation = provider.observed_high_so_far(
            station,
            target_date=date(2026, 7, 22),
            now=now,
        )
        provider._fetch_awc_direct_current(now + timedelta(seconds=59))

        assert observation.usable is True
        assert observation.latest_temp_c == 28.0
        assert calls == ["direct", "cache"]


def test_provider_uses_phase_aligned_current_cache_before_history_api():
    csv_text = """raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string
\"METAR RJTT 211530Z 18009KT CAVOK 28/26 Q1008\",RJTT,2026-07-21T15:30:00.000Z,28,26,
"""
    compressed = gzip.compress(csv_text.encode("utf-8"))
    clock = [datetime(2026, 7, 21, 15, 41, 43, tzinfo=timezone.utc)]
    calls: list[datetime] = []

    def fake_get(url, *, timeout, headers):
        assert url.endswith("/data/cache/metars.cache.csv.gz")
        assert timeout <= 5.0
        assert headers["User-Agent"]
        calls.append(clock[0])
        return _CacheResponse(
            compressed,
            last_modified="Tue, 21 Jul 2026 15:41:13 GMT",
        )

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        awc_current_cache_enabled=True,
        cache_ttl_seconds=60,
        clock=lambda: clock[0],
    )
    station = STATION_MAP["tokyo"]
    provider._accumulate_metar_daily_extremes(
        station,
        [
            (datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc), 26.0),
            (datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc), 27.0),
        ],
        date(2026, 7, 21),
        persist=False,
    )

    first = provider.observed_high_so_far(
        station,
        target_date=date(2026, 7, 22),
        now=clock[0],
    )

    assert first.source == "aviationweather-metar"
    assert first.latest_temp_c == 28.0
    assert calls == [datetime(2026, 7, 21, 15, 41, 43, tzinfo=timezone.utc)]
    assert provider._awc_current_cache_next_request_at == datetime(
        2026, 7, 21, 15, 42, 13, 500000, tzinfo=timezone.utc
    )

    clock[0] = datetime(2026, 7, 21, 15, 42, 12, tzinfo=timezone.utc)
    provider._fetch_awc_current_cache(clock[0])
    assert len(calls) == 1

    clock[0] = datetime(2026, 7, 21, 15, 42, 14, tzinfo=timezone.utc)
    provider._fetch_awc_current_cache(clock[0])
    assert len(calls) == 2


def test_current_cache_uses_response_clock_when_report_crosses_next_minute():
    csv_text = """raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string
"METAR RJTT 211531Z 28/26",RJTT,2026-07-21T15:31:00Z,28,26,
"""
    actual_clock = [datetime(2026, 7, 21, 15, 30, 59, tzinfo=timezone.utc)]

    def fake_get(*_args, **_kwargs):
        actual_clock[0] = datetime(2026, 7, 21, 15, 31, 2, tzinfo=timezone.utc)
        return _CacheResponse(
            gzip.compress(csv_text.encode("utf-8")),
            last_modified="Tue, 21 Jul 2026 15:31:01 GMT",
        )

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        awc_current_cache_enabled=True,
        clock=lambda: actual_clock[0],
    )
    station = STATION_MAP["tokyo"]
    provider._accumulate_metar_daily_extremes(
        station,
        [
            (datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc), 27.0),
            (datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc), 27.5),
        ],
        date(2026, 7, 22),
        persist=False,
    )

    observation = provider.observed_high_so_far(
        station,
        target_date=date(2026, 7, 22),
        now=datetime(2026, 7, 21, 15, 30, 59, tzinfo=timezone.utc),
    )

    assert observation.usable is True
    assert observation.unavailable_reason == ""
    assert observation.observed_at == datetime(2026, 7, 21, 15, 31, tzinfo=timezone.utc)
    assert observation.bot_received_at == datetime(2026, 7, 21, 15, 31, 2, tzinfo=timezone.utc)


def test_provider_falls_back_to_history_api_when_current_cache_fails():
    now = datetime(2026, 7, 21, 15, 31, tzinfo=timezone.utc)
    urls: list[str] = []

    def fake_get(url, **kwargs):
        del kwargs
        urls.append(url)
        if url.endswith("metars.cache.csv.gz"):
            raise TimeoutError("current cache timeout")
        return _JsonResponse(
            [
                {
                    "icaoId": "RJTT",
                    "obsTime": "2026-07-21T15:30:00Z",
                    "temp": 28.0,
                }
            ]
        )

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        awc_current_cache_enabled=True,
        cache_ttl_seconds=0,
        clock=lambda: now,
    )
    station = STATION_MAP["tokyo"]
    provider._accumulate_metar_daily_extremes(
        station,
        [
            (datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc), 26.0),
            (datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc), 27.0),
        ],
        date(2026, 7, 21),
        persist=False,
    )

    observation = provider.observed_high_so_far(
        station,
        target_date=date(2026, 7, 22),
        now=now,
    )

    assert observation.usable is True
    assert observation.latest_temp_c == 28.0
    assert urls[0].endswith("metars.cache.csv.gz")
    assert urls[1].endswith("/api/data/metar")
    assert provider._awc_current_cache_next_request_at == now + timedelta(seconds=3)


def test_history_rebuilds_cold_start_day_after_latest_cache_row_arrives_first():
    now = datetime(2026, 7, 21, 15, 31, tzinfo=timezone.utc)
    provider = AviationWeatherMetarNowcastProvider(
        http_get=lambda *_args, **_kwargs: None,
        awc_current_cache_enabled=True,
        clock=lambda: now,
    )
    station = STATION_MAP["tokyo"]
    source = provider.sources[station.station_id]
    current_payload = [
        {
            "icaoId": "RJTT",
            "obsTime": "2026-07-21T15:30:00Z",
            "temp": 28.0,
        }
    ]
    history_payload = [
        {
            "icaoId": "RJTT",
            "obsTime": "2026-07-21T14:30:00Z",
            "temp": 27.0,
        },
        {
            "icaoId": "RJTT",
            "obsTime": "2026-07-21T15:00:00Z",
            "temp": 27.5,
        },
        current_payload[0],
    ]

    first = provider._parse_payload(
        current_payload,
        station,
        date(2026, 7, 22),
        now,
        source,
    )
    recovered = provider._parse_payload(
        history_payload,
        station,
        date(2026, 7, 22),
        now,
        source,
    )

    assert first.usable is True
    assert first.daily_extremes_complete is False
    assert first.data_block_reason == "metar-daily-extremes-baseline-missing"
    assert recovered.usable is True
    assert recovered.daily_extremes_complete is True
    assert recovered.data_block_reason == ""
    assert recovered.latest_temp_c == 28.0


def test_provider_requests_history_when_current_cache_cold_start_is_incomplete():
    csv_text = """raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string
"METAR RJTT 211530Z 28/26",RJTT,2026-07-21T15:30:00Z,28,26,
"""
    history_payload = [
        {"icaoId": "RJTT", "obsTime": "2026-07-21T14:30:00Z", "temp": 27.0},
        {"icaoId": "RJTT", "obsTime": "2026-07-21T15:00:00Z", "temp": 27.5},
        {"icaoId": "RJTT", "obsTime": "2026-07-21T15:30:00Z", "temp": 28.0},
    ]
    now = datetime(2026, 7, 21, 15, 31, tzinfo=timezone.utc)
    urls: list[str] = []

    def fake_get(url, **_kwargs):
        urls.append(url)
        if url.endswith("metars.cache.csv.gz"):
            return _CacheResponse(
                gzip.compress(csv_text.encode("utf-8")),
                last_modified="Tue, 21 Jul 2026 15:31:13 GMT",
            )
        return _JsonResponse(history_payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        awc_current_cache_enabled=True,
        cache_ttl_seconds=0,
        clock=lambda: now,
    )

    observation = provider.observed_high_so_far(
        STATION_MAP["tokyo"],
        target_date=date(2026, 7, 22),
        now=now,
    )

    assert urls[0].endswith("metars.cache.csv.gz")
    assert urls[1].endswith("/api/data/metar")
    assert observation.usable is True
    assert observation.daily_extremes_complete is True
    assert observation.data_block_reason == ""
    assert observation.latest_temp_c == 28.0


def test_provider_recovers_history_when_current_cache_omits_kma_station():
    current_csv = """raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string
"METAR RJTT 211530Z 28/26",RJTT,2026-07-21T15:30:00Z,28,26,
"""
    history_payload = [
        {"icaoId": "RKPK", "obsTime": "2026-07-21T14:30:00Z", "temp": 24.0},
        {"icaoId": "RKPK", "obsTime": "2026-07-21T15:00:00Z", "temp": 25.0},
        {"icaoId": "RKPK", "obsTime": "2026-07-21T15:30:00Z", "temp": 27.0},
    ]
    now = datetime(2026, 7, 21, 15, 31, 20, tzinfo=timezone.utc)
    get_urls: list[str] = []

    def fake_get(url, **_kwargs):
        get_urls.append(url)
        if "global.amo.go.kr" in url:
            return _HtmlResponse(
                """
                <table><thead><tr>
                  <th>공항명</th><th>UTC</th><th>KST</th><th>종류</th>
                  <th>관측시각<br>(UTC)</th><th>풍향<br>(˚)</th><th>풍속/G<br>(kt)</th>
                  <th>시정<br>(m)</th><th>일기현상</th><th>구름(운량 okta/운고 100ft)</th>
                  <th>기온<br>(˚C)</th><th>기압<br>(hPa)</th><th>강수량<br>(㎜)</th><th>신적설<br>(㎝)</th>
                </tr></thead><tbody>
                  <tr><td>Busan(RKPK)</td><td>15:31</td><td>00:31</td><td>METAR</td>
                  <td>211531Z</td><td></td><td></td><td></td><td></td><td></td>
                  <td></td><td></td><td></td><td></td><td>25.1</td><td></td><td></td><td></td></tr>
                </tbody></table>
                """
            )
        if url.endswith("metars.cache.csv.gz"):
            return _CacheResponse(
                gzip.compress(current_csv.encode("utf-8")),
                last_modified="Tue, 21 Jul 2026 15:31:13 GMT",
            )
        return _JsonResponse(history_payload)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        awc_current_cache_enabled=True,
        kma_public_html_enabled=True,
        kma_metar_station_ids={"RKPK"},
        cache_ttl_seconds=0,
        clock=lambda: now,
    )

    observation = provider.observed_high_so_far(
        STATION_MAP["busan"],
        target_date=date(2026, 7, 22),
        now=now,
    )

    assert any("global.amo.go.kr" in url for url in get_urls)
    assert any(url.endswith("/api/data/metar") for url in get_urls)
    assert observation.source == "aviationweather-metar"
    assert observation.daily_extremes_complete is True
    assert observation.data_block_reason == ""
    assert observation.latest_temp_c == 27.0


def test_unchanged_last_modified_retries_after_three_seconds_then_realigns():
    csv_text = """raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string
"METAR RJTT 211530Z 28/26",RJTT,2026-07-21T15:30:00Z,28,26,
"""
    compressed = gzip.compress(csv_text.encode("utf-8"))
    clock = [datetime(2026, 7, 21, 15, 41, 43, tzinfo=timezone.utc)]
    last_modified = [
        "Tue, 21 Jul 2026 15:41:13 GMT",
        "Tue, 21 Jul 2026 15:41:13 GMT",
        "Tue, 21 Jul 2026 15:42:13 GMT",
    ]

    def fake_get(url, *, timeout, headers):
        del url, timeout, headers
        return _CacheResponse(compressed, last_modified=last_modified.pop(0))

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        awc_current_cache_enabled=True,
        clock=lambda: clock[0],
    )
    provider._fetch_awc_current_cache(clock[0])
    clock[0] = datetime(2026, 7, 21, 15, 42, 14, tzinfo=timezone.utc)
    provider._fetch_awc_current_cache(clock[0])

    assert provider._awc_current_cache_next_request_at == clock[0] + timedelta(seconds=3)

    clock[0] += timedelta(seconds=2)
    provider._fetch_awc_current_cache(clock[0])
    assert len(last_modified) == 1

    clock[0] += timedelta(seconds=1)
    provider._fetch_awc_current_cache(clock[0])
    assert provider._awc_current_cache_next_request_at == datetime(
        2026, 7, 21, 15, 43, 13, 500000, tzinfo=timezone.utc
    )


def test_implausibly_old_last_modified_is_rejected_without_minute_loop():
    csv_text = """raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string
"METAR RJTT 211530Z 28/26",RJTT,2026-07-21T15:30:00Z,28,26,
"""
    now = datetime(2026, 7, 21, 15, 41, 43, tzinfo=timezone.utc)
    provider = AviationWeatherMetarNowcastProvider(
        http_get=lambda *_args, **_kwargs: _CacheResponse(
            gzip.compress(csv_text.encode("utf-8")),
            last_modified="Thu, 01 Jan 1970 00:00:00 GMT",
        ),
        awc_current_cache_enabled=True,
        clock=lambda: now,
    )

    entry = provider._fetch_awc_current_cache(now)

    assert entry.unavailable_reason == ""
    assert provider._awc_current_cache_last_modified is None
    assert provider._awc_current_cache_next_request_at == now + timedelta(seconds=3)


def test_new_current_cache_generation_invalidates_all_awc_station_observations():
    csv_text = """raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string
"METAR RJTT 211530Z 28/26",RJTT,2026-07-21T15:30:00Z,28,26,
"METAR RKSI 211530Z 23/22",RKSI,2026-07-21T15:30:00Z,23,22,
"""
    compressed = gzip.compress(csv_text.encode("utf-8"))
    clock = [datetime(2026, 7, 21, 15, 41, 43, tzinfo=timezone.utc)]
    generations = [
        "Tue, 21 Jul 2026 15:41:13 GMT",
        "Tue, 21 Jul 2026 15:42:13 GMT",
    ]

    def fake_get(url, *, timeout, headers):
        del url, timeout, headers
        return _CacheResponse(compressed, last_modified=generations.pop(0))

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        awc_current_cache_enabled=True,
        clock=lambda: clock[0],
    )
    provider._fetch_awc_current_cache(clock[0])
    for city in ("tokyo", "seoul"):
        station = STATION_MAP[city]
        source = provider.sources[station.station_id]
        if city == "seoul":
            source = replace(source, source="kma-official-public-metars")
        provider._cache[(station.station_id, "2026-07-22")] = (
            clock[0],
            provider._unavailable(station, "seed", source),
        )

    clock[0] = datetime(2026, 7, 21, 15, 42, 14, tzinfo=timezone.utc)
    provider._fetch_awc_current_cache(clock[0])

    assert provider._cache == {}


def test_concurrent_cities_share_one_current_cache_download():
    csv_text = """raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string
"METAR RJTT 211530Z 28/26",RJTT,2026-07-21T15:30:00Z,28,26,
"METAR RKSI 211530Z 23/22",RKSI,2026-07-21T15:30:00Z,23,22,
"""
    compressed = gzip.compress(csv_text.encode("utf-8"))
    clock = datetime(2026, 7, 21, 15, 41, 43, tzinfo=timezone.utc)
    simultaneous_requests = threading.Barrier(2)
    calls: list[int] = []

    def fake_get(url, *, timeout, headers):
        del url, timeout, headers
        calls.append(1)
        try:
            simultaneous_requests.wait(timeout=0.1)
        except threading.BrokenBarrierError:
            pass
        return _CacheResponse(
            compressed,
            last_modified="Tue, 21 Jul 2026 15:41:13 GMT",
        )

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        awc_current_cache_enabled=True,
        clock=lambda: clock,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(provider._fetch_awc_current_cache, [clock, clock]))

    assert len(calls) == 1
    assert results[0] is results[1]


def test_current_cache_mode_allows_independent_station_refreshes():
    provider = AviationWeatherMetarNowcastProvider(
        http_get=lambda *_args, **_kwargs: None,
        awc_current_cache_enabled=True,
    )

    seoul_lock = provider._observation_lock_for(STATION_MAP["seoul"])
    busan_lock = provider._observation_lock_for(STATION_MAP["busan"])

    assert provider.supports_parallel_station_refresh is True
    assert seoul_lock is not busan_lock


def test_current_cache_mode_runs_seoul_and_busan_kma_requests_concurrently():
    csv_text = """raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string
"METAR RKSI 211530Z 23/22",RKSI,2026-07-21T15:30:00Z,23,22,
"METAR RKPK 211530Z 27/25",RKPK,2026-07-21T15:30:00Z,27,25,
"""
    compressed = gzip.compress(csv_text.encode("utf-8"))
    now = datetime(2026, 7, 21, 15, 31, 20, tzinfo=timezone.utc)
    barrier = threading.Barrier(3)
    counter_lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_get(url, *, params=None, **_kwargs):
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            barrier.wait(timeout=2)
            if "global.amo.go.kr" in url:
                station_id = params["stnCd"]
                return _HtmlResponse(
                    f"""
                    <table><thead><tr>
                      <th>공항명</th><th>UTC</th><th>KST</th><th>종류</th>
                      <th>관측시각<br>(UTC)</th><th>풍향<br>(˚)</th><th>풍속/G<br>(kt)</th>
                      <th>시정<br>(m)</th><th>일기현상</th><th>구름(운량 okta/운고 100ft)</th>
                      <th>기온<br>(˚C)</th><th>기압<br>(hPa)</th><th>강수량<br>(㎜)</th><th>신적설<br>(㎝)</th>
                    </tr></thead><tbody>
                      <tr><td>Airport({station_id})</td><td>15:31</td><td>00:31</td><td>METAR</td>
                      <td>211531Z</td><td></td><td></td><td></td><td></td><td></td>
                      <td></td><td></td><td></td><td></td><td>25.1</td><td></td><td></td><td></td></tr>
                    </tbody></table>
                    """
                )
            return _CacheResponse(
                compressed,
                last_modified="Tue, 21 Jul 2026 15:31:13 GMT",
            )
        finally:
            with counter_lock:
                active -= 1

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        awc_current_cache_enabled=True,
        kma_public_html_enabled=True,
        kma_metar_station_ids={"RKSI", "RKPK"},
        clock=lambda: now,
    )
    for station in (STATION_MAP["seoul"], STATION_MAP["busan"]):
        baseline = [
            (datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc), 24.0),
            (datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc), 24.5),
        ]
        provider._accumulate_metar_daily_extremes(
            station,
            baseline,
            date(2026, 7, 22),
            persist=False,
        )
        provider._accumulate_metar_daily_extremes(
            station,
            baseline,
            date(2026, 7, 22),
            persist=False,
            source_name="kma-official-public-metars",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        observations = list(
            pool.map(
                lambda station: provider.observed_high_so_far(
                    station,
                    target_date=date(2026, 7, 22),
                    now=now,
                ),
                (STATION_MAP["seoul"], STATION_MAP["busan"]),
            )
        )

    assert max_active == 3
    assert {observation.station_id for observation in observations} == {"RKSI", "RKPK"}
