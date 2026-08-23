# M3 Residual Geometry Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and run a fail-closed exact-vector M3 experiment that reconciles fixed-sheet
material and measures residual differences for deterministic candidate pairs across all 203
qualifying M2 tasks.

**Architecture:** Add a solver-independent `yieldforge.residuals` package for strict geometry
contracts, transforms, overlay, classification, canonical hashing, and pair comparison. Add a
separate `yieldforge.experiments.residual_geometry` boundary that verifies M2 archives, freezes a
compact residual-blind input pack, evaluates it, and publishes a content-addressed result. Expose
only preparation and evaluation commands through the existing CLI.

**Tech Stack:** Python 3.12, Pydantic 2 strict frozen models, Shapely 2.1.2, pytest, Ruff, canonical
JSON and SHA-256.

---

Implementation occurs on the user-authorized `main` checkout. The repository's `Docs/` casing is
canonical, so this plan lives under `Docs/plans/` even though the generic workflow names
`docs/plans/`.

### Task 1: Define strict residual contracts

**Files:**
- Create: `yf/src/yieldforge/residuals/__init__.py`
- Create: `yf/src/yieldforge/residuals/contracts.py`
- Create: `yf/tests/residuals/__init__.py`
- Create: `yf/tests/residuals/test_contracts.py`

**Step 1: Write failing contract tests**

Cover these wished-for public contracts:

```python
config = ResidualGeometryConfig(part_buffer_distance=0.0, forbidden_polygons=())
assert config.process_model == "explicit_symmetric_part_buffer"

with pytest.raises(ValidationError):
    ResidualGeometryConfig(part_buffer_distance=-0.1)

rules = frozen_m0_rules()
assert tuple(rule.name for rule in rules) == ("permissive", "primary", "conservative")
```

Also require finite coordinates, closed polygon rings, nonnegative tolerances, sorted unique
component hashes, nonnegative accounting areas, and a residual observation that cannot be both
valid and carry an error code.

**Step 2: Run the tests and confirm RED**

Run:

```bash
cd yf && uv run pytest tests/residuals/test_contracts.py -q
```

Expected: collection fails because `yieldforge.residuals.contracts` does not exist.

**Step 3: Implement the minimum contracts**

Create strict frozen Pydantic models for:

- `ResidualGeometryConfig` with zero-default buffer, explicit forbidden exterior rings,
  coordinate tolerance `1e-7`, and relative area tolerance `1e-10`;
- `ResidualRule` and `ResidualRuleSet`, built from M0 `RemnantEligibility` without copying mutable
  experiment state;
- `ResidualComponentMetrics`;
- `ResidualAccounting`;
- `CandidateResidualObservation`;
- `ResidualPairComparison`; and
- `ResidualGeometryError` carrying a stable error code.

Do not persist Shapely objects. Persist only canonical hashes, numeric metrics, IDs, validity, and
errors.

**Step 4: Run GREEN and lint the new package**

```bash
cd yf && uv run pytest tests/residuals/test_contracts.py -q
cd yf && uv run ruff check src/yieldforge/residuals tests/residuals
```

Expected: all focused tests pass and Ruff reports no errors.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/residuals yf/tests/residuals
git commit -m "feat: define M3 residual geometry contracts"
```

### Task 2: Transform and validate placed geometry

**Files:**
- Create: `yf/src/yieldforge/residuals/geometry.py`
- Create: `yf/tests/residuals/test_geometry.py`

**Step 1: Write failing transform and validation tests**

Use an asymmetric triangle to prove rotation around `(0, 0)` occurs before translation. Add one
test each for missing, duplicate, and unknown placement IDs, nonfinite transforms, invalid source
polygons, material overlap, and out-of-sheet placements.

The intended API is:

```python
placed = placed_part_polygons(problem, candidate, coordinate_tolerance=1e-7)
assert placed["part-a"].equals_exact(expected_polygon, tolerance=1e-9)

with pytest.raises(ResidualGeometryError, match="overlap"):
    extract_candidate_residual(problem, overlapping_candidate, rules, config)
