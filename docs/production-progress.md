# Production Progress

## Completed

- This is a live-data paper-trading service. It does not send real wallet
  orders, connect private keys, or enable live trading.
- Completed strategy hardening is implemented and locally verified: baseline
  hardening, forecast/WebSocket health, fee-aware entry filtering, exact
  weather-event discovery, city-date portfolio selection, same-station nowcast,
  settlement runners, and shadow public-signal research.
- Current strategy guardrails are active: register only the 41 `STATION_MAP`
  cities, execute paper trading only for the 40 `TRADING_READY_STATION_MAP`
  cities, drip-feed real Open-Meteo forecast HTTP calls one at a time with a
  default 60-second post-finish gap, use the Polymarket CLOB WebSocket stream,
  keep held token IDs subscribed, fail closed on stale or unsupported data, and
  preserve paper-only execution.
- New paper-entry discovery now excludes inactive or closed Polymarket markets
  from both `/events` and weather category-slug expansion. Closed markets
  remain usable only as settlement evidence for already-held paper positions.
- The paper strategy is now temperature-only. Rain, snow, precipitation, wind,
  humidity, and other non-temperature weather markets are marked unsupported by
  the parser and excluded before forecast probability calculation, order-book
  subscription, or paper trade logging. Open-Meteo requests use only
  temperature daily variables, the realtime stream subscribes only
  temperature discovery tokens, and example environment files no longer expose
  removed non-temperature market toggles.
- Station evidence is now gated separately from station registration: 40 cities
  have stored official Polymarket rule evidence and are trading-ready; Karachi
  is excluded because its found rule source conflicts with the current station
  code.
- Temperature nowcast uses same-station observed extrema: observed high-so-far
  for daily-high markets and observed low-so-far for daily-low markets. METAR
  and HKO providers derive high/low from one station-date response and cache it.
  A just-ended local-yesterday target date may use fresh same-station extrema
  during the post-close freshness window for paper exit and settlement-risk
  evidence only.
- Realtime paper evaluation now refreshes same-station nowcast-backed
  `WeatherSignal` values on the station nowcast cache TTL inside the larger
  stream cycle, while real Open-Meteo forecast HTTP calls still go through the
  forecast client cache and global 60-second request throttle.
- Realtime held-position exit evidence now refreshes from the latest
  `WeatherSignal` even when the conservative new-entry bankroll is unusable.
  New entries still fail closed, but already-held paper positions can receive
  fresh probability-stop evidence. Local verification:
  `tests/test_realtime_runner.py` passed with `19 passed`.
- Realtime order-book cache treats `best_bid_ask` as indicative best-price
  reference only. Executable bid/ask depth comes only from `book` snapshots or
  `price_change` level updates.
- `OrderBook.best_bid` and `OrderBook.best_ask` now return only executable
  positive-size depth from `bids`/`asks`; indicative `best_bid_ask` prices stay
  available only as reference fields for display or diagnostics.
- Realtime WebSocket and REST CLOB order-book parsing now share defensive
  numeric guards. Malformed, non-finite, out-of-range, zero-size, or negative
  executable `price`/`size` levels are ignored; malformed whole WebSocket
  snapshot shapes fail closed without replacing the current executable book.
- Paper analysis and shadow research reports now stream
  `paper_decisions.csv` and `paper_trades.csv` rows instead of materializing
  whole CSV files. The default report meaning remains full-history; only the
  memory posture changed.
- Paper analysis Brier scoring now prefers structured `OPEN` entry
  probabilities from `paper_trades.csv` (`entry_p_true`) and falls back to the
  latest entry decision only for legacy trade CSVs without the new columns.
- Dashboard trade panels separate actual paper trade actions from high-volume
  SKIP diagnostics. `Recent Trades`, realized rows, and realized equity points
  use cached paper-action rows so late SKIP bursts do not hide older closed
  trades. `ADD` add-on rows count as recent paper-trade activity, while realized
  PnL remains limited to close/settlement actions.
- Dashboard scanner totals now disclose whether decision counts are exact
  full-ledger totals or bounded recent-tail totals, so large
  `paper_decisions.csv` protection cannot be mistaken for all-time cumulative
  scanner history.
- Dashboard operator labels are Korean, the right rail uses tabs for
  `스캐너 정보` and `최근 체결`, realized PnL sorts by parsed close time newest
  first, and open-position Polymarket links target the event slug rather than
  condition-specific market slugs. Deployed to the Oracle VPS on 2026-06-03 as
  commit `a52f3bb`.
