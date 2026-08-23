# M6 Temporal Benchmark Design

**Date:** 2026-08-23

**Status:** Approved for implementation by the instruction to proceed to M6, with the
source-faithful full-catalog option selected after live catalog inspection.

## Purpose

M6 creates immutable chronological test worlds from real Lectra polygon geometry. It must make
recurrence, related families, compatible release bundles, high-mix demand, regime shifts, and a
no-signal control independently inspectable. The resulting data is the common paired input for M7
and M8; M6 itself does not compare policies or claim savings.

## Evidence boundary

The primary corpus is the committed `lectra-7030786-v1.1` catalog:

- artifact SHA-256 `0e5c3d8aa39846fc69a1c662d01f0a0a9a1761f5d7ce0fbb10efdcf759fc55ad`;
- 256 source tasks, 8,358 source part rows, and 745 exact source polygon shapes;
- 254 tasks runnable with the catalog's explicit `s1` assumptions; and
- tasks `4365` and `25801` visibly excluded because their non-`s1` semantics remain unresolved.

Geometry, task composition, source sheet dimensions, and source-recorded orientation/flip states
remain source-observed or derived from source evidence. Chronology, material assignments, economic
rates, regime labels, and partitions are generated or assumed and are labeled as such. The catalog
does not contain observed factory chronology, material identity, or economic rates.

The two-task representative slice and three existing order-book manifests remain legacy schema
fixtures. They are not promoted into the M6 benchmark population.

## Population design

The registered population contains 48 paired streams:

| Dimension | Frozen value |
| --- | --- |
| Regimes | `no_signal`, `exact_recurrence`, `family_similarity`, `compatible_bundle`, `high_mix`, `regime_shift` |
| Common seed count | 8 per regime |
| Events per stream | 24 |
| Calibration seeds | first 2 common seeds, producing 12 streams |
| Evaluation seeds | remaining 6 common seeds, producing 36 streams |
| Start | `2026-01-01T00:00:00Z` |
| Cadence | 60-minute release slots |
| Compatible bundle size | 3 events per release slot |
| Regime-shift point | after event 12 |
| Projection | source-recorded only |

Seed values are reused across all regimes to preserve common-seed pairing. Membership is explicit in
the content-addressed benchmark contract and population index; no stream can belong to both
partitions or neither.

## Regime constructions

Regimes are accepted from measured realized properties, not their requested label.

1. **No signal.** Select 24 distinct source tasks with no repeated shape hashes where the source
   pool permits it. Assign a distinct assumed material identity to every event. This removes
   material-compatible future reuse by construction.
2. **Exact recurrence.** Repeat one source task on one compatible assumed material for all 24
   events. Exact-task and shape recurrence must both be one.
3. **Family similarity.** Cycle distinct source tasks that share an identical unique-shape family
   signature and source sheet dimensions. Use one compatible assumed material. This isolates
   related compositions from exact task recurrence.
4. **Compatible bundle.** Release groups of three distinct tasks at the same timestamp, with the
   same source sheet dimensions and assumed material within a bundle. Every event must belong to an
   exactly compatible three-event release batch.
5. **High mix.** Select 24 distinct tasks from a common source sheet group while maximizing new
   shape coverage. Reuse a small assumed material palette so future-compatible work exists without
   exact task recurrence.
6. **Regime shift.** Use exact recurrence for the first 12 events, then switch to 12 distinct
   low-recurrence tasks under the same source sheet and material boundary. Diagnostics must measure
   the change rather than infer it from the label.

Generation fails closed if the realized thresholds cannot be met by the pinned source pool.

## Contracts and identities

M6 adds versioned contracts without changing legacy `yieldforge.order-book.v1` identities:

- `yieldforge.temporal-benchmark-contract.v1` binds M0, catalog evidence, generator identity,
  population cells, regimes, timing, partitions, stock/rate rules, projection mode, and candidate
  requirements;
- `yieldforge.temporal-stream.v1` binds one realized generated stream and its measured diagnostics;
- `yieldforge.temporal-population.v1` binds all 48 stream IDs, hashes, partitions, and generation
  outcomes; and
- `yieldforge.temporal-lowering-report.v1` proves every accepted stream resolves back to exact
  source-recorded polygon demand without missing parts, changed projection, mixed stock inside a
  batch, or lost material identity.

All persisted identities use canonical JSON semantic hashing. Readers recompute identities and
reject altered, duplicate, missing, or unexpected records.

## Replay-ready lowering

Each event resolves through the pinned full catalog and `project_task(...,
mode=source_as_recorded)`. Lowering preserves every source part row and its allowed orientations.
Part IDs are namespaced by stream and event so same-timestamp tasks can be combined without
collisions.

Events are grouped using the frozen M0 rule: all and only work at the same timestamp with identical
material and source sheet specification enters one compatible batch. A lowered batch contains one
`StripPackingProblem`, its material identity, source task IDs, event IDs, source projection hashes,
and exact part count. If work at one timestamp is incompatible, deterministic stock/material
sub-batches are ordered lexicographically; no incompatible work is silently combined.

This is a demand adapter and feasibility boundary. Candidate selection and strong baseline behavior
belong to M7.

## Rates, stock, and candidate requirements

Source sheet dimensions are preserved as the stock boundary and retain the uninterpreted source
coordinate unit. Material is assumed. The generated feasibility-rate manifest uses explicit finite
nonnegative values and cannot be described as observed factory economics.

M7 candidate evidence must use the already selected ordinary geometry budget: Spyrrow `0.9.0`,
seeds `0,1,2,3`, 10 seconds per seed, one worker, no early termination, no added item separation,
and verified immutable archives shared across paired policies. M6 records this requirement but does
not generate M7's action archives.

## Validation and pilot

The M6 validator must:

1. bind the contract to the frozen M0 and catalog artifacts;
2. regenerate every accepted stream byte-for-byte;
3. rederive every diagnostic and regime threshold;
4. prove exactly one immutable partition per stream and common seed coverage per regime;
5. lower every event through the source-recorded projection;
6. reconcile source event, task, part, and batch counts; and
7. report every failure rather than drop or replace a cell.

A stratified six-stream lowering pilot profiles catalog resolution, projection, exact Shapely
geometry validation, and batch construction. Shapely remains the correctness oracle. A Jagua spike
is authorized only if actual exact fit/search calls—not catalog parsing or source validation—reach
at least 30% of end-to-end replay runtime, or a registered replay pilot projects beyond 15 minutes.
M6 contains no policy-scale fit/search loop, so this milestone cannot manufacture that trigger.

## Acceptance and claim ceiling

M6 passes when all 48 registered streams regenerate identically, satisfy their measured regime
properties, appear in exactly one immutable partition, preserve baseline/oracle information
separation, and lower into exact compatible batches without source or material loss.

A pass proves controlled synthetic chronology over real catalog geometry. It does not prove real
factory demand, remnant opportunity frequency, candidate richness, baseline strength, future-aware
policy value, material savings, physical recoverability, or commercial value.

