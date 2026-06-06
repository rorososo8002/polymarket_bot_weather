# Live Trading Project Implementation Plan

Created: 2026-06-01 Asia/Seoul

## Purpose

This document is a handoff plan for a future live-trading project after the
paper strategy is mature enough.

The live project must not redesign the trading strategy. Entry logic, exit
logic, fee math, risk limits, supported cities, settlement stations, forecast
validation, and order-book evaluation should reuse the strategy already proven
in the paper bot.

The new live component is only the real order execution layer. In plain terms:
keep the bot's decision brain, then add the hands and eyes that submit orders,
track fills, and reconcile the real exchange state.

## Safety Boundary Before Starting

- This document is a future work list, not permission to enable live trading.
- Do not connect real wallets, register private keys, send real orders, or
  deploy a live-trading server without explicit user approval.
- Never store private keys, API keys, API secrets, passphrases, seed phrases, or
  wallet secrets in the repository, logs, dashboard, chat, or test fixtures.
- Paper trading remains the default mode even after live support exists.
  Live mode must require an intentional user opt-in.
- Before any real order is submitted, call Polymarket's geographic restriction
  endpoint from the actual order-sending server IP. This is not a strategy
  preference; it is a legal and operational prerequisite. Official docs identify
  the United States as blocked. Do not submit new entries or exit orders from a
  blocked location, and do not build bypass behavior.

## Completed Paper Foundation

The live project should reuse these paper-bot foundations instead of rebuilding
them:

- Only cities with verified settlement stations and stored Polymarket rule
  evidence are eligible for trading. `src/weather_bot/stations.py` and
  `STATION_MAP` are the station metadata source of truth, while
  `TRADING_READY_STATION_MAP` is the executable subset.
- Open-Meteo forecasts use the paper bot's current forecast budget rules:
  `FORECAST_CACHE_TTL_SECONDS=2400` for the forecast answer-sheet freshness
  window, plus `FORECAST_REQUEST_MIN_INTERVAL_SECONDS=60` so real forecast HTTP
  calls are one-at-a-time and spaced after the previous request finishes or
  times out.
- The bot uses the Polymarket CLOB market WebSocket for realtime order books and
  diagnoses dead receiver threads and stale books.
- Expected net-return filtering includes entry VWAP, spread, slippage, and the
  weather taker fee curve.
- Paper positions, decision logs, trade logs, exposure limits, probability-based
  stops, model-target exits, and partial-runner exits already exist.

## Current Status

- Live-trading implementation has not started.
- The repository is still paper-only.
- No real wallet, real order-submission code, or live-trading secret has been
  added.

## Work To Do

The following items should be implemented in order only after the paper strategy
is complete enough to justify live execution.

### 1. Freeze The Paper Strategy Contract

The live executor must not create a separate trading strategy. If the paper
strategy says "buy this much YES", "sell this much NO", or "skip this
candidate", the live executor converts that intent into an order without adding
its own hidden strategy thresholds.

Why this matters:

- If paper and live results diverge, we can tell whether the cause is strategy
  logic or order execution.
- It prevents accidental strategy changes that happen only because the order
  path is live.

Completion gate:

- Tests prove that the same input produces the same order intent for paper and
  live execution.
- The live layer has no independent trading-strategy thresholds.

### 2. Fetch Market State And Token Fees Immediately Before Ordering

Paper mode uses the official weather-category default fee rate of `0.05` for
expected-return math. Live mode must fetch the official `fee-rate` endpoint by
token ID immediately before ordering, and fetch `getClobMarketInfo(conditionID)`
by condition ID.

`token ID` means the unique identifier for one tradable side, such as YES or
NO. Even inside the same weather question, YES and NO are separate assets, so a
real order must target the exact token.

`condition ID` means the identifier for the full question that contains both
YES and NO tokens. Use token ID for the exact order target and condition ID for
shared market settings.

Values to fetch:

- `fee-rate`: the token-level base fee. API `base_fee` values are in basis
  points, meaning parts per 10,000. For example, `30` means `0.30%`.