- Closed binary markets can settle paper positions from exact Polymarket
  `outcomePrices` only when YES/NO prices are provably `1/0` or `0/1`.
  Ambiguous closed markets remain open until a clear winner is available.
- The realtime WebSocket runner now applies resolved settlement before starting
  a stream cycle and removes newly settled old markets from the stream token
  set, matching the batch `run_cycle()` settlement behavior.
- The realtime WebSocket runner now records refresh-cycle exceptions in
  `paper_runner_status.json` with `phase=error`, a concrete message, and the
  failed phase before backing off and retrying. Local verification: focused
  `tests/test_realtime_runner.py` passed with `12 passed`; full `pytest -q`
  passed with `379 passed`.
- The realtime WebSocket receiver now only marks updated token/event work for
  evaluation instead of running strategy evaluation and writing
  `paper_decisions.csv` inline. A bounded coalescer worker merges short bursts
  by weather event, then runs paper-only strategy evaluation and exposes queue
  depth, coalesced update count, dropped update count, and worker errors in
  `paper_runner_status.json`. Local verification: focused realtime/coalescer
  tests passed with `31 passed`; full `pytest -q` passed with `383 passed`.
- Paper accounting is fee-aware end to end: `size_usd` is the all-in entry
  budget, closes add after-fee proceeds, and dashboard/liquidation values use
  after-exit-fee marks.
- Exit triggers are fee-aware end to end: model-target profit, overheated
  profit, and edge-faded loss limits compare after-fee liquidation PnL against
  configured thresholds instead of raw token-price movement.
- `FORECAST_REQUEST_MIN_INTERVAL_SECONDS` now fails startup below 60 seconds,
  matching the Open-Meteo drip-feed contract instead of allowing a typo to
  weaken the budget guard.
- Paper state and accounting-journal atomic replacement now retries short
  transient Windows `PermissionError` file locks while still failing closed on
  persistent ledger-write failures.
- Paper account state is persisted with a temp-file write followed by
  `os.replace`, and existing corrupt, structurally invalid, or position-field
  invalid `paper_state.json` fails closed instead of starting from a fresh
  default account.
- Executed paper accounting actions now guard `paper_state.json` and
  `paper_trades.csv` with a small transaction journal. `OPEN`, `ADD`, `CLOSE`,
  and `PARTIAL_CLOSE` leave `paper_state.json.journal` behind if state saving
  or trade logging fails, and startup refuses to continue until an operator
  reconciles the account book and execution ledger. Startup also fails closed
  when executed trade rows exist without `paper_state.json`, or when an open
  state position has no matching `OPEN` row in `paper_trades.csv`.
- Existing `paper_state.json` account numbers and stats now fail closed when
  unsafe: `cash_usd` must be finite and non-negative, `realized_pnl_usd` must be
  finite, stats win/loss counts must be non-negative integers, and stats PnL
  must be finite.
- Dashboard startup fails closed on public hosts such as `0.0.0.0` or `::`
  unless `DASHBOARD_TOKEN` is at least 32 characters and not an obvious weak
  example value. Public `/api/status` rejects URL query-token authentication
  and accepts only the `X-Dashboard-Token` header; server logs still redact
  token query values if they appear.
- Boolean environment settings now accept only explicit true/false aliases.
  Unknown values raise `ValueError` at startup so safety switches such as
  `REQUIRE_DATE_HINT_FOR_TRADE` cannot be disabled by typos.
- Numeric money, risk, fee, and runtime-cadence settings now fail closed at
  `Settings` startup. Negative minimum orders, negative weather taker fees,
  weather taker fees above 1, exposure fractions above 1, zero bankroll, zero
  cache TTL, zero stream-cycle interval, and zero WebSocket stale windows
  raise operator-readable `ValueError` messages before paper trading can start.
- `SIZE_MODE` now fails closed at `Settings` startup. Only `fixed_fraction`
  and `kelly` are accepted, values are normalized to lowercase, and typos such
  as `kellyy` stop startup instead of silently falling back to fixed-fraction
  paper sizing.
