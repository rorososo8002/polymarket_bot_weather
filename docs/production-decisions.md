# Production Decisions

This is the active decision ledger. Keep only rules that a future AI must obey
while changing or operating the project.

## Execution Boundary

- Paper-only execution is the boundary. No private keys, wallet connection,
  signing, live orders, automatic copy trading, private data collection, or
  redemption flows are allowed without explicit approval and a separate
  `docs/live-trading-safety-plan.md` pass.
- Public dashboard exposure requires a real `DASHBOARD_TOKEN` with at least 32
  characters and not an obvious example value. Public `/api/status` accepts the
  token only through `X-Dashboard-Token`; public `?token=...` API
  authentication is rejected because URLs leak through history, logs, copied
  links, and screen sharing.
- Boolean, numeric, integer, and choice environment settings fail closed at
  startup when malformed or outside safe ranges.

## Market Universe And Forecasts

- The bot registers 41 cities in `STATION_MAP`, but paper execution uses only
  `TRADING_READY_STATION_MAP`: 40 cities with stored official Polymarket rule
  evidence and no known station-code conflict. Karachi remains excluded until
  the station evidence is reconciled.
- Execution is temperature-only. Non-temperature weather markets must not reach
  forecast probability calculation, order-book subscription, or paper trade
  logging.
- Unknown, stale, malformed, unsupported, suspicious, missing, or conflictful
  data means skip.
- Forecast rows must match the target market date exactly. Nearby forecast dates
  are not substitutes.
- `pre_forecast_tradeability_gate` rejects markets before Open-Meteo when they
  are not temperature-shaped, not trading-ready, or missing required date
  evidence. Undated markets always fail closed before forecast or trade.
- `WEATHER_BIAS_JSON` is optional calibration evidence. Empty means neutral
  defaults; an explicit missing, unreadable, invalid, malformed, or non-numeric
  file produces `forecast-unavailable` with zero confidence.
- Open-Meteo forecast HTTP calls use batch mode. Within a batch, requests are
  serialized with `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=15` gaps. After a batch
  completes, the bot waits until `FORECAST_CACHE_TTL_SECONDS=10800` (3 h) expires
  before starting the next batch. GFS updates every 6 h (processed in 3-4 h), so
  a 3-h cache captures each new model run without redundant calls.
  Budget: 39 active cities × 8 batches/day × 31 units ≈ 9 672 units/day < 10 000.
- On a non-rate-limit forecast failure, skip that city and move to the next one in
  the batch. Do not retry within the same batch. Failure cooldown equals the cache
  TTL so the city is only retried at the next batch (~90 min later).
- On a 429 rate-limit response, stop the entire batch and wait for the rate-limit
  cooldown before resuming. Do not hammer failed cities.
- If a real Open-Meteo request does not succeed within the configured request
  budget, treat it as timeout/cancelled failure, record the reason, avoid
  immediate retry pressure on the same key, and continue to the next eligible
  city or forecast key.
- `forecast_cache.json` is an answer cache, not the call ledger. Real attempts
  are recorded in `forecast_request_log.jsonl`.

## Realtime Order Books

- Use the Polymarket CLOB WebSocket market stream by default. Do not silently
  replace realtime streaming with polling.
- Realtime startup must not wait for every supported market forecast before
  starting the CLOB WebSocket stream. Start streaming once the temperature token
  subscription set is known, then attach forecast/nowcast signals as they become
  ready.
- Early WebSocket streaming does not permit forecast-free entries. Markets
  without a fresh supported signal must not open new positions.
- Realtime forecast signals are maintained as a fill-as-ready registry. Missing
  or stale signals must be logged as blocked evaluation and queued for refresh,
  not treated as executable entry evidence.
- Forecast refresh uses a two-lane scheduler: a priority lane for held
  positions, near-close markets, nowcast-near-threshold markets, and live-price
  opportunity markets; plus a normal round-robin lane for the trading-ready city
  universe.
- The priority lane changes which eligible city gets the next single request
  slot. It must not create duplicate, parallel, or burst Open-Meteo calls.
- Forecast scheduling targets are 40 minutes for general cities, 30 minutes for
  held-position cities, and 20 minutes for near-close, nowcast-near-threshold,
  or live-price opportunity cities. These are based on last successful real
  forecast time, not city-count position.
- Runner status must show forecast-worker pending key/city, in-flight key/city,
  queue depth, priority reason, last success, last failure, and next eligible
  request time so cold-start and refresh gaps are observable.
- `websocket-client` must be importable before the paper runner starts a market
  stream. Missing WebSocket support fails closed and is written to runner
  status.
- Keep token IDs for open positions subscribed even when discovery moves to
  newer markets.
- Discovery maps YES/NO token IDs only from explicit outcome labels. If tokens
  or outcomes cannot prove the YES and NO sides, skip the market.
- Closed or inactive Polymarket markets are not new-entry candidates. Closed
  markets remain settlement evidence for already-held paper positions only.
