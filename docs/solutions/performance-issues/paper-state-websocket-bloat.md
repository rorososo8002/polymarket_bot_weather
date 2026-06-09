# paper_state.json 무한 비대화 — 웹소켓 헬스 데이터 실수로 영구 저장

## 카테고리
performance-issue / disk-bomb

## 발견 경위
봇을 초기화하고 재시작한 지 33분 만에 `paper_decisions.csv` 113MB,
`paper_state.json` 68KB로 급격히 증가. `paper_state.json` 안에 수백 개의
토큰 타임스탬프가 포함된 것을 발견.

---

## 초보자 완전 이해 설명 (새 AI도 이 방식으로 설명할 것)

### 1. stream.health_snapshot()은 뭐에 쓰이는가?

봇은 Polymarket 실시간 호가를 받기 위해 WebSocket(웹소켓) 연결을 유지한다.
웹소켓 = 서버와 봇 사이에 전화선처럼 계속 연결된 채널.
주문장 가격이 바뀔 때마다 봇에게 실시간으로 밀어준다.

`stream.health_snapshot()`은 이 전화선의 건강 상태를 점검하는 함수다.
"지금 연결 살아있어? 마지막 메시지는 언제 왔어?" 같은 것을 확인한다.

### 2. 왜 subscribed_book_ts(타임스탬프)가 포함되어 있었나?

웹소켓이 연결되면 봇은 39개 도시 x 2개 토큰(YES/NO) = 약 수백 개 토큰을 구독한다.
각 토큰마다 "마지막으로 호가 데이터가 언제 왔는가"를 추적하는데,
이게 바로 subscribed_book_ts다.

  subscribed_book_ts = {
    "토큰ID_도쿄YES": "16:47:09",
    "토큰ID_서울NO":  "16:47:10",
    "토큰ID_뉴욕YES": "16:46:55",
    ... (수백 개)
  }

용도: "이 토큰에서 데이터가 너무 안 오면 -> 웹소켓이 죽었다고 판단 ->
      청산 보류" 판단에 쓰임.

이건 메모리 안에서만 쓰면 충분한 임시 데이터다.

### 3. 왜 이걸 포지션 메타데이터에 저장했나?

코드에서 웹소켓이 불안정할 때 이런 일을 했다:

  # 웹소켓 불안정 -> 포지션 청산 보류 처리
  pos.metadata["last_websocket_health"] = health  # <- 문제 지점
  broker.save_state()  # <- 그리고 디스크에 저장

저장한 이유: 대시보드에서 "웹소켓 상태가 어떤지" 보여주려고
포지션 객체에 붙여둔 것이다.
개발자가 "상태 정보를 포지션과 함께 보관하면 나중에 조회하기 편하겠다"고
생각했던 것.

### 4. 왜 이게 디스크 폭탄이 됐나?

save_state()는 포지션 정보를 paper_state.json에 저장한다.
포지션 metadata에 last_websocket_health가 있으니
수백 개의 토큰 타임스탬프가 통째로 파일에 기록된다.

웹소켓 불안정 -> 매 사이클(약 40초)마다 -> 수백 개 타임스탬프 덮어쓰기
-> 파일이 무한정 커짐.

### 5. 굳이 저장할 필요가 없는 이유

- 타임스탬프는 "지금 이 순간 연결 상태"를 보는 것
  -> 저장해봤자 10분 뒤엔 쓸모없는 과거 데이터
- 대시보드는 봇이 살아있는 동안만 보면 됨
  -> 메모리에만 있어도 충분
- paper_state.json의 진짜 목적은 현금 잔고와 오픈 포지션
  -> 웹소켓 로그가 아님

비유: 통장 잔고 수첩(paper_state.json)에
     "오늘 ATM 기계 네트워크 연결 시간 로그"를 빼곡히 적어두는 것.
     잔고 확인할 때 전혀 필요 없는 정보다.

---

## 수정 내용

파일: src/weather_bot/paper.py -> save_state() 함수

저장 전에 웹소켓 진단 키를 걸러낸다:

  _TRANSIENT_METADATA_KEYS = {"last_websocket_health", "last_websocket_token_health"}

  def _clean_metadata(meta: dict) -> dict:
      return {k: v for k, v in meta.items() if k not in _TRANSIENT_METADATA_KEYS}

  payload = {
      "positions": [
          {**asdict(p), "metadata": _clean_metadata(p.metadata)}
          for p in self.state.positions
      ],
  }

메모리에서는 그대로 유지(대시보드용), 디스크에는 저장하지 않음.

---

## 재발 방지 규칙

새 AI가 코드를 수정하거나 리뷰할 때 반드시 지킬 것:

1. pos.metadata에 뭔가 저장할 때는 반드시 질문하라:
   "이 데이터, 봇이 꺼졌다 켜져도 필요한가?"
   -> YES: 영구 저장 OK
   -> NO: 메모리 전용, save_state()에서 제외 필요

2. 외부 API의 health snapshot은 절대 영구 저장하지 마라.
   health 데이터는 "지금 이 순간"의 상태이므로
   저장해둬봤자 다음 부팅 때 아무 의미가 없다.

3. save_state() 수정 시 _TRANSIENT_METADATA_KEYS 집합을 먼저 확인하라.
   새로운 진단 키를 metadata에 추가했다면 이 집합에도 추가해야 한다.

---

## 관련 파일
- src/weather_bot/paper.py L772 save_state()
- src/weather_bot/paper.py L1584 pos.metadata["last_websocket_health"] = health
- src/weather_bot/paper.py L1616 pos.metadata["last_websocket_token_health"] = token_health
- docs/codex/data-and-disk.md -- 디스크 관리 규칙

## 커밋
fix: exclude websocket health metadata from paper_state.json save