- Dashboard port and shadow research integer controls now fail closed at
  `Settings` startup. `DASHBOARD_PORT` must be in the TCP port range
  `1..65535`; `SHADOW_MAX_MARKETS`, `SHADOW_MAX_TRADES_PER_MARKET`, and
  `SHADOW_COMPARE_WINDOW_SECONDS` must be positive integers; and
  `SHADOW_MAX_ROWS` is a non-negative integer so `0` still means keep no
  shadow rows.
- Entry candidate `size_shares` now means the actual fee-adjusted shares bought
  with the all-in `size_usd` budget, so portfolio scenarios and broker-opened
  paper positions use the same held quantity.
- Entry liquidity sizing now probes executable ask depth with the minimum paper
  order first, computes the actual fee-aware `size_usd`, then rechecks final
  ask depth and reprices edge/expected return from the final VWAP before
  allowing a candidate.
- New-entry evaluation fails closed before expected-return math when
  `entry_bankroll <= 0` or the calculated order is below `MIN_ORDER_USD`; the
  decision logs an operator-readable SKIP instead of raising a zero-share
  exception.
- Settlement runners recover principal first, then keep a bounded 25% runner
  only when conservative settlement value beats fee-adjusted sell-now value.
  Runner logs distinguish actual held shares from target runner shares.
- Shadow research is separate from execution. Public trade rows are locally
  size-checked, deduplicated by full row identity, bounded by
  `SHADOW_MAX_ROWS`, and compared to bot entries only on paired resolved
  samples.
- Forecast target dates now require exact `daily.time` matches. If the target
  date is absent, the probability path returns `forecast-unavailable` with zero
  confidence instead of using a nearby forecast date.
- Explicit `WEATHER_BIAS_JSON` forecast-bias files now fail closed. An empty
  setting still uses neutral defaults, but a missing, unreadable, invalid JSON,
  malformed, or non-numeric explicit file returns `forecast-unavailable` with
  zero confidence instead of silently trading from uncorrected forecasts.
- Polymarket market discovery no longer trusts `clobTokenIds` list order for
  YES/NO side mapping. It maps token IDs only from explicit `tokens` or
  `outcomes` labels and skips markets when the side cannot be proven.
- Realtime order-book health now treats only `book` snapshots and
  `price_change` updates as executable depth refreshes. Indicative
  `best_bid_ask` messages keep reference prices only, stale/dead WebSocket
  health blocks new entries, held-position exit evaluation pauses with
  `HOLD_STREAM_UNHEALTHY`, and a dead receiver thread can rebuild a WebSocket
  stream without falling back to REST polling.
- Realtime order-book health now also records executable-depth freshness per
  held `token_id`. If the overall WebSocket stream is fresh but one held token
  is stale, only that position pauses with `HOLD_STREAM_UNHEALTHY`; other fresh
  token positions can still be marked or closed from their own executable
  depth. Indicative `best_bid_ask` updates still do not refresh executable
  freshness.
- Local verification after numeric Settings range validation: focused
  config/deployment pytest and full `pytest -q`. Full result: `273 passed`.
- Local verification after malformed order-book level hardening: focused
  realtime order-book/runner/edge pytest and full `pytest -q`. Full result:
  `276 passed`.
- Local verification after executable-only `OrderBook.best_bid/best_ask`
  hardening: focused realtime order-book/runner/hardening pytest passed with
  `69 passed`; full `pytest -q` passed with `307 passed`.
- Local verification after `SIZE_MODE` startup validation: focused
  `tests/test_config.py -k size_mode` passed with `2 passed`; full
  `pytest -q` passed with `373 passed`.

## In Progress

- Completed local strategy hardening includes review fixes and fee-adjusted
  paper-share consistency.
- Entry-bankroll fail-closed hardening is complete locally and remains
  paper-only.
- Station-rule evidence hardening is complete locally and remains paper-only.
- YES/NO token mapping hardening is complete locally and remains paper-only.
- Boolean config parsing hardening is complete locally and remains paper-only.
- WebSocket stale/dead order-book hardening is complete locally and remains
  paper-only.
- Token-level executable order-book freshness for held-position exit
  evaluation is complete locally and remains paper-only.
- WebSocket malformed order-book level hardening is complete locally and
  remains paper-only.
- REST CLOB malformed order-book level hardening is complete locally and
  remains paper-only.
- Executable-only `OrderBook.best_bid/best_ask` hardening is complete locally
  and remains paper-only.
- Public-dashboard token-strength and public query-token rejection hardening is
  complete locally and remains paper-only.
