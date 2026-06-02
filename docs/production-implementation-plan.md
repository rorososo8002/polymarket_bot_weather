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
  -> attach verified settlement-station nowcast evidence when available
  -> stream CLOB order-book updates
  -> compute executable YES/NO VWAP edge
  -> reject entries with weak expected net return after costs
  -> normalize city-date outcome probabilities and compare at most two-leg portfolios
  -> apply risk, exposure, and probability-stop rules
  -> open/close paper positions
  -> write state, market decisions, event portfolios, trades, raw snapshots, and runner heartbeat
```

Shadow research is a separate path:

```text
supported weather markets
  -> bounded public Data API trade/activity sample
  -> shadow_external_signals.jsonl
  -> timing/side/outcome comparison with paper_decisions.csv
  -> shadow_signal_report.md
```

The shadow path is never an execution input by default.

## Code Map

```text
src/weather_bot/stations.py           41-city station registry and supported count
src/weather_bot/weather_client.py     question parser backed by station coordinates
src/weather_bot/polymarket_client.py  Gamma discovery and CLOB REST book parsing
src/weather_bot/realtime_orderbook.py CLOB WebSocket order-book cache
src/weather_bot/probability.py        station-based Open-Meteo ensemble model
src/weather_bot/nowcast.py            settlement-station observed high pilot
docs/station-registry-audit.md        41-city forecast/nowcast station audit table
src/weather_bot/portfolio.py          city-date portfolio budget and complementary-leg selection
src/weather_bot/live_paper_runner.py  paper loop and realtime stream orchestration
src/weather_bot/paper.py              paper broker, logs, settlements, exits
src/weather_bot/exit_policy.py        probability stop and profit exits
src/weather_bot/dashboard.py          local read-only dashboard
src/weather_bot/shadow_signals.py     public whale/external-signal research only
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

`size_usd` is an all-in paper-entry budget. It includes the modeled entry taker
fee, so the broker buys fewer shares than `size_usd / p_exec`. Normal closes
and partial closes add only after-fee proceeds back to paper cash. Conservative
entry bankroll and dashboard market value also subtract the executable exit
fee. This keeps entry filtering, paper-wallet accounting, risk caps, and
operator-visible PnL on one cost definition.

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

## Settlement-Station Nowcast Contract

Same-day temperature decisions may use a settlement-station nowcast only when
the observation source is explicitly mapped to the same station used by
`STATION_MAP`. The bot must not substitute city-center weather, nearby airport
weather, deterministic forecast fallback, or guessed values.

Phase 5 originally started with a Seoul/RKSI pilot, then expanded after source
checks:

```text
39 ICAO stations -> Aviation Weather Center METAR API
hong kong/HKO   -> Hong Kong Observatory max/min temperature since midnight CSV
karachi/OPMR    -> forecast-only for now; AWC did not return recent OPMR METAR data
```

For ICAO airport stations, the nowcast source is the Aviation Weather Center
METAR API for the same station id used by `STATION_MAP`. For Hong Kong, the
source is the Hong Kong Observatory row in HKO's official "maximum/minimum air
temperature since midnight" CSV because HKO is not an ICAO METAR station.
Hong Kong stayed in scope because the current weather event scan showed it near
the top of active highest-temperature volume. OPMR remains forecast-only until
a same-station official observation source is verified.

Nowcast sources are cached for 15 minutes and observations older than 90 minutes
are unusable. This means a 30-minute trading loop does not hammer the source:
each station-date normally triggers at most one nowcast HTTP request per cache
window when that city's market is actually evaluated.

Each nowcast record carries:

```text
observed_high_c
observed_high_f
observed_at
high_observed_at
source
source_url
settlement_source_url
freshness_seconds
unavailable_reason
raw_observation_count
```

If the observation is fresh and verified, temperature probability notes say
`evidence=forecast-plus-nowcast` and the source becomes
`open-meteo-ensemble-station+nowcast`. If the observed high has already crossed
a threshold or made a lower/exact bucket impossible, the probability is bounded
to `1.0` or `0.0` accordingly. If the observation is missing, stale, malformed,
for a future date, or from an unmapped station, nowcast-dependent logic is
skipped and the note says `evidence=forecast-only` with
`nowcast_unavailable=<reason>`.

