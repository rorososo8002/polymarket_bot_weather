# Production Decisions

This file is the compact decision ledger for fresh-chat handoff. Keep the
current rule, why it exists, and the operational consequence. Put long
investigation detail in `docs/solutions/`, `docs/codex/`, roadmap docs, or
specialized reference docs.

## Active Decision Snapshot

- Paper-only execution is the boundary. No private keys, wallet connection,
  signing, real orders, automatic copy trading, or live deployment are allowed
  without explicit approval and a separate live-trading safety pass.
- The bot trades only stations in `TRADING_READY_STATION_MAP`, the subset of
  `STATION_MAP` with stored official Polymarket rule evidence and no known
  station-code conflict. Current count: 40 trading-ready cities. Karachi remains
  registered in `STATION_MAP` but excluded because the official rule evidence
  points to `OPKC` while the registry uses `OPMR`. Unknown, stale, malformed,
  unsupported, or suspicious data means skip.
- Execution is temperature-only. Rain, snow, precipitation, wind, humidity, and
  other non-temperature weather markets are outside the paper strategy and are
  excluded before forecast probability calculation, order-book subscription, or
  paper trade logging. Example environment files must not expose removed
  non-temperature market toggles.
- Forecast rows must match the target market date exactly. Nearby forecast
  dates are not substitutes and produce `forecast-unavailable`.
- `pre_forecast_tradeability_gate` must reject markets before Open-Meteo when
  they are not temperature-shaped, not trading-ready, or missing required
  `date_hint` evidence. SKIP diagnostics are recorded, but forecast API budget
  is not spent on markets that cannot trade.
- Explicit `WEATHER_BIAS_JSON` files are part of the forecast evidence. Empty
  means use neutral defaults, but a missing, unreadable, invalid JSON,
  malformed, or non-numeric explicit file produces `forecast-unavailable` with
  zero confidence instead of silently removing calibration.
- Real Open-Meteo forecast HTTP calls are globally drip-fed by
  `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60`: one request must finish or
  timeout, then at least 60 seconds pass before the next real request starts.
  Values below 60 seconds fail startup instead of weakening the budget guard.
  `FORECAST_CACHE_TTL_SECONDS=2400` is the default forecast answer-sheet
  freshness window. Cache hits do not count as calls. Order books use the
  Polymarket CLOB WebSocket stream, and open-position token IDs stay subscribed
  until the position closes or settles.
- Realtime nowcast-backed `WeatherSignal` refresh is separate from the
  Open-Meteo forecast cache/request throttle. Why: forecasts are slower-moving and
  API-budgeted, while same-day observed high/low evidence can change while a
  cached forecast answer sheet is still valid. Consequence: realtime paper
  decisions may recalculate station nowcast on its own cache TTL using the
  existing forecast cache.
- The WebSocket receiver thread must not run heavy strategy evaluation or write
  the strategy ledger directly. It updates the order-book cache and submits
  event work to a bounded coalescer/worker, which merges short bursts by
  event/city/date before evaluating and writing `paper_decisions.csv`. Why:
  the receiver is the market-price telephone line, so blocking it with
  portfolio math or CSV writes can miss later price updates and grow the
  decision ledger too aggressively. Consequence: `paper_runner_status.json`
  exposes realtime evaluator queue depth, coalesced update count, dropped
  update count, and worker errors.
- The realtime bot records refresh-cycle failures in `paper_runner_status.json`
  instead of relying only on `systemd Restart=always`. Why: a restarted process
  without an operator-readable error can make the dashboard look stale rather
  than failed. Consequence: market discovery, forecast preparation, WebSocket
  startup, and status-update exceptions write `phase=error`, `failed_phase`,
  and a concrete message before a short retry backoff.
- Discovery maps YES/NO token IDs only from explicit outcome labels. If
  `tokens` or `outcomes` cannot prove the YES and NO side for `clobTokenIds`,
  the market is skipped rather than guessed from list order.
- Discovery and the final paper-entry gate reject inactive or closed markets as
  new-entry candidates, while closed markets remain valid evidence for settling
  already-held paper positions. Why: a closed market is a scored answer sheet,
  not a fresh price opportunity; mixing settlement evidence with buy candidates
  contaminates paper-performance validation.
- `best_bid_ask` is indicative price data only. Executable depth comes from
  `book` snapshots or `price_change` updates, not assumed sizes.
  `OrderBook.best_bid` and `OrderBook.best_ask` mean executable positive-size
  depth only; reference/indicative fields are for display and diagnostics.
  Realtime paper evaluation is enqueued only for executable depth updates, so
  quote-only or trade-only messages do not grow the decision ledger.
- Executable order-book levels are used only after shared defensive numeric
  parsing in both REST CLOB and WebSocket paths. Non-numeric, non-finite,
  zero-size, negative, or out-of-range executable prices/sizes are discarded;
  malformed WebSocket snapshot shapes do not replace the current executable
  book.
- Entry decisions are fee-aware. `p_exec` is executable VWAP; `size_usd` is the
  all-in paper-entry budget; `size_shares` is the fee-adjusted actual held
  quantity; paper cash, liquidation bankroll, and dashboard PnL use after-fee
  accounting. Entry ask-depth checks must be based on the actual computed
  `size_usd`, not the maximum single-market cap. The evaluator may probe the
  minimum order first to estimate price, but it must recheck final depth and
  recalculate edge, fees, shares, and expected return when final VWAP changes.
- Exit decisions that claim profit or bounded loss are fee-aware too. Take
  profit, overheated profit, and edge-faded exits compare after-fee liquidation
  PnL against the configured thresholds, not raw token-price movement. Why:
  raw price gains can still be after-fee account losses. Consequence: exit
  reasons disclose both `net_pnl` and `raw_pnl`, while the close decision uses
  the account-book value.
- New-entry evaluation blocks before expected-return math when
  `entry_bankroll <= 0` or the calculated order is below the `$10` minimum, so
  fail-closed account uncertainty is logged as SKIP instead of a zero-share
  exception.
- City-date weather buckets share one correlated-risk budget. At most two
  complementary legs are selected per event, with a `$10` minimum leg and
  conservative city, event, and total exposure caps.
- Portfolio allocation-size candidates stay dense for small allowed ranges but
  are capped at 50 sizes for large ranges. The minimum order, allowed maximum,
  and any affordable preferred size remain explicit candidates so large paper
  bankrolls cannot make event selection explode computationally.
