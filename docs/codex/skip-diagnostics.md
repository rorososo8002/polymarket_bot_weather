# Skip Diagnostics Runbook

This paper bot should skip when trading inputs are uncertain. A SKIP is the
safe first response, not the final investigation result.

Use this runbook when many markets are skipped, when the same SKIP reason keeps
repeating, or before deciding that the strategy itself is weak.

## What A SKIP Means

`SKIP` means the bot chose not to open a new paper position.

That can be good. For example, skipping a market with stale data prevents fake
paper performance from looking safer than it really is. But repeated SKIPs are
also evidence. They tell us what is blocking the bot from reaching the next
strategy improvement.

## Main SKIP Categories

### Account Safety

Typical reason:

```text
기존 포지션을 안전하게 평가할 수 없어 신규 진입 차단
```

Meaning: the bot already holds a paper position, but cannot safely estimate how
much that position could be sold for from executable order-book depth.

Check next:

- Is the held `token_id` still subscribed in the WebSocket stream?
- Can the CLOB order book for that held token be read?
- Does the held token have enough bid depth to sell the held shares?
- Is the WebSocket stream alive and fresh?

Likely fixes:

- Restore stale or stopped WebSocket order-book streaming.
- Ensure open-position token IDs remain subscribed until the position closes.
- If the market has no real bid depth, keep blocking new entries and treat the
  position as a liquidity-risk case.

### Minimum Order Or Budget

Typical reason:

```text
calculated order $5.00 below minimum order $10.00
```

Meaning: the model may like the trade, but the risk caps and bankroll produce
an order smaller than the minimum allowed paper order.

Check next:

- Is `entry_bankroll` small because open positions lost value?
- Are city, event-date, total exposure, or single-market caps already nearly
  full?
- Is `MIN_ORDER_USD` too high for the current account size?

Likely fixes:

- Do not force the trade. Keep the minimum-order SKIP.
- Study whether the account size and risk caps match each other.
- If many good candidates are blocked only by minimum size, plan a separate
  paper-only sizing experiment.

### Market Liquidity

Typical reasons:

```text
no ask
no bid
spread too wide
exit bid depth $... < $10
insufficient ask depth
```

Meaning: the market exists, but the order book does not prove the bot can enter
and later exit at a reasonable executable price.

Check next:

- Does the market have confirmed `book` or `price_change` depth, not only
  indicative `best_bid_ask` quotes?
- Are both YES and NO sides thin?
- Is the spread wide only temporarily, or repeated across cycles?

Likely fixes:

- Wait if the issue is temporary.
- Keep skipping if the market is structurally thin.
- Improve reporting so thin-liquidity SKIPs are counted separately from
  forecast or parsing SKIPs.

### Weather Data Or Parsing

Typical reasons:

```text
forecast-unavailable
confidence too low
date_hint=None
unsupported station
unsupported market type
```

Meaning: the bot does not have trusted weather or market interpretation for
the exact city-date event.

Check next:

- Does the Open-Meteo response contain the exact target date?
- Is the parsed city in `TRADING_READY_STATION_MAP`?
- Does the question shape match supported temperature-market parsing?
- Is same-station nowcast missing, stale, or malformed?

Likely fixes:

- Fix parsing only when the market is a real supported weather question.
- Keep skipping unsupported cities or missing rule-evidence stations.
- Do not substitute nearby forecast dates or nearby weather stations.

### Strategy Threshold

Typical reasons:

```text
edge below ...
expected net return below ...
YES+NO ask sum abnormal
```

Meaning: the market may be readable and liquid, but it does not meet the
strategy's conservative entry requirements.

Check next:

- Is the model probability too close to the executable price?
- Are fees, spread, or expected exit costs eating the apparent edge?
- Is YES+NO pricing suspicious enough that the book should be ignored?

Likely fixes:

- Usually no fix is needed. This is normal selectivity.
- If almost every market fails this way, run a paper-only strategy research
  review rather than weakening thresholds casually.

## What To Record

For repeated SKIPs, collect:

- SKIP reason text.
- `market_id`, question, city, date hint, and side if available.
- `entry_bankroll`, `cost_basis_bankroll`, and `liquidation_bankroll`.
- Open held `token_id` values when account safety blocks new entries.
- WebSocket health: thread alive, stale or fresh, subscribed token count.
- Order-book evidence: bid depth, ask depth, spread, and whether depth came
  from executable levels.
- Count by reason over a fixed operator-chosen window such as 1 hour or 24 hours.

## WebSocket Freshness Report

Use the paper runtime diagnosis report before weakening strategy thresholds or
WebSocket freshness rules:

```powershell
& 'C:\Users\wpdla\Python312\python.exe' -m weather_bot.runtime_diagnostics --data-dir data --tail 500
```

On the VPS, run it from `/opt/polymarket-weather-bot`:

```bash
sudo -u polymarket .venv/bin/python -m weather_bot.runtime_diagnostics --data-dir data --tail 500
```

This report reads `paper_runner_status.json`, `paper_state.json`, and a bounded
tail of `paper_trades.csv`. It separates:

- whole-stream WebSocket health, which can block all new entries
- token-level held-position freshness, which can pause one position
- recent `OPEN`, `CLOSE`, `HOLD_RUNNER`, and `HOLD_STREAM_UNHEALTHY` counts
- the top stale held tokens by city, market, and latest blocker text

If whole-stream health is fresh but one token repeatedly appears in
`token_stale_blocks`, do not loosen global freshness rules. Treat that as a
held-token liquidity or subscription investigation.

## Portfolio Rejection Report

When the question is "why did the event portfolio throw away candidates?",
temporarily enable:

```text
PORTFOLIO_LOG_SKIP_ENABLED=true
```

This makes `paper_event_portfolios.jsonl` record zero-selection portfolio rows:
rejection counts, a small rejection sample, candidate budget, and scenario
payoff audit. It is the right evidence for portfolio blockers such as
`scenario probabilities overlap`, insufficient probability mass, minimum-order
budget, or "not selected by event portfolio optimizer".

Because this can grow quickly, run it only with the runtime archive cleanup
enabled. The cleanup budget deletes diagnostic archives, not account ledgers.

Summarize the recent rejection reasons with:

```bash
python -m weather_bot.runtime_diagnostics --data-dir data --tail 500
```

The report includes a `portfolio_rejections` section with zero-selection rows,
top rejection reasons, and the latest event that selected no legs.

## When To Investigate

Treat these as default triggers:

- Same account-safety SKIP repeats for more than 3 consecutive cycles.
- Any one SKIP reason dominates a 24-hour paper run.
- A market that should be liquid repeatedly has no executable depth.
- Forecast or parsing SKIPs increase after a code or station-registry change.
- The dashboard shows the service is alive but decisions are mostly SKIP.

## Next Tooling To Build

Add bounded `paper_decisions.csv` reason aggregation when a future investigation
needs non-portfolio candidate-level SKIP reasons. Keep the report paper-only and
do not enable live trading, wallet use, or real orders.
