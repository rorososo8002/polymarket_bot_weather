---
title: Explain technical fields before listing them for beginner handoffs
date: 2026-06-01
category: workflow-issues
module: assistant communication
problem_type: workflow_issue
component: assistant
severity: medium
applies_when:
  - "A document introduces API fields, configuration names, status values, or developer terms."
  - "The user must make a money, security, deployment, or production decision."
  - "A handoff document is written for a beginner developer."
tags: [beginner, explanation, documentation, handoff, workflow, api-fields]
---

# Explain technical fields before listing them for beginner handoffs

## 1. 무슨 문제가 있었는지

실거래 계획을 설명하면서 `MATCHED`, `MINED`, `CONFIRMED`, `RETRYING`,
`FAILED` 같은 상태값을 이름만 나열했습니다. 초보 개발자는 이 목록을
보고도 어느 상태에서 기다려야 하는지, 어느 상태에서 복구 작업이
필요한지 판단하기 어렵습니다.

## 2. 왜 문제가 되었는지

필드명과 상태값은 개발자끼리 빠르게 대화할 때 쓰는 짧은 표지판입니다.
하지만 표지판의 뜻을 모르는 사람에게 표지판 이름만 보여주면 실제
작동 방식을 설명한 것이 아닙니다.

이 프로젝트는 돈, 지갑, 서버, 주문 상태를 다룹니다. 뜻을 모른 채
설정을 따라 하면 잘못된 주문 제출, 체결 오판, 복구 누락으로 이어질
수 있습니다.

## 3. 어떻게 고쳤는지

`AGENTS.md`에 한국어 설명 규칙을 강화했습니다. 개발자 용어, 필드,
설정 이름, 상태 값, API 이름을 사용할 때 다음 내용을 쉬운 예시와
함께 설명합니다.

1. 무엇인지
2. 실제로 어떤 역할을 하는지
3. 어디에 쓰이는지
4. 이것이 있으면 무엇이 좋아지는지
5. 왜 이 프로젝트에 필요한지
6. 초보자가 흔히 오해하는 점

새 실거래 계획 문서에도 같은 기준을 적용해 주문 이벤트, 체결 상태,
수수료 필드, 지갑 역할을 각각 설명했습니다.

## 4. 다음에 같은 실수를 막으려면 무엇을 확인해야 하는지

- 전문 용어 목록을 적은 뒤 각 항목 아래에 쉬운 설명이 있는지
  확인합니다.
- 상태값은 “무슨 뜻인지”뿐 아니라 “bot이 다음에 무엇을 해야 하는지”
  함께 적습니다.
- 숫자 설정은 단위와 예시를 적습니다. 예를 들어 `base_fee=30`은
  30%가 아니라 10,000분의 30인 `0.30%`입니다.
- 비슷해 보이는 두 용어는 차이를 적습니다. 예를 들어 token ID는
  실제 YES 또는 NO 주문 대상을 찾고, condition ID는 질문 전체의
  공통 설정을 찾습니다.
- 설명을 읽은 초보자가 다음 행동을 선택할 수 없다면 아직 설명이
  부족한 것입니다.

## 5. 이 프로젝트에서 특히 조심해야 할 점

나쁜 설명:

```text
MATCHED, MINED, CONFIRMED, RETRYING, FAILED를 추적합니다.
```

좋은 설명:

```text
MATCHED는 상대 주문과 짝이 맞아 체결 처리를 시작한 상태입니다.
아직 최종 완료가 아니므로 포지션을 확정했다고 가정하면 안 됩니다.

CONFIRMED는 체결이 충분히 확정된 상태입니다. 이 시점에 bot이
성공한 체결로 기록할 수 있습니다.

FAILED는 체결 반영이 실패로 끝난 상태입니다. bot의 로컬 기록과
Polymarket의 실제 상태를 다시 비교한 뒤 다음 주문을 판단해야 합니다.
```

실거래 문서에서는 필드를 빠르게 많이 적는 것보다, 사용자가 위험과
다음 행동을 이해할 수 있게 설명하는 것이 우선입니다.

## Related

- `AGENTS.md`
- `docs/live-trading-safety-plan.md`
