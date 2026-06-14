---
title: Exact and range bucket nowcast must flag held NO exit risk
date: 2026-06-07
last_updated: 2026-06-12
category: logic-errors
module: weather_bot.live_paper_runner, weather_bot.exit_policy
problem_type: logic_error
component: service_object
symptoms:
  - "A held NO exact/range bucket position could look safe after the settlement station entered the bucket."
  - "The existing probability stop could miss a bucket-lock risk because the nowcast was not a decisive YES/NO probability change."
  - "Daily-low safety still required observed low, not observed high."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, nowcast, exit-policy, exact-bucket, range-bucket, held-no]
---

# Exact and range bucket nowcast must flag held NO exit risk

## 1. What The Problem Was

Exact and range temperature markets are settlement buckets. A question such as
`Will the highest temperature in Hong Kong be 30C today?` is not the same as
`30C or higher` in this project. It means the final observed high must land in
the displayed exact value. Do not widen the exact value into a hidden half-step
interval. A range bucket still means the displayed inclusive range endpoints.

The probability code already handled decisive evidence: if observed high fully
moved above the exact or range bucket, YES became impossible and `p_true` went
to `0.0`. The missing case was one step earlier. If observed high was already
inside the bucket, a held NO position had serious risk, but the generic
probability stop could still say hold.

## 2. Why It Was A Problem

`paper_state.json` is the paper account book. If it keeps a NO position open
while same-station nowcast has already entered the settlement bucket, the paper
results can look calmer than the real risk. That pollutes the profitability
experiment because the bot is no longer measuring the strategy honestly.

This is especially dangerous because the fix must not make new entries more
aggressive. The correct behavior is exit-only: use the nowcast bucket evidence
to reassess positions that already exist, not to loosen entry thresholds.

## 3. How It Was Fixed

`EdgeResult` now carries optional exit-only fields:

```python
exit_signal: str = ""
exit_signal_reason: str = ""
```

`live_paper_runner` sets `exit_signal="nowcast_bucket_lock_risk"` only for held
or evaluated NO-side exact/range buckets when same-station nowcast is inside
the parsed bucket interval. Daily-high markets read `observed_high_f`; daily-low
markets read `observed_low_f`.

`exit_policy.assess_exit()` then treats that explicit exit signal as a close
trigger before the generic hold path. If there is no executable bid depth or
the WebSocket token is stale, the existing paper blocker path still preserves
`exit_trigger=nowcast_bucket_lock_risk` instead of pretending a sale happened.

## 4. What To Check Next Time

- Add a focused held-position test before changing exit behavior.
- Test exact and range buckets together; they share the same bucket-lock risk.
- Keep the YES-impossible rule for observed values that make the final bucket
  impossible: daily-high observed values above the exact value/range upper
  endpoint, and daily-low observed values below the exact value/range lower
  endpoint.
- For daily-low markets, verify that observed high is not requested or applied.
- Check `paper_trades.csv` reasons for the specific `exit_trigger`, not only
  that the position disappeared.

## 5. What This Project Must Be Especially Careful About

This project is paper-only. Do not connect wallets, private keys, real orders,
or live trading while fixing exit evidence.

Nowcast must remain same-station evidence. Observed high belongs to daily-high
markets, and observed low belongs to daily-low markets. Missing or mismatched
nowcast is not guessed.

Do not turn `nowcast_bucket_lock_risk` into an entry booster. It is a warning
for already-held NO positions, so the profitability experiment can record risk
and blocked exits honestly.

## Related

- [Held-position exit evidence must not depend on entry bankroll](../best-practices/held-position-exit-evidence-must-not-depend-on-entry-bankroll.md)
- [Realtime nowcast signals must refresh on the nowcast TTL](realtime-nowcast-signal-refresh-must-follow-nowcast-ttl.md)
- [Do not use observed high nowcast for daily-low markets](observed-high-nowcast-daily-low-markets.md)
- [Temperature range buckets must preserve both endpoints](temperature-range-buckets-must-preserve-endpoints.md)
