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
  -> parse weather question against supported city registry
  -> reject unmapped cities and unsupported question shapes
  -> estimate station-based weather probability
  -> stream CLOB order-book updates
  -> compute executable YES/NO VWAP edge
  -> apply risk, exposure, and probability-stop rules
  -> open/close paper positions
  -> write state, decisions, trades, raw snapshots, and runner heartbeat
```

## Code Map

```text
src/weather_bot/stations.py           41-city station registry and supported count
src/weather_bot/weather_client.py     question parser backed by station coordinates
src/weather_bot/polymarket_client.py  Gamma discovery and CLOB REST book parsing
src/weather_bot/realtime_orderbook.py CLOB WebSocket order-book cache
src/weather_bot/probability.py        station-based Open-Meteo ensemble model
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
too wide, prices were extreme, YES+NO asks were abnormal, or no executable side
could be evaluated. When both sides fail liquidity validation, the SKIP reason
includes the YES and NO rejection details so operators can see why neither side
was executable.

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

Below those summary rows, show two operational health explanations:

- `예보 상태`: show when a fresh Open-Meteo request was last attempted, when
  one last succeeded, how old the reusable cache is, why the latest request
  failed, and whether disk persistence failed.
- `WebSocket 상태`: show whether the background receiver thread is alive, how
  many reconnections occurred, when any message last arrived, when a real
  order-book price update last arrived, how old that book is, and the latest
  stream error.

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
MAX_MARKETS=41
ENABLE_PRECIPITATION_MARKETS=false
REQUIRE_DATE_HINT_FOR_TRADE=true
```

`MAX_MARKETS=41` matches the current station registry. In code, the default is derived from `SUPPORTED_CITY_COUNT`; environment files keep the explicit value so deployment config is easy to inspect.

## Verification

```powershell
$env:PYTHONPATH='src'
$env:TMP=(Resolve-Path '.pytest-tmp-all').Path
$env:TEMP=$env:TMP
python -m pytest -q
```

For a local paper service start on Windows, run:

```powershell
$env:PYTHONPATH='src'
python -m weather_bot.live_paper_runner
```

For VPS deployment, use `docs/VPS_LIVE_PAPER.md`.

## Source Notes

- Station choices come from Polymarket weather rule text and resolution sources.
- Hong Kong uses the Hong Kong Observatory daily extract.
- Live wallet execution is intentionally absent and requires a separate production-safety design.
