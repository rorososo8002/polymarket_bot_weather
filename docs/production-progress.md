# Production Progress

## 완료

- 봇은 실거래 주문을 보내지 않는 paper-trading 전용 서비스입니다.
- 거래 가능한 도시는 `src/weather_bot/stations.py`의 검증된 41개 도시로
  제한했습니다. `STATION_MAP`이 정산 관측소의 기준입니다.
- Open-Meteo 예보는 기본 30분마다 갱신합니다. 메모리 캐시와 디스크
  캐시에 같은 TTL을 적용하고, 마지막 시도·성공·실패 이유·캐시 나이·
  오래된 예보 여부·디스크 저장 오류를 대시보드에 표시합니다.
- 주문서는 Polymarket CLOB WebSocket으로 받습니다. 실시간 수신
  스레드가 살아 있는지, 재접속 횟수, 마지막 메시지, 마지막 실제
  주문서 갱신, 주문서 나이, 오류를 대시보드에 표시합니다.
- 새 마켓 탐색 날짜가 바뀌어도 보유 포지션의 토큰은 WebSocket 구독에
  남깁니다. 그래야 보유 포지션 가격이 과거 값에서 멈추지 않습니다.
- YES와 NO 양쪽 모두 거래하기 어려운 경우, SKIP 로그에 양쪽 거절
  이유를 남깁니다.
- `net_edge=-999`처럼 계산 실패를 뜻하는 값은 손절 신호로 사용하지
  않습니다.
- 대시보드는 현재 포지션, 진입금액, 최근 예보, 실현 수익·손실, 남은
  현금과 예보·WebSocket 건강 상태를 보여줍니다.
- weather taker 수수료는 공식 곡선
  `shares * 0.05 * price * (1 - price)`로 계산합니다. 진입 VWAP에는
  진입 스프레드와 슬리피지가 이미 포함되므로 다시 빼지 않습니다.
- 기존 `net_edge` 조건과 별도로 예상 순수익률이 기본 `6%` 이상인
  진입만 허용합니다. 일반 청산 경로와 보수적 정산 보유 경로를
  평가하며, 고가 진입을 가격만으로 무조건 금지하지 않습니다.
- decision log의 `reason`에는 예상 총수익, 예상 비용, 예상 순수익률,
  선택 경로와 거절 이유를 남깁니다.
- Phase 0과 Phase 1은 로컬에서 검증했고 `4ac3cf5`로 커밋했습니다.
- Phase 2는 로컬에서 구현하고 테스트했습니다. 아직 커밋하거나
  배포하지 않았습니다.

## 진행 중

- Phase 2 로컬 변경은 검증을 마쳤고 커밋 전 상태입니다.
- Oracle VPS에는 Phase 0, Phase 1, Phase 2 변경을 아직 배포하지
  않았습니다.
- 배포는 변경 내용, 위험, 검증 방법, 되돌리는 방법을 설명한 뒤 사용자
  승인을 받아야 합니다.

## 다음 작업

1. 다음 fresh chat에서는 `docs/strategy-upgrade-roadmap.md`의 Phase 3만
   진행합니다.
2. `26°C` 같은 exact bucket, lower-tail, upper-tail 질문을 지원합니다.
3. binary-market 개수가 아니라 event, city, date 기준으로 discovery
   범위를 측정합니다.
4. 기존 남은 위험도 보존합니다. 기본 WebSocket 경로에는 해결된
   시장의 settlement 처리가 아직 연결되지 않았습니다.

## 이어받는 AI에게

> 처음부터 다시 설계하지 말고 이 문서의 '진행 중'과 '다음 작업'부터 이어갑니다. 완료된 항목을 다시 구현하지 않고, 코드와 문서가 맞지 않으면 차이를 기록한 뒤 진행합니다.

- 먼저 `AGENTS.md`, `docs/production-implementation-plan.md`,
  `docs/production-decisions.md`, `docs/strategy-upgrade-roadmap.md`를
  읽습니다.
- `src/weather_bot/stations.py`의 `STATION_MAP`을 지원 도시와 정산
  관측소의 단일 기준으로 취급합니다.
- 실거래, 지갑 연결, 자동 배포는 별도 승인 없이 추가하지 않습니다.
- 향후 실거래 실행 계층은 `docs/live-trading-safety-plan.md`에서 별도로
  이어갑니다. paper 전략 Phase와 섞지 않습니다.
- Phase 3에서는 이번 Phase 2의 공식 수수료 함수와 예상 순수익률
  필터를 유지합니다.
