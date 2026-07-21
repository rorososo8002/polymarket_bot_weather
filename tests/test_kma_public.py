import gzip
from datetime import date, datetime, timezone

from weather_bot.kma_public import parse_kma_public_metar_html
from weather_bot.nowcast import AviationWeatherMetarNowcastProvider
from weather_bot.stations import STATION_MAP


_KMA_HEADER_HTML = """
<thead><tr>
  <th>공항명</th><th>UTC</th><th>KST</th><th>종류</th>
  <th>관측시각<br>(UTC)</th><th>풍향<br>(˚)</th><th>풍속/G<br>(kt)</th>
  <th>시정<br>(m)</th><th>일기현상</th><th>구름(운량 okta/운고 100ft)</th>
  <th>기온<br>(˚C)</th><th>기압<br>(hPa)</th><th>강수량<br>(㎜)</th>
  <th>신적설<br>(㎝)</th>
</tr></thead>
"""


class _HtmlResponse:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text

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


class _CurrentCacheResponse:
    status_code = 200

    def __init__(self, csv_text: str) -> None:
        self.content = gzip.compress(csv_text.encode("utf-8"))
        self.headers = {"Last-Modified": "Tue, 21 Jul 2026 15:30:10 GMT"}

    def raise_for_status(self) -> None:
        return None


def test_parse_kma_public_metar_html_keeps_precise_station_temperatures():
    html = f"""
    <table>
      {_KMA_HEADER_HTML}
      <tbody>
        <tr>
          <td>인천(RKSI)</td><td>02:00</td><td>11:00</td><td>METARSCIAL</td>
          <td>190200Z</td><td>220</td><td>4/-</td><td>10,000</td><td></td>
          <td>5</td><td>3/010</td><td>5/025</td><td>-/-</td><td>-/-</td>
          <td>26.5</td><td>1006.2</td><td></td><td></td>
        </tr>
        <tr>
          <td>인천(RKSI)</td><td>02:30</td><td>11:30</td><td>METAR</td>
          <td>190230Z</td><td>230</td><td>5/-</td><td>10,000</td><td>RA</td>
          <td>6</td><td>4/010</td><td>6/025</td><td>-/-</td><td>-/-</td>
          <td>25.9</td><td>1006.0</td><td></td><td></td>
        </tr>
        <tr>
          <td>부산(RKPK)</td><td>02:00</td><td>11:00</td><td>METAR</td>
          <td>190200Z</td><td>180</td><td>3/-</td><td>10,000</td><td></td>
          <td>4</td><td>3/010</td><td>-/-</td><td>-/-</td><td>-/-</td>
          <td>29.1</td><td>1004.0</td><td></td><td></td>
        </tr>
      </tbody>
    </table>
    """

    rows = parse_kma_public_metar_html(
        html,
        station_id="RKSI",
        reference=datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc),
    )

    assert [row["temp"] for row in rows] == [26.5, 25.9]
    assert [row["reportTime"] for row in rows] == [
        "2026-07-19T02:00:00+00:00",
        "2026-07-19T02:30:00+00:00",
    ]
    assert all(row["icaoId"] == "RKSI" for row in rows)
    assert rows[1]["wxString"] == "RA"


def test_parse_kma_public_metar_html_handles_month_boundary_and_fails_closed():
    html = f"""
    <table>{_KMA_HEADER_HTML}<tbody>
      <tr>
        <td>인천(RKSI)</td><td>23:30</td><td>08:30</td><td>METAR</td>
        <td>302330Z</td><td></td><td></td><td></td><td></td>
        <td></td><td></td><td></td><td></td><td></td>
        <td>21.7</td><td></td><td></td><td></td>
      </tr>
    </tbody></table>
    """

    rows = parse_kma_public_metar_html(
        html,
        station_id="RKSI",
        reference=datetime(2026, 7, 1, 0, 10, tzinfo=timezone.utc),
    )

    assert rows == [
        {
            "icaoId": "RKSI",
            "rawOb": "KMA-OFFICIAL-WEB RKSI 302330Z TEMP=21.7C",
            "reportTime": "2026-06-30T23:30:00+00:00",
            "temp": 21.7,
            "wxString": "",
        }
    ]


