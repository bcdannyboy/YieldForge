# M11 Gate 3 Bounded-Catalog Repair Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Repair Gate 3 so it tests the registered bounded product algorithm without weakening strict M9 defaults or overstating geometric completeness.

**Architecture:** Add an explicit catalog policy to M9 scoring and exact search, defaulting to strict untruncated behavior. Bind a Gate-3-only bounded policy through contracts, traces, controls, and the official config, then rerun the untouched confirmation population.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, Ruff, content-addressed canonical JSON.

---

### Task 1: Freeze the revised claim

**Files:**
- Modify: `yf/src/yieldforge/realistic_falsification/gate3_contracts.py`
- Modify: `yf/tests/realistic_falsification/test_gate3_contracts.py`
- Regenerate: `yf/benchmarks/falsification/m11-gate3-config-v1.json`

1. Write a failing contract test requiring a Gate-3-specific bounded catalog
   declaration and forbidding the old `complete_no_truncation` label.
2. Run the focused test and confirm the expected assertion failure.
3. Implement the smallest frozen field change and canonical regeneration path.
4. Run the focused test and confirm it passes.

### Task 2: Add an opt-in bounded M9 policy

**Files:**
- Modify: `yf/src/yieldforge/oracle/search_validation.py`
- Modify: `yf/tests/oracle/test_search_validation.py`

1. Write a failing test proving strict default search still rejects truncation
   while explicit bounded mode completes over all discovered actions and records
   nonzero geometry-truncation telemetry.
2. Run both branches and confirm RED for bounded mode only.
3. Add the minimal explicit policy parameter; do not change the default.
4. Run focused M9 tests and confirm GREEN.

### Task 3: Bind bounded semantics through Gate 3

**Files:**
- Modify: `yf/src/yieldforge/realistic_falsification/gate3_backend.py`
- Modify: `yf/src/yieldforge/realistic_falsification/gate3_controls.py`
- Modify: `yf/src/yieldforge/realistic_falsification/confirmation.py`
- Modify corresponding focused tests under `yf/tests/realistic_falsification/`

1. Write failing tests for bounded B/F/K scoring, bounded exact audits, persisted
   truncation telemetry, and rejection of mismatched semantics.
2. Confirm each fails for the intended missing behavior.
3. Thread the explicit bounded policy through Gate 3 only and preserve all other
   fail-closed checks.
4. Run the Gate 3 focused suite and confirm GREEN.

### Task 4: Re-freeze and verify

**Files:**
- Modify only canonical artifacts whose semantic hashes depend on Gate 3 config.

1. Regenerate the Gate 3 config before executing central confirmation.
2. Run format, lint, M9, Gate 3 contract, adapter, backend, confirmation,
   controls, and runner tests.
3. Commit the repair with the original invalid control outcome documented.

### Task 5: Execute the verdict test

**Files:**
- Create: official Gate 3 result under `yf/experiments/results/`
- Create: compact verdict report under `Docs/`

1. Authenticate Gate 1, Gate 2, and the revised Gate 3 config.
2. Execute calibration and all validity controls.
3. Execute LOCo central confirmation first; abandon immediately on a valid
   threshold failure.
4. Continue to Lectra, pool, support, adverse, and deployment gates only if each
   preceding gate survives.
5. Publish and independently read back the result, then record the forced
   abandon-or-bounded-pilot verdict.

