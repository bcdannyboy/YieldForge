# M11 Realistic Falsification Design

**Status:** Approved for implementation on 2026-08-29

## Decision

M11 decides whether to abandon the modeled YieldForge product hypothesis or retain it only for a
bounded real-history/operator pilot. It does not rewrite M0-M10, satisfy M10's real-data reopen
conditions, authorize productization, establish buyer demand, or claim realized factory ROI.

Only `retain_for_pilot` maps to `CONTINUE`. Every valid result that does not clear that bar maps to
`ABANDON`.

## Hypothesis

Within the preregistered high-value irregular-cutting target envelope, future-aware remnant
decisions reduce all-in sheet-equivalent net cost by at least 2.5% against the strongest frozen
as-of-time baseline, with at least 1.5 percentage points attributable to information unavailable
to the baseline at decision time.

## Evidence boundary

The test is semi-synthetic:

- geometry comes from two independent source lineages;
- chronology, customer identity, material identity, visibility, order composition, and economics
  are generated or assumed under a frozen contract;
- every field is labeled `source_observed`, `derived`, `externally_anchored`, `generated`, or
  `assumed`;
- physical coordinate units are not inferred from the Lectra source;
- economics are normalized to sheet-equivalent cost and separately anchored to declared labor and
  material assumptions.

The two geometry sources are:

1. the committed Lectra-derived M3/M4 evidence, using authentic polygons and the two residual-blind
   ordinary candidates selected within the frozen 0.5% width envelope; and
2. the University of Campinas LOCo 2D irregular cutting-stock archive, pinned to
   `https://www.loco.ic.unicamp.br/files/instances/2dics_cutting_stock.zip` with SHA-256
   `86980c3d4a33fb329bd9a4cdc9464a6de9e8450baf70b1b4365944ab471a5133`.

LOCo order composition, stock boundary, and candidate layouts are generated and must remain labeled
as such. A split, transform, or resampling of Lectra is not a second corpus.

## Frozen population

Each corpus contains:

- 8 calibration streams used only to freeze the baseline and deployable-policy parameters;
- 20 held-out confirmation streams used as paired inferential units;
- 24 scored chronological events per stream;
- one central arm, one optimistic ceiling arm, and one fixed primary adverse arm evaluated on the
  same streams;
- one shuffled/no-signal twin per confirmation stream;
- at least three hard-null fixtures; and
- at least six short exact-search audit episodes spanning central, adverse, and null behavior.

The pack generator freezes exact source cases, task families, seeds, chronology parameters,
material parameters, horizons, candidate budgets, economics, eligibility rules, and weights before
confirmation output is visible. Calibration and confirmation hold out complete geometry families,
not merely new chronology seeds over the same cases.

The generated event record includes `known_at`, `released_at`, `due_at`, customer, job, family,
quantity, material key, source-geometry reference, priority, economics, and field-level provenance.
A fixed firm-schedule horizon makes known future work a release-ordered prefix and prevents
noncontiguous visibility ambiguity.

## Economic contract

M11 preserves the M0 ledger:

`NetCost = purchases + storage + return handling + retrieval handling - scrap proceeds - terminal credit`

For confirmation stream `i`:

- `B_i` is the strongest frozen as-of-time baseline cost;
- `F_i` is the full-future reference-policy cost;
- `K_i` is the identical reference algorithm, action set, and compute with unknown events masked;
- `D_i` is the time-causal deployable-policy cost;
- `D0_i` is the same deployable policy with unreleased-demand forecasting disabled; and
- `L_i` is a verified lower bound on minimum cost in the explicitly relaxed optimistic arm.

Primary percentages are equal-weight means of stream percentages:

- `FullFutureSavings_i = 100 * (B_i - F_i) / B_i`
- `UnknownFutureContribution_i = 100 * (K_i - F_i) / B_i`
- `DeployableSavings_i = 100 * (B_i - D_i) / B_i`
- `DeployableUnknownContribution_i = 100 * (D0_i - D_i) / B_i`

Corpora receive equal total weight when pooled. Costs use the existing six-decimal canonical ledger.

## Staged execution

### Gate 0: validity

Before economic interpretation, require deterministic regeneration, immutable parent identities,
candidate/action parity, valid geometry, accounting reconciliation, visibility isolation, identical
known/full algorithms and compute budgets, unique confirmation streams, independent source
lineages, exact-case agreement, and valid null controls.

Hard-null savings must be zero within the accounting quantum. No-signal mean above 0.5% invalidates
the test; 0.3-0.5% requires diagnosis before interpretation. One repair is permitted only for a
preregistered integrity or software defect using the same data and decision contract.

### Gate 1: certified optimistic opportunity ceiling

Run the cheapest mathematical relaxation first. It may assume perfect future knowledge, fractional
or perfectly fungible material where declared, permissive remnant eligibility, perfect inventory
identification, and zero operational friction. The resulting `L_i` must be a verified lower bound
on achievable cost; a heuristic oracle is not a certified ceiling.

