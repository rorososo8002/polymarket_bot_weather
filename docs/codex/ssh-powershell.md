# Windows PowerShell SSH Rules

Read this file only when issuing SSH or SCP commands from Windows PowerShell.

- Start with the verified first commands in `docs/codex/known-good-commands.md`.
  If they fail, inspect the concrete error before trying a different shape.
- For the active Oracle VPS, use `ubuntu@140.245.69.242` with the private key
  named `ssh-key-2026-05-25.key` in the Oracle SSH directory under
  `C:\Users\wpdla\Documents`.
- Do not use the adjacent `.pub` file with `ssh -i`. Never print, open, copy, or
  commit the private key contents.
- Treat nested quoting from Windows PowerShell into `ssh` as fragile by default.
- Do not start with complex inline remote snippets such as `ssh ... "python3 -c '...'"`, especially when they contain quotes, parentheses, braces, semicolons, CSV headers, JSON, or shell substitutions.
- Prefer simple remote commands first: `systemctl`, `cat`, `tail`, `head`, `stat`, `ls`, and `date` with minimal quoting.
- Prefer serial SSH checks when they reuse the same Windows private key. Parallel
  probes can produce intermittent `Identity file ... not accessible:
  Permission denied` errors in managed execution environments.
- For CSV, JSON, or log analysis, pull a bounded amount of output into a local PowerShell variable and parse locally. For example, use `tail -n 2000 remote.csv | ConvertFrom-Csv` instead of complex remote `python -c`.
- For `journalctl` over SSH, avoid relative time arguments containing spaces. Use forms such as `--since=-30min`, `--since=-2h`, or an ISO timestamp without shell-ambiguous quoting.
- Dashboard request logs may contain `DASHBOARD_TOKEN` in URLs. Aggregate counts or redact `token=...` before printing log lines.
- If remote computation is truly needed, test the command shape with a tiny harmless command first. Then use a temporary script file or an existing checked-in script instead of a long inline command.
- When a PowerShell or SSH command fails with parser or quote-related errors, stop and change approach immediately. Do not keep retrying quote variants.
