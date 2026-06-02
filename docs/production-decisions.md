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
  station-code conflict. Unknown, stale, malformed, unsupported, or suspicious
  data means skip.
- Forecast rows must match the target market date exactly. Nearby forecast
  dates are not substitutes and produce `forecast-unavailable`.
- Forecasts refresh every 30 minutes by default. Order books use the Polymarket
  CLOB WebSocket stream, and open-position token IDs stay subscribed until the
  position closes or settles.
- `best_bid_ask` is indicative price data only. Executable depth comes from
  `book` snapshots or `price_change` updates, not assumed sizes.
- Entry decisions are fee-aware. `p_exec` is executable VWAP; `size_usd` is the
  all-in paper-entry budget; `size_shares` is the fee-adjusted actual held
  quantity; paper cash, liquidation bankroll, and dashboard PnL use after-fee
  accounting.
- City-date weather buckets share one correlated-risk budget. At most two
  complementary legs are selected per event, with a `$10` minimum leg and
  conservative city, event, and total exposure caps.
- Profit exits may recover principal and keep a bounded settlement runner only
  when conservative settlement value beats fee-adjusted sell-now value. Active
  runners are rechecked; they are not risk exemptions.
- Same-day nowcast is allowed only from explicitly mapped same-station official
  sources. Observed high-so-far is evidence only for daily-high markets, and
  observed low-so-far is evidence only for daily-low markets. Providers should
  derive both extrema from one station-date response and cache it. No
  nearby-station or city-center substitutions.
- Public whale/external-signal research remains shadow-only. Promotion requires
  paired resolved public-signal and bot-entry samples, then only suggests a
  paper-only A/B experiment.
- Known-good commands belong in `docs/codex/known-good-commands.md`; fresh work
  should use them before inventing command shapes.
- `paper_state.json` is an account book. Saves use atomic temp-file replacement,
  and existing corrupt or invalid paper state fails closed instead of resetting.
- Public dashboard exposure requires a real `DASHBOARD_TOKEN`; empty,
  placeholder, basic, default, change-me style, or other obvious example tokens
  stop startup before binding to a public host.

## Compact Ledger

### 2026-05-26: Trade Only Verified Polymarket Weather Stations

Decision: Trade only the 41 cities mapped in `src/weather_bot/stations.py`.
Why: Forecasting the wrong station destroys weather-market edge. Consequence:
weather-shaped markets are skipped unless their parsed city is in `STATION_MAP`.

### 2026-05-26: Remove City-Centroid Trading Fallback

Decision: Missing verified station returns `unsupported-station` with zero
confidence. Why: city-centroid forecasts are exploration data, not production
trading data. Consequence: parser support and trading support use the same
verified station set.

### 2026-05-26: Forecast Cache Refresh Is 30 Minutes

Decision: Forecast cache TTL and refresh interval default to 1800 seconds. Why:
forecast data moves slower than order books. Consequence: WebSocket evaluations
reuse cache until it expires.

### 2026-05-26: Order Books Use The CLOB WebSocket Stream

Decision: Default order-book monitoring uses the Polymarket CLOB WebSocket
market channel. Why: realtime order-book monitoring was required; a REST loop
is polling. Consequence: discovery/forecasts refresh slowly while book events
trigger paper evaluation.

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

Decision: Use AWC METAR for 39 ICAO stations, HKO daily max/min CSV for Hong
Kong, and keep Karachi/OPMR forecast-only. Why: Hong Kong has official HKO data;
OPMR lacked a verified same-station provider. Consequence: no substitutions.

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

### 2026-06-03: Use Fee-Adjusted Shares As The Canonical Entry Quantity

Decision: `EdgeResult.size_shares` means the actual shares bought after entry
fees, not gross `size_usd / p_exec` shares. Why: portfolio scenarios that use
gross shares overstate settlement payoff and expected profitability. Consequence:
entry filtering, portfolio selection, scenario PnL, and paper broker opens all
share the same all-in-budget quantity formula.

### 2026-06-03: Treat Paper State As A Fail-Closed Account Book

Decision: Save `paper_state.json` by writing a complete temp file and replacing
the live file with `os.replace`; reject corrupt, unreadable, or structurally
invalid existing state with `PaperStateLoadError`. Why: cash and open positions
are the paper strategy's ledger, not a rebuildable cache. Consequence: missing
state can initialize on first run, but an existing bad state stops paper trading
until an operator investigates or restores a good file.

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
dashboard refuses to start unless `DASHBOARD_TOKEN` is non-empty and
non-placeholder. Why: binding to `0.0.0.0` exposes the service to anyone who can
reach the URL, including automated scanners. Consequence: copied example files,
empty tokens, or basic/default/change-me tokens fail before the HTTP server
binds; local development can still run without a token, and query-token values
are redacted from dashboard logs.

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
