# M11 Realistic Falsification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Execute the minimum credible semi-synthetic falsification test and publish a forced `ABANDON` or bounded `CONTINUE_TO_REAL_PILOT` verdict without reopening the frozen M6-M10 evidence.

**Architecture:** Add a self-contained `yieldforge.realistic_falsification` package. It owns a strict content-addressed M11 contract, two-source deterministic pack, certified relaxed-cost bound, staged evaluation, statistics, verdict, and immutable report. Existing M0 and M10 artifacts are parents; existing M6-M9 orchestration remains untouched. Execution is terminal-gate driven: stop as soon as a valid mathematical or economic result forces abandonment, and only build the more expensive geometry/replay stage when the preceding gate survives.

**Tech Stack:** Python 3.12, Pydantic v2 strict frozen models, Decimal six-place accounting, NumPy deterministic bootstrap, Shapely/Jagua only if Gate 2 opens, Typer CLI, pytest.

---

## Task 1: Freeze strict M11 contracts and verdict semantics

**Files:**

- Create: `yf/src/yieldforge/realistic_falsification/__init__.py`
- Create: `yf/src/yieldforge/realistic_falsification/contracts.py`
- Create: `yf/tests/realistic_falsification/test_contracts.py`

**Steps:**

1. Write failing tests for strict/frozen models, semantic IDs, parent bindings, corpus independence, provenance labels, frozen sample counts, metric identities, and terminal verdict mapping.
2. Run `uv run pytest tests/realistic_falsification/test_contracts.py` from `yf` and confirm the import/test failure.
3. Implement minimal Pydantic models and builders. The four evidence states are `invalid_test`, `falsified_by_optimistic_ceiling`, `insufficient_headroom`, and `retain_for_pilot`; only the last maps to `CONTINUE_TO_REAL_PILOT`.
4. Require 8 calibration and 20 confirmation streams per corpus, 24 events per stream, exactly two independent source lineages, fixed thresholds, and the M11 claim ceiling.
5. Rerun the focused test and commit.

## Task 2: Parse, attest, and normalize two independent geometry sources

**Files:**

- Create: `yf/src/yieldforge/realistic_falsification/sources.py`
- Create: `yf/tests/realistic_falsification/test_sources.py`
- Create: `yf/tests/realistic_falsification/fixtures/loco-mini.zip`
- Create: `yf/benchmarks/falsification/source-manifest-v1.json`

**Steps:**

1. Write failing tests for the official LOCo archive hash, strict parser behavior, canonical polygon normalization, Lectra M3/M4 parent verification, cross-corpus geometry de-duplication, and malformed input rejection.
2. Implement a bounded ZIP reader with no path extraction, normalized geometry-family hashes, and explicit source/derived/generated provenance.
3. Bind the official LOCo URL and SHA-256 `86980c3d4a33fb329bd9a4cdc9464a6de9e8450baf70b1b4365944ab471a5133` without committing the raw upstream archive. Bind the committed Lectra M3/M4 artifact identities.
4. Generate and validate a small source manifest; preserve source coordinate units as unknown where they are not declared.
5. Run focused tests and commit.

## Task 3: Generate and byte-regenerate the frozen semi-synthetic pack

**Files:**

- Create: `yf/src/yieldforge/realistic_falsification/pack.py`
- Create: `yf/tests/realistic_falsification/test_pack.py`
- Create: `yf/benchmarks/falsification/m11-contract-v1.json`
- Create: `yf/benchmarks/falsification/m11-population-v1.json`
- Create: `yf/benchmarks/falsification/streams/.gitkeep`

**Steps:**

1. Write failing tests for deterministic generation, family-level calibration/confirmation isolation, 8/20/24 census, unique stream IDs, fixed central/optimistic/adverse parameters, firm-schedule prefix visibility, hard-null and shuffled twins, and per-field provenance.
2. Implement a transparent generator driven only by frozen source-family hashes and seeds. Generate chronology, customer/job/material identities, recurrence, costs, handling/storage, and known/release/due times independently of any YieldForge outcome.
3. Freeze one central target phenotype, one permissive optimistic arm, and one conservative adverse arm. Ensure the two corpora receive equal total weight and no confirmation result can feed parameter selection.
4. Regenerate the pack twice into separate temporary roots and require byte identity.
5. Publish the contract/population and compact stream files, run focused tests, and commit.

