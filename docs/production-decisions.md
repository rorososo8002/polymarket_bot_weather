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

## 2026-05-28: Strategy Changes Must Be Research-Backed And Reproducible

Decision: Production strategy work must be documented as an executable specification, not a chronological activity log.

Why: A fresh AI should be able to open the folder, read the production docs, and reproduce the same bot behavior and research priorities. The core mission is to improve risk-adjusted paper returns through market research, mathematical reasoning, and empirical trade review.

Consequence: Future strategy changes should state the expected-value, calibration, Kelly/fractional-Kelly, liquidity, slippage, forecast-error, or drawdown rationale behind the rule; update production docs alongside code; and keep live-wallet execution out of scope unless explicitly requested.

## 2026-05-28: Dashboard Scanner Counts Are Cumulative

Decision: Scanner Intelligence totals count the full `paper_decisions.csv` log,
while recent candidate cards, event stream entries, and buy-pressure bars remain
tail-limited for responsiveness.

Why: The dashboard previously derived candidate judgments and skips from the
latest 800 decisions, so the numbers changed as older rows fell out of the
window. Operators need these top-line counts to behave like accumulated service
telemetry, not a moving sample.

Consequence: `dashboard.py` keeps an incremental append cache for decision
totals. `예보 없음` replaces the unclear `NO FORECAST` label and counts rows
whose reason or note says the forecast was unavailable or no forecast could be
used. `총 열린 진입금액` means open-position entry cost, and `남은 현금` means
paper cash remaining.
