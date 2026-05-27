---
title: Invalid edge sentinel must not trigger edge-faded exits
date: 2026-05-28
category: logic-errors
module: exit policy
problem_type: logic_error
component: service_object
symptoms:
  - "A Seoul NO paper position closed after about 12 minutes for roughly 0.1% instead of waiting for its model target"
  - "The close reason was edge faded: latest_edge=-999.0000, then the same market reopened one second later with strong NO edge"
root_cause: logic_error
resolution_type: code_fix
severity: high
tags: [exit-policy, edge-fade, paper-trading, sentinel, churn]
---

# Invalid edge sentinel must not trigger edge-faded exits

## Problem

The paper bot treated `net_edge=-999` as if it were a real negative edge during
exit evaluation. That caused a valid position to close early when the current
order-book update could not produce an executable edge, then reopen immediately
when the next valid update restored the same trade thesis.

## Symptoms

- A trade closes with `edge faded: latest_edge=-999.0000`.
- The realized gain or loss is tiny and unrelated to the model target.
- The same market can reopen seconds later with a strong `DECISION YES` or
  `DECISION NO`.
- Decision logs around the close contain `DECISION SKIP` with `No valid side
  evaluated.` or other liquidity/evaluation failure reasons.

## What Didn't Work

- Interpreting dashboard "win" count alone was misleading. The closed Seoul
  trade showed one win, but it was only about `$0.07` on a `$50` position and was
  not a planned take-profit.
- Looking only at the trade row hid the distinction between a real edge fade and
  an evaluation sentinel. The decision CSV and exit code had to be compared.

## Solution

Require an executable latest edge before using the edge-faded exit rule:

```python
if (
    latest_edge is not None
    and latest_edge.p_exec is not None
    and latest_edge.net_edge <= settings.exit_net_edge
    and pnl_pct >= -settings.edge_fade_max_loss_pct
):
    ...
```

The regression test is `test_invalid_edge_sentinel_does_not_trigger_edge_fade_exit`.
It creates a held NO position, passes an invalid `EdgeResult("SKIP", ..., None,
-999.0, ...)`, and verifies the position is held instead of closed.

## Why This Works

`net_edge=-999` is a sentinel for "could not evaluate this side", not a measured
expected-value estimate. A real edge-faded exit needs a current executable price
for the held side. Checking `p_exec is not None` keeps real negative edges
actionable while ignoring transient invalid book states.

## Prevention

- Treat sentinel values as control-flow states, not numeric trading signals.
- For every exit condition, document whether it needs model probability, current
  executable order-book price, or both.
- When a trade closes unexpectedly, inspect the close reason, matching decision
  rows, and the position metadata before changing thresholds.
- Keep production docs as an executable contract that explains which decision
  events are trade signals, which are skip diagnostics, and which values are
  invalid sentinels.

## Related Issues

- [Probability stop replaces fixed price stop](../workflow-issues/probability-stop-replaces-fixed-price-stop-2026-05-26.md)
- [Realtime orderbook requirement not polling](../workflow-issues/realtime-orderbook-requirement-not-polling-2026-05-26.md)