- Event portfolio `scenario_probabilities` are normalized only when the
  temperature intervals are non-overlapping and exhaustive. Incomplete sets
  below one keep `other`; overlapping intervals or sums above one without full
  coverage fail closed and leave an operator-readable rejection reason.
- Temperature range markets are not exact single-temperature buckets. A market
  such as `86-87F` means `86.0 <= temperature_f <= 87.0`, so parser,
  probability, and portfolio interval logic must preserve the displayed
  inclusive lower and upper endpoints without half-step expansion.
- Profit exits may recover principal and keep a bounded settlement runner only
  when conservative settlement value beats fee-adjusted sell-now value. Active
  runners are rechecked; they are not risk exemptions.
- Resolved paper settlement requires a proven binary winner. Explicit winner
  fields are preferred; exact closed-market `outcomePrices` of YES/NO `1/0` or
  `0/1` are accepted. Ambiguous closed-market prices are not guessed. The
  realtime WebSocket runner applies this settlement check before starting each
  stream cycle so old resolved markets do not stay subscribed as open risk.
- Same-day nowcast is allowed only from explicitly mapped same-station official
  sources. Observed high-so-far is evidence only for daily-high markets, and
  observed low-so-far is evidence only for daily-low markets. Providers should
  derive both extrema from one station-date response and cache it. No
  nearby-station or city-center substitutions.
- AWC METAR bulk rows are observation evidence only when the row itself carries
  an explicit station ID that matches the requested settlement station
  case-insensitively. Rows missing both `icaoId` and `station_id` are invalid
  evidence and must be skipped.
- Public whale/external-signal research remains shadow-only. Promotion requires
  paired resolved public-signal and bot-entry samples, then only suggests a
  paper-only A/B experiment.
- Known-good commands belong in `docs/codex/known-good-commands.md`; fresh work
  should use them before inventing command shapes.
- Repeated SKIPs are research signals. Diagnose and classify them before
  changing strategy thresholds, risk caps, or data-source assumptions.
- `paper_state.json` is an account book. Saves use atomic temp-file replacement,
  and existing corrupt, structurally invalid, account-number invalid,
  stats-field invalid, or position-field invalid paper state fails closed
  instead of resetting.
- `paper_state.json` and `paper_trades.csv` are paired paper-accounting ledgers
  for executed actions. `OPEN`, `ADD`, `CLOSE`, and `PARTIAL_CLOSE` write a
  small `paper_state.json.journal` before changing the account book and clear
  it only after both state save and trade logging finish. A leftover journal,
  a missing state file with existing executed trade rows, open positions with a
  missing/empty trade ledger, or open positions without matching `OPEN` rows
  means the account book and execution ledger may disagree, so startup fails
  closed for operator reconciliation instead of trading from guessed state.
- An operator-approved paper reset creates a new performance window. Why: old
  decisions, trades, and state are experimental evidence for the previous
  bankroll, not neutral cache. Consequence: after the 2026-06-05 UTC VPS reset,
  active paper results start from 200 USD cash and zero positions; do not mix
  them with pre-reset logs when judging profitability.
- Public dashboard exposure requires a real `DASHBOARD_TOKEN` with at least 32
  characters; empty, short, placeholder, basic, default, change-me, secret,
  token, password, abc, 123456, or other obvious example tokens stop startup
  before binding to a public host. Public dashboard API authentication accepts
  only the `X-Dashboard-Token` header; `?token=...` query authentication is
  rejected because URLs are easy to leak through browser history, copied links,
  logs, and screen sharing.
- Boolean environment settings accept only explicit true/false aliases. Unknown
  values fail startup instead of silently disabling safety switches.
- Numeric Settings values for paper money, risk caps, fees, and runtime
  freshness windows fail closed at startup when they are outside safe ranges.
  This prevents negative orders, negative fees, impossible exposure fractions,
  fee rates above 1, zero cache TTL, or zero stream-cycle timing windows from
  contaminating paper-performance evidence.
- Dashboard port and shadow research integer limits are also startup
  validation targets. `DASHBOARD_PORT` must be a real TCP port from 1 to 65535;
  shadow research collection limits must be sane integers, with
  `SHADOW_MAX_ROWS=0` preserved as "keep no shadow rows." This prevents broken
  operator settings from quietly corrupting dashboard startup or research
  samples.
- `SIZE_MODE` accepts only `fixed_fraction` and `kelly` at `Settings` startup.
  Values are case-normalized, and typos such as `kellyy` raise `ValueError`
  before the runner can fall back to the wrong paper sizing path.
- WebSocket health is based on executable order-book depth, not indicative
  `best_bid_ask` reference quotes. Stale/dead WebSocket health blocks new
  entries, pauses held-position exit evaluation with explicit
  `HOLD_STREAM_UNHEALTHY` logs, and may rebuild a dead WebSocket receiver
  without switching to REST polling.
- Held-position marking and close evaluation also require fresh executable
  order-book depth for that position's own `token_id`. Why: one token's fresh
  update proves the WebSocket is alive, but it does not prove another held
  YES/NO token has executable bid depth. Consequence: a globally fresh stream
  can still pause only the stale-token position with `HOLD_STREAM_UNHEALTHY`
  while fresh-token positions continue normal paper evaluation.
- Paper analysis reports treat `paper_decisions.csv` and `paper_trades.csv` as
  source ledgers. Reports may scan full history when that is the promised
  meaning, but they must stream rows and keep only aggregates or bounded
  lookups in memory instead of materializing whole CSV files.
- Resolved Brier scoring uses `paper_trades.csv` `OPEN` entry metadata first:
  `entry_p_true` is the YES probability at actual paper entry time. Legacy
  trade CSVs without those columns fall back to the latest entry decision so
  old reports do not break. Existing trade CSV headers are not rewritten just
  to add newer columns; new files get the current full header, while legacy
  files keep their evidence format and analysis falls back when needed.
- `paper_raw_snapshots.jsonl` is diagnostic evidence, not a source ledger.
  Normal raw decision snapshots are off by default; `RAW_SNAPSHOTS_MODE=error`
  saves only error evidence, and `debug` is for bounded investigations. Raw
  snapshots rotate over 100MB into compressed `data/archive/` files, keep 7
  days by default, and suspend raw writes with a `paper_runner_status.json`
  warning when disk pressure is dangerous. Do not apply this cleanup rule to
  `paper_state.json`, `paper_trades.csv`, or `paper_decisions.csv`.
- `forecast_cache.json` is not the Open-Meteo call ledger. It caches the latest
  successful forecast per key and can overwrite older evidence. Real Open-Meteo
  HTTP attempts are recorded in `forecast_request_log.jsonl` with cache-miss
  reason and safe city/station metadata; the log rotates at 10MB into
  `data/archive/` with zstd compression.
