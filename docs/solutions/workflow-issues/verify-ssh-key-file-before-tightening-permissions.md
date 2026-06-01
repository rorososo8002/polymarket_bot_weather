---
title: Verify SSH key paths point to files before tightening permissions
date: 2026-05-28
category: workflow-issues
module: remote-vps-access
problem_type: workflow_issue
component: development_workflow
severity: low
applies_when:
  - "A user provides a local path for an SSH key"
  - "OpenSSH refuses a key with an unprotected private key warning"
  - "A path may be a directory containing key files, not the key file itself"
tags: [ssh, oracle-vps, icacls, private-key, workflow]
---

# Verify SSH key paths point to files before tightening permissions

## Context

During an Oracle VPS check, the provided SSH path was
`C:\Users\wpdla\Documents\오라클ssh`. That path was a directory, not the private
key file. Treating it as the key caused `icacls` to narrow the directory
permissions instead of the actual private key.

The real key file was:

```powershell
C:\Users\wpdla\Documents\오라클ssh\ssh-key-2026-05-25.key
```

## Guidance

Before changing key permissions or passing a user-provided path to `ssh -i`,
verify whether it is a file or a directory:

```powershell
Get-Item -LiteralPath 'C:\Users\wpdla\Documents\오라클ssh' |
  Select-Object FullName,Mode,Length,Attributes
```

If the path is a directory, list file names only and choose the private key file,
not the `.pub` file:

```powershell
Get-ChildItem -LiteralPath 'C:\Users\wpdla\Documents\오라클ssh' -Force |
  Select-Object Name,Mode,Length,LastWriteTime
```

Then restrict only the actual private key file:

```powershell
icacls "C:\Users\wpdla\Documents\오라클ssh\ssh-key-2026-05-25.key" `
  /inheritance:r `
  /grant:r "DESKTOP-FHI6IIU\wpdla:R" `
  /remove:g "Everyone" "Users" "Authenticated Users"
```

Do not print, copy, or commit the private key contents. Use it only as an
identity file:

```powershell
ssh -i "C:\Users\wpdla\Documents\오라클ssh\ssh-key-2026-05-25.key" ubuntu@140.245.69.242
```

After the private key file has been verified, record the canonical key directory
and exact private key path in the repo's `AGENTS.md` and VPS access runbook.
Future chats should reuse that recorded path instead of searching for the key
again. Keep only the path in documentation; never open or store the key contents.

## Why This Matters

OpenSSH rejects private keys that are too broadly readable. On Windows, fixing
that with `icacls` is normal, but applying the permission change to a directory
can temporarily block directory listing and still leave the real key unusable.
Checking `Mode` first makes the next safe step obvious.

## When To Apply

- A user gives a key location as a folder path.
- SSH says `WARNING: UNPROTECTED PRIVATE KEY FILE!`.
- SSH says `Operation not supported on socket` after `ssh -i`.
- Remote VPS access is needed for bot status, deployment, or dashboard checks.

## Related

- [Verify remote dashboard state and entry counters before diagnosing paper entries](./verify-remote-dashboard-state-and-entry-counters.md)
- [Run remote pytest from the application directory](./run-remote-pytest-from-app-cwd.md)
