# M4 Remnant Reuse Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and run a fail-closed M4 experiment that exactly fits a source-observed later-part
role into a retained M3 remnant, creates reconciled child remnants with immutable lineage, and
publishes a content-addressed possibility witness.

**Architecture:** Add a solver-independent `yieldforge.reuse` package whose geometry validator is
authoritative for explicit placements and whose deterministic search only discovers bounded
witnesses. Reuse the M3 exact-vector, canonical-hash, classification, and tolerance conventions.
Add an experiment boundary that reconstructs canonical M3 residuals, freezes source-observed
pairing/search inputs before fit results, and publishes one strictly validated artifact.

**Tech Stack:** Python 3.12, Pydantic 2, Shapely 2.1.2, pytest, Ruff, existing YieldForge canonical
JSON and CLI conventions.

**Repository rule:** Work directly on `main` as requested. Preserve unrelated changes, use the
existing `Docs/` casing, and use `UV_CACHE_DIR=/tmp/yieldforge-uv-cache` for local `uv` commands.

---

### Task 1: Define strict reuse and lineage contracts

**Files:**
- Create: `yf/src/yieldforge/reuse/__init__.py`
- Create: `yf/src/yieldforge/reuse/contracts.py`
- Create: `yf/tests/reuse/__init__.py`
- Create: `yf/tests/reuse/test_contracts.py`

**Step 1: Write the failing contract tests**

Define the wished-for public records:

```python
material = MaterialIdentity(
    material_code="assumed-uniform",
    grade="assumed-uniform",
    thickness="assumed-uniform",
    surface="assumed-uniform",
    grain="assumed-uniform",
    provenance=MaterialProvenance.ASSUMED,
)
polygon = canonical_polygon_record(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]))
root = RemnantLineage.root(
    root_stock_id="stock-fixture",
    source_candidate_id="candidate-fixture",
    source_component_sha256=polygon.polygon_sha256,
)
remnant = RemnantStock(
    remnant_id=derive_remnant_id(root, polygon, material),
    geometry=polygon,
    material=material,
    root_sheet_area=100.0,
    root_sheet_short_side=10.0,
    lineage=root,
)
assert polygon_from_record(remnant.geometry).area == 16.0
```

Require strict finite fields, positive root dimensions, SHA-256 formats, exact material fields,
generation `1` for roots, no parent for roots, and unique ordered ancestors. Add tamper tests for
WKB, polygon hash, remnant ID, duplicate ancestors, generation mismatch, root changes, and cycles.

**Step 2: Run RED**

```bash
cd yf
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest tests/reuse/test_contracts.py -q
```

Expected: collection fails because `yieldforge.reuse.contracts` does not exist.

**Step 3: Implement the minimum contracts**

Add strict frozen Pydantic models and helpers:

- `MaterialProvenance` with `observed`, `generated`, and `assumed`;
- `MaterialIdentity` with the five M0 compatibility fields plus provenance;
- `CanonicalPolygon` with normalized little-endian 2D WKB hex, SHA-256, and area;
- `RemnantLineage` with root, parent, ancestors, generation, source candidate/component bindings;
- `RemnantStock` with content-derived ID, root sheet dimensions, material, and exact geometry;
- `RemnantFitConfig` with zero-default clearance and M3 tolerances;
- `FitPlacement`, `ReuseAccounting`, `ChildRemnantSummary`, and `RemnantFitResult`;
- `ReuseGeometryError` with stable code; and
- `canonical_polygon_record`, `polygon_from_record`, `derive_remnant_id`, and `child_lineage`.

Persist only polygons, records, and values—never Shapely runtime objects inside Pydantic models.

**Step 4: Run GREEN and lint**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest tests/reuse/test_contracts.py -q
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff check src/yieldforge/reuse tests/reuse
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff format --check src/yieldforge/reuse tests/reuse
```

Expected: all contract tests pass; Ruff lint and format checks are clean.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/reuse yf/tests/reuse
git commit -m "feat: define M4 remnant reuse contracts"
```

### Task 2: Validate exact part placement in an irregular remnant

**Files:**
- Create: `yf/src/yieldforge/reuse/geometry.py`
- Create: `yf/tests/reuse/test_geometry.py`

**Step 1: Write the failing positive-placement test**

Use a concave L-shaped remnant and an asymmetric source part. Assert rotation around local `(0, 0)`
before translation and exact containment:

