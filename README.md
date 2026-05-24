# Polymarket Weather Bot — Live Paper Trading v3

이 프로젝트는 **폴리마켓 날씨 시장을 실제 데이터로 스캔하고, 실제 주문은 넣지 않고, 로컬에서 가상매매(paper trading)를 수행하는 봇**입니다.

핵심 목표는 이것입니다.

```text
실제 Polymarket 날씨 후보 조회
→ 실제 YES/NO 오더북 조회
→ 실제 Open-Meteo 예보 조회
→ 내 모델 확률 P_model 계산
→ 시장 체결가와 비교
→ 엣지가 충분하면 원금의 5% 가상 진입
→ -10% 손절 / 모델 적정가 기반 익절 / 과열 익절 / 엣지 소멸 청산
→ paper_trades.csv, paper_decisions.csv, paper_state.json에 이유까지 기록
```

이 버전은 **private key를 요구하지 않으며 실제 주문을 보내지 않습니다.**

---

## 1. 설치

```bash
cd polymarket_weather_bot_livepaper
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

pip install -e .
copy .env.example .env
```

실행:

```bash
live-paper-bot
```

중지:

```bash
Ctrl + C
```

테스트:

```bash
pytest -q
```

---

## 2. 파일 구조

```text
src/weather_bot/config.py             설정값 / .env 로딩
src/weather_bot/models.py             공통 데이터 구조
src/weather_bot/polymarket_client.py  Polymarket Gamma + CLOB 공개 데이터 조회
src/weather_bot/weather_client.py     도시/질문 파싱 + Open-Meteo 호출
src/weather_bot/probability.py        날씨 확률 P_model 계산
src/weather_bot/edge.py               YES/NO 순엣지 계산
src/weather_bot/risk.py               Kelly 선택 시 확률 shrink + sizing
src/weather_bot/exit_policy.py        진입계획 / 손절 / 익절 / 과열 / 엣지소멸 청산
src/weather_bot/paper.py              로컬 paper broker, CSV/state 저장
src/weather_bot/live_paper_runner.py  메인 실행 루프
```

불필요한 옛날 단발 scanner는 제거했습니다. 메인 명령어는 `live-paper-bot` 하나입니다.

---

## 3. 전략식 구현 계약

다른 AI나 개발자가 봐도 같은 구조로 구현하려면 이 부분만 그대로 지키면 됩니다.

### 3-1. 시장 해석

폴리마켓 날씨 시장 하나를 이진 옵션으로 봅니다.

```text
YES = 이벤트 발생 시 1달러, 아니면 0달러
NO  = 이벤트 미발생 시 1달러, 아니면 0달러
```

예시:

```text
시장: 오늘 Seoul 최고기온은 21도 이상인가?
YES: 21도 이상
NO: 21도 미만 또는 이하 조건 반대편
```

현재 parser는 전세계 주요 도시 일부와 Fahrenheit/Celsius를 지원합니다.

예:

```text
Seoul 21C / 21°C / 21도 → 내부에서는 69.8F로 변환
NYC 90F / 90°F → 그대로 90F
```

---

## 4. 진입 조건

봇은 YES와 NO를 모두 평가합니다.

### YES 순엣지

```text
net_edge_yes = P_model_yes - P_exec_yes - fee - slippage - model_margin - resolution_margin
```

### NO 순엣지

```text
net_edge_no = (1 - P_model_yes) - P_exec_no - fee - slippage - model_margin - resolution_margin
```

여기서:

```text
P_model_yes       = 내 날씨 모델이 계산한 YES 발생 확률
P_exec_yes/no     = 실제 오더북을 먹었을 때의 VWAP 체결가
fee               = 수수료 추정값
slippage          = best ask 대비 VWAP 상승분
model_margin      = 예보 모델 오차 안전마진, 기본 0.03
resolution_margin = 정산 기준 오해/관측소 차이 안전마진, 기본 0.01
```