- `feesEnabled`: whether fees are active on that market.
- `fd.r`, `fd.e`, `fd.to`: fee-curve parameters that describe the rate, price
  curve shape, and whether the fee applies only to takers.
- `tick size`: the valid price increment. If tick size is `0.01`, `0.52` is
  valid and `0.523` is rejected.
- `negRisk`: whether the market belongs to a negative-risk event that needs
  special exchange rules.
- Minimum order size and trading status, so the bot does not submit tiny orders
  or orders for closed markets.

Completion gate:

- Missing, stale, or contradictory market metadata fails closed and logs a clear
  reason.
- Expected-return math and order preparation use the same fresh fee metadata.
- Official fee-curve changes are not silently replaced with a fixed estimate.

### 3. Check Geographic Restrictions

Call `https://polymarket.com/api/geoblock` from the actual server IP right
before enabling any real order path.

Why this matters:

- A local PC and a VPS can be in different legal or operational locations.
- VPS location does not prove the user's legal eligibility.
- The project must not include any restriction-bypass feature.

Completion gate:

- If `blocked=true`, the bot submits no real orders, including entry and exit
  orders.
- Restriction responses and network errors are logged in a way an operator can
  understand.

### 4. Decide Wallet And Authentication Structure

Polymarket CLOB authentication has two layers:

- `L1 authentication`: proves wallet ownership with a wallet private-key
  signature. It is used to create API credentials or sign order payloads.
- `L2 authentication`: uses the derived `apiKey`, `secret`, and `passphrase` to
  authenticate order submission, cancellation, and balance requests.

Common beginner trap: an L2 API key does not remove the need for order-payload
signing, and both the wallet key and API credentials are secrets.

Before implementation, document the wallet type:

- Existing Safe or proxy users must verify their current wallet structure.
- New API users should review the official deposit-wallet flow. A deposit wallet
  holds pUSD and conditional tokens; the modern flow uses `POLY_1271`, also
  known as `signatureType=3`.
- `funder` is the wallet address that actually holds funds and tokens.
- `signer` is the address or key role that signs orders. It can differ from the
  funder, so never assume they are the same just because the names look related.

Completion gate:

- The wallet type, funder address, and signer role are documented.
- Secrets are injected only from an operational secret store, not repo files.
- Tests prove logs and exception messages do not expose secrets.

### 5. Separate Live Execution From Paper Execution

`PaperBroker` records simulated fills. Live mode needs a separate executor that
creates, signs, submits, tracks, and reconciles real CLOB orders.

Why this matters:

- Paper mode remains the default.
- Live-order bugs cannot damage paper validation.
- Operators can disable the live executor and return to paper mode quickly.

Completion gate:

- Default settings never call real order-submission functions.
- Real order submission is reachable only through explicit live settings.
- Order-intent conversion and Polymarket responses are logged without secrets.

### 6. Match Order Type To Strategy Intent

Do not hard-code one order type for every situation.

- `GTC`: good until cancelled; the order rests on the book until cancelled.
- `GTD`: good until date; the order rests until a chosen expiration time.
- `FOK`: fill or kill; fill the whole quantity immediately or cancel all.
- `FAK`: fill and kill; fill whatever is available immediately and cancel the
  rest.

Use `FOK` or `FAK` for immediate-entry intent. Use `GTC` or `GTD` only when the
strategy intentionally wants to wait at a limit price.

Completion gate:

- Entry, full exit, and partial exit each document and test the selected order
  type.
- No request is allowed without a worst acceptable price.

### 7. Track Orders And Fills With User WebSocket

The market WebSocket is public order-book data. Live mode also needs the user
WebSocket, which reports private order lifecycle events.

Order events:

- `PLACEMENT`: the order was accepted onto the order book; it does not prove a
  fill yet.
- `UPDATE`: quantity or price changed, often because of a partial fill.
- `CANCELLATION`: the order was cancelled and no longer waits for fills.

Post-match states:

- `MATCHED`: an opposing order matched and settlement processing started.
- `MINED`: the fill transaction was included on-chain but not fully final.
- `CONFIRMED`: the fill is final enough to treat as successful.
- `RETRYING`: Polymarket is retrying a recoverable fill-settlement issue.
- `FAILED`: retries ended in failure and local state must be reconciled again.

