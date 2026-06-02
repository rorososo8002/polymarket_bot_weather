# Shadow Research Needs Local Guards And Paired Samples

## 1. What The Problem Was

The public-signal research collector and report had four boundary mistakes:

- It trusted the remote API minimum-cash filter without checking parsed rows
  locally.
- It deduplicated by transaction hash alone, even though one transaction can
  contain multiple distinct market or outcome rows.
- A retention limit of `0` still kept one row.
- It compared all resolved external signals against only the bot's scoreable
  entries, so bot `SKIP` rows could inflate the apparent external advantage.

## 2. Why It Was A Problem

Shadow research is intentionally separated from execution, but misleading
research can still lead to a bad future strategy experiment. Unequal
denominators create an apples-to-oranges comparison: the public signal gets
credit for rows where the bot deliberately abstained, while the bot has no
score on those rows.

## 3. How It Was Fixed

The collector rechecks `SHADOW_MIN_TRADE_USDC` after parsing every row.
Deduplication uses transaction hash plus market, wallet, side, outcome, asset,
timestamp, price, and size. A zero retention cap returns an empty dataset.

The report still shows all resolved external signals for diagnosis, but
experiment promotion uses only paired resolved rows where both the external
signal and a real bot `YES` or `NO` entry can be scored.

## 4. What To Check Next Time

- Test remote responses that violate the requested filter.
- Test two distinct rows from one transaction.
- Test retention caps at `0`, `1`, and a normal positive value.
- Print both the all-resolved external rate and the paired external/bot rates.
- Require equal denominators before calculating a promotion candidate.

## 5. What This Project Must Be Especially Careful About

A large public trade is evidence to study, not proof of edge. Keep shadow data
bounded, public-only, paper-only, and separate from order execution. Bot
abstention is a useful diagnostic outcome, but it is not automatically a loss.