- Real Open-Meteo forecast HTTP calls are serialized across
  `OpenMeteoEnsembleClient` instances. Why: Open-Meteo can reject bursty or
  overlapping city requests with `Too many concurrent requests`, and a timeout
  can still be running server-side after the local client gives up.
  Consequence: cache hits return immediately, but cache misses wait behind the
  previous real request and then honor
  `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60` before starting.
- `forecast_rate_limit_state.json` is the Open-Meteo cooldown memo, not a
  forecast. When a real HTTP request returns 429, the bot records the 429 kind
  and a UTC `blocked_until` time, then later runner cycles skip new
  Open-Meteo calls until that time. Daily quota responses use the next UTC
  reset cooldown. `Too many concurrent requests` uses a short 15-minute
  `concurrent` cooldown and does not permanently disable the client for the
  whole market-discovery/stream cycle. Legacy state files without `kind` are
  reclassified from their reason text, so old concurrent memos do not keep behaving like
  daily-limit blocks. Cache hits can still be used because they do not spend API
  budget.
- Open-Meteo `ReadTimeout` is handled as a per-forecast-key temporary miss.
  Why: a server-side request can still be running after the local client gives
  up, so immediate retries can stack up behind one slow city and trigger
  concurrent-request limiting. Consequence: one timed-out key receives a
  30-minute in-process temporary failure memo, repeated calls for that same key
  fail fast without another HTTP attempt, and other city keys continue.
- Station nowcast caches are not METAR/HKO call ledgers. Real AWC METAR and
  HKO max/min HTTP attempts are recorded in
  `station_nowcast_request_log.jsonl` with source, request time, status, and
  cache-miss reason. HKO rows carry city/station details directly. AWC rows use
  `request_mode=awc_metar_bulk_cache`, `station_id=METAR_BULK`, and
  `requested_station_ids` because one real HTTP request covers many ICAO
  stations. Cache hits do not write rows, and the VPS log rotates at 10MB into
  `data/archive/` with zstd compression.
- Dashboard trade-history panels treat SKIP rows as diagnostics, not executed
  trades. Recent trades, realized rows, and realized equity points use cached
  actual trade actions so SKIP bursts cannot hide older closes.
- Dashboard scanner totals must disclose their counting scope. Full-ledger
  totals use `decision_totals_exact=true` and `decision_totals_scope=full`;
  large-file recent-tail initialization uses `decision_totals_exact=false` and
  `decision_totals_scope=recent_tail`.
- Dashboard open-position links point to Polymarket event pages using
  `event_slug`; condition-specific market slug suffixes such as `-25corbelow`
  or `-30corhigher` are not event URLs.

## Compact Ledger

### 2026-05-26: Register Only Verified Polymarket Weather Stations

Status: Superseded for execution by the 2026-06-03 rule-evidence decision;
retained as the registration boundary.
Decision: Register only the 41 cities mapped in `src/weather_bot/stations.py`.
Why: Forecasting the wrong station destroys weather-market edge. Consequence:
weather-shaped markets are skipped unless their parsed city is in `STATION_MAP`,
and actual paper execution still requires `TRADING_READY_STATION_MAP`.

### 2026-05-26: Remove City-Centroid Trading Fallback

Decision: Missing verified station returns `unsupported-station` with zero
confidence. Why: city-centroid forecasts are exploration data, not production
trading data. Consequence: parser support and trading support use the same
verified station set.

### 2026-05-26: Forecast API Budget Must Be Protected

Decision: Forecast API budget must be protected with cache freshness and real
request pacing. Why: Open-Meteo forecast data moves slower than order books,
and the free API budget can be exhausted by many ensemble city-date requests.
Consequence: current forecast HTTP calls use a 40-minute cache TTL plus a
one-request-at-a-time 60-second throttle.

### 2026-05-26: Order Books Use The CLOB WebSocket Stream

Decision: Default order-book monitoring uses the Polymarket CLOB WebSocket
market channel. Why: realtime order-book monitoring was required; a REST loop
is polling. Consequence: discovery cycles remain slower, real forecast HTTP
requests are drip-fed, and book events trigger paper evaluation.

### 2026-05-26: Keep Paper Trading As The Execution Boundary

Decision: No private keys, signing, or live order submission. Why: real-money
execution needs separate safety work. Consequence: "trade" means paper
open/close through `PaperBroker`.

### 2026-05-26: Probability Stop Replaces Fixed Price Stop

Decision: Close when current side probability falls below the entry-time
`probability_stop_threshold`. Why: forecast deterioration is the thesis break,
not thin price noise. Consequence: YES uses `p_true`; NO uses `1 - p_true`.

### 2026-05-28: Invalid Edge Sentinels Are Not Exit Signals

Decision: `edge faded` exits require a fresh executable held-side edge and
non-empty `p_exec`. Why: `net_edge=-999` means evaluation failure, not negative
edge. Consequence: invalid book updates can log SKIP but cannot force a close.

### 2026-05-28: Keep Held Position Tokens In WebSocket Subscriptions

Decision: Realtime subscriptions include current discovery tokens plus every
open paper-position token. Why: discovery can move to newer dates while older
positions still carry risk. Consequence: held positions keep live marks.

### 2026-05-28: Explain Two-Sided Liquidity Rejections In SKIP Logs

Decision: If both YES and NO fail liquidity validation, the final SKIP reason
includes both side-specific details. Why: opaque SKIPs hide the real blocker.
Consequence: fail-closed logs remain operator-readable.

### 2026-05-28: Strategy Changes Must Be Research-Backed And Reproducible

Decision: Strategy changes must state expected-value, calibration, sizing,
liquidity, slippage, forecast-error, or drawdown rationale. Why: a fresh AI
must reproduce behavior without redesigning. Consequence: update production
docs alongside strategy code.

### 2026-05-28: Dashboard Scanner Counts Are Cumulative But Hidden

Decision: Keep cached totals internally, but show current operator state in the
visible Scanner Intelligence panel. Why: candidate counts are not actual open
trades. Consequence: detailed dashboard contract lives in
`docs/dashboard-build-spec.md`.

### 2026-06-01: Forecast Freshness And WebSocket Health Are Separate Signals

Decision: Runner status records forecast health and WebSocket health
separately. Why: a live process can have stale forecasts or a dead receiver
thread. Consequence: dashboard warns on stale, degraded, or failed inputs.

### 2026-06-01: Keep The Entrance Guide Short And Progress Current