Completion gate:

- Partial fills, cancellations, retries, and failures have focused tests.
- Startup reconciliation compares local open orders and positions with the real
  exchange state.

### 8. Add Heartbeat And Emergency Cancel-All

`heartbeat` tells Polymarket the bot is alive. If heartbeat stops, official
behavior can cancel open orders. The bot should also provide an explicit
operator-triggered `cancelAll()` path.

Important distinction: cancelling orders does not erase already-filled
positions. It only removes resting open orders. Existing positions still need
settlement, exit, or redemption handling.

Completion gate:

- Heartbeat-stop tests prove open orders do not remain unmanaged.
- An operator can stop new orders and cancel open orders with one documented
  emergency action.

### 9. Reconcile Real Orders, Real Positions, And Local State

Reconciliation means comparing the bot's memory or files with the real exchange
state and correcting local state when they differ.

Examples:

- The bot remembers an open order that was already filled.
- An order partially filled while the bot was restarting.
- A WebSocket message was missed but the server still has the real position.

Completion gate:

- Startup and periodic tasks refetch open orders, fill history, and real
  positions.
- If state disagrees, new orders pause, the reason is logged, and local state is
  rebuilt from the exchange.
- Duplicate orders are blocked for the same strategy signal.

### 10. Redeem Winning Tokens After Resolution

`resolved` means the official market result has been decided. A `winning token`
is the YES or NO token that matches that result. Winning tokens do not always
turn into cash automatically; the bot must use the official `redeem` flow to
recover pUSD.

Completion gate:

- The bot confirms resolved status, winning-token holdings, and condition ID
  before redeeming.
- A market is not redeemed twice.
- Redemption failures are logged and retryable.

### 11. Add Operations Dashboard, Deployment, And Rollback

The live dashboard must show real execution state, not only strategy output.

Show at least:

- Open orders and remaining unfilled quantity.
- Real positions and average fill price.
- Last user-WebSocket message time.
- Last heartbeat success time.
- Last geographic restriction check result.
- Recent order rejection, cancellation, retry, and failure reasons.
- Winning tokens waiting for redemption.

Deployment rule:

- Before the first live deployment, explain the change, benefit, risk,
  verification method, and rollback method, then get explicit approval.
- The first real order should be tiny and should test submission, fill tracking,
  cancellation, reconciliation, and dashboard visibility.
- If a problem appears, stop new orders, run `cancelAll()`, return to paper mode,
  and reconcile any already-filled positions separately.

## Decisions Required Before Implementation

- Whether to use an existing Safe/proxy wallet or the newer deposit-wallet flow.
- Which secret store injects operational secrets.
- Whether the real order-sending server IP and user eligibility satisfy
  geographic restriction rules.
- Which order type applies to entry, full exit, and partial exit.
- Which live-mode setting name and emergency-stop command operators will use.

## For The Next AI

> Do not redesign the live trading strategy from scratch. After the paper
> strategy is mature, reuse it and implement only the real order execution layer
> from this document's "Work To Do" section. Do not connect real wallets, add
> secrets, send real orders, or deploy live trading without the user's separate,
> explicit approval.

## Official Documents

- Fees: https://docs.polymarket.com/trading/fees
- Token fee-rate API: https://docs.polymarket.com/api-reference/market-data/get-fee-rate
- CLOB public methods: https://docs.polymarket.com/trading/clients/public
- Authentication: https://docs.polymarket.com/api-reference/authentication
- Deposit wallets: https://docs.polymarket.com/trading/deposit-wallets
- Create orders and order types: https://docs.polymarket.com/trading/orders/overview
- User WebSocket: https://docs.polymarket.com/market-data/websocket/user-channel
- Heartbeat: https://docs.polymarket.com/api-reference/trade/send-heartbeat
- Cancel orders: https://docs.polymarket.com/trading/orders/cancel
- Geographic restrictions: https://docs.polymarket.com/api-reference/geoblock
- Redeem tokens: https://docs.polymarket.com/trading/ctf/redeem
