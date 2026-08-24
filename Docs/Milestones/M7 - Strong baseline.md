# M7 — Strong baseline

**Status:** In progress — feasibility and collision gate passed; calibration runtime optimization required

M7 builds the opponent YieldForge must honestly beat. The baseline uses current released work, current inventory, remnant age and regularity, storage costs, and already-known orders without seeing unavailable future information.

> **Question:** Can future information outperform a competent system using everything legitimately known at the time?

## Acceptance boundary

Baseline variants are selected only on calibration streams. The winning deterministic policy is frozen before evaluation and receives the same feasible actions and compute accounting as the oracle.

The exact entry inventory, candidate-problem census, missing contracts, collision gate, and ordered
implementation sequence are recorded in
`Docs/plans/2026-08-23-m7-strong-baseline-preparation.md`.

## Implemented boundary

- The corrected M7 index contains 1,152 temporal instances and 209 reusable source problems:
  288/90 calibration instances/problems, 864/198 evaluation instances/problems, and 79 shared
  problems.
- All 90 calibration problems bind to four verified ordinary M2 archives. The first-seed
  feasibility slice reverified 51 problems and 204 archives before replay.
- Five deterministic as-of-safe policy variants, exact complete-layout standard/remnant actions,
  inventory continuity, cost ledgers, and lookahead masking are implemented separately from M5 v1.
- The preregistered collision gate triggered on the first real recurrence opportunity. A local
  extension of Spyrrow's pinned `jagua-rs` 0.7.0 backend now generates the registered bounded
  translations from exact `f64` bit patterns and uses a guarded `f32` collision prefilter while
  Shapely remains the accepted-witness, residual-overlay, and differential oracle.

## Measured Task 5 result

Differential result `yfm7d-055c3aa7a09c85fbee2f1ca2` compared all 174,626 translations for 709
real archived layouts. Rust generation matched the Python sequence exactly and the guarded Jagua
path had zero collision/result mismatches. The measured production-search speedup was 44.273522x;
collision evaluation alone was 21.912771x faster.

Feasibility result `yfm7f-7edef2fa8719168941e431d2` completed all six regimes and 144 events. It
evaluated 89,936 actions, 778,286 complete-layout/remnant queries, and 186,305,788 registered
translation candidates. The immutable-search cache recorded 479 hits and 709 misses. All 144
selected actions opened standard sheets; zero remnant actions were feasible or selected. This is a
negative complete-layout-reuse result for the registered slice, not proof that remnant reuse is
impossible in general.

The run took 3,864.895624 seconds, of which 3,225.582819 seconds was fit search. The frozen 10x
projection for 12 calibration streams by five policies is 644.149271 minutes. Task 6 therefore
requires a documented runtime reduction or an explicitly accepted long calibration run before
policy selection. Evaluation remains unopened and M8 remains out of scope.
