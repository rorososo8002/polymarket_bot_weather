# Production Progress

## Completed

- This project is a paper-trading service. It does not send real wallet orders.
- Tradable cities are limited to the 41 verified cities in
  `src/weather_bot/stations.py`. `STATION_MAP` is the single source of truth for
  settlement stations.
- Open-Meteo forecasts refresh every 30 minutes by default. Memory and disk
  caches use the same TTL.
- Order books come from the Polymarket CLOB WebSocket stream. Token IDs for
  open positions stay subscribed even when discovery rolls forward to newer
  dates.
- Phases 0-5 were implemented and verified locally. Forecast freshness,
  WebSocket health, executable net-return entry filtering, exact bucket/event
  discovery, city-date portfolio selection, and settlement-station nowcast are
  reflected in code and docs.
- Phase 6 was implemented and verified locally. Profit exits compare
  fee-adjusted sell-now value with conservative settlement expected value. When
  settlement is still better, the broker sells a principal-recovery tranche and
  keeps a bounded 25% settlement runner by default.
- Phase 6 runner decisions are written to `paper_trades.csv` as
  `PARTIAL_CLOSE`, `HOLD_RUNNER`, or `HOLD_NO_LIQUIDITY`, with tranche-level
  reasons.
- Probability deterioration stops, valid edge-fade exits, max-hold exits,
  invalid-sentinel protection, low-liquidity limits, and nowcast fail-closed
  behavior remain in force.
- Phase 7 was implemented and verified locally. It added public Polymarket Data
  API whale/external-signal shadow research, bounded JSONL storage, a paper
  decision comparison report, and separate evidence/speculation handling for
  manually reviewed public posts.
- Phase 7 is separate from execution. The paper runner does not consume shadow
  signals. Automatic copy trading, wallet connection, live orders, and private
  data collection were not added.
- Phase 7 review hardening was implemented locally. Public trade rows are
  rechecked against the local minimum-size filter, distinct rows from one
  transaction are retained, a zero retention cap keeps zero rows, and promotion
  compares external signals against bot entries on the same paired sample.
- Paper fee accounting was corrected end to end. `size_usd` is the all-in
  paper-entry budget, entry shares fit price plus fee, closes add after-fee
  proceeds, and liquidation bankroll/dashboard PnL use after-exit-fee value.
- Settlement-runner logs now distinguish actual held shares from a freshly
  recalculated target-runner size.
- Repository text was normalized to English-only tracked content. A repository
  Hangul scan now returns no matches outside ignored runtime/git folders.
- Local verification passed with focused pytest and full `pytest -q`. The final
  full result was `187 passed in 2.76s`.

## In Progress

- The current local work is Phase 7 complete with review hardening.
- Phase 0-7 changes have not been automatically deployed to the Oracle VPS.
- Before any deployment, explain the change, benefit, risk, verification method,
  and rollback method, then get explicit user approval.

## Next Work

1. Do not feed Phase 7 research into strategy execution until enough resolved
   matched public signals accumulate.
2. Later, run `shadow-signal-report --collect` to gather bounded public data.
   Suggest a paper-only A/B experiment only if the report shows at least 20
   resolved matched samples and at least a five-percentage-point edge over the
   matched bot entries.
3. Automatic copy trading, wallet connection, live orders, and private data
   collection remain prohibited.
4. Existing paper runtime files are not rewritten retroactively. When comparing
   results, record the boundary between pre-fix gross-fee accounting and
   post-fix fee-adjusted accounting.
5. Before local pytest or VPS/SSH work, use the command shapes in
   `docs/codex/known-good-commands.md`.

## For The Next AI

> Do not redesign from scratch. Continue from this document's 'In Progress' and 'Next Work' sections. Do not reimplement completed items. If the code and documents disagree, record the drift before continuing.

- First read `AGENTS.md`, `docs/production-implementation-plan.md`,
  `docs/production-decisions.md`, and `docs/strategy-upgrade-roadmap.md`.
- Do not rebuild the Phase 6 implementation. Preserve the principal
  recovery/settlement runner path in `src/weather_bot/paper.py` and the exit
  trigger separation in `src/weather_bot/exit_policy.py`.
- Do not rebuild the Phase 7 implementation. Continue public signal research
  from `src/weather_bot/shadow_signals.py` and
  `docs/shadow-signal-research.md`.
- Shadow signals are research-only. Do not add real orders, wallet connection,
  automatic copy trading, operations deployment, or private data collection.
