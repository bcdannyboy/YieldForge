# M4 Remnant Reuse Design

**Date:** 2026-08-23  
**Status:** Approved for implementation  
**Milestone:** M4 — Remnant reuse proof

## Decision

M4 will implement an exact, solver-independent remnant-fit boundary and use it to publish one
reproducible source-observed reuse witness. The geometry oracle will authoritatively validate a
specific placement, subtract the consumed part and explicit process loss, reconcile material, and
create content-addressed child remnants with immutable lineage.

Placement discovery will remain a separate deterministic, finite witness search. A returned
placement is exact evidence because the geometry oracle revalidates it. Exhausting the bounded
search means only `no_witness_within_registered_search`; it must never be reported as proof that no
placement exists.

The primary M4 arm uses zero clearance, matching the source-faithful M2/M3 boundary. Nonzero
clearance remains an explicit tested capability, not a source claim. Source geometry is observed;
the uniform material identity and the role of one task as later demand are generated assumptions
because the Lectra corpus does not contain production chronology or material properties.

## Approaches considered

1. **Shapely exact validator plus bounded deterministic witness search — selected.** This reuses
   M3's pinned exact-vector and fail-closed conventions, keeps the truth boundary small, and can
   prove the favorable M4 mechanism without introducing a second runtime. Its search is incomplete,
   so negative search outcomes remain explicitly inconclusive.
2. **Integrate `jagua-rs` and an optimizer now.** `jagua-rs` is a strong future collision-detection
   boundary for irregular containers, but it separates collision truth from placement optimization
   and has no existing YieldForge adapter. Adding a Rust build, schemas, optimizer budget, and
   differential validation would make M4 substantially larger without changing its possibility-only
   acceptance gate. Keep this as the candidate search engine for later frequency-sensitive work.
3. **Use area, bounding boxes, or raster occupancy as the fit oracle.** These are useful cheap
   filters or diagnostics but can accept geometrically impossible placements or reject narrow valid
   fits. They cannot be the source of truth.

## Scope and claim ceiling

M4 includes:

- strict material, remnant, lineage, fit-request, placement, and result contracts;
- exact polygon containment under allowed rotations and translation;
- explicit nonnegative clearance and fail-closed invalid geometry;
- exact subtraction of the placed part and process loss from an irregular polygon with holes;
- material reconciliation and retained/scrap classification of child components;
- content-addressed parent-child remnant identity and acyclic lineage validation;
- positive, negative, hole, concavity, clearance, material, orientation, and tamper fixtures;
- a deterministic bounded transform generator and witness finder;
- one content-addressed M4 input/result pair bound to the canonical M0 and M3 artifacts; and
- one favorable source-observed part/remnant sequence that avoids opening a full sheet in a declared
  one-order toy state.

M4 does not estimate opportunity frequency, compare policies, replay chronology, value inventory,
calculate savings, prove search completeness, model cut sequencing or recoverability, or establish
physical or commercial value. Those questions remain in M5 and later milestones.

## Architecture

Add `yieldforge.reuse` beside `yieldforge.residuals`:

- `reuse.contracts` owns persisted material, polygon, lineage, remnant, fit, and witness contracts;
- `reuse.geometry` owns exact placement validation, subtraction, accounting, and child-remnant
  creation; and
- `reuse.search` owns deterministic candidate-transform generation and bounded witness discovery.

Add `experiments.remnant_reuse` for M3 reconstruction, source-observed pairing, immutable
publication, summary recomputation, and CLI orchestration. The experiment boundary may call the
search strategy, but every proposed placement must pass `reuse.geometry` before publication.

This keeps the policy-independent question, “Is this placement geometrically feasible?”, separate
from the optimization question, “Where should we look for a placement?”

## Persisted geometry and identity

M3 results store exact geometry hashes and metrics while the M3 input pack plus pinned code can
reconstruct polygons. M4 will reconstruct the selected M3 candidate, verify the resulting component
hash, and persist the chosen remnant and child polygons as normalized little-endian 2D WKB hex.
Each persisted polygon carries its SHA-256 and positive finite area. Loading recomputes the hash and
rejects invalid, empty, nonpolygonal, or mismatched WKB.

The root M4 remnant identity binds:

- M3 input and result identities;
- origin task, candidate, and residual-component identities;
- root sheet area and short-side length;
- exact material identity and its `assumed` provenance; and
- generation `1` with no parent remnant.

A child identity binds its exact polygon hash, parent remnant ID, root stock ID, generation, and full
ordered ancestor chain. A child must have generation `parent + 1`; its ancestors must equal the
parent's ancestors followed by the parent ID. Duplicate IDs, cycles, or root changes fail closed.

## Material and orientation boundary