Unsupported observation sources are intentionally not backfilled with nearby
stations. Karachi/OPMR is the current example: the bot may still trade Karachi
from Open-Meteo station-coordinate forecasts, but it does not use same-day
observed highs until an OPMR-matching official observation source is verified.

`src/weather_bot/stations.py` also exposes `station_audit_rows()` so the station
coverage can be checked without reading Python objects by hand. Each row records
the Open-Meteo forecast source, the fact that the forecast coordinates come from
the settlement station, the candidate nowcast station, and whether that nowcast
source is enabled, still only a candidate, or needs a separate provider. The
human-readable version is `docs/station-registry-audit.md`.

Important: `provider_enabled` means the observation API is implemented for the
same station identity. `provider_unavailable` means forecast-only. The
`rule_evidence_status` field remains separate: it records whether the exact
Polymarket rule URL and station wording are stored in the repository.

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

- `Open Positions`: current open position count from paper state
- `Total Open Entry Cost`: sum of `cost_usd` for currently open positions
- `Latest Open-Meteo Forecast`: latest `created_at` in `forecast_cache.json`
- `Total Profit`: cumulative positive realized PnL from closed trade rows
- `Total Loss`: cumulative absolute negative realized PnL from closed trade rows
- `Remaining Cash`: current `cash_usd` in paper state

Below those summary rows, show three operational explanations:

- `Forecast Health`: show when a fresh Open-Meteo request was last attempted, when
  one last succeeded, how old the reusable cache is, why the latest request
  failed, and whether disk persistence failed.
- `WebSocket Health`: show whether the background receiver thread is alive, how
  many reconnections occurred, when any message last arrived, when a real
  order-book price update last arrived, how old that book is, and the latest
  stream error.
- `Event Portfolio`: show the latest city-date selection, conservative
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

Profit-taking exits first evaluate the Phase 6 principal-recovery runner policy.
For a model-target or overheated profit exit, the runner compares the current
executable sell value after the weather taker fee with the conservative
hold-to-settlement expected value. If settlement value is at least as good,
the paper broker sells a principal-recovery tranche and, if needed, extra
shares to bound the remaining runner at `SETTLEMENT_RUNNER_MAX_FRACTION`
of the current position. The default runner cap is 25%.

Once a position has an active settlement runner, later model-target or
overheated profit signals do not keep chopping it down. The runner is held
until settlement unless a safety exit fires or the fresh settlement expected
value becomes worse than the fee-adjusted sell-now value. Probability stops,
valid edge fade exits, max-hold exits, settlement resolution, and
low-liquidity limits still take precedence. If available bid depth cannot fill
the desired principal-recovery tranche, the broker sells only executable
shares, logs `low_liquidity`, and leaves the runner as pending for a later
cycle.

Each tranche decision is logged to `paper_trades.csv`: `PARTIAL_CLOSE` for the
principal-recovery sale, `HOLD_RUNNER` for the remaining runner tranche, and
`HOLD_NO_LIQUIDITY` when no executable tranche can be sold.

Evaluation failure sentinels are not exit signals. In particular, `net_edge=-999`
with no executable `p_exec` means the side could not be evaluated from the
current order book; it must not trigger an edge-faded close. This prevents churn
where a position closes on an invalid transient book update and immediately
reopens when the next valid update arrives.

## Shadow Signal Research Contract

Phase 7 studies public external signals without copy trading. The research code
uses public Polymarket Data API rows, optional manually classified public notes,
and the bot's own paper decision logs. It does not connect wallets, sign orders,
submit orders, alter open positions, or feed signals into
`live_paper_runner.py`.

Default public sources:

```text
Gamma API events/markets -> supported weather market discovery
Data API /trades         -> public wallet trade rows by condition id
Data API /activity       -> bounded user activity lookup when a wallet is intentionally studied
Data API /holders        -> top public holders for a market
```

Every stored signal records the evidence level. `observed_public_api` means the
row came from Polymarket's public API. Manually entered posts must be classified
as `evidence` or `speculation` in `shadow_public_notes.jsonl`; the report counts
them separately.

