# Station Registry Audit

This checklist shows which forecast coordinates and nowcast station candidates
are used for the 41 supported cities.

Beginner explanation: a weather market is settled by a specific official
station, not by a vague city-center weather reading. For example, a Seoul market
may settle on Incheon International Airport station data rather than a downtown
Seoul sensor. Forecast inputs and same-day observation checks must therefore use
the same settlement station. Mixing nearby stations is like grading an answer
sheet against the wrong exam.

## How To Read This

- `forecast_source`: where the forecast comes from. The current source is the
  Open-Meteo ensemble forecast.
- `forecast coordinates`: the latitude and longitude sent to Open-Meteo. These
  are settlement-station coordinates from `STATION_MAP`, not city-center
  coordinates.
- `nowcast_station`: the station candidate used for same-day observation checks.
- `nowcast status`: whether current observed values can be used in probability
  calculation.
- `provider_enabled`: the code can read an official observation API for the same
  settlement station.
- `provider_unavailable`: a settlement station code exists, but the official
  observation API did not provide recent data, so the bot uses forecasts only.
- `rule_evidence_status`: whether the Polymarket rules URL and settlement
  station wording are stored in code. `needs_rule_source_url` means the station
  is not being marked wrong; it only means the original rule evidence has not yet
  been captured in code fields for later human review.

## Current Conclusion

- All 41 cities use `STATION_MAP` station coordinates for Open-Meteo forecast
  input.
- 39 ICAO stations read same-day observed values through the Aviation Weather
  Center METAR API.
- `hong kong/HKO` uses Hong Kong Observatory max/min temperature CSV data since
  midnight instead of METAR. Hong Kong max-temperature markets were high-volume
  during research, so this provider was implemented instead of skipping them.
- `karachi/OPMR` currently uses forecasts only because recent AWC METAR data was
  not available during verification.
- Original Polymarket rule URLs and rule wording have not yet been filled for
  every city. `STATION_MAP` remains the existing settlement-station baseline.

