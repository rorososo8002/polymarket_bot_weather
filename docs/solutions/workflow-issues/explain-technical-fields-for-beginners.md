# Explain Technical Fields For Beginners

## 1. What Went Wrong

The project uses many trading, API, and operations terms: `settlement EV`,
`VWAP`, `fee-rate`, `stale`, `condition ID`, `token ID`, WebSocket health, and
many more. When those terms were listed without explanation, the user had to
guess what they meant and why they mattered.

## 2. Why It Mattered

This is a trading bot, so unclear terminology can lead to risky decisions. A
field like `settlement EV` is not decorative. It estimates the value of holding
a position until final resolution after conservative uncertainty adjustments.
That helps decide whether the bot should sell now, recover principal, or keep a
small runner until settlement.

If the explanation is missing, the user cannot tell whether a setting is a
strategy rule, a safety guard, a data-quality check, or only a log field.

## 3. How It Was Fixed

`AGENTS.md` now requires beginner-friendly explanations for developer terms,
commands, field names, settings, status values, API names, and feature names.

The expected explanation pattern is:

- What the term is.
- How it works in practice.
- Where the project uses it.
- What becomes better when it exists.
- Why this project needs it.

## 4. What To Check Next Time

- Do not only name a field; explain its job.
- When mentioning an API, say what data it returns and why the bot needs it.
- When mentioning a status value such as `stale` or `skip`, explain what action
  the bot takes because of that status.
- When discussing money or risk, explain both the benefit and the failure mode.

## 5. Project-Specific Caution

The user wants Korean responses during normal conversation, but repository docs
now use English. Even when the file text is English, final explanations to the
user should remain clear, patient, and beginner-friendly unless the user asks
for another language.
