# M8 Distributed-Exact Calibration Gate Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete the calibration-only M8 certificate gate with exact multi-process execution and
deterministic, fail-closed proof reassembly.

**Architecture:** Execute one complete cell per CPU process, close the generator pool, verify all
proofs in a fresh process pool, then run matched action-level certificate, checker, and reference
audit phases over the identical frozen keys. Preserve the existing proof semantics and use measured
distributed wall time for the held-out projection.

**Tech Stack:** Python 3.12, owned `multiprocessing` workers, Pydantic v2, pytest, uv, Ruff.

---

### Task 1: Freeze the distributed result contract

**Files:**
- Modify: `yf/src/yieldforge/oracle/experiment.py`
- Modify: `yf/tests/oracle/test_experiment.py`

1. Add failing tests for the v3 schema, measured process bounds, wall-time reconciliation, and a
   projection derived from distributed wall time without an additional worker divisor.
2. Run the focused tests and confirm that they fail because the v2 contract only permits one
   measured process.
3. Add the minimal v3 fields and validation.
4. Run the focused tests and commit.

### Task 2: Add deterministic worker phase results and reassembly

**Files:**
- Modify: `yf/src/yieldforge/oracle/experiment.py`
- Modify: `yf/tests/oracle/test_experiment.py`

1. Add failing tests proving that reassembly accepts shuffled cell completion order but rejects a
   missing regime, duplicate action, mismatched proof identity, or invalid checker result.
2. Implement top-level picklable generator, checker, and audit worker functions plus deterministic
   regime-ordered reassembly.
3. Keep all private generator/checker capabilities process-local.
4. Run the focused tests and commit.

### Task 3: Execute the three distributed phases

**Files:**
- Modify: `yf/src/yieldforge/oracle/experiment.py`
- Modify: `yf/tests/oracle/test_experiment.py`
- Modify: `yf/tests/test_cli.py`

1. Add failing tests for separate generator/checker executors, fail-closed worker errors, the fixed
   canonical eight-worker configuration, and the absence of a public worker-count override.
2. Replace the sequential preflight/check/audit loop with three bounded process phases. Use a local
   one-process path only as a private test seam.
3. Record cleanup-inclusive phase wall times and preserve existing progress reporting.
4. Run focused tests and commit.

### Task 4: Verify, review, and execute the calibration gate

**Files:**
- Modify: `Docs/Milestones/M8 - Rollout oracle.md`
- Modify: `Docs/Current Work.md`
- Create only on full completion: `yf/experiments/results/m8-certificate-proof-<id>.json`

1. Run the oracle/baseline/CLI suites, Ruff, compile checks, and Git whitespace checks.
2. Review the implementation adversarially for process isolation, completeness, timing honesty,
   determinism, and evaluation leakage; fix all important findings.
3. Execute the six-cell distributed calibration gate. Do not open evaluation.
4. Strictly reload and reconcile any completed artifact, record the observed decision and timings,
   then commit. If execution does not finish, publish no artifact and document the exact remaining
   straggler before considering action sharding.

### Measured refinement after the first distributed attempt

The six-cell generator and fresh checker phases both completed, but the combined audit worker phase
reached its 1,800-second deadline. The authorized refinement splits the audit into three independently
bounded action-level phases. All three phases use the same frozen action keys and process budget;
missing or duplicate results fail closed before per-cell assembly. Full generator/checker work
remains cell-sharded because it completed and action sharding would duplicate expensive common paths.

The first split-audit execution exposed a second scheduling boundary: a phase-global 1,800-second
timer shortchanged actions queued behind the first eight workers. The supervisor now grants each
started task 1,800 seconds from its confirmed start handshake, while retaining fail-closed process
group cleanup. Audit actions are scheduled by descending observed full-generator regime time only
after sample membership is frozen, so slow actions start first without changing evidence selection.
