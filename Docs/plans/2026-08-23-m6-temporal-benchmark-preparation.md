# M6 Temporal Benchmark Preparation

**Prepared:** 2026-08-23

**Entry state:** M5 canonical replay passed

**Decision:** Reuse and extend the existing order-book contracts; do not treat current fixtures as a benchmark result.

## Purpose and claim ceiling

M6 must create immutable, reproducible temporal test worlds that isolate when future-aware remnant
decisions should and should not help. Its output can establish generator reproducibility,
construction-property coverage, information-set safety, and immutable calibration/evaluation
partitions. It cannot establish real-factory chronology, policy superiority, material savings,
physical recoverability, or commercial value.

## Existing assets

The repository already has a strong schema prototype in `yieldforge.order_books`:

- content-addressed manifests and deterministic MT19937 generator identity;
- source-observed Lectra task composition and shape references;
- separately labeled generated chronology and economics plus assumed material fields;
- exact-recurrence, high-mix, and no-signal regime labels;
- realized construction diagnostics and threshold validation;
- as-of-safe baseline views that omit future events and the generator-only regime label;
- oracle-only full-book views; and
- safe immutable archive reading, writing, and replay checks.

Three committed fixtures are deliberately tiny:

| Regime | Order-book ID | Seed | Events | Unique source tasks | Concentration | Shape recurrence | Part references |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| No signal | `yfob-c4879d4f16c0b6fe5eacc700` | 20260817 | 2 | 2 | 0.5 | 0.0 | 66 |
| Exact recurrence | `yfob-bf049e9141623c98654a2255` | 20260818 | 6 | 1 | 1.0 | 1.0 | 204 |
| High mix | `yfob-dccfa3fa98b63b3ac6bfd322` | 20260819 | 2 | 2 | 0.5 | 0.0 | 66 |

All three bind normalized Lectra slice
`sha256:d1e6d6d6aa300f9699cc8d9ffb63cee1747735f640f2b5501298d383ea1402e8`, start at
`2026-01-01T00:00:00Z`, and use 60-minute intervals.

These fixtures validate schemas and hand inspection only. In particular, the realized no-signal and
high-mix fixtures have the same two-task concentration and recurrence diagnostics; their labels and
generated material/economic draws differ, but they do not yet isolate materially distinct temporal
worlds.

## Gaps that block M6 acceptance

1. **Registered population:** there is no frozen regime-by-seed matrix, event-count scale, or
   immutable population identity.
2. **Partitions:** calibration and evaluation allocations, split salt, and leakage checks are absent.
3. **Regime coverage:** family similarity, compatible bundles, and explicit regime shifts are not
   implemented; the no-signal control needs stronger construction diagnostics.
4. **Replay lowering:** order-book task references have no source-faithful adapter into M5 replay
   orders, compatible release batches, exact parts, and material classes.
5. **Economics and stock:** M5 rates and its 10 by 10 sheet are generated mechanism fixtures, not an
   approved M6 rate manifest or stock catalog. Units and sensitivity arms must be registered.
6. **Candidate evidence:** per-event candidate archive identity, solver budget, failure handling, and
   common-seed requirements are not yet bound to each stream.
7. **Scale evidence:** the current fixtures have two or six events; they do not exercise meaningful
   inventory aging, reuse opportunity density, or regime shifts.
8. **External validity:** there is still no independent second polygon corpus or observed production
   chronology. M6 may proceed with generated chronology, but must preserve this claim ceiling.

## Ordered M6 entry plan

### M6.0 — Freeze the benchmark contract

Register generator version, regimes, population seeds, event counts, UTC cadence rules, source
slice identity, stock catalog, material mapping, rate manifest, candidate-archive requirements,
construction diagnostics, split salt, and calibration/evaluation membership. Freeze baseline and
oracle information sets before generating the population.

### M6.1 — Complete regime generators and diagnostics

Add family-similarity, compatible-bundle, and regime-shift constructions. Strengthen diagnostics so
each realized regime is distinguishable by measured properties rather than its requested label.
Generation must fail closed when realized thresholds are missed.

### M6.2 — Lower manifests into replay-ready demand

Resolve every source task reference through the pinned normalized slice, preserve source-faithful
polygon and orientation state, form M0-compatible same-timestamp batches, and translate assumed
material/economic fields without relabeling them as observed. The adapter must expose baseline-safe
as-of views and a separate oracle view.

### M6.3 — Generate and pin the population

Produce the registered seed matrix, publish canonical manifests, verify byte-for-byte regeneration,
measure every construction property, and pin immutable calibration/evaluation indexes. Include all
registered failures in the population report.

### M6.4 — Run a replay feasibility pilot

Replay a small stratified sample through the M5 kernel using frozen candidate budgets. Measure total
runtime, exact-geometry query count, query-time share, inventory growth, and failure modes. This is a
pipeline and scale check, not a policy comparison.

### M6.5 — Close the gate

M6 passes only if every accepted stream regenerates identically, meets its declared construction
properties, belongs to exactly one immutable partition, preserves as-of information safety, and can
be lowered into replay without silent geometry or material loss.

## Collision-runtime decision

Shapely remains the authoritative backend. M5 averaged about 4.08 ms for its two-order replay, while
canonical M4/M5 preparation is dominated by source-evidence validation rather than the replay
kernel. Do not add Jagua during contract work. In M6.4, profile a representative stratified pilot;
spike a Jagua adapter only if exact fit/search calls account for at least 30 percent of end-to-end
runtime or the registered local pilot projects beyond 15 minutes. Any adapter must reproduce
Shapely decisions and persisted geometry on the frozen differential corpus before it can accelerate,
never replace, the correctness oracle.

## First implementation slice

The optimal next code slice is M6.0 plus the missing regime contracts and tests, before generating
larger files. It should produce a reviewable benchmark contract and an executable validation command,
then a tiny fixture for each new regime. Population generation, replay-scale profiling, and any
collision acceleration follow only after that boundary is green.
