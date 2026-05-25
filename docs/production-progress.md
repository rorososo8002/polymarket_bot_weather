# Production Progress

## Completed

- Added `AGENTS.md` with local workflow notes, production bot guardrails, and a 65-line concise-work rule set.
- Added `src/weather_bot/stations.py` as the single source of truth for 41 verified Polymarket weather settlement cities.
- Updated parsing so only verified station cities are recognized as supported bot cities.
- Updated Polymarket market discovery to reject weather-shaped markets outside the verified station set.
- Updated probability estimation to return `unsupported-station` instead of using city-centroid fallbacks.
- Changed default forecast cache TTL to 30 minutes.
- Added `FORECAST_REFRESH_INTERVAL_SECONDS=1800`.
- Added WebSocket order-book stream settings:
  - `ORDERBOOK_STREAM_ENABLED=true`
  - `ORDERBOOK_STREAM_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market`
  - `ORDERBOOK_STREAM_HEARTBEAT_SECONDS=10`
  - `ORDERBOOK_STREAM_RECONNECT_SECONDS=2`
- Added `src/weather_bot/realtime_orderbook.py` for Polymarket CLOB market-channel order-book caching.
- Updated `run_forever()` to use realtime WebSocket mode by default.
- Added event-driven evaluation for WebSocket order-book updates while reusing 30-minute forecast signals.
- Updated `.env.example` and `deploy/systemd/live-paper.env.example`.
- Added focused regression tests for station gating, forecast settings, WebSocket order-book cache updates, and realtime runner selection.
- Added production handoff docs:
  - `docs/production-implementation-plan.md`
  - `docs/production-progress.md`
  - `docs/production-decisions.md`

## In Progress

- WebSocket order-book monitoring is implemented as the default path.
- Execution remains paper trading through `PaperBroker`; there is no live wallet order submission path.
- Station coordinates are mapped to the Polymarket settlement station for forecasting, but forecast bias calibration is still neutral.
- Stream health handling is basic; reconnects exist, but stale-stream alerting and operational metrics are not yet implemented.

## Next Work

- Add tests that prove forecast calls do not happen inside WebSocket order-book update callbacks.
- Add stream health telemetry: startup snapshot coverage, last-event age, reconnect count, and stale-stream alerts.
- Add station-level forecast bias files after enough paper-trading and resolved-weather data exists.
- Decide explicitly whether to keep paper-only operation or add a live execution broker.
- If live execution is requested, add signing, key isolation, order validation, and kill-switch docs before any real order path.

## For The Next AI

[처음부터 다시 설계하지 말고 이 문서의 “진행 중”과 “다음 작업”부터 이어갑니다. 완료된 항목을 다시 구현하지 않고, 코드와 문서가 맞지 않으면 차이를 기록한 뒤 진행합니다.]

Start by reading `AGENTS.md`, this file, and `docs/production-decisions.md`. Then run the focused tests around station gating, settings, and WebSocket order-book behavior before editing.
