# Production Implementation Summary

## Goal

Run a conservative paper-trading bot for Polymarket weather markets using only verified settlement stations.

## Non-Negotiable Rules

- Trade only the 41 cities in `src/weather_bot/stations.py`.
- Use `STATION_MAP` as the single source of truth for settlement stations.
- Skip any weather market whose parsed city is not in `STATION_MAP`.
- Refresh Open-Meteo forecast data no more often than every 30 minutes by default.
- Monitor order books through the Polymarket CLOB WebSocket market stream by default.
- Keep token IDs for open positions subscribed even when discovery rolls forward to newer markets.
- Keep paper-trading behavior intact unless live execution is explicitly requested.

## Architecture

```text
Polymarket discovery
  -> fetch city-date weather events and expand their binary submarkets
  -> parse weather question against supported city registry
  -> group exact, lower-tail, and upper-tail temperature buckets by event
  -> reject unmapped cities and unsupported question shapes
  -> estimate station-based weather probability
  -> stream CLOB order-book updates
  -> compute executable YES/NO VWAP edge
  -> reject entries with weak expected net return after costs
  -> normalize city-date outcome probabilities and compare at most two-leg portfolios
  -> apply risk, exposure, and probability-stop rules
  -> open/close paper positions
  -> write state, market decisions, event portfolios, trades, raw snapshots, and runner heartbeat
```

## Code Map

```text
src/weather_bot/stations.py           41-city station registry and supported count
src/weather_bot/weather_client.py     question parser backed by station coordinates
src/weather_bot/polymarket_client.py  Gamma discovery and CLOB REST book parsing
src/weather_bot/realtime_orderbook.py CLOB WebSocket order-book cache
src/weather_bot/probability.py        station-based Open-Meteo ensemble model
src/weather_bot/portfolio.py          city-date portfolio budget and complementary-leg selection
src/weather_bot/live_paper_runner.py  paper loop and realtime stream orchestration
src/weather_bot/paper.py              paper broker, logs, settlements, exits
src/weather_bot/exit_policy.py        probability stop and profit exits
src/weather_bot/dashboard.py          local read-only dashboard
```

## Strategy Contract

The bot should be understandable and reproducible from this repository alone.
When changing strategy, update this document, `docs/production-progress.md`, and
`docs/production-decisions.md` so a fresh AI can rebuild the same behavior.

Strategy changes must be framed as research-backed hypotheses. Use expected
value, probability calibration, fractional Kelly sizing, liquidity/slippage,
drawdown control, and empirical paper-trade review when deciding whether a rule
should change. The objective is to increase risk-adjusted paper returns while
keeping unknown or unverifiable situations fail-closed.

## Planned Phases

The ordered upgrade phases live in `docs/strategy-upgrade-roadmap.md`. Complete
one phase per fresh chat so each strategy change remains reviewable.

- Phase 0: preserve and verify the existing baseline
- Phase 1: expose forecast freshness and WebSocket health
- Phase 2: reject entries with weak executable net return after costs
- Phase 3: support exact temperature buckets and event-based discovery
- Phase 4: select city-date portfolios without multiplying correlated risk
- Phase 5: add verified settlement-station nowcasts
- Phase 6: recover principal while retaining a limited settlement runner
- Phase 7: research public whale and external signals in shadow mode only

## Decision Event Contract

Every order-book update can produce a decision log:

```text
DECISION YES   latest executable YES edge is above the configured threshold
DECISION NO    latest executable NO edge is above the configured threshold
DECISION SKIP  no trade should open from this update
```

`DECISION YES` and `DECISION NO` are model/order-book judgments, not guaranteed
opens. A position may still be blocked by existing exposure, same-market hedge
protection, missing token ids, or risk caps. `DECISION SKIP` means the bot saw
the market but refused to open a trade because the edge was too small, confidence
was too low, parsing/date safety failed, liquidity was inadequate, the spread was
too wide, the expected net return after costs was too small, prices were
invalid, YES+NO asks were abnormal, or no executable side could be evaluated.
When both sides fail liquidity validation, the SKIP reason includes the YES and
NO rejection details so operators can see why neither side was executable.

## Entry Net-Return Contract

An entry must pass both the existing model `net_edge` condition and a separate
executable expected-net-return condition. The default paper hypothesis is:

```text
ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.06
WEATHER_TAKER_FEE_RATE=0.05
```

The weather taker fee follows the official Polymarket formula:

```text
fee_usdc = shares * fee_rate * price * (1 - price)
```

`p_exec` is the entry ask-side VWAP, so it already contains the entry spread and
entry slippage. Do not subtract those costs a second time. For an expected early
exit, use the current spread and observed slippage as a conservative future
exit haircut and calculate the exit taker fee at the estimated executable exit
price.

A high entry price is not banned by itself. The runner also evaluates a
hold-to-settlement route using the model probability after model-error and
resolution-error margins. That route has no order-book exit haircut and no exit
taker fee. It may pass only when its conservative expected net return still
meets the same 6% threshold.