| city | settlement station | station name | forecast coordinates | forecast_source | nowcast_station | nowcast type | nowcast status | rule evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| amsterdam | EHAM | Amsterdam Airport Schiphol Station | 52.3086, 4.7639 | open-meteo-ensemble | EHAM | metar | provider_enabled | needs_rule_source_url |
| ankara | LTAC | Esenboga Intl Airport Station | 40.1281, 32.9951 | open-meteo-ensemble | LTAC | metar | provider_enabled | needs_rule_source_url |
| atlanta | KATL | Hartsfield-Jackson International Airport Station | 33.6407, -84.4277 | open-meteo-ensemble | KATL | metar | provider_enabled | needs_rule_source_url |
| beijing | ZBAA | Beijing Capital International Airport Station | 40.0801, 116.5846 | open-meteo-ensemble | ZBAA | metar | provider_enabled | needs_rule_source_url |
| buenos aires | SAEZ | Minister Pistarini Intl Airport Station | -34.8222, -58.5358 | open-meteo-ensemble | SAEZ | metar | provider_enabled | needs_rule_source_url |
| busan | RKPK | Gimhae Intl Airport Station | 35.1795, 128.9382 | open-meteo-ensemble | RKPK | metar | provider_enabled | needs_rule_source_url |
| cape town | FACT | Cape Town International Airport Station | -33.9700, 18.6021 | open-meteo-ensemble | FACT | metar | provider_enabled | needs_rule_source_url |
| chengdu | ZUUU | Chengdu Shuangliu International Airport Station | 30.5785, 103.9471 | open-meteo-ensemble | ZUUU | metar | provider_enabled | needs_rule_source_url |
| chicago | KORD | Chicago O'Hare Intl Airport Station | 41.9742, -87.9073 | open-meteo-ensemble | KORD | metar | provider_enabled | needs_rule_source_url |
| chongqing | ZUCK | Chongqing Jiangbei International Airport Station | 29.7192, 106.6417 | open-meteo-ensemble | ZUCK | metar | provider_enabled | needs_rule_source_url |
| dallas | KDAL | Dallas Love Field Station | 32.8471, -96.8518 | open-meteo-ensemble | KDAL | metar | provider_enabled | needs_rule_source_url |
| guangzhou | ZGGG | Guangzhou Baiyun International Airport Station | 23.3924, 113.2988 | open-meteo-ensemble | ZGGG | metar | provider_enabled | needs_rule_source_url |
| helsinki | EFHK | Helsinki Vantaa Airport Station | 60.3172, 24.9633 | open-meteo-ensemble | EFHK | metar | provider_enabled | needs_rule_source_url |
| hong kong | HKO | Hong Kong Observatory | 22.3022, 114.1744 | open-meteo-ensemble | HKO | hko_maxmin_since_midnight | provider_enabled | needs_rule_source_url |
| istanbul | LTFM | Istanbul Airport | 41.2613, 28.7419 | open-meteo-ensemble | LTFM | metar | provider_enabled | needs_rule_source_url |
| jeddah | OEJN | King Abdulaziz International Airport Station | 21.6796, 39.1565 | open-meteo-ensemble | OEJN | metar | provider_enabled | needs_rule_source_url |
| karachi | OPMR | Masroor Airbase Station | 24.8936, 66.9388 | open-meteo-ensemble | OPMR | metar_unavailable | provider_unavailable | needs_rule_source_url |
| london | EGLC | London City Airport Station | 51.5053, 0.0553 | open-meteo-ensemble | EGLC | metar | provider_enabled | needs_rule_source_url |
| los angeles | KLAX | Los Angeles International Airport Station | 33.9416, -118.4085 | open-meteo-ensemble | KLAX | metar | provider_enabled | needs_rule_source_url |
| madrid | LEMD | Adolfo Suarez Madrid-Barajas Airport Station | 40.4983, -3.5676 | open-meteo-ensemble | LEMD | metar | provider_enabled | needs_rule_source_url |
| manila | RPLL | Ninoy Aquino International Airport Station | 14.5086, 121.0196 | open-meteo-ensemble | RPLL | metar | provider_enabled | needs_rule_source_url |
| miami | KMIA | Miami Intl Airport Station | 25.7959, -80.2870 | open-meteo-ensemble | KMIA | metar | provider_enabled | needs_rule_source_url |
| milan | LIMC | Malpensa Intl Airport Station | 45.6306, 8.7281 | open-meteo-ensemble | LIMC | metar | provider_enabled | needs_rule_source_url |
| moscow | UUWW | Vnukovo International Airport | 55.5915, 37.2615 | open-meteo-ensemble | UUWW | metar | provider_enabled | needs_rule_source_url |
| munich | EDDM | Munich Airport Station | 48.3538, 11.7861 | open-meteo-ensemble | EDDM | metar | provider_enabled | needs_rule_source_url |
| nyc | KLGA | LaGuardia Airport Station | 40.7769, -73.8740 | open-meteo-ensemble | KLGA | metar | provider_enabled | needs_rule_source_url |
| panama city | MPMG | Marcos A. Gelabert Intl Airport Station | 8.9733, -79.5556 | open-meteo-ensemble | MPMG | metar | provider_enabled | needs_rule_source_url |
| paris | LFPB | Paris-Le Bourget Airport Station | 48.9694, 2.4414 | open-meteo-ensemble | LFPB | metar | provider_enabled | needs_rule_source_url |
| qingdao | ZSQD | Qingdao Jiaodong International Airport Station | 36.3619, 120.0881 | open-meteo-ensemble | ZSQD | metar | provider_enabled | needs_rule_source_url |
| seattle | KSEA | Seattle-Tacoma International Airport Station | 47.4502, -122.3088 | open-meteo-ensemble | KSEA | metar | provider_enabled | needs_rule_source_url |
| seoul | RKSI | Incheon Intl Airport Station | 37.4602, 126.4407 | open-meteo-ensemble | RKSI | metar | provider_enabled | needs_rule_source_url |
| shanghai | ZSPD | Shanghai Pudong International Airport Station | 31.1443, 121.8083 | open-meteo-ensemble | ZSPD | metar | provider_enabled | needs_rule_source_url |
| shenzhen | ZGSZ | Shenzhen Bao'an International Airport Station | 22.6393, 113.8107 | open-meteo-ensemble | ZGSZ | metar | provider_enabled | needs_rule_source_url |
| singapore | WSSS | Singapore Changi Airport Station | 1.3644, 103.9915 | open-meteo-ensemble | WSSS | metar | provider_enabled | needs_rule_source_url |
| taipei | RCSS | Taipei Songshan Airport Station | 25.0697, 121.5525 | open-meteo-ensemble | RCSS | metar | provider_enabled | needs_rule_source_url |
| tel aviv | LLBG | Ben Gurion International Airport | 32.0055, 34.8854 | open-meteo-ensemble | LLBG | metar | provider_enabled | needs_rule_source_url |
| tokyo | RJTT | Tokyo Haneda Airport Station | 35.5494, 139.7798 | open-meteo-ensemble | RJTT | metar | provider_enabled | needs_rule_source_url |
| toronto | CYYZ | Toronto Pearson Intl Airport Station | 43.6777, -79.6248 | open-meteo-ensemble | CYYZ | metar | provider_enabled | needs_rule_source_url |
| warsaw | EPWA | Warsaw Chopin Airport Station | 52.1657, 20.9671 | open-meteo-ensemble | EPWA | metar | provider_enabled | needs_rule_source_url |
| wellington | NZWN | Wellington Intl Airport Station | -41.3272, 174.8053 | open-meteo-ensemble | NZWN | metar | provider_enabled | needs_rule_source_url |
| wuhan | ZHHH | Wuhan Tianhe International Airport Station | 30.7838, 114.2081 | open-meteo-ensemble | ZHHH | metar | provider_enabled | needs_rule_source_url |
