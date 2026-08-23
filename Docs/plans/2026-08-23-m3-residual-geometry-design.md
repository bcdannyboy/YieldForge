# M3 Residual Geometry Design

**Date:** 2026-08-23  
**Status:** Approved for implementation  
**Milestone:** M3 — Residual geometry truth

## Decision

M3 will add a strict, exact-vector residual geometry boundary and apply it to one deterministic,
residual-blind pair of ordinary M2 candidates for each of the 203 qualifying evaluation tasks.
The primary empirical replay will use the full fixed source sheet, zero part buffer, and no
forbidden regions. That is the only source-faithful primary because the Lectra corpus does not
provide physical kerf or clearance and the M2 confirmation used default separation `null`.

The engine will nevertheless accept an explicit nonnegative part-buffer distance and explicit
forbidden polygons. Adversarial fixtures will verify those behaviors. A future empirical run may
use nonzero process loss only under a separately frozen, labeled physical or synthetic model; M3
will not silently invent one.

## Approaches considered

1. **Pinned Shapely exact-vector overlay — selected.** Reuse the repository's pinned Shapely
   dependency to transform placed polygons, form disjoint material-accounting regions, extract
   connected residual polygons, and canonicalize geometry for hashing. This is small, auditable,
   and independent of the solver while preserving exact polygon evidence.
2. **Raster residual approximation.** A raster or distance-field implementation would be useful as
   a later diagnostic oracle, but pixel size would define the answer and could misclassify narrow
   necks or exterior access. It cannot be the M3 source of truth.
3. **Solver-native or new Rust geometry kernel.** This could reduce long-term duplication, but it
   would couple evidence replay to Spyrrow or introduce a second runtime before M4 needs exact
   remnant fitting. The added surface is not justified for M3.

## Scope

M3 includes:

- exact replay of archived rigid transforms against the archived projected problem;
- a rectangular stock polygon spanning `0..sheet_length` by `0..strip_height`;
- explicit part buffering and forbidden-region subtraction;
- connected residual component extraction with holes preserved;
- primary, permissive, and conservative remnant classification from the frozen M0 rules;
- disjoint material accounting and a scale-aware reconciliation tolerance;
- canonical residual/component fingerprints and geometry-difference diagnostics;
- a content-addressed, committed evidence pack containing only the 406 selected candidates;
- a committed result covering every one of the 203 qualifying M2 tasks; and
- adversarial geometry and invalid-input tests.

M3 does not add inventory reuse, temporal demand, baselines, an oracle, economics, savings, a
simulator, or production/CAM claims. Exact future-shape fit belongs to M4.

## Evidence bridge and candidate selection

The committed M2 result records observations but not full placements. The immutable local M2 run
archives contain the verified problems, projection bindings, solver settings, and full candidate
placements. A preparation command will read those archives and create a compact M3 input pack.

For each M2-qualifying task:

1. verify every referenced candidate archive before reading it;
2. reconstruct the ordinary candidate set from all four frozen seeds;
3. determine the best ordinary used length;
4. keep candidates within the frozen 0.5% envelope;
5. sort by `(width, candidate_id)`;
6. select the first candidate and the first subsequent candidate with a distinct canonical
   candidate ID; and
7. persist the projected problem, source/projection binding, solver/archive hashes, and both full
   candidates.

Selection uses no residual geometry or remnant metric. The input pack is canonical JSON with a
semantic SHA-256. The result binds to both that hash and the canonical M2 confirmation result.
This makes the 406-candidate replay portable without committing all 124,641 archived candidates.

## Geometry model

### Transform and validity

Each candidate must contain exactly one placement for every demanded part instance. Current M2
problems have demand one and placement IDs equal problem part IDs; the implementation remains
strict and rejects missing, duplicate, or unknown IDs rather than guessing an expansion scheme.
The existing rotation-then-translation convention will be reused.

Every source polygon, transformed polygon, buffer, forbidden polygon, union, and difference must
be finite and valid. Placed polygons must lie inside the fixed sheet within the declared numerical
tolerance and must not overlap in material area. Invalid geometry fails the observation; geometry
repair such as `make_valid` or `buffer(0)` is forbidden.

### Disjoint material partition

Let:

- `Stock` be the fixed sheet polygon;
- `Placed` be the union of unbuffered placed parts;
- `Buffered` be the union of explicitly buffered placed parts intersected with `Stock`;
- `ProcessLoss = Buffered minus Placed`;
- `ForbiddenLoss = (Forbidden intersect Stock) minus (Placed union ProcessLoss)`; and
- `Unused = Stock minus (Placed union ProcessLoss union ForbiddenLoss)`.

