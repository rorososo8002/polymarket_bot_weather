---
title: Use temporary remote scripts for PowerShell SSH mutations
date: 2026-06-04
category: workflow-issues
module: vps_operations
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - "Changing VPS files or service settings from Windows PowerShell"
  - "A remote SSH command contains quotes, braces, parentheses, semicolons, sed, JSON, or shell substitutions"
  - "A PowerShell-to-SSH command fails with parser or quoting errors"
tags: [powershell, ssh, quoting, vps, deployment, workflow]
---

# Use temporary remote scripts for PowerShell SSH mutations

## 1. What The Problem Was

A VPS environment edit was attempted as one long inline PowerShell SSH command:

```powershell
ssh -i $key $oracle "set -e; env=/etc/...; sed ...; if ...; then ..."
```

That command failed because Windows PowerShell, `ssh`, and the remote Linux
shell all tried to interpret the same quotes, parentheses, semicolons, and
regular expressions. The fix eventually worked only after switching to a small
temporary shell script copied to the VPS.

## 2. Why It Was A Problem

Nested quoting is not just annoying. It wastes operator time during deployment
and can make a safe, narrow change look risky. Each retry also increases the
chance of accidentally changing the wrong file, printing the wrong output, or
leaving a test process behind.

Think of it as passing one sentence through three translators. If the sentence
contains punctuation-heavy code, each translator may split it differently. The
safe answer is to send the remote machine a written checklist file, then ask it
to run that file.

## 3. How It Was Fixed

For complex remote changes, use the temporary-script pattern:

1. Write a narrow local script under `.deploy_tmp/`, such as
   `.deploy_tmp/update_forecast_env_1800.sh`.
2. Keep the script focused on one job and explicit paths.
3. Copy it to `/tmp` with `scp -i`.
4. Run only `bash /tmp/script-name.sh` over SSH.
5. Verify the result with simple `grep`, `systemctl`, `tail`, or `cat`
   commands.
6. Delete the local `.deploy_tmp` artifact after the operation.

Good shape:

```powershell
scp -i $key .deploy_tmp\update_forecast_env_1800.sh "${oracle}:/tmp/update_forecast_env_1800.sh"
ssh -i $key $oracle bash /tmp/update_forecast_env_1800.sh
```

Avoid:

```powershell
ssh -i $key $oracle "set -e; sed -i 's#...#...#' /etc/...; if grep ...; then ..."
```

## 4. What To Check Next Time

- Start VPS work from `docs/codex/known-good-commands.md`.
- Read `docs/codex/ssh-powershell.md` before PowerShell SSH work.
- If the command has quote-heavy logic, do not try an inline SSH command first.
  Use a temporary remote script.
- If an inline SSH command fails with a parser or quoting error, switch
  approaches immediately. Do not keep trying quote variants.
- Make the script auditable: one purpose, explicit target file, backup before
  mutation, and a final verification printout.

## 5. What This Project Must Be Especially Careful About

Do not print, open, copy, or commit private key contents, dashboard tokens, API
keys, wallet keys, or seed phrases. The SSH private key is only an identity file
for `ssh -i` and `scp -i`.

For the weather bot, runtime ledgers such as `paper_state.json`,
`paper_trades.csv`, `paper_decisions.csv`, and request logs are evidence. A
remote script must not delete or truncate those files unless the user explicitly
approved that exact runtime-data operation.
