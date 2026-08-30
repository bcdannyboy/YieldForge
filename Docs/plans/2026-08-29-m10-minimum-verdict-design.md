# M10 Minimum Investment Verdict Design

**Status:** Approved for implementation on 2026-08-29 by the user's instruction to do the minimum
work necessary, continue without stopping, and reach a final YieldForge verdict.

## Decision

Complete M10 as a content-addressed **investment decision**, not as an unexecuted claim that the
original M0 economic experiment passed. The result will answer the roadmap question—whether
YieldForge has earned another investment—while preserving an explicit `not_computed` state for the
formal oracle-savings band.

The minimum defensible current verdict is:

> Do not productize YieldForge or spend more time expanding the virtual oracle. Acquire real
> manufacturer history only if there is appetite for one more bounded evidence stage.

This maps to M10's named middle outcome, `acquire_real_manufacturer_history`. It is an
evidence-ceiling investment verdict, not a numeric M0 yellow band and not proof of savings,
physical recovery, adoption, ROI, integration reliability, or market demand.

## Why the original numeric route is not the minimum

The original M0 band requires paired baseline, known-only, and full-future policy results. M7
supplies only the baseline denominator. M8 did not publish an oracle evaluation, and M9 validates
fixed two-ply first-action selection only on 45 finite cases.

Directly applying M9's two-ply scorer to the complete M7 evaluation is not a small wrapper. M7
recorded 550,542 actions over 864 evaluation events, about 637 actions per event. A direct
two-ply cross product is on the order of 350 million explicit branches and billions of frozen-M7
continuation-event executions before repeatability or sensitivity arms. A reduced stream or
candidate sample cannot produce the frozen numeric M10 result.

The current benchmark also binds one Lectra geometry corpus. M0 requires positive evidence in at
least two corpora for green. Its chronology and economics are generated and its material identity
is assumed. Completing more virtual computation cannot remove those evidence ceilings.

## Alternatives rejected

### Full 36-stream two-ply evaluation

This is the only path to the original numeric M0 band, but it requires a new scalable dynamic
program, extensive memoization/state-equivalence work, all control arms, and a large execution
budget. It is not the minimum work needed to answer the investment question and would continue the
same proof/compute program the user explicitly chose to stop expanding.

### Six-stream or sampled numeric verdict

A sampled oracle run could be directional, but it cannot honestly be labeled the preregistered
M10 result. M0 requires every registered regime and seed, common paired actions, known-only
attribution, controls, and frozen statistics. Sampling would create false precision without
removing the one-corpus and synthetic-history limitations.

### Evidence-ceiling investment verdict

This is the selected approach. It verifies the immutable evidence chain, records exactly which
formal quantities are absent, and applies a separately frozen M10 decision rule before any oracle
evaluation result exists. It closes product-development authorization now while preserving a clear
reopen condition: obtain real manufacturer history and a genuinely independent second geometry
corpus before considering another bounded experiment.

## Frozen inputs

The runner binds exact raw bytes plus semantic identities for:

- the frozen M0 contract;
- the M6 contract and immutable 48-stream population;
- the twice-reproduced M7 evaluation baseline;
- the M8 Gate-3 `hold_performance` decision; and
- the passing M9 two-ply finite decision artifact.

Every parent binding includes repository-relative filename, schema version, semantic ID,
`content_sha256`, and raw-file SHA-256. Any missing, replaced, malformed, duplicate-key,
non-finite, or identity-drifted parent fails closed and publishes no verdict.

## Decision rule

The v1 decision is valid only when all of the following reconcile:

1. M0 remains frozen and requires at least two positive geometry corpora for green.
2. M6 binds exactly one source geometry dataset, generated chronology/economics, and assumed
   material compatibility.
3. M7 completed and twice reproduced the registered 36-stream baseline.
4. M8's current technical result is `hold_performance`, its oracle evaluation was not opened, and
   no oracle-savings result exists.
5. M9's fixed two-ply repair returns `pass_decision_feasibility` on the registered finite cases,
   while retaining its narrow claim ceiling.
6. No formal `OracleSavings` or `UnknownFutureContribution` estimate is supplied to this runner.

Under those exact conditions:

- `formal_economic_band = not_computed`;
- `formal_numeric_m10_complete = false`;
- `productization_decision = do_not_productize`;
- `additional_virtual_oracle_investment = stop`;
- `investment_verdict = acquire_real_manufacturer_history`; and
- `roadmap_decision_complete = true`.

Changing any decision-defining condition requires a new schema/experiment identity. The runner may
not silently convert absent economic measurements into red, yellow, or green.

## Artifact and publication

Publish one immutable artifact with schema
`yieldforge.m10-minimum-investment-verdict.v1`, result namespace `yfm10-`, and semantic identity
derived from canonical compact JSON. Operational wall time remains outside semantic identity.

The artifact contains:

- exact parent bindings;
- observed, derived, generated, and assumed evidence distinctions;
- the missing formal measurements and controls;
- the deterministic decision predicates;
- the final investment/productization decisions;
- explicit reopen conditions; and
- the claim ceiling.

Two fresh evidence loads must produce identical semantic bytes before publication. Publication uses
the shared race-resistant immutable artifact publisher. A separate stdlib-only verifier must
strict-load the committed artifact, recompute parent raw hashes, semantic identity, decision
predicates, and exact pretty bytes without importing the runner or evaluator.

## Reopen conditions

YieldForge product development remains closed. Reopen only when all of the following exist:

- permissioned real manufacturer chronological order and remnant-history data;
- observed material identities and economically meaningful cost inputs;
- a genuinely independent second geometry corpus; and
- a buyer/operator willing to define the next bounded decision and its cost.

Meeting those conditions does not automatically authorize a product build. It authorizes a new,
separately frozen evidence contract.

## Claim ceiling

The M10 v1 artifact supports only a present investment decision:

> Existing technical evidence is sufficient to stop additional virtual productization work, while
> the unresolved economic measurement and real-data ceilings make manufacturer-history acquisition
> the only justified optional next investment.

It does not claim a formal M0 red/yellow/green band, oracle savings, unknown-future contribution,
physical recoverability, factory representativeness, customer demand, or commercial value.
