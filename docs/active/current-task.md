# Current Task

Status: active

## Objective

`available_entry_bankroll()` 버그 수정 — 정산 중인 보유 포지션 1개의 주문장이
없다는 이유로 신규 진입 전체가 차단되는 문제 수정.

## Root Cause

`portfolio.py` `available_entry_bankroll()` 함수에서 held position의
`get_order_book()` 결과가 exception이거나 exit_price=None이면
`EntryBankrollSnapshot(usable=False)` 를 즉시 반환해 모든 신규 진입을 막았음.

이는 June 10 시장처럼 정산 단계에 진입한 포지션에서 주문장이 사라지는 경우에도
동일하게 적용돼 하루 종일 신규 진입을 차단함.

## Fix Plan

1. `available_entry_bankroll()` 예외 경로(L184-191): 주문장 스냅샷 없음/예외 시
   블록 대신 liquidation value=$0으로 처리 후 계속 진행 + WARNING 로그
2. `available_entry_bankroll()` no-bid 경로(L192-198): exit_price=None 시
   블록 대신 liquidation value=$0으로 처리 후 계속 진행 + WARNING 로그

안전성: liquidation_bankroll이 cash만으로 계산되므로 보수적.
entry_bankroll = min(cost_basis_bankroll, liquidation_bankroll) = min($200, $190) = $190.
정산 중인 포지션의 가치를 $0으로 가정하는 것이 차단보다 올바른 동작.

## Next Action

portfolio.py 수정 완료 후:
- 로컬 pytest 실행
- git commit
- VPS 배포 (scp + systemctl restart polymarket-weather-bot)

## Files In Play

- src/weather_bot/portfolio.py

## Non-Negotiables

- Preserve paper-only execution.
- Runtime ledgers 변경 없음.
- Keep this card replace-only.

## New Chat Prompt

```text
Continue this project. Follow AGENTS.md. First read
docs/active/current-task.md and docs/production-decisions.md. If current-task
is active, continue from Next Action. If current-task is none, use my latest
request and read only the conditional documents needed for that task.
```