진입 조건:

```text
net_edge > MIN_NET_EDGE
```

기본값:

```text
MIN_NET_EDGE=0.05
```

즉, 비용과 안전마진을 빼고도 5%p 이상 유리할 때만 진입합니다.

---

## 5. 진입 금액

기본은 고정 비중입니다.

```text
entry_usd = 진입 직전 총 원금 × ENTRY_FRACTION
```

기본값:

```text
ENTRY_FRACTION=0.05
```

예:

```text
진입 직전 총 원금 = $100
진입 비중 = 5%
가상 진입 금액 = $5
```

중요: v3에서는 `settings.bankroll_usd` 고정값이 아니라 **진입 직전 현재 bankroll**을 기준으로 5%를 계산합니다.

현재 bankroll 계산:

```text
current_bankroll = cash_usd + open_position_cost_basis
```

`realized_pnl_usd`는 이미 cash에 반영되므로 중복해서 더하지 않습니다.

---

## 6. 손절 기준

손절은 단순하고 고정입니다.

```text
stop_loss_price = entry_price × (1 - STOP_LOSS_PCT)
```

기본값:

```text
STOP_LOSS_PCT=0.10
```

예:

```text
진입가 = 0.50
손절가 = 0.50 × 0.90 = 0.45
현재 bid <= 0.45 → 가상 손절
```

손절 사유는 `paper_trades.csv`에 다음처럼 남습니다.

```text
stop loss: mark 0.4500 <= stop 0.4500 (-10.0%)
```

---

## 7. 수익화 기준

이 봇은 `+5%`, `+10%` 같은 고정 익절이 아닙니다.

수익화는 **모델 적정가** 기준입니다.

### 7-1. 모델 적정가

YES일 때:

```text
model_fair_price = P_model_yes - fee - model_margin - resolution_margin
```

NO일 때:

```text
model_fair_price = (1 - P_model_yes) - fee - model_margin - resolution_margin
```

예:

```text
P_model_yes = 0.68
fee = 0.00
model_margin = 0.03
resolution_margin = 0.01
model_fair_price = 0.64
```

### 7-2. 모델 기반 익절 목표

```text
target_exit_price = entry_price + TAKE_PROFIT_TO_FAIR_RATIO × (model_fair_price - entry_price)
```

기본값:

```text
TAKE_PROFIT_TO_FAIR_RATIO=0.70
```

예:

```text
진입가 = 0.52
모델 적정가 = 0.64
차이 = 0.12
70%만 먹기 = 0.084
익절 목표 = 0.604
```

즉, 모델 적정가 0.64까지 완전히 기다리지 않고, 70% 정도 도달하면 수익화합니다.

청산 조건:

```text
current_bid >= target_exit_price
and pnl_pct >= MIN_PROFIT_PCT
```

기본값:

```text
MIN_PROFIT_PCT=0.03
```

---

## 8. 과열 수익화

시장이 내 모델 적정가보다 비싸지면 더 들고 있을 이유가 없습니다.

```text
market_heat_score = (current_bid - model_fair_price) / model_fair_price
```

```text
current_bid >= model_fair_price + OVERHEAT_MARGIN
and pnl_pct > 0
→ 과열 수익화
```

기본값:

```text
OVERHEAT_MARGIN=0.02
```

예:

```text
모델 적정가 = 0.64
과열 기준 = 0.66
현재 bid = 0.67
→ overheated vs model fair 사유로 수익화
```

---

## 9. 엣지 소멸 청산

진입 후 예보가 바뀌면 처음의 우위가 사라질 수 있습니다.

```text
latest_edge <= EXIT_NET_EDGE
and pnl_pct >= -EDGE_FADE_MAX_LOSS_PCT
→ 엣지 소멸 청산
```

기본값:

```text
EXIT_NET_EDGE=0.00
EDGE_FADE_MAX_LOSS_PCT=0.02
```

뜻:

```text
더 이상 양의 엣지가 없고,
손실이 -2%보다 크지 않으면 빠져나온다.
큰 손실 중이면 edge fade로 던지지 않고 stop loss가 처리한다.
```

---

## 10. 보유 시간 제한

```text
holding_hours >= MAX_HOLDING_HOURS
→ 시간 초과 청산
```

기본값:

```text
MAX_HOLDING_HOURS=96
```

---

## 11. 서울 21도 예시

시장:

```text
오늘 Seoul 최고기온은 21도 이상인가?
```

가정:

```text
내 모델 YES 확률 P_model_yes = 0.68
YES 실제 체결가 P_exec_yes = 0.52
fee = 0.00
slippage = 0.00
model_margin = 0.03
resolution_margin = 0.01
MIN_NET_EDGE = 0.05
진입 직전 총 원금 = $100
ENTRY_FRACTION = 0.05
STOP_LOSS_PCT = 0.10
TAKE_PROFIT_TO_FAIR_RATIO = 0.70
```

진입 엣지:

```text
net_edge_yes = 0.68 - 0.52 - 0.00 - 0.00 - 0.03 - 0.01
             = 0.12
```

```text
0.12 > 0.05 → YES 진입
```

진입 금액:

```text
$100 × 5% = $5
```

손절:

```text
0.52 × 0.90 = 0.468
```

모델 적정가:

```text
0.68 - 0.03 - 0.01 = 0.64
```

익절 목표:

```text
0.52 + 0.70 × (0.64 - 0.52)
= 0.604
```

로그 예시:

```text
entry: model_p=0.680, side=YES, p_exec=0.5200, net_edge=0.1200, bankroll=$100.00, entry_fraction=5.00%, stop=0.4680, model_fair=0.6400, target_exit=0.6040, heat=-18.75%
```

청산 사유 예시:

```text
take profit: market reached model target 0.6040
take profit: overheated vs model fair 0.6400, heat=4.2%
edge faded: latest_edge=-0.0031, pnl=2.1%
stop loss: mark 0.4680 <= stop 0.4680 (-10.0%)
```

---

## 12. 결과 파일

### paper_decisions.csv

시장별 매 스캔 판단입니다.

주요 컬럼:

```text
ts
question
side
p_true
p_exec
net_edge
size_usd
entry_fraction
stop_loss_price
model_fair_price
target_exit_price
market_heat_score
reason
note
```

### paper_trades.csv

실제 가상 진입/청산 기록입니다.

```text
OPEN  = 가상 진입
CLOSE = 가상 청산
SKIP_EXPOSURE_CAP = 총 노출 제한으로 진입 안 함
SKIP_CASH = 현금 부족으로 진입 안 함
```

### paper_state.json

현재 가상 계좌 상태입니다.

```text
cash_usd
realized_pnl_usd
positions[]
```

각 포지션 metadata에는 다음이 저장됩니다.

```text
entry_edge
entry_p_true
bankroll_before
entry_fraction
stop_loss_price
model_fair_price
target_exit_price
market_heat_score
entry_rationale
last_exit_assessment
```

---

## 13. 기본 .env 값

```text
BANKROLL_USD=1000
ENTRY_FRACTION=0.05
STOP_LOSS_PCT=0.10
MIN_NET_EDGE=0.05
MODEL_ERROR_MARGIN=0.03
RESOLUTION_ERROR_MARGIN=0.01
TAKE_PROFIT_TO_FAIR_RATIO=0.70
OVERHEAT_MARGIN=0.02
MAX_TOTAL_EXPOSURE_FRACTION=0.30
```

---

## 14. 현재 한계

이 봇은 실제 데이터를 쓰는 paper trading 구조입니다. 다만 확률 모델은 아직 가볍습니다.

현재:

```text
Open-Meteo forecast
+ 간단한 질문 parser
+ 정규분포 기반 forecast error approximation
```

아직 아님:

```text
정확한 Polymarket 정산 관측소 자동 매핑
GEFS/ECMWF 앙상블 확률
과거 Polymarket 가격 + 과거 예보 백테스트
실제 주문 실행 LiveBroker
```

하지만 이 구조는 의도적으로 분리되어 있습니다.

```text
나중에 probability.py / weather_client.py만 강화하면
edge.py, exit_policy.py, paper.py, live_paper_runner.py는 그대로 유지 가능
```

즉, 지금은 **전략식·진입/청산/로그 구조를 고정한 뒤, 모델 확률만 계속 개선하는 구조**입니다.

---

## 15. probability.py v4 — 앙상블/관측소/동적 sigma 모델

기존 v3의 `probability.py`는 Open-Meteo 단일 daily forecast 값 하나와 고정 `sigma=4.5F`를 사용했습니다. v4 교체본은 다음 순서로 `P_model_yes`를 계산합니다.

```text
1. Polymarket 질문 파싱: 도시, threshold, >= / <=, 날짜 힌트
2. 도시 좌표 대신 STATION_MAP의 공식 관측소 좌표 선택
3. Open-Meteo Ensemble API에서 daily temperature_2m_max/min 멤버들을 조회
4. 각 앙상블 멤버를 하나의 시나리오로 보고 threshold 충족 여부 투표
5. 관측소별 bias를 빼서 forecast를 보정
6. 앙상블 spread + lead time으로 dynamic_sigma_f 계산
7. P_yes = 0.70 * ensemble_vote + 0.30 * NormalCDF(mean, dynamic_sigma)
8. 기존 edge/risk/exit 로직에 동일한 WeatherSignal 형태로 전달
```

### 핵심 공식

```text
corrected_member_i = raw_member_i - station_bias_f

p_vote = count(corrected_member_i >= threshold_f) / N          # >= 시장
p_vote = count(corrected_member_i <= threshold_f) / N          # <= 시장

spread = stddev(corrected_members)
lead_component = 0.85 + 0.45 * sqrt(days_to_event)
sigma_dynamic = clamp(sqrt(spread^2 + lead_component^2), 1.25F, 8.0F)

p_cdf_ge = 1 - Phi((threshold_f - mean(corrected_members)) / sigma_dynamic)
p_cdf_le = Phi((threshold_f - mean(corrected_members)) / sigma_dynamic)

P_model_yes = 0.70 * p_vote + 0.30 * p_cdf
```

### 실전 전 필수 튜닝

- `STATION_MAP`: Polymarket resolution rules에 적힌 정산 관측소와 다르면 반드시 수정합니다.
- `OPEN_METEO_ENSEMBLE_MODELS`: 기본값은 `gfs_seamless,ecmwf_ifs025`입니다. Open-Meteo 모델 ID가 바뀌거나 지역별로 더 좋은 모델이 있으면 `.env`에서 덮어씁니다.
- `WEATHER_BIAS_JSON`: 관측소별 예보 편향을 저장하는 JSON 파일 경로입니다. 예시는 다음과 같습니다.

```json
{
  "USW00094728": {"temperature_2m_max": 1.1, "temperature_2m_min": -0.4},
  "KMA-108": {"temperature_2m_max": 0.8, "temperature_2m_min": 0.2}
}
```

bias 값은 `raw forecast - actual observation` 기준입니다. 예보가 실제보다 보통 1.1F 높으면 `1.1`을 넣고, 코드에서 forecast에서 1.1F를 뺍니다.

### 주의

이 모델은 수익을 보장하지 않습니다. 실제 투입 전에는 최소 30~90일치 시장/예보/정산 데이터를 저장해서 Brier score, calibration curve, edge bucket별 PnL을 확인해야 합니다. 특히 precipitation 시장은 아직 앙상블 확률 모델로 고도화하지 않았으므로 실제 자금 투입 대상에서 제외하는 것이 안전합니다.
