---
title: Recheck active settlement runners before holding them again
date: 2026-06-02
category: logic-errors
module: weather_bot.paper
problem_type: logic_error
component: service_object
symptoms:
  - "A settlement runner could be treated as an unconditional hold after the first principal-recovery sale."
  - "Fresh settlement expected value could become worse than fee-adjusted sell-now value without closing the runner."
  - "A test that only checks initial runner creation would miss later runner deterioration."
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [polymarket, settlement-runner, exit-policy, paper-trading, tranche-logs]
---

# Recheck active settlement runners before holding them again

## Problem

Phase 6 added a useful rule: when a cheap favorable YES or NO position reaches a profit target, sell enough to recover principal and keep a bounded runner for settlement if settlement value is still better than selling now.

The easy mistake is to think `settlement_runner_active=True` means "never touch this again until settlement." That is too strong. It should mean "do not keep taking ordinary profit from this runner," not "ignore fresh evidence that settlement is now worse than selling."

## Symptoms

- The initial runner test can pass: the broker sells a principal-recovery tranche and leaves the bounded runner.
- A later cycle can still have a profit signal, but the model probability has weakened enough that conservative settlement expected value is now below the fee-adjusted sell-now value.
- If the active-runner branch returns early without rechecking that comparison, the bot keeps a runner it should close.

## What Didn't Work

- Only testing runner creation was not enough. That test proves the first split works, but it does not prove an already-active runner is still safe to hold later.
- Treating "runner active" as a blanket bypass for model-target and overheated exits was too broad. It protected the runner from repeated trimming, but also protected it from valid settlement-risk reduction.

## Solution

Keep the branch order explicit:

1. Probability stop, valid edge fade, max-hold, and settlement resolution remain real exits.
2. For profit exits, calculate a settlement-runner decision from fresh probability and current executable bid.
3. If the runner is already active and the fresh decision still says settlement is at least as good, log `HOLD_RUNNER` and keep it.
4. If the fresh decision says settlement is now worse, fall through to the normal close path with a reason that includes `settlement runner blocked`.

The regression test shape is:

```python
def test_active_runner_closes_when_settlement_value_turns_unfavorable(tmp_path):
    pos = PaperPosition(
        side="YES",
        entry_price=0.20,
        shares=25.0,
        cost_usd=5.0,
        metadata={
            "entry_p_true": 0.70,
            "probability_stop_threshold": 0.60,
            "settlement_runner_active": True,
        },
    )
    latest_edges = {("m1", "YES"): EdgeResult("YES", 0.65, 0.80, -0.05, 0.0, 0.0, "latest")}

    messages = maybe_close_positions(broker, client, {"m1": market}, latest_edges)

    assert any("settlement runner blocked" in msg for msg in messages)
    assert broker.state.positions == []
```

In plain words: if the runner can sell around `0.80` today but the conservative settlement value has fallen to `0.65`, it is no longer a runner worth holding. Sell it.

## Why This Works

`settlement_runner_active` is a state flag, not a risk exemption.

It prevents repeated profit-taking from gradually shaving a runner to dust. But the original Phase 6 contract still depends on a comparison:

```text
hold-to-settlement expected value >= sell-now value after fee
```

That comparison can change. The bot must repeat it with fresh probability before deciding to hold the active runner again.

This also keeps the beginner mental model clean:

- "Take profit" asks: should we sell because price reached our target?
- "Runner" answers: maybe sell only principal and hold a capped remainder.
- "Settlement risk" asks again later: is the capped remainder still worth holding?

Those are three different questions. Do not collapse them into one boolean.

## Prevention

- When adding a state flag that skips an action, add a test for the later cycle where the flag is already set.
- Name the flag narrowly in your head: `settlement_runner_active` means "active runner tranche exists," not "all future profit exits are impossible."
- Keep blocked-runner reasons in the paper log. The phrase `settlement runner blocked` is useful because tests and operators can see why a runner was not held.
- Test both sides of a strategic partial-exit rule:
  - create runner when settlement expected value is better than sell-now value
  - close or reduce runner when settlement expected value becomes worse
- For low-liquidity paths, inspect the fallback branch directly. A single misplaced assignment or commented-out line can hide until a partial-fill test exercises it.

## Related Issues

- [Correlated event budgets need a broker backstop](./correlated-event-budget-needs-broker-backstop.md)
- [Invalid edge sentinel is not an exit signal](./invalid-edge-sentinel-not-exit-signal.md)
- [Use same-station nowcast pilots before expanding weather observations](./use-same-station-nowcast-pilots.md)
- `src/weather_bot/paper.py`
- `tests/test_hardening.py`