Decision-log reasons include the chosen route, expected exit value, expected
gross profit, estimated total cost, expected net-return rate, entry fee, exit
fee, future exit-market cost, spread, slippage, and the rejection reason when
the threshold is not met.

## Temperature Bucket And Discovery Contract

A Polymarket weather `event` is one city-date question, such as Seoul's highest
temperature on May 25. A `market` is one tradable YES/NO result inside that
event. One temperature event can contain many binary markets:

```text
18°C or below
19°C
20°C
...
27°C
28°C or higher
```

The parser distinguishes four shapes:

- `threshold`: a standalone condition such as `NYC reaches 90°F`
- `exact`: one displayed bucket such as `26°C`
- `lower_tail`: the lowest displayed range such as `18°C or below`
- `upper_tail`: the highest displayed range such as `28°C or higher`

For one-degree Celsius buckets, the ensemble model assigns continuous forecast
scenarios to non-overlapping intervals:

```text
26°C exact       -> 25.5°C <= forecast < 26.5°C
18°C or below    -> forecast < 18.5°C
28°C or higher   -> forecast >= 27.5°C
```

Using shared boundaries means the probabilities for every bucket in one event
sum to 100%. Discovery fetches events, keeps every supported binary market
inside every supported weather-category event it finds, groups the markets
before evaluation, and reports actual event, city, market, and token coverage.
The 41-city `STATION_MAP` remains the settlement-station allowlist. It is not an
event-count cutoff.

## City-Date Portfolio Contract

The runner selects entries at the city-date event level. Nearby weather buckets
are correlated outcomes, not independent bets. A Seoul `26°C YES` entry must
not multiply the budget available to a Seoul `27°C YES` entry on the same date.

Before opening a new event portfolio, calculate:

```text
cost_basis_bankroll = cash + open-position entry cost
liquidation_bankroll = cash + executable sell value of every open position
entry_bankroll = min(cost_basis_bankroll, liquidation_bankroll)
```

Unrealized profits do not increase new-entry sizing. Executable unrealized
losses reduce sizing immediately. If any held position cannot be valued from a
usable order book, new entries fail closed until safe valuation is available.

Initial paper defaults:

```text
ENTRY_FRACTION=0.10
MAX_SINGLE_MARKET_FRACTION=0.10
MIN_ORDER_USD=10.00
MAX_CITY_EXPOSURE_FRACTION=0.20
MAX_EVENT_DATE_EXPOSURE_FRACTION=0.10
LARGE_BANKROLL_EVENT_DATE_EXPOSURE_FRACTION=0.05
EVENT_DATE_EXPOSURE_TRANSITION_USD=1000
MAX_EVENT_PORTFOLIO_LEGS=2
MAX_TOTAL_EXPOSURE_FRACTION=0.90
```

Below `$1,000`, at most two selected legs share one city-date budget of 10%.
At `$1,000` or more, the shared city-date budget shrinks to 5%. Every opened
leg must be at least `$10`. A strong single leg may use the full event budget.
This means a `$100` account can open one `$10` leg for an event, while a `$200`
account can use two `$10` legs when the portfolio calculation prefers them.

For one city-date event, normalize the bucket probabilities to 100% before
portfolio scoring. Compare one-leg and at-most-two-leg combinations across
different non-overlapping temperature buckets:

```text
YES + YES
YES + NO
NO + NO
```

The optimizer builds a payout table for every possible final temperature,
includes entry taker fees, and chooses the positive-cost-adjusted candidate
with the highest expected logarithmic bankroll growth. A `NO` leg is not an
independent trade: it wins in every scenario except its own bucket. Same-market
`YES + NO`, overlapping threshold positions, and third legs remain blocked.
Different dates for one city share a separate 20% city cap. Total open
positions remain paper-only and are capped at 90%, leaving at least 10% cash.

Each event evaluation appends one bounded JSONL record to
`paper_event_portfolios.jsonl`. It records the reference bankroll, cap,
existing and selected exposure, selected legs, rejected legs, expected net
profit, expected log growth, normalized scenario probabilities, and scenario
PnL.

## Dashboard Contract

The dashboard is a dark Polymarket-style operator surface. Its detailed build
contract lives in `docs/dashboard-build-spec.md`.

The visible right-side Scanner Intelligence panel must stay focused on current
operator decisions:

- `오픈 포지션`: current open position count from paper state
- `총 오픈 진입금액`: sum of `cost_usd` for currently open positions
- `Open-Meteo 최근 예보`: latest `created_at` in `forecast_cache.json`
- `총 수익금`: cumulative positive realized PnL from closed trade rows
- `총 손실금`: cumulative absolute negative realized PnL from closed trade rows
- `남은 현금`: current `cash_usd` in paper state

Below those summary rows, show three operational explanations:

- `예보 상태`: show when a fresh Open-Meteo request was last attempted, when
  one last succeeded, how old the reusable cache is, why the latest request
  failed, and whether disk persistence failed.
