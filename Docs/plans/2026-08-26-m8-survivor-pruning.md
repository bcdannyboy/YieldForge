# M8 Survivor Pruning Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Preserve the exact frozen M7 common transition while running registered translation and collision search only for inventory remnants that survive the compiled scalar frontier.

**Architecture:** Generalize the existing all-rejected standard-only catalog into a mixed catalog that accepts externally proven zero-generation remnants and sends only unresolved survivors through the unchanged M7 generator. Reconcile the omitted zero-generation query counts into the exact catalog, then keep the authoritative full catalog as a differential oracle until the unchanged Gate 2 probes pass or reject the redesign.

**Tech Stack:** Python 3.12, pytest, existing M7 replay and M8 compiled-frontier infrastructure, Jagua/Shapely exact search.

---

### Task 1: Add an exact mixed pruned catalog

**Files:**

- Modify: `yf/src/yieldforge/baseline/replay.py`
- Modify: `yf/tests/oracle/test_fast_common_transition.py`

1. Add a failing test with one scalar-rejected remnant and one unresolved survivor.
2. Require the mixed catalog to equal the full authoritative catalog exactly.
3. Require exact search to receive only the unresolved survivor while query and generation counters still reconcile.
4. Implement the smallest replay helper that generates against survivors, restores zero-generation query counts, and uses the full cursor for storage and action application semantics.

### Task 2: Integrate partial frontier pruning

**Files:**

- Modify: `yf/src/yieldforge/oracle/certificates.py`
- Modify: `yf/src/yieldforge/oracle/profiling.py`
- Modify: `yf/tests/oracle/test_fast_common_transition.py`

1. Add failing tests for all-rejected, mixed, and all-survivor inventories.
2. Collect a rejection witness for every provable inventory item instead of abandoning the scan at the first survivor.
3. Use the mixed catalog when at least one item is rejected; fail closed to the unchanged authoritative catalog when none are rejected.
4. Record rejected-remnant and exact-survivor counts separately.

### Task 3: Differential and adversarial verification

**Files:**

- Modify: `yf/tests/oracle/test_fast_common_transition.py`

1. Compare mixed and authoritative facts across frozen policies, inventory orderings, incompatible materials, area-only uncertainty, and cached-search states.
2. Require byte-for-byte fact equality and zero differential mismatches.
3. Run the focused oracle tests and the complete Python suite.

### Task 4: Rerun unchanged Gate 2 probes

**Files:**

- Update: `Docs/Development/M8 Gate 2 Fast Common Transition.md`
- Update: `Docs/Current Work.md`
- Update: `Docs/Milestones/M8 - Rollout oracle.md`
- Update: `Docs/Milestones/Milestone Roadmap.md`

1. Rerun the two-event `no_signal` and `regime_shift` probes with seed `2026082300`.
2. Compare against the frozen pre-redesign common-transition timings.
3. Proceed to the fact DAG only if both arms exceed `10x`, semantic mismatches remain zero, and survivor fallback is low enough for the official target.
4. If mixed pruning remains below the gate, retain content-addressed exact survivor witnesses at the authoritative M7 boundary and remeasure before considering collision-backend optimization.

### Task 5: Test a stronger convex-translation survivor certificate

**Gate:** Begin only if Task 4 reports zero useful scalar rejects on the hard arm.

**Files:**

- Create: `yf/src/yieldforge/oracle/convex_translation.py`
- Create: `yf/tests/oracle/test_convex_translation.py`
- Create or modify: a calibration-only survivor-coverage command under the existing M8 CLI

1. Add failing geometric tests for translated convex-hull containment, including axis-aligned survivors whose hulls cannot fit under translation.
2. Implement a fail-closed two-dimensional half-plane feasibility proof. It may reject only when the candidate convex hull cannot translate into the remnant convex hull.
3. Run a coverage-only calibration diagnostic that performs no collision search and opens no evaluation data.
4. Integrate exact no-fit search-result synthesis only if every hard-arm scalar survivor is rejected by the convex certificate; otherwise stop this branch and proceed to retained exact witnesses.

## Execution outcome — 2026-08-26

- Tasks 1–3 completed. The mixed catalog is exact across all five frozen policies and preserves
  query counts, event identity, policy context, inventory, and accounting while searching only
  unresolved inventory.
- The Task 4 mixed-only hard probe was a clean no-go: zero inventory items were frontier-rejected,
  both transitions fell back, 459/459 proofs remained valid, and total process time was
  `3146.917652` seconds.
- Task 5's collision-free diagnostic found a stronger and simpler result than expected: every one
  of the 459 hard-arm candidates was already scalar impossible. Convex translation proof was tested
  as a conservative relaxation but had zero incremental hard-arm coverage, so it was not promoted
  into the authoritative fast path. The diagnostic stayed local rather than adding another public
  CLI surface.
- Exact M7 identity still required translation-search counts. Python reconstruction remained too
  slow, so the content-bound frozen Jagua generator now supplies only those counts; a separate
  source-sequence reconstruction audits them, scalar geometry remains the no-fit authority, and
  unsupported cases fail closed.
- Final unchanged probes passed on aggregate common-transition wall time: `no_signal` achieved
  `393.341866x` and `regime_shift` achieved `13.255735x`, with 887/887 valid proofs, exact
  common-fact and reference equality, zero authoritative fallbacks, charged child-process work, and
  no evaluation access.

The next bounded plan is the v2 fact DAG and generator/checker reuse. The official six-cell M8 gate
must still re-establish witness coverage, `20x` sampled speedup, and a seven-day projection before
any evaluation action is authorized.