- Numeric Settings range validation is complete locally and remains paper-only.
- `SIZE_MODE` choice validation is complete locally and remains paper-only.
- Dashboard port and shadow integer Settings validation is complete locally and
  remains paper-only.
- Explicit forecast-bias file fail-closed hardening is complete locally and
  remains paper-only.
- Analysis/shadow-report CSV streaming is complete locally and remains
  paper-only.
- OPEN-entry Brier provenance hardening is complete locally and remains
  paper-only. Local verification: focused analyze/hardening/dashboard/shadow
  pytest passed with `101 passed`; full `pytest -q` passed with `328 passed`.
- Dashboard trade-history filtering, closed-market `outcomePrices` settlement
  fallback, and realtime pre-stream settlement are complete locally and remain
  paper-only.
- Dashboard localization, right-rail tabs, realized-PnL newest-first sorting,
  and event-slug open-position links are deployed to the Oracle VPS. Remote
  focused tests passed with `67 passed`; remote full tests passed with
  `289 passed`.
- Temperature-only market filtering is complete locally and remains paper-only:
  disabled/out-of-scope market types are marked unsupported by the parser and
  removed before probability estimation rather than being scored and skipped
  later in the runner.
- Oracle VPS runtime cleanup on 2026-06-03 UTC archived the 18GB
  `paper_raw_snapshots.jsonl` diagnostic ledger to
  `data/archive/paper_raw_snapshots.20260603T115820Z.jsonl.zst` at 136MB,
  created a fresh active raw snapshot file, installed raw-snapshot logrotate,
  and reduced root disk use from 84% to 48%.
- Oracle VPS emergency disk cleanup on 2026-06-04 UTC reduced root disk use
  from 100% to 50%. Cleanup cleared oversized `/var/log/syslog*`, removed
  rebuildable pytest caches and old tiny `/opt` deploy backups, archived the
  active 18.7GB `paper_raw_snapshots.jsonl` to
  `data/archive/paper_raw_snapshots.20260604T115423Z.jsonl.zst` at 144MB, and
  recreated a fresh writable raw snapshot file. `paper_decisions.csv` was
  preserved because it is the strategy evidence ledger.
- Oracle VPS was reset for a fresh 200 USD paper-only experiment on
  2026-06-05 UTC at the operator's request. Old paper state, decisions, trades,
  raw snapshots, request logs, archives, and the root-level
  `paper_event_portfolios.jsonl` were cleared; before service restart,
  `data/paper_state.json` was recreated with 200 USD cash and zero positions,
  and
  `PORTFOLIO_DECISIONS_JSONL_PATH` points under `data/`. Root disk use dropped
  from 100% to 14%. Remote focused pytest passed with `93 passed`; remote full
  `pytest -q` passed with `421 passed`; bot and dashboard services were
  restarted and are paper-only.
- Forecast request logging is implemented so future Open-Meteo usage reviews
  count real HTTP attempts from `forecast_request_log.jsonl` instead of trying
  to infer calls from overwritten `forecast_cache.json` entries. Rows include
  cache-miss reason plus safe city/station metadata, and the request log rotates
  at 10MB into `data/archive/` with zstd compression.
- Open-Meteo forecast-budget hardening is complete locally and remains
  paper-only. Forecast cache defaults to 40 minutes, real Open-Meteo forecast
  HTTP calls are globally serialized with a 60-second post-finish gap, and HTTP
  429 daily limit responses persist a `forecast_rate_limit_state.json` cooldown
  so later runner cycles skip new forecast HTTP calls until the recorded UTC
  reset time.
- Open-Meteo 429 classification is deployed to the Oracle VPS and remains
  paper-only.
  Daily quota 429 responses keep the next-UTC-day cooldown, while
  `Too many concurrent requests` responses persist only a 15-minute
  `concurrent` cooldown and do not permanently disable the client for the
  whole market-discovery/stream cycle. Legacy cooldown state files without `kind` are
  reclassified from their reason text, so an old concurrent 429 memo does not
  keep blocking until the next-day daily reset. Forecast request-log rows and
  health snapshots expose `rate_limit_kind`. Local verification:
  `tests/test_probability_ensemble.py` passed with `45 passed`,
  `tests/test_realtime_runner.py` passed with `18 passed`, and full
  `pytest -q` passed with `423 passed`. Remote verification on 2026-06-06 UTC:
  focused probability pytest passed with `45 passed`, realtime runner pytest
  passed with `18 passed`, `polymarket-weather-bot` restarted active with PID
  `192056`, and the old concurrent cooldown memo is no longer an effective
  block under the deployed code.
