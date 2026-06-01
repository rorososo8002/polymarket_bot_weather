# City-Date Portfolio Selection Design

Created: 2026-06-01 Asia/Seoul
Revised: 2026-06-02 Asia/Seoul

## Scope

Phase 4 changes paper-trading entry selection only. It does not deploy code,
connect a wallet, send real orders, or start Phase 5 nowcast work.

## Problem

The runner currently evaluates and opens one binary market at a time. A Seoul
position can consume the city-date cap before another useful bucket is
considered. The opposite mistake is also dangerous: treating nearby buckets as
independent bets can multiply exposure to one weather outcome.

## Risk Budget

Every new entry uses a conservative reference bankroll:

```text
cost_basis_bankroll = cash + open-position entry cost
liquidation_bankroll = cash + executable sell value of every open position
entry_bankroll = min(cost_basis_bankroll, liquidation_bankroll)
```

Unrealized profits do not increase new-entry size. Executable unrealized losses
reduce it immediately. If an open position cannot be valued from a usable
order book, new entries fail closed until a safe value is available.

The paper defaults are:

```text
single leg                         <= 10% of entry_bankroll and >= $10
same city-date event below $1,000 <= 10% of entry_bankroll
same city-date event at $1,000+   <= 5% of entry_bankroll
same city total                   <= 20% of entry_bankroll
all open positions                <= 90% of entry_bankroll
selected legs per event           <= 2
```

The `$1,000` transition uses `entry_bankroll`, not a temporary market-price
profit. A later cap increase is a separate paper-evidence decision.

## Selection

The runner evaluates a city-date event before opening candidates. It
normalizes the event's bucket probabilities to 100%, builds a payout table for
each possible final temperature, and compares one-leg and at-most-two-leg
allocations in whole-dollar increments. Each opened leg must be at least `$10`.

A complementary leg is allowed only when:

- every included leg retains positive expected net profit after costs;
- it remains inside the shared city-date budget;
- it does not exceed city, total, cash, or single-leg limits;
- it is not the opposite side of an already held market;
- it does not create uncontrolled same-direction concentration.

For distinct non-overlapping temperature buckets, compare `YES+YES`, `YES+NO`,
and `NO+NO`. A `NO` leg wins in every final-temperature scenario except its
own bucket, so it must be scored as part of the event payout table rather than
as an independent trade. Same-market `YES+NO`, overlapping thresholds, and a
third leg remain blocked.

With a `$100` bankroll, the event cap and the minimum leg are both `$10`, so
only one leg can open. With `$200`, the event budget is `$20`, so the optimizer
may select two `$10` legs.

## Logging And Dashboard

Each evaluated event writes a bounded JSONL event-level decision record. The
record contains the reference bankroll, cap fraction, cap value, existing
exposure, selected legs, rejected legs, expected net profit, expected log
growth, normalized scenario probabilities, and scenario PnL.
The dashboard exposes recent event portfolio decisions and explains the
adaptive cap without loading a large runtime file in full.

## Verification

Tests cover:

- one-leg selection;
- a complementary two-leg selection;
- repeated `NO` and mixed `YES+NO` selection across distinct buckets;
- event-probability normalization;
- minimum `$10` leg enforcement;
- 20% city and 90% total exposure enforcement;
- same-market contradiction blocking;
- city-date cap enforcement;
- `$1,000` cap transition;
- conservative bankroll calculation from executable liquidation value;
- fail-closed valuation when a held position cannot be priced;
- event-level log reconstruction and dashboard payload explanation.