```python
validated = validate_fit_placement(
    remnant,
    part,
    FitPlacement(part_id=part.id, rotation=90.0, translation=(4.0, 1.0)),
    part_material=remnant.material,
    config=RemnantFitConfig(),
)
assert remnant_polygon.covers(validated.buffered_footprint)
assert validated.placed_polygon.area == pytest.approx(source_polygon.area)
```

**Step 2: Run RED**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest \
  tests/reuse/test_geometry.py::test_validates_rotation_then_translation_inside_concave_remnant -q
```

Expected: missing `yieldforge.reuse.geometry` or missing-function failure.

**Step 3: Implement strict transform and containment validation**

Implement:

```python
@dataclass(frozen=True)
class ValidatedFitPlacement:
    placement: FitPlacement
    placed_polygon: Polygon
    buffered_footprint: Polygon
    area_tolerance: float

def validate_fit_placement(...) -> ValidatedFitPlacement: ...
```

The validator must:

- decode and verify the remnant polygon;
- require `part.demand == 1`;
- require exact material compatibility fields;
- require the rotation to match one source-allowed rotation within coordinate tolerance;
- reject nonfinite translation or source geometry;
- rotate then translate using the M2/M3 convention;
- use an explicit mitre buffer only when clearance is positive;
- reject empty, invalid, nonpolygonal, or nonpositive geometry;
- require the footprint difference outside the remnant to be within
  `max(coordinate_tolerance, remnant_area * relative_area_tolerance)`; and
- never repair geometry.

**Step 4: Write failing adversarial tests**

Add one focused test for each behavior:

- part area and bounding box fit, but the proposed placement crosses a concavity;
- placement crosses a remnant hole;
- zero-clearance boundary touch passes;
- the same placement fails with positive clearance;
- unlisted rotation fails;
- material mismatch fails;
- nonfinite translation fails; and
- invalid source/remnant polygon fails during strict loading.

**Step 5: Run GREEN, full reuse tests, and lint**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest tests/reuse/test_geometry.py -q
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest tests/reuse -q
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff check src/yieldforge/reuse tests/reuse
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff format --check src/yieldforge/reuse tests/reuse
```

Expected: all exact-placement fixtures pass.

**Step 6: Commit**

```bash
git add yf/src/yieldforge/reuse/geometry.py yf/tests/reuse/test_geometry.py
git commit -m "feat: validate exact remnant placements"
```

### Task 3: Generalize residual classification and create child remnants

**Files:**
- Modify: `yf/src/yieldforge/residuals/geometry.py`
- Modify: `yf/tests/residuals/test_geometry.py`
- Modify: `yf/src/yieldforge/reuse/geometry.py`
- Modify: `yf/tests/reuse/test_geometry.py`

**Step 1: Write a failing arbitrary-container classification test**

Add a residual test whose container is a concave polygon rather than a rectangular sheet. Require
component access to be measured against the supplied parent-container boundary while area and width
thresholds remain relative to explicit root-sheet dimensions.

**Step 2: Run RED**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest \
  tests/residuals/test_geometry.py::test_classifies_components_against_arbitrary_parent_boundary -q
```

Expected: missing public classification helper.

**Step 3: Extract reusable M3 helpers without changing M3 outputs**

Expose narrowly named helpers that accept explicit geometry and reference dimensions:

```python
def measure_residual_components(
    components: tuple[Polygon, ...],
    *,
    access_boundary: BaseGeometry,
    rules: ResidualRuleSet,
    reference_short_side: float,
    coordinate_tolerance: float,
) -> tuple[ResidualComponentMetrics, ...]: ...

def classify_residual_components(
    components: tuple[Polygon, ...],
    metrics: tuple[ResidualComponentMetrics, ...],
    rules: ResidualRuleSet,
    *,
    reference_area: float,
    reference_short_side: float,
    area_tolerance: float,
    coordinate_tolerance: float,
) -> tuple[RuleClassificationSummary, ...]: ...
```

Make M3 call the same helpers and keep every committed M3 identity unchanged.

**Step 4: Write failing recursive-consumption tests**

Call `consume_remnant` on hand-computable fixtures and assert:

```python
accounted = (
    result.accounting.placed_area
    + result.accounting.process_loss_area
    + result.accounting.retained_child_area
    + result.accounting.scrap_area
)
assert accounted == pytest.approx(result.accounting.parent_remnant_area)
assert all(child.lineage.parent_remnant_id == remnant.remnant_id for child in result.children)
assert all(child.lineage.generation == remnant.lineage.generation + 1 for child in result.children)
```

Cover a placement that leaves one child with a hole, one that splits the remnant, primary retained
versus scrap classification, and injected reconciliation failure.

**Step 5: Implement child creation and reconciliation**

Subtract the validated buffered footprint, extract polygon components, measure/classify them with
the M0 primary rule, build content-addressed children for retained components, report scrap
components, and enforce exact lineage plus the material invariant. Sort children by polygon hash.

**Step 6: Run GREEN and regression verification**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest tests/residuals tests/reuse -q
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest \
  tests/experiments/test_residual_geometry.py -q
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff check \
  src/yieldforge/residuals src/yieldforge/reuse tests/residuals tests/reuse
```

