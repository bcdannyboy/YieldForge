# M8 Portable Common-Fact Bundle Implementation Plan

**Status:** Implemented and verified through complete Gate 3 — performance hold 2026-08-27

The plan produced portable result `yfm8gate3-ea8a12969396172d7dbc4774` and complete decision
`yfm8g3decision-c13ec320e9fcd02873bf649c`. Exactness, independent proof comparison, and all 16
executed mutations passed. Performance did not: `686.535011` charged seconds project to
`113.434097` held-out days, so the next plan must target at least a `22.686819x` algorithmic
reduction before six-cell execution can be considered.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove duplicated M8 common-transition derivation by sending an explicitly unchecked,
content-addressed fixed-layer fact bundle from the generator to a fresh independent checker.

**Architecture:** Preserve the existing trusted v1 APIs as the differential oracle. Add a strict v2
bundle with common-transition lemmas, influence facts, and action roots; the generator captures
portable evidence, while the fresh checker validates each shared node once and issues only
process-local capabilities. Enforce an eight-slot compute budget before any distributed run.

**Tech Stack:** Python 3.12, Pydantic v2, frozen dataclasses, NumPy, pytest, existing M7/M8 replay,
SHA-256 canonical JSON, multiprocessing spawn/fork boundaries.

---

## Execution rules

- Work in `/Users/danielbloom/Desktop/YieldForge/.worktrees/m6-temporal-benchmark` on
  `codex/m8-rollout-preparation`.
- Use strict red-green-refactor TDD for every behavior change.
- Keep `score_sparse_event()` and `check_action_proofs()` unchanged as trusted v1 APIs.
- New generator output is named and typed as unchecked until the v2 checker succeeds.
- Do not load or inspect evaluation bindings.
- Stop before the official six-cell run unless Gate 3 and the abbreviated performance gate pass.
- Keep commits task-scoped.

### Task 1: Freeze the nested concurrency budget

**Files:**

- Create: `yf/src/yieldforge/oracle/concurrency.py`
- Create: `yf/tests/oracle/test_m8_concurrency.py`
- Modify: `yf/src/yieldforge/oracle/translation_count_audit.py`
- Modify: `yf/src/yieldforge/oracle/certificates.py`
- Modify: `yf/src/yieldforge/oracle/experiment.py`
- Modify: `yf/tests/oracle/test_translation_count_audit.py`
- Modify: `yf/tests/oracle/test_experiment.py`

**Step 1: Write the failing budget-contract tests**

Require a frozen internal contract:

```python
budget = M8ConcurrencyBudget(
    total_compute_slots=8,
    cell_phase_processes=4,
    translation_audit_processes_per_cell=2,
    reference_phase_processes=6,
)
assert budget.peak_nested_compute == 8
```

Reject booleans, zeros, a nested product above eight, and a reference width above eight. Require a
process-scoped context to expose the configured audit width and restore the prior value on success
and failure.

**Step 2: Verify red**

Run:

```bash
cd yf
PYTHONPATH=src .venv/bin/python -m pytest -q tests/oracle/test_m8_concurrency.py
```

Expected: import failure because `oracle.concurrency` does not exist.

**Step 3: Implement the minimal contract and execution context**

Use a `ContextVar[int | None]` plus a context manager. The translation audit batch must receive an
explicit positive integer; remove its default width of four.

```python
@dataclass(frozen=True, slots=True)
class M8ConcurrencyBudget:
    total_compute_slots: int = 8
    cell_phase_processes: int = 4
    translation_audit_processes_per_cell: int = 2
    reference_phase_processes: int = 6
```

**Step 4: Thread the budget through execution**

