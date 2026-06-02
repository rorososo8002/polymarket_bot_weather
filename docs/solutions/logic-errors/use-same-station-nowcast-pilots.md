# Use Same-Station Nowcast Pilots

## 1. What Went Wrong

Same-day nowcast logic can become misleading if it uses a convenient nearby
weather feed instead of the official settlement station. A city-center reading
may look more intuitive, but the market settles on the station named in the
rules.

## 2. Why It Mattered

Nowcast data is powerful because it can update the probability after part of the
day has already happened. But if the observation source is not the settlement
station, the bot is improving confidence in the wrong answer.

This can be worse than no nowcast at all. A missing nowcast should make the bot
skip nowcast-dependent logic; a wrong nowcast can make it enter bad trades with
false confidence.

## 3. How It Was Fixed

Nowcast support was designed as a verified pilot instead of a forced 41-city
feature. The code uses official sources only when they match the settlement
station:

- Aviation Weather Center METAR for verified ICAO stations.
- Hong Kong Observatory max/min data for `hong kong/HKO`.
- Forecast-only behavior when the provider is unavailable, such as
  `karachi/OPMR`.

Each observation records the value, observation time, source, freshness, and
unavailable reason.

## 4. What To Check Next Time

- Start from `STATION_MAP` and verify the exact settlement station.
- Do not substitute city-center weather, nearby airports, or guessed values.
- Test fresh, stale, malformed, and unavailable provider responses.
- If official data is missing, skip nowcast-dependent logic instead of inventing
  a runner decision.
- Keep provider coverage documented by station, not only by city.

## 5. Project-Specific Caution

This bot must fail closed. Same-day observations are useful only when they refer
to the same station that settles the market. Partial provider coverage is
acceptable; silently widening coverage with unverified sources is not.
