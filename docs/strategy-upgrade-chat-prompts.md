# Strategy Upgrade Fresh-Chat Prompts

Created: 2026-05-31 Asia/Seoul

Use one prompt per fresh chat. Wait until the current phase is complete before
starting the next one.

## 0. Baseline Preservation And Verification

```text
이 저장소의 AGENTS.md와 docs/strategy-upgrade-roadmap.md를 먼저 읽어줘.
이번 채팅에서는 Phase 0만 끝까지 진행해줘. 현재 작업 폴더에 이미 남아
있는 변경사항을 절대 삭제하거나 되돌리지 말고, 누가 만든 변경인지
모르더라도 먼저 검토해서 보존해줘.

git status와 변경 diff를 확인하고, 현재 들어가 있는 WebSocket 보유
포지션 구독 유지 수정 및 SKIP 진단 개선이 서로 일관적인지 검토해줘.
관련 focused test를 실행한 뒤 전체 pytest도 실행해줘. 코드와 문서가
다르면 필요한 문서만 갱신해줘. durable learning이 있으면
docs/solutions/에 기록해줘.

이번 채팅에서는 자동 배포하거나 자동 commit하지 마. 마지막에
수정 파일, 테스트 결과, 남은 위험, commit해도 되는지, Phase 1로
넘길 인수인계를 초보자도 이해하게 한국어로 보고하고 멈춰줘.
```

## 1. Forecast Freshness And WebSocket Health

```text
이 저장소의 AGENTS.md와 docs/strategy-upgrade-roadmap.md를 먼저 읽어줘.
Phase 0 완료 인수인계를 확인하고, 이번 채팅에서는 Phase 1만 진행해줘.

Open-Meteo 최근 예보 시간이 오래 멈춰 있어도 대시보드가 정상처럼
보이는 문제와 WebSocket 스레드가 죽어도 메인 서비스가 건강한 것처럼
보일 수 있는 문제를 고쳐줘. 메모리 캐시에도 TTL을 적용하고, 마지막
예보 시도 시각, 마지막 성공 시각, 최근 실패 이유, cache age, stale
여부를 상태 JSON과 대시보드에 표시해줘. WebSocket은 스레드 생존 여부,
재접속 횟수, 마지막 메시지 시각, stale book age를 추적해줘.

테스트를 먼저 추가하고 focused test와 전체 pytest를 실행해줘. VPS의
현재 상태를 확인해야 하면 읽기 전용 SSH 명령만 사용해도 돼.
AGENTS.md에 고정된 Oracle SSH 경로를 사용하고 키 내용은 절대 열거나
출력하지 마. 로컬 검증 후 배포가 필요하면 자동 배포하지 말고 변경
내용, 위험, 검증 방법, 되돌리는 방법을 설명한 뒤 내 승인을 기다려줘.
마지막에 Phase 2 인수인계를 남기고 멈춰줘.
```

## 2. Executable Net-Return Entry Filter

```text
이 저장소의 AGENTS.md와 docs/strategy-upgrade-roadmap.md를 먼저 읽어줘.
이전 Phase 완료 인수인계를 확인하고, 이번 채팅에서는 Phase 2만 진행해줘.

0.88에 진입해서 0.92 근처에 청산하는 것처럼 수수료, 스프레드,
슬리피지를 빼면 수익이 너무 얇은 거래를 막아줘. Polymarket 공식
수수료 문서를 다시 확인하고, 고정 추정치 대신 테스트 가능한 실제
수수료 계산 함수를 사용해줘. 기존 net edge 조건과 별개로 진입 전
예상 순수익률 필터를 추가하고 기본 가설은 6%로 둬. 다만 고가 진입을
무조건 금지하지는 말고, 정산까지 보유했을 때 보수적으로 충분한
수익이 남는 경우는 평가 가능하게 해줘.

테스트를 먼저 추가해 0.88 -> 0.92가 거절되고 충분히 유리한 거래는
통과하는지 보여줘. decision log에 예상 총수익, 비용, 순수익률,
거절 이유를 남겨줘. focused test와 전체 pytest를 실행하고 production
문서를 갱신해줘. 자동 배포하지 말고 마지막에 Phase 3 인수인계를
남기고 멈춰줘.
```

## 3. Exact Buckets And Event-Based Discovery

```text
이 저장소의 AGENTS.md와 docs/strategy-upgrade-roadmap.md를 먼저 읽어줘.
이전 Phase 완료 인수인계를 확인하고, 이번 채팅에서는 Phase 3만 진행해줘.

날씨 이벤트를 단순한 임계값 마켓으로 가정하지 말고 실제 온도 구간
이벤트로 처리해줘. 예를 들어 26도 정확히, 18도 이하, 28도 이상 같은
구간을 모두 파싱하고, 앙상블 분포에서 서로 일관적인 확률을 계산해줘.
도시와 날짜 이벤트 단위로 마켓을 묶어 탐색해줘. MAX_MARKETS=41이면
41개 도시를 충분히 훑는다는 기존 가정은 검증하고, 실제로 이진
서브마켓 개수를 세고 있었다면 이벤트 기반 탐색으로 고쳐줘.

parser, probability, discovery, runner 테스트를 먼저 추가해줘. focused
test와 전체 pytest를 실행하고 오래된 production 문서 설명도 고쳐줘.
자동 배포하지 말고 마지막에 Phase 4 인수인계를 남기고 멈춰줘.
```

