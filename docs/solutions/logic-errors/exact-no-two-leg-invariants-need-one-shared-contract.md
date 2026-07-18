---
title: Exact NO two-leg invariants need one shared contract
date: 2026-07-19
category: logic-errors
module: weather_bot.config, weather_bot.portfolio, weather_bot.paper
problem_type: logic_error
component: service_object
symptoms:
  - "MAX_EVENT_PORTFOLIO_LEGS=3 could reopen a third city-date position."
  - "Candidates from different cities or local dates could be grouped by a colliding event_id."
  - "A compatible exact-NO candidate could be logged with a false payoff-overlap rejection."
  - "Independent rounding of paired order sizes could exceed the shared budget by a tiny amount."
root_cause: missing_validation
resolution_type: code_fix
severity: high
related_components:
  - weather_bot.config
  - weather_bot.paper
  - testing_framework
tags:
  - paper-trading
  - exact-temperature
  - no-only
  - portfolio
  - city-date
  - validation
  - rejection-reasons
  - exposure-budget
---

# Exact NO two-leg invariants need one shared contract

## Problem

The paper bot needed to allow two distinct exact-temperature NO positions for
one city and local date when both refer to the same daily high or daily low.
Changing the default leg count was not enough: configuration, event grouping,
selection, rejection logging, and dollar allocation all had to enforce the
same safety contract.

## Symptoms

- An environment value of `MAX_EVENT_PORTFOLIO_LEGS=3` bypassed the intended
  two-position ceiling.
- A reused or colliding external `event_id` could put different cities or local
  dates into one portfolio calculation, even though both NO positions could
  then lose independently.
- Selection used the new compatibility rule while rejection logging still used
  the older payoff-overlap rule, producing an incorrect audit reason.
- Rounding both proportional order sizes upward could exceed the shared
  city-date budget by less than one millionth of a dollar.

## What Didn't Work

- Updating only the default value treated a safety invariant as an operator
  preference.
- Trusting an external event identifier treated grouping metadata as settlement
  evidence.
- Duplicating compatibility logic allowed the decision and its explanation to
  disagree.
- Ordinary rounding was unsuitable for a hard exposure ceiling because it can
  move a value upward.

## Solution

Enforce the same invariant at every boundary:

1. Accept only one or two city-date legs; reject larger settings at startup.
2. Compare every candidate's parsed city and local date with the first
   candidate and fail closed if the group is mixed.
3. Use `_event_legs_are_compatible` for both portfolio selection and rejected
   reason classification. Compatible third legs are logged as
   `event leg cap reached`; incompatible pairs are logged as
   `event legs are not complementary`.
4. Floor each proportional order size to six decimal places so their sum never
   crosses the shared exposure budget.
5. Keep broker-level checks as the final backstop even when a caller bypasses
   the portfolio selector.

Regression coverage includes a setting of three, a third direct broker order,
mixed-city event identifiers, non-exact ranges and tails, mixed high/low exact
buckets, rejection-ledger reasons, and an asymmetric rounding boundary. The
complete local suite passed with 824 tests.

## Why This Works

The number `2` is now a hard system limit rather than a default suggestion.
Parsed city and local date prove that both contracts describe the same weather
event before the special exact-NO rule applies. Reusing one compatibility
predicate keeps action and audit evidence aligned. Flooring dollar amounts
makes floating-point error conservative instead of allowing accidental excess
exposure.

## Prevention

- Pair a changed safety default with explicit lower and upper validation.
- Do not use external grouping identifiers as the sole proof that candidates
  share one settlement outcome.
- Derive rejection reasons from the same predicate that admits or rejects the
  action.
- Test both the strategy selector and the broker backstop for position limits.
- For hard money limits, test asymmetric values near the rounding boundary and
  round toward the safe side.

## Related Issues

- [Correlated event budget needs a broker backstop](correlated-event-budget-needs-broker-backstop.md)
- [High-confidence paper entries need independent risk gates](high-confidence-paper-entries-need-independent-risk-gates.md)
- [Portfolio scenario probabilities must be coherent before normalization](portfolio-scenario-probabilities-must-be-coherent.md)