```

**Step 2: Run RED**

```bash
cd yf && uv run pytest tests/residuals/test_geometry.py -q
```

Expected: import or missing-function failure for the new geometry API.

**Step 3: Implement rigid transforms and fail-closed checks**

Implement explicit trigonometric rotation followed by translation, matching the workbench's
existing convention. Build Shapely polygons without repair. Reject:

- any nonfinite coordinate or transform;
- invalid, empty, or zero-area source/transformed polygons;
- demand other than one until an explicit instance-ID contract exists;
- placement ID set mismatch;
- intersections whose area exceeds the scale-aware area tolerance; and
- placed area outside the fixed stock by more than the same tolerance.

Use `box(0, 0, problem.sheet_length, problem.strip_height)` as stock.

**Step 4: Run GREEN**

```bash
cd yf && uv run pytest tests/residuals/test_geometry.py -q
```

Expected: transform and invalid-geometry tests pass.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/residuals/geometry.py yf/tests/residuals/test_geometry.py
git commit -m "feat: validate exact placed geometry"
```

### Task 3: Extract, reconcile, classify, and compare residuals

**Files:**
- Modify: `yf/src/yieldforge/residuals/geometry.py`
- Modify: `yf/tests/residuals/test_geometry.py`

**Step 1: Write failing accounting tests**

Add simple rectangles with hand-computable areas for:

- zero-buffer stock partition;
- nonzero buffers whose process regions overlap;
- forbidden zones overlapping process loss, proving disjoint priority;
- a residual with multiple components;
- a residual polygon with a hole; and
- an injected reconciliation delta exceeding tolerance.

Assert:

```python
accounted = (
    result.accounting.placed_area
    + result.accounting.process_loss_area
    + result.accounting.forbidden_loss_area
    + result.accounting.retained_area
    + result.accounting.scrap_area
)
assert accounted == pytest.approx(result.accounting.stock_area)
```

**Step 2: Run RED**

```bash
cd yf && uv run pytest tests/residuals/test_geometry.py -q
```

Expected: failures for missing extraction/accounting behavior.

**Step 3: Implement the disjoint overlay**

Use unioned geometry and differences in this order:

```python
placed = union(unbuffered_parts)
process = (union(buffered_parts) & stock) - placed
forbidden = (union(forbidden_polygons) & stock) - (placed | process)
unused = stock - (placed | process | forbidden)
```

Extract only polygonal connected components, preserve holes, and reject unexpected nonpolygonal
material output or invalid overlay geometry. Compute and enforce
`max(1e-7, stock_area * relative_area_tolerance)`.

**Step 4: Write failing classification and canonicalization tests**

Add boundary-focused fixtures for:

- exterior touch versus positive exterior-access length;
- effective width via inward buffer at half the rule threshold;
- area thresholds for all three M0 rules;
- enclosed components becoming scrap;
- ring orientation/start-point invariance; and
- pair comparison using exact fingerprints and symmetric-difference area.

**Step 5: Run RED, implement, and run GREEN**

```bash
cd yf && uv run pytest tests/residuals/test_geometry.py -q
```

Implement normalized little-endian 2D WKB hashing, component metrics, three-rule classifications,
candidate residual observations, and `compare_candidate_residuals`. Run the same command again;
expected: all geometry tests pass.

**Step 6: Run the residual package suite and commit**

```bash
cd yf && uv run pytest tests/residuals -q
cd yf && uv run ruff check src/yieldforge/residuals tests/residuals
git add yf/src/yieldforge/residuals yf/tests/residuals
git commit -m "feat: reconcile and classify residual geometry"
```

### Task 4: Verify M2 archives and freeze the M3 input pack

**Files:**
- Create: `yf/src/yieldforge/experiments/residual_geometry.py`
- Create: `yf/tests/experiments/test_residual_geometry.py`

**Step 1: Write failing archive verification tests**

Build miniature `CandidateArchive` directories for four seeds and a synthetic canonical M2 result.
Assert the loader reconstructs `CandidateBatch`, recomputes `batch_content_hash`, checks manifest
candidate count, binds selected job IDs and batch hashes, and rejects a symlink, malformed JSONL,
unknown candidate, tampered placement, or mismatched projected problem.

**Step 2: Run RED**