Decision: `AGENTS.md` stays the short entrance guide and
`docs/production-progress.md` stays a current handoff. Why: long mandatory
checklists waste fresh-chat tokens. Consequence: old detail goes to reference
docs or `docs/solutions/`.

### 2026-06-01: Require Executable Expected Net Return Before Entry

Decision: Entry needs both model `net_edge` and executable expected net return
above `ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.06`. Why: apparently profitable
high-price entries can fail after fees and exit costs. Consequence: reasons log
route, gross profit, estimated costs, fees, spread, slippage, and rejection.

### 2026-06-01: Keep Live Execution Separate From Paper Strategy

Decision: Future live trading is tracked in
`docs/live-trading-safety-plan.md`. Why: strategy quality and real-order
execution safety are different problems. Consequence: wallet connection,
credentials, and live orders require explicit separate approval.

### 2026-06-01: Discover Complete Weather Events Before Evaluating Markets

Decision: Discovery expands every supported binary market inside supported
weather-category events. Why: one event can contain many exact, lower-tail, and
upper-tail markets; stopping at 41 binaries can cut coverage. Consequence:
runner status reports event, city, market, and token coverage separately.

### 2026-06-01: Promote Known-Good Commands Into Executable Defaults

Decision: Routine local pytest, Oracle SSH, remote pytest, bounded log checks,
SCP, and dashboard reachability commands live in
`docs/codex/known-good-commands.md`. Why: command guessing repeats avoidable
failures. Consequence: use known-good shapes first.

### 2026-06-01: Split One Conservative City-Date Budget

Status: Superseded by the 2026-06-02 portfolio decision. Retained principle:
nearby buckets are correlated, so one event must not multiply exposure.

### 2026-06-02: Score YES And NO Event Portfolios With A Ten-Dollar Minimum Leg

Decision: Compare one-leg and at-most-two-leg `YES+YES`, `YES+NO`, and `NO+NO`
portfolios across distinct buckets. Minimum leg is `$10`; event cap is 10%
below `$1,000` and 5% from `$1,000`; city cap is 20%; total open cap is 90%.
Why: useful NO legs and multi-outcome negative-risk relationships were missed
before. Consequence: selection maximizes expected log growth under shared
event risk.

### 2026-06-02: Start Settlement-Station Nowcast As A Seoul Pilot

Decision: Same-day temperature probability may use observed high-so-far only
from explicitly mapped settlement-station sources. Why: city-center or nearby
station substitutions repeat the wrong-station mistake. Consequence: missing,
stale, malformed, future-date, or unmapped nowcast remains forecast-only or
skip.

### 2026-06-02: Keep Station Audit Evidence Explicit

Decision: Station registry records forecast source, coordinate source, nowcast
candidate, provider status, and rule evidence status separately. Why: station
ID alone does not prove nowcast readiness. Consequence: readable audit table is
`docs/station-registry-audit.md`.

### 2026-06-02: Expand Same-Station Observation Providers After Source Checks

Decision: Use AWC METAR for 39 ICAO stations and HKO daily max/min CSV for Hong
Kong, while keeping Karachi/OPMR as registered metadata only until its rule
evidence conflict is resolved. Why: Hong Kong has official HKO data; Karachi's
official rule evidence points to `OPKC` while the registry uses `OPMR`.
Consequence: no substitutions and no Karachi paper execution.

### 2026-06-02: Recover Principal Before Holding A Settlement Runner

Decision: Profit exits may sell a principal-recovery tranche and keep a bounded
runner when conservative settlement value is better than fee-adjusted sell-now
value. Why: a favorable low-cost position can be worth more at settlement.
Consequence: runner actions log `PARTIAL_CLOSE`, `HOLD_RUNNER`, or
`HOLD_NO_LIQUIDITY`; active runners are rechecked.

### 2026-06-02: Keep Whale And External Signals In Shadow Research

Decision: Public whale/external signals are research-only and not execution
inputs. Why: trade size alone is not proof of edge. Consequence: bounded public
rows and notes can produce a report, but cannot copy trades.

### 2026-06-02: Keep Paper Fee Accounting Consistent End To End

Decision: Treat `size_usd` as all-in entry budget, buy fewer shares after entry
fee, subtract taker fees from normal and partial closes, and value dashboard
PnL/liquidation after exit fee. Why: gross paper fills overstated performance.
Consequence: post-fix paper results are more conservative; old runtime files
are not rewritten.

### 2026-06-02: Guard Shadow Research Locally And Compare Paired Samples

Decision: Recheck minimum public trade size locally, dedupe by full row
identity, honor zero retention, and promote only from paired resolved rows
where the bot made a scoreable entry. Why: remote filters and unequal
denominators can overstate public-signal edge. Consequence: shadow promotion
requires at least 20 paired rows and a five-point advantage, then only
paper-only A/B testing.

### 2026-06-02: Require Exact Forecast Dates Before Strategy Evaluation

Decision: `weather_bot.probability` rejects an Open-Meteo daily forecast unless
`daily.time` contains the exact target market date. Why: a nearby date can make
a different city-date event look tradable and contaminate paper-performance
results. Consequence: missing target-date rows return `forecast-unavailable`
with zero confidence, so paper trading skips instead of guessing.

### 2026-06-03: Do Not Turn Best-Bid-Ask Quotes Into Depth

Decision: `best_bid_ask` WebSocket messages update indicative best bid/ask
prices only; they do not add, move, or resize bid/ask levels. Why: those
messages do not prove how many shares are actually available at the quoted
price. Consequence: executable VWAP, liquidity filters, exits, and paper fills
use only depth confirmed by `book` snapshots or `price_change` updates.

### 2026-06-04: Make OrderBook Best Prices Executable-Only

Decision: `OrderBook.best_bid` and `OrderBook.best_ask` return only positive-size
levels from `bids` and `asks`; indicative stream quotes stay in
`indicative_best_bid`, `indicative_best_ask`, or reference helpers. Why: shared
best-price properties were still able to feed `best_bid_ask` reference quotes
into liquidity filters, `YES+NO` ask checks, spread audits, and position marks.
Consequence: quote-only books fail closed for entries and exits, while display
code can still show the indicative quote explicitly.

### 2026-06-03: Use Fee-Adjusted Shares As The Canonical Entry Quantity

Decision: `EdgeResult.size_shares` means the actual shares bought after entry
fees, not gross `size_usd / p_exec` shares. Why: portfolio scenarios that use
gross shares overstate settlement payoff and expected profitability. Consequence:
entry filtering, portfolio selection, scenario PnL, and paper broker opens all
share the same all-in-budget quantity formula.