- Open-Meteo ReadTimeout duplicate-request guard is deployed to the Oracle VPS
  and remains paper-only. A `ReadTimeout` now records one real HTTP attempt, skips
  immediate tenacity retries, and places only that forecast cache key under a
  30-minute in-process temporary failure memo. Later calls for the same
  city/station/date/model key fail fast as `forecast-unavailable` without
  spending another Open-Meteo request, while different city keys can continue.
  Forecast request-log rows expose `temporary_failure_kind=read_timeout` and
  `temporary_failure_blocked_until` for the real timed-out attempt. Local
  verification: focused `tests/test_probability_ensemble.py` passed with
  `46 passed`, focused `tests/test_realtime_runner.py` passed with
  `18 passed`, and full `pytest -q` passed with `424 passed`. Remote
  verification on 2026-06-06 UTC: focused probability pytest passed with
  `46 passed`, focused realtime runner pytest passed with `18 passed`, remote
  full `pytest -q` passed with `424 passed`, `polymarket-weather-bot`
  restarted active with PID `193573`, runner phase was `discovering`, and
  `runner_last_error` was empty.
- Open-Meteo forecast HTTP drip-feed is deployed to the Oracle VPS and remains
  paper-only. The default forecast cache TTL is now
  `FORECAST_CACHE_TTL_SECONDS=2400`, and real forecast HTTP requests use
  `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60`: a request must finish or timeout,
  then the bot waits at least 60 seconds before the next real Open-Meteo
  request starts. Cache hits do not wait because they do not call Open-Meteo.
  The old forecast-refresh setting name has been removed from code, tests,
  docs, example env files, and the active VPS env; the
  market-discovery/WebSocket rebuild interval is now
  `STREAM_CYCLE_INTERVAL_SECONDS=2400`.
  Local verification: focused config/probability/deployment pytest passed with
  `119 passed`; full local pytest reached `422 passed` but hit 4 Windows
  temp-file `PermissionError` failures in existing paper-accounting journal
  tests. Remote verification on 2026-06-06 UTC: focused config/probability/
  deployment pytest passed with `119 passed`, live env safe values are
  `FORECAST_CACHE_TTL_SECONDS=2400` and
  `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60`, dashboard env uses
  `FORECAST_CACHE_TTL_SECONDS=2400`, `polymarket-weather-bot` restarted active
  with PID `195560`, `polymarket-weather-dashboard` restarted active with PID
  `195566`, dashboard HTML and authenticated `/api/status` returned HTTP 200,
  and runner status was `phase=discovering` with empty `last_error`.
  Follow-up deploy after removing the old setting name: local focused pytest
  passed with `179 passed`; remote focused pytest passed with `179 passed`;
  active VPS env safe values are `STREAM_CYCLE_INTERVAL_SECONDS=2400`,
  `FORECAST_CACHE_TTL_SECONDS=2400`, and
  `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60`; bot restarted active with PID
  `196183`; dashboard restarted active with PID `196190`; dashboard HTML and
  authenticated `/api/status` returned HTTP 200; and API
  `scan_interval_seconds` is `2400`.
- Paper-state account-number and stats-field validation is complete locally and
  remains paper-only. Existing bad `paper_state.json` files raise
  `PaperStateLoadError` instead of resetting or loading polluted cash/PnL
  evidence.
- Pre-forecast tradeability gating is complete locally and remains paper-only.
  When `REQUIRE_DATE_HINT_FOR_TRADE=true`, undated temperature markets now log a
  SKIP decision before Open-Meteo is called. Non-temperature, unsupported-city,
  and non-trading-ready markets are also rejected before forecast fetching.
  Local verification: focused runner/probability/hardening pytest passed with
  `76 passed`; full `pytest -q` passed with `300 passed`.
- Station nowcast request logging is implemented so METAR/HKO usage reviews
  count real observation HTTP attempts from `station_nowcast_request_log.jsonl`.
  Rows include source, request time, status, and cache-miss reason. HKO rows
  carry the city/station directly; AWC bulk rows use `station_id=METAR_BULK`,
  `request_mode=awc_metar_bulk_cache`, and `requested_station_ids`. Cache hits
  do not write rows, and the VPS log rotates at 10MB into `data/archive/` with
  zstd compression.