Expected: M3 canonical-result tests remain unchanged and all reuse tests pass.

**Step 7: Commit**

```bash
git add yf/src/yieldforge/residuals/geometry.py yf/tests/residuals/test_geometry.py \
  yf/src/yieldforge/reuse/geometry.py yf/tests/reuse/test_geometry.py
git commit -m "feat: create recursive remnant residuals"
```

### Task 4: Add deterministic bounded witness discovery

**Files:**
- Create: `yf/src/yieldforge/reuse/search.py`
- Create: `yf/tests/reuse/test_search.py`
- Modify: `yf/src/yieldforge/reuse/contracts.py`
- Modify: `yf/tests/reuse/test_contracts.py`

**Step 1: Write failing search-contract tests**

Define `FitSearchConfig`, `FitSearchStatus`, `FitSearchAttemptSummary`, and `FitSearchResult`.
Freeze grid columns/rows, maximum candidates, rotation order, transform ordering, and first-valid
selection. Require no-witness results to use exactly `no_witness_within_registered_search`.

**Step 2: Run RED**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest tests/reuse/test_search.py -q
```

Expected: missing search module/contracts.

**Step 3: Implement candidate-transform generation**

Generate bbox alignments, vertex alignments, and uniform-grid translations for each sorted allowed
rotation. Require finite values, canonicalize signed zero, deduplicate exact `(rotation, x, y)`
tuples, sort them, and enforce the frozen maximum before evaluation.

**Step 4: Implement witness search**

Apply only safe necessary filters (area and rotated bounding dimensions), call
`validate_fit_placement` for every remaining candidate, return the first valid placement, and retain
stable rejection counts by error code. Never convert search exhaustion to `no_fit`.

**Step 5: Add deterministic and adversarial tests**

Prove identical inputs yield identical candidate ordering and result; a known fit is found and
exactly revalidated; duplicate anchors collapse; budget truncation is visible; holes/concavity are
respected; and a no-witness result is explicitly inconclusive.

**Step 6: Run GREEN and commit**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest tests/reuse -q
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff check src/yieldforge/reuse tests/reuse
git add yf/src/yieldforge/reuse yf/tests/reuse
git commit -m "feat: search for bounded remnant fit witnesses"
```

### Task 5: Freeze M4 experiment input and canonical evidence bindings

**Files:**
- Create: `yf/src/yieldforge/experiments/remnant_reuse.py`
- Create: `yf/tests/experiments/test_remnant_reuse.py`

**Step 1: Write failing M3 reconstruction tests**

Load miniature M3 input/result fixtures. Verify the publisher recomputes each candidate residual,
finds the exact primary-retained component by SHA-256, and rejects M0/M2/M3 identity mismatch,
result tampering, component mismatch, a nonprimary component, or an invalid source part.

**Step 2: Implement strict input contracts**

Add `M4OriginRemnant`, `M4FuturePartRole`, and `M4ReuseInputPack`. Bind them to canonical M0/M3
identities, Shapely version, zero-clearance config, assumed material, generated-order disclaimer,
search config, and a deterministic ordered enumeration. Derive `yfri-<24 hex>` and semantic
SHA-256 from canonical JSON; publish immutably.

**Step 3: Test residual-blind enumeration**

Require origin tasks, candidates, component hashes, greater-index future tasks, and part IDs to use
the design's frozen order. Preparing the input must not call search or inspect fit results.

**Step 4: Run GREEN and commit**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest \
  tests/experiments/test_remnant_reuse.py -q
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff check \
  src/yieldforge/experiments/remnant_reuse.py tests/experiments/test_remnant_reuse.py
git add yf/src/yieldforge/experiments/remnant_reuse.py \
  yf/tests/experiments/test_remnant_reuse.py
