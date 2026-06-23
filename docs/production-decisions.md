# Production Decisions

This is the active rule book for the paper-only Polymarket temperature bot.

Historical notes belong in focused `docs/solutions/` entries. Active work belongs in `docs/active/current-task.md`. Temporary step orders belong under `docs/active/` and must be deleted after completion.

---

## 1. Current Phase

Current phase:

```text
paper-only strategy validation
```

Live trading remains out of scope.

The evidence window and readiness gates are defined in `docs/paper-validation-runbook.md`. Passing them does not authorize live trading; it only permits discussion of a separate safety project.

Do not build or enable:

```text
wallet connection
private keys
signing
real orders
redemption
claim
copy trading
LiveBroker
```

A paper PnL number is trusted only if it uses:

```text
executable ask depth for entry
executable bid depth for exit
fees
spread
slippage
stale-data fail-closed behavior
official settlement-station observations
replayable ledgers
```

---

## 2. Replaced Decisions From Older Strategy

The previous active default was effectively lock-only and too inactive.

Remove or stop relying on these assumptions:

```text
OFFICIAL_NOWCAST_ENTRY_ONLY=true as the long-term active default
endDate as hard order cutoff
near local midnight as the main new-entry window
best ask or midpoint as executable fill proof
all integer labels sharing one universal rounding rule
HKO decimal values treated as fully verified without audit
```

Replace them with:

```text
STRATEGY_MODE=hybrid_observation_edge
CLOB tradability gate at final pre-trade
official-station intraday observation edge
regional high/low strategy profile
settlement precision profile
strategy-mode ledger tags
step-by-step implementation and verification
```

---

## 3. Market Universe

Trade temperature markets only.

Use:

```text
STATION_MAP = registered station universe
TRADING_READY_STATION_MAP = paper execution universe
```

Karachi stays excluded unless station-rule evidence is reconciled.

Non-temperature weather markets must fail closed before:

```text
station-signal calculation
order-book subscription
paper trade logging
```

Unknown, stale, malformed, unsupported, suspicious, missing, or conflictful data means skip.

Market title parsing is not enough. Keep and compare:

```text
question text
rule/resolution text when available
station/source evidence
unit
bucket shape
station-local date
UTC window
outcome labels
token IDs
```

Rule/title/station/unit/date conflicts fail before trading.

---

## 4. Polymarket Tradability

Do not treat Gamma `endDate` as the hard trading cutoff.

`endDate` may be useful for sorting, event-date interpretation, and diagnostics, but it is not enough to prove the CLOB is closed or accepting orders.

A new paper entry requires all of these at final pre-trade time:

```text
market.active is true
market.closed is false
market.archived is not true
CLOB accepting_orders is true
CLOB enable_order_book is true
YES token ID exists
NO token ID exists
ask-side executable depth exists
final ask-side executable VWAP is computable
spread gate passes
fee-aware edge passes
expected net return gate passes
portfolio and exposure gates pass
same-station evidence is fresh and valid
```

If CLOB tradability cannot be verified, fail closed with:

```text
SKIP_TRADABILITY_UNKNOWN
```

`accepting_orders=false` fails with:

```text
SKIP_NOT_ACCEPTING_ORDERS
```

`enable_order_book=false` fails with:

```text
SKIP_ORDERBOOK_DISABLED
```

Do not silently skip these cases. Record stable reason codes in decisions, diagnostics, or grouped counters.

---

## 5. Order Book And Execution Realism

Use Polymarket CLOB WebSocket market stream by default.

REST order-book snapshots are allowed only as bounded verification/resync helpers. They must not replace WebSocket monitoring, trigger evaluations by themselves, or write raw order books to runtime ledgers.

Executable depth comes from:

```text
full book snapshots
valid price_change updates after a token has a full-depth snapshot
bounded REST /book seed or resync when configured
```

Indicative quotes are not executable proof.

Entry rule:

```text
entry = ask-side executable VWAP for the final size
```

Exit rule:

```text
exit = bid-side executable VWAP for the final close size
```

No bid depth means no successful `CLOSE`.

Partial bid depth means `PARTIAL_CLOSE` or hold blocker, not fake full close.

---

## 6. Official Station Evidence

The strategy is official settlement-station observation first.

Do not enter from generic weather-model forecasts.

Same-station nowcast is allowed only from explicitly mapped official sources:

```text
AWC METAR for supported ICAO stations
HKO for Hong Kong
other mapped official source only when explicitly implemented and tested
```

Station observations must match the station-local target date. Nearby dates are not substitutes.

Provider request floors must be respected. Do not retry-bomb providers after stale, malformed, or failed evidence.

