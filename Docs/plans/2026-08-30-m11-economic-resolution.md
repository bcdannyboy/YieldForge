# M11 Economic Resolution Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Produce the first valid, auditable estimate of YieldForge's modeled net-cost savings and stop with either `INSUFFICIENT_ECONOMIC_VALUE` or `RETAIN_FOR_BOUNDED_REAL_PILOT`.

**Architecture:** Preserve the failed M11 artifact and reuse the authenticated Gate 3 execution engine under a new repair-lineage economic-resolution protocol. Fix the non-economic floating-point producer defect, replace the multi-gigabyte monolith with per-attempt compact receipts plus compressed content-addressed sidecars, and execute calibration, validity, and LOCo-first paired B/F/K measurement. Only execute Lectra, adverse, or forecast-dependent work when the preceding economic gate survives.

**Tech Stack:** Python 3.12, Pydantic v2, Shapely/GEOS, NumPy PCG64 bootstrap, pytest, canonical JSON, deterministic gzip, Jupyter.

---

## Decision contract

The new protocol answers a narrower question than the old M11 investment-process verdict: does the modeled system reduce cumulative material cost enough to justify a bounded real-world pilot?

- `B`: frozen strong baseline net cost.
- `F`: full-future oracle net cost; `100 * (B - F) / B` is maximum modeled savings.
- `K`: known-only causal executor net cost; `100 * (B - K) / B` is directly deployable no-forecast savings.
- Net cost remains purchases + storage + handling - scrap proceeds - terminal inventory credit.
- Central full-future gate remains: mean savings at least 2.5%, mean unknown-future contribution at least 1.5 points, lower paired-bootstrap bound above zero, median above zero, and more than half of streams positive.
- Causal product gate: mean B-versus-K savings at least 1.5% with lower paired-bootstrap bound above zero, median above zero, and more than half of streams positive.
- Adverse causal gate: mean B-versus-K savings at least 1.5% with lower paired-bootstrap bound above zero.
- A valid Red full-future result is terminal economic insufficiency because a deployable system cannot exceed the ideal-information arm under the frozen action catalog.
- A Green causal result authorizes only a bounded real-world pilot. It does not authorize productization.
- If F is Green but K is Red, implement and test a time-causal forecast arm D only then; require mean deployable savings at least 1.5%, lower bound above zero, and at least 50% capture of full-future savings.
- Translate percentages to a commercial threshold without inventing factory spend: break-even annual addressable material spend = annual product TCO / deployable savings fraction.

### Task 1: Repair exact material reconciliation

**Files:**
- Modify: `yf/src/yieldforge/baseline/geometry.py:516-632`
- Test: `yf/tests/baseline/test_geometry.py`
- Test: `yf/tests/realistic_falsification/test_gate3_backend_impl.py`

**Step 1: Write the failing numeric regression**

Add a focused test that calls a private reconciliation helper through the module and uses the first observed LOCo failure:

```python
def test_material_reconciliation_uses_validator_addition_order() -> None:
    categories = (3079.4000000000005, 0.0, 1048.3, 215.10000000000008)
    expected = abs(4342.8 - (((categories[0] + categories[1]) + categories[2]) + categories[3]))

    assert geometry_module._material_reconciliation_delta(4342.8, categories) == expected
    assert expected == 9.094947017729282e-13
```

Parameterize the remaining observed one-to-two-ULP cases if their exact inputs are available from the preserved artifact. Do not loosen tolerance.

**Step 2: Verify RED**

Run: `uv run pytest tests/baseline/test_geometry.py::test_material_reconciliation_uses_validator_addition_order -q`

Expected: FAIL because `_material_reconciliation_delta` does not exist.

**Step 3: Implement the minimal producer helper**

```python
def _material_reconciliation_delta(
    parent_area: float,
    category_areas: tuple[float, float, float, float],
) -> float:
    placed, process_loss, retained, scrap = category_areas
    accounted = placed + process_loss + retained + scrap
    return abs(float(parent_area) - accounted)
```

Use the helper in `consume_layout`. Preserve all category areas, tolerance, geometry, lineage, and economic ledger behavior.

**Step 4: Verify GREEN and the real path**

Run the focused numeric test, existing baseline geometry/action suites, and a real previously failing LOCo stream smoke. The real smoke must complete all 24 events under each of the six registered policies and produce reconciled ledgers. Record the stream ID and costs; do not publish them as confirmation evidence.

**Step 5: Bind repair lineage and commit**

Add an explicit accounting semantic identifier to the new economic-resolution manifest, not to the failed artifact. Commit only the source and tests.

### Task 2: Add compact, resumable economic evidence