The first whale filter is intentionally simple: `SHADOW_MIN_TRADE_USDC` keeps
only public trades above a chosen notional size. A large trade is not a strategy
by itself. It is only worth studying when enough later outcomes exist to compare
the public signal against our paper decisions.

The report compares:

```text
external signal timestamp vs nearest paper decision timestamp
external implied side vs paper side
later resolved outcome vs external side
later resolved outcome vs paper side
```

When a closed Gamma market is available, the research helper can infer
`later_outcome` from binary `outcomes` and `outcomePrices` by treating the
YES/NO side priced near `1.0` as the winner.

`implied_side` translates trade direction:

```text
BUY YES  -> YES
BUY NO   -> NO
SELL YES -> NO
SELL NO  -> YES
```

The promotion rule is conservative. Report all resolved public signals for
research, but calculate promotion only from paired rows where the public signal
and a real bot `YES` or `NO` entry can both be scored. Bot `SKIP` rows remain
useful diagnostics but cannot inflate the public-signal win rate. Fewer than 20
paired resolved rows means no promotion. A public signal set must beat bot
entries on that same paired sample by at least five percentage points before
the report suggests a paper-only A/B experiment. Even then, automatic copy
trading stays out of scope.

## Runtime Defaults

```text
ORDERBOOK_STREAM_ENABLED=true
ORDERBOOK_STREAM_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
ORDERBOOK_STREAM_STALE_SECONDS=60
RUNNER_HEALTH_STATUS_INTERVAL_SECONDS=5
FORECAST_REFRESH_INTERVAL_SECONDS=1800
FORECAST_CACHE_TTL_SECONDS=1800
STATION_NOWCAST_ENABLED=true
STATION_NOWCAST_CACHE_TTL_SECONDS=900
STATION_NOWCAST_FRESHNESS_SECONDS=5400
DISCOVERY_MAX_PAGES=8
DISCOVERY_PAGE_SIZE=100
PORTFOLIO_DECISIONS_JSONL_PATH=paper_event_portfolios.jsonl
BANKROLL_USD=100
ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.06
WEATHER_TAKER_FEE_RATE=0.05
SETTLEMENT_RUNNER_ENABLED=true
SETTLEMENT_RUNNER_MAX_FRACTION=0.25
SETTLEMENT_RUNNER_MIN_EV_MARGIN_USD=0.00
POLYMARKET_DATA_BASE=https://data-api.polymarket.com
SHADOW_SIGNALS_JSONL_PATH=shadow_external_signals.jsonl
SHADOW_PUBLIC_NOTES_JSONL_PATH=shadow_public_notes.jsonl
SHADOW_REPORT_PATH=shadow_signal_report.md
SHADOW_MAX_MARKETS=100
SHADOW_MAX_TRADES_PER_MARKET=100
SHADOW_MAX_ROWS=1000
SHADOW_MIN_TRADE_USDC=100.0
SHADOW_COMPARE_WINDOW_SECONDS=86400
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
- Shadow signal research uses Polymarket's public market-data overview and Data
  API docs:
  https://docs.polymarket.com/market-data/overview,
  https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets,
  https://docs.polymarket.com/api-reference/core/get-user-activity,
  https://docs.polymarket.com/api-reference/core/get-top-holders-for-markets.
- Multi-outcome `NO` portfolio handling follows
  https://docs.polymarket.com/advanced/neg-risk.
- Correlated city-date concentration controls apply the general concentration
  risk principle described at
  https://www.finra.org/investors/insights/concentration-risk.
- Ensemble member forecasts follow https://open-meteo.com/en/docs/ensemble-api.
- Seoul settlement-station nowcast uses the same-station RKSI pilot. The
  settlement reference is
  https://www.wunderground.com/history/daily/kr/incheon/RKSI and the METAR
  observation API is https://aviationweather.gov/api/data/.
- Hong Kong uses the Hong Kong Observatory daily extract.
- Live wallet execution is intentionally absent. Future live execution work is
  tracked separately in `docs/live-trading-safety-plan.md` so it can reuse the
  completed paper strategy without mixing actual-order concerns into the paper
  upgrade phases.
