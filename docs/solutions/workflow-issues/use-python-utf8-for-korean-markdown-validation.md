---
title: Use UTF-8-aware tooling and ASCII tracked prompts
module: workflow
date: 2026-06-07
last_updated: 2026-06-07
problem_type: workflow_issue
component: documentation
severity: medium
tags:
  - "powershell"
  - "utf-8"
  - "korean-text"
  - "markdown"
  - "handoff"
applies_when:
  - "Validating Markdown that may contain non-ASCII text on Windows"
  - "Adding fresh-chat or handoff prompt examples to tracked project docs"
  - "Preparing Korean copy/paste text for the user after editing docs"
symptoms:
  - "Windows PowerShell output shows Korean text as mojibake even when the file bytes may be valid UTF-8"
  - "Tracked Markdown receives user-facing Korean prompt text that could have been stored as ASCII English"
root_cause: missing_workflow_step
resolution_type: documentation_update
---

# Use UTF-8-Aware Tooling And ASCII Tracked Prompts

## 1. What Went Wrong

Python-based Markdown validation on Windows failed with `UnicodeDecodeError`
when documents contained non-ASCII text.

A later handoff cleanup also put Korean copy/paste prompt examples directly into
tracked Markdown. The files could be valid UTF-8, but Windows PowerShell output
can still render the text as mojibake, which creates avoidable churn and makes
the agent waste time rewriting docs.

## 2. Why It Mattered

The files were valid UTF-8, but the Windows Python process tried to read them
with the default `cp949` encoding. The document was not broken; the read mode was
wrong.

For project handoff docs, the bigger problem is workflow design. `AGENTS.md`
requires Korean user-facing replies, not Korean tracked documentation. When an
operational prompt example can be expressed in ASCII English, storing it as
Korean in Markdown adds encoding risk without improving the handoff.

## 3. How It Was Fixed

Run Python with UTF-8 mode when validating Markdown that may contain non-ASCII
text:

```powershell
python -X utf8 -m pytest -q
```

For tracked project docs, prefer ASCII English prompt examples when the same
operational meaning can be preserved. If the user needs Korean copy/paste text,
provide that text in the final chat report instead of committing it into
Markdown.

Bad pattern:

```markdown
Fresh chat prompt: [Korean user-facing copy/paste prompt stored in a tracked doc]
```

Preferred pattern:

```markdown
Fresh chat prompt: Continue the current production task. Read AGENTS.md,
docs/active/current-task.md, and docs/production-decisions.md first.
```

Then provide the Korean version in the final answer to the user.

## 4. What To Check Next Time

- If Markdown validation fails with a `cp949` decode error, do not assume the
  file content is corrupt.
- Retry the same command with `python -X utf8`.
- If UTF-8 mode still fails, then inspect the actual document format.
- Do not add Korean text to tracked Markdown just to give the user a
  copy/paste prompt. Use ASCII English in docs and translate in the final chat
  response.
- Do not trust PowerShell's rendered output as proof that a UTF-8 file is
  corrupted. Check with UTF-8-aware tooling or an encoding-safe editor.
- Before amending a handoff doc, ask whether the Korean text is actually needed
  inside the repository, or whether it belongs only in the user-facing report.

## 5. Project-Specific Caution

The repository now prefers English-only tracked text, but file paths, user
environments, or external data can still contain non-ASCII. Use UTF-8 mode for
Windows document tooling when text encoding is part of the failure.

For this project specifically:

- Keep `AGENTS.md` Korean-answer requirements as chat behavior, not as a reason
  to put Korean examples into every tracked handoff document.
- Keep active handoff docs ASCII unless a file already intentionally contains
  UTF-8 Korean and the change genuinely requires it.
- Put Korean copy/paste instructions for the user in the final response, where
  the user actually reads and copies them.
