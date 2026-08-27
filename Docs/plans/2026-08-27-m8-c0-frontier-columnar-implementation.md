# M8 C0 Frontier-Columnar Kernel Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the hard arm's repeated 459-by-459 rejection-certificate expansion with one
source-bound 459-by-13 frontier batch while preserving exact portable bytes, independent scalar
checking, fail-closed fallback, and sealed evaluation.

**Architecture:** Batch validated numeric queries at the event-major branch loop, where one prepared
frontier and all branch cursors coexist. A pure NumPy CPU kernel returns only ordered booleans; a
semantic wrapper differentially checks every row against the existing scalar frontier predicate and
emits a distinct compact internal capture for proven rows. Portable assembly reuses the existing
complete scalar-reference group, while the fresh checker independently validates full membership and
recomputes the scalar predicate without trusting or calling the C0 kernel.

**Tech Stack:** Python 3.12, NumPy 2.5.2 `float64`/boolean CPU arrays, pytest, Pydantic strict
contracts, existing M8 prepared-runtime/frontier/fact infrastructure, Jagua/Shapely exact fallback.

**Required workflow:** Apply `@superpowers:test-driven-development` to each behavior change and
`@superpowers:verification-before-completion` before every completion or gate claim. Keep Phase-B
commit `17a202e` and its v3 artifact immutable.

---

### Task 1: Add the pure numeric frontier batch

**Files:**

- Create: `yf/src/yieldforge/oracle/columnar.py`
- Create: `yf/tests/oracle/test_columnar.py`

**Step 1: Write the failing boundary and differential tests**

Define tests around these frozen records and API:

```python
@dataclass(frozen=True, slots=True)
class C0FrontierColumns:
    areas: tuple[float, ...]
    widths: tuple[float, ...]
    heights: tuple[float, ...]

@dataclass(frozen=True, slots=True)
class C0FrontierQuery:
    row_id: int
    material_matches: bool
    remnant_area: float
    remnant_width: float
    remnant_height: float
    area_tolerance: float
    coordinate_tolerance: float

@dataclass(frozen=True, slots=True)
class C0FrontierResult:
    row_id: int
    all_impossible: bool

def certify_frontier_impossible_batch(
    frontier: C0FrontierColumns,
    queries: tuple[C0FrontierQuery, ...],
) -> tuple[C0FrontierResult, ...]: ...
```

Cover fixed-random equality with `certify_frontier_impossible`, material mismatch, empty frontier,
query order, frontier order, exact Python booleans, and `math.nextafter` immediately below/equal/above
each area/width/height threshold. Cover tolerance-addition rounding and finite overflow to infinity.
Reject NaN/infinity inputs, NumPy scalar inputs, ints/bools in float fields, negative values,
mismatched column lengths, and non-dense/duplicate/reordered row IDs.

**Step 2: Run the focused test and confirm RED**

Run: `.venv/bin/pytest -q tests/oracle/test_columnar.py`

Expected: collection/import failure because `yieldforge.oracle.columnar` does not exist.

**Step 3: Implement the smallest pure kernel**

Validate exact Python inputs before array creation. Build only local `numpy.float64` and boolean
arrays. Use strict `numpy.greater`, boolean OR, and `numpy.all(axis=1)` under a local overflow-ignored
context. Empty frontier returns `False` for every row. Cast each output to exact Python `bool`; no
runtime objects, geometry, facts, hashes, capabilities, or caller-owned arrays cross the boundary.

**Step 4: Run GREEN and quality checks**

Run:

```bash
.venv/bin/pytest -q tests/oracle/test_columnar.py tests/oracle/test_frontier.py
.venv/bin/ruff check src/yieldforge/oracle/columnar.py tests/oracle/test_columnar.py
git diff --check
```