- AWC METAR nowcast now uses an `awc_metar_bulk_cache` prefetch: one AWC METAR
  JSON request asks for the enabled ICAO station set, then each city parses its
  own station rows from that shared response. HKO still uses its single official
  max/min CSV request. AWC request-log rows use `request_mode=awc_metar_bulk_cache`
  plus `requested_station_ids` so usage reviews count one real HTTP attempt
  rather than one row per station.
- Runtime log storage hardening is complete locally and remains paper-only.
  Normal raw decision snapshots are disabled by default, raw error/debug
  diagnostics rotate over 100MB into compressed archives with 7-day retention,
  disk pressure suspends raw writes with a runner-status warning, and new
  decision/portfolio rows keep compact summaries. Local verification: focused
  runtime-log pytest passed with `86 passed`; full `pytest -q` passed with
  `341 passed`.
- Temperature range buckets such as `86-87F`, `62-63F`, and `22-23C` are now
  parsed as distinct range buckets instead of exact single-temperature buckets.
  Probability estimation and city-date portfolio interval checks use the exact
  displayed inclusive endpoints, such as `86.0 <= T <= 87.0`, without
  half-step expansion. The change remains paper-only. Local verification:
  focused parser/probability/portfolio/hardening pytest passed with
  `144 passed`; full `pytest -q` passed with `358 passed`.
- Actual-order liquidity evaluation and conditional same-side add-ons are
  complete locally and remain paper-only. The runner no longer requires
  max-single-market ask depth before computing a smaller `size_usd`; if final
  target depth is short but at least `MIN_ORDER_USD` is executable, it scales
  down and logs `partial_fill`, while below-minimum depth still SKIPs. Same-side
  add-ons require the configured price drop, live probability above the stop
  threshold, positive edge/return, and normal exposure caps; they update the
  existing position and log `ADD`. Local verification: focused
  portfolio/config pytest passed with `85 passed`, focused
  hardening/dashboard/analyze pytest passed with `96 passed`, and full
  `pytest -q` passed with `366 passed`.
- Portfolio scenario-probability coherence hardening is complete locally and
  remains paper-only. Event probability tables normalize only when parsed
  temperature intervals are non-overlapping and exhaustive; incomplete sums
  below one keep `other`, while overlapping intervals or sums above one without
  full coverage fail closed with readable rejection reasons. Local
  verification: focused `tests/test_portfolio.py` passed with `45 passed`; full
  `pytest -q` passed with `371 passed`.
- Portfolio allocation-size candidate limiting is complete locally and remains
  paper-only. `_allocation_sizes` keeps one-dollar spacing for small ranges,
  caps large candidate grids at 50 sizes, and preserves the minimum order,
  allowed maximum, and affordable preferred size. Local verification: focused
  `tests/test_portfolio.py` passed with `48 passed`; full `pytest -q` passed
  with `377 passed`.
- Dashboard decision-total scope labeling is complete locally and remains
  paper-only. Large `paper_decisions.csv` files still use recent-tail
  initialization for responsiveness, and `/api/status` now exposes
  `decision_totals_exact` plus `decision_totals_scope`. Local verification:
  focused `tests/test_dashboard.py` passed with `38 passed`; full `pytest -q`
  passed with `377 passed`.
- AWC METAR bulk nowcast rows now require an explicit station ID match before
  becoming observation evidence. Rows missing both `icaoId` and `station_id`
  are skipped instead of inheriting the requested station ID. Local
  verification: focused `tests/test_nowcast_provider.py` passed with
  `15 passed`; full `pytest -q` passed with `378 passed`.
- WebSocket receiver and realtime strategy evaluation are separated locally and
  remain paper-only: receiver callbacks enqueue/coalesce event updates, and a
  bounded worker performs evaluation plus `paper_decisions.csv` evidence writes.
  Local verification: focused realtime/coalescer tests passed with `31 passed`;
  full `pytest -q` passed with `383 passed`.
- Paper state/trade ledger transaction hardening is complete locally and
  remains paper-only. Startup now rejects obvious state/trade evidence drift:
  executed trade rows without `paper_state.json`, and open positions without
  matching `OPEN` ledger rows. Local verification: focused
  `tests/test_paper_state_io.py` passed with `45 passed`; full `pytest -q`
  passed with `398 passed`.
