---
title: Correlated event budgets need a broker backstop
date: 2026-06-01
last_updated: 2026-06-02
category: logic-errors
module: city-date portfolio selection
problem_type: logic_error
component: service_object
symptoms:
  - "Nearby weather buckets could be treated like independent trades even though they share one city-date outcome."
  - "A runner-level maximum-two-leg rule could be bypassed by direct broker calls using several small orders."
  - "Adjacent Celsius buckets could look overlapping because Fahrenheit conversion produced tiny floating-point boundary differences."
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags: [polymarket, weather, portfolio, exposure-cap, correlated-risk, floating-point, neg-risk]
---

# Correlated event budgets need a broker backstop

## 1. 무슨 문제가 있었는지

서울 같은 도시의 같은 날짜 최고기온 이벤트에는 여러 마켓이 있습니다.

```text
26°C
27°C
28°C or higher
```

이 마켓들을 각각 별도 거래처럼 열면 같은 날씨 결과에 노출되는 금액이
의도보다 커집니다. runner에서 최대 2개 leg만 선택해도 충분하지
않았습니다. 작은 주문으로 `PaperBroker.open_position()`을 직접 여러 번
호출하면 세 번째 leg가 event 예산 안에 들어갈 수 있었습니다.

또한 `26°C`와 `27°C`는 서로 겹치지 않는 인접 구간이지만, 섭씨를
화씨로 바꾸면 내부 경계가 `79.700000...`과 `79.699999...`처럼 아주
조금 달라질 수 있습니다. 단순 비교는 이를 겹치는 구간으로 잘못
판단했습니다.

## 2. 왜 문제가 되었는지

한 마켓의 `YES`와 다른 마켓의 `YES`가 서로 다른 토큰이어도 경제적
위험이 독립적이라는 뜻은 아닙니다. 같은 도시와 날짜의 기온 결과를
공유하면 하나의 event 예산으로 관리해야 합니다.

선택 로직만 믿는 것도 위험합니다. runner 외의 테스트, 유지보수 코드,
향후 실행 경로가 broker를 직접 호출할 수 있습니다. 돈을 기록하는
마지막 계층에서도 같은 제한을 검사해야 우회가 생기지 않습니다.

## 3. 어떻게 고쳤는지

- `src/weather_bot/portfolio.py`에서 도시+날짜 event 후보를 함께
  평가합니다.
- 작은 paper 계좌는 같은 event 전체에 최대 10%, 기준금 `$1,000`
  이상은 최대 5%만 사용합니다.
- 단일 leg는 최대 10%이면서 최소 `$10`, 같은 도시의 여러 날짜 합계는
  최대 20%, 전체 오픈 노출은 최대 90%로 제한합니다.
- event당 최대 2개 leg만 허용합니다.
- event 확률 합을 100%로 정규화하고 가능한 최종 기온별 손익표를 만든
  뒤, 비용 차감 후 예상 순이익이 양수인 `YES+YES`, `YES+NO`, `NO+NO`
  조합을 비교합니다.
- `PaperBroker.open_position()`도 같은 마켓 보유, 비보완 조합, event leg
  개수를 다시 검사합니다.
- 인접 온도 구간 비교에는 작은 `epsilon`을 사용합니다.
- `paper_event_portfolios.jsonl`에 선택, 거절, 예산과 scenario PnL을
  기록합니다.

## 4. 다음에 같은 실수를 막으려면 무엇을 확인해야 하는지

- 위험 한도는 `단일 주문`, `도시+날짜 event`, `도시`, `전체 계좌`
  순서로 각각 확인합니다.
- 전략 선택 계층과 실제 상태를 바꾸는 broker 계층에 같은 핵심 제한을
  둡니다.
- 한도 테스트는 정상 선택 경로만 쓰지 않습니다. broker를 직접
  호출하여 세 번째 leg, `$10` 미만 주문, 도시 20%, 전체 90% 제한
  우회를 확인합니다.
- 연속값 경계를 비교할 때는 단위 변환 뒤의 부동소수점 오차를
  테스트합니다.

## 5. 이 프로젝트에서 특히 조심해야 할 점

`MAX_EVENT_DATE_EXPOSURE_FRACTION=0.10`은 leg마다 10%를 허용한다는 뜻이
아닙니다. 같은 도시+날짜 event에 선택된 모든 leg가 합쳐서 10%를
나눠 씁니다. 기준금이 `$1,000` 이상이면 이 공유 예산은 5%로
줄어듭니다.

`$100` 계좌에서는 event 예산과 leg 최소 금액이 모두 `$10`이므로 한
leg만 열립니다. `$200` 계좌부터 event 예산이 `$20`이 되어 계산
결과에 따라 `$10+$10` 두 leg가 가능합니다. 전체 90% 한도는 한
event에 90%를 넣는다는 뜻이 아닙니다. event 한도와 도시 한도가 먼저
적용됩니다.

새 진입 기준금도 남은 현금만 보지 않습니다.

```text
cost_basis_bankroll = cash + open-position entry cost
liquidation_bankroll = cash + executable sell value of open positions
entry_bankroll = min(cost_basis_bankroll, liquidation_bankroll)
```

보유 포지션을 안전하게 청산할 주문서가 없으면 새 진입은 멈춰야
합니다. 알 수 없는 값을 낙관적으로 추측하지 않습니다.

## Related

- `docs/solutions/logic-errors/discover-weather-events-before-binary-markets.md`
- `docs/production-implementation-plan.md`
- `src/weather_bot/portfolio.py`
- `src/weather_bot/paper.py`

## 6. 같은 event의 `NO` leg도 손익표에서 함께 비교해야 합니다

도시+날짜 event 포트폴리오는 서로 겹치지 않는 `YES` 구간만 비교하면
불완전합니다. Polymarket의 `negRisk=true` event에서는 한 구간의 `NO`
1주가 경제적으로 다른 모든 구간의 `YES` 1주씩과 연결됩니다.

예를 들어 서울 최고기온이 `27C`로 정산되면 `27C YES`, `25C NO`,
`26C NO`는 모두 승리합니다. 따라서 `25C NO`와 `26C NO`의 가격이
앙상블 확률보다 충분히 싸다면 두 leg를 함께 사는 조합도 후보가
되어야 합니다.

하지만 두 leg를 독립 거래처럼 계산하면 안 됩니다. 가능한 모든 최종
기온 구간을 한 줄씩 펼친 뒤, 각 줄에서 포트폴리오 전체가 받는 정산
금액을 계산해야 합니다.

```text
선택한 leg: 25C NO + 26C NO

최종 기온 25C -> 25C NO는 패배, 26C NO는 승리
최종 기온 26C -> 25C NO는 승리, 26C NO는 패배
그 외 구간   -> 두 NO가 모두 승리
```

구현에서는 같은 event의 `YES+YES`, `YES+NO`, `NO+NO` 조합을 최대
leg 개수 안에서 모두 비교합니다. 같은 market의 `YES+NO` 동시
보유는 계속 막습니다. 조합 선택 전에 event 전체 구간 확률 합을
100%로 정규화하고, 주문장 깊이, 슬리피지, 수수료를 반영합니다.

공식 참고:
https://docs.polymarket.com/advanced/neg-risk