Expected: all pass.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/oracle/columnar.py yf/tests/oracle/test_columnar.py
git commit -m "feat: add M8 C0 frontier batch kernel"
```

### Task 2: Add a proof-owned scalar-query batch wrapper

**Files:**

- Modify: `yf/src/yieldforge/oracle/compiled.py`
- Modify: `yf/src/yieldforge/oracle/certificates.py`
- Modify: `yf/src/yieldforge/oracle/prepared.py`
- Modify: `yf/src/yieldforge/oracle/profiling.py`
- Modify: `yf/tests/oracle/test_compiled.py`
- Modify: `yf/tests/oracle/test_fact_capture.py`

**Step 1: Write failing semantic-wrapper tests**

Require one immutable internal row binding per branch/remnant and one numeric query per supported
row. Measurements, material, fit configuration, candidate membership, and the 13-member frontier
must come from the prepared proof-owned runtime/lease. Require exact row count/order/identity and
exact `bool` output before any branch cursor changes. Corrupt, missing, duplicated, or reordered
kernel output must raise an integrity error rather than fall back.

Add tests for equality-at-tolerance survivors, material mismatch, multi-item deltas, incomplete
rejection archives, transient source mutation, and persistent source drift. Multi-item branches are
compact-eligible only when every changed item is proven impossible; unsupported/survivor branches
remain explicitly unresolved.

**Step 2: Run the focused tests and confirm RED**

Run: `.venv/bin/pytest -q tests/oracle/test_compiled.py tests/oracle/test_fact_capture.py -k 'columnar or prepared_snapshot'`

Expected: new columnar-wrapper tests fail because the wrapper and row-binding type do not exist.

**Step 3: Implement the semantic wrapper**

Expose the minimum private prepared accessor needed for an immutable scalar query: complete frontier,
prepared remnant measurement, proof-owned material, and fit configuration. Build frontier columns
once per exact problem/candidate-set/material/config partition. Run the C0 batch, then independently
compare every supported result with `certify_frontier_impossible`; any mismatch raises. Record a
`frontier_columnar_batch` profile phase. Add kernel mode/identity to the prepared-context fingerprint,
but do not alter historical Phase-B runtime/profile contracts.

**Step 4: Run GREEN and mutation checks**

Run:

```bash
.venv/bin/pytest -q tests/oracle/test_compiled.py tests/oracle/test_fact_capture.py
.venv/bin/ruff check src/yieldforge/oracle/compiled.py src/yieldforge/oracle/certificates.py src/yieldforge/oracle/prepared.py src/yieldforge/oracle/profiling.py tests/oracle/test_compiled.py tests/oracle/test_fact_capture.py
git diff --check
```

Expected: all pass, including existing prepared snapshot/capability/Jagua cleanup tests.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/oracle/compiled.py yf/src/yieldforge/oracle/certificates.py yf/src/yieldforge/oracle/prepared.py yf/src/yieldforge/oracle/profiling.py yf/tests/oracle/test_compiled.py yf/tests/oracle/test_fact_capture.py
git commit -m "feat: bind M8 C0 queries to prepared evidence"
```

### Task 3: Integrate one event-major producer batch

**Files:**

- Modify: `yf/src/yieldforge/oracle/certificates.py`
- Modify: `yf/src/yieldforge/oracle/sparse.py`
- Modify: `yf/tests/oracle/test_fact_capture.py`
- Modify: `yf/tests/oracle/test_sparse.py`

**Step 1: Write failing producer bypass tests**

At `_capture_prepared_unchecked_traversal`, require one batch call per common event regardless of
action count. For all-impossible rows, patch `_compile_prepared_translation_rejections`,
`_calculate_influence_source`, and `_evidence_sha256` to fail: the compact path must not call them.
For mixed batches, require each survivor/unsupported row to call `_advance_unchecked_branch` exactly
once. Malformed output must fail before any branch state mutation.

**Step 2: Run the focused tests and confirm RED**

Run: `.venv/bin/pytest -q tests/oracle/test_fact_capture.py tests/oracle/test_sparse.py -k 'columnar or compact_no_fit'`

Expected: the new producer tests fail because traversal still expands certificates per branch.

**Step 3: Implement a distinct compact internal capture**

Add `M8UncheckedCompactNoFitInfluenceCapture` rather than fabricating the existing 459-entry legacy
capture. It binds event, direction, remnant/item, delta, problem/candidate-set/config identity, and
the state/common commitments needed by portable assembly. Under the existing common-source guard,
prepare and validate the whole batch, then apply the common frozen action exactly once for each
proven branch. Leave state-rejoin, selected-stock removal, survivor, unsupported, exact-search,
policy-dominance, fallback, and attempted-influence ordering on the unchanged path.

Keep the legacy path available as a private differential reference; production portable scoring
selects C0 explicitly.

**Step 4: Run GREEN and regression checks**

Run:

```bash
.venv/bin/pytest -q tests/oracle/test_fact_capture.py tests/oracle/test_sparse.py tests/oracle/test_certificates.py
.venv/bin/ruff check src/yieldforge/oracle/certificates.py src/yieldforge/oracle/sparse.py tests/oracle/test_fact_capture.py tests/oracle/test_sparse.py
git diff --check
```

