---
title: Direct AWC and exact-NO frontier selection prevent late candidate checks
date: 2026-08-02
category: integration-issues
module: weather_bot.nowcast, weather_bot.live_paper_runner
problem_type: integration_issue
component: background_job
symptoms:
  - "Asian station observations reached the bot 30 to 60 minutes behind the official direct feed"
  - "Eligible exact-NO decisions repeatedly reached the book only after the executable ask disappeared near 0.99"
  - "Realtime evaluation requested books for every exact bucket instead of the nearest eligible unheld bucket"
  - "A colliding external event ID could suppress another city or local date"
root_cause: wrong_api
resolution_type: code_fix
severity: high
related_components:
  - "weather_bot.config"
  - "testing_framework"
tags: [awc, metar, exact-no, observation-latency, frontier-selection, order-book, paper-trading]
---

# Direct AWC and exact-NO frontier selection prevent late candidate checks

## Problem

The production paper bot relied on AWC's published current-cache file for the
newest METAR. A production comparison found that the cache still showed Asian
reports 30 to 60 minutes after the official direct METAR API had published
newer rows. After receiving the late observation, the evaluator also inspected
every exact-temperature sibling, including distant buckets already priced near
0.99.

## Symptoms

- The account stayed at zero new fills after deployment.
- Recent exact-NO decisions overwhelmingly ended as `SKIP_NO_EXECUTABLE_ASK`.
- Tokyo, Busan, Seoul, and Shanghai cache rows were 30 to 60 minutes older than
  the direct official response in the same production sample.
- Distant exact buckets consumed book checks even though only the boundary
  nearest the observed high or low could offer the cheapest locked NO.

## What Didn't Work

- Polling the current-cache file more often could not reveal a report that the
  cache publisher had not released yet.
- Raising queue throughput did not remove the upstream 30-to-60-minute delay.
- Keeping every locked exact bucket in the hot path repeated expensive book
  work and filled the audit ledger with equivalent 0.99 rejections.
- Trusting an external `event_id` as the frontier identity could merge unrelated
  cities or local dates.

## Solution

Request the official `/api/data/metar` endpoint once per minute with all mapped
ICAO station IDs in one query. A shared lock makes concurrent city workers reuse
that response. Record requested, returned, and missing station IDs. If the
direct request fails or omits the requested station, fall back to the official
current-cache file; retain history recovery for an incomplete daily ledger.

For Seoul and Busan, start the direct AWC and public KMA reads together. Keep the
existing safety distances: two Celsius degrees for integer AWC METAR evidence
and one Celsius degree only for the precise KMA source.

Before requesting candidate books, select one new-entry exact-NO frontier for
each parsed city, target local date, and high/low direction. A maximum-market
frontier uses the highest eligible threshold below the observed high; a
minimum-market frontier uses the lowest eligible threshold above the observed
low. Exclude held positions from frontier competition, then add them back so
their exit evidence remains monitored.

Expose the selected and excluded market counts and samples in runner status.
This preserves an explanation for filtered siblings without writing thousands
of repetitive decision rows.

## Why This Works

The grouped direct request removes the cache-publication delay without turning
47 stations into 47 HTTP calls. The hard 60-second boundary respects the
provider limit, and the old cache remains a bounded failure path rather than the
primary clock.

The frontier filter moves the likely cheapest fully locked NO to the front and
removes farther siblings before order-book work. Parsed city and local date,
not an untrusted external grouping ID, keep independent markets separate.
Paper-account writes and held-position management remain unchanged.

## Prevention

- Test simultaneous direct calls and prove they perform one HTTP request.
- Test reuse at 59 seconds and a new request at exactly 60 seconds.
- Test direct failure and missing-station responses through the current-cache
  fallback.
- Log returned and missing station IDs for partial-response diagnosis.
- Test frontier separation across colliding event IDs and local dates.
- Test that a held nearest bucket does not suppress the next unheld frontier.
- Test that a farther-only book update does not wake the farther sibling.
- Do not interpret a passing suite as proof of a real fill; verify the deployed
  observation, decision, first executable ask, and paper receipt timeline.

## Related Issues

- [One-shot station changes must bypass bounded probe limits](../logic-errors/one-shot-station-changes-must-bypass-probe-limits.md)
- [Exact NO two-leg invariants need one shared contract](../logic-errors/exact-no-two-leg-invariants-need-one-shared-contract.md)
- [Prefetch AWC METAR stations in bulk](../best-practices/prefetch-awc-metar-stations-in-bulk.md)
