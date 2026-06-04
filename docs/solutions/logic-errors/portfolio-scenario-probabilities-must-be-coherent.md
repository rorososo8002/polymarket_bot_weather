---
title: Portfolio scenario probabilities must be coherent before normalization
date: 2026-06-05
category: logic-errors
module: weather_bot.portfolio
problem_type: logic_error
component: service_object
symptoms:
  - "`scenario_probabilities` could be normalized just because its probability sum was at least one."
  - "Incomplete or overlapping temperature buckets could still feed the event portfolio optimizer."
  - "Paper entries could be selected from a weather outcome table that did not match the real event."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [weather-bot, portfolio, probability, fail-closed, temperature-buckets]
---

# Portfolio scenario probabilities must be coherent before normalization

## 1. What The Problem Was

`scenario_probabilities` is the portfolio outcome table. It says, for example:
if Seoul ends at 26C, this bucket wins; if it ends at 27C, another bucket wins;
if it lands somewhere else, the `other` scenario applies.

The old portfolio code normalized this table when either of these was true:

- the temperature intervals covered the whole outcome space
- the probability sum was at least `1.0`

The second rule was too loose. A table can sum to more than `1.0` because the
bot counted overlapping buckets, or because it missed part of the weather world
and then forced the visible buckets to add up.

## 2. Why It Was A Problem

Portfolio optimization depends on the scenario table. Think of it as the answer
sheet used to grade every possible weather result. If the answer sheet is wrong,
the optimizer can choose legs that look profitable only because the cases were
miscounted.

For example, exact `26C` and exact `28C` do not cover every possible Seoul high
temperature. If their probabilities add up to more than one, the right response
is not to scale them down. Scaling them down hides the fact that the table is
not a trustworthy description of the event.

The same danger exists when intervals overlap. Two range buckets such as
`86-87F` and `87-88F` both include `87F`, so one real weather outcome is counted
twice. That must not reach paper entry selection.

## 3. How It Was Fixed

`weather_bot.portfolio` now assesses scenario probabilities before the optimizer
can use them:

- normalize only when every candidate has a parsed temperature interval and the
  intervals form one non-overlapping exhaustive ladder from lower tail through
  upper tail
- keep `other` when the intervals are incomplete and the probability sum is
  below one
- fail closed when intervals overlap
- fail closed when intervals are not exhaustive and the probability sum is
  meaningfully above one
- allow only tiny floating-point dust with an epsilon of `1e-9`

Fail closed means the bot does not guess and does not select a leg. The
portfolio decision records a human-readable rejection reason such as
`scenario probabilities overlap` or
`scenario probabilities exceed one without exhaustive intervals`.

## 4. What To Check Next Time

- Do not normalize a probability table only because the numbers add to one.
- First check whether the intervals are mutually exclusive.
- Then check whether they cover every possible temperature result.
- Add a test for the safe incomplete case where `other` remains.
- Add a test for the unsafe incomplete case where the sum exceeds one.
- Add overlap tests for exact/tail buckets and range buckets when range parsing
  exists.
- Use a tiny epsilon only for computer arithmetic dust, not as a strategy
  tolerance.

## 5. What This Project Must Be Especially Careful About

This bot exists to validate paper-trading edge. A clean-looking percentage
table is not enough evidence. Weather bucket probabilities must describe the
same real-world event that Polymarket will settle.

Never repair incoherent event buckets by rounding temperatures, widening
intervals, inventing range parsing, or silently rescaling probabilities. If the
outcome table is incomplete but below one, keep `other`. If it overlaps or
exceeds one without full coverage, skip the portfolio selection and leave a
clear rejection reason.

## Related

- [Temperature range buckets must preserve both endpoints](temperature-range-buckets-must-preserve-endpoints.md)
- [Paper fees must flow through accounting](paper-fees-must-flow-through-accounting.md)
