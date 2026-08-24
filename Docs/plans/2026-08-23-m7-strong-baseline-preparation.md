# M7 Strong Baseline Preparation

**Prepared:** 2026-08-23

**Entry state:** M6 canonical population and lowering pilot passed

**Decision:** Build M7 on the immutable M6 calibration partition; preserve evaluation blindness
until the baseline variant and all hyperparameters are frozen.

## Purpose and claim ceiling

M7 must create the competent as-of-safe opponent that M8's future-aware policies must beat. A pass
can establish deterministic baseline execution, candidate/action parity, calibration-only policy
selection, and a frozen evaluation policy. It cannot establish future-information value, savings,
factory representativeness, physical recoverability, or commercial value.

## Frozen M6 inputs

- Contract: `yfm6-3eeda3f4feb80813807c501a`
- Population: `yftp-49bd7ce5fd34b2779440c52f`
- Streams: 48 total, 12 calibration and 36 evaluation
- Demand: 1,152 events, 37,247 source-part references
- Replay-ready lowering: 1,024 compatible batches
- Source pool: 254 runnable full-catalog tasks; tasks `4365` and `25801` remain excluded
- Candidate requirement: Spyrrow 0.9.0, seeds `0,1,2,3`, 10 seconds per seed, one worker, no
  early termination, no added separation, verified immutable archives shared across paired policies

The M6 lowering pilot is `yfm6p-c5852afe0dd52f41689f3ed6`. It measured fast source projection
and exact geometry validation but made zero repeated remnant fit/search calls. It therefore does not
authorize a second collision backend.

## Candidate-problem census

M6's 1,024 event batches reduce to 273 distinct geometry/stock problems when repeated event
namespaces and assumed material labels are removed while preserving ordered source task
composition:

| Scope | Batches | Distinct geometry problems | Four-seed solver runs | Registered solver seconds |
| --- | ---: | ---: | ---: | ---: |
| Calibration | 256 | 106 | 424 | 4,240 |
| Evaluation | 768 | 246 | 984 | 9,840 |
| Full population union | 1,024 | 273 | 1,092 | 10,920 |

Calibration and evaluation share 79 geometry problems. A content-addressed reusable problem
identity can avoid recomputing those actions without leaking event chronology or evaluation policy
outcomes. The identity must retain ordered source task composition, source-recorded projection,
stock dimensions, solver configuration, and all exact part geometry while removing only
stream/event namespace.

## Gaps that block M7 acceptance

1. **Batch replay version:** M5 v1 accepts one part per order. M7 needs a new versioned batch action
   contract without changing canonical M5 identities.
2. **Reusable problem identity:** M6 batch part IDs include event namespaces. Candidate archives
   need a geometry-stable identity and an explicit binding back to every stream batch instance.
3. **Candidate/action definition:** M7 must specify how feasible layouts, remnant retrievals,
   standard-sheet openings, recursive residuals, and no-action failures form the common action set.
4. **Archive population:** none of the 273 distinct M6 geometry problems has its registered four
   ordinary candidate archives yet.
5. **Baseline variants:** myopic geometry, cost-aware remnant-first, known-order lookahead, and any
   age/regularity heuristics are not yet formalized or bounded.
6. **Calibration selector:** the exact primary score, tie-breakers, hyperparameter grid, failure
   treatment, and freeze artifact are absent.
7. **Parity evidence:** M7 and later M8 must consume identical candidate archive hashes, stock,
   remnant rules, seeds, solver settings, and ordinary compute budgets.
8. **Repeated-search runtime:** actual remnant fit/search share under batch replay is still
   unmeasured; validation-only polygon timing cannot justify Jagua.

## Ordered M7 entry plan

### M7.0 — Freeze batch action and reusable problem contracts

Add versioned replay-batch, reusable geometry-problem, candidate-set, action, failure, and instance
binding contracts. Preserve M5 v1 unchanged. Prove that equivalent repeated source-task batches map
to one geometry problem while every event instance remains auditable.

### M7.1 — Run a calibration-only action feasibility slice

Select one preregistered calibration stream per regime. Generate the four ordinary candidate
archives for its distinct geometry problems, verify every archive, derive exact residual actions,
and execute batch replay. Measure archive yield, action-set size, remnant fit/search query share,
inventory growth, runtime, and failure causes.

### M7.2 — Decide the collision backend

Keep Shapely authoritative. Spike Jagua only if actual fit/search calls consume at least 30% of
end-to-end replay runtime or the registered full calibration replay projects beyond 15 minutes.
Any accelerator must reproduce Shapely decisions and persisted geometry on a frozen differential
corpus before use.

### M7.3 — Populate calibration candidates

Generate and verify the remaining distinct calibration problems under the frozen ordinary budget.
Persist problem-to-archive and batch-instance bindings. Fail the cell rather than substitute a
missing or invalid action.

### M7.4 — Implement and select baseline variants

Run preregistered deterministic baseline variants on the 12 calibration streams only. Select the
lowest mean net-cost variant under M0, then apply frozen tie-breakers and supporting failure gates.
Persist the winning policy identity, parameters, code/runtime versions, and calibration evidence.

### M7.5 — Freeze evaluation execution

Generate/verify the remaining evaluation candidate problems without examining comparative policy
outcomes. Bind all 36 evaluation streams to the frozen baseline, common action archives, rates,
stock, and remnant rules. M7 passes when repeated baseline execution is identical and complete.

## Optimal first implementation slice

Implement M7.0 plus the six-stream calibration-only feasibility slice before launching all 424
calibration solver runs. This resolves the batch/action identity and measures actual fit/search
costs while the expensive candidate population remains avoidable. Only after that slice is green
should the full calibration archive run begin.

