# Discover Weather Events Before Binary Markets

## 1. What Went Wrong

The bot originally risked treating each binary submarket as if it were the whole
weather opportunity. That made `MAX_MARKETS=41` look like enough coverage for 41
cities, while in reality the discovery loop could spend those slots on many
intervals from only a few city+date events.

## 2. Why It Mattered

Polymarket weather markets often come as a group of related interval questions:
exactly a temperature, at-or-below a threshold, or at-or-above a threshold. A
bot that counts binary markets instead of city+date events can miss most cities
before the strategy even evaluates them.

That is a discovery bug, not an edge bug. The model cannot choose good trades
from events it never sees.

## 3. How It Was Fixed

Discovery was moved toward an event-first model. The bot groups related weather
markets by city and date, then evaluates the binary legs inside that event with
consistent probability math.

This lets `MAX_MARKETS` describe how much market data to inspect while the
strategy reasons about the actual unit of weather risk: one city, one date, and
one settlement outcome.

## 4. What To Check Next Time

- Count discovered city+date events separately from binary submarkets.
- Add tests where one event contains multiple interval legs.
- Confirm that the parser handles exact, lower-bound, and upper-bound wording.
- Confirm that probability mass across related intervals is internally
  consistent.
- Keep dashboard and docs clear about event count versus market count.

## 5. Project-Specific Caution

The supported city list comes from `STATION_MAP`, but discovery still depends on
real market listings. Do not assume one market slot equals one supported city.
The bot must discover events broadly enough before it can judge edge quality.