## 4. City-Date Portfolio Selection

```text
이 저장소의 AGENTS.md와 docs/strategy-upgrade-roadmap.md를 먼저 읽어줘.
이전 Phase 완료 인수인계를 확인하고, 이번 채팅에서는 Phase 4만 진행해줘.

서울 한 포지션 때문에 같은 날짜의 다른 유리한 구간 기회를 전부
놓치지 않도록 도시+날짜 이벤트 단위 포트폴리오 선택을 추가해줘.
하지만 같은 날 같은 도시의 베팅들은 서로 강하게 연관되어 있으므로
독립 거래처럼 한도를 곱하지 마. 초기 paper 단계에서는 기존 도시+
날짜 전체 노출 한도를 보수적으로 유지하고, 선택된 여러 leg가 그
예산을 나눠 쓰게 해줘. 비용 반영 후 포트폴리오 EV가 개선될 때만
보완 조합을 허용하고, 같은 마켓 반대 포지션과 과도한 같은 방향
집중은 막아줘.

테스트를 먼저 추가하고 focused test와 전체 pytest를 실행해줘.
event-level decision log와 필요한 dashboard 설명을 추가하고 production
문서를 갱신해줘. 노출 한도를 늘리고 싶다면 코드로 몰래 바꾸지 말고
해결된 paper 거래 근거가 왜 충분한지 먼저 나에게 설명해줘. 자동
배포하지 말고 마지막에 Phase 5 인수인계를 남기고 멈춰줘.
```

## 5. Settlement-Station Nowcast

```text
이 저장소의 AGENTS.md와 docs/strategy-upgrade-roadmap.md를 먼저 읽어줘.
이전 Phase 완료 인수인계를 확인하고, 이번 채팅에서는 Phase 5만 진행해줘.

당일 매매 판단을 개선하기 위해 정산 기준 관측소의 현재 최고기온
nowcast를 설계하고 구현해줘. 먼저 공식 정산 규칙과 관측 데이터 출처,
업데이트 주기를 조사해줘. 도시 중심 날씨나 추측값으로 대체하지 마.
신뢰 가능한 관측 출처가 일부 관측소에만 있으면 41개 도시 전체를
억지로 지원하지 말고 검증된 소규모 pilot부터 시작해줘.

관측 최고기온, 관측 시각, 출처, freshness, unavailable 이유를 기록하고,
관측이 없거나 오래됐거나 검증되지 않았으면 nowcast 의존 로직은
SKIP 처리해줘. fixture 기반 provider 테스트를 먼저 추가하고 fresh,
stale, malformed, unavailable 사례를 검증해줘. focused test와 전체
pytest를 실행하고 문서를 갱신해줘. 자동 배포하지 말고 마지막에
Phase 6 인수인계를 남기고 멈춰줘.
```

## 6. Principal Recovery And Settlement Runner

```text
이 저장소의 AGENTS.md와 docs/strategy-upgrade-roadmap.md를 먼저 읽어줘.
이전 Phase 완료 인수인계를 확인하고, 이번 채팅에서는 Phase 6만 진행해줘.

낮은 가격에 산 유리한 NO 또는 YES 포지션을 너무 일찍 전량 청산하지
않도록 전략적 부분청산을 추가해줘. 지금 팔았을 때 수수료 차감 후
금액과 보수적인 정산 기대값을 비교하고, 적절하면 일부를 팔아 원금을
회수한 뒤 제한된 runner 수량을 정산까지 보유하게 해줘. 기존 확률
악화 stop, invalid sentinel 방어, 최대 보유 시간, 유동성 제한,
관측 위험 방어는 유지해줘.

테스트를 먼저 추가해 원금 회수, runner 유지, 확률 악화, 정산 위험,
낮은 유동성 상황을 검증해줘. tranche별 판단을 paper log에 남기고
focused test와 전체 pytest를 실행한 뒤 문서를 갱신해줘. 자동
배포하지 말고 마지막에 Phase 7 인수인계를 남기고 멈춰줘.
```

## 7. Whale And External-Signal Shadow Research

```text
이 저장소의 AGENTS.md와 docs/strategy-upgrade-roadmap.md를 먼저 읽어줘.
이전 Phase 완료 인수인계를 확인하고, 이번 채팅에서는 Phase 7만 진행해줘.

고래 트레이더와 외부 전략은 바로 자동 추종하지 말고 shadow research로
분리해줘. 최신 Polymarket 공식 API 문서를 확인하고, 공개 데이터만
사용해서 날씨 마켓 관련 wallet activity, 마켓, 방향, 가격, 시각,
나중의 결과를 제한된 크기로 수집할 수 있는 연구 구조를 만들어줘.
트위터나 공개 글도 조사할 수 있지만 근거와 추측을 구분해줘.

자동 copy trading, 실거래, 비공개 정보 수집은 추가하지 마. 우리 봇의
paper decision과 외부 신호의 시점 및 결과를 비교하는 보고서를 만들고,
나중에 paper-only 실험으로 승격할 가치가 있는지 결론을 내려줘.
필요한 테스트와 문서를 갱신하고 전체 pytest를 실행해줘. 마지막에
전체 로드맵 완료 요약과 아직 실험으로 남겨야 할 항목을 보고해줘.
```
