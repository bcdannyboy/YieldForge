# M8 Distributed-Exact Calibration Gate Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete the calibration-only M8 certificate gate with exact multi-process execution and
deterministic, fail-closed proof reassembly.

**Architecture:** Execute one complete cell per CPU process, close the generator pool, verify all
proofs in a fresh process pool, then run per-regime certificate/checker audit batches and independent
single-action brute-reference tasks over identical frozen action membership. Reassemble reference
actions into their six regime vectors before reconciliation. Preserve the existing proof semantics
and use measured distributed wall time for the held-out projection.

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
bounded phases. Every phase uses the same frozen action membership and at most six measured
processes; missing or duplicate results fail closed before per-cell assembly. Full generator/checker
work remains cell-sharded because it completed and action sharding would duplicate expensive common
paths.

The first split-audit execution exposed a second scheduling boundary: a phase-global 1,800-second
timer shortchanged actions queued behind the first eight workers. The supervisor now grants each
started task 1,800 seconds from its confirmed start handshake, while retaining fail-closed process
group cleanup. Audit actions are scheduled by descending observed full-generator regime time only
after sample membership is frozen, so slow actions start first without changing evidence selection.

The slow-first action-level rerun established that task queuing was not the only cost: one isolated
certificate action itself exceeded 1,800 seconds under eight-process contention. Certificate
generation and checking therefore use six per-regime batches, sharing each regime's common geometry
and reducing simultaneous CPU pressure. Reference action isolation is reconsidered only after its
distinct two-branch batch limit is measured below.

The matched per-regime rerun cleared its generator (`1400.985884` seconds) and checker
(`1485.550346` seconds), but the sequential two-action brute reference reached one worker's
unchanged 1,800-second limit. Keep the six frozen regime batches and exact reference semantics, but
advance each batch's independent action cursors one frozen M7 event at a time. This event-major
ordering permits same-event prepared-geometry reuse without consulting certificate evidence.
Differential tests must prove equality with repeated isolated single-action replay before another
canonical calibration attempt.

The event-major rerun again reached the reference worker limit after all preceding phases passed.
Run one brute reference action per fresh task, cap the phase at the six already measured audit
processes, preserve slow-regime-first ordering, and aggregate the two exact action results back into
each frozen regime batch. Reject missing, duplicate, wrong-regime, or wrong-action results before
the existing audit assembly. Keep the certificate generator and checker per-regime because those
batches already pass and share necessary common geometry.
