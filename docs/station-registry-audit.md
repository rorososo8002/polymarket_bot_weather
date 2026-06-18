# Station Registry Audit

This checklist explains the official settlement-station registry used by the
paper strategy.

Beginner explanation: a weather market is not settled by a vague city-center
weather reading. It is settled by a named official station in the market rules.
For example, a Seoul market may settle on Incheon International Airport station
data rather than a downtown Seoul sensor. Using the wrong station is like
grading an answer sheet with the wrong answer key.

## How To Read This

- `STATION_MAP` is the full station registry in code.
- `TRADING_READY_STATION_MAP` is the execution universe. A city may trade only
  when its official rule evidence and station observation provider are trusted.
- `nowcast_station` is the same official station used for same-day observed
  high/low checks.
- `nowcast status` says whether current observed values can be used for paper
  strategy validation.
- `nowcast confidence grade` says whether same-station observed values are
  trusted enough for paper execution. Grades A/B may enter the trading-ready
  subset; grades C/D stay excluded.
- `rule_evidence_status` says whether the Polymarket rules URL and settlement
  station wording are stored in code.

## Current Conclusion

- All 49 cities remain in `STATION_MAP` as the station registry.
- 48 cities are trading-ready because code stores an official Polymarket rule
  URL and station wording for them.
- `karachi/OPMR` is not trading-ready. Its station evidence conflicts with the
  current registry and must be reconciled from a primary source before use.
- Trading discovery and station-signal generation use the trading-ready subset,
  not the full 49-city registry.
- 47 ICAO stations read same-day observed values through the Aviation Weather
  Center METAR API and carry grade A station confidence. One bulk AWC request
  covers the enabled ICAO set, and real AWC calls are floored at 60 seconds.
- `hong kong/HKO` uses Hong Kong Observatory max/min temperature CSV data since
  midnight. It carries grade A station confidence. Real HKO calls stay floored
  at 10 minutes.
- The dashboard should show every supported settlement station from the
  registry, not only stations that happened to appear in the latest request
  log.
- Seoul paper trading remains tied to `RKSI/Incheon Intl Airport Station`
  because the stored Polymarket rule text names that station. KMA Seoul ASOS
  108 is display-only reference context unless a Polymarket rule explicitly
  settles on it.
- Original Polymarket rule URLs and rule wording are stored in
  `src/weather_bot/stations.py`.

## June 19 Expansion Evidence

These eight stations were named by the current Polymarket resolution text and
returned matching live rows from the official AWC METAR API on June 19, 2026.
The timezone is the station's local clock used to decide which calendar day the
market is asking about. The unit is the value Polymarket displays and uses for
settlement buckets.

| city | station | coordinates | timezone | settlement unit | AWC METAR |
| --- | --- | --- | --- | --- | --- |
| austin | KAUS | 30.1831, -97.6806 | America/Chicago | whole Fahrenheit | verified |
| denver | KBKF | 39.7130, -104.7580 | America/Denver | whole Fahrenheit | verified |
| houston | KHOU | 29.6458, -95.2821 | America/Chicago | whole Fahrenheit | verified |
| kuala lumpur | WMKK | 2.7470, 101.7140 | Asia/Kuala_Lumpur | whole Celsius | verified |
| lucknow | VILK | 26.7610, 80.8890 | Asia/Kolkata | whole Celsius | verified |
| mexico city | MMMX | 19.4360, -99.0720 | America/Mexico_City | whole Celsius | verified |
| san francisco | KSFO | 37.6196, -122.3656 | America/Los_Angeles | whole Fahrenheit | verified |
| sao paulo | SBGR | -23.4320, -46.4690 | America/Sao_Paulo | whole Celsius | verified |
