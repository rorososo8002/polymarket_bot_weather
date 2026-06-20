---
date: 2026-06-21
topic: station-residual-probability-sizing
status: approved-design
---

# Station Residual Probability Sizing

## Summary

Replace fixed intraday probability guesses with a paper-only strategy that estimates the chance of each temperature bucket from official same-station observations and historically observed remaining temperature movement. Allocate 10%, 25%, or 50% of the paper bankroll when the conservative side probability reaches 80%, 90%, or 95%, while retaining executable-price, fee, liquidity, and tradability gates.

---

## Problem Frame

The current intraday strategy assigns a small set of fixed probabilities and sizes after broad regional clock thresholds. It does not prove that a signal labeled 90% or 97% has historically won at that rate for the settlement station, month, and local time.

Daily temperature markets are mutually exclusive bucket events. Their visible city name is not enough to identify the evidence source: for example, Seoul resolves from the Incheon International Airport station, while Hong Kong uses a different official source and decimal precision. A strategy that floors every 23.4 C observation into a 23 C bucket would therefore trade some markets under the wrong settlement rule.

The user wants concentrated paper positions when official observations make one bucket genuinely likely, including up to 50% of a $1,000 paper bankroll. That concentration makes probability calibration, price discipline, and correlated-event controls essential: a contract bought for $0.93 can lose its entire purchase cost while offering only $0.07 gross upside.

---

## Actors

- A1. Paper strategy runner: observes official station data, estimates probabilities, evaluates prices, and records simulated trades.
- A2. Operator: reviews probability calibration, entries, skips, exposure, and paper performance before approving later strategy changes.
- A3. Official observation provider: supplies settlement-station temperature observations at a provider-defined cadence.
- A4. Polymarket public market and CLOB services: supply market rules, bucket contracts, tradability metadata, and executable order-book depth.

---

## Key Flows

- F1. High-temperature observation entry
  - **Trigger:** A supported station enters its station-local high-formation monitoring window.
  - **Actors:** A1, A3, A4
  - **Steps:** Refresh provider-safe official observations, update the observed high, estimate the distribution of additional warming, calculate conservative probabilities for every event bucket, select the best eligible side, and evaluate its executable price.
  - **Outcome:** One paper entry is placed at the applicable probability tier, or a stable skip reason is recorded.
  - **Covered by:** R1-R17

- F2. Low-temperature observation entry
  - **Trigger:** A supported station enters its station-local low-formation monitoring window.
  - **Actors:** A1, A3, A4
  - **Steps:** Refresh official observations, update the observed low, estimate the distribution of additional cooling, calculate conservative bucket probabilities, select the best eligible side, and evaluate its executable price.
  - **Outcome:** One paper entry is placed at the applicable probability tier, or a stable skip reason is recorded.
  - **Covered by:** R1-R17

- F3. Concentrated paper-risk review
  - **Trigger:** A conservative side probability reaches at least 95% and the candidate passes all entry gates.
  - **Actors:** A1, A2
  - **Steps:** Cap the station-date event at 50% of entry bankroll, prevent competing legs in that event, record the probability evidence and price economics, and expose the position in reports.
  - **Outcome:** The operator can audit why a concentrated position existed and whether similarly labeled signals were calibrated.
  - **Covered by:** R12-R22

---

## Requirements

**Settlement evidence and market interpretation**

- R1. The strategy remains paper-only and must not add wallet, signing, private-key, redemption, claim, or real-order behavior.
- R2. Every candidate must use the explicit settlement station and source named or verified by the market rules, not the display city or a nearby station.
- R3. The strategy must apply the market's settlement precision profile before assigning a bucket. A 23.4 C observation belongs to the 23 C bucket only for a verified whole-degree source-display rule; it must not be floored for decimal-resolution sources such as HKO.
- R4. Unknown, conflicting, stale, unsupported, or insufficient station evidence must block entry rather than produce an assumed probability.
- R5. Probability inputs may use current and historical official observations from the same settlement station. Generic forecast-model predictions must not create an entry signal.

**Probability model**

