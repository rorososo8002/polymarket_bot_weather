---
title: Low exact entries require rise confirmation
date: 2026-06-27
category: logic-errors
module: weather_bot.nowcast, weather_bot.station_signal, weather_bot.dashboard
problem_type: logic_error
component: service_object
symptoms:
  - "A lowest-temperature exact YES entered after q75 formation time but before the daily low had actually rolled over."
  - "The selected bucket became nearly worthless when the station later printed a lower low."
  - "The dashboard rebuilt the open-position title as a highest-temperature event and linked only the parent event."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [paper-trading, low-exact, nowcast, rollover, dashboard-link]
---

# Low exact entries require rise confirmation

## Problem

The bot bought a London lowest-temperature 22°C YES after the low formation
q75 minute had passed and residual probability looked attractive. About an
hour later, the same settlement station printed a new 21°C low, making the
22°C exact YES effectively worthless.

## Symptoms

- The paper position question was `Will the lowest temperature in London be
  22°C on June 27?`, but the dashboard displayed a rebuilt
  `Highest temperature...` event title.
- The position entered at 35.8¢ average and later marked around 0.1¢ after
  EGLC updated the station-local daily low to 21°C.
- `metar_daily_extremes_state.json` tracked `low_observed_at` but did not track
  whether the low had been followed by a higher observation.

## What Didn't Work

- Waiting only for the city/month/direction q75 low-formation minute was not
  enough. A q75 cutoff still leaves a tail of days where the low moves later.
- Reusing the high-temperature rollover rule conceptually did not help until
  the low side stored its own opposite-move timestamp.
- A generated dashboard title made the trade look like a high-temperature
  mistake, slowing diagnosis.

## Solution

Track the low-side mirror of high rollover:

- `low_last_observed_at`: the latest observation that still matched the current
  daily low.
- `low_rise_observed_at`: the first later observation above the current daily
  low.

Then block ordinary exact low YES entries until `low_rise_observed_at` exists.
Keep irreversible low strong-NO behavior unchanged: if the observed low is
already below the exact bucket lower bound, that YES is impossible.

The dashboard should also use the stored position `question` as the event title
and build links to the exact Polymarket submarket when a market slug exists:

```text
/event/<event-slug>/<market-slug>
```

## Why This Works

For daily highs, a later lower observation is practical evidence that the high
has rolled over. For daily lows, the symmetric evidence is a later higher
observation. Without that opposite move, the low can still be active and can
fall into a lower exact bucket.

This does not claim the day is final. It only prevents the bot from treating a
single observed low as locked while the temperature has not yet bounced.

## Prevention

- Every exact high YES gate that depends on “rolled over” needs a low-side
  mirror before enabling exact low YES.
- Regression tests should cover both sides:
  - high exact waits for a later lower observation;
  - low exact waits for a later higher observation;
  - dashboard titles preserve the actual market question;
  - market links point to the exact submarket, not only the parent event.
- When diagnosing an apparent wrong-direction trade, check `paper_state.json`
  first. The display layer can be wrong; the paper account book is the source
  of truth for what was actually bought.

## Related Issues

- [High-confidence paper entries need independent risk gates](./high-confidence-paper-entries-need-independent-risk-gates.md)
- [Surface nowcast evidence in open-position dashboard payloads](./dashboard-open-position-nowcast-payload.md)