def test_parse_kma_public_metar_html_rejects_changed_table_schema():
    wrong_header = """
    <table><thead><tr><th>관측시각 (UTC)</th><th>습도 (%)</th></tr></thead><tbody>
      <tr>
        <td>인천(RKSI)</td><td>02:30</td><td>11:30</td><td>METAR</td>
        <td>190230Z</td><td>230</td><td>5/-</td><td>10,000</td><td></td>
        <td>6</td><td>4/010</td><td>6/025</td><td>-/-</td><td>-/-</td>
        <td>25.9</td><td>1006.0</td><td></td><td></td>
      </tr>
    </tbody></table>
    """
    shifted_row = f"""
    <table>{_KMA_HEADER_HTML}<tbody>
      <tr>
        <td>인천(RKSI)</td><td>02:30</td><td>11:30</td><td>UNKNOWN</td>
        <td>190230Z</td><td>230</td><td>5/-</td><td>10,000</td><td></td>
        <td>6</td><td>4/010</td><td>6/025</td><td>-/-</td><td>-/-</td>
        <td>25.9</td><td>1006.0</td><td></td><td></td>
      </tr>
    </tbody></table>
    """
    shifted_header = f"""
    <table>{_KMA_HEADER_HTML.replace('<th>기온<br>(˚C)</th><th>기압<br>(hPa)</th>', '<th>기압<br>(hPa)</th><th>기온<br>(˚C)</th>')}<tbody>
      <tr>
        <td>인천(RKSI)</td><td>02:30</td><td>11:30</td><td>METAR</td>
        <td>190230Z</td><td>230</td><td>5/-</td><td>10,000</td><td></td>
        <td>6</td><td>4/010</td><td>6/025</td><td>-/-</td><td>-/-</td>
        <td>25.9</td><td>1006.0</td><td></td><td></td>
      </tr>
    </tbody></table>
    """
    reference = datetime(2026, 7, 19, 3, 0, tzinfo=timezone.utc)

    assert parse_kma_public_metar_html(
        wrong_header,
        station_id="RKSI",
        reference=reference,
    ) == []
    assert parse_kma_public_metar_html(
        shifted_row,
        station_id="RKSI",
        reference=reference,
    ) == []
    assert parse_kma_public_metar_html(
        shifted_header,
        station_id="RKSI",
        reference=reference,
    ) == []


def test_provider_uses_keyless_official_kma_page_before_awc():
    html = f"""
    <table>{_KMA_HEADER_HTML}<tbody>
      <tr>
        <td>인천(RKSI)</td><td>15:30</td><td>00:30</td><td>METAR</td>
        <td>211530Z</td><td>290</td><td>3/-</td><td>7,000</td><td></td>
        <td>8</td><td>8/002</td><td>-/-</td><td>-/-</td><td>-/-</td>
        <td>22.8</td><td>1008.5</td><td></td><td></td>
      </tr>
      <tr>
        <td>인천(RKSI)</td><td>15:00</td><td>00:00</td><td>METAR</td>
        <td>211500Z</td><td>290</td><td>3/-</td><td>7,000</td><td></td>
        <td>8</td><td>8/002</td><td>-/-</td><td>-/-</td><td>-/-</td>
        <td>22.3</td><td>1008.5</td><td></td><td></td>
      </tr>
      <tr>
        <td>인천(RKSI)</td><td>14:30</td><td>23:30</td><td>METAR</td>
        <td>211430Z</td><td>290</td><td>3/-</td><td>7,000</td><td></td>
        <td>8</td><td>8/002</td><td>-/-</td><td>-/-</td><td>-/-</td>
        <td>22.1</td><td>1008.5</td><td></td><td></td>
      </tr>
    </tbody></table>
    """
    now = datetime(2026, 7, 21, 15, 30, 20, tzinfo=timezone.utc)
    gets: list[tuple[str, dict[str, str], float, dict[str, str]]] = []

    def fake_get(url, *, params, timeout, headers):
        gets.append((url, params, timeout, headers))
        return _HtmlResponse(html)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=60,
        kma_public_html_enabled=True,
        kma_metar_poll_seconds=30,
        kma_metar_station_ids={"RKSI", "RKPK"},
        clock=lambda: now,
    )
    station = STATION_MAP["seoul"]
    provider._accumulate_metar_daily_extremes(
        station,
        [
            (datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc), 22.1),
            (datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc), 22.3),
        ],
        date(2026, 7, 22),
        persist=False,
        source_name="kma-official-public-metars",
    )
    observation = provider.observed_high_so_far(
        station,
        target_date=date(2026, 7, 22),
        now=now,
    )

    assert len(gets) == 1
    assert gets[0][1] == {"stnCd": "RKSI"}
    assert "Mozilla/5.0" in gets[0][3]["User-Agent"]
    assert gets[0][3]["Referer"].endswith("?stnCd=RKSI")
    assert "text/html" in gets[0][3]["Accept"]
    assert observation.source == "kma-official-public-metars"
    assert observation.latest_temp_c == 22.8
    assert observation.observed_at.isoformat() == "2026-07-21T15:30:00+00:00"
    assert observation.bot_detection_latency_seconds == 20
    assert observation.daily_extremes_complete is True
    assert observation.data_block_reason == ""


def test_provider_falls_back_to_awc_when_public_kma_returns_block_page():
    now = datetime(2026, 7, 21, 15, 31, tzinfo=timezone.utc)

    def fake_get(url, *, params, timeout, headers):
        del params, timeout, headers
        if "global.amo.go.kr" in url:
            return _HtmlResponse("<html><body>Access denied</body></html>")
        return _JsonResponse(
            [
                {
                    "icaoId": "RKSI",
                    "obsTime": "2026-07-21T15:30:00Z",
                    "temp": 23.0,
                }
            ]
        )

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        cache_ttl_seconds=0,
        kma_public_html_enabled=True,
        kma_metar_station_ids={"RKSI", "RKPK"},
        clock=lambda: now,
    )
    station = STATION_MAP["seoul"]
    provider._accumulate_metar_daily_extremes(
        station,
        [
            (datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc), 22.0),
            (datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc), 22.0),
        ],
        date(2026, 7, 22),
        persist=False,
    )

    observation = provider.observed_high_so_far(
        station,
        target_date=date(2026, 7, 22),
        now=now,
    )

    assert observation.usable is True
    assert observation.source == "aviationweather-metar"
    assert observation.latest_temp_c == 23.0


