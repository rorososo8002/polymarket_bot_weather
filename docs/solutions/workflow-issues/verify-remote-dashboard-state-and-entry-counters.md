# Verify Remote Dashboard State And Entry Counters

## 1. What Went Wrong

The dashboard displayed a large `Entry Signals` count. That number came from
cumulative decision rows whose side was `YES` or `NO`, not from actual `OPEN`
trades. After the last open trade, every valid `YES` or `NO` decision referred
to an already-held market, so the runner correctly skipped duplicate openings.

## 2. Why It Mattered

An entry signal is not the same thing as a new position. It means the strategy
found a direction that looked valid at the decision stage. The broker can still
skip it because exposure limits, duplicate-position checks, or market constraints
block a new paper position.

Without this distinction, the dashboard can look like the bot is failing to act
when it is actually enforcing risk rules.

## 3. How It Was Fixed

Dashboard review now compares three layers:

- Decision rows: what the strategy wanted to do.
- Trade rows: what the paper broker actually opened or closed.
- Current state: what positions remain open now.

The explanation of counters was updated so `Entry Signals` is interpreted as a
decision-stage count, not as the number of newly opened trades.

## 4. What To Check Next Time

- Inspect decision logs and trade logs separately.
- For a suspicious counter, ask which file or state object feeds it.
- Check duplicate-position skip reasons before assuming a missed entry bug.
- Compare local dashboard assumptions with the current remote runtime state.

## 5. Project-Specific Caution

Expected-net-return and later filters can produce many valid strategy signals
that do not become positions. This is expected when paper risk controls are
working. Report the difference between "signal", "order intent", and "opened
position" clearly.