### 2026-06-03: Treat Paper State As A Fail-Closed Account Book

Decision: Save `paper_state.json` by writing a complete temp file and replacing
the live file with `os.replace`; reject corrupt, unreadable, structurally
invalid, account-number invalid, stats-field invalid, or position-field invalid
existing state with `PaperStateLoadError`.
Why: cash, realized PnL, stats, and open positions are the paper strategy's
ledger, not a rebuildable cache. Consequence: missing state can initialize on
first run, but an existing bad state stops paper trading until an operator
investigates or restores a good file.

### 2026-06-03: Require Rule Evidence Before Station Trading

Decision: Keep all 41 registered cities in `STATION_MAP`, but use
`TRADING_READY_STATION_MAP` for discovery and probability execution. Why:
station coordinates alone do not prove the Polymarket settlement rule. A wrong
settlement station contaminates paper-profit evidence. Consequence: 40 cities
are trading-ready with stored Polymarket rule URLs and station wording; Karachi
is excluded because its found rule source points to `OPKC` while the registry
uses `OPMR`.

### 2026-06-03: Fail Closed On Public Dashboard Without A Real Token

Decision: When `DASHBOARD_HOST` is not `127.0.0.1` or `localhost`, the
dashboard refuses to start unless `DASHBOARD_TOKEN` is at least 32 characters
and not an obvious weak example value. Why: binding to `0.0.0.0` or `::`
exposes the service to anyone who can reach the URL, including automated
scanners. Consequence: copied example files, empty, short, placeholder,
basic/default/change-me, secret, token, password, abc, or 123456 values fail
before the HTTP server binds; local development can still run without a token,
query-token values are redacted from dashboard logs, and public API requests
must authenticate with `X-Dashboard-Token` instead of `?token=...`.

### 2026-06-06: Reject Public Dashboard Query Token Authentication

Decision: On public dashboard hosts such as `0.0.0.0` or `::`, `/api/status`
rejects `?token=...` and accepts `DASHBOARD_TOKEN` only through the
`X-Dashboard-Token` header. Localhost and `127.0.0.1` keep query-token
first-load compatibility for development. Why: URL tokens are stored or copied
more easily than headers, and a public dashboard can be probed by anyone who
knows or finds the URL, including automated scanners. Consequence: bare public
`/api/status` and public `/api/status?token=...` both return 403 when a token is
configured; header-authenticated requests return 200.

### 2026-06-03: Do Not Use Observed High Nowcast For Daily-Low Markets

Status: Superseded by the observed-extrema nowcast decision below.
Decision: `observed_high_so_far` nowcast applies only to daily-high temperature
markets. Daily-low markets stay forecast-only unless a separate same-station
observed low provider is verified. Why: today's observed high does not prove
whether today's low was below a threshold. Consequence: lowest-temperature
questions cannot be forced toward NO by an unrelated high-temperature
observation.

### 2026-06-03: Use One Same-Station Extrema Nowcast For Daily High And Low

Decision: The nowcast provider returns same-station observed temperature
extrema for the target station-date: high-so-far and low-so-far. Why: METAR
history and HKO max/min data can supply both values from one provider response,
which avoids extra calls and keeps the metric matched to the market question.
Consequence: daily-high markets may use observed high, daily-low markets may
use observed low, and missing or stale extrema still fall back to forecast-only
or skip according to the normal fail-closed rules.

### 2026-06-03: Block New Entries Before Zero-Share Return Math

Decision: If `entry_bankroll <= 0`, or if sizing produces an order below
`MIN_ORDER_USD`, the live paper evaluator returns SKIP before calling
expected-return helpers that require positive shares. Why: an unpriceable held
position means the account basis for new entries is untrusted, and a zero-share
or below-minimum order is not a real executable candidate. Consequence:
operators see "기존 포지션을 안전하게 평가할 수 없어 신규 진입 차단" or a minimum-order
SKIP reason instead of `shares must be positive`.

### 2026-06-03: Treat Repeated SKIPs As A Diagnosis Input

Decision: Repeated SKIPs must be grouped by reason category before strategy or
risk changes. Why: SKIP can mean very different things: account safety, minimum
order, thin liquidity, stale weather data, parser uncertainty, or a valid weak
edge. Consequence: use `docs/codex/skip-diagnostics.md` and future paper-only
reporting to find the blocker first; do not weaken thresholds just because the
bot skipped many markets.

### 2026-06-03: Map Token IDs By Explicit YES/NO Outcomes

Decision: `polymarket_client.py` maps tradable YES and NO token IDs only from
explicit `tokens[].outcome` or market `outcomes` labels. Why: `clobTokenIds`
are tradable asset IDs, but their list order is not a safe proof of side. If
YES and NO are swapped, a correct prediction can be recorded as the opposite
paper position. Consequence: missing, duplicated, malformed, or non-YES/NO
outcome labels make discovery skip the market instead of guessing.

### 2026-06-03: Reject Unknown Boolean Environment Values

Decision: Boolean environment variables accept only explicit true aliases
(`true`, `1`, `yes`, `y`, `on`) and false aliases (`false`, `0`, `no`, `n`,
`off`). Why: misspellings such as `REQUIRE_DATE_HINT_FOR_TRADE=treu` can
silently disable a safety switch if every unknown value becomes `False`.
Consequence: startup raises `ValueError` for unknown boolean values, so the
operator must fix the setting before paper trading continues.

### 2026-06-03: Reject Unsafe Numeric Settings At Startup

Decision: `Settings` validates paper-money, risk-fraction, fee-rate, and
runtime-cadence values as soon as settings are created. Why: a negative minimum
order, negative fee rate, fee rate above 1, exposure cap above 1, zero
bankroll, zero cache TTL, zero stream-cycle interval, or zero stale window makes
the paper account measure a broken experiment instead of strategy performance.
Consequence: invalid env values raise `ValueError` with the setting name and
range rule before the live paper runner, dashboard payload, or paper broker can
start from bad assumptions.

### 2026-06-03: Use Executable Depth Health For WebSocket Safety

Decision: WebSocket freshness is refreshed only by executable depth messages:
`book` snapshots and `price_change` level updates. `best_bid_ask` remains an
indicative reference quote and does not refresh the usable order-book clock.
Why: `best_bid_ask` does not carry executable size, so treating it as fresh
depth can let the bot value entries or exits from stale bid/ask levels.
Consequence: stale/dead WebSocket health blocks new entries, records the
operator-readable reason in runner status and decision snapshots, pauses
held-position exit evaluation with `HOLD_STREAM_UNHEALTHY`, and can rebuild a
dead WebSocket receiver thread without falling back to REST polling.