---

## 7. Settlement Precision

Settlement precision must be explicit per station/source.

Required profile fields:

```text
city
station_id
source_type
unit
reporting_precision
bucket_model
confidence
note
```

Accepted bucket models:

```text
whole_degree_source_display_band
one_decimal_range_containing
unknown
```

Rules:

```text
whole-degree source display:
  integer N uses [N.0, N+1.0) as the strategy evidence band
  do not use hidden half-step ranges

one-decimal source display:
  use range-containing interpretation only when rules or audited settlements support it
  until verified, confidence=needs_audit and size is reduced

unknown precision:
  block new entries
```

HKO/Hong Kong starts as:

```text
source_type=HKO
reporting_precision=0.1C
bucket_model=one_decimal_range_containing
confidence=needs_audit
```

HKO can become `verified` only after historical settled Polymarket outcomes are audited against HKO raw Absolute Daily Max/Min values.

---

## 8. Bucket Direction Rules

Daily-high exact integer N:

```text
N <= observed_high < N+1.0  => still inside N bucket
observed_high >= N+1.0     => N YES impossible, N NO strong
```

Daily-low exact integer N:

```text
N <= observed_low < N+1.0  => still inside N bucket
observed_low < N           => N YES impossible, N NO strong
```

Tail examples:

```text
N or below for high/low uses the wording from the actual market rule
N or higher for high/low uses the wording from the actual market rule
```

Threshold markets must follow actual rule wording:

```text
above
at or above
below
at or below
highest
lowest
```

These are not interchangeable.

---

## 9. Strategy Modes

Allowed values:

```text
lock_only
intraday_observation_edge
hybrid_observation_edge
```

Default:

```text
hybrid_observation_edge
```

### lock_only

Conservative official-station lock strategy.

Use when the official observed value has already made a side impossible or nearly impossible, or when near local event close the bucket remains safely inside the displayed range.

### intraday_observation_edge

More active strategy using official settlement-station observations during the city-local high/low formation window.

Minimum side probability:

```text
0.90
```

Strong side probability:

```text
0.97
```

Paper sizing by calibrated selected-side probability:

```text
< 0.90:
  keep ordinary city exposure at or below 20% of bankroll

0.90 <= probability < 0.95:
  target 30% of bankroll for one exclusive city-date position

probability >= 0.95:
  target 50% of bankroll for one exclusive city-date position

both high-confidence tiers:
  still require executable depth, positive fee-aware edge, final CLOB checks,
  bounded VWAP price impact, complete station-local daily extremes, fresh
  official station evidence, and the 50% single-market ceiling
```

An official station signal calculated within the station cache TTL is already
the current observation evidence. At final pre-trade, reuse that signal and
recalculate the executable CLOB price and economics. Fetch the station again
only when the signal has aged past the TTL; do not create a second provider
failure opportunity a few seconds after a successful observation.

### abnormal_official_station_mispricing

A tag, not a separate independent source of truth.

Use when:

```text
side_probability >= 0.90
net_edge >= abnormal min net edge
expected net return passes
final ask VWAP exists
spread passes
tradability gate passes
```

Abnormal price opportunities may size larger, but still obey all exposure caps.

---

## 10. Station Formation-Time Strategy Profile

Do not use one world-wide confirmation hour or a guessed provider-specific
hour. Read the verified sibling manifest for the station, target month, and
high/low direction:

```text
monitoring_start_local_minute
first_final_high_local_minute q25 / median / q75
first_final_low_local_minute q25 / median / q75
remaining movement probability from the matching residual histogram
```

Before `monitoring_start_local_minute`, ordinary residual-probability entries
are blocked. Exact-bucket entries also wait until the matching direction's q75
formation minute. A same-day, reset-verified observation that has irreversibly
broken an exact bucket may still produce strong NO before q75. Missing or
hash-mismatched formation metadata fails closed. HKO remains `needs_audit` and
cannot use the verified residual path.

The final entry check must still verify current CLOB `accepting_orders` and
`enable_order_book`. When the CLOB response itself supplies a close time and it
precedes the city's historical high-formation window, block the high residual
strategy. Gamma `endDate` alone is not this evidence.

HKO additionally requires a persistent midnight handoff state. Missing prior-
day baseline, an unchanged prior-day pair after midnight, a same-day high
decrease, or a same-day low increase blocks the source before probability
calculation. No fixed “allow after HH:MM” exception exists.

AWC METAR responses are current observations, not proof of complete daily
extremes. The provider must persist each station's reports across the local
midnight handoff. A restart without the prior observation baseline, a missed
continuity interval, date regression, or incomplete current local day blocks
new entries before probability calculation.

