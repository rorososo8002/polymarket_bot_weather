# Production Decisions

## 2026-05-26: Trade Only Verified Polymarket Weather Stations

Decision: The bot trades only the 41 cities currently mapped in `src/weather_bot/stations.py`.

Why: Weather-market edge can disappear if the bot forecasts the wrong station. Previous defaults used city or common climate stations that differed from Polymarket rules, such as Seoul ASOS vs Incheon Intl Airport, Heathrow vs London City Airport, and NYC Central Park vs LaGuardia.

Consequence: A weather-shaped Polymarket market is skipped unless its parsed city is in `STATION_MAP`.

## 2026-05-26: Remove City-Centroid Trading Fallback

Decision: `estimate_weather_probability()` returns `unsupported-station` with zero confidence when no verified settlement station exists.

Why: City-centroid forecasts are useful for exploration but unsafe for production trading. Unknown settlement stations must fail closed.

Consequence: Parser support and trading support use the same verified station set. Unmapped cities are not treated as supported bot cities unless they are added to `STATION_MAP`.

## 2026-05-26: Forecast Cache Refresh Is 30 Minutes

Decision: Default forecast cache TTL and refresh interval are 1800 seconds.

Why: Forecast data is slow-moving compared with order books. Pulling forecasts every fast loop wastes API quota and increases rate-limit risk.

Consequence: Order-book stream evaluations reuse cached Open-Meteo ensemble responses until the cache expires.

## 2026-05-26: Order Books Use The CLOB WebSocket Stream

Decision: Default order-book monitoring uses the Polymarket CLOB WebSocket market channel.

Why: The user requirement is real-time order-book monitoring. A REST loop is still polling and can miss the intended execution behavior.

Consequence: `run_forever()` enters `run_realtime_forever()` by default. Market discovery and forecasts refresh every 30 minutes, while WebSocket `book`, `price_change`, `best_bid_ask`, and tick-size events update the order-book cache and trigger paper-trade evaluation.

## 2026-05-26: Keep Paper Trading As The Execution Boundary

Decision: No private keys, signing, or live order submission were added.

Why: The repository is explicitly structured as a live-data paper-trading bot. Moving to real-money execution requires separate safety work.

Consequence: "Trade" in this implementation means paper open/close through `PaperBroker`. Live execution remains a future explicit project.

## 2026-05-26: Probability Stop Replaces Fixed Price Stop

Decision: The bot no longer uses a fixed entry-price percentage stop. It records `probability_stop_threshold` at entry and closes when the current side probability falls below that threshold.

Why: Weather-market risk is driven more by forecast probability deterioration than by a fixed token-price move. A static price stop can fire on thin bid/ask noise even when the forecast thesis is unchanged.

Consequence: `PROBABILITY_STOP_DROP_THRESHOLD=0.10` is the default. YES positions compare current `p_true` to entry-side probability; NO positions compare `1 - p_true`. Decision logs use `probability_stop_threshold` instead of a price stop column.

## 2026-05-28: Invalid Edge Sentinels Are Not Exit Signals

Decision: `edge faded` exits require a fresh executable held-side edge with a non-empty `p_exec`. A sentinel such as `net_edge=-999` with `p_exec=None` means the side could not be evaluated from the current order book and must not close a position by itself.

Why: During Oracle paper trading, a Seoul NO position opened with a model target far above entry, closed about 12 minutes later for only about 0.1% because `latest_edge=-999` satisfied the old edge-faded condition, then immediately reopened when the next valid update showed the NO edge was still strong. That churn was caused by treating an evaluation failure as a real negative edge.

Consequence: Invalid order-book/evaluation updates can still appear as `DECISION SKIP`, but they do not force an edge-faded close. Probability stops, target exits, overheated exits, valid executable edge fades, and max-hold exits still work.

## 2026-05-28: Keep Held Position Tokens In WebSocket Subscriptions

Decision: The realtime stream registry combines current discovery markets with every open paper position. If market hydration fails, the runner reconstructs a minimal market from the held position so its token remains subscribed.

Why: Discovery can roll forward to newer dates while an older market position is still open. Dropping that token from the WebSocket subscription leaves mark prices and exit checks stale even though the position still has economic risk.

Consequence: Open positions keep receiving live order-book updates while they remain in paper state. Stream status counts the combined discovery-plus-held registry, so the visible binary-market count can exceed the current discovery result.

## 2026-05-28: Explain Two-Sided Liquidity Rejections In SKIP Logs

Decision: When neither YES nor NO has a valid executable liquidity evaluation, the final `DECISION SKIP` reason includes both per-side rejection details.

Why: An opaque `No valid side evaluated.` message cannot tell an operator whether the book lacked bids, lacked asks, had extreme prices, had an excessive spread, or lacked exit depth.