- Distributed generator/checker/audit-generator/audit-checker phases use four top-level workers.
- Their worker entrypoints activate two translation-audit workers.
- The reference phase uses six top-level workers and no nested audit.
- The isolated profile activates four audit workers.
- Existing direct trusted APIs activate a named local budget explicitly.
- Record top-level, nested, and peak-compute counts separately; retain the old result field only as
  the maximum top-level width across all phases. Because the reference phase remains six-wide, the
  frozen v3 `measured_process_count` remains the literal `6`. Nested/peak metrics exist only in the
  new Gate-3 contract and internal telemetry.

**Step 5: Add behavior and cleanup tests**

- Translation audit results are identical under widths 1, 2, and 4.
- Width 1 stays inline and creates no pool.
- Distributed phase calls use 4/2 for nested phases and 6 for reference.
- A synthetic nested failure restores the context and reaps descendants.
- No public CLI worker override appears.

**Step 6: Verify and commit**

```bash
cd yf
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/oracle/test_m8_concurrency.py \
  tests/oracle/test_translation_count_audit.py \
  tests/oracle/test_experiment.py
.venv/bin/ruff check src/yieldforge/oracle tests/oracle
git add yf/src/yieldforge/oracle/concurrency.py \
  yf/src/yieldforge/oracle/translation_count_audit.py \
  yf/src/yieldforge/oracle/certificates.py \
  yf/src/yieldforge/oracle/experiment.py \
  yf/tests/oracle/test_m8_concurrency.py \
  yf/tests/oracle/test_translation_count_audit.py \
  yf/tests/oracle/test_experiment.py
git commit -m "fix: bound M8 nested audit concurrency"
```

### Task 2: Define the fixed-layer fact contracts

**Files:**

- Create: `yf/src/yieldforge/oracle/facts.py`
- Create: `yf/tests/oracle/test_facts.py`
- Modify: `yf/src/yieldforge/oracle/__init__.py`

**Step 1: Write failing canonicalization tests**

Require:

- exact f64 bit-string round trips, including `-0.0` normalization policy;
- canonical encoding of outcome-defining datetimes, including event `occurred_at`, storage
  intervals, `current_time`, and `previous_release`, while excluding only profiling/artifact
  timestamps from semantic hashes;
- domain-separated full SHA-256 node IDs;
- strict schemas for translation batches, common lemmas, influence facts, action roots, and bundle
  roots;
- strict candidate-scalar, frontier-membership, and complete standard-candidate profile/context
  facts;
- deterministic fixed-layer ordering;
- rejection of dangling, duplicate, unused, cross-runtime, cross-stream, and out-of-order facts;
- rejection of unknown modes and noncanonical numbers; and
- byte-identical JSON on repeated construction.

**Step 2: Verify red**

```bash
cd yf
PYTHONPATH=src .venv/bin/python -m pytest -q tests/oracle/test_facts.py
```

Expected: import failure because `oracle.facts` does not exist.

**Step 3: Implement the smallest fixed layers**

Define strict Pydantic contracts:

```python
class M8PortableTranslationBatch(BaselineContractModel): ...
class M8CandidateScalarFactV2(BaselineContractModel): ...
class M8FrontierFactV2(BaselineContractModel): ...
class M8StandardCandidateFactV2(BaselineContractModel): ...
class M8CommonTransitionLemmaV2(BaselineContractModel): ...
class M8InfluenceFactV2(BaselineContractModel): ...
class M8ActionRootV2(BaselineContractModel): ...
class M8UncheckedFactBundleV2(BaselineContractModel): ...
```

Use an allowed dependency matrix rather than generic graph edges. Bundle validation computes
reachability from action roots and rejects unused facts.

**Step 4: Preserve compatibility**

Strict-load the committed v3 M8 artifact and representative v1 action proofs unchanged. New schemas
are selected only by explicit v2 entrypoints.

**Step 5: Verify and commit**

```bash
cd yf
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/oracle/test_facts.py tests/oracle/test_proofs.py tests/oracle/test_experiment.py
.venv/bin/ruff check src/yieldforge/oracle/facts.py tests/oracle/test_facts.py
git add yf/src/yieldforge/oracle/facts.py yf/src/yieldforge/oracle/__init__.py \
  yf/tests/oracle/test_facts.py
git commit -m "feat: define M8 portable fact bundle"
```