## Task 4: Implement and prove the certified Gate 1 relaxed-cost bound

**Files:**

- Create: `yf/src/yieldforge/realistic_falsification/bounds.py`
- Create: `yf/tests/realistic_falsification/test_bounds.py`

**Steps:**

1. Write failing analytical tests for zero demand, one item, fractional material, material mismatch, known-only masking, chronology relaxation, zero friction, terminal credit, and monotonicity under every relaxation.
2. Implement a lower bound on achievable full-future net cost by relaxing geometry, stock indivisibility, chronology, storage, handling, and perfect identification only in favorable directions. Keep material identities separate. Document the proof direction in code and artifact fields.
3. Compute `B_feasible` and `K_feasible` only from verified feasible frozen policies. `K_feasible` uses the same algorithm, action set, and compute contract with unknown events masked; it is not another lower bound. The certified upper bounds are `B_feasible - L_full` and `K_feasible - L_full`.
4. Cross-check tiny cases against exhaustive enumeration and assert `L_full <= exact_full_optimum <= B_feasible`, `exact_known_optimum <= K_feasible`, and `K_feasible - L_full >= exact_known_optimum - exact_full_optimum`. A violation is `invalid_test`, never economic evidence.
5. Run focused tests and commit.

## Task 5: Implement deterministic inference and Gate 1 decision

**Files:**

- Create: `yf/src/yieldforge/realistic_falsification/statistics.py`
- Create: `yf/src/yieldforge/realistic_falsification/evaluate.py`
- Create: `yf/tests/realistic_falsification/test_statistics.py`
- Create: `yf/tests/realistic_falsification/test_evaluate.py`

**Steps:**

1. Write failing tests for equal-stream/equal-corpus weighting, 10,000 paired stratified bootstrap replicates with seed 0, frozen quantiles, Wilson intervals, joint ceiling margin, and every terminal branch.
2. Implement stream metrics and the joint margin `R = max(min(mean_savings_c - 1.5, mean_unknown_c - 0.5))` over both corpora and the equal-corpus pool.
3. Permit `falsified_by_optimistic_ceiling` only when the one-sided 97.5th percentile of `R` is below zero and every bound audit passes.
4. Otherwise return a typed `gate_1_survived` result that opens Gate 2; do not infer retention from the relaxed bound.
5. Run focused tests and commit.

## Task 6: Validate and execute canonical Gates 0-1

**Files:**

- Create: `yf/src/yieldforge/realistic_falsification/runner.py`
- Create: `yf/tests/realistic_falsification/test_runner.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/test_cli.py`
- Create: `yf/experiments/results/m11-gate1-<content-id>.json` at execution time

**Steps:**

1. Write failing end-to-end and CLI tests for `benchmark m11-generate`, `m11-validate`, and `m11-run` with frozen inputs only.
2. Implement immutable publication using the existing artifact publisher and a strict read-back validator.
3. Run Gate 0, execute Gate 1 across every registered stream, independently recompute identities/metrics, and publish all stream-level cells including negative and failed cells.
4. If the certified ceiling robustly falsifies the opportunity, publish `ABANDON`, write the decision report, and skip Tasks 7-9.
5. If Gate 1 survives, record the exact headroom and proceed without changing the contract.

## Task 7: Tighten surviving opportunity with geometry-informed matching

**Condition:** Execute only if canonical Gate 1 survives.

**Files:**

- Create: `yf/src/yieldforge/realistic_falsification/matching.py`
- Create: `yf/src/yieldforge/realistic_falsification/geometry_gate.py`
- Create: `yf/tests/realistic_falsification/test_matching.py`
- Create: `yf/tests/realistic_falsification/test_geometry_gate.py`