def _provider_with_kma_and_awc_current(*, awc_observed_at: str):
    html = f"""
    <table>{_KMA_HEADER_HTML}<tbody>
      <tr>
        <td>인천(RKSI)</td><td>14:30</td><td>23:30</td><td>METAR</td>
        <td>211430Z</td><td></td><td></td><td></td><td></td>
        <td></td><td></td><td></td><td></td><td></td>
        <td>22.7</td><td></td><td></td><td></td>
      </tr>
      <tr>
        <td>인천(RKSI)</td><td>15:30</td><td>00:30</td><td>METAR</td>
        <td>211530Z</td><td></td><td></td><td></td><td></td>
        <td></td><td></td><td></td><td></td><td></td>
        <td>22.8</td><td></td><td></td><td></td>
      </tr>
    </tbody></table>
    """
    csv_text = (
        "raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string\n"
        f'"METAR RKSI 211530Z 23/22",RKSI,{awc_observed_at},23,22,\n'
    )
    now = datetime(2026, 7, 21, 15, 31, tzinfo=timezone.utc)

    def fake_get(url, **_kwargs):
        if "global.amo.go.kr" in url:
            return _HtmlResponse(html)
        return _CurrentCacheResponse(csv_text)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        awc_current_cache_enabled=True,
        kma_public_html_enabled=True,
        kma_metar_station_ids={"RKSI"},
        cache_ttl_seconds=0,
        clock=lambda: now,
    )
    provider._accumulate_metar_daily_extremes(
        STATION_MAP["seoul"],
        [
            (datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc), 22.0),
            (datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc), 22.0),
        ],
        date(2026, 7, 21),
        persist=False,
    )
    return provider, now


def test_newer_awc_current_report_wins_over_older_kma_report():
    provider, now = _provider_with_kma_and_awc_current(
        awc_observed_at="2026-07-21T15:31:00Z"
    )

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"], target_date=date(2026, 7, 22), now=now
    )

    assert observation.source == "aviationweather-metar"
    assert observation.observed_at == datetime(2026, 7, 21, 15, 31, tzinfo=timezone.utc)


def test_complete_awc_day_wins_over_newer_but_incomplete_kma_day():
    html = f"""
    <table>{_KMA_HEADER_HTML}<tbody>
      <tr>
        <td>Incheon(RKSI)</td><td>15:31</td><td>00:31</td><td>METAR</td>
        <td>211531Z</td><td></td><td></td><td></td><td></td>
        <td></td><td></td><td></td><td></td><td></td>
        <td>22.9</td><td></td><td></td><td></td>
      </tr>
    </tbody></table>
    """
    csv_text = (
        "raw_text,station_id,observation_time,temp_c,dewpoint_c,wx_string\n"
        '"METAR RKSI 211530Z 23/22",RKSI,2026-07-21T15:30:00Z,23,22,\n'
    )
    now = datetime(2026, 7, 21, 15, 31, 20, tzinfo=timezone.utc)
    def fake_get(url, **_kwargs):
        if "global.amo.go.kr" in url:
            return _HtmlResponse(html)
        return _CurrentCacheResponse(csv_text)

    provider = AviationWeatherMetarNowcastProvider(
        http_get=fake_get,
        awc_current_cache_enabled=True,
        kma_public_html_enabled=True,
        kma_metar_station_ids={"RKSI"},
        cache_ttl_seconds=0,
        clock=lambda: now,
    )
    station = STATION_MAP["seoul"]
    provider._accumulate_metar_daily_extremes(
        station,
        [
            (datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc), 22.0),
            (datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc), 22.2),
        ],
        date(2026, 7, 22),
        persist=False,
    )

    observation = provider.observed_high_so_far(
        station,
        target_date=date(2026, 7, 22),
        now=now,
    )

    assert observation.source == "aviationweather-metar"
    assert observation.daily_extremes_complete is True
    assert observation.observed_at == datetime(2026, 7, 21, 15, 30, tzinfo=timezone.utc)


def test_equal_timestamp_prefers_precise_kma_without_awc_integer_contamination():
    provider, now = _provider_with_kma_and_awc_current(
        awc_observed_at="2026-07-21T15:30:00Z"
    )

    observation = provider.observed_high_so_far(
        STATION_MAP["seoul"], target_date=date(2026, 7, 22), now=now
    )

    assert observation.source == "kma-official-public-metars"
    assert observation.observed_high_c == 22.8
    assert observation.latest_temp_c == 22.8
