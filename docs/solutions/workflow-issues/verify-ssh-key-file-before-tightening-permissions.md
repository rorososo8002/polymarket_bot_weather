---
title: Verify SSH key file before tightening permissions
date: 2026-05-26
last_updated: 2026-06-03
category: workflow-issues
module: vps_operations
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - "Checking Oracle VPS access from Windows PowerShell"
  - "The recursive SSH key search hits an access-denied directory"
  - "Several SSH checks need to use the same private key"
tags: [ssh, powershell, vps, oracle, private-key, workflow]
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

## 2. Why It Mattered

Tightening permissions on the wrong path can leave SSH still failing while
making the investigation look like a permissions issue. It also increases the
risk of accidentally printing or opening key material while troubleshooting.

## 3. How It Was Fixed

First locate the key file without reading its contents:

```powershell
$key = 'C:\Users\wpdla\Documents\오라클ssh\ssh-key-2026-05-25.key'
Test-Path -LiteralPath $key
```

If the exact path is not present, fall back to a search that tolerates
access-denied directories:

```powershell
$key = (Get-ChildItem -LiteralPath 'C:\Users\wpdla\Documents' -Recurse -Filter 'ssh-key-2026-05-25.key' -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
Test-Path -LiteralPath $key
```

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

## 4. What To Check Next Time

- Confirm the path is a file before applying key-file permissions.
- Never open, print, copy, or commit private key contents.
- Prefer the known exact Oracle key path when it exists; use `Get-ChildItem
  -ErrorAction SilentlyContinue` only as a fallback when the path is missing.
- Validate access with a harmless remote command before doing operational work.
- Avoid parallel SSH probes that reuse the same local identity file.

## 5. Project-Specific Caution

The active Oracle VPS is `ubuntu@140.245.69.242`. Treat the private key as a
secret. Documentation should identify the key by filename and safe search
method, not by printing key contents or relying on fragile path text.
