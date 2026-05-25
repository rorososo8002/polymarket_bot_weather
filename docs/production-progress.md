# Production Progress

## Completed

- Added `AGENTS.md` with local workflow notes, production bot guardrails, and a 65-line concise-work rule set.
- Added `src/weather_bot/stations.py` as the single source of truth for 41 verified Polymarket weather settlement cities.
- Updated parsing so known unverified cities can still be recognized, while trading gates use only `STATION_MAP`.
- Updated Polymarket market discovery to reject weather-shaped markets outside the verified station set.
- Updated probability estimation to return `unsupported-station` instead of using city-centroid fallbacks.
- Changed default forecast cache TTL to 30 minutes.
- Added `FORECAST_REFRESH_INTERVAL_SECONDS=1800`.
- Added `ORDERBOOK_POLL_INTERVAL_SECONDS=5`.
- Updated `run_forever()` to use the fast order-book polling interval.
- Updated `.env.example` and `deploy/systemd/live-paper.env.example`.
- Added focused regression tests for station gating and polling/forecast settings.
- Added production handoff docs:
  - `docs/production-implementation-plan.md`
  - `docs/production-progress.md`
  - `docs/production-decisions.md`

## In Progress

- The bot still uses HTTP polling, not a CLOB websocket.
- The bot still runs through `run_cycle()` for each fast poll, so market discovery is not yet fully decoupled from order-book checks.
- Execution remains paper trading through `PaperBroker`; there is no live wallet order submission path.
- Station coordinates are mapped to the Polymarket settlement station for forecasting, but forecast bias calibration is still neutral.

## Next Work

- Split the long-running process into:
  - a 30-minute market discovery and forecast signal refresh,
  - a 5-second order-book-only evaluation loop using the cached signal snapshot.
- Add tests that prove forecast calls do not happen inside the fast order-book loop.
- Consider CLOB websocket monitoring after the HTTP polling behavior is stable.
- Add station-level forecast bias files after enough paper-trading and resolved-weather data exists.
- Decide explicitly whether to keep paper-only operation or add a live execution broker.
- If live execution is requested, add signing, key isolation, order validation, and kill-switch docs before any real order path.

## For The Next AI

처음부터 다시 설계하지 말고 이 문서의 “진행 중”과 “다음 작업”부터 이어갑니다. 완료된 항목을 다시 구현하지 않고, 코드와 문서가 맞지 않으면 차이를 기록한 뒤 진행합니다.

Start by reading `AGENTS.md`, this file, and `docs/production-decisions.md`. Then run the focused tests around station gating and settings before editing.
