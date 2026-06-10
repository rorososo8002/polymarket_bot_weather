# 정산 중인 보유 포지션이 신규 진입 전체를 차단하는 버그

## 날짜
2026-06-10

## 증상
- 전날부터 신규 진입이 1건뿐이고 그 이후 아무것도 열리지 않음
- journalctl에서 `HOLD_STREAM_UNHEALTHY` 메시지 16,850번 발생
- `paper_decisions.csv`는 1행뿐 (skip 로그 비활성화 상태)
- `MARK ERROR: 'no websocket orderbook snapshot for token ...'` 반복 출력

## 근본 원인

`portfolio.py`의 `available_entry_bankroll()` 함수에 두 가지 블록 경로가 있었다:

1. **전체 스트림 블록** (`websocket_pricing_block_reason(health)`)  
   WebSocket 스레드가 죽거나 stale하면 진입 차단 — **올바른 동작**

2. **개별 포지션 가격 산정 실패 블록** (per-position loop)  
   보유 포지션의 `get_order_book()` 호출이 예외를 던지거나  
   `exit_price is None`이면 `EntryBankrollSnapshot(usable=False)` 즉시 반환  
   → **전체 신규 진입 차단 — 버그**

Paris June-10 마켓이 정산 단계에 진입하면서 해당 토큰의 주문장이 사라졌고,
이로 인해 하루 종일 모든 신규 진입이 차단됐다.

## 수정 방법

`available_entry_bankroll()` (portfolio.py):

- 예외 경로: `return EntryBankrollSnapshot(usable=False)` →
  `_logger.warning(...)` + `continue` (포지션 가치를 $0으로 처리)
- no-bid 경로: 동일하게 `continue`로 변경

안전성 근거:
- `cost_basis_bankroll = cash + total_exposure()` — 포지션 비용은 이미 차감됨
- `liquidation_bankroll = cash + 0` = cash만 — 보수적 계산
- `entry_bankroll = min(cost_basis, liquidation)` = cash — 과도한 레버리지 없음

전체 스트림 블록(WebSocket 스레드 다운, stale)은 그대로 유지.

## 예방 규칙

- `available_entry_bankroll()`의 per-position 가격 산정 실패는 `usable=False`가 아닌
  `$0 처리 + 경고 로그`로 처리해야 한다.
- 전체 스트림 블록과 개별 포지션 가격 실패는 **다른 심각도**로 취급해야 한다.
- 정산 임박 마켓의 토큰은 주문장이 사라지는 것이 정상이며, 이것이 신규 진입을
  막아서는 안 된다.

## 관련 파일
- `src/weather_bot/portfolio.py` — `available_entry_bankroll()` L164-210
- `tests/test_portfolio.py` — `test_run_cycle_proceeds_with_zero_when_held_position_cannot_be_priced`
- `tests/test_realtime_runner.py` — `test_realtime_update_computes_held_exit_edge_with_fresh_signal`

## 커밋
d2747a9 fix: treat settling held position as $0 instead of blocking all new entries