Consequence: Paper decision logs remain fail-closed while exposing the concrete YES and NO liquidity filters that blocked entry.

## 2026-05-28: Strategy Changes Must Be Research-Backed And Reproducible

Decision: Production strategy work must be documented as an executable specification, not a chronological activity log.

Why: A fresh AI should be able to open the folder, read the production docs, and reproduce the same bot behavior and research priorities. The core mission is to improve risk-adjusted paper returns through market research, mathematical reasoning, and empirical trade review.

Consequence: Future strategy changes should state the expected-value, calibration, Kelly/fractional-Kelly, liquidity, slippage, forecast-error, or drawdown rationale behind the rule; update production docs alongside code; and keep live-wallet execution out of scope unless explicitly requested.

## 2026-05-28: Dashboard Scanner Counts Are Cumulative

Decision: `dashboard.py` keeps cached decision and trade totals for diagnostics,
but the visible Scanner Intelligence panel shows only operator-useful current
state: open positions, open entry cost, latest Open-Meteo cache time, total
realized profit, total realized loss, and remaining cash.

Why: Candidate decisions, forecast-unavailable rows, and YES/NO decision rows are
not the same thing as actual open trades. Showing them beside open exposure made
the dashboard easy to misread during operation.

Consequence: The UI no longer displays cumulative candidate-judgment,
forecast-unavailable, actual-open, or YES/NO decision counters. The detailed
dashboard rebuild contract is documented in `docs/dashboard-build-spec.md`.

## 2026-06-01: Forecast Freshness And WebSocket Health Are Separate Signals

Decision: The runner writes forecast and WebSocket health snapshots to
`paper_runner_status.json` while streaming. The dashboard shows them separately
and raises `STALE`, `DEGRADED`, or `FAILED` warnings instead of treating a
reachable dashboard page as proof that trading inputs are healthy.

Why: A main process can remain open after its WebSocket receiver thread dies.
An Open-Meteo response can also remain in memory long after its TTL expires.
Those two failures can leave an apparently normal dashboard backed by old
prices or old forecasts.

Consequence: Memory and disk forecast caches share the same TTL. Forecast
diagnostics distinguish fetch attempts, successful forecast timestamps,
failure reasons, cache age, stale data, and disk-save failures. WebSocket
diagnostics distinguish receiver-thread death, reconnect churn, any incoming
message, actual order-book price updates, stale-book age, and stream errors.

## 2026-06-01: Keep The Entrance Guide Short And The Progress File Current

Decision: `AGENTS.md` contains the project-specific safety and handoff rules
that every new chat needs. Generic long engineering reminders are not duplicated
in a separate mandatory checklist. `docs/production-progress.md` stays short and
uses `완료`, `진행 중`, `다음 작업`, and `이어받는 AI에게`.

Why: Reading a long checklist and a chronological progress diary in every new
chat spends tokens without improving the next decision. The important rules are
the ones that prevent concrete mistakes: preserve user work, fail closed, test
behavior changes, expose failures, keep paper trading, and leave a clear next
step.

Consequence: Old chronological detail belongs in `docs/archive/` when needed,
while reusable prevention lessons belong in `docs/solutions/`. A fresh AI
starts from the current handoff instead of redesigning completed work.

## 2026-06-01: Require Executable Expected Net Return Before Entry

Decision: A paper entry must pass the existing `net_edge` condition and a
separate expected-net-return condition. The default hypothesis is
`ENTRY_MIN_EXPECTED_NET_RETURN_PCT=0.06`. Weather taker fees use the official
Polymarket formula `shares * 0.05 * price * (1 - price)` instead of a fixed
per-share estimate.

Why: A trade such as buying near `0.88` and expecting an exit near `0.92` can
look profitable before costs but leave too little return after fees and future
exit-market costs. Entry VWAP already contains the entry spread and slippage, so
subtracting those again would double-count cost.

Consequence: The runner logs expected gross profit, estimated cost, expected net
return, spread, slippage, fee components, selected route, and any threshold
rejection in the decision reason. Expected early exits use a conservative
future spread-and-slippage haircut. A high entry price is not banned by itself:
a conservative hold-to-settlement route may still pass when it leaves at least
the same 6% expected net return. The bot remains paper-only. Official reference:
https://docs.polymarket.com/trading/fees.

## 2026-06-01: Keep Live Execution Separate From Paper Strategy

Decision: Future live trading work is tracked in
`docs/live-trading-safety-plan.md`. It starts only after the paper strategy is
accepted as sufficiently complete. The live project reuses the paper strategy
instead of adding a second, artificially more conservative trading strategy.

Why: Paper trading and live execution answer different questions. Paper work
tests whether the trading rules are useful. Live work must make actual order
submission, authentication, fill tracking, cancellation, restart recovery,
regional eligibility checks, and redemption reliable. Mixing them makes it
hard to tell whether a result changed because of strategy or execution.

