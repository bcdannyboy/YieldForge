# M7 Strong Baseline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build, calibrate, freeze, and execute a deterministic strong baseline over the canonical
M6 temporal population using verified shared candidate evidence and exact batch material actions.

**Architecture:** Add a separate `yieldforge.baseline` package so M5 v1 identities remain unchanged.
Convert each M6 source event into a reusable source-projection problem plus an auditable temporal
instance binding, verify and reuse the canonical M2 ordinary archives, derive exact standard-sheet
and remnant actions, then replay frozen policies under M0 accounting.

**Tech Stack:** Python 3.12, Pydantic v2, Shapely, existing Spyrrow archive contracts, pytest, Ruff,
Typer-style argparse CLI conventions already used by YieldForge.

---

### Task 1: Freeze reusable problem and temporal instance contracts

**Files:**
- Create: `yf/src/yieldforge/baseline/__init__.py`
- Create: `yf/src/yieldforge/baseline/contracts.py`
- Create: `yf/src/yieldforge/baseline/problems.py`
- Create: `yf/tests/baseline/__init__.py`
- Create: `yf/tests/baseline/test_problems.py`

1. Write failing tests proving repeated instances of one source task share a problem ID while their
   stream/event bindings remain distinct.
2. Run `pytest tests/baseline/test_problems.py -q` and confirm failure from missing modules.
3. Add strict content-addressed problem, binding, and census contracts.
4. Implement source-event decomposition and population indexing from committed M6 artifacts.
5. Re-run the focused tests and Ruff.
6. Commit `feat: freeze M7 reusable problem identities`.

### Task 2: Verify and bind ordinary M2 candidate archives

**Files:**
- Create: `yf/src/yieldforge/baseline/archives.py`
- Create: `yf/tests/baseline/test_archives.py`
- Modify: `yf/src/yieldforge/baseline/contracts.py`

1. Write failing tests for four-seed completeness, exact M2 manifest/JSONL reconstruction,
   cross-seed deduplication, and tamper rejection.
2. Confirm the expected missing behavior.
3. Implement canonical M2 result mapping, archive verification, candidate-set identity, and
   population index publication without copying the 2.4 GB runtime archive tree.
4. Verify all 90 calibration problems resolve to four ordinary archives before replay work.
5. Re-run focused tests and Ruff.
6. Commit `feat: bind M7 to verified M2 candidate evidence`.

### Task 3: Implement exact complete-layout actions

**Files:**
- Create: `yf/src/yieldforge/baseline/geometry.py`
- Create: `yf/src/yieldforge/baseline/actions.py`
- Create: `yf/tests/baseline/test_geometry.py`
- Create: `yf/tests/baseline/test_actions.py`

1. Write failing tests for standard-sheet execution, whole-layout remnant translation, part overlap
   rejection, containment rejection, residual reconciliation, and deterministic search order.
2. Confirm failures.
3. Implement complete-layout validation, bounded translation search, atomic subtraction,
   classification, lineage, and strict action evidence.
4. Re-run focused tests and Ruff.
5. Commit `feat: execute exact M7 layout actions`.

### Task 4: Implement M7 replay v1 and policy variants

**Files:**
- Create: `yf/src/yieldforge/baseline/replay.py`
- Create: `yf/src/yieldforge/baseline/policies.py`
- Create: `yf/tests/baseline/test_replay.py`
- Create: `yf/tests/baseline/test_policies.py`

1. Write failing tests for equal-timestamp grouping, storage intervals, cost reconciliation,
   inventory continuity, deterministic repeatability, missing-action failure, policy decision keys,
   and lookahead masking.
2. Confirm failures.
3. Implement M7 input/result builders, common action generation, the five registered variants, and
   M0 cost accounting.
4. Re-run focused tests and Ruff.
5. Commit `feat: add deterministic M7 baseline replay`.

### Task 5: Execute the six-regime feasibility slice and collision gate

**Files:**
- Create: `yf/src/yieldforge/baseline/experiment.py`
- Create: `yf/tests/baseline/test_experiment.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/test_cli.py`
- Create: `yf/experiments/results/m7-feasibility-<id>.json`

1. Write failing tests for deterministic slice membership, complete metrics, idempotent publication,
   and collision threshold decisions.
2. Implement `benchmark m7-index`, `benchmark m7-pilot`, and strict artifact loading/publication.
3. Verify and index all distinct problems used by the first calibration seed in each regime.
4. Replay the slice, recording archive yield, action counts, inventory, fit-search calls/time,
   failures, and projected calibration runtime.
5. Apply the frozen Jagua gate; if triggered, stop before full calibration and build a differential
   accelerator spike. Otherwise record the defer decision.
6. Re-run focused tests and Ruff.
7. Commit `experiment: measure M7 action feasibility`.

### Task 6: Calibrate and freeze the strong baseline

**Files:**
- Modify: `yf/src/yieldforge/baseline/experiment.py`
- Modify: `yf/src/yieldforge/baseline/policies.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/baseline/test_experiment.py`
- Create: `yf/experiments/results/m7-calibration-<id>.json`
- Create: `yf/experiments/m7-frozen-baseline-v1.json`

1. Write failing tests for all-12-stream inclusion, selector score, tie-breakers, failure treatment,
   freeze identity, and evaluation-blind inputs.
2. Implement full calibration replay and selector publication.
3. Verify all 90 calibration problems against the shared candidate index.
4. Execute all five variants on all 12 calibration streams.
5. Freeze the winning policy and runtime identity.
6. Re-run focused tests and Ruff.
7. Commit `experiment: freeze M7 strong baseline`.

### Task 7: Execute frozen evaluation and close M7

**Files:**
- Modify: `yf/src/yieldforge/baseline/experiment.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/baseline/test_experiment.py`
- Create: `yf/experiments/results/m7-evaluation-<id>.json`
- Modify: `Docs/Milestones/M7 - Strong baseline.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`
- Modify: `Docs/Current Work.md`

1. Write failing tests that evaluation accepts only the frozen policy, includes all 36 streams,
   and reproduces identical results without calibration comparisons.
2. Implement frozen evaluation execution and strict result publication.
3. Verify all 198 evaluation problems against the shared candidate index.
4. Execute the frozen policy on all 36 streams twice and compare content identities.
5. Update milestone documentation with measured results, limitations, collision decision, and M8
   entry requirements.
6. Run the full Python suite, Ruff, package build, and targeted CLI regeneration checks.
7. Commit `docs: close M7 and prepare rollout oracle`.