- R6. High-market probability must be based on the distribution of remaining warming from the observed high at the current station-local time. For an exact bucket `[L, U)`, the model must estimate the chance that the final daily high remains within that interval.
- R7. Low-market probability must use the opposite direction: the distribution of remaining cooling from the observed low. For an exact bucket `[L, U)`, it must estimate the chance that the final daily low remains within that interval.
- R8. Historical comparison data must be conditioned at minimum by settlement station, season or month, local time, market direction, and settlement precision. Recent same-station temperature trend and time since the last new extreme may refine the estimate.
- R9. The probability used for sizing must be a conservative probability that discounts sampling uncertainty and model error. An uncalibrated point estimate must not be labeled or sized as a 95% signal.
- R10. Insufficient historical support must produce a skip or a lower validated tier. It must never be promoted to a larger tier through a default probability.
- R11. Exact, lower-tail, and upper-tail outcomes must each receive probabilities consistent with their actual wording, and probabilities across one mutually exclusive event must be checked for coherence.

**Monitoring windows and refresh cadence**

- R12. High monitoring may begin at 14:30 station-local time as the default candidate window for supported summer profiles, but the effective window must be station-and-season aware rather than globally fixed.
- R13. Low monitoring must begin before the station-and-season's historically observed low-formation window. A broad fallback window may be used only when it is explicitly conservative and validated for that station.
- R14. Official observation requests must respect provider floors. AWC METAR may make no more than one real request per minute, and HKO must retain its protected ten-minute request floor unless the provider contract changes.
- R15. Order books remain continuously observed through the existing market stream, but probability recalculation should occur when new official evidence arrives or when a validated time boundary changes the residual distribution.

**Sizing and selection**

- R16. The requested paper-entry cost must follow the conservative selected-side probability:
  - below 80%: no entry;
  - 80% to below 90%: 10% of entry bankroll;
  - 90% to below 95%: 25% of entry bankroll;
  - 95% or above: 50% of entry bankroll.
- R17. The 50% tier is an actual station-date event cap, not merely a signal request that is reduced to the ordinary 5% city-date cap. It may bypass the ordinary city and event-date caps only for the single selected strong candidate, while the 90% total-exposure cap and cash cap remain mandatory.
- R18. A station-date event with a 50% tier position must not open another competing bucket or side. Existing exposure in that event counts toward the 50% maximum rather than allowing an additional 50% purchase.
- R19. When multiple buckets or sides qualify, select the candidate with the best conservative fee-aware expected value, subject to mutually exclusive event coherence. Do not split concentration across several merely similar candidates.
- R20. A probability tier does not override price discipline. Final ask-side executable VWAP, fees, spread, slippage, minimum net edge, minimum expected net return, final tradability, and executable exit-side evidence must all pass before entry.
- R21. Partial ask liquidity must scale the entry down to executable size; it must not create a fictitious 10%, 25%, or 50% fill.
- R22. Existing drawdown breakers, city cooldowns, maximum single-market fraction, and the 90% total-exposure cap remain active except for the explicit strong-signal city/event-cap override in R17.

**Audit and validation**

- R23. Every decision must record the station, local timestamp, current extreme, target bucket, residual distribution context, raw probability, conservative probability, tier, requested size, executable size, price, fees, expected return, and entry or skip reason.
- R24. Reports must separate 10%, 25%, and 50% tier performance and show predicted-versus-realized calibration for high, low, station, month or season, bucket type, and settlement precision confidence.
- R25. A 50% tier must remain paper-only and disabled for any station/profile whose out-of-sample calibration has not demonstrated that the conservative probability method is reliable enough to issue a 95% lower-bound estimate.
- R26. Old state and ledger rows without the new probability evidence must remain readable, but they must not be silently treated as calibrated observations.

---

## Acceptance Examples