### 2026-06-06: Check Held Token Freshness Before Paper Exits

Decision: Held-position marking and close evaluation must check the executable
order-book freshness of the position's own `token_id`, not only the overall
WebSocket stream freshness. Why: a fresh update on one token means the market
stream is alive, but it does not prove another held YES/NO token has executable
bid depth. Consequence: if the stream is globally stale, all held exits pause;
if only one held token is stale, only that position logs
`HOLD_STREAM_UNHEALTHY`, while positions with fresh executable depth can still
be marked or closed. Indicative `best_bid_ask` updates remain display/reference
data and do not refresh token-level executable freshness.

### 2026-06-03: Ignore Malformed Order-Book Price And Size Levels

Decision: `realtime_orderbook.py` parses `book` and `price_change` levels
defensively. Non-numeric, NaN, infinite, negative, or out-of-range prices and
sizes are ignored at the individual level/change boundary; malformed snapshot
shapes fail closed without replacing the current executable book. Why: the
order book is the paper bot's executable price calculator, and guessed price or
size contaminates entry, exit, and liquidity evidence. Consequence: valid levels
continue updating normally, while broken external stream rows cannot crash the
cache or create guessed paper trades.

### 2026-06-04: Share REST And WebSocket Order-Book Numeric Guards

Decision: REST CLOB book parsing in `polymarket_client.py` and WebSocket book
parsing in `realtime_orderbook.py` both use the shared
`orderbook_validation.py` numeric guards. Why: REST fallback/order-book reads
are the same executable price evidence as streamed depth, so `size=inf`,
`NaN`, zero, negative, or malformed values must not create fake liquidity.
Consequence: valid levels remain usable, while suspicious REST or WebSocket
levels are ignored before VWAP, liquidity checks, paper entries, or exits.

### 2026-06-03: Fail Closed On Explicit Weather Bias Files

Decision: Empty `WEATHER_BIAS_JSON` keeps the neutral default bias table, but an
explicit file must be readable valid JSON with numeric Fahrenheit bias values.
Why: a requested calibration file is part of the forecast evidence. If it is
missing or broken, paper results no longer measure the intended calibrated
strategy. Consequence: invalid explicit bias files surface as
`forecast-unavailable` with zero confidence, so paper entries skip instead of
quietly using uncorrected forecasts.

### 2026-06-03: Stream Paper Report Ledgers Instead Of Materializing CSVs

Decision: `analyze_paper.py` and `shadow_signals.py` read
`paper_decisions.csv` and `paper_trades.csv` as streams, keeping aggregate
counts, market-level latest decisions, resolved outcomes, and bounded shadow
signals rather than whole CSV row lists. Why: these files are the paper
strategy's evidence ledger and naturally grow during long VPS operation.
Consequence: default report semantics stay full-history, but memory use scales
with the number of tracked markets/signals instead of the total row count.

### 2026-06-04: Score Brier From OPEN Entry Probability

Decision: Resolved Brier scoring prefers `entry_p_true` from the actual `OPEN`
row in `paper_trades.csv`; `entry_side_probability`, `entry_net_edge`, and
`decision_ts` are also recorded as structured entry metadata. Why: a later
decision for the same market can update `p_true`, but Brier score must grade
the probability the bot acted on at entry time. Consequence: new paper results
avoid hindsight scoring drift, while legacy CSVs without the new columns still
fall back to the previous latest-decision behavior.

### 2026-06-04: Record Real Station Nowcast HTTP Attempts

Decision: Write one `station_nowcast_request_log.jsonl` row only when the
nowcast provider makes a real AWC METAR or HKO max/min HTTP request. Cache hits
do not write rows. Why: the 15-minute station nowcast cache can only be audited
if observation API attempts are counted separately from cached reuse.
Consequence: operators can compare external observation request volume against
cache settings without treating `StationNowcastObservation` reuse as new API
usage.

### 2026-06-04: Prefetch AWC METAR Stations In Bulk

Decision: AWC METAR nowcast uses `awc_metar_bulk_cache`: one AWC JSON request
asks for the enabled ICAO station IDs, and each station parses its own
station-date rows from that shared response. HKO remains a separate single CSV
fetch because it already returns the needed max/min table in one file. Why:
calling AWC once per METAR station at refresh start can create a burst of
avoidable requests, while one shared response still preserves same-station
observed high/low derivation. Consequence: AWC request logs count one bulk HTTP
attempt with `station_id=METAR_BULK` and `requested_station_ids`, not one row
per station.

### 2026-06-06: Require Explicit Station IDs In AWC METAR Rows

Decision: AWC METAR bulk nowcast rows must have an explicit `icaoId` or
`station_id` that matches the requested settlement station case-insensitively.
Rows with no station ID are skipped instead of inheriting the requested station
ID. Why: a row without a station label cannot prove which official observation
station produced the temperature, and wrong-station evidence contaminates
paper-profit validation. Consequence: valid same-station rows still contribute
observed high/low, while unlabeled rows produce no nowcast evidence.

### 2026-06-06: Refresh Realtime Nowcast Separately From Forecasts

Decision: Realtime paper evaluation can recalculate market `WeatherSignal`
values after `STATION_NOWCAST_CACHE_TTL_SECONDS` even when the larger
market-discovery/stream cycle has not restarted. Why: Open-Meteo forecasts are
budget-sensitive and slow-moving, but same-day observed high/low evidence can
change intraday and should affect entry/exit judgment. Consequence: the runner
reuses the existing forecast client/cache for forecast evidence while allowing
station nowcast to refresh on its own cache TTL.

### 2026-06-04: Gate Untradable Markets Before Forecast Requests

Decision: Run `pre_forecast_tradeability_gate` before calling Open-Meteo from
the paper runner. When `REQUIRE_DATE_HINT_FOR_TRADE=true`, a temperature market
with no parsed `date_hint` logs SKIP before forecast fetching. The same gate
also blocks non-temperature, unsupported-city, and non-trading-ready markets
before forecast calls. Why: `forecast_request_log.jsonl` measures real external
forecast attempts, so markets that cannot trade should not spend API budget.
Consequence: `evaluate_market()` still keeps its date-hint fail-closed guard,
but the runner now avoids the upstream forecast request and records the SKIP
diagnostic earlier.

### 2026-06-03: Keep SKIP Diagnostics Out Of Recent Trades

