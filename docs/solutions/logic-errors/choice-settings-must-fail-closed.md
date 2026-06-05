---
title: Choice Settings Must Fail Closed
date: 2026-06-05
category: logic-errors
module: weather_bot.config
problem_type: logic_error
component: tooling
symptoms:
  - "SIZE_MODE=kellyy could pass startup without a clear operator error."
  - "The paper runner could use the fixed-fraction sizing branch while the operator believed Kelly sizing was active."
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags: [config, settings, validation, fail-closed, paper-trading, risk]
---

# Choice Settings Must Fail Closed

## Problem

`SIZE_MODE` is the paper order-size method switch. It chooses whether the bot
sizes entries by a fixed account fraction or by fractional Kelly.

Before this fix, an invalid value such as `SIZE_MODE=kellyy` was not rejected
when `Settings` was created. The runner checked only whether the value was
`kelly`; every other value effectively fell through to the fixed-fraction path.

## Why It Was A Problem

Paper trading is the measurement rig for this strategy. The bot is trying to
learn whether its weather-market decisions might be profitable under a specific
risk policy.

If `SIZE_MODE` is misspelled, the operator may think the experiment is using
Kelly sizing while the code uses fixed-fraction sizing. That changes position
sizes, drawdown behavior, and the meaning of the paper-performance sample.

Think of `SIZE_MODE` as a two-position switch, not a memo field. A switch with
only two safe positions must not accept a third spelling and quietly choose one
for the operator.

## How It Was Fixed

`weather_bot.config.Settings.__post_init__` now validates `size_mode` with the
same choice validator already used for other enumerated settings:

```python
_SIZE_MODES = ("fixed_fraction", "kelly")

_validate_choice(self, "size_mode", _SIZE_MODES)
```

The validator strips whitespace, normalizes case to lowercase, and raises
`ValueError` when the normalized value is not in the allowed set.

Focused tests now prove both sides of the rule:

- `SIZE_MODE=KeLlY` becomes `settings.size_mode == "kelly"`.
- `SIZE_MODE=kellyy` raises `ValueError` with `SIZE_MODE` and the allowed
  choices in the message.

## What To Check Next Time

- When adding a string setting that selects behavior, define an explicit
  allowed-value tuple near the other settings constants.
- Validate it in `Settings.__post_init__` instead of relying on downstream
  `if` or `else` branches.
- Add one test for accepted case normalization and one test for an invalid
  typo that must raise `ValueError`.
- Make the error message include the environment setting name and the allowed
  values so the operator can fix the setting without reading the code.

## What This Project Must Be Especially Careful About

Choice settings that affect money, risk, execution mode, evidence sources, or
runtime safety are not cosmetic labels. For this weather bot, they define which
paper experiment is being measured.

Fail closed at startup. A bad choice setting should stop the bot before it can
write misleading rows to `paper_trades.csv`, `paper_decisions.csv`, or
`paper_state.json`.

## Related

- `docs/solutions/logic-errors/boolean-env-values-must-be-explicit.md`
- `docs/solutions/logic-errors/numeric-settings-must-fail-closed.md`
- `docs/solutions/logic-errors/weather-bias-json-must-fail-closed.md`
