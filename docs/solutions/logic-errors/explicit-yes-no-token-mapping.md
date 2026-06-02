---
title: Map Polymarket token IDs from explicit YES/NO outcomes
date: 2026-06-03
category: logic-errors
module: weather_bot.polymarket_client
problem_type: logic_error
component: service_object
symptoms:
  - "`clobTokenIds[0]` could be treated as YES and `clobTokenIds[1]` as NO even when the market listed outcomes in the opposite order."
  - "Paper trades could record the opposite side from the model decision."
  - "Markets without clear YES/NO labels could still enter discovery as tradable."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [polymarket, token-id, outcomes, paper-trading, fail-closed]
---

# Map Polymarket Token IDs From Explicit YES/NO Outcomes

## 1. What The Problem Was

`clobTokenIds` are the tradable asset IDs for a Polymarket binary market. One
token represents the YES side and the other represents the NO side.

The parser had a fallback that assumed the first `clobTokenIds` value was YES
and the second was NO. That was unsafe because the Gamma payload can also
provide an `outcomes` list, and the order can be `["No", "Yes"]`.

## 2. Why It Was A Problem

Paper trading is the evidence used to judge whether the weather strategy has
edge. If YES and NO token IDs are swapped, the model can make the correct side
decision while the paper ledger records a position in the opposite token.

That contaminates both entry evaluation and later PnL analysis. The bot may
look wrong when it was right, or look profitable for the wrong reason.

## 3. How It Was Fixed

`weather_bot.polymarket_client` now maps token IDs only from explicit labels:

- `tokens[].outcome` or `tokens[].name` when Gamma returns token objects
- `outcomes` zipped with `clobTokenIds` when Gamma returns parallel lists

Both paths require exactly one YES token and one NO token. Missing labels,
duplicated labels, malformed JSON, empty token IDs, or non-YES/NO outcome names
return no tradable token pair. Discovery then skips the market because both
`yes_token_id` and `no_token_id` are required.

The regression test uses a reversed fixture:

```python
{
    "outcomes": json.dumps(["No", "Yes"]),
    "clobTokenIds": json.dumps(["no-token", "yes-token"]),
}
```

The expected result is `yes_token_id == "yes-token"` and
`no_token_id == "no-token"`.

## 4. What To Check Next Time

- When parsing Polymarket market payloads, never infer side from raw list order.
- Add a reversed-order fixture whenever code maps parallel market arrays.
- Add a missing-label fixture and an ambiguous-label fixture that must produce
  SKIP behavior.
- In discovery tests, valid `clobTokenIds` fixtures should include explicit
  `outcomes` labels so the test does not depend on the forbidden fallback.

## 5. What This Project Must Be Especially Careful About

This project is paper-only, but paper results are still decision-making data.
Any parser that guesses a side, station, date, fee, or executable price can make
the strategy validation look better or worse than reality.

For this bot, uncertain market identity must fail closed: skip first, investigate
second, and only trade on data that proves what it represents.

## Related

- `src/weather_bot/polymarket_client.py`
- `tests/test_hardening.py`
- `docs/solutions/logic-errors/paper-fees-must-flow-through-accounting.md`
- `docs/solutions/logic-errors/forecast-date-must-match-market-date.md`
