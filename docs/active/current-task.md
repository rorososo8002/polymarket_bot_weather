# Current Task

Status: active

## Objective

Replace the model-led paper strategy with an official settlement-station
observation strategy, deploy it, and reset the paper experiment to a fresh
$200 account.

## Current Scope

- Remove model-only new-entry behavior from the active runner.
- Use only explicit official settlement-station nowcast/observation evidence
  for new entries.
- Whole-degree Celsius exact buckets use source-display integer settlement:
  `23.7C` remains in the `23C` bucket; `24.0C` breaks a daily-high `23C`
  YES and creates the strong NO lock.
- Official station lock sizing is 20% for base lock and 50% for strong lock,
  still subject to executable depth, fees, spread, expected return, and
  exposure caps.
- Keep execution paper-only. Do not add wallet, key, signing, real orders, or
  live trading paths.
- Deploy the strategy to the Oracle VPS after local verification and reset
  runtime ledgers/archives for a fresh $200 paper experiment.

## Next Action

Complete local verification, deploy the official settlement-station strategy to
the Oracle VPS, and reset runtime ledgers/archives for a fresh $200 experiment.

## New Chat Prompt

Continue the official settlement-station strategy conversion. Keep the bot
paper-only, deploy after verification, then reset server runtime ledgers and
archives for a fresh $200 paper account.
