# Station Registry Audit

이 문서는 41개 지원 도시가 어떤 예보 좌표와 어떤 관측소 nowcast 후보를
쓰는지 한눈에 보기 위한 점검표입니다.

초보자용으로 비유하면 이렇습니다. 서울 날씨 시장에서 정산은 "서울역 온도"가
아니라 "인천공항 관측소 온도"로 끝납니다. 그러면 예보도 서울 도심 좌표가
아니라 인천공항 좌표를 넣어야 하고, 당일 진행 상황을 볼 때도 같은 인천공항
관측소 자료만 봐야 합니다. 근처 다른 관측소를 섞으면 답안지를 다른 시험지로
채점하는 것과 같습니다.

## 읽는 법

- `forecast_source`: 예보를 어디서 받는지입니다. 현재는 Open-Meteo ensemble입니다.
- `forecast 좌표`: Open-Meteo에 넣는 위도/경도입니다. 도시 중심이 아니라
  `STATION_MAP`의 정산 관측소 좌표를 씁니다.
- `nowcast_station`: 당일 관측 진행 상황을 볼 후보 관측소입니다.
- `nowcast 상태`: 오늘 실제 관측값을 확률 계산에 반영할 수 있는지입니다.
  - `provider_enabled`: 같은 정산 관측소의 공식 관측 API를 코드가 읽을 수 있습니다.
  - `provider_unavailable`: 정산 관측소 코드는 있지만 공식 관측 API에서 최근 자료를
    확인하지 못했으므로 예보만 씁니다.
- `rule_evidence_status`: Polymarket 규칙 URL과 정산 관측소 문구가 코드에
  보관되어 있는지입니다. 현재는 전부 `needs_rule_source_url`입니다. 이 말은
  "관측소가 틀렸다"가 아니라 "나중에 사람이 확인할 수 있는 원문 증거 URL과
  문구를 아직 코드 필드로 저장하지 않았다"는 뜻입니다.

## 현재 결론

- 41개 도시 모두 Open-Meteo 예보 입력 좌표는 `STATION_MAP`의 관측소 좌표를 씁니다.
- 39개 ICAO 관측소는 Aviation Weather Center METAR API로 오늘 실제 관측값을
  읽습니다.
- `hong kong/HKO`는 METAR가 아니라 Hong Kong Observatory의 자정 이후 최고/최저
  기온 CSV를 읽습니다. 홍콩 최고기온 시장은 조사 시점에 거래량 상위권이라
  제끼지 않고 구현했습니다.
- `karachi/OPMR`은 AWC METAR에서 최근 자료가 확인되지 않아 현재는 예보만 씁니다.
- Polymarket 규칙 원문 URL과 문구는 아직 전 도시 필드에 채우지 않았습니다.
  다만 `STATION_MAP` 자체는 기존 정산 관측소 기준표로 유지합니다.

| city | settlement station | station name | forecast 좌표 | forecast_source | nowcast_station | nowcast 종류 | nowcast 상태 | rule evidence |
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
