# M7 Strong Baseline Design

**Approved direction:** Continue from the passed M6 population, preserve M5 v1, reuse verified M2
ordinary candidate archives, select policy variants on calibration only, and freeze the winner before
evaluation execution.

## Claim boundary

M7 builds a deterministic, as-of-safe baseline and the common action boundary later used by M8. A
pass proves only that the frozen baseline executes completely and reproducibly over the registered
population with verified candidate parity and exact material accounting. It does not prove savings,
future-information value, factory representativeness, physical recoverability, or commercial value.

## Measured decomposition correction

M6 groups all compatible work released at the same timestamp. Its eight `compatible_bundle`
batches per stream each contain three source tasks. A live area census found that the six-stream
feasibility slice's three-task batches require `1.658845` to `1.746756` sheet areas, and every
two-task subset also exceeds one sheet area. Those grouped problems cannot be valid one-sheet
Spyrrow cells.

M0 already requires deterministic pre-policy decomposition of multi-sheet work. M7 therefore
decomposes every M6 group at its original source-event boundary, preserving event order, source
projection, release timestamp, material, and stock. This creates 1,152 executable single-sheet
instances over 209 distinct source-faithful geometry problems:

| Scope | Instances | Distinct problems |
| --- | ---: | ---: |
| Calibration | 288 | 90 |
| Evaluation | 864 | 198 |
| Union | 1,152 | 209 |
| Shared across partitions | — | 79 |

This is deliberately conservative: it does not invent cross-task co-nesting evidence and it does
not silently submit impossible multi-sheet groups as one-sheet problems.

## Selected architecture

### 1. Reusable problem and instance binding

`yieldforge.baseline.problems` creates a strict content-addressed reusable problem from one exact
catalog projection. Its identity retains the task index, full problem, projection hash and
assumptions, stock, source catalog hash, and frozen solver requirement. It excludes stream,
timestamp, event ID, and assumed material.

Each temporal event receives a separate content-addressed instance binding containing its stream,
event, original M6 batch, release time, material, and reusable problem identity. This makes archive
reuse explicit without erasing chronology or material provenance.

### 2. Candidate evidence reuse

M2 already generated immutable ordinary archives for every eligible task with Spyrrow 0.9.0,
seeds `0..3`, 10 seconds per seed, one worker, no early termination, and no added separation. M7
verifies those files from the canonical M2 calibration and confirmation result mappings and binds
their recomputed hashes into an M7 candidate-set index. It never trusts paths, job IDs, or prior
success prose without revalidating manifest, JSONL, problem, projection, settings, and batch hash.

Cross-seed candidate duplicates are collapsed only when their existing canonical candidate IDs and
geometry content agree. Zero candidates or any invalid/missing archive fail the registered cell.

### 3. Exact batch actions

An executable instance action consumes one complete source-task layout. The common action generator
offers:

- one standard-sheet action for every distinct verified archived layout; and
- one remnant action for every archived layout that a bounded deterministic translation search can
  place inside a compatible current remnant.

The search preserves all archived part rotations and relative positions. It translates the complete
layout as one rigid footprint using bounding-box alignments, vertex alignments, then a frozen grid,
deduplicated and evaluated in lexical order. Shapely remains authoritative for containment,
part-part overlap, residual overlay, exact component geometry, and material reconciliation.

Execution subtracts the complete placed union atomically, applies the M0 primary remnant rule,
creates acyclic child lineage, and records retained and scrapped areas. Standard-sheet and remnant
actions share the same candidate archive hashes.

### 4. Replay and costs

The M7 replay contract is versioned separately from M5 v1. It groups equal timestamps for reporting,
then executes the deterministic source-event subinstances in source sequence with zero elapsed time
between them. Storage accrues once from the prior timestamp to the group timestamp. Purchases,
return/retrieval handling, scrap proceeds, and terminal scrap-only liquidation follow M0 exactly.

Every result records action-set size, search queries and time, inventory transitions, costs,
failures, candidate hashes, and the policy decision key. Missing actions fail closed.

### 5. Frozen calibration variants

The preregistered variants are:

1. `myopic_geometry`: smallest archived width, then candidate and stock identity.
2. `remnant_first`: remnant actions before standard sheets, then least immediate scrap and stable
   identity.
3. `net_cost`: lowest current purchase, handling, scrap, and one-interval storage liability.
4. `age_regularity`: `net_cost` plus deterministic preference for consuming older remnants and for
   producing more rectangular retained components.
5. `known_order_lookahead`: the same economic score plus only firmly scheduled work known at the
   timestamp. M6 contains no pre-release `known_at` field, so this term is explicitly zero rather
   than leaking realized future demand.

The lowest mean final net cost on all 12 calibration streams wins. Tie-breakers are fewer invalid
streams, lower median net cost, fewer sheet openings, then lexical policy ID. The selected policy,
parameters, code/runtime identity, candidate-set identity, and calibration results are frozen before
the 36 evaluation streams are replayed.

## Alternatives rejected

- **Regenerate M2-equivalent archives:** wasteful and introduces new wall-clock evidence despite
  identical registered geometry and settings.
- **Keep three-task M6 groups as one sheet:** geometrically impossible by area and contrary to M0's
  multi-sheet rule.
- **Use an expanded virtual strip:** placements could cross physical sheet boundaries and would not
  define valid stock actions.
- **Add Jagua immediately:** M6 made zero fit searches. The M7 feasibility slice must first show at
  least 30% search share or more than 15 projected calibration minutes.
- **Give lookahead realized future events:** violates the frozen information set.

## Failure and verification

Strict models forbid extra fields and non-finite values. Content identities are recomputed.
Candidate archives, geometry, action execution, inventory continuity, material accounting, and cost
ledgers fail closed. Tests cover identity reuse, decomposition, archive tampering, overlap and
containment rejection, deterministic action order, replay repeatability, information masking, and
selector freeze. The six-regime calibration slice is required before full calibration execution.

## Task 5 execution checkpoint

The collision gate triggered on the first exact-recurrence opportunity. The implemented local
extension pins `jagua-rs` 0.7.0, transports registered translation inputs as exact IEEE-754 `f64`
bits, uses a one-unit guarded container for `f32` rejection queries, and retains Shapely as the
accepted-witness and residual oracle. Differential `yfm7d-055c3aa7a09c85fbee2f1ca2` recorded zero
mismatches over 174,626 real translations.

Feasibility `yfm7f-7edef2fa8719168941e431d2` completed all 144 registered slice events. It found no
complete-layout remnant action; every selected action opened a standard sheet. Runtime was
3,864.895624 seconds and the frozen calibration projection is 644.149271 minutes. This passes the
software-feasibility/collision-backend boundary but requires runtime work or an accepted long run
before Task 6 execution.
