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

Consequence: Open positions keep receiving live order-book updates while they remain in paper state. Stream status counts the combined discovery-plus-held registry, so the visible market count can exceed `MAX_MARKETS`.

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
