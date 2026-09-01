# M11 economic resolution

Date: 2026-08-30

**Status:** `INSUFFICIENT_CURRENT_MODELED_VALUE` — economic value is resolved for the current
modeled product and algorithms; a bounded pilot and productization are not authorized.

## Executive Summary

- **Stop investing in the current YieldForge product and algorithms.** Across 40 complete paired
  confirmation streams, the known-only executor produced exactly `0%` savings in both tested
  segments. It therefore demonstrated no deployable economic benefit that could pay for a product.
- **Perfect future information did not reveal a reliable hidden opportunity.** The full-future arm
  saved `0%` on LOCo and averaged only `0.536368330506%` on Lectra, with a `0%` lower confidence
  bound, a `0%` median, and gains in only 3 of 20 Lectra streams. Both segments failed every frozen
  reliability and magnitude gate.
- **The conclusion is deliberately bounded.** This falsifies current modeled value, not every
  possible future algorithm, factory segment, or product form. Reopen only for materially new
  algorithmic capability, a materially different segment, or real factory chronology and costs
  strong enough to justify a new test independently.

## The tested product does not create deployable savings

Every stream compared three arms over the same released jobs, candidate catalog, geometry, event
sequence, material assumptions, and cost ledger:

- **B — baseline:** the frozen `age_regularity` policy, using only released information;
- **F — full future:** the tested YieldForge executor with complete future visibility; and
- **K — known only:** the tested YieldForge executor using only information available at the time
  of each decision.

F was a deliberately favorable-information arm, but it was not proven to be a mathematical upper
bound on K. The two arms executed separate finite policies, and neither was a proof of globally
optimal material cost. Under the registered decision rule, F alone could not authorize the
terminal stop: that disposition required both F and K to be red in both segments.

The baseline was not chosen to make YieldForge look good. Six registered policies were scored on
separate calibration streams before the central outcomes were opened. `age_regularity` tied for
lowest cost with four other policies on Lectra and with all five alternatives on LOCo; the materially
weaker Lectra `myopic_geometry` policy was not selected.

Net cost is purchase cost plus storage and handling, less scrap proceeds and terminal inventory
credit. Per-stream savings are `100 × (B - arm) / B`. This is a paired, cost-complete comparison:
an apparent material saving counts only after its storage, handling, scrap, and terminal-inventory
effects reconcile.

| Segment | Arm | Mean savings | 95% paired-bootstrap CI | Median | Positive streams | Frozen result |
|---|---|---:|---:|---:|---:|---|
| LOCo 2D-ICS | F, full future | `0.000000000000%` | `[0.000000000000%, 0.000000000000%]` | `0.000000000000%` | 0/20 | Red |
| LOCo 2D-ICS | K, known only | `0.000000000000%` | `[0.000000000000%, 0.000000000000%]` | `0.000000000000%` | 0/20 | Red |
| Lectra M3/M4 | F, full future | `0.536368330506%` | `[0.000000000000%, 1.098250947619%]` | `0.000000000000%` | 3/20 | Red |
| Lectra M3/M4 | K, known only | `0.000000000000%` | `[0.000000000000%, 0.000000000000%]` | `0.000000000000%` | 0/20 | Red |

The intervals use 10,000 paired-stream bootstrap resamples with NumPy `PCG64(0)` and linear/type-7
quantiles. All 40 registered central cells completed and persisted; there were zero failed cells.

Semantically, LOCo offers no measured opportunity under either information set: `B = F = K` in
every stream. Lectra contains three isolated cases where perfect future knowledge helped, but that
signal is too small and too sparse to survive the registered test. More importantly, the known-only
executor matched the baseline in every Lectra stream. The measured `0.536368330506` percentage-point
Lectra gap between K and F is forecast headroom, not a deployable saving and not a product result.

## Both segments fail the decision gates

The outcome-blind addendum required every condition in the applicable column:

