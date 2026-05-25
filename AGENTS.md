# AGENTS.md

## Local Codex Workflow Notes

- When Superpowers review workflows finish, run a compound learning check before final completion.
- Invoke `ce-compound` for non-trivial execution/review-cycle mistakes, repeated review findings, debugging lessons, workflow corrections, or prevention rules.
- Capture durable lessons under `docs/solutions/` so future runs do not repeat the same mistake.
- Skip `ce-compound` only when there is no durable learning to capture, and mention that briefly in the final response.

## Production Bot Guardrails

- Trade only cities listed in `src/weather_bot/stations.py`.
- Treat `STATION_MAP` as the single source of truth for Polymarket weather settlement stations.
- If a Polymarket weather market city is not in `STATION_MAP`, skip discovery/trading.
- Refresh forecast data through the Open-Meteo cache no more often than every 30 minutes by default.
- Monitor Polymarket order books through the CLOB WebSocket market stream by default.
- Do not add private keys or live-wallet execution without an explicit user request.
- Keep paper-trading behavior intact unless the user explicitly asks for live execution.
- If code and production docs disagree, update the docs or record the drift before continuing.

## Karpathy-Style Work Rules

The linked CLAUDE.md is a compact public guideline document. Do not copy it verbatim into this repo; use this local 65-line rewrite instead.

01. Think before coding.
02. State assumptions before acting.
03. Ask when uncertainty changes the solution.
04. Prefer the smallest design that works.
05. Do not add speculative features.
06. Avoid abstractions with one caller.
07. Keep edits tied to the user request.
08. Match the local code style.
09. Touch only files needed for the task.
10. Remove only dead code created by your change.
11. If 200 lines can be 50, rewrite it.
12. Senior code is usually shorter.
13. Tests define success.
14. Write the failing test first for behavior changes.
15. Watch the test fail for the right reason.
16. Implement the minimum green path.
17. Refactor only after green.
18. Run the focused tests before broad tests.
19. Verify with real commands, not vibes.
20. Report test gaps honestly.
21. Keep state handoffs explicit.
22. Record production decisions as they happen.
23. Do not hide confusing tradeoffs.
24. Push back on unsafe requirements.
25. Prefer deterministic behavior over cleverness.
26. Name functions by what they promise.
27. Keep functions small enough to scan.
28. Keep modules focused on one responsibility.
29. Avoid global behavior changes by accident.
30. Preserve user changes in the worktree.
31. Never reset or revert unrelated work.
32. Use structured parsers over brittle strings.
33. Keep configuration boring and documented.
34. Defaults should be safe for production.
35. External data assumptions need source notes.
36. Trading code must fail closed.
37. Unknown markets are skips, not guesses.
38. Unknown stations are skips, not city centroids.
39. Rate limits should degrade gracefully.
40. Cache external forecasts intentionally.
41. Stream market prices separately from slow forecasts.
42. Prefer paper validation before real money.
43. Risk controls are part of the feature.
44. Logs should explain skipped trades.
45. Snapshots should preserve raw evidence.
46. Do not bury important state in comments only.
47. Code comments should explain why, not what.
48. Keep docs aligned with code.
49. A handoff doc should unblock the next agent.
50. Write progress as facts, not hopes.
51. Mark incomplete work clearly.
52. Commit only coherent change sets.
53. Stage intentionally.
54. Review diffs before committing.
55. Use concise commit messages.
56. Push only after tests pass or gaps are stated.
57. Avoid broad dependency changes.
58. Prefer standard library tools where enough.
59. Do not chase unrelated cleanup.
60. If a shortcut creates future risk, document it.
61. Make failure modes observable.
62. Treat production config as part of the system.
63. Keep security boundaries visible.
64. Make the next safe step obvious.
65. Leave the repo easier to continue than you found it.