- `best_bid_ask` messages are indicative quotes only. Executable bid/ask depth
  comes from `book` snapshots and valid `price_change` updates.
- A `price_change` may update executable depth only after that token already
  received an initial `book` snapshot in the current WebSocket cache.
- WebSocket callbacks must not make forecast HTTP calls.
- Stale or dead executable depth blocks new entries and pauses held-position
  exits with observable reasons.

## Strategy, Risk, And Accounting

- `DECISION YES` and `DECISION NO` are model/order-book judgments, not
  guaranteed opens. Broker exposure, hedge, confidence, liquidity, fee, and
  stale-data gates may still block entry.
- Entry decisions are fee-aware. `p_exec` is executable VWAP; `size_usd` is the
  all-in paper-entry budget; `size_shares` is the actual fee-adjusted share
  count.
- Same-market opposite-side entries remain blocked. Same-side add-ons are
  allowed only when price, probability, edge, expected return, cash, and
  exposure caps all still pass.
- City-date weather buckets share one correlated-risk budget. At most two
  complementary legs are selected per event.
- Exit decisions use after-fee liquidation PnL, not raw token-price movement.
  Probability stop, take profit, overheated profit, edge-faded exit, max hold,
  settlement, and nowcast bucket-lock risk are the allowed exit paths.
- Nowcast bucket-lock risk blocks **new entry** as well as triggering exit.
  If `_nowcast_bucket_lock_exit_signal` fires for the candidate side before
  an order is placed, the result must be converted to SKIP so that no position
  is opened. Enter-then-immediately-exit is forbidden.
- If an exit signal fires but the close cannot execute, the broker logs the
  blocker and preserves the original `exit_trigger`; it must not pretend to
  sell.
- Profit exits may recover principal and keep a bounded settlement runner only
  when conservative settlement value beats fee-adjusted sell-now value.
- Resolved paper settlement requires a proven binary winner. Ambiguous closed
  prices are not guessed.
- `paper_state.json` is the account book, not a disposable cache. Existing
  corrupt, structurally invalid, or unsafe state fails closed instead of
  resetting.
- `paper_state.json` and `paper_trades.csv` are paired ledgers. Startup replays
  executed trade rows against `BANKROLL_USD` and fails closed if replayed state
  disagrees with the account book.

## Position Sizing Strategy (2026-06-11 결정)

### 결정 사항

Fixed-fraction(고정 비율)에서 **Fractional Kelly(켈리 분수)** 방식으로 전환.
엣지가 클수록 자동으로 베팅 크기가 커지는 수학적으로 최적화된 사이징.

### 현재 적용 파라미터

```
SIZE_MODE              = kelly          # 엣지 비례 사이징
FRACTIONAL_KELLY       = 0.25          # 1/4 켈리 (모델 오차 보수치 감안)
ENTRY_FRACTION         = 0.20          # 단일 이벤트 최대 베팅 상한 (자본금 20%)
MAX_TOTAL_EXPOSURE_FRACTION = 0.60     # 동시 포지션 전체 상한 60%
MAX_CITY_EXPOSURE_FRACTION  = 0.20     # 도시별 집중도 상한 20%
```

### 실제 사이징 예시 (자본금 $200 기준)

켈리 공식: `f = FRACTIONAL_KELLY × (p_adj - p_exec) / (1 - p_exec)`
`p_adj`는 gamma=0.65 확률 축소 적용값 (과신 방지)

```
모델 p=0.75, 시장가 0.60 → 베팅 ~$7   (보통 신호)
모델 p=0.85, 시장가 0.60 → 베팅 ~$11  (강한 신호)
모델 p=0.90, 시장가 0.60 → 베팅 ~$20  (아주 강한 신호)
모델 p=0.95, 시장가 0.60 → 베팅 ~$27  (최강 신호)
단일 이벤트 상한: $40 (ENTRY_FRACTION=0.20 × $200)
```

### 왜 1/4 켈리인가

- 순수 켈리는 모델이 완벽할 때만 최적. GFS 날씨 예보는 오차가 있음.
- gamma=0.65 확률 축소 + 0.25 배율 = 실제 베팅은 순수 켈리의 약 16% 수준.
- 연속 손실 시나리오에서 자본금 보존 우선.

### 왜 MAX_TOTAL_EXPOSURE=0.60인가

- 사용자 요청은 90%였으나, 유럽 폭염 등 지역 동조 리스크로 동시 포지션이
  모두 틀릴 수 있음. 60%는 최악의 경우 자본금 40%는 보존.
- 30%였던 이전 설정은 너무 보수적 (동시 포지션 3개 한도).

### 미래 AI에게

- 이 파라미터를 되돌리거나 변경하려면 반드시 위 근거를 읽고 사용자에게
  변경 이유와 위험을 설명할 것.
- ENTRY_FRACTION은 kelly 모드에서 단일 이벤트의 상한 캡 역할을 함.
  fixed_fraction 모드에서는 정확한 베팅 크기를 직접 결정함. 혼동 금지.