| Required condition | F economic gate | K causal gate |
|---|---:|---:|
| Mean savings | at least `2.500000000000%` | at least `1.500000000000%` |
| 95% lower confidence bound | strictly greater than `0%` | strictly greater than `0%` |
| Median savings | strictly greater than `0%` | strictly greater than `0%` |
| Positive-stream fraction | strictly greater than `50%` | strictly greater than `50%` |

LOCo fails every F and K condition. Lectra F fails the mean, lower-bound, median, and positive-stream
conditions; Lectra K fails all four. Each segment is therefore `current_segment_red`. The registered
cross-segment reducer maps two red segments to `INSUFFICIENT_CURRENT_MODELED_VALUE`, with
`economic_value_resolved=true`, `bounded_pilot_authorized=false`, and
`productization_authorized=false`. Thus the terminal decision rests on the joint F-and-K result in
both segments, not on treating F as a mathematical ceiling or on its failure alone.

## There is no deployable break-even scale

For annual product total cost of ownership `T` and deployable savings fraction `s`, the minimum
annual addressable material spend needed to break even is `T / s`.

- At the frozen K threshold of `1.5%`, break-even spend would be `66.67 × T`.
- Measured K savings are exactly `0%` in both segments, and each K lower bound is also `0%`.
  Therefore no finite modeled material-spend scale makes the measured deployable benefit cover any
  positive product TCO.
- Lectra's `0.536368330506%` F mean would imply about `186.44 × T`, but F uses complete future
  knowledge, has a zero lower bound and median, and occurs in only 3/20 streams. It is not a valid
  product break-even estimate.

This is why the current result is not merely “the savings might be too small for a small factory.”
The tested deployable system produced no savings at any modeled scale.

## Evidence provenance limits the claim

The test pack uses the frozen five-category provenance boundary:

| Provenance category | Evidence used here |
|---|---|
| `source_observed` | Geometry references and source demand from Lectra and LOCo |
| `externally_anchored` | Lectra candidate references |
| `derived` | Geometry-family identity, order quantity, cost ledgers, B/F/K metrics, intervals, and decisions |
| `generated` | Chronology, customer/job identity, release/known/due times, priority, fallback layouts, LOCo candidates, and stock boundaries |
| `assumed` | Material identity and all economics |

Accordingly, the result is a valid semi-synthetic hypothesis disposition. It is strong enough to
stop the current investment thesis because even its tested deployable executor creates no modeled
benefit. It is not factory representativeness, realized ROI, buyer demand, adoption, integration
reliability, or commercial proof. A different algorithm or newly observed factory segment would be
a new hypothesis, not a reinterpretation of this result.

## Artifact and readback record

| Evidence | Semantic identity | Content SHA-256 | Status |
|---|---|---|---|
| Economic protocol | `yfm11econp-fa9218f96bb810350e8526e4` | `sha256:fa9218f96bb810350e8526e48bd2f9cfe7404eea47146ef2516878c6aa266cfd` | Bound |
| Outcome-blind decision addendum | `yfm11econdec-d4671bb7386d07f2eca1a6df` | `sha256:d4671bb7386d07f2eca1a6dfc3e77eb779230b8d61a541bb04a77c3828ae058e` | Bound before central outcomes |
| Calibration manifest | `yfm11econcalman-3409ada18b831fee1394410d` | `sha256:3409ada18b831fee1394410dfec88a02806ff0c3709372cfbe9340e05b920533` | Complete and valid, 96/96 attempts |
| Validity evidence | `yfm11g3valrcpt-c5964f606ac450e583ea0859` | `sha256:c5964f606ac450e583ea08595e935a3389ece7c45045489fe7c985882140e9e6` | Valid |
| Validity manifest | `yfm11econvalman-8d736641be30b3d04dad50eb` | `sha256:8d736641be30b3d04dad50eb21698738ac4924bb42f664961911910ddb9ddfe4` | Central authorized |
| LOCo segment summary | `yfm11econsegsummary-0474c6399f3465fe3f50ad45` | `sha256:0474c6399f3465fe3f50ad455c80a133ec967febedfe7f66bbad373080cd212e` | `current_segment_red` |
| Lectra segment summary | `yfm11econsegsummary-746e83d7bdc4482ec2276adc` | `sha256:746e83d7bdc4482ec2276adccf2646b611b4d2267a4d6b3c201cff85e403ef9f` | `current_segment_red` |
| Cross-segment decision | `yfm11econglobal-daa88aa1b9aee893d301a9ee` | `sha256:daa88aa1b9aee893d301a9ee8721e0143ae8264fd1d3195c74fdca8cbe705d9c` | Terminal red |
| Central manifest | `yfm11econcentral-71171ff1cb601f546f55b78e` | `sha256:71171ff1cb601f546f55b78eda8dc2b81d60d7e02949042a55d53feb29e5dcf2` | Complete, 40/40 cells |
| Independent notebook | source file | `sha256:e40e73d0da4e22e4d723c9604cf6fd129e4690b93d80f57e43c93988bd71beba` | Two clean, identical executions |

