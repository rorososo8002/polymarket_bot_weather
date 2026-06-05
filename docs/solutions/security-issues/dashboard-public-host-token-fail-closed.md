---
title: Fail closed when a public dashboard host has no real token
date: 2026-06-03
last_updated: 2026-06-06
category: security-issues
module: weather_bot.dashboard
problem_type: security_issue
component: authentication
symptoms:
  - "Dashboard could bind to 0.0.0.0 with an empty or example token"
  - "Short tokens such as abc or 123456 could pass public-host startup checks"
  - "API polling kept placing the dashboard token in the URL query string"
  - "Public /api/status accepted ?token=... as authentication"
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags: [dashboard, token, public-host, query-token, fail-closed, logging, systemd]
---

# Fail closed when a public dashboard host has no real token

## Problem

The dashboard can be useful on a VPS, but `0.0.0.0` means the server listens on
all network interfaces, not just the local machine. If that public binding is
allowed with an empty, short, or placeholder token, anyone who can reach the
URL, including automated scanners, can try to access the read-only
paper-trading dashboard.

## Symptoms

- `DASHBOARD_HOST=0.0.0.0` with an empty `DASHBOARD_TOKEN` could still reach the
  server-start path.
- Placeholder values such as `placeholder`, `basic`, `default`, or
  `change-me-long-random-token` were not rejected before binding.
- Short or obvious values such as `abc`, `123456`, `changeme`, `secret`,
  `token`, or `password` could be treated as present even though they are not
  meaningful protection for a public URL.
- Browser polling appended `?token=...` to `/api/status`, making token leakage
  more likely if raw request logs were inspected.
- Public `/api/status?token=...` could still authenticate even after browser
  polling moved to the `X-Dashboard-Token` header.

## What Didn't Work

- Relying only on `/api/status` returning `403` is too late. The service has
  already bound to the public host, and the root dashboard page is reachable.
- Keeping a placeholder token in `dashboard.env.example` is unsafe because an
  operator can accidentally copy it into production.

## Solution

Add startup validation before `ThreadingHTTPServer` is created:

```python
if host not in {"127.0.0.1", "localhost"} and token_is_too_short_or_weak(token):
    raise ValueError("DASHBOARD_TOKEN must be set ...")
```

The public-host token check should reject three groups:

- missing tokens
- tokens shorter than 32 characters
- obvious example values such as `changeme`, `placeholder`, `secret`, `token`,
  `password`, `abc`, or `123456`

Then make browser polling use the `X-Dashboard-Token` header instead of a URL
query string. If the operator initially opens `/?token=...`, store the token in
local storage, remove it from the visible URL, and redact `token=` values in
server logs.

For public hosts such as `0.0.0.0` or `::`, also make the server reject query
tokens as an authentication method:

```python
if header_token == token:
    return True
if public_host:
    return False
return query_token == token
```

Localhost and `127.0.0.1` may keep query-token first-load compatibility for
development, but public `/api/status` should accept only the
`X-Dashboard-Token` header.

The env example should leave `DASHBOARD_TOKEN=` empty with a comment explaining
that public hosts refuse to start until a real random token with at least 32
characters is set.

## Why This Works

The safest point to block a bad public dashboard configuration is before the
HTTP server binds. That makes the failure obvious in systemd and prevents a
copied example file from exposing even a read-only operations surface. Header
based API polling also keeps the token out of repeated request URLs, while log
redaction protects accidental first-load query tokens. Rejecting public query
tokens closes the remaining shortcut: even if someone pastes
`/api/status?token=...`, the public API still treats the URL token as invalid
and requires the header.

Length matters because `DASHBOARD_TOKEN` is the API-door password. A value like
`abc` is technically non-empty, but it is not a serious lock on a public host.
Checking length and placeholder words together prevents the most common
copy-paste and "I'll fill it in later" mistakes.

## Prevention

- For public dashboard work, test both startup policy and request logging:
  public host plus empty, short, or placeholder token must raise before server
  binding, a 32+ character random token must pass, and localhost without a
  token remains available for development.
- Test public API authentication separately: bare `/api/status` must return
  `403`, public `/api/status?token=...` must also return `403`, and
  `X-Dashboard-Token` must return `200` with the real token.
- Do not put real dashboard tokens in env examples, docs, command output, final
  answers, or commits.
- Before sharing a public dashboard URL, explain that anyone who knows or finds
  it can try to access it, including automated scanners.

## Related Issues

- [Verify public dashboard API access before sharing the URL](../workflow-issues/verify-public-dashboard-api-before-sharing-url.md)
- [Use no-space journalctl since values and redact dashboard tokens](../workflow-issues/powershell-ssh-journalctl-since-no-space.md)
