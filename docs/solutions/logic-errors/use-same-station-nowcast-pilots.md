---
title: Use same-station nowcast pilots before expanding weather observations
date: 2026-06-02
category: logic-errors
module: weather_bot.nowcast
problem_type: logic_error
component: service_object
symptoms:
  - "A nowcast provider could be wired to all 41 supported cities before each station source is verified."
  - "City-center weather or a nearby station could look like useful same-day evidence while not matching the Polymarket settlement station."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [polymarket, weather-nowcast, settlement-station, fail-closed, observations]
---

# Use same-station nowcast pilots before expanding weather observations

## 1. 무슨 문제가 있었는지

당일 최고기온 nowcast를 추가할 때 가장 쉬운 실수는 "서울 날씨"처럼
도시 중심 관측값을 가져와서 정산 관측소 관측값처럼 쓰는 것입니다.

이 프로젝트에서 서울 마켓은 서울 도심이 아니라 RKSI, 즉 인천국제공항
관측소 기준으로 정산됩니다. 그래서 당일 관측도 같은 관측소에서 나온
값이어야 합니다. 이번 Phase 5는 Seoul/RKSI pilot으로 시작한 뒤 공식 관측
API 확인이 된 곳만 확장했습니다.

## 2. 왜 문제가 되었는지

날씨 마켓의 수익성은 1~2도 차이로 달라질 수 있습니다. 서울 도심은
덥고 인천공항은 해풍 때문에 덜 더울 수 있습니다. 이때 도심 관측값을
쓰면 봇은 "이미 27도 넘었다"고 생각하지만, 정산 관측소는 아직 27도에
도달하지 않았을 수 있습니다.

초보자가 흔히 오해하는 부분은 `STATION_MAP`에 도시 41개가 있으니
nowcast도 41개를 바로 지원해야 한다고 생각하는 것입니다. 하지만
`STATION_MAP`은 거래 허용 관측소 목록이고, nowcast provider 검증은
별개의 일입니다.

## 3. 어떻게 고쳤는지

- `src/weather_bot/nowcast.py`를 새로 두고 observation provider를
  명시적 station mapping 뒤에 숨겼습니다.
- Phase 5의 첫 pilot은 Seoul/RKSI였고, 이후 39개 ICAO 관측소는
  Aviation Weather Center METAR로 확장했습니다.
- Hong Kong/HKO는 거래량이 활발한 편이라 제끼지 않고 Hong Kong Observatory
  자정 이후 최고/최저기온 CSV provider를 붙였습니다.
- Karachi/OPMR은 AWC METAR에서 최근 자료가 확인되지 않아 forecast-only로
  남겼습니다.
- 정산 기준 URL은 Wunderground RKSI daily history로 기록하고, 당일 진행
  관측은 같은 ICAO station id의 Aviation Weather Center METAR API에서만
  읽습니다.
- fresh, stale, malformed, unavailable fixture 테스트를 먼저 만들고 RED를
  확인한 뒤 구현했습니다.
- 관측이 fresh/verified이면 `evidence=forecast-plus-nowcast`를 남기고,
  없거나 오래됐거나 malformed이거나 mapping이 없으면
  `evidence=forecast-only`와 unavailable 이유를 남깁니다.
- nowcast가 실제로 쓸 수 있을 때만 threshold crossed 또는 bucket
  impossible 같은 보정이 적용됩니다.
- `src/weather_bot/stations.py`에 `station_audit_rows()`를 추가해 각 도시가
  예보용 좌표, nowcast provider 상태, Polymarket 규칙 원문 증거
  보관 여부를 한 줄로 설명하게 했습니다. 사람이 읽는 전체 표는
  `docs/station-registry-audit.md`에 둡니다.

## 4. 다음에 같은 실수를 막으려면 무엇을 확인해야 하는지

- provider를 추가하기 전에 Polymarket 정산 규칙의 station id와 관측
  provider의 station id가 같은지 확인합니다.
- `observed_high_c`, `observed_at`, `source_url`, `settlement_source_url`,
  `freshness_seconds`, `unavailable_reason`이 로그에 남는지 확인합니다.
- stale 기준을 넘은 관측이 probability를 바꾸지 않는지 테스트합니다.
- malformed fixture가 SKIP/fallback으로 처리되는지 테스트합니다.
- 새 도시를 추가할 때는 "도시 이름"이 아니라 "정산 관측소의 공식 또는
  같은-station 관측 출처"를 먼저 검증합니다.
- `provider_unavailable`은 "정산 관측소 코드는 있지만 실제 관측 API를 아직
  안전하게 못 읽는다"는 뜻입니다. 이 상태의 도시는 예보만 사용합니다.
- `rule_evidence_status`는 별도입니다. 나중에 Polymarket 규칙 URL과 정확한
  관측소 문구를 코드 필드에 채우면 사람이 검증하기 더 쉬워집니다.

## 5. 이 프로젝트에서 특히 조심해야 할 점

Phase 5의 nowcast는 보조 증거입니다. Wunderground 등 최종 정산 기준이
마지막 답안지이고, METAR/HKO nowcast는 당일 진행 상황을 일찍 보기 위한
자료입니다.

다음 Phase 6에서 settlement runner를 만들 때도 이 원칙을 유지해야
합니다. nowcast가 없으면 runner 판단을 지어내지 말고 forecast-only로
남기거나 nowcast 의존 부분을 건너뜁니다.

관련 문서:

- `docs/production-decisions.md`
- `docs/production-implementation-plan.md`
- `docs/station-registry-audit.md`
- `docs/solutions/logic-errors/discover-weather-events-before-binary-markets.md`
- `docs/solutions/workflow-issues/do-not-use-deterministic-forecast-fallback-for-strategy-validation-2026-05-26.md`