Define:

- `CeilingSavings_i = 100 * (B_opt_i - L_i) / B_opt_i`
- `CeilingUnknown_i = 100 * (K_opt_i - L_i) / B_opt_i`

Here `B_opt_i` is a verified feasible cost from the strongest frozen as-of-time optimistic-arm
baseline and `K_opt_i` is a verified feasible cost from the identical frozen known-only algorithm
with unknown events masked. Neither may be a lower bound or another relaxation. Subtracting the
full-information lower bound `L_i` from those feasible costs is what makes both quantities certified
upper bounds; subtracting a known-only lower bound would not be valid.

If the joint one-sided 95% upper confidence bound is below either the 1.5% savings floor or the
0.5-point unknown-future floor on every corpus and the equal-corpus pool, issue
`falsified_by_optimistic_ceiling` and stop.

### Gate 2: geometry-informed optimistic relaxation

If Gate 1 survives, evaluate first-generation remnant-to-future-opening opportunity with frozen
material compatibility, chronology, one-use constraints, and necessary area/bounds filters. Use
exact Shapely/Jagua witnesses for every edge that can affect the central decision. Publish rejected,
truncated, and unresolved edges rather than treating them as no-fit.

This gate may stop as `insufficient_headroom`; it cannot by itself issue `retain_for_pilot` unless
all confirmation requirements below are executed.

### Gate 3: confirmation and deployable capture

Only if Gate 2 survives, run paired baseline, known-only, full-future, forecast-disabled deployable,
and deployable policies on the held-out streams. Candidate sets and compute budgets are shared.
The deployable model is fit on calibration streams only. The fixed adverse arm, terminal treatment,
ordinary/expanded candidate comparison, eligibility sensitivity, exact short cases, and no-signal
controls are mandatory.

## Supporting metrics

- **Immediate sacrifice:** sum of positive immediate-ledger excess of the future-aware choice over
  its same-state fallback, divided by baseline stream cost. Mean must be at most 0.5%.
- **Opportunity frequency:** divergent beneficial decisions divided by valid decision epochs with a
  fallback and at least one parity-valid alternative. Must be at least 20%.
- **Ordinary availability:** beneficial expanded-catalog epochs whose best action/post-state exists
  in the ordinary catalog within `1e-6` continuation cost, divided by all beneficial expanded
  epochs. Must be at least 60%.
- **Remnant realization:** distinct deliberately preserved eligible remnant origins consumed before
  horizon close, divided by all such origins. Descendants and repeated withdrawals do not
  double-count. Must be at least 60%.
- **Decision concentration:** exact telescoping baseline-prefix/full-future-suffix attribution. The
  ten largest positive contributions must be at most 25% of total signed savings.
- **Positive stream:** strict full-future savings above zero. More than 50% of streams must be
  positive.

Zero denominators fail the supporting gate; they do not invalidate the test.

Deployable capture is the minimum of its savings capture and unknown-future capture versus the
central full-future reference. It must be at least 50%, while deployable savings must itself be at
least 1.5% and deployable unknown contribution at least 0.5 points.

## Statistics

- The paired stream is the inferential unit.
- Use 10,000 deterministic stratified paired percentile-bootstrap resamples with seed 0.
- Resample complete paired-arm vectors within corpus; reproducibility reruns are not samples.
- Report a two-sided 95% interval for means and a Wilson 95% interval for positive-stream fraction.
- Report mean and median stream savings and preserve all negative cells.

Model uncertainty is addressed through the frozen optimistic, central, and adverse arms; bootstrap
intervals describe simulated-stream variability and do not turn assumed inputs into observed
factory evidence.

## Forced verdict

```text
if validity fails:
    evidence_state = invalid_test
    action = ONE_REPAIR_AND_RERUN if repair_count == 0 else ABANDON
elif the certified joint optimistic ceiling is robustly Red:
    evidence_state = falsified_by_optimistic_ceiling
    action = ABANDON
elif central full-future savings and unknown contribution are Green on both corpora and pooled
     and lower mean bound > 0
     and median savings > 0
     and positive-stream fraction > 50%
     and every supporting/control gate passes
     and the fixed adverse arm is non-Red
     and deployable capture >= 50%
     and deployable savings and unknown contribution are non-Red:
    evidence_state = retain_for_pilot
    action = CONTINUE
else:
    evidence_state = insufficient_headroom
    action = ABANDON
```

## Runtime boundary

The complete canonical run has a 72-hour wall-clock ceiling, with at least 12 hours reserved for
independent recomputation or the single permitted repair. Calibration must estimate each later
gate before it opens. A runtime overrun is `invalid_test`, not economic evidence.

## Publication

M11 publishes new immutable artifacts under `yf/benchmarks/falsification/` and
`yf/experiments/results/`. It never edits prior canonical M6-M10 inputs or results. The final report
must expose source hashes, generated assumptions, all stream-level metrics, failures, confidence
calculations, claim ceiling, evidence state, and forced action.