Expected: all pass with existing exact/fallback behavior unchanged.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/oracle/certificates.py yf/src/yieldforge/oracle/sparse.py yf/tests/oracle/test_fact_capture.py yf/tests/oracle/test_sparse.py
git commit -m "feat: batch M8 C0 producer traversal"
```

### Task 4: Assemble byte-identical compact portable evidence

**Files:**

- Modify: `yf/src/yieldforge/oracle/factored.py`
- Modify: `yf/tests/oracle/test_factored_generator.py`

**Step 1: Write failing identity and cache tests**

Require C0 and legacy generation to emit identical semantic bytes, bundle hash, decision, root
commitments, costs, classifications, and state hashes across the exhaustive certificate cases.
Require each true compact group to contain the complete sorted scalar-ref set, with no search or
competitor evidence. Instrument scalar construction: complete candidate membership is resolved once
per exact partition, not once per branch.

**Step 2: Run the focused tests and confirm RED**

Run: `.venv/bin/pytest -q tests/oracle/test_factored_generator.py -k 'columnar or compact or semantic_bytes'`

Expected: new compact-capture and partition-cache tests fail.

**Step 3: Extend `_FactStore` and portable assembly**

Cache the canonical complete scalar-reference tuple by semantic runtime/problem/candidate-set/config
partition. Convert a compact capture directly into the existing
`M8CandidateScalarGroupEvidenceV2(direction, remnant_id, complete_refs, True)`. Do not change the
portable schema. Legacy captures continue through `_rejection_evidence`; survivor/exact evidence is
untouched.

**Step 4: Run GREEN and strict-roundtrip checks**

Run:

```bash
.venv/bin/pytest -q tests/oracle/test_factored_generator.py tests/oracle/test_facts.py
.venv/bin/ruff check src/yieldforge/oracle/factored.py tests/oracle/test_factored_generator.py
git diff --check
```

Expected: all pass and legacy/C0 semantic bytes are exactly equal.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/oracle/factored.py yf/tests/oracle/test_factored_generator.py
git commit -m "feat: assemble compact M8 C0 facts"
```

### Task 5: Give the fresh checker an independent scalar fast path

**Files:**

- Modify: `yf/src/yieldforge/oracle/compiled.py`
- Modify: `yf/src/yieldforge/oracle/fact_checker.py`
- Modify: `yf/tests/oracle/test_fact_checker.py`

**Step 1: Write failing independent-checker tests**

For compact `all_candidates_impossible=True`, patch the C0 kernel and both full certificate-compilation
entrypoints to fail; checking must still pass. Two groups in one partition must call
`_validate_influence_scalar` exactly `candidate_count`, not `group_count * candidate_count`, while
calling scalar `certify_frontier_impossible` once per row. Missing, extra, duplicated, reordered,
cross-partition, or coherently rehashed scalar evidence must reject as
`influence_rejection_mismatch`. A false compact group, unsupported archive, or nonportable path must
retain the existing exact route and telemetry.

**Step 2: Run the focused tests and confirm RED**

Run: `.venv/bin/pytest -q tests/oracle/test_fact_checker.py -k 'compact or columnar or influence_scalar'`

Expected: the no-expansion and membership-once tests fail because compilation currently precedes
compact-group detection.

**Step 3: Implement compact-first independent verification**

Detect a true compact group before constructing candidate certificates. Obtain the proof-owned
prepared rejection problem and remnant scalar query; validate the complete scalar-ref/candidate
bijection once and cache its exact canonical tuple in `_FullCheckState`. Later groups must equal that
tuple exactly. Call the existing scalar `certify_frontier_impossible`; false results reject rather
than fall through. Do not import, call, or trust the C0 kernel for this proof. Leave compact-false,
expanded, unsupported, and `require_portable=False` paths unchanged.

**Step 4: Run GREEN and mutation suites**

Run:

```bash
.venv/bin/pytest -q tests/oracle/test_fact_checker.py tests/oracle/test_fact_capture.py tests/oracle/test_compiled.py
.venv/bin/ruff check src/yieldforge/oracle/compiled.py src/yieldforge/oracle/fact_checker.py tests/oracle/test_fact_checker.py
git diff --check
```