- SIZE_MODE를 fixed_fraction으로 되돌리면 ENTRY_FRACTION=0.05로도 복원 필요.


## Nowcast And Research

- Same-station nowcast is allowed only from explicitly mapped official sources.
  AWC METAR covers ICAO stations; HKO covers Hong Kong.
- Nowcast derives observed high-so-far and low-so-far from the same station-date
  response when possible.
- Real AWC METAR bulk nowcast requests must be at least 5 minutes apart. Real
  HKO max/min nowcast requests must be at least 10 minutes apart. Cache hits do
  not write request-log rows.
- `STATION_NOWCAST_CACHE_TTL_SECONDS=300` (5 min) is the recommended cache TTL.
  Keeping it equal to the AWC METAR provider floor (5 min) means held-position
  observations are fresh enough for timely exit signals. The old 900-s (15-min)
  default created up to 10-minute blind spots after a sudden threshold breach.
- The target date may be station-local today, or station-local yesterday only
  during the post-close freshness window for already-held paper exit and
  settlement-risk evidence.
- For daily-high threshold markets, `observed_high_c >= threshold_c` makes held
  YES favorable evidence and held NO a `nowcast_bucket_lock_risk` exit attempt.
  Exact and range buckets must use parsed bucket boundaries before choosing the
  held-side exit-risk treatment.
- Repeated SKIPs are research signals. Diagnose categories before changing
  thresholds, risk caps, or data-source assumptions.

## Runtime Data And Handoff Hygiene

- `docs/active/current-task.md` is the only default unfinished-work handoff
  card.
- Runtime ledgers are ignored by git. Delete or recreate them only for an
  intentional fresh paper-experiment window.
- `paper_decisions.csv` and `paper_trades.csv` are source ledgers. Reports may
  scan full history when that is the promised meaning, but must stream rows and
  keep only aggregates or bounded lookups in memory.
- `paper_raw_snapshots.jsonl` is diagnostic evidence, not a source ledger.
  Cleanup rules for raw snapshots must not be applied to `paper_state.json`,
  `paper_trades.csv`, or `paper_decisions.csv`.
- Individual market evaluation exceptions must fail closed as observable
  diagnostics: write a `SKIP_ERROR` row, write an error raw snapshot, and
  preserve runner-status error fields.
- Dashboard trade-history panels treat SKIP rows as diagnostics, not executed
  trades.
- `docs/codex/known-good-commands.md` is the command source for fresh local
  pytest, Oracle SSH, remote pytest, and dashboard checks.

## Open-Meteo Rate Limit (2026-06-10 사건 기록)

- 원인: `FORECAST_CACHE_TTL_SECONDS` 가 2400(40분)으로 잘못 설정되어 하루 36번 × 39도시
  = 1,404 호출 × 31 units = **43,524 units/day** → 일일 한도(10,000) 4배 초과.
- 수정: `FORECAST_CACHE_TTL_SECONDS=10800`(3시간)으로 정정. 이후 예상 호출량:
  39 × 8 × 31 = 9,672 units/day < 10,000.
- 함께 수정: `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60 → 15` (AGENTS.md 규칙 준수).
- 재발 방지: AGENTS.md의 예산 공식(`39 cities × 8 batches/day × 31 units`)을 변경 전
  계산기로 사용. TTL을 낮추면 batches/day가 증가한다.

## 대시보드 UI 변경 (2026-06-11 적용)

- **실패한 렌더링 버그**: `realizedTable()` 함수가 존재하지 않았음 → `realizedCards()`가 정답.
  이 오류가 `render()` 중간에 크래시를 유발해 이후 최근 체결 카드도 전부 렌더링 실패했음.
- **시간대**: `shortDateTime()` / `shortTime()` 에 `timeZone:'Asia/Seoul'` 적용 → KST 고정.
- **손익 부호**: 포지션 카드 음수 손익에 `-` 부호 추가 (`-$0.84` 형태).
- **현재가 색상**: `badge.current-price` (파란색) 적용.
- **데이터 없음 표시**: 예보/확률 데이터 없는 포지션에 `예보 --`, `확률 --`, `관측소 --` badge 표시.
- **스캐너 패널 레이블**: 총 이익 → 총 손익(net), 수익 현황, 손실 현황, 매매가능현금.
- **3번째 탭 "예보&관측 호출"**: 도시별 예보/관측소 호출 기록을 스캐너 패널에서 분리,
  좌우 분할 레이아웃으로 신설 탭에 배치.
- **거래 카드**: 체결가 파란색, 진입/청산 상황별 한글 reason 파싱 (진입금액, 수수료, 엣지 등).
- **도시 예보 카드**: 공식 관측소명, 성공 시간(KST), 실패 이유 표시.
- **관측소 호출 카드**: 일괄(bulk-metar) 요청을 트리거 도시명으로 표시.
