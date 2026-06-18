# Expand Temperature Settlement Stations Design

## Goal

Register the eight active Polymarket temperature-market cities missing from the
local station registry, but enable paper execution only when the settlement
station, coordinates, timezone, settlement unit, and live AWC METAR evidence
all agree.

## Verified Evidence

The Polymarket Gamma event descriptions for June 19, 2026 name these settlement
stations and units. AWC METAR returned current rows for all eight station IDs,
including matching `icaoId`, coordinates, observation time, and temperature.

| city | station | AWC coordinates | timezone | settlement unit | AWC live METAR |
| --- | --- | --- | --- | --- | --- |
| austin | KAUS, Austin-Bergstrom International Airport Station | 30.1831, -97.6806 | America/Chicago | whole Fahrenheit | verified |
| denver | KBKF, Buckley Space Force Base Station | 39.7130, -104.7580 | America/Denver | whole Fahrenheit | verified |
| houston | KHOU, William P. Hobby Airport Station | 29.6458, -95.2821 | America/Chicago | whole Fahrenheit | verified |
| kuala lumpur | WMKK, Kuala Lumpur Intl Airport Station | 2.7470, 101.7140 | Asia/Kuala_Lumpur | whole Celsius | verified |
| lucknow | VILK, Chaudhary Charan Singh Intl Airport Station | 26.7610, 80.8890 | Asia/Kolkata | whole Celsius | verified |
| mexico city | MMMX, Benito Juarez International Airport Station | 19.4360, -99.0720 | America/Mexico_City | whole Celsius | verified |
| san francisco | KSFO, San Francisco International Airport Station | 37.6196, -122.3656 | America/Los_Angeles | whole Fahrenheit | verified |
| sao paulo | SBGR, Sao Paulo-Guarulhos International Airport Station | -23.4320, -46.4690 | America/Sao_Paulo | whole Celsius | verified |

## Registration Contract

Add all eight cities to `STATION_MAP` with:

- the AWC coordinates and station elevation
- the station-local IANA timezone
- exact Polymarket rule URL and settlement-station wording
- settlement temperature unit and whole-degree precision
- `metar`, provider enabled, confidence grade A

Because every station returned current same-station AWC METAR evidence, all
eight may enter `TRADING_READY_STATION_MAP`. Karachi remains registered but
excluded, so the resulting totals are 49 registered cities and 48
paper-execution cities.

## Forecast Budget

The existing 3-hour Open-Meteo answer cache is safe for 40 execution cities but
not 48:

```text
48 cities x 8 batches/day x 31 units = 11,904 units/day
```

Raise `FORECAST_CACHE_TTL_SECONDS` to 14,400 seconds (4 hours):

```text
48 cities x 6 batches/day x 31 units = 8,928 units/day
```

This remains under the 10,000-unit daily limit. The 15-second spacing between
real requests remains unchanged. Cached forecast answers may still be combined
with the 60-second AWC nowcast refresh; forecast freshness and nowcast freshness
remain separate clocks.

## Failure Handling

- Missing or malformed AWC rows do not produce same-station evidence.
- A station ID mismatch remains fail-closed.
- A future Polymarket rule conflict removes that city from the trading-ready
  subset until reconciled.
- Open-Meteo errors continue to skip one city; HTTP 429 stops the full batch.
- No wallet, private key, signing, or real-order behavior is added.

## Tests

Add focused tests proving:

- registry totals become 49 registered and 48 trading-ready
- all eight cities carry the expected station IDs, coordinates, timezone,
  settlement unit, precision, rule source, and AWC provider state
- all eight station IDs are included in the AWC bulk request
- real fixture-like AWC rows for the new stations are accepted only when the
  row station ID matches
- forecast TTL defaults and deployment examples become 14,400 seconds
- the 48-city daily forecast budget stays below 10,000 units

Run focused registry, provider, config, discovery, and deployment tests before
the complete suite. Deploy code and the VPS forecast-cache setting together,
restart the bot and dashboard, and verify services plus authenticated dashboard
status without resetting runtime ledgers.