```bash
cd yf && uv run pytest tests/experiments/test_residual_geometry.py -q
```

Expected: missing `yieldforge.experiments.residual_geometry`.

**Step 3: Implement a bounded archive loader**

Read regular files only, cap manifest and JSONL byte sizes, parse each candidate strictly, rebuild
`CandidateBatch`, and compare all hashes and bindings to `GeometryConfirmationResult`. Never use
the unverified directory name as evidence.

**Step 4: Write failing residual-blind selection tests**

Construct candidates across seeds with duplicated IDs and shuffled archive/report order. Verify:

- best length is computed across all ordinary candidates;
- only the frozen 0.5% envelope is eligible;
- duplicate IDs with identical content are deduplicated;
- conflicting duplicate IDs fail;
- selection is the first two distinct IDs after `(width, candidate_id)` sorting;
- changing residual geometry code cannot affect selection; and
- a task with fewer than two eligible distinct candidates is rejected.

**Step 5: Implement content-addressed input contracts and publisher**

Add strict models for selected candidate evidence, task pairs, and `M3ResidualInputPack`. Bind the
pack to the M2 result ID/SHA, M0 contract ID/SHA, Shapely version, zero primary buffer, empty
forbidden zones, selection rule, all archive hashes, and exactly 203 sorted task pairs. Derive
`yfgi-<24 hex>` and `sha256:<64 hex>` from semantic JSON. Publish immutably with canonical encoding.

**Step 6: Run GREEN and commit**

```bash
cd yf && uv run pytest tests/experiments/test_residual_geometry.py -q
cd yf && uv run ruff check src/yieldforge/experiments/residual_geometry.py tests/experiments/test_residual_geometry.py
git add yf/src/yieldforge/experiments/residual_geometry.py yf/tests/experiments/test_residual_geometry.py
git commit -m "feat: freeze M3 residual input evidence"
```

### Task 5: Evaluate pairs and publish the canonical M3 result

**Files:**
- Modify: `yf/src/yieldforge/experiments/residual_geometry.py`
- Modify: `yf/tests/experiments/test_residual_geometry.py`

**Step 1: Write failing evaluator tests**

Create small valid and invalid task pairs. Assert the evaluator records every task in order,
captures stable error codes instead of dropping failed tasks, recomputes all summary counts, and
sets the technical decision to pass only when the registered population is complete, every pair
is valid and reconciled, and at least one exact residual fingerprint differs.

**Step 2: Run RED**

```bash
cd yf && uv run pytest tests/experiments/test_residual_geometry.py -q
```

Expected: missing evaluator/result behavior.

**Step 3: Implement evaluation and canonical validation**

Add `M3TaskResult`, `M3Summary`, and `M3ResidualGeometryResult`. Persist per-rule accounting and
comparison diagnostics, exact-difference and classification-difference counts, failures, decision,
and the bounded claim ceiling. Recompute the full summary and content identity when loading.

**Step 4: Test tamper rejection and immutable publication**

Add tests changing one component hash, accounting value, summary count, input binding, or result
identity. Each must fail strict validation. Publishing the same bytes twice is allowed; different
bytes at the same path are rejected.

**Step 5: Run GREEN and commit**

```bash
cd yf && uv run pytest tests/experiments/test_residual_geometry.py -q
cd yf && uv run pytest tests/residuals tests/experiments/test_residual_geometry.py -q
git add yf/src/yieldforge/experiments/residual_geometry.py yf/tests/experiments/test_residual_geometry.py
git commit -m "feat: evaluate M3 residual geometry"
```

### Task 6: Add CLI commands and execute the 203-task experiment

