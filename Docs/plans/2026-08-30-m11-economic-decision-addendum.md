# M11 Economic-Decision Addendum

Status: registered outcome-blind, before central outcomes were opened.

This addendum binds and narrowly supersedes the central economic interpretation in:

- base protocol: `yfm11econp-fa9218f96bb810350e8526e4`
- base content: `sha256:fa9218f96bb810350e8526e48bd2f9cfe7404eea47146ef2516878c6aa266cfd`
- addendum: `yfm11econdec-d4671bb7386d07f2eca1a6df`
- addendum content: `sha256:d4671bb7386d07f2eca1a6dfc3e77eb779230b8d61a541bb04a77c3828ae058e`

It changes no calibration input, execution, evidence identity, or validity rule. Calibration evidence does not need to be regenerated or revalidated because this addendum changes only the interpretation of unopened central outcomes.

## Why the interpretation needed correction

The first flaw was conflating three different questions. Full-future savings (`F`) measure an oracle-assisted opportunity; unknown-future headroom measures the gap between `K` and `F`; known-only savings (`K`) measure the causal candidate. Unknown headroom is informative about forecast opportunity, but it is not an independent economic veto. Further, `F` has not been proven an upper bound on `K` in this implementation, so an `F` failure cannot erase a passing causal `K` result or justify abandonment by itself.

The second flaw was treating a negative LOCo result as a global product verdict. LOCo and Lectra can support segment-specific products. A red LOCo segment therefore opens the minimum Lectra screen; only both segments being red can falsify current modeled value globally.

## Frozen statistics and gates

All paired-stream bootstraps use NumPy `Generator(PCG64(0))`, 10,000 resamples, linear/type-7 quantiles, and a 95% interval.

The `F` economic gate is green only when all four conditions hold:

- mean savings is at least `2.500000000000%`;
- the 95% lower confidence bound is strictly greater than zero;
- median savings is strictly greater than zero; and
- positive-stream fraction is strictly greater than `0.500000000000`.

Mean unknown headroom of at least `1.500000000000` percentage points is recorded as a diagnostic only. It cannot turn any candidate red.

The `K` causal gate is green only when all four conditions hold, with a mean threshold of `1.500000000000%` and the same strict lower-bound, median, and positive-fraction rules.

Every scalar is a bounded, finite, canonical twelve-place decimal string. The implementation rederives every component flag, aggregate flag, candidate classification, next step, and content identity.

## Segment decision

| K causal gate | F economic gate | Segment class | Meaning |
| --- | --- | --- | --- |
| Green | Either | `causal_candidate` | Continue to adverse confirmation for this segment. |
| Red | Green | `forecast_candidate` | Continue to the forecast branch for this segment. |
| Red | Red | `current_segment_red` | No current candidate in this segment. |

For LOCo, those classes map respectively to `CONTINUE_ADVERSE_LOCO`, `CONTINUE_FORECAST_LOCO`, and `CONTINUE_LECTRA_SCREEN`. A LOCo-only decision is never a terminal global verdict.

## Cross-segment reducer

The reducer consumes exactly one LOCo decision and one Lectra decision in that order:

1. If either segment is a causal candidate, continue adverse confirmation for every causal segment.
2. Otherwise, if either segment is a forecast candidate, continue the forecast branch for every forecast segment.
3. Only when both segments are `current_segment_red` return `INSUFFICIENT_CURRENT_MODELED_VALUE`.

This last disposition falsifies economic value for the currently modeled product and algorithms. It is not proof that no possible algorithm or product form can ever work.

No modeled result authorizes productization. A positive segment can become, at most, a bounded-pilot candidate after adverse or deployable confirmation succeeds.