### Task 3: Capture all portable common and influence evidence at its source

**Files:**

- Modify: `yf/src/yieldforge/oracle/certificates.py`
- Modify: `yf/src/yieldforge/oracle/sparse.py`
- Modify: `yf/src/yieldforge/oracle/translation_count_audit.py`
- Modify: `yf/tests/oracle/test_fast_common_transition.py`
- Create: `yf/tests/oracle/test_fact_capture.py`

**Step 1: Write failing capture tests**

Require a capture result containing:

- the exact existing `M8CommonTransitionFact`;
- one classification per common inventory item;
- zero-generation scalar witnesses;
- complete counted-no-fit `LayoutTranslationCandidates` batches;
- explicit exact-survivor markers; and
- exact problem, candidate-set, search-config, runtime, and Jagua executable bindings;
- the complete ordered standard action profiles and policy contexts; and
- the complete rejection/search/competitor preimages currently discarded after influence hashing.

The trusted path must remain byte-for-byte equal when capture is disabled.

**Step 2: Verify red**

```bash
cd yf
PYTHONPATH=src .venv/bin/python -m pytest -q tests/oracle/test_fact_capture.py
```

Expected: missing capture API.

**Step 3: Implement capture without broadening authority**

Generalize the fast derivation and influence builder to return their existing results plus portable
evidence before local capabilities are released or influence payloads are reduced to hashes. Do not
serialize Jagua collision masks.

Add two explicit modes:

- `trusted_local`: retains the independent count audit before granting the current capability;
- `unchecked_portable`: captures Jagua translation claims in a producer-only transition record
  without creating a `ValidatedCommonTransition` or entering `_VALIDATED_COMMON_REGISTRY`.

Unsupported representation emits `exact_replay`; it never becomes an implicit fast fact.

Refactor shared mathematical calculations beneath two wrappers:

- the existing trusted wrapper requires a registry-backed common capability and preserves v1
  behavior;
- a new producer-only traversal consumes only the producer transition record and returns unchecked
  witnesses/evidence for bundle construction.

Tests must prove the unchecked path creates no trusted registry entry, cannot be passed to
`certify_event_passivity`, and cannot be returned by any accepted-proof API.

**Step 4: Add perturbation tests**

Mutate count, translation order, foreign point, truncation, candidate identity, scalar certificate,
executable identity, standard candidate context, policy rank, each semantic event/cursor/storage
datetime, and influence search/competitor payload. Capture may construct unchecked bytes, but no
trusted capability may be issued from a perturbed claim.

**Step 5: Verify and commit**

```bash
cd yf
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/oracle/test_fact_capture.py tests/oracle/test_fast_common_transition.py
.venv/bin/ruff check src/yieldforge/oracle/certificates.py tests/oracle/test_fact_capture.py
git add yf/src/yieldforge/oracle/certificates.py \
  yf/src/yieldforge/oracle/translation_count_audit.py \
  yf/src/yieldforge/oracle/sparse.py \
  yf/tests/oracle/test_fast_common_transition.py \
  yf/tests/oracle/test_fact_capture.py
git commit -m "feat: capture M8 common fact evidence"
```

### Task 4: Build an explicitly unchecked bundle generator

**Files:**

- Create: `yf/src/yieldforge/oracle/factored.py`
- Create: `yf/tests/oracle/test_factored_generator.py`
- Modify: `yf/src/yieldforge/oracle/sparse.py`

**Step 1: Write failing generator tests**

Require:

- one common lemma per common event;
- shared lemma IDs across every action root;
- one count-evidence payload per unique counted-no-fit remnant/event;
- deterministic topological serialization;
- v1/v2 normalized equality for scores, states, actions, witnesses, and costs;
- bundle type/name explicitly says unchecked; and
- no evaluation access.