The central manifest's raw-file SHA-256 is
`sha256:3f3eb6aaa59ea4a1809e8684b3603096b180ecad4d53e42af6e950d08f7f4633`.
Its 40 content-addressed compressed cell sidecars are present in the authenticated external packet,
not the tracked tree, and no failure sidecars exist.

The independent recomputation notebook is
`yf/notebooks/m11-economic-resolution.ipynb`. It was executed twice from clean source with zero
cell errors. Both executions produced byte-identical normalized outputs with SHA-256
`a1c74735dd94d8b3c75325c75c75af091e9cb00ad864ba9630b5d5f066df7028`. It independently
reconciled 40 checkpoints, 40 compressed cell sidecars, 120 ledgers, all per-stream metrics, both
bootstrap intervals, every gate, and the cross-segment reducer to the same terminal verdict.

### Tracked and local evidence boundary

The full authenticated replay packet remains preserved separately as local evidence outside Git.
The repository also tracks three selected calibration, validity, and central manifests. They
contain no source geometry or source demand, but collectively embed complete non-reconstructive
checkpoint,
receipt, and segment-summary payloads, including per-stream/per-policy records, legacy offsets and
failure details, and standalone sidecar names and hashes. The corresponding standalone checkpoint,
summary, and raw sidecar files are omitted; their embedded record payloads are disclosed.

A clean clone can authenticate the selected manifests and inspect this report, but the notebook
cannot perform its complete raw reconciliation without the separately preserved source-bound
parents, raw observation/validity/central-cell sidecars, and expected standalone file layout.
“Non-reconstructive” is a technical observation about source geometry and demand, not a claim that
the manifests are the complete replay packet. See
[Artifact Policy](../Development/Artifact%20Policy.md) and
[PUBLIC_RELEASE.md](../../PUBLIC_RELEASE.md).

## This result adds economic evidence; it does not rewrite history

M10 result `yfm10-931b3a95fe84cd96cff799f2` remains the historical minimum roadmap verdict:
`do_not_productize`, stop virtual-oracle investment, and `formal_economic_band=not_computed` at that
time. The earlier [[M11 - Realistic falsification verdict]] also remains the canonical record of the
invalid first M11 attempt and its contract-forced abandon decision.

This document records a later, separately bound repair-lineage economic-resolution test. It answers
the question those artifacts did not: after repairing the non-economic execution and evidence
defects, did paired cost-complete measurement reveal enough modeled savings to retain YieldForge?
The valid answer is no.

## Final action

Stop current YieldForge product and algorithm investment. Preserve the research artifacts, but do
not build a pilot or product around the measured system. Reopen only if a materially new algorithm,
a materially different segment, or real factory evidence creates a new independently justified
hypothesis; freeze and test that hypothesis prospectively rather than relaxing this result.
