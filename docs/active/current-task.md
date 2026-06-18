# Current Task

Status: active

## Objective

Deploy and verify the official-station dashboard explanation update on the
Oracle VPS.

## Current Scope

- Local commit `2a71d91` adds Korean skip explanations, full supported
  settlement-station registry display, Seoul KMA ASOS 108 display-only
  reference context, and AWC/HKO provider-floor visibility.
- Local focused tests passed: 156 dashboard/config/nowcast/station/deployment
  tests.
- Local full suite passed: 531 tests.
- Browser verification with installed Chrome passed against a mocked dashboard
  payload: official station registry, KMA Seoul ASOS 108 reference, and Korean
  Chengdu skip explanation rendered with no JavaScript errors.
- Deployment is blocked because the sandbox denied SSH-key access, and the
  required escalated SSH approval was rejected due to an authentication refresh
  problem in Codex.

## Next Action

After Codex account/tool approval is available again, deploy commit `2a71d91`
to `/opt/polymarket-weather-bot`, update the VPS nowcast TTL env to 60 seconds
if the active env still says 300, run remote pytest once, restart
`polymarket-weather-bot` and `polymarket-weather-dashboard`, then verify
dashboard HTML 200, bare `/api/status` 403, query-token `/api/status` 403, and
header-authenticated `/api/status` 200 without printing the dashboard token.

## New Chat Prompt

Continue from local commit `2a71d91`: deploy and verify the official-station
dashboard explanation update on the Oracle VPS. Do not print SSH key contents
or dashboard tokens.
