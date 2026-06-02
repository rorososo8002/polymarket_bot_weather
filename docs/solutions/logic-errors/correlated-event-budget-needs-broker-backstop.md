# Correlated Event Budget Needs A Broker Backstop

## 1. What Went Wrong

Phase 4 added event-level portfolio selection so the bot could choose multiple
useful legs inside the same city+date weather event. That created a risk: if the
selector is the only place enforcing the shared city+date budget, a later order
path could accidentally treat those legs as independent trades.

The dangerous mistake is simple: two bets inside the same city and date are not
two independent opportunities. They are different slices of the same weather
outcome.

## 2. Why It Mattered

If correlated legs are allowed to spend independent budgets, total exposure can
be much larger than the strategy intended. A single forecast miss can then hurt
multiple positions at once.

This matters for a paper bot too. Paper trading is where risk rules are tested.
If the simulation allows impossible or oversized exposure, later return analysis
will look better than the strategy really deserves.

## 3. How It Was Fixed

Portfolio selection shares one city+date budget across selected legs. The broker
also keeps a backstop check so exposure limits are enforced even if a future
caller bypasses the selector or sends a malformed candidate.

The broker check is important because it is closer to the point where positions
are created. Strategy selection can rank ideas, but the broker is the final gate
that decides whether a paper position is actually recorded.

## 4. What To Check Next Time

- Confirm that event-level selection and broker-level exposure checks agree.
- Test multiple legs from the same city+date event, not only one leg at a time.
- Confirm that opposite sides in the same market are blocked.
- Confirm that same-direction concentration does not exceed the shared budget.
- Keep rejection reasons visible in the paper decision log.

## 5. Project-Specific Caution

Weather interval markets are correlated by design. A Seoul high-temperature
event and another Seoul interval for the same date are both driven by the same
settlement station outcome. Treating them as independent can silently inflate
paper returns and hide downside risk.