- Dashboard HTML/CSS/JS now lives in `src/weather_bot/dashboard_template.py`,
  while `src/weather_bot/dashboard.py` keeps the read-only API, file-reading,
  payload, and HTTP handler logic. This was a mechanical no-behavior cleanup;
  `weather_bot.dashboard.HTML` remains re-exported for existing tests and
  callers.
- Other local hardening changes have not all been treated as one automatic
  deployment bundle; verify the specific commit and service state before
  assuming a future local change is live.
- Before any deployment, explain the change, benefit, risk, verification method,
  public exposure implications, and rollback method, then get explicit user
  approval.
- After-fee exit-trigger hardening, forecast-request minimum-spacing validation,
  fee-aware all-in buy-depth probing, and transient Windows journal-replace
  retry are deployed to the Oracle VPS and remain paper-only. Local full
  verification: `pytest -q` passed with `440 passed`. Remote verification on
  2026-06-06 UTC: focused exit/config/edge/paper-state pytest passed with
  `134 passed`, portfolio pytest passed with `48 passed`, full pytest passed
  with `440 passed`, active env kept
  `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60`, `polymarket-weather-bot`
  restarted active with PID `199286`, `polymarket-weather-dashboard` restarted
  active with PID `199291`, dashboard HTML returned HTTP 200, public bare
  `/api/status` and query-token `/api/status` returned HTTP 403,
  header-authenticated `/api/status` returned HTTP 200, and runner status was
  `phase=discovering` with empty `last_error`. During deployment, the app root
  permission was corrected to `polymarket:polymarket` with mode `755` after an
  archive-preserving payload copy briefly narrowed it to `700`.
- Realtime paper evaluation now enqueues only executable `book` and
  `price_change` depth updates. `best_bid_ask`, `last_trade_price`, and other
  reference-only messages can update display/reference metadata but do not wake
  the strategy evaluator or grow `paper_decisions.csv`. Multi-level
  `executable_buy_price()` now solves the actual all-in budget across VWAP and
  fee instead of overspending from a best-ask share estimate. `.codexignore`
  excludes additional growing runtime evidence files, and dashboard build
  verification now documents header-token `/api/status` checks instead of
  public query-token checks. Local verification: focused realtime/edge/
  portfolio tests passed with `93 passed`; full `pytest -q` passed with
  `442 passed`. Remote verification on 2026-06-06 UTC: focused pytest passed
  with `216 passed`, full pytest passed with `442 passed`,
  `polymarket-weather-bot` restarted active with PID `201851`,
  `polymarket-weather-dashboard` restarted active with PID `201857`, runner
  status was `phase=discovering` with empty `last_error`, dashboard HTML
  returned HTTP 200, public bare `/api/status` and query-token `/api/status`
  returned HTTP 403, and header-authenticated `/api/status` returned HTTP 200.
- Held-position exit blockers now preserve the fired exit signal locally and
  remain paper-only. If `assess_exit()` says to close but there is no
  executable bid depth, `paper_trades.csv` keeps `HOLD_NO_LIQUIDITY` instead of
  pretending to sell, while the reason records
  `exit signal fired but no executable liquidity` plus
  `exit_trigger=<trigger>`. Partial closes also preserve the original
  `exit_trigger`, and stale WebSocket holds store the latest model/nowcast exit
  signal in position metadata without using fake prices. Local verification:
  focused hardening pytest passed with `3 passed`, related
  hardening/exit/realtime pytest passed with `90 passed`, and full
  `pytest -q` passed with `444 passed`.
- Exact/range temperature bucket nowcast risk is complete locally and remains
  paper-only. For held NO positions, same-station observed high/low inside an
  exact or range bucket now carries an exit-only
  `nowcast_bucket_lock_risk` signal; observed values fully outside the bucket
  still preserve the existing YES-impossible probability behavior, and
  daily-low markets use observed low rather than observed high. Local
  verification: focused new tests passed with `4 passed`, related
  probability/exit/hardening/parser/portfolio pytest passed with `185 passed`,
  and full `pytest -q` passed with `448 passed`.
