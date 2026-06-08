---
title: Verify SSH key file before tightening permissions
date: 2026-05-26
last_updated: 2026-06-08
category: workflow-issues
module: vps_operations
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - "Checking Oracle VPS access from Windows PowerShell"
  - "The recursive SSH key search hits an access-denied directory"
  - "The Oracle SSH directory has Korean characters or appears mojibaked in logs"
  - "The sandbox cannot read the local SSH key directory without escalation"
  - "Several SSH checks need to use the same private key"
  - "SSH connects at TCP level but times out during banner exchange"
tags: [ssh, powershell, vps, oracle, private-key, workflow, unicode-path, banner-timeout]
---

# Verify SSH Key File Before Tightening Permissions

## 1. What Went Wrong

An SSH permission fix targeted the Oracle SSH directory instead of the private
key file named `ssh-key-2026-05-25.key`. The directory and the key file are not
the same thing, and `ssh -i` needs the private key file.

A later VPS health check hit a second version of the same workflow issue:
`Get-ChildItem -Recurse` under `C:\Users\wpdla\Documents` failed with an
access-denied error before it could assign `$key`. Because `$key` was empty,
the following `ssh -i $key ... date` command was parsed incorrectly. Several
parallel SSH checks also produced intermittent `Identity file ... not
accessible: Permission denied` errors while trying to read the same Windows
private key.

A third version happened during deployment from the sandboxed Codex
environment: typing the Korean Oracle SSH directory path directly, or relying
on a mojibaked path copied from documentation, produced empty output or access
errors. The fix was to discover the directory object under `Documents` by name
pattern, then join the known key filename. When sandbox permissions blocked the
key directory, the correct next step was a single escalated `scp` or `ssh`
request that did not print or open the key.

A fourth version appeared during live VPS operations: one harmless SSH command
returned `date`, but follow-up SSH checks hung and then failed with
`Connection timed out during banner exchange`. That message happens after TCP
connect and before key authentication, so it is not evidence that the private
key path is wrong. In that case, stop re-litigating the key location, clean up
local stuck `ssh` processes created by timed-out attempts, wait briefly for
remote unauthenticated connection slots to clear, and retry one short command
with the already-known key path.

## 2. Why It Mattered

Tightening permissions on the wrong path can leave SSH still failing while
making the investigation look like a permissions issue. It also increases the
risk of accidentally printing or opening key material while troubleshooting.

For Korean or otherwise non-ASCII path names, hand-typing a path from a log is
fragile. The path may render differently in PowerShell, Codex, Markdown, or the
terminal transcript. Treat the directory object as the truth, not the displayed
text.

## 3. How It Was Fixed

First locate the key file without reading its contents. Prefer object-based
directory discovery over hand-typing the Korean path:

```powershell
$sshDir = Get-ChildItem -LiteralPath "$env:USERPROFILE\Documents" -Directory |
  Where-Object { $_.Name -like '*ssh*' } |
  Select-Object -First 1
$key = Join-Path $sshDir.FullName 'ssh-key-2026-05-25.key'
Test-Path -LiteralPath $key -PathType Leaf
```

If the directory object cannot be found, then fall back to a bounded recursive
search that tolerates access-denied directories:

```powershell
$key = (Get-ChildItem -LiteralPath "$env:USERPROFILE\Documents" -Recurse -Filter 'ssh-key-2026-05-25.key' -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
Test-Path -LiteralPath $key -PathType Leaf
```

If the directory exists but the sandbox reports access denied, do not keep
trying path spellings. Request one escalated operation for the actual `scp` or
`ssh` command and state that the key contents will not be printed.

When troubleshooting key permissions, inspect metadata only:

```powershell
Get-Item -LiteralPath $key | Format-List FullName,Length,LastWriteTime
```

Use `$key` as the identity file:

```powershell
ssh -i $key ubuntu@140.245.69.242 date
```

Run multiple VPS checks serially when they all need the same Windows private
key. One clean SSH command that returns `date`, `systemctl`, or `ps` is more
useful than several parallel checks that race on key access and make the server
look broken.

If `ssh` reports `Connection timed out during banner exchange` after a previous
successful command, treat it as a connection-handshake or server-load symptom,
not a key-discovery problem. Remove only the local stuck SSH processes from the
timed-out attempts, wait about a minute, then retry a short command:

```powershell
Get-Process | Where-Object { $_.ProcessName -like '*ssh*' } |
  Select-Object Id,ProcessName,StartTime

Stop-Process -Id <timed-out-ssh-process-id> -Force
Start-Sleep -Seconds 75
ssh -T -o BatchMode=yes -o ConnectTimeout=10 -i $key ubuntu@140.245.69.242 date
```

## 4. What To Check Next Time

- Confirm the path is a file before applying key-file permissions.
- Never open, print, copy, or commit private key contents.
- Prefer object-based discovery of the Oracle SSH directory under `Documents`.
  Do not hand-type mojibaked Korean path text from logs.
- Use `Get-ChildItem -ErrorAction SilentlyContinue` only as a fallback when the
  directory object lookup fails.
- If sandbox permissions block the key directory, request a single escalated
  `scp` or `ssh` command instead of repeating failed local path probes.
- Validate access with a harmless remote command before doing operational work.
- Avoid parallel SSH probes that reuse the same local identity file.
- After `banner exchange` timeouts, do not keep rediscovering the key. Clean up
  local timed-out SSH processes, wait briefly, and retry one bounded command.

## 5. Project-Specific Caution

The active Oracle VPS is `ubuntu@140.245.69.242`. Treat the private key as a
secret. Documentation should identify the key by filename and safe search
method, not by printing key contents or relying on fragile path text.
