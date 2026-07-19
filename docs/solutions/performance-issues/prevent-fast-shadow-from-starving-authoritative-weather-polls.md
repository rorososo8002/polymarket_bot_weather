---
title: Keep fast weather shadows from delaying authoritative polls
date: 2026-07-20
category: performance-issues
module: "weather_bot.nowcast, weather_bot.live_paper_runner"
problem_type: performance_issue
component: background_job
symptoms:
  - "A healthy research-only fast feed could stretch authoritative daily-history polling from 5 seconds to 60 seconds."
  - "A stalled fast request could block the same city's authoritative request for up to 20 seconds."
  - "Fast requests could consume the shared API allowance before daily-history or final entry revalidation."
  - "A fixed station order could repeatedly leave later cities without a timely request slot."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [wunderground, fast-shadow, daily-history, rate-limit, cache-control, exact-no, realtime-latency, paper-trading]
---

# Keep Fast Weather Shadows From Delaying Authoritative Polls

## Problem

The Weather Company time-series feed was added as a research-only early warning
for new physical-station observations. It was supposed to wake an immediate
daily-history check and preserve the contemporaneous NO book. Instead, its
health, latency, and request volume could delay the Wunderground daily-history
response that is the only observation source allowed to authorize an exact-NO
paper entry.

## Symptoms

- A successful fast response made the normal 60-second cache look sufficient,
  suppressing the existing 5-second daily-history cadence near a learned report
  boundary.
- The fast path inherited the provider's 20-second timeout and ran before the
  authoritative request under the same station lock.
- Fast and authoritative calls competed for one API-key-wide allowance. The
  optional request could take the last slot needed by daily history or final
  revalidation.
- Attempts to make a strict deferred-station cohort introduced head-of-line
  blocking: one inactive queued station could block unrelated authoritative
  requests even when capacity was free.

## What Didn't Work

- Treating a healthy fast feed as permission to slow daily-history polling. A
  research signal can be missing or can publish in a different order.
- Reacting only after an HTTP 429. The opportunity is already lost when the
  provider starts rejecting requests.
- Sharing the full timeout with an optional feed. A fast warning that waits 20
  seconds is slower than the source it is meant to accelerate.
- Enforcing fairness with a blocking cohort shared by both request types. It
  protected order at the cost of delaying authoritative and final checks.

## Solution

Keep the fast feed additive and lower priority:

1. Near the learned report boundary, poll daily history every 5 seconds whether
   the fast feed is healthy, cached, unavailable, or pending.
2. Cap the fast request timeout at 2 seconds. A fast failure never changes the
   authoritative polling cadence.
3. Enforce one API-key-wide ceiling of 90 requests per rolling minute, a
   separate fast ceiling of 30, and preserve the last 10 available slots for
   daily-history and final revalidation calls.
4. Honor `Cache-Control: max-age` only for the fast endpoint that returned it;
   never use that header to delay the separate daily-history endpoint.
5. Rotate the first submitted active station on every refresh instead of always
   starting from the same city.
6. Let fast observations wake evaluation and record the NO order-book top five,
   but keep `trade_evidence=false`. Entry remains blocked until daily history
   matches station, local date, observation time, temperature, and units.

The request log stores fast first-seen time, daily-history first-seen time,
match status, and lead seconds. Raw snapshots store the NO bid/ask depth seen at
the fast wake. These records measure whether the extra feed actually finds
prices before they reach 0.99 without granting it trading authority.

## Why This Works

The fast feed is a doorbell; daily history is the identity check. The doorbell
may trigger work sooner, but it cannot slow, replace, or consume the protected
capacity of the identity check. Short failure bounds keep an optional network
path out of the critical path, while separate request ceilings prevent a burst
of research calls from exhausting the provider allowance.

Rotating station submission does not invent more API capacity. It makes the
limited capacity move across the active city set so a fixed map order does not
systematically favor Europe over later Asian cities.

## Prevention

- Keep `test_wunderground_fast_shadow_honors_response_cache_control` asserting
  both the reduced fast-call count and the unchanged 5-second history calls.
- Keep `test_wunderground_fast_shadow_timeout_cannot_delay_daily_history_by_twenty_seconds`
  proving the authoritative call follows after at most 2 seconds.
- Keep `test_wunderground_fast_budget_yields_capacity_to_daily_history` proving
  fast calls cannot consume the protected authoritative allowance.
- Keep `test_official_station_refresh_rotates_which_city_is_submitted_first`
  preventing fixed-order city starvation.
- Keep the fast-wake order-book test asserting that no paper trade is written
  before matching daily-history evidence arrives.

## Related Issues

- [Prefetch AWC METAR stations in bulk](../best-practices/prefetch-awc-metar-stations-in-bulk.md)
- [Wunderground ZGSZ source mismatch](../integration-issues/wunderground-zgsz-source-mismatch.md)
- [WRH timeseries requires direct temperature verification](../integration-issues/wrh-timeseries-requires-direct-temp-verification.md)
- [One-shot station changes must bypass bounded probe limits](../logic-errors/one-shot-station-changes-must-bypass-probe-limits.md)
