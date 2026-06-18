# Current Task

Status: active

## Objective

Finish the one remaining authenticated live-dashboard verification.

## Current Scope

- Dashboard code is deployed at commit `302ac54`.
- Local and remote suites passed with 529 tests.
- External HTML is 200; bare API and query-token API are 403.
- Header-authenticated `/api/status` 200 remains unverified because the tool usage limit blocked the final SSH/SCP check.

## Next Action

When tool access is available, source the existing dashboard environment on the VPS without printing the token, verify header-authenticated `/api/status` returns 200, then set this card to `Status: none`.

## New Chat Prompt

none
