# Verify SSH Key File Before Tightening Permissions

## 1. What Went Wrong

An SSH permission fix targeted the Oracle SSH directory instead of the private
key file named `ssh-key-2026-05-25.key`. The directory and the key file are not
the same thing, and `ssh -i` needs the private key file.

## 2. Why It Mattered

Tightening permissions on the wrong path can leave SSH still failing while
making the investigation look like a permissions issue. It also increases the
risk of accidentally printing or opening key material while troubleshooting.

## 3. How It Was Fixed

First locate the key file without reading its contents:

```powershell
$key = (Get-ChildItem -LiteralPath 'C:\Users\wpdla\Documents' -Recurse -Filter 'ssh-key-2026-05-25.key' | Select-Object -First 1).FullName
```

Then inspect metadata only:

```powershell
Get-Item -LiteralPath $key | Format-List FullName,Length,LastWriteTime
```

Use `$key` as the identity file:

```powershell
ssh -i $key ubuntu@140.245.69.242 date
```

## 4. What To Check Next Time

- Confirm the path is a file before applying key-file permissions.
- Never open, print, copy, or commit private key contents.
- Use `Get-ChildItem` to locate the key by filename when the directory name may
  contain non-ASCII characters.
- Validate access with a harmless remote command before doing operational work.

## 5. Project-Specific Caution

The active Oracle VPS is `ubuntu@140.245.69.242`. Treat the private key as a
secret. Documentation should identify the key by filename and safe search
method, not by printing key contents or relying on fragile path text.