**Step 2: Verify red**

```bash
cd yf
PYTHONPATH=src .venv/bin/python -m pytest -q tests/oracle/test_factored_generator.py
```

**Step 3: Implement the builder**

Add `score_unchecked_fact_bundle(request)` as a new API. It uses the producer-only event-major
traversal from Task 3, captures each common/influence fact, builds action roots after terminal
reconciliation, and never creates or returns a `ValidatedCommonTransition` or other trusted process
capability.

**Step 4: Add cross-process determinism**

Generate the same fixture in two fresh spawned workers, strict-load the JSON, and require identical
semantic bytes and root hashes.

**Step 5: Verify and commit**

```bash
cd yf
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/oracle/test_factored_generator.py tests/oracle/test_sparse.py
.venv/bin/ruff check src/yieldforge/oracle/factored.py tests/oracle/test_factored_generator.py
git add yf/src/yieldforge/oracle/factored.py yf/src/yieldforge/oracle/sparse.py \
  yf/tests/oracle/test_factored_generator.py
git commit -m "feat: generate unchecked M8 fact bundles"
```

### Task 5: Independently validate common lemmas

**Files:**

- Create: `yf/src/yieldforge/oracle/fact_checker.py`
- Create: `yf/tests/oracle/test_fact_checker.py`
- Modify: `yf/src/yieldforge/oracle/certificates.py`

**Step 1: Write failing checker-independence tests**

Patch the factored generator, `certify_event_passivity`, and `_derive_m8_common_transition_fact*` to
raise. A fully fact-certified bundle must still validate. Require the checker to:

- strict-load canonical bundle bytes;
- validate runtime/suffix/current-catalog bindings;
- recompute scalar/frontier implications;
- validate every frozen standard candidate profile/context and independently prove that the selected
  action is the policy minimum;
- reject a self-consistent nonwinner even when every enclosing hash is recomputed;
- independently audit counted-no-fit translation order and counts once per lemma;
- ignore Jagua collision classifications;
- reconcile the portable exact fact algebraically; and
- issue a fresh process-local capability only after all checks pass.

**Step 2: Verify red**

```bash
cd yf
PYTHONPATH=src .venv/bin/python -m pytest -q tests/oracle/test_fact_checker.py
```

**Step 3: Implement the common-lemma checker**

Expose one internal registration boundary that accepts only a checker-owned proof token. Do not
reuse generator registries, mutable caches, or pickled capabilities. `exact_replay` nodes invoke the
current exact path and increment explicit fallback counters.

**Step 4: Add adversarial tests**

For every semantic field, mutate the payload and recompute node and bundle hashes. Require a stable
failure code and first failing fact ID. Include every event `occurred_at`, storage-interval,
`current_time`, and `previous_release` field plus dangling, unused, cross-runtime, and reordered
facts.

**Step 5: Verify and commit**

```bash
cd yf
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/oracle/test_fact_checker.py tests/oracle/test_checker.py \
  tests/oracle/test_translation_count_audit.py
.venv/bin/ruff check src/yieldforge/oracle/fact_checker.py tests/oracle/test_fact_checker.py
git add yf/src/yieldforge/oracle/fact_checker.py \
  yf/src/yieldforge/oracle/certificates.py \
  yf/tests/oracle/test_fact_checker.py
git commit -m "feat: independently check M8 common facts"
```

### Task 6: Traverse influence facts and action roots

**Files:**

- Modify: `yf/src/yieldforge/oracle/fact_checker.py`
- Modify: `yf/src/yieldforge/oracle/checker.py`
- Modify: `yf/tests/oracle/test_fact_checker.py`
- Modify: `yf/tests/oracle/test_exhaustive_certificate_kernel.py`

**Step 1: Write failing traversal tests**

Cover all four witness classifications. Require exact equality with v1 for ordered event positions,
common/branch actions, before/after state hashes, influence bindings/policy keys, final state, and
final cost bits.

