# AGENTS.md

## Local Codex Workflow Notes

- When Superpowers review workflows finish, run a compound learning check before final completion.
- Invoke `ce-compound` for durable debugging lessons, workflow corrections, repeated review findings, or prevention rules.
- Capture durable lessons under `docs/solutions/`. Skip only when there is no durable learning, and mention that briefly in the final response.
- `docs/solutions/` is a searchable knowledge store organized by category and YAML frontmatter (`module`, `tags`, `problem_type`); it is relevant when implementing, debugging, or making decisions in documented areas.
- Keep this file short. Read situation-specific rules from `docs/codex/` only when the task needs them.

## User Communication Rules

- Always explain to this user in Korean unless the user explicitly asks for another language.
- Assume the user is a development beginner. Explain jargon in plain Korean and connect it to the practical effect.
- Before risky security, money, production, deployment, or configuration changes, explain what changes, why, benefits, risks, access scope, verification, and rollback.
- For public exposure decisions, explain that anyone who knows or finds the URL may access it, including automated scanners.
- Recommend a default option when useful, but separate the recommendation from the facts.
- If the user sounds frustrated, acknowledge the concrete mistake first, then fix the workflow that allowed it.

## Safety And Project Guardrails

- Never print, open, copy, commit, or expose private keys, wallet keys, tokens, or secrets.
- Keep paper-trading behavior intact unless the user explicitly asks for a separate live-execution safety project.
- Trade only cities listed in `src/weather_bot/stations.py`; treat `STATION_MAP` as the single source of truth.
- Unknown markets, unknown stations, missing data, and invalid sentinels such as `-999` are skips, not guesses or exit signals.
- Refresh forecasts through the Open-Meteo cache no more often than every 30 minutes by default.
- Monitor Polymarket order books through the CLOB WebSocket market stream by default. Do not silently replace realtime streaming with polling.
- Keep token IDs for open positions subscribed even when discovery rolls forward to newer markets.
- If code and production docs disagree, update the docs or record the drift before continuing.

## Oracle VPS Access Reference

- The active Oracle VPS is `ubuntu@140.245.69.242`.
- The canonical local SSH key directory is `C:\Users\wpdla\Documents\오라클ssh`.
- Use the private key file `C:\Users\wpdla\Documents\오라클ssh\ssh-key-2026-05-25.key` with `ssh -i` or `scp -i`. Do not use the `.pub` file.
- Never print, open, copy, or commit the key contents. Read `docs/codex/vps-dashboard.md` and `docs/codex/ssh-powershell.md` before VPS work.

## Token-Safe Runtime Rules

- Do not open runtime outputs or data files in full. Use file sizes, counts, tails, filters, summaries, or small samples.
- Treat `runtime/`, `paper_raw_snapshots.jsonl`, `paper_decisions.csv`, `paper_trades.csv`, `forecast_cache.json`, `paper_state.json`, and `paper_runner_status.json` as token-dangerous by default.
- Do not delete local runtime data, `.git/`, or `.antigravitycli/` merely to reduce Codex token usage. Exclude them from normal analysis unless the task needs them.

## Workflow

- Think before coding, state assumptions, and touch only files needed for the task.
- Preserve user changes. Never reset or revert unrelated work.
- Run focused tests before broad tests and report gaps honestly.
- Run git mutations serially.

## Situation-Specific Docs

- `docs/codex/vps-dashboard.md`: VPS, dashboard, systemd, public URL, and health checks.
- `docs/codex/ssh-powershell.md`: Windows PowerShell SSH quoting and remote commands.
- `docs/codex/runtime-data.md`: large logs, paper data, dashboard readers, and safe investigation.
- `docs/codex/strategy-research.md`: strategy, EV, calibration, WebSocket, risk, and trading rules.
- `docs/codex/work-rules.md`: longer engineering rule set.
- `docs/strategy-upgrade-roadmap.md`: ordered paper-strategy upgrade phases and fresh-chat handoffs.
