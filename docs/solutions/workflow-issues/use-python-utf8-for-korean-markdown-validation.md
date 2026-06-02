# Use Python UTF-8 Mode For Markdown Validation

## 1. What Went Wrong

Python-based Markdown validation on Windows failed with `UnicodeDecodeError`
when documents contained non-ASCII text.

## 2. Why It Mattered

The files were valid UTF-8, but the Windows Python process tried to read them
with the default `cp949` encoding. The document was not broken; the read mode was
wrong.

## 3. How It Was Fixed

Run Python with UTF-8 mode when validating Markdown that may contain non-ASCII
text:

```powershell
python -X utf8 -m pytest -q
```

## 4. What To Check Next Time

- If Markdown validation fails with a `cp949` decode error, do not assume the
  file content is corrupt.
- Retry the same command with `python -X utf8`.
- If UTF-8 mode still fails, then inspect the actual document format.

## 5. Project-Specific Caution

The repository now prefers English-only tracked text, but file paths, user
environments, or external data can still contain non-ASCII. Use UTF-8 mode for
Windows document tooling when text encoding is part of the failure.