**Files:**
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/test_cli.py`
- Create: `yf/experiments/results/residual-geometry-input-<input-id>.json` (generated)
- Create: `yf/experiments/results/residual-geometry-result-<result-id>.json` (generated)
- Modify: `yf/tests/experiments/test_residual_geometry.py`

**Step 1: Write failing CLI tests**

Add parser/handler tests for:

```text
yieldforge experiments prepare-residual-geometry
yieldforge experiments evaluate-residual-geometry
```

The prepare command requires the canonical M2 result, archive root, M0 contract, and output
directory. The evaluate command requires the frozen input pack, M0 contract, and output directory.

**Step 2: Run RED, implement the handlers, and run GREEN**

```bash
cd yf && uv run pytest tests/test_cli.py -q
```

Expected RED: parser rejects the new commands. Add thin handlers calling the experiment module,
then rerun; expected GREEN.

**Step 3: Run all focused tests before real data**

```bash
cd yf && uv run pytest tests/residuals tests/experiments/test_residual_geometry.py tests/test_cli.py -q
cd yf && uv run ruff check src/yieldforge tests/residuals tests/experiments/test_residual_geometry.py tests/test_cli.py
```

Expected: all focused tests and lint pass.

**Step 4: Prepare the real input pack**

From `yf/` run:

```bash
uv run yieldforge experiments prepare-residual-geometry \
  --m0 experiments/m0-contract-v1.json \
  --confirmation-result experiments/results/pure-geometry-confirmation-yfgfr-47d42952e0003154baceee02.json \
  --archive-root var/experiments/yfgp-392644d98bb7035fdc218512-confirmation-run-01/workbench/candidate-archives \
  --output experiments/results
```

Expected: one canonical `residual-geometry-input-yfgi-*.json` covering 203 tasks and 406 selected
candidates.

**Step 5: Write the committed-input test and verify RED/GREEN**

Pin the generated filename and identities in a test. First run it before staging the artifact path
to prove any wrong identity fails, then correct the expected identity and rerun:

```bash
cd yf && uv run pytest tests/experiments/test_residual_geometry.py::test_committed_m3_input_is_canonical -q
```

Expected final state: PASS.

**Step 6: Evaluate the real input pack**

```bash
uv run yieldforge experiments evaluate-residual-geometry \
  --m0 experiments/m0-contract-v1.json \
  --input experiments/results/residual-geometry-input-yfgi-<id>.json \
  --output experiments/results
```

Expected: 203 task results, no dropped failures, and one canonical
`residual-geometry-result-yfgr-*.json`.

**Step 7: Add the committed-result validation test**

Pin the result filename and require canonical loading, summary recomputation, exact 203-task
coverage, M2/M0/input bindings, and matching result identity. Run it and expect PASS.

**Step 8: Commit code and generated evidence**

```bash
git add yf/src/yieldforge/cli.py yf/tests/test_cli.py yf/tests/experiments/test_residual_geometry.py yf/experiments/results
git commit -m "data: publish M3 residual geometry result"
```

### Task 7: Explain the result, update the roadmap, and verify the repository

**Files:**
- Modify: `Docs/Milestones/M3 - Residual geometry truth.md`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`

**Step 1: Read the canonical result and write only supported conclusions**

Report exact task counts, failures, reconciliation maximum, exact residual-difference proportion,
classification-difference proportions under all three rules, symmetric-difference distribution,
and concentration. State that the primary used zero source-faithful process buffer and that M3
does not establish reuse, savings, ROI, or physical manufacturability.

**Step 2: Update milestone state**

If and only if the frozen technical gate passes, mark M3 passed and M4 next. Otherwise leave M3
open with the exact failing gate. Do not authorize M4 implementation in this task.

**Step 3: Run fresh focused and full verification**

```bash
cd yf && uv run pytest tests/residuals tests/experiments/test_residual_geometry.py tests/test_cli.py -q
cd yf && uv run pytest -q
cd yf && uv run ruff check src tests
cd yf && uv build
git diff --check
git status --short --branch
```

Also run the repository's existing frontend verification commands if the root package exposes
them. Record exact pass/fail/skip counts from fresh output.

**Step 4: Commit documentation**

```bash
git add 'Docs/Milestones/M3 - Residual geometry truth.md' 'Docs/Current Work.md' 'Docs/Milestones/Milestone Roadmap.md'
git commit -m "docs: record M3 residual geometry findings"
```

**Step 5: Final audit**

Re-run the full verification command after the documentation commit, inspect `git log -n 8`, and
confirm the worktree contains no unintended files. Report the exact result IDs, semantic SHA-256
values, commit IDs, verification evidence, limitations, and the bounded M4 next step.
