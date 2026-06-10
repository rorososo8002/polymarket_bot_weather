# Current Task

Status: active

## Objective

대시보드 UI 전면 개편:
1. 이벤트 포트폴리오 섹션 제거
2. 보유 포지션 카드 재설계 (영문 side/Long, 예보온도, 확률, 진입가, 현재가, 손익)
3. 확정 손익(최근 체결) 테이블→카드 전환 (수익/손절 표시, 이유, 수수료)
4. 도시별 예보 상태 카드 추가 (스크롤)
5. 도시별 관측소 상태 카드 추가 (스크롤)
6. entry: 파라미터 한글 라벨로 표시

## Next Action

dashboard.py:
- _position_payload()에 p_true, net_edge, entry_fee_usdc, entry_fraction 추가
- _realized_results()에 action, p_true 추가
- _per_city_forecast_status(), _per_city_nowcast_status() 함수 추가
- build_dashboard_payload()에 per_city_forecast, per_city_nowcast 포함

dashboard_template.py:
- CSS: city-card 스타일 추가
- HTML: 이벤트 포트폴리오 제거, 도시별 카드 섹션 추가
- JS: cardForPosition() 재설계
- JS: realizedTable() → realizedCards() 전환
- JS: render() 이벤트 포트폴리오 블록 제거/대체

## Files In Play

- src/weather_bot/dashboard.py
- src/weather_bot/dashboard_template.py


## Non-Negotiables

- Preserve paper-only execution unless the user explicitly approves a separate
  live-trading safety pass.
- Runtime ledgers are generated experiment evidence. Delete or reset them only
  when the user intentionally asks for a fresh paper-experiment window.
- Keep this card replace-only. Do not append completed work history here.

## New Chat Prompt

```text
Continue this project. Follow AGENTS.md. First read
docs/active/current-task.md and docs/production-decisions.md. If current-task
is active, continue from Next Action. If current-task is none, use my latest
request and read only the conditional documents needed for that task.
```