git commit -m "feat: freeze M4 reuse input evidence"
```

### Task 6: Evaluate and publish the source-observed reuse witness

**Files:**
- Modify: `yf/src/yieldforge/experiments/remnant_reuse.py`
- Modify: `yf/tests/experiments/test_remnant_reuse.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/test_cli.py`
- Create: `yf/experiments/results/remnant-reuse-input-<input-id>.json` (generated)
- Create: `yf/experiments/results/remnant-reuse-result-<result-id>.json` (generated)

**Step 1: Write failing evaluator and result tests**

Require every attempted origin/future pair to be counted, stable failures to remain visible, the
first valid fit to stop the scan, and the result to contain exact placement, parent/child polygons,
accounting, lineage, full-sheet reference feasibility, and `avoided_full_sheet_openings = 1` only in
the declared one-order toy state. Require the claim ceiling:

```text
exact_remnant_reuse_possibility_only_not_frequency_savings_physical_recovery_or_commercial_value
```

**Step 2: Implement evaluation and canonical validation**

Add `M4ReuseWitness`, `M4ReuseSummary`, and `M4ReuseResult`. Recompute the summary, result ID, input
binding, polygon hashes, material compatibility, placement validity, reconciliation, lineage, and
full-sheet reference during loading. Reject any mismatch or unsupported avoided-sheet count.

**Step 3: Add CLI tests and commands**

Add thin handlers for:

```text
yieldforge experiments prepare-remnant-reuse
yieldforge experiments evaluate-remnant-reuse
```

The prepare command requires canonical M0, M3 input, and M3 result paths. The evaluate command
requires the frozen M4 input pack and M0 contract. Both require a new output path or identical
existing bytes.

**Step 4: Run focused GREEN**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest \
  tests/reuse tests/experiments/test_remnant_reuse.py tests/test_cli.py -q
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff check src tests
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff format --check src tests
```

**Step 5: Prepare and evaluate canonical evidence**

From `yf/`:

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run yieldforge experiments prepare-remnant-reuse \
  --m0 experiments/m0-contract-v1.json \
  --m3-input experiments/results/residual-geometry-input-yfgi-2fe5b848ea643d282c284f90.json \
  --m3-result experiments/results/residual-geometry-result-yfgr-0ac2c37f0938d9d399e7a076.json \
  --output experiments/results

UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run yieldforge experiments evaluate-remnant-reuse \
  --m0 experiments/m0-contract-v1.json \
  --input experiments/results/remnant-reuse-input-yfri-<id>.json \
  --output experiments/results
```

If no witness is found, publish the bounded no-witness result, leave M4 open, and do not change the
search budget post hoc. Design a separately versioned expanded search only after reporting the
primary result.

**Step 6: Pin committed-artifact tests and commit**

Add exact IDs/SHA-256 values only after publication. Require canonical load/recomputation and rerun
the focused suite.

```bash
git add yf/src/yieldforge/experiments/remnant_reuse.py yf/src/yieldforge/cli.py \
  yf/tests/experiments/test_remnant_reuse.py yf/tests/test_cli.py \
  yf/experiments/results/remnant-reuse-*.json
git commit -m "data: publish M4 remnant reuse witness"
```

### Task 7: Record the M4 decision and verify the complete repository

**Files:**
- Modify: `README.md`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Milestones/M4 - Remnant reuse proof.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`

**Step 1: Update only supported project truth**

If the canonical result contains a valid source-observed witness and every gate passes, mark M4
Passed and M5 Next. Record artifact IDs, origin task/candidate/component, future task/part,
placement, child count, reconciliation delta, attempted-pair count, and explicit generated/assumed
fields. Otherwise leave M4 open with the exact failed gate.

Do not report reuse frequency, economic savings, physical recoverability, or commercial value.

**Step 2: Run fresh full verification**

```bash
cd yf
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest -q
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv build

cd web
npm test
npm run typecheck
npm run build

cd ../..
git diff --check
git status --short --branch
```

The real browser suite is required only if M4 changes browser/API behavior. Otherwise record it as
not applicable to the solver-independent experiment slice rather than rerunning an unrelated
mutation.

**Step 3: Commit and synchronize**

```bash
git add README.md 'Docs/Current Work.md' \
  'Docs/Milestones/M4 - Remnant reuse proof.md' \
  'Docs/Milestones/Milestone Roadmap.md'
git commit -m "docs: record M4 remnant reuse result"
git push origin main
```

**Step 4: Handoff**

Report exact commit IDs, artifact IDs, verification counts, skips, observed/generated/assumed data
boundaries, result limitations, and the bounded M5 next step. Confirm the worktree and
`origin/main` synchronization state.