**Files:**
- Create: `yf/src/yieldforge/realistic_falsification/economic_resolution.py`
- Create: `yf/src/yieldforge/realistic_falsification/economic_evidence_store.py`
- Create: `yf/tests/realistic_falsification/test_economic_resolution.py`
- Create: `yf/tests/realistic_falsification/test_economic_evidence_store.py`
- Modify: `yf/src/yieldforge/realistic_falsification/gate3_backend_impl.py`
- Test: `yf/tests/realistic_falsification/test_gate3_backend_impl.py`

**Step 1: Write failing receipt/store tests**

Specify frozen Pydantic receipts for calibration, validity, and central cells. A calibration receipt must include roots, corpus/stream/policy, observation ID/SHA, complete cost ledger, sheet openings, source lineage, and status. A central receipt must additionally include B/F/K costs, full-future savings, unknown-future contribution, causal B-versus-K savings, candidate/config/tie parity flags, and cell ID/SHA.

Test that:

- semantic IDs change when any cost or binding changes;
- deterministic gzip uses `mtime=0`;
- sidecar SHA and byte size reconcile;
- publication is atomic and refuses overwrite with different bytes;
- a manifest can resume after any completed attempt;
- a released backend stream drops its full cell/projection cache without affecting other streams.

**Step 2: Verify RED**

Run both new test modules. Expected: FAIL because the modules and cache-release behavior do not exist.

**Step 3: Implement the minimum store and receipts**

Validate each full Gate 3 observation/cell in memory, write its canonical bytes as a deterministic compressed content-addressed sidecar, then retain only the compact receipt in the stage manifest. Use write-to-temporary-file plus `os.replace`, `fsync`, and immutable collision checks. Preserve failed attempts as compact failure receipts.

**Step 4: Implement cache release**

Add a narrow method on `AdapterGate3Backend` that removes only the completed stream's central/projection cache entries. Keep existing repeated-call cache semantics until explicitly released.

**Step 5: Verify and commit**

Run the new store tests plus `test_gate3_backend_impl.py`. Commit the compact evidence layer separately.

### Task 3: Reuse authenticated calibration successes and finish calibration

**Files:**
- Create: `yf/tools/run_m11_economic_resolution.py`
- Modify: `yf/src/yieldforge/realistic_falsification/economic_resolution.py`
- Test: `yf/tests/realistic_falsification/test_economic_resolution.py`

**Step 1: Write failing streaming-extraction tests**

Implement tests around a chunked JSON-array iterator that can extract `result.calibration_attempts` one object at a time, including chunk boundaries, braces and escaped quotes inside strings, success rows, and failure rows. It must never load the 2.27 GB source artifact as one object.

**Step 2: Verify RED**

Run the extraction tests. Expected: FAIL because the iterator is absent.

**Step 3: Implement strict legacy-success reuse**

Stream the preserved failed artifact. Strictly validate each successful `Gate3CalibrationAttempt` and convert it to a compact receipt. Reuse it only when:

- all roots and registered identities match;
- the observation itself strictly validates;
- it was successful under the old producer, which proves its persisted delta already equals the validator-order delta;
- its receipt is labeled `legacy_success_output_equivalent`.

Never reuse a failed attempt or impute its cost.

**Step 4: Execute only the missing calibration attempts**

Authenticate Gate 1, Gate 2, and Gate 3 parents once; build the repaired backend; execute the 36 previously failed LOCo attempts; validate and publish each receipt/sidecar immediately. If equivalence cannot be proved mechanically, rerun all 96 attempts instead.

**Step 5: Freeze both baselines from compact receipts**

Call the existing deterministic selector with the complete eight-stream costs and sheet openings for all six policies. Require zero invalid streams. Publish the two baseline freezes and a complete 96-attempt manifest.

**Step 6: Verify readback and commit**

Fresh-read the manifest and every referenced receipt/sidecar. Commit the runner and tests; leave large run artifacts untracked.

### Task 4: Execute validity controls

**Files:**
- Modify: `yf/src/yieldforge/realistic_falsification/economic_resolution.py`
- Modify: `yf/tools/run_m11_economic_resolution.py`
- Test: `yf/tests/realistic_falsification/test_economic_resolution.py`

**Step 1: Write failing stage-transition tests**

Require calibration completeness before validity. Test valid, diagnosis-required, invalid, exception, interrupted, and resume branches. No invalid control may be interpreted economically.

**Step 2: Verify RED, then implement**

Execute the existing frozen 6 hard-null / 40 shuffled-twin / 12 exact-audit controls. Strictly validate the full receipt, publish it as a compressed sidecar, and retain a compact census of every control ID/SHA/pass flag plus no-signal summaries and status.

**Step 3: Verify and commit**

Fresh-read the validity sidecar. If status is not `valid`, diagnose and repair only validity defects, rerun this stage, and preserve every failed receipt. Commit only code/tests, not raw evidence.

### Task 5: Run the first economic go/no-go on LOCo

**Files:**
- Modify: `yf/src/yieldforge/realistic_falsification/economic_resolution.py`
- Modify: `yf/tools/run_m11_economic_resolution.py`
- Test: `yf/tests/realistic_falsification/test_economic_resolution.py`

