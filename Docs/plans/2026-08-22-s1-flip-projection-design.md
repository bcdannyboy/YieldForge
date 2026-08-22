# Assumption-backed `s1` flip projection and ablation design

**Status:** Approved for implementation on 2026-08-22.

## Goal

Expand the bounded Lectra geometry test bed without discarding recorded flip constraints or
misrepresenting simplified experiments as source tasks. YieldForge will project the recorded
`s1` rotation and flip state under exact, acknowledged assumptions, while offering a separately
identified no-flip ablation for matched sensitivity experiments.

The source-lossless catalog remains immutable. Neither projection mode becomes directly supported,
and neither establishes physical process feasibility, material behavior, residual geometry,
chronology, an oracle, savings, production fitness, or buyer value.

## Source evidence and bounded interpretation

The pinned Lectra release describes `r1_start`, `r1_end`, and `r1_flip_x` together as rotation
parameters and permits multiple ranges. It does not fully define the reflection axis or transform
order. YieldForge will therefore retain the existing degenerate-rotation assumption and add an
exact flip assumption that defines a local source-coordinate reflection before rotation. The
assumption name and transform formula are persisted with every source task binding and derived
archive.

Projection continues to require one `s1` row per part, one known part reference, missing unrelated
parameters, finite equal-length orientation sequences, and degenerate start/end values. For the
current 256-task catalog, all alternatives within an `s1` row share one flip value, so the projector
can derive one solver polygon per part and pass the recorded rotation alternatives to Spyrrow.
Mixed flip alternatives remain blocked until the solver contract can express mutually exclusive
shape alternatives directly.

## Projection modes

`source_as_recorded` is the default assumption-backed mode. A zero flip preserves the normalized
source polygon. A one flip derives a reflected polygon under the exact declared convention, then
Spyrrow searches the recorded degenerate rotation values using its native `allowed_orientations`
interface.

`force_flip_x_zero` is a derived ablation. It uses the same source task but deliberately replaces
all recorded flip values with zero before solver projection. It receives a distinct projection
mode, assumption/intervention code, problem identity, canonical projection hash, archive evidence,
and visible provenance label. It must never be described as the original Lectra task or compared
to the source efficiency label as though constraints were unchanged.

The first implementation will not enumerate every per-part flip assignment. Such enumeration can
grow exponentially and can introduce opportunities the source did not record. Parallelism is used
for matched projection arms and seeds; rotation alternatives remain inside each Spyrrow solve.

## Contracts and lowering

An intermediate orientation-state contract retains each `(rotation_start, rotation_end, flip_x)`
tuple. A versioned projection descriptor records the mode, exact transform convention, sorted
assumption/intervention codes, and a canonical SHA-256 over the descriptor and projected problem.
The source task binding remains the immutable catalog identity; the projection descriptor identifies
the derived solver interpretation.

The Spyrrow-facing `Part` continues to contain one polygon and its allowed rotations. Reflection is
lowered before adapter invocation because the installed Spyrrow `Item` accepts one shape and a list
of allowed rotation angles but no reflection flag. Candidate placements therefore remain rotation
plus translation relative to the archived solver polygon. Rendering and replay use the projected
problem preserved in the immutable candidate batch, while the descriptor makes the source-to-solver
transform auditable.

## API, jobs, and workbench

Corpus detail exposes precise projection diagnostics, including flip-bearing part and row counts,
rather than only `s1_projection_requirements_not_met`. Solver submission requires the exact
assumptions for `source_as_recorded` and additionally requires explicit selection of the ablation
mode for `force_flip_x_zero`; server-owned capability state remains authoritative.

Nest Lab exposes the projection mode and its assumptions before submission, in live job state,
completed history, candidate archives, and read-only run comparison. Matched ablation runs reuse
the same task, solver configuration, and seed. They remain independent immutable jobs and can be
launched together only through a bounded paired-run action that applies equal per-arm compute
budgets. The UI reports geometric solver outputs neutrally and does not select a winner or claim
material savings.

Task `6669` is the primary real flip-bearing fixture. Pure-`s1` tasks that pass the extended strict
projection become assumption-backed. Tasks with unresolved non-`s1` constraints, including task
`25801`, remain view-only even if they also contain flips.

## Failure behavior

Projection fails closed for non-binary flip values, mixed flip alternatives within one part,
non-degenerate intervals, unsupported constraint types, invalid polygons after transformation,
missing assumption acknowledgement, unknown projection modes, provenance/hash drift, or archive
contract mismatches. No failure path silently falls back to an unflipped or different catalog task.

## Verification

Red-green-refactor coverage will include:

- asymmetric-polygon golden tests for the exact reflection-before-rotation formula;
- reflection involution, preserved area, valid ring, source immutability, and deterministic hashes;
- strict projection and capability reclassification for zero- and one-flip tasks;
- rejection of mixed, malformed, or unacknowledged transformations;
- Spyrrow adapter, candidate identity, archive round-trip, SVG rendering, and replay evidence;
- API submission, paired equal-budget jobs, completed history, and comparison provenance;
- frontend contract parsing, Corpus Explorer diagnostics, Nest Lab controls, and error states;
- a real browser run for task `6669` through FastAPI and Spyrrow, progressive candidates, immutable
  archive browsing, and a matched no-flip ablation;
- continued blocking of task `25801` and preservation of existing task `13958` behavior.

Completion requires the full Python suite, Ruff check and format check, frontend unit tests,
TypeScript/build, real Playwright against the running Postgres-backed API, deterministic catalog
requalification checks, `git diff --check`, and final diff/status review.