Decision: Dashboard `Recent Trades` uses cached actual trade actions: `OPEN`,
`ADD`, `CLOSE`, `SETTLED`, and `PARTIAL_CLOSE`; realized rows and realized
equity points use only realized actions: `CLOSE`, `SETTLED`, and
`PARTIAL_CLOSE`. SKIP actions remain ledger diagnostics but are not shown as
executed trades. Why: repeated exposure-cap or data-quality SKIPs can dominate
the tail of `paper_trades.csv` and hide older closes. Consequence: the
dashboard shows paper-trading activity rather than scanner rejection noise,
while cumulative scanner totals still preserve SKIP context.

### 2026-06-03: Settle Closed Markets From Exact Binary Outcome Prices

Decision: If a closed Polymarket binary market has no explicit winner field,
paper settlement may infer the winner only from exact YES/NO `outcomePrices`
of `1/0` or `0/1`. Why: some closed weather markets expose the final payout
prices without `winningOutcome`; leaving those open makes the paper account
look riskier and hides realized PnL. Consequence: clear closed markets settle
in paper mode, while ambiguous prices such as `0.52/0.48` remain open until
clear evidence appears.

The realtime WebSocket runner must run this settlement check before opening a
stream cycle and remove newly settled markets from the token subscription set.
The batch `run_cycle()` path already checked settlements; the live service path
needs the same behavior.

### 2026-06-03: Dashboard Links Use Event Slugs

Decision: Open-position dashboard cards link to
`https://polymarket.com/ko/event/{event_slug}`. If old state only has a market
slug with a terminal weather condition suffix, the dashboard strips that suffix
before building the link. Why: Polymarket event pages exist at the event slug,
while condition-specific suffixes such as `-25corbelow` and `-30corhigher`
produce 404 pages. Consequence: new paper positions persist `RawMarket.event_slug`
in metadata, and old positions remain linkable through conservative suffix
normalization.

### 2026-06-04: Keep Paper Execution Temperature-Only

Decision: Remove rain, snow, precipitation, wind, humidity, and other
non-temperature weather markets from the paper strategy path. Why: those
markets have too little useful liquidity for the current profitability
experiment, and scoring them wastes forecast calls and adds SKIP noise.
Consequence: discovery uses temperature category pages only, the parser marks
non-temperature weather questions as unsupported, Open-Meteo requests only
temperature daily variables, and the runner filters out-of-scope markets before
probability estimation, WebSocket subscription, or paper trade logging.

### 2026-06-04: Slow Open-Meteo Forecast Refresh And Persist Daily Limit Cooldown

Status: Superseded for refresh cadence by the 2026-06-06 drip-feed rule, but
the persisted 429 cooldown behavior remains active.
Decision: Persist HTTP 429 cooldowns in `forecast_rate_limit_state.json`.
Daily quota responses use the next UTC reset time, while `Too many concurrent
requests` uses a 15-minute `concurrent` cooldown. Why: the ensemble API can
count one weather request as multiple equivalent calls, but a burst/concurrency
warning is not the same as exhausting the whole daily quota. Consequence:
realtime order books still stream, cached forecasts remain usable, new forecast
HTTP calls pause for the right cooldown window, and the dashboard can
distinguish `rate_limit_kind`.

### 2026-06-06: Do Not Immediately Retry Open-Meteo ReadTimeouts

Decision: A `ReadTimeout` is not retried immediately by
`OpenMeteoEnsembleClient`. The failed forecast cache key gets a 30-minute
temporary failure memo, while other forecast keys can continue. Why: if the
client times out after 20 seconds, Open-Meteo may still be processing the
server-side request; calling the same key again can turn one slow city into a
request stack. Consequence: `forecast_request_log.jsonl` records only the real
timed-out HTTP attempt with `temporary_failure_kind=read_timeout`, same-key
re-entry fails fast as unavailable, and the key is retried only after the memo
expires.

### 2026-06-06: Drip-Feed Open-Meteo Forecast HTTP Calls

Decision: Real Open-Meteo forecast HTTP requests are serialized globally across
`OpenMeteoEnsembleClient` instances and spaced by
`FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60` after the previous real request
finishes or times out. The forecast cache TTL defaults to
`FORECAST_CACHE_TTL_SECONDS=2400`, so a roughly 40-minute 40-city rotation can
refresh one city at a time instead of keeping every successful forecast valid
through a long multi-market window. Why: Open-Meteo rejected the prior bursty
pattern with `Too many concurrent requests`, and a local timeout can still
represent a server-side request in progress. Consequence: cache hits return
immediately, but cache misses follow the shape "city request starts ->
finish/timeout -> wait at least 60 seconds -> next city request"; do not batch
many cities into one forecast request or immediately retry a slow city.
`FORECAST_REQUEST_MIN_INTERVAL_SECONDS` values below 60 fail startup because
they weaken the production budget guard instead of changing a harmless tuning
knob.

### 2026-06-06: Judge Exits By After-Fee Liquidation PnL

Decision: Profit exits and edge-faded loss limits use after-fee liquidation
PnL, not raw token-price movement. Why: a token can move up enough to satisfy a
price-only profit percentage while the paper account still loses money after
entry and exit fees. Consequence: `assess_exit()` calculates sell-now proceeds
minus exit fee minus `PaperPosition.cost_usd`; exit reasons show both
`net_pnl` and `raw_pnl`, but configured profit/loss thresholds use `net_pnl`.

### 2026-06-04: Keep Runtime Raw Diagnostics Bounded By Default

Decision: Disable normal raw decision snapshots by default, keep only error
raw diagnostics unless `RAW_SNAPSHOTS_MODE=debug` is deliberately enabled, and
rotate active raw snapshot files over 100MB into compressed archives with
7-day retention. If disk usage is dangerous, suspend raw snapshot writes and
record `raw_snapshot_storage` in `paper_runner_status.json`.
Why: raw diagnostics can grow faster than source ledgers and fill the VPS
disk, while `paper_state.json`, `paper_trades.csv`, and `paper_decisions.csv`
must remain evidence. Consequence: new decision and portfolio rows are compact
summaries, raw diagnostics are opt-in or error-only, and disk pressure blocks
only raw snapshot writes rather than deleting or truncating paper ledgers.

### 2026-06-05: Preserve Temperature Range Bucket Endpoints

Decision: Parse temperature markets such as `86-87F`, `62-63F`, and `22-23C`
as `range` buckets with stored inclusive lower and upper endpoints. Why:
treating `86-87F` as exact `87F`, or widening it to a half-step interval,
changes the YES condition used by `p_true`. Consequence: probability applies
the exact displayed inequality such as `86.0 <= temperature_f <= 87.0`, and
portfolio scenarios use the same range without expanding or shrinking it.

