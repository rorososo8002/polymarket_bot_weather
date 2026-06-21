---
title: Structured risk decisions must stay structured across compatibility boundaries
date: 2026-06-21
last_updated: 2026-06-22
category: best-practices
module: weather_bot.edge
problem_type: best_practice
component: service_object
severity: high
applies_when:
  - "A sizing or risk decision carries a tier, fraction, and override metadata."
  - "A scalar-returning API is being replaced by a structured result."
  - "Old test fixtures must be migrated to new cross-field configuration invariants."
  - "A downstream gate changes an executable decision into a SKIP or HOLD result."
tags: [paper-trading, risk, sizing, structured-data, compatibility, configuration]
---

# Structured risk decisions must stay structured across compatibility boundaries

## Context

The calibrated observation strategy changed sizing from one floating-point
fraction into a decision with three meanings:

- probability tier,
- requested bankroll fraction,
- optional event-cap override.

An early compatibility attempt made the new result inherit from `float`. That
let old callers keep treating the object as a number, but it also blurred which
part of the decision they were actually using and complicated equality,
testing, and metadata propagation.

The same migration exposed invalid test settings where a 50% top tier was
combined with a 10% or 30% single-market maximum. Allowing a special legacy
exception would have made startup validation depend on whether values happened
to equal old defaults.

## Guidance

Keep the decision as an explicit immutable structure:

```python
@dataclass(frozen=True)
class ObservationSizingTier:
    probability_tier: str
    entry_fraction: float
    event_cap_override_fraction: float | None = None
```

Callers must select the exact field they need:

```python
tier = observation_edge_entry_fraction(side_probability, ...)
size_fraction = tier.entry_fraction if tier is not None else fallback_fraction
```

Treat a cap override like a signed permission slip, not a number that can be
reconstructed later. Every downstream boundary must require the signal and the
evaluated result to agree on the tier, selected-side probability, requested
fraction, and event-cap override. Probability alone must never recreate a 50%
permission.

The portfolio selector and the broker must enforce the same hard limits
independently. The selector chooses a valid plan; the broker is the final
backstop for direct calls and stale callers. In particular, the broker must
recheck single-market, total-exposure, cash, city, and event-date limits before
writing the paper ledger. Same-side additions count existing market cost toward
the single-market cap.

When a later gate changes only the disposition of an immutable decision, use
`dataclasses.replace` so its audit evidence survives:

```python
blocked = replace(
    evaluated,
    side="SKIP",
    size_usd=0.0,
    size_shares=0.0,
    reason=block_reason,
)
```

Reconstructing `EdgeResult(...)` from only side, price, edge, and reason silently
drops selected-side probability, calibration profile, probability tier,
requested size, executable size, and cap-override evidence. This is especially
dangerous for SKIP rows because a rejected trade still needs enough evidence to
explain why it was considered and why it was blocked.

Do not retain the old calculation under legacy argument names. If the old
90/97 thresholds and abnormal-price fraction are no longer the strategy, the
replacement API should reject those arguments instead of silently accepting
and ignoring them.

Cross-field configuration rules must also be unconditional:

```python
if observation_tier_95_fraction > max_single_market_fraction:
    raise ValueError(...)
```

Tests that need a smaller single-market cap should provide a smaller, strictly
ascending tier table. That preserves the test's real purpose without weakening
production validation.

## Why This Matters

A scalar answers only "how much." A structured risk decision also answers
"why this much" and "which ordinary cap may be overridden." Losing those fields
can make a 50% paper position indistinguishable from an ordinary fraction and
can allow downstream code to infer privileges from text or old defaults.

Strict cross-field validation also keeps the strategy's rule sheet internally
consistent before any market data or paper money is evaluated.

## When to Apply

- When replacing a primitive return value with a domain decision.
- When compatibility code would require subclassing `float`, `str`, or another
  scalar solely to keep old callers running.
- When one configuration field places a hard limit on another.
- When unrelated tests construct settings that no longer satisfy the active
  strategy contract.

## Examples

Avoid:

```python
class Tier(float):
    probability_tier: str
```

Prefer:

```python
tier = ObservationSizingTier("95", 0.50, 0.50)
requested_fraction = tier.entry_fraction
```

## Related

- [Numeric settings must fail closed](../logic-errors/numeric-settings-must-fail-closed.md)
- [Portfolio scenario probabilities must be coherent before normalization](../logic-errors/portfolio-scenario-probabilities-must-be-coherent.md)