**Steps:**

1. Write failing tests for no remnants, one-to-one matches, competing edges, material mismatch, negative reward, deterministic ties, and a tiny exact result no better than the optimistic relaxation.
2. Build first-generation remnant-to-future-standard-sheet opportunity graphs with one-use constraints and a deterministic maximum-weight matcher.
3. Apply necessary area/bounds filters, then require exact Shapely/Jagua witnesses for every decision-relevant edge. Preserve unresolved/truncated cases explicitly.
4. Include sheet cost avoided minus return, storage, and retrieval cost under central and adverse economics.
5. If both corpora or the pooled result cannot reach the Green floors with adequate support headroom, publish `insufficient_headroom` / `ABANDON` and skip Tasks 8-9.

## Task 8: Execute paired confirmation only if geometry headroom survives

**Condition:** Execute only if canonical Gate 2 survives.

**Files:**

- Create: `yf/src/yieldforge/realistic_falsification/adapter.py`
- Create: `yf/src/yieldforge/realistic_falsification/confirmation.py`
- Create: `yf/tests/realistic_falsification/test_adapter.py`
- Create: `yf/tests/realistic_falsification/test_confirmation.py`

**Steps:**

1. Write failing tests for M11-to-M7 projection, action/candidate parity, known/full visibility isolation, same algorithm/compute, causal deployable behavior, hard nulls, shuffled twins, and exact sentinel agreement.
2. Fit the strongest baseline and deployable parameters on calibration streams only and freeze them before confirmation output is exposed.
3. Execute paired `B/F/K/D/D0` arms with shared candidates and budgets. Use existing M7 replay and M9 two-ply components through a narrow adapter; do not modify frozen orchestration.
4. Execute the fixed adverse, terminal, eligibility, ordinary/expanded, exact-case, and search controls.
5. Preserve every registered stream and failure.

## Task 9: Aggregate support gates and force the final verdict

**Condition:** Execute only if canonical Gate 2 survives.

**Files:**

- Extend: `yf/src/yieldforge/realistic_falsification/evaluate.py`
- Extend: `yf/tests/realistic_falsification/test_evaluate.py`
- Create: `yf/experiments/results/m11-realistic-falsification-<content-id>.json`
- Create: `Docs/Evidence/M11 - Realistic falsification verdict.md`

**Steps:**

1. Add TDD cases for immediate sacrifice, opportunity frequency, ordinary availability, remnant realization, exact telescoping concentration, deployable capture, central/adverse gates, and no-signal validity.
2. Aggregate both corpora and equal-corpus pool with confidence intervals and all support metrics.
3. Apply the frozen decision algorithm mechanically. Only a fully valid Green result with all controls maps to `CONTINUE_TO_REAL_PILOT`; every other valid result maps to `ABANDON`.
4. Publish an answer-first report exposing assumptions, source hashes, result distributions, failure cells, claim ceiling, and the exact next action.

## Task 10: Independent audit and final verification

**Files:**

- Modify only files required by discovered defects; preserve the failed canonical artifact if the single allowed repair is used.

**Steps:**

1. Have an independent reviewer recompute pack identities, bounds, metrics, confidence intervals, and the verdict from published stream cells.
2. Run `uv run --all-groups pytest` from `yf`, then rerun the M11 canonical command from a clean temporary output root.
3. Run `git diff --check`, inspect `git status`, and verify every committed result hash/read-back contract.
4. If a preregistered integrity/software defect is found, perform exactly one same-contract repair and rerun. A second integrity failure forces `ABANDON` while retaining `invalid_test` as the evidence state.
5. Commit the verified evidence and report. Mark the goal complete only after reporting the forced verdict and final token usage.

## Stop rules

- Never interpret invalid data as evidence for or against the hypothesis.
- Never make the test easier after seeing confirmation output.
- Stop expensive downstream work immediately on a valid terminal `ABANDON` result.
- A positive modeled result authorizes only a bounded real-history/operator pilot; it never authorizes productization.