Material compatibility requires exact equality across the M0 fields: material, grade, thickness,
surface, and grain. The M4 empirical witness assigns one explicit generated identity to the source
sheet, remnant, and later part. The artifact labels that identity as assumed; it does not imply the
source dataset supplied it.

Allowed rotations come only from the projected source part. The primary empirical arm uses
`source_as_recorded`; recorded flip semantics have already been lowered into the projected polygon
and are not reinterpreted by M4. A placement with an unlisted rotation fails before geometry work.

## Exact fit and recursive accounting

For a proposed placement:

1. validate the remnant polygon, material identity, part polygon, rotation, and finite translation;
2. rotate the part around local `(0, 0)` and then translate, matching M2/M3;
3. if clearance is positive, buffer the transformed part symmetrically with a mitre join;
4. require the buffered footprint to lie inside or on the remnant boundary within the declared
   scale-aware tolerance;
5. calculate `ProcessLoss = BufferedFootprint minus PlacedPart`;
6. calculate `Unused = ParentRemnant minus BufferedFootprint`;
7. extract polygonal child components with holes preserved;
8. classify each component under the frozen M0 rule using root-sheet thresholds and access to the
   parent-remnant boundary; and
9. require
   `ParentArea ~= PlacedArea + ProcessLossArea + RetainedChildArea + ScrapArea`.

Geometry repair is forbidden. Invalid input, unexpected nonpolygonal output, disallowed rotation,
material mismatch, out-of-remnant placement, or reconciliation failure returns a stable error and
cannot create inventory.

## Bounded witness search

For each allowed rotation, normalize the rotated part around its bounds. Generate translations from
three deterministic families:

- part and container bounding-box corner and center alignments;
- every container-ring vertex aligned with every part exterior vertex; and
- a registered uniform grid over the translation bounding box, including both boundaries.

Canonicalize and deduplicate transforms, then evaluate them in `(rotation, x, y)` order. Cheap area
and bounding-box checks may reject impossible candidates before exact validation. They never accept
a fit. The search manifest freezes grid dimensions, maximum candidate count, rotation order, and
first-valid tie breaking before the empirical witness run.

The search returns either `fit_found` with an exactly validated placement or
`no_witness_within_registered_search`. Only the first status supports a geometric claim.

## M4 evidence run

The M4 input publisher will bind the canonical M0 contract, M3 input, M3 result, Shapely version,
zero-clearance primary config, assumed material identity, and search config. It will enumerate origin
remnants and future-shape roles without fit results:

1. origin M3 tasks in ascending task index;
2. each selected candidate in stored order;
3. primary-retained residual components in component-hash order;
4. different M3 tasks with greater task index in ascending order; and
5. projected parts in part-ID order.

The greater task index is a deterministic generated ordering, not observed chronology. The runner
stops at the first exactly validated fit and records all attempted pair counts and stable failures.
It also runs the same part against a full-sheet reference only to establish that the declared toy
demand is feasible. In the toy state, the remnant is on hand and no full sheet is open; consuming
the remnant therefore avoids one full-sheet opening relative to the reference action. No prices or
savings are assigned.

If the registered search finds no witness, M4 remains open. The result may motivate a larger frozen
search or a `jagua-rs` adapter, but it may not be described as proof that the corpus contains no
reuse opportunity.

## Test design

Tests will establish:

- canonical polygon WKB round-trip and tamper rejection;
- strict material equality and provenance labels;
- root and child lineage identity, generation, ancestry, and cycle rejection;
- rotation-before-translation and rejection of unlisted rotations;
- exact positive containment in a concave remnant;
- rejection of an area/bounds-compatible placement that crosses a concavity;
- holes acting as unavailable material;
- boundary-touch acceptance at zero clearance and rejection under positive clearance;
- nonfinite, invalid, empty, multipolygon-as-single-stock, and material-mismatch failures;
- hand-computable placed/process/retained/scrap reconciliation;
- child-remnant creation with holes and deterministic hashes;
- deterministic transform ordering, deduplication, budget enforcement, and explicit inconclusive
  no-witness status;
- exact revalidation of every search result;
- reconstruction and binding to canonical M0/M3 evidence;
- immutable content-addressed input/result publication and tamper rejection; and
- one committed favorable source-observed witness with a recursive lineage chain.

## Acceptance decision

M4 passes only when:

1. all adversarial geometry, material, and lineage fixtures pass;
2. every accepted placement is exactly contained and reconciled;
3. a committed source-observed later-part role fits a retained M3 remnant under the frozen bounded
   search and produces valid child residual evidence;
4. the toy reference establishes one avoided full-sheet opening without assigning economic value;
5. the evidence artifact is content-addressed, bound to canonical M0/M3 inputs, and reproducible; and
6. full repository verification passes.

A pass proves only that exact remnant reuse is mechanically possible in the modeled geometry and
declared toy state. It does not prove that the opportunity is frequent, valuable, physically
recoverable, or commercially relevant.
