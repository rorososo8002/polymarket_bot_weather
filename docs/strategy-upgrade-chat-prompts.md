# Strategy Upgrade Chat Prompts

Copy one prompt into a fresh chat for each phase. Replace phase numbers only
when the roadmap says to do so.

## Phase 0

```text
Read `AGENTS.md` and `docs/strategy-upgrade-roadmap.md` first.
In this chat, complete only Phase 0. Never delete or revert changes already
present in the working folder. Even if you do not know who made them, inspect
and preserve them first.

Check `git status` and the current diff. Verify that the existing WebSocket
open-position subscription retention change and SKIP diagnostic improvement are
consistent with each other.

Run the relevant focused tests, then run the full pytest suite. If code and docs
disagree, update only the necessary docs. If there is durable learning, record it
under `docs/solutions/`.

Do not auto-deploy or auto-commit in this chat. At the end, report changed
files, test results, remaining risks, whether it is safe to commit, and the
handoff for Phase 1 in beginner-friendly language.
```

## Phase 1

```text
Read `AGENTS.md` and `docs/strategy-upgrade-roadmap.md` first.
Review the Phase 0 completion handoff, then complete only Phase 1 in this chat.

Fix the problem where the dashboard can look healthy even when recent
Open-Meteo forecast time has stopped moving, and where the main service can look
healthy even if the WebSocket thread has died.

Apply TTL to the in-memory forecast cache. Show last forecast attempt time, last
success time, recent failure reason, cache age, and stale status in the status
JSON and dashboard. For WebSocket, track receiver-thread liveness, reconnect
count, last message time, and stale order-book age.

Add tests first, then run focused tests and the full pytest suite. If you need
to inspect the current VPS state, use read-only SSH commands only. Use the Oracle
SSH path documented in `AGENTS.md` and never open or print key contents.

If deployment is needed after local verification, do not deploy automatically.
Explain the change, risk, verification method, and rollback method, then wait
for my approval. End with the Phase 2 handoff.
```

## Phase 2

```text
Read `AGENTS.md` and `docs/strategy-upgrade-roadmap.md` first.
Review the previous phase completion handoff, then complete only Phase 2 in this
chat.

Block trades where the return is too thin after fees, spread, and slippage, such
as entering near 0.88 and exiting near 0.92. Re-check the official Polymarket
fee documentation and use a testable real fee calculation function instead of a
fixed estimate.

Add an expected net-return filter before entry, separate from the existing
`net_edge` condition. Use 6% as the default hypothesis. Do not ban high-price
entries categorically; still allow evaluation when conservative settlement-hold
math leaves enough return.

Add tests first to show that 0.88 -> 0.92 is rejected and sufficiently favorable
trades pass. Add expected gross return, costs, net return, and rejection reason
to the decision log. Run focused tests and the full pytest suite, update
production docs, do not auto-deploy, and end with the Phase 3 handoff.
```

## Phase 3

```text
Read `AGENTS.md` and `docs/strategy-upgrade-roadmap.md` first.
Review the previous phase completion handoff, then complete only Phase 3 in this
chat.

Do not assume weather events are simple threshold markets. Treat them as real
temperature interval events. Parse intervals such as exactly 26 degrees, 18 or
below, and 28 or above. Compute internally consistent probabilities from the
ensemble distribution.

Group markets by city and date event during discovery. Verify the old assumption
that `MAX_MARKETS=41` is enough to cover 41 cities. If the bot was actually
counting binary submarkets, change discovery to event-based exploration.

Add parser, probability, discovery, and runner tests first. Run focused tests
and the full pytest suite. Update stale production-doc explanations, do not
auto-deploy, and end with the Phase 4 handoff.
```

## Phase 4

```text
Read `AGENTS.md` and `docs/strategy-upgrade-roadmap.md` first.
Review the previous phase completion handoff, then complete only Phase 4 in this
chat.

Add city+date event-level portfolio selection so one Seoul position does not
block every other favorable interval opportunity for the same date.

Because same-city same-date bets are strongly correlated, do not multiply limits
as if they are independent trades. In the initial paper phase, keep the
conservative city+date total exposure limit and let selected legs share that
budget. Allow complementary combinations only when cost-adjusted portfolio EV
improves. Block opposite positions in the same market and excessive same-side
concentration.

Add tests first, run focused tests and the full pytest suite, add event-level
decision-log and dashboard explanations as needed, and update production docs.
If you think exposure limits should be increased, do not change them silently in
code. First explain why resolved paper-trade evidence is strong enough. Do not
auto-deploy, and end with the Phase 5 handoff.
```

## Phase 5

```text
Read `AGENTS.md` and `docs/strategy-upgrade-roadmap.md` first.
Review the previous phase completion handoff, then complete only Phase 5 in this
chat.

Design and implement same-day nowcast support for the official settlement
station's current high temperature. First research the official settlement rules,
observation data source, and update cadence. Do not substitute city-center
weather or guessed values.

If reliable observation sources exist for only some stations, do not force all
41 cities into support. Start with a verified small pilot.

Record observed high temperature, observation time, source, freshness, and
unavailable reason. If observations are missing, stale, or unverified, SKIP the
nowcast-dependent logic. Add fixture-based provider tests first and verify fresh,
stale, malformed, and unavailable cases. Run focused tests and the full pytest
suite, update docs, do not auto-deploy, and end with the Phase 6 handoff.
```

## Phase 6

```text
Read `AGENTS.md` and `docs/strategy-upgrade-roadmap.md` first.
Review the previous phase completion handoff, then complete only Phase 6 in this
chat.

Add strategic partial exits so favorable low-entry YES or NO positions are not
fully closed too early. Compare the fee-adjusted amount available from selling
now with conservative settlement EV. When appropriate, sell part of the position
to recover principal and keep a limited runner quantity until settlement.

Keep existing probability-deterioration stops, invalid-sentinel defenses,
maximum holding time, liquidity limits, and observation-risk safeguards.

Add tests first for principal recovery, runner retention, probability
deterioration, settlement risk, and low-liquidity cases. Record tranche-level
decisions in paper logs, run focused tests and the full pytest suite, update
docs, do not auto-deploy, and end with the Phase 7 handoff.
```

## Phase 7

```text
Read `AGENTS.md` and `docs/strategy-upgrade-roadmap.md` first.
Review the previous phase completion handoff, then complete only Phase 7 in this
chat.

Do not automatically follow whale traders or external strategies. Keep them
separated as shadow research. Check the latest official Polymarket API docs and
use only public data to build a bounded research structure that can collect
weather-market wallet activity, market, direction, price, timestamp, and later
outcome.

You may research Twitter or public posts, but separate evidence from inference.
Do not add automatic copy trading, live trading, or private-information
collection. Build a report comparing our bot's paper decisions with the timing
and outcomes of external signals, then conclude whether the signal deserves
promotion to a future paper-only experiment.

Update required tests and docs, run the full pytest suite, and finish with a
summary of the whole roadmap plus the items that must remain experimental.
```