**Step 1: Write failing compact-statistics tests**

Build synthetic receipt sets with known Red/Green outcomes. Reuse the existing PCG64(0), 10,000-draw, linear-type-7 paired bootstrap implementation through a receipt-compatible adapter. Independently calculate causal savings `100 * (B - K) / B` and its confidence interval, median, and positive fraction.

**Step 2: Verify RED, then implement**

Execute all 20 held-out LOCo B/F/K cells in frozen order. For each cell: strict-validate, publish sidecar, publish compact receipt, release its cache, and checkpoint. Missing or failed cells make the test invalid; never complete-case filter or impute.

**Step 3: Classify LOCo**

- If full-future central is Red, publish `INSUFFICIENT_ECONOMIC_VALUE` and stop expensive execution.
- If full-future is Green, report causal K savings and proceed.
- If causal K is Green, continue to adverse causal confirmation.
- If F is Green but K is Red, defer forecast D until both corpora central survive.

**Step 4: Verify readback and commit**

Independently recompute all metrics from compact costs, compare bit-for-bit where deterministic and tolerance-bounded where floating-point, and commit code/tests.

### Task 6: Complete central only if LOCo survives

**Files:**
- Modify: `yf/src/yieldforge/realistic_falsification/economic_resolution.py`
- Modify: `yf/tools/run_m11_economic_resolution.py`
- Test: `yf/tests/realistic_falsification/test_economic_resolution.py`

Run all 20 Lectra B/F/K cells using the same checkpointed path, then compute Lectra and equal-corpus-pool summaries. Any valid Red full-future group is `INSUFFICIENT_ECONOMIC_VALUE`. If all central groups survive, retain both full-future and causal summaries and continue conditionally.

Follow the same RED/GREEN/commit cycle as Task 5; do not duplicate statistics code.

### Task 7: Resolve the positive branch with minimum downstream work

**Files (create only if central survives):**
- Create: `yf/src/yieldforge/realistic_falsification/economic_downstream.py`
- Create: `yf/tests/realistic_falsification/test_economic_downstream.py`
- Modify: `yf/src/yieldforge/realistic_falsification/adapter.py`
- Modify: `yf/src/yieldforge/realistic_falsification/gate3_backend_impl.py`
- Modify: `yf/tools/run_m11_economic_resolution.py`

**Causal branch:** Execute fixed-adverse B/K comparisons first. A Green causal adverse result is sufficient modeled evidence for `RETAIN_FOR_BOUNDED_REAL_PILOT`; forecast D is unnecessary.

**Forecast branch:** Only if F is Green and K is Red, implement six outcome-blind forecast variants on the 16 calibration streams, select one, and execute D on 40 confirmation streams. Reuse K as the horizon-zero D0 comparator only after proving the executor/action catalog is identical. Require deployable savings at least 1.5%, lower 95% bound above zero, and at least 50% capture. Otherwise publish `INSUFFICIENT_ECONOMIC_VALUE`.

Use TDD for every new branch and preserve all adverse/forecast failures. Do not implement legacy support/operability material unless the minimum economic verdict depends on it.

### Task 8: Independently validate and publish the verdict

**Files:**
- Create: `yf/notebooks/m11-economic-resolution.ipynb`
- Create: `Docs/Evidence/M11 - Economic resolution.md`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Milestones/M10 - Experiment and verdict.md`

**Step 1: Build the notebook**

Load only compact manifests/receipts. Distinguish observed, derived, generated, and assumed values. Recompute B/F/K savings, causal savings, uncertainty, medians, positive fractions, and all threshold flags from raw per-stream costs. Execute top-to-bottom without errors.

**Step 2: Independent validation**

Use a separate calculation path to sample-check ledgers, recompute all means and intervals, verify stream/corpus completeness, verify every referenced hash, and confirm that no calibration outcome entered confirmation selection except through the frozen policy IDs.

**Step 3: Publish the answer-first report**

State one of exactly three evidence statuses:

- `INSUFFICIENT_ECONOMIC_VALUE`: valid measured savings fail the frozen minimum.
- `RETAIN_FOR_BOUNDED_REAL_PILOT`: modeled deployable savings survive central and adverse gates; real buyer chronology/cost observation remains required.
- `ECONOMIC_VALUE_UNRESOLVED`: only if an irreparable validity or data blocker remains; do not substitute an engineering failure for an economic verdict.

Include the savings distribution, uncertainty, causal versus oracle decomposition, break-even material-spend formula/table, limitations of the cost ledger, and the exact next commercial evidence required. Explicitly keep productization unauthorized.

**Step 4: Final verification and commit**

Run the focused suites, full Python test suite in proportion to runtime, notebook execution, evidence readback, and repository checks. Request spec-compliance and code-quality review, then commit the durable report and notebook separately from code.