- Fresh local-yesterday station nowcast is complete locally and remains
  paper-only. AWC METAR and HKO nowcast can use the target date when it is the
  station's local yesterday and the post-close freshness window is still open;
  older dates, expired windows, future observations, stale rows, missing values,
  and wrong-station rows remain unusable. AWC bulk cache reuse now requires the
  cached `hoursBeforeNow` lookback to cover the requested target date. Local
  verification: focused `tests/test_nowcast_provider.py` passed with
  `21 passed`, related nowcast/probability/realtime pytest passed with
  `89 passed`, and full `pytest -q` passed with `454 passed`.

## Next Work

1. Do not feed shadow research into strategy execution until enough resolved
   paired public signals accumulate.
2. When comparing paper results, record the boundary between pre-fix gross-fee
   accounting and post-fix fee-adjusted accounting. Existing runtime files were
   not rewritten retroactively.
3. Later, run `shadow-signal-report --collect` only when bounded public data
   collection is intentional. Suggest a paper-only A/B experiment only if the
   report has at least 20 paired resolved rows and at least a
   five-percentage-point edge over matched bot entries.
4. Automatic copy trading, wallet connection, live orders, and private data
   collection remain prohibited.
5. Before local pytest or VPS/SSH work, use the command shapes in
   `docs/codex/known-good-commands.md`.
6. Do not bypass `TRADING_READY_STATION_MAP`; `STATION_MAP` is the registry,
   while trading-ready means official rule evidence is stored and conflict-free.
7. For any public dashboard exposure, set a real random `DASHBOARD_TOKEN` with
   at least 32 characters. Empty, short, placeholder, basic, default,
   change-me, secret, token, password, abc, or 123456 style values stop the
   dashboard before it binds to the public host. Public API access must use the
   `X-Dashboard-Token` header; `?token=...` URLs are not accepted for public API
   authentication.
8. Build or run a paper-only SKIP diagnosis report before treating repeated
   SKIPs as strategy failure. Use `docs/codex/skip-diagnostics.md` to classify
   whether the blocker is account safety, minimum order, market liquidity,
   weather/parsing data, or strategy threshold.
9. If full-history reports become too slow on very large ledgers, add an
    explicit operator option such as `--since` or `--max-rows`; do not silently
    change the default full-history report semantics.
10. After any future dashboard change, deploy it immediately to the Oracle VPS,
    restart the affected service, and verify both the server-rendered HTML and
    `/api/status` with the dashboard token. For settlement changes, also restart
    the paper bot and verify that older closed positions with exact binary
    `outcomePrices` settle on the next paper cycle.
11. `paper_raw_snapshots.jsonl`, `forecast_request_log.jsonl`, and
    `station_nowcast_request_log.jsonl` have automatic rotation. Raw snapshots
    default to error-only storage and may suspend with
    `raw_snapshot_storage.status=suspended` when disk pressure is dangerous.
    Do not rotate or truncate `paper_decisions.csv` until reports/dashboard
    readers have an explicit archive-aware path or bounded operator option.
12. The active VPS paper experiment was intentionally reset on 2026-06-05 UTC:
    `paper_state.json` starts from 200 USD cash with zero positions, and old
    paper evidence ledgers are no longer available on the VPS runtime path.
    Future performance comparisons must treat this as a new experiment window.

## For The Next AI

> Do not redesign from scratch. Continue from this document's 'In Progress' and 'Next Work' sections. Do not reimplement completed items. If the code and documents disagree, record the drift before continuing.

- First read `AGENTS.md`, this file,
  `docs/production-implementation-plan.md`, and
  `docs/production-decisions.md`.
- Do not rebuild completed settlement-runner or shadow-research work. Preserve the
  principal-recovery/settlement runner path in `src/weather_bot/paper.py`, exit
  trigger separation in `src/weather_bot/exit_policy.py`, and shadow research
  isolation in `src/weather_bot/shadow_signals.py`.
- Shadow signals are research-only. Do not add real orders, wallet connection,
  automatic copy trading, operations deployment, or private data collection.
- Use `TRADING_READY_STATION_MAP` for execution candidates. Karachi remains
  excluded until the `OPMR` registry entry is reconciled with the official
  Polymarket rule source that points to `OPKC`.
- Repeated SKIPs are research signals, not the end of the investigation. Use
  `docs/codex/skip-diagnostics.md` before changing thresholds or strategy.
- Keep the AWC METAR nowcast path bulk-prefetched. Do not reintroduce
  per-station AWC HTTP requests during one refresh.