Consequence: The strategy-upgrade roadmap remains paper-focused. Live-specific
operational controls protect actual order handling without silently changing
the paper strategy. Wallet connection, credentials, real orders, and live
deployment still require separate explicit user approval.

## 2026-06-01: Discover Complete Weather Events Before Evaluating Binary Markets

Decision: Discovery expands every supported binary market inside every
weather-category event it finds. The 41-city `STATION_MAP` is only a verified
settlement-station allowlist. It must not be reused as an event-count cutoff.
The fallback Gamma events endpoint is bounded separately by pagination safety
controls. Runner status reports event, city, market, and token coverage
separately.

Why: A single temperature event contains many mutually exclusive outcomes, such
as `18°C or below`, exact buckets from `19°C` through `27°C`, and `28°C or
higher`. Stopping after 41 binary markets can cut off an event partway through
or scan only a few cities while appearing to cover the 41-city station map.
Stopping after 41 events is also wrong because a supported city can have more
than one active city-date event.

Consequence: Exact, lower-tail, and upper-tail buckets are priced from one
ensemble distribution with shared half-degree boundaries. Their probabilities
remain mutually consistent. Standalone threshold questions keep their existing
threshold meaning. The bot remains paper-only.

## 2026-06-01: Promote Known-Good Commands Into Executable Defaults

Decision: Local pytest automatically uses a process-specific workspace temp
directory through the repository root `conftest.py`. Routine local pytest,
Oracle SSH preflight, interactive SSH, remote pytest, bounded log checks, SCP
shape, and dashboard reachability commands are collected in
`docs/codex/known-good-commands.md`. `AGENTS.md` directs fresh chats there before
they invent command variants.

Why: A documented workaround that is read only after failure still wastes a
test run and investigation tokens. The same applies to SSH: retrying guessed
key paths or fragile nested quoting repeats avoidable work.

Consequence: Raw local `python -m pytest -q` works without manual `TMP` or
`TEMP` setup. Each pytest process gets a separate workspace temp directory.
Oracle work starts with a harmless key-existence check and `date` preflight
before longer remote commands. Existing detailed SSH safety docs remain the
reference when a recorded first command fails.

## 2026-06-01: Split One Conservative City-Date Budget Across At Most Two Legs

Status: Superseded by the 2026-06-02 event-portfolio decision below.

Decision: New entries are selected at the city-date event level. A reference
bankroll below `$1,000` may allocate at most 10% to one city-date event. At
`$1,000` or more, that shared cap shrinks to 5%. A single leg remains capped at
5%, one city remains capped at 10%, total open exposure remains capped at 30%,
and one event may hold at most two legs.

Why: A `$100` paper account needs enough nominal room to study complementary
weather buckets, but nearby buckets remain strongly correlated. Treating each
bucket as an independent 10% trade would multiply one-day weather risk. Sizing
new trades only from remaining cash would cause order size to shrink for the
wrong reason after normal entries. Sizing from temporary unrealized profits
would increase risk before those profits are safely realizable.

Consequence: Before a new event entry, calculate cost-basis bankroll as cash
plus open-position entry cost and liquidation bankroll as cash plus executable
sell value of every held position. Use the smaller value. Missing or stale
held-position valuation pauses new entries. A second leg is allowed only when
it adds positive expected net profit after costs and is a non-overlapping
temperature-bucket `YES` position. Repeated `NO` legs, overlapping thresholds,
same-market opposite positions, and third legs are blocked. Event-level JSONL
logs record budget, exposure, selected and rejected legs, expected net profit,
and scenario PnL. Raising these caps later requires a separate resolved-paper-
trade evidence review. General concentration-risk reference:
https://www.finra.org/investors/insights/concentration-risk.

## 2026-06-02: Score YES And NO Event Portfolios With A Ten-Dollar Minimum Leg

Decision: The city-date selector now compares one-leg and at-most-two-leg
portfolios across distinct non-overlapping temperature buckets. Allowed
combinations are `YES+YES`, `YES+NO`, and `NO+NO`. Same-market `YES+NO`,
overlapping thresholds, and third legs remain blocked. Before scoring, event
bucket probabilities are normalized to 100%. Each candidate must retain
positive expected net profit after executable costs, and the selected
portfolio maximizes expected logarithmic bankroll growth.

Decision: A paper leg must be at least `$10`. A bankroll below `$1,000` keeps
the shared city-date cap at 10%; from `$1,000`, that cap remains 5%. A strong
single leg may use the full event budget. Different dates for one city share a
20% city cap. Total open paper exposure may reach 90%, leaving at least 10%
cash.

