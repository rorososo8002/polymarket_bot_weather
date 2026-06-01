---
title: Discover complete weather events before counting binary markets
date: 2026-06-01
category: logic-errors
module: weather market discovery
problem_type: logic_error
component: service_object
symptoms:
  - "MAX_MARKETS=41 stopped discovery after 41 binary submarkets instead of scanning 41 city-date events."
  - "One multi-bucket temperature event could consume many scan slots and make city coverage look larger than it was."
  - "Renaming the cutoff to MAX_EVENTS=41 still confused the verified-city allowlist with a discovery stop condition."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [polymarket, weather-discovery, events, temperature-buckets, gamma-api]
---

# Discover complete weather events before counting binary markets

## 1. 무슨 문제가 있었는지

날씨 마켓 탐색 제한인 `MAX_MARKETS=41`을 지원 도시 41개와 같은
뜻으로 사용했습니다. 하지만 이 값이 실제로 세고 있던 것은 도시가
아니라 YES/NO 이진 서브마켓 개수였습니다.

예를 들어 서울 5월 25일 최고기온 이벤트 하나에는 다음과 같은 여러
서브마켓이 들어갈 수 있습니다.

```text
18°C or below
19°C
20°C
...
27°C
28°C or higher
```

서울 이벤트 하나만으로 여러 슬롯을 소비하므로, 41개 서브마켓을
가져왔다고 해서 41개 도시를 확인한 것은 아닙니다.

## 2. 왜 문제가 되었는지

`event`는 도시와 날짜가 같은 질문 묶음입니다. `market`은 그 묶음
안에서 실제로 거래하는 YES/NO 결과 하나입니다.

두 개념을 섞으면 탐색이 이벤트 중간에서 잘릴 수 있습니다. 그러면
어떤 온도 구간은 평가하고 바로 옆 구간은 누락합니다. 운영자는 숫자
`41`만 보고 전체 도시를 충분히 확인했다고 오해할 수 있습니다.

## 3. 어떻게 고쳤는지

- Gamma `/events` endpoint를 기준으로 날씨 이벤트를 탐색합니다.
- Polymarket 날씨 카테고리 페이지에서 찾은 지원 가능한 이벤트는 개수로
  자르지 않고 모두 펼칩니다.
- 선택한 이벤트 안의 지원 가능한 이진 서브마켓도 전부 유지합니다.
- fallback Gamma API가 끝없이 페이지를 요청하지 않도록
  `DISCOVERY_MAX_PAGES`와 `DISCOVERY_PAGE_SIZE`를 별도로 둡니다.
- runner는 실제 event 수, 도시 수, market 수, token 수를 따로
  표시합니다.
- exact, lower-tail, upper-tail 구간은 같은 앙상블 분포와 공유
  경계로 계산합니다.

## 4. 다음에 같은 실수를 막으려면 무엇을 확인해야 하는지

- 외부 API의 목록 제한이 무엇을 세는지 확인합니다. 도시, 이벤트,
  마켓, 토큰은 서로 다른 개념입니다.
- 하나의 이벤트가 여러 마켓을 포함할 때 이벤트 중간에서 목록을
  자르지 않습니다.
- 지원 도시 allowlist의 크기를 탐색 중단 기준으로 재사용하지 않습니다.
- coverage 로그에는 event, 도시, market, token을 각각 표시합니다.
- 테스트에서 카테고리 이벤트가 41개보다 많아도 모두 남는지, 한
  이벤트의 서브마켓이 모두 남는지 확인합니다.

## 5. 이 프로젝트에서 특히 조심해야 할 점

지원 도시 수 `41`은 정산 관측소 allowlist 크기입니다. 실제 탐색에서
41개 도시를 모두 찾았다는 보증이 아닙니다.

```text
DISCOVERY_MAX_PAGES=8
DISCOVERY_PAGE_SIZE=100
```

이 설정은 fallback API 결과를 최대 8페이지, 페이지마다 최대 100행씩
읽겠다는 뜻입니다. API가 이상하게 동작해도 무한 요청을 하지 않게
막습니다. 도시를 8개 또는 100개로 줄이는 기능도 아니고, 이벤트를
41개에서 자르는 기능도 아닙니다. 실제 coverage는 runner 상태에
표시된 event 수와 도시 수를 확인해야 합니다.

## Related

- `docs/solutions/logic-errors/weather-discovery-false-positives-2026-05-24.md`
- `docs/production-implementation-plan.md`
- `src/weather_bot/polymarket_client.py`