- `WebSocket 상태`: show whether the background receiver thread is alive, how
  many reconnections occurred, when any message last arrived, when a real
  order-book price update last arrived, how old that book is, and the latest
  stream error.
- `이벤트 포트폴리오`: show the latest city-date selection, conservative
  reference bankroll, shared cap, selected legs, rejected legs, and worst
  logged scenario PnL. Also show expected net profit and expected log growth.
  Explain the 10%-below-`$1,000`, 5%-from-`$1,000`, minimum-`$10`, city-20%,
  total-open-90%, maximum-two-leg, and distinct-bucket `YES+NO`/`NO+NO` rules.

Do not show cumulative candidate-judgment, forecast-unavailable, actual-open,
or YES/NO decision counters in the UI. Decision and trade totals can remain
internally cached for diagnostics, but the operator panel should not imply that
candidate signals are the same thing as actual open trades.

## Exit Policy Contract

Open positions are closed only by these rules:

- probability stop: current side probability drops below the entry-time
  `probability_stop_threshold`
- model-target take profit: executable exit VWAP reaches the model-derived
  `target_exit_price` and profit is at least `MIN_PROFIT_PCT`
- overheated take profit: market price trades above conservative model fair value
  by `OVERHEAT_MARGIN`
- edge-faded exit: a fresh executable edge for the held side is at or below
  `EXIT_NET_EDGE`, while loss is no worse than `EDGE_FADE_MAX_LOSS_PCT`
- max holding time: holding duration reaches `MAX_HOLDING_HOURS`

Evaluation failure sentinels are not exit signals. In particular, `net_edge=-999`
with no executable `p_exec` means the side could not be evaluated from the
current order book; it must not trigger an edge-faded close. This prevents churn
where a position closes on an invalid transient book update and immediately
reopens when the next valid update arrives.

## Runtime Defaults

```text
ORDERBOOK_STREAM_ENABLED=true
ORDERBOOK_STREAM_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
ORDERBOOK_STREAM_STALE_SECONDS=60
RUNNER_HEALTH_STATUS_INTERVAL_SECONDS=5
FORECAST_REFRESH_INTERVAL_SECONDS=1800
FORECAST_CACHE_TTL_SECONDS=1800
DISCOVERY_MAX_PAGES=8
DISCOVERY_PAGE_SIZE=100
PORTFOLIO_DECISIONS_JSONL_PATH=paper_event_portfolios.jsonl
BANKROLL_USD=100
ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.06
WEATHER_TAKER_FEE_RATE=0.05
MAX_EVENT_DATE_EXPOSURE_FRACTION=0.10
LARGE_BANKROLL_EVENT_DATE_EXPOSURE_FRACTION=0.05
EVENT_DATE_EXPOSURE_TRANSITION_USD=1000
MAX_EVENT_PORTFOLIO_LEGS=2
MIN_ORDER_USD=10.00
ENTRY_FRACTION=0.10
MAX_SINGLE_MARKET_FRACTION=0.10
MAX_CITY_EXPOSURE_FRACTION=0.20
MAX_TOTAL_EXPOSURE_FRACTION=0.90
ENABLE_PRECIPITATION_MARKETS=false
REQUIRE_DATE_HINT_FOR_TRADE=true
```

`DISCOVERY_MAX_PAGES=8` and `DISCOVERY_PAGE_SIZE=100` are fallback API
pagination safety controls. If Polymarket category discovery is unavailable,
the bot reads at most eight Gamma event pages with up to 100 rows per page. This
prevents an endless request loop. These values do not reduce the verified
41-city allowlist and do not cap normal category discovery at 41 events.

## Verification

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m pytest -q
```

The root `conftest.py` automatically uses a process-specific directory under
`.pytest-tmp/`. Routine verified commands live in
`docs/codex/known-good-commands.md`; use them before inventing variants.

For a local paper service start on Windows, run:

```powershell
$env:PYTHONPATH='src'
python -m weather_bot.live_paper_runner
```

For VPS deployment, use `docs/VPS_LIVE_PAPER.md`.

## Source Notes

- Station choices come from Polymarket weather rule text and resolution sources.
- Weather taker-fee defaults follow https://docs.polymarket.com/trading/fees.
- Event-based discovery follows
  https://docs.polymarket.com/quickstart/fetching-data.
- Multi-outcome `NO` portfolio handling follows
  https://docs.polymarket.com/advanced/neg-risk.
- Correlated city-date concentration controls apply the general concentration
  risk principle described at
  https://www.finra.org/investors/insights/concentration-risk.
- Ensemble member forecasts follow https://open-meteo.com/en/docs/ensemble-api.
- Hong Kong uses the Hong Kong Observatory daily extract.
- Live wallet execution is intentionally absent. Future live execution work is
  tracked separately in `docs/live-trading-safety-plan.md` so it can reuse the
  completed paper strategy without mixing actual-order concerns into the paper
  upgrade phases.