Why: The first Phase 4 implementation considered only non-overlapping `YES`
legs and capped one leg at 5%. That missed economically useful `NO` positions.
For example, if Seoul settles at `27C`, `27C YES`, `25C NO`, and `26C NO` all
win. Polymarket `negRisk=true` events explicitly connect one outcome's `NO`
share with the other outcomes. The previous 30% total cap also prevented a
`$100` paper account from studying enough independent city-date opportunities.
At the same time, `$1` legs produced nominal paper activity with little
economic meaning.

Consequence: With a `$100` reference bankroll, one city-date event can open one
`$10` leg because the event budget and minimum leg are both `$10`. With a
`$200` bankroll, the event budget is `$20`, so a calculated `$10+$10`
combination becomes possible. The 90% account-level cap does not permit one
event to consume 90%; event and city caps still apply first. This remains
paper-only and has not been deployed automatically.

Official reference:
https://docs.polymarket.com/advanced/neg-risk.

## 2026-06-02: Start Settlement-Station Nowcast As A Seoul-Only Pilot

Decision: Same-day temperature probability may use observed high-so-far only
when the observation provider is explicitly mapped to the same settlement
station in `STATION_MAP`. Phase 5 maps only Seoul/RKSI. All other cities,
including otherwise supported Polymarket cities, report
`nowcast-source-unmapped` and continue as forecast-only.

Decision: The Seoul pilot uses the Aviation Weather Center METAR API for RKSI
as same-station progress evidence, while documenting Wunderground RKSI as the
Polymarket settlement reference. This does not replace final settlement data:
it only prevents the bot from ignoring fresh same-day observations before
Wunderground finalizes the day's high.

Why: Polymarket Seoul rules resolve to the Wunderground finalized highest
temperature for all times on the day at the Incheon Intl Airport Station.
Using a city-center Seoul temperature would be the same class of mistake this
project already banned. AWC documents METAR terminal observations as worldwide,
provides an API for ICAO station ids, and updates the current METAR cache once
a minute. The bot still caches nowcast calls for 15 minutes and requires the
latest station observation to be no older than 90 minutes so missing or stale
feeds do not become strategy data.

Consequence: `WeatherSignal` now carries optional `nowcast` metadata with
observed high, observation timestamps, source URLs, freshness, raw observation
count, and unavailable reason. Decision notes say either
`evidence=forecast-plus-nowcast` or `evidence=forecast-only`. Fresh nowcast can
bound a same-day temperature market to `1.0` when the observed high has already
crossed a threshold or to `0.0` when an exact/lower bucket is already
impossible. Missing, stale, malformed, future-date, or unmapped nowcast is a
skip for nowcast-dependent logic, not a guessed observation.

Official references:
- Polymarket Seoul weather rule pages name Wunderground RKSI as the resolution
  source for the finalized daily high.
- Aviation Weather Center Data API documents METAR terminal observations,
  JSON/API access by ICAO station id, once-per-minute current METAR cache
  updates, and request-rate restrictions.

## 2026-06-02: Keep Station Audit Evidence Explicit

Decision: The station registry now records forecast source, forecast coordinate
source, nowcast candidate station, nowcast enablement status, and whether the
Polymarket rule URL and exact station wording are stored. The readable audit
table lives in `docs/station-registry-audit.md`.

Why: A station id such as `RKSI` or `KLGA` is not enough for a beginner or a
future AI to know whether the bot is using a forecast coordinate, a settlement
station, a same-day observation feed, or a verified trading signal. Putting
those states in separate fields prevents the dangerous shortcut of treating all
41 station ids as automatically nowcast-ready.

Consequence: This audit is separate from live observation provider coverage.
`rule_evidence_status=needs_rule_source_url` means the exact rule URL and
station wording are not yet stored in the repository fields.

## 2026-06-02: Expand Same-Station Observation Providers After Source Checks

Decision: Actual same-day observed highs can now come from two official source
families. ICAO settlement stations use the Aviation Weather Center METAR API
when that exact ICAO id returns observations. Hong Kong/HKO uses the Hong Kong
Observatory maximum/minimum temperature since midnight CSV. Karachi/OPMR stays
forecast-only because AWC did not return recent OPMR METAR data, and no
same-station substitute was added.

Why: Hong Kong was not worth skipping blindly. The current Polymarket weather
scan showed "Highest temperature in Hong Kong on June 1, 2026" near the top of
active weather-event 24-hour volume, so implementing the official HKO-shaped
source had trading-research value. For Karachi, substituting a different
airport would repeat the city-center/nearby-station mistake, so the safer
choice is to leave it forecast-only.

Consequence: `DEFAULT_NOWCAST_SOURCES` follows the station registry:
39 ICAO stations are `aviationweather-metar`, Hong Kong is
`hko-maxmin-since-midnight`, and OPMR is unmapped. Missing, stale, malformed,
or unmapped observations still keep the strategy forecast-only with an
unavailable reason.