The resulting categories are disjoint. Connected polygon components of `Unused` are classified as
retained remnants or scrap. Holes remain holes and do not contribute material area.

The reconciliation invariant is:

`area(Stock) ~= area(Placed) + area(ProcessLoss) + area(ForbiddenLoss) + area(Retained) + area(Scrap)`

The default area tolerance is `max(1e-7, area(Stock) * 1e-10)`. All raw values and the delta are
reported. Exceeding the tolerance invalidates the observation.

### Remnant classification

For each residual component, compute area, bounds, exact exterior-access length, whether it is
exterior-connected, and whether an inward buffer by half the minimum effective width is nonempty.
Exterior access is the length of the component boundary intersecting the stock boundary. Numerical
contact shorter than the coordinate tolerance is not treated as access.

Apply all three frozen M0 rules relative to the parent fixed sheet:

| Rule | Minimum area | Effective width | Exterior access |
| --- | ---: | ---: | ---: |
| Permissive | 0.25% of sheet area | 0.5% of short side | exterior touch |
| Primary | 1% of sheet area | 2% of short side | 2% of short side |
| Conservative | 2.5% of sheet area | 5% of short side | 5% of short side |

All rules require exterior connection. A component failing a rule is scrap under that rule.
Material compatibility and lineage remain vacuously fixed within a single-sheet M3 cut; their
cross-event enforcement begins with the inventory model, not this geometry engine.

### Canonical evidence

Shapely-normalized, little-endian two-dimensional WKB is the canonical geometry representation
under the pinned library version. Result records store hashes, component metrics, and symmetric
difference area rather than duplicating every WKB payload. The committed input pack plus pinned
code can reproduce every exact polygon.

Two candidates differ exactly when their normalized residual fingerprints differ. Symmetric
difference area relative to sheet area, retained/scrap area changes, component counts, largest
component area, and eligibility changes are diagnostics; no tolerance cluster replaces the exact
fingerprint.

## Experiment and result boundary

The empirical run evaluates all 203 deterministic candidate pairs. For each pair and each of the
three remnant rules it reports:

- validity and reconciliation deltas;
- exact residual fingerprint equality;
- residual symmetric-difference area and fraction of sheet area;
- retained and scrap areas;
- retained and scrap component counts;
- largest retained component area; and
- whether classification differs.

The aggregate reports counts and proportions across all tasks, including exact residual
differences and classification differences for each rule. It reports every failure rather than
dropping a task.

M3 passes its technical gate only when:

1. all adversarial fixtures pass;
2. invalid and unreconcilable geometry fails closed;
3. all 203 selected task pairs are evaluated with verified input evidence;
4. every valid observation reconciles within tolerance; and
5. at least one M2 pair has a different exact residual fingerprint.

The full difference distribution remains the finding; the design does not invent a percentage
threshold after M2. A sparse difference result would be reported as an empirical warning even if
the geometry engine itself is valid.

## Test design

Unit fixtures will cover:

- rotation and translation against an asymmetric polygon;
- one simple exterior residual and exact material accounting;
- multiple connected components and an enclosed interior component;
- holes preserved through overlay and hashing;
- explicit process buffer and overlapping buffer union accounting;
- explicit forbidden regions and disjoint loss priority;
- permissive/primary/conservative area, effective-width, and exterior-access boundaries;
- canonical equality across ring order and orientation;
- exact residual difference and symmetric-difference diagnostics;
- missing, duplicate, unknown, out-of-sheet, overlapping, nonfinite, and invalid geometry; and
- reconciliation failure injection.

Integration tests will build a miniature archive set, verify residual-blind pair selection, reject
tampered archives or result bindings, run the experiment, and validate canonical result
round-trips. The real M2 replay runs only after the full test suite is green.

## Deliverables

- residual geometry contracts and exact-vector engine under `yf/src/yieldforge/residuals/`;
- M3 evidence preparation and evaluation under `yf/src/yieldforge/experiments/`;
- focused unit and integration tests under `yf/tests/`;
- one content-addressed M3 input pack and one content-addressed M3 result under
  `yf/experiments/results/`;
- updated M3, Current Work, and roadmap notes with the result and claim ceiling; and
- fresh project-wide verification evidence.
