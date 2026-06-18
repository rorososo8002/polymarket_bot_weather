# Official Station Dashboard Design

## Goal

Make the operator dashboard explain the active official-station settlement-lock
strategy without showing forecast-oriented panels or badges.

## Information Architecture

The existing dark, dense operator layout stays. The scan order is:

1. account and paper PnL
2. official station monitoring and strategy evidence
3. open positions and executable exit value
4. recent fills and bounded skip diagnostics

## Payload Contract

The public dashboard payload removes forecast health, latest forecast time,
per-city forecast calls, and forecast temperature from position/realized rows.
It adds:

- `health.station`: aggregate official-station request state
- `scanner.latest_station_at`: latest successful official observation request
- `scanner.station_observations`: latest AWC/HKO request rows
- `scanner.station_signals`: recent official-lock decisions with observed
  high/low, settlement boundary, buffer, hours to close, and 20%/50% allocation
- `scanner.recent_skips`: bounded recent rejection rows from
  `paper_skip_diagnostics.jsonl`

## UI Contract

- Rename scanner and log tabs around official observations and entry blockers.
- Remove forecast temperature, forecast probability, forecast health, and
  forecast-call UI.
- Open positions show station, observed high/low, observation time, settlement
  boundary/buffer, lock signal, allocation, executable liquidation PnL, and
  order-book freshness.
- Realized rows keep plain-language defensive-close explanations but do not show
  forecast badges.
- Station-call cards distinguish current success, previous success plus latest
  failure, and no usable observation.

## Safety And Verification

The dashboard remains read-only and token-protected. No paper ledger is reset.
Tests cover the payload contract and absence of visible forecast UI. Deployment
restarts dashboard and bot services, then verifies HTML 200, unauthenticated API
403, query-token API 403, and header-authenticated API 200.
