---
title: Keep VWAP Slippage Out of Edge Math
date: 2026-05-25
last_updated: 2026-06-01
category: logic-errors
module: weather_bot.edge
problem_type: logic_error
component: service_object
symptoms:
  - "A review finding called the slippage parameter dead code in yes_net_edge and no_net_edge"
  - "Subtracting slippage in the edge formula caused the existing no-double-count regression to fail"
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [vwap, slippage, edge, paper-trading, regression-test]
---

# Keep VWAP Slippage Out of Edge Math

## 무슨 문제가 있었는지

`executable_buy_price()`가 계산한 `p_exec`는 단순 최우선 호가가 아니라
ask-side VWAP입니다. 즉, 진입 스프레드와 진입 슬리피지가 이미 가격에
들어 있습니다. 그런데 예상 수익을 계산하면서 슬리피지를 별도 비용으로
다시 빼면 같은 비용을 두 번 차감하게 됩니다.

Phase 2에서는 고정 `estimated_fee_per_share=0.02`도 제거해야 했습니다.
Polymarket weather taker 수수료는 가격에 따라 달라지므로 고정값으로
모델링하면 진입 가격에 따라 비용을 과대 또는 과소 추정합니다.

## 왜 문제가 되었는지

- `0.88 -> 0.92`처럼 겉보기 수익이 얇은 거래를 잘못 통과시킬 수
  있습니다.
- 반대로 VWAP 슬리피지를 이중 차감하면 정상 후보까지 과도하게
  거절할 수 있습니다.
- 고가 진입을 가격만 보고 막으면 정산까지 보유했을 때 보수적으로도
  충분한 수익이 남는 후보를 평가하지 못합니다.

## 어떻게 고쳤는지

공식 weather 수수료 곡선을 테스트 가능한 함수로 분리했습니다.

```python
fee_usdc = shares * fee_rate * price * (1 - price)
```

`fee_rate`의 paper 기본값은 공식 weather 카테고리 값인 `0.05`입니다.
USDC 수수료는 공식 문서에 따라 소수점 다섯 자리로 반올림합니다.

진입 판단에서는 두 비용 경로를 구분합니다.

1. 일반 청산 경로: 진입 VWAP은 그대로 사용하고, 미래 청산 가격에서
   현재 스프레드와 관측 슬리피지를 보수적으로 차감한 뒤 청산
   수수료를 계산합니다.
2. 정산 보유 경로: 모델 오차와 정산 오차를 뺀 보수적 정산 기대값을
   사용합니다. 주문서 청산이 아니므로 청산 스프레드, 슬리피지,
   taker 수수료는 없습니다.

기존 `net_edge` 조건은 유지하고, 별도로 예상 순수익률이 기본 `6%`
이상인지 확인합니다.

## 다음에 같은 실수를 막으려면 무엇을 확인해야 하는지

- 비용 항목을 추가하기 전에 이미 VWAP 가격에 포함된 비용인지
  확인합니다.
- 진입 비용과 미래 청산 비용을 구분합니다.
- 공식 수수료 문서의 함수, 카테고리 비율, 반올림 규칙을 테스트합니다.
- `0.88 -> 0.92` 거절과 고가 정산 후보 통과를 회귀 테스트로
  유지합니다.
- 실거래 프로젝트로 확장할 때는 카테고리 기본값만 믿지 말고 마켓별
  CLOB 수수료 파라미터를 조회합니다.

## 이 프로젝트에서 특히 조심해야 할 점

이 저장소는 paper bot입니다. 이번 변경은 paper 진입 필터를 현실적으로
만든 것이지 실거래 주문을 추가한 것이 아닙니다. decision log의
`reason`에 예상 총수익, 비용, 순수익률, 경로와 거절 이유를 남겨서
나중에 paper 결과를 검토할 수 있게 유지합니다.