**Step 2: Verify red**

Run the two focused test modules and confirm missing traversal behavior.

**Step 3: Implement fixed-layer traversal**

Check every common/influence node once, then traverse all action roots. Permit exact M7 work only
for declared `policy_dominated_exact_check`, `exact_transition`, or `exact_replay` modes. Report
fallback counts and wall time.

**Step 4: Verify exhaustive equality and mutations**

All finite exhaustive cases must match v1. Every altered reference, classification, action, state,
rank, or terminal value fails closed even after enclosing hashes are recomputed.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/oracle/fact_checker.py yf/src/yieldforge/oracle/checker.py \
  yf/tests/oracle/test_fact_checker.py \
  yf/tests/oracle/test_exhaustive_certificate_kernel.py
git commit -m "feat: check M8 fact action roots"
```

### Task 7: Integrate the fresh-process distributed pipeline

**Files:**

- Modify: `yf/src/yieldforge/oracle/experiment.py`
- Modify: `yf/tests/oracle/test_experiment.py`
- Modify: `yf/tests/oracle/test_m8_profiling.py`

**Step 1: Write failing integration tests**

Require generator workers to return unchecked bundles and checker workers to accept only serialized
bundle bytes. Require transport, strict-load, common verification, action traversal, fallback, and
cleanup phases in timing output.

**Step 2: Verify red**

Run the experiment and profiling tests; confirm the old proof-only handoff fails the new assertions.

**Step 3: Integrate without changing the official gate**

Add a calibration-only Gate-3 command/result. Do not replace the frozen v3 official artifact or
change its thresholds. The Gate-3 artifact records bundle root, size, node counts, checker fallback,
outer/nested process counts, and evaluation access.

**Step 4: Test fail-closed transport and cleanup**

Corrupt serialized bytes, kill a worker during nested audit, and force a checker exception. No
partial artifact may publish; all descendants and registries must be empty.

**Step 5: Verify and commit**

```bash
cd yf
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/oracle/test_experiment.py tests/oracle/test_m8_profiling.py
.venv/bin/ruff check src/yieldforge/oracle/experiment.py tests/oracle
git add yf/src/yieldforge/oracle/experiment.py \
  yf/tests/oracle/test_experiment.py yf/tests/oracle/test_m8_profiling.py
git commit -m "feat: run M8 portable fact pipeline"
```

### Task 8: Execute Gate 3 and update the roadmap

**Files:**

- Create: `yf/experiments/results/m8-gate3-portable-fact-evidence-v1.json`
- Update: `Docs/Current Work.md`
- Update: `Docs/Development/M8 Gate 2 Fast Common Transition.md`
- Update: `Docs/Milestones/M8 - Rollout oracle.md`
- Update: `Docs/Milestones/Milestone Roadmap.md`

**Step 1: Run exhaustive and full verification**

```bash
cd yf
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

**Step 2: Run sealed hard/easy Gate-3 probes**

Use the unchanged `no_signal` and `regime_shift` seed `2026082300`, two events, frozen baseline,
candidate archives, and Jagua binary. Charge serialization, transport, child work, and cleanup to
wall time.

**Step 3: Apply Gate 3**

Pass only if:

- 887/887 action roots validate;
- v1/v2/reference mismatch count is zero;
- adversarial mutation rejection is 100%;
- count audit occurs once per unique counted-no-fit lemma;
- real probes have zero hidden/exact checker fallback;
- two fresh generations have identical root hashes;
- peak compute is at most eight slots;
- evaluation access is false; and
- no worker or capability survives.

If the proof gate passes, run the abbreviated `25x`/five-day performance gate. Do not run the
official six-cell gate until both pass.

**Step 4: Commit the decision evidence**

Commit compact hashes/metrics, not ignored raw profiles. State explicitly that Gate 3 is software
calibration evidence only and does not establish M8 advantage or savings.