---

## 11. Active Paper Defaults

Recommended active paper defaults for this upgrade:

```text
BANKROLL_USD=1000
SIZE_MODE=kelly
FRACTIONAL_KELLY=0.25
ENTRY_FRACTION=0.20
MIN_ORDER_USD=10.00
MIN_NET_EDGE=0.08
ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.04
WEATHER_TAKER_FEE_RATE=0.05
MAX_TOTAL_EXPOSURE_FRACTION=0.90
MAX_CITY_EXPOSURE_FRACTION=0.20
MAX_EVENT_DATE_EXPOSURE_FRACTION=0.10
MAX_EVENT_PORTFOLIO_LEGS=2
OFFICIAL_NOWCAST_ENTRY_ONLY=false
STRATEGY_MODE=hybrid_observation_edge
INTRADAY_OBSERVATION_EDGE_ENABLED=true
INTRADAY_MIN_SIDE_PROBABILITY=0.90
INTRADAY_STRONG_SIDE_PROBABILITY=0.97
INTRADAY_BASE_ENTRY_FRACTION=0.10
INTRADAY_STRONG_ENTRY_FRACTION=0.25
INTRADAY_ABNORMAL_PRICE_ENTRY_FRACTION=0.35
INTRADAY_ABNORMAL_MIN_NET_EDGE=0.20
INTRADAY_HKO_NEEDS_AUDIT_FRACTION_MULTIPLIER=0.25
HKO_ROLLOVER_STATE_PATH=data/hko_rollover_state.json
```

These values are paper-experiment defaults, not live-trading settings.

---

## 12. Risk And Portfolio

Same-market opposite-side entries remain blocked.

Same-side add-ons are allowed only when price, station-side probability, edge, expected return, cash, and exposure caps still pass.

City-date markets share one correlated-risk budget.

At most two complementary non-overlapping legs may be selected for one city-date event unless the user explicitly approves a separate portfolio-risk redesign.

Non-overlap means winning payoff outcomes, not merely different displayed
temperature buckets. A `YES 31` leg and a `NO 32` leg overlap because both win
when the final value is 31; two different NO legs also overlap over most
outcomes and cannot be treated as diversification.

A calibrated probability from 0.90 up to 0.95 targets 30% of bankroll, and
0.95 or above targets 50%, as one exclusive city-date position. Executable
liquidity, bounded VWAP impact, and the 50% single-market ceiling may reduce or
block the actual fill. Probabilities below 0.90 remain under the ordinary 20%
city cap.

`SKIP` means the held side could not be evaluated; it is not `NO`. Probability
stops require a latest edge whose side matches the held position. Missing or
invalid order-book evaluation must hold the existing probability rather than
complementing it.

VWAP price impact must pass the same absolute and relative limits used by the
entry spread gate, both during evaluation and final pre-trade. Closing a
city-date position also blocks another entry based on the identical official
station observation timestamp; a newer observation is required before capital
can rotate into a sibling bucket.

Drawdown circuit breakers block new entries only. Held exits and settlements must continue.

---

## 13. Ledgers

`paper_state.json` is the account book, not a cache.

`paper_trades.csv` is the paper execution receipt ledger.

`paper_decisions.csv` is the strategy decision evidence ledger.

Existing corrupt, structurally invalid, or unsafe state fails closed instead of reset.

New rows should carry compact evidence:

```text
strategy_mode
signal_family
price_anomaly
settlement_precision_confidence
city
target_date
shape
side
side_probability
station_id
observed_high
observed_low
entry_vwap
exit_vwap
spread
fee_rate
expected_net_return
reason
```

Old rows without these columns must remain readable.

---

## 14. Reporting

Minimum paper report must include:

```text
realistic net PnL
open count
close count
partial close count
no-liquidity blocker count
not accepting orders skip count
orderbook disabled skip count
tradability unknown skip count
wide spread skip count
no executable depth skip count
strategy_mode breakdown
signal_family breakdown
price_anomaly breakdown
city breakdown
high/low breakdown
settlement_precision_confidence breakdown
HKO needs_audit exposure/PnL
```

Do not trust a 24-hour result that lacks these breakdowns.

---

## 15. Completed Upgrade State

The observation-edge implementation is now part of the permanent paper strategy contract. Future changes start from the current code, tests, this decision file, and `docs/strategy-validation-roadmap.md`; they must not depend on a completed temporary work order.

When a future temporary implementation order is finished:

1. delete that temporary file,
2. reset `docs/active/current-task.md` to `Status: none`,
3. keep AGENTS.md, this file, and the roadmap,
4. report the changed files and verification results.