### 2026-06-05: Check Final Entry Liquidity After Sizing

Decision: `_side_result()` probes ask depth with `MIN_ORDER_USD`, calculates
fee-aware edge and the actual `size_usd`, then rechecks ask depth for final
`size_usd`. If the final VWAP differs from the probe price, edge, fees, shares,
and expected return are recalculated from the final price. Why: using
`bankroll * MAX_SINGLE_MARKET_FRACTION` as the first liquidity target can reject
a real $10 paper order just because $100 of depth is unavailable. Consequence:
small executable paper candidates survive, while final-order ask-depth
shortfalls still fail closed as SKIP instead of recording unavailable fills.

### 2026-06-05: Scale Paper Entries To Executable Depth And Gate Add-Ons

Decision: If final entry sizing asks for more ask depth than exists, the paper
runner may scale the entry down to the confirmed executable amount, but only
when that amount still meets `MIN_ORDER_USD`. Same-market opposite-side entries
remain blocked. Same-side add-ons are allowed only when the current executable
price is at least `ADD_TO_POSITION_DROP_PCT` below the existing average entry,
the current side probability remains above `probability_stop_threshold`, edge
and expected return stay positive, and the normal cash and exposure caps leave
at least `MIN_ORDER_USD`. Why: skipping every partially executable candidate is
too conservative, but averaging down on a broken thesis compounds losses.
Consequence: partial entries log their reduced sizing, add-ons update the
existing `paper_state.json` position and write an `ADD` row to `paper_trades.csv`,
and the dashboard treats `ADD` as paper trade activity but not realized PnL.

### 2026-06-05: Reject Unknown Paper Sizing Modes At Startup

Decision: `SIZE_MODE` must be either `fixed_fraction` or `kelly`, with
case-normalized values stored in `Settings`.
Why: The setting controls paper order size. A typo such as `kellyy` can make
the runner use the fixed-fraction branch while the operator believes Kelly
sizing is active.
Consequence: Invalid values raise `ValueError` during settings creation, before
paper trading starts and before risk evidence is contaminated.

### 2026-06-06: Validate Dashboard Port And Shadow Research Limits At Startup

Decision: `DASHBOARD_PORT` must be an integer from 1 to 65535. Shadow research
integer controls must be explicit safe counts: `SHADOW_MAX_MARKETS`,
`SHADOW_MAX_TRADES_PER_MARKET`, and `SHADOW_COMPARE_WINDOW_SECONDS` must be
positive integers, while `SHADOW_MAX_ROWS` must be a non-negative integer so
zero can intentionally keep no shadow rows.
Why: The dashboard port is the local server's door number, and invalid values
should fail before the dashboard tries to bind. Shadow settings control bounded
public-data research; negative or nonsensical limits can quietly distort
research samples.
Consequence: Bad values raise `ValueError` during `Settings` creation and
paper trading remains unchanged and paper-only.

### 2026-06-05: Cap Portfolio Allocation Size Candidates

Decision: `_allocation_sizes` keeps one-dollar candidate spacing for small
allowed ranges but caps large allocation grids at 50 sizes while preserving the
minimum order, allowed maximum, and any affordable preferred size.
Why: The event portfolio selector compares many leg and size combinations. A
large paper bankroll or higher order cap can turn one-dollar grids into tens of
thousands of candidates, and two-leg combinations multiply those counts.
Consequence: Paper-only portfolio selection stays bounded and responsive as the
account scales, while small-account behavior and key order-size anchors remain
stable.

### 2026-06-05: Label Dashboard Decision-Total Scope

Decision: `/api/status` exposes `decision_totals_exact` and
`decision_totals_scope` beside scanner decision, skip, and entry counts.
Why: Large `paper_decisions.csv` ledgers may initialize dashboard scanner
totals from recent tail rows to keep the API responsive, and those bounded
counts must not look like all-time cumulative totals.
Consequence: Small ledgers report exact full-history totals. Oversized ledgers
keep the performance guard, but operators and clients can see
`recent_tail`/`false` before interpreting the scanner numbers.

### 2026-06-06: Decouple WebSocket Receiving From Strategy Evaluation

Decision: The WebSocket receiver callback updates the realtime order-book cache
and enqueues evaluation work only. Strategy evaluation, portfolio selection,
paper decision logging, and close checks run in a separate bounded
coalescer/worker that groups short bursts by weather event before evaluation.
Why: WebSocket receiving is the market-price telephone line; if it performs
slow probability/portfolio math or appends too many decision rows inline, later
price updates can wait behind that work and `paper_decisions.csv` can grow from
noise rather than useful judgment evidence. Consequence: the paper strategy
still preserves `paper_decisions.csv`, but bursty updates for the same
event/city/date are collapsed to latest-state evaluations, and
`paper_runner_status.json` reports queue depth, coalesced updates, dropped
updates, evaluation errors, and recent evaluation timing.

### 2026-06-06: Fail Closed On Paper State And Trade Ledger Transaction Drift

Decision: `OPEN`, `ADD`, `CLOSE`, and `PARTIAL_CLOSE` are guarded by a
`paper_state.json.journal` transaction marker. The broker writes the marker
before mutating the paper account, saves `paper_state.json`, appends the
matching `paper_trades.csv` row, then clears the marker only after both ledgers
finish. Why: `paper_state.json` is the current account book, while
`paper_trades.csv` is the execution diary. If a disk or CSV write fails in the
middle, the bot must not quietly keep trading from one changed ledger and one
unchanged ledger. Consequence: a leftover journal halts further paper
accounting writes and makes the next startup fail closed for operator
reconciliation. Startup also fails closed when the state file is missing but
the trade ledger already has executed accounting actions, when open positions
exist but the trade ledger is missing or empty, or when an open position has no
matching `OPEN` row in the trade ledger.

### 2026-06-06: Keep Closed Markets Out Of New Paper Entries

Decision: New paper-entry discovery and `_open_position_if_needed()` allow only
active and not-closed markets. Closed markets can still be loaded by market ID
for existing paper-position settlement.
Why: A closed market is no longer a real buyable opportunity, even if it
contains useful final-outcome evidence. Treating it as both a settlement
answer sheet and a new-buy candidate pollutes the paper account's profitability
test.
Consequence: Category-slug event expansion, paginated discovery, and the final
entry gate skip inactive/closed markets, while `maybe_settle_resolved_positions`
continues to use closed markets to close already-held paper positions.
