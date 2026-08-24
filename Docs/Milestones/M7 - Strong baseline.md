# M7 — Strong baseline

**Status:** Passed — calibrated, frozen, and twice reproduced on evaluation

M7 builds the opponent YieldForge must honestly beat. The baseline uses current released work,
current inventory, remnant age and regularity, storage costs, and already-known orders without
seeing unavailable future information.

> **Question:** Can future information outperform a competent system using everything legitimately
> known at the time?

## Acceptance result

Passed. Five deterministic as-of-safe variants ran on all 12 calibration streams. The selected
policy and runtime were frozen before the 36 evaluation streams were opened. The frozen policy
then executed all 864 evaluation events twice with identical replay content.

This pass establishes a reproducible baseline opponent. It does not establish oracle advantage,
material savings, physical recoverability, solver optimality, or commercial value.

## Implemented boundary

- The corrected M7 index contains 1,152 temporal instances and 209 reusable source problems:
  288/90 calibration instances/problems, 864/198 evaluation instances/problems, and 79 shared
  problems.
- M7 verifies ordinary M2 candidate archives rather than copying or regenerating candidates. The
  final evaluation reverified 198 problems against 792 frozen archives.
- Five deterministic as-of-safe policy variants, exact complete-layout standard/remnant actions,
  inventory continuity, cost ledgers, and lookahead masking are implemented separately from M5 v1.
- A local extension of Spyrrow's pinned `jagua-rs` 0.7.0 backend generates registered bounded
  translations from exact `f64` bit patterns and applies a guarded `f32` rejection prefilter.
  Shapely remains authoritative for accepted witnesses, residual overlay, and accounting geometry.

## Feasibility and collision result

Differential result `yfm7d-055c3aa7a09c85fbee2f1ca2` compared all 174,626 translations for 709
real archived layouts. Rust generation matched the Python sequence exactly and the guarded Jagua
path had zero collision/result mismatches. The measured production-search speedup was 44.273522x;
collision evaluation alone was 21.912771x faster.

Feasibility result `yfm7f-7edef2fa8719168941e431d2` completed all six regimes and 144 events. It
evaluated 89,936 actions, 778,286 complete-layout/remnant queries, and 186,305,788 registered
translation candidates. All 144 selected actions opened standard sheets; no remnant action was
feasible or selected in that bounded slice. This was a negative complete-layout-reuse result for
the registered slice, not proof that remnant reuse is impossible.

## Calibration and freeze result

Calibration result `yfm7cal-172006fc66891ceee0c41d49` executed five policies across all 12
calibration streams: 60 stream replays and 1,440 event decisions. Candidate verification took
871.008355 seconds; replay took 11,325.353834 seconds, including 6,267.527667 fit-search seconds.
The shared exact-search cache recorded 10,105 hits and 1,247 misses.

`remnant_first`, `net_cost`, `age_regularity`, and `known_order_lookahead` tied exactly on every
selector term: mean net cost 30,497.582533, median net cost 31,961.748673, and 276 sheet openings.
`age_regularity` won only the preregistered final lexical tie-break. It must not be described as
empirically superior to the other three tied policies. `myopic_geometry` produced mean net cost
32,785.004506 and 288 sheet openings on calibration; that calibration-only difference is not an
evaluation or commercial claim.

The original runtime freeze was amended before any evaluation artifact was published. Replay engine
1.0.1 records a single compatibility rule: if an otherwise valid exact Shapely remnant cannot be
represented as Jagua's one guarded `f32` polygon, Jagua rejects nothing and authoritative Shapely
checks every translation. Policy, candidates, calibration outcomes, and exact decision semantics
were unchanged. Amended freeze `yfm7freeze-5c13c3fe531828d8cd986c39` pins CPython 3.12.7, Shapely
2.1.2, Jagua 0.7.0, and binary SHA-256
`f886f49f1132a8f9023ef8a1feda9b9f4f8296ce07812afba3bea5ee54fdb1c3`.

## Evaluation result

Canonical result `yfm7eval-f2cb310c4b7e879d119e8f94` executed the frozen `age_regularity` policy
on all 36 evaluation streams and 864 events twice. Replay content matched exactly with reproducible
content SHA-256 `47cc40ff16ab71f70163df23bb1a346c061d2765d2e2113eca5f0c06e5756cf8`.

The evaluation reverified 120,685 raw candidates, retained 120,682 distinct exact-valid candidates,
and rejected three. It evaluated 550,542 actions, opened 826 sheets, selected 38 remnant retrievals,
and ended with mean net cost 29,733.304499. The Jagua representation fallback was invoked 32 times
across parallel candidate chunks in exactly one stream: `regime_shift`, seed 2026082305. That
stream completed and reproduced semantically; the cached second pass did not need to invoke the
runtime fallback again.

These figures characterize the frozen baseline only. M8 must supply the future-aware rollout arm
on the same evaluation worlds before any paired advantage or savings statement exists.

## Runtime and M8 handoff

The available M4 Max GPU is not used by the canonical path. Shapely/GEOS topology and Jagua are CPU
backends, and profiling located the dominant residual work in GEOS validity, topology, noding, and
spatial-index operations. The next runtime optimization should persist content-addressed exact
standard-action and residual profiles, with byte-identical differential replay, before considering
a separate Metal collision experiment.

M8 may now be planned against the immutable M7 freeze and evaluation result. It must preserve the
same candidate/action evidence, keep the baseline information set masked, isolate future access to
the rollout arm, pass hand-computed toy and no-signal sanity cases, and report paired evaluation
effects without promoting rollout to a mathematical upper bound.