- AE1. **Covers R2, R3, R6, R16.** Given the Seoul event resolves from RKSI with verified whole-degree Celsius display precision, the observed high is 23.4 C, and the conservative probability of a final high in `[23.0, 24.0)` is 96%, the 23 C YES candidate requests 50% of entry bankroll before price and exposure gates.
- AE2. **Covers R3, R4.** Given an HKO market reports 23.4 C with one-decimal settlement precision, the strategy does not convert that observation into a verified whole-degree 23 C bucket signal.
- AE3. **Covers R9, R10, R16.** Given a raw estimate of 96% but a conservative uncertainty-adjusted estimate of 94.4%, the candidate requests 25%, not 50%.
- AE4. **Covers R16, R17, R18.** Given a $1,000 entry bankroll, a 96% conservative candidate, no existing station-date exposure, and all gates passing, the station-date event may request up to $500 and cannot add a second competing event leg.
- AE5. **Covers R18.** Given $200 is already exposed in the same station-date event and a new 50% tier candidate qualifies, no more than $300 additional cost may be requested.
- AE6. **Covers R20.** Given a 95% conservative probability and a $0.93 executable ask, if fees and slippage reduce expected net return below the configured minimum, the strategy records a price-economics skip instead of buying 50%.
- AE7. **Covers R7, R13, R16.** Given the observed low is 8.4 C and the conservative probability that the final low stays in `[8.0, 9.0)` is 97% during the validated low-formation window, the 8 C YES candidate requests 50% before price and exposure gates.
- AE8. **Covers R6, R11.** Given the observed high reaches 24.0 C, the exact 23 C YES probability becomes zero; the strategy may evaluate 23 C NO separately but still requires executable price and expected-return gates.
- AE9. **Covers R14, R15.** Given the runner checks every second but the station is AWC METAR-backed, cached evidence may be reused while no more than one real provider request occurs per minute; order-book monitoring continues independently.
- AE10. **Covers R21.** Given a valid $500 request but only $180 of executable ask depth, the paper receipt may show at most $180 of entry cost and must record the liquidity reduction.

---

## Success Criteria

- Probability labels are replayable from stored official evidence and historical residual data; no 80%, 90%, or 95% label is produced solely from a fixed clock rule.
- Out-of-sample reliability reports show how often each probability tier actually won, with sampling uncertainty visible rather than hidden behind a point estimate.
- A $1,000 paper account can place a real $500 simulated entry only for one calibrated 95% station-date candidate that also passes executable-price and economic gates.
- Seoul whole-degree and HKO decimal examples resolve to different precision behavior, preventing silent bucket conversion errors.
- Entry and exit receipts continue to represent executable depth, fees, spread, slippage, partial liquidity, and no-liquidity holds.
- A downstream implementation plan can map every behavior to tests without inventing probability tiers, concentration rules, or failure behavior.

---

## Scope Boundaries

- Real-money trading and all live-trading infrastructure remain outside this work.
- Generic forecast models do not create entry probabilities in this strategy.
- The first version does not require a black-box machine-learning model; a replayable empirical or Bayesian residual model is preferred.
- Fixed 14:30 and fixed pre-dawn clocks are candidate monitoring defaults, not universal claims that every station reaches its extreme at the same time.
- This change does not relax executable-depth, fee, spread, slippage, market-state, stale-data, or settlement-precision protections.
- Historical runtime ledgers are not deleted or rewritten as part of this design; backward readability remains required.

---

## Key Decisions

- Station and season over country: settlement stations, local climates, and source precision differ within a country, so probability profiles must be station-specific.
- Residual movement over fixed probability: the probability comes from how much warmer or colder the station historically became after the current observation time.
- Conservative probability over raw frequency: concentrated sizing requires uncertainty to reduce, never inflate, the tier probability.
- Concentration with exclusivity: a calibrated 95% signal may use 50% of bankroll, but only one station-date candidate may carry that concentration.
- Probability plus price: high correctness probability is necessary but not sufficient when a high-priced contract offers little upside.

---

## Dependencies / Assumptions

- Sufficient timestamped historical observations and finalized daily extremes can be obtained for each enabled settlement station and precision profile.
- Historical data can be aligned to station-local dates without mixing UTC days or nearby stations.
- The current public Gamma/CLOB metadata, WebSocket order-book cache, paper broker, and replayable ledgers remain available as strategy inputs and controls.
- The operator accepts that unsupported or statistically thin stations may trade less often until enough calibration evidence exists.

---

## Outstanding Questions

### Resolve Before Planning

None.

### Deferred to Planning

- [Affects R8-R10][Needs research] Select the simplest conservative residual estimator and minimum-sample rule that can be replayed and calibrated with available station history.
- [Affects R12-R13][Needs research] Determine station-season monitoring windows from historical extreme-occurrence times and define conservative fallbacks.
- [Affects R24-R25][Technical] Define the out-of-sample calibration split and report thresholds that permit a station profile to issue 50% paper signals.
