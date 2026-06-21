---
title: Historical weather profiles must honor QC provenance and local-day boundaries
date: 2026-06-21
category: logic-errors
module: weather_bot.residual_profile_builder
problem_type: logic_error
component: tooling
symptoms:
  - "Valid U.S. NCEI temperatures were discarded, making populated stations look statistically thin."
  - "UTC calendar-year files could leave the first or last station-local day incomplete."
  - "A nearby Seoul archive identifier could be mistaken for the RKSI settlement station."
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags: [ncei, global-hourly, quality-code, timezone, residual-profile, station-mapping]
---

# Historical weather profiles must honor QC provenance and local-day boundaries

## Problem

An official archive can still produce a false calibration result when its quality
flags, station identifiers, or UTC boundaries are interpreted too narrowly.
During residual-profile generation, quality code `5` was rejected even though
NCEI defines it as passing all quality-control checks for an NCEI-origin value.
This discarded most KDAL, KBKF, and KHOU observations.

## Symptoms

- Large annual CSV files produced only a handful of accepted observations.
- Stations with years of data were labeled `insufficient_history`.
- A UTC year boundary could create a partial station-local day and distort its
  finalized high or low.
- The NCEI identifier `47111099999` described Seoul AB/RKSM, not the required
  Incheon/RKSI station `47113199999`.

## What Didn't Work

- Whitelisting only quality code `1` looked conservative, but ignored that code
  `5` has the same passed-all-checks status with different source provenance.
- Downloading exactly January 1 through December 31 in UTC did not prove that
  the first and last station-local days were complete.
- Matching a city name or nearby station was not a substitute for exact ICAO
  and catalog-period validation.

## Solution

Accept only the two automatically quality-controlled temperature codes needed
by the builder:

```python
VALID_TMP_QUALITY_CODES = frozenset({"1", "5"})
```

Keep suspect, erroneous, merely gross-limit-checked, missing, and manually
edited codes excluded unless a later audited policy explicitly allows them.

For multi-year station-local profiles, include boundary observations from the
UTC day before the first year and the UTC day after the last year. Record those
files, hashes, and accepted-row counts in the manifest.

Resolve each station through the official ISD history catalog and require one
exact ICAO/NCEI mapping that covers the calibration period. Unsupported or
ambiguous mappings remain disabled.

## Why This Works

NCEI quality codes encode both validation status and source provenance. Codes
`1` and `5` both passed all quality-control checks, while their source differs.
Using both retains valid observations without admitting suspect values.

Boundary UTC observations let the builder assign complete days after timezone
conversion. Exact station and catalog-period checks prevent a plausible nearby
archive from silently becoming settlement evidence.

## Prevention

- Test every accepted quality code against the official format definition.
- Compare annual file size with accepted-row count; a large file yielding only
  a few rows is a parser warning.
- Add station-local boundary buffers before aggregating complete years.
- Store source URL, file hash, station mapping, record period, and accepted-row
  count in the profile manifest.
- Pin known-confusable mappings such as RKSI versus RKSM in coverage tests.

## Related Issues

- [Local-yesterday nowcast post-close window](./local-yesterday-nowcast-post-close-window.md)
- [Avoid full decision-log scans in runtime readers](../performance-issues/dashboard-large-decision-log-initial-scan.md)