Expected: all pass; compact proof is scalar-independent from the producer batch.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/oracle/compiled.py yf/src/yieldforge/oracle/fact_checker.py yf/tests/oracle/test_fact_checker.py
git commit -m "feat: verify M8 C0 groups without expansion"
```

### Task 6: Add separate C0 source/runtime evidence

**Files:**

- Create: `yf/src/yieldforge/oracle/c0_evidence.py`
- Create: `yf/tests/oracle/test_c0_evidence.py`
- Modify: `yf/src/yieldforge/oracle/experiment.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/oracle/test_experiment.py`
- Modify: `yf/tests/test_cli.py`

**Step 1: Write failing evidence-contract tests**

Define a new content-addressed C0 profile schema; do not reinterpret Phase-B v3. Bind the official
Gate-3 identity, exact output/repeat identities, source-attested fresh workers, zero fallback/mismatch,
numeric shape, action-traversal timings, evaluation false, and six-cell authorization false. Require
controller and workers to share a new `yieldforge-m8-c0-runtime-v1` identity. Test strict JSON,
tampering, NumPy version/native-extension drift, source drift, output races, and stale-path refusal.

**Step 2: Run the focused tests and confirm RED**

Run: `.venv/bin/pytest -q tests/oracle/test_c0_evidence.py tests/oracle/test_experiment.py tests/test_cli.py -k 'c0'`

Expected: the new contract/command/runtime tests fail because C0 evidence does not exist.

**Step 3: Implement the distinct evidence identity**

Compose the historical Gate-3 runtime ID/hash with NumPy distribution/module version, SHA-256 of
the loaded NumPy core native extension, `float64`/bool dtype descriptors, byte order, and machine
architecture. Derive the kernel identity through `source_tree_implementation_identity` with
`columnar.py` and `frontier.py` as named primary sources. Reuse the hardened fresh-worker,
source-mirror, handoff, strict-load, and immutable-publisher machinery. Record row count 459,
frontier width 13, pair count 5,967, numeric mismatches zero, exact-path/fallback counts, producer
and checker action-traversal times, and their combined time.

**Step 4: Run GREEN and historical-contract regressions**

Run:

```bash
.venv/bin/pytest -q tests/oracle/test_c0_evidence.py tests/oracle/test_profile_evidence.py tests/oracle/test_experiment.py tests/test_cli.py
.venv/bin/ruff check src/yieldforge/oracle/c0_evidence.py src/yieldforge/oracle/experiment.py src/yieldforge/cli.py tests/oracle/test_c0_evidence.py tests/oracle/test_experiment.py tests/test_cli.py
git diff --check
```

Expected: all pass; the historical Gate-3 and Phase-B identities remain unchanged.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/oracle/c0_evidence.py yf/src/yieldforge/oracle/experiment.py yf/src/yieldforge/cli.py yf/tests/oracle/test_c0_evidence.py yf/tests/oracle/test_experiment.py yf/tests/test_cli.py
git commit -m "feat: bind M8 C0 profile evidence"
```

### Task 7: Verify and execute the C0 gate

**Files:**

- Create: `yf/experiments/results/m8-c0-frontier-columnar-regime-shift-v1.json`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Milestones/M8 - Rollout oracle.md`
- Modify: `Docs/plans/2026-08-27-m8-post-gate3-algorithmic-redesign.md`

**Step 1: Run focused, oracle, and full verification**

Run:

```bash
.venv/bin/pytest -q tests/oracle/test_columnar.py tests/oracle/test_compiled.py tests/oracle/test_fact_capture.py tests/oracle/test_factored_generator.py tests/oracle/test_fact_checker.py tests/oracle/test_c0_evidence.py tests/oracle/test_experiment.py tests/test_cli.py
.venv/bin/pytest -q tests/oracle
.venv/bin/pytest -q
.venv/bin/ruff check src/yieldforge tests
git diff --check
```

Expected: all pass; only the three previously documented environment skips may remain.

**Step 2: Run the sealed hard-arm C0 profile**

Use the canonical amended M7 freeze `m7-frozen-baseline-v1.0.1.json`, the official Gate-3 artifact,
the same three frozen candidate-archive roots, the frozen Jagua binary, fresh worker processes, and
an absent immutable output path. Do not modify `src/yieldforge` while the source-attested run is
active.

**Step 3: Independently verify the artifact**

Strict-load canonical JSON. Externally recompute content, complete source-tree/kernel, C0 runtime,
NumPy native-extension, controller/worker, official Gate-3, bundle/decision, repeat-output, numeric
shape, fallback/mismatch, evaluation, and six-cell fields. Require exactly 459 rows, 13 frontier
entries, 5,967 predicates, zero mismatch, and exact official identities.

**Step 4: Apply the C0 go/no-go**

Go to a wider compiled boundary only if combined producer/checker action traversal is at most
`8.566559` seconds (at least `20x` versus Phase-B v3's `171.331178` seconds). Otherwise record C0 as
a performance no-go. Either outcome is calibration-only and does not pass the full
`30.261404`-second Phase-C two-probe gate, authorize six-cell calibration, open evaluation, or prove
oracle advantage/savings.

**Step 5: Update the roadmap and commit evidence**

Record exact IDs, hashes, timings, verification, limitations, decision, and next authorized step in
the three roadmap documents. Commit the immutable artifact and documentation without overwriting
Phase-B evidence.

