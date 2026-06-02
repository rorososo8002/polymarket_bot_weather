# Use Known-Good Commands Before Troubleshooting

## 1. What Went Wrong

Local pytest and Oracle SSH work had already accumulated known environment
fixes, but later work risked rediscovering those fixes from scratch. For
example, pytest once failed because of Windows temporary-directory permissions
before the project settled on repository-local pytest temp folders.

SSH has the same risk. If the Oracle host and private-key filename are already
known, starting with a different path or quoting pattern wastes time and makes it
harder to distinguish command-shape problems from real server problems.

## 2. Why It Mattered

A prevention document only helps if it is read before the first avoidable
failure. Otherwise the same setup mistake repeats, and every new worker has to
decide which command form to trust.

For a beginner, this is like checking the map only after getting lost. For a
known route, start with the saved route.

## 3. How It Was Fixed

- Local pytest uses repository-local temp folders through the root `conftest.py`.
- Routine commands were collected in `docs/codex/known-good-commands.md`.
- `AGENTS.md` tells workers to read the known-good command doc before local
  pytest or VPS/SSH work.
- SSH verification checks that the key file exists without opening it, then uses
  a harmless command such as `date` to validate the connection.

## 4. What To Check Next Time

- Start routine work with the first matching known-good command.
- If the recorded command fails, inspect the concrete error before inventing
  several variants.
- Classify the first error: permissions, path, network, remote service, quoting,
  or something else.
- If a new command form is truly needed, verify it and update the known-good doc.

## 5. Project-Specific Caution

The private key is only an identity file for `ssh -i` or `scp -i`. Never open or
print its contents. Large runtime logs should be inspected with bounded tails or
summaries. Known-good commands are not workarounds; they encode verified
environment assumptions so tests can reach product code quickly.
