# M8 Sparse Exact Rollout Oracle Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and execute the complete-candidate, full-horizon M8 rollout oracle through a sparse
exact delta evaluator that must match brute force and project no more than seven days of held-out
execution.

**Architecture:** Expose M7's exact action/transition seam, then build a deliberately slow reference
oracle for correctness. Compile inventory-independent M7 standard winners and safe future-fit
rejection certificates so the sparse evaluator can skip passive stretches and branch only where an
inventory delta can change a future action. Persistent caching and complete-stream orchestration are
authorized only after a calibration-prefix proof records zero mismatches and at least 20x speedup.

**Tech Stack:** Python 3.12, Pydantic v2, Shapely 2.1.2, pinned Jagua 0.7.0 extension, standard
library SQLite, pytest, Ruff, existing argparse CLI and content-addressed JSON artifact conventions.

---

### Task 1: Expose exact M7 actions and arbitrary-state continuation

**Files:**
- Modify: `yf/src/yieldforge/baseline/contracts.py`
- Modify: `yf/src/yieldforge/baseline/replay.py`
- Modify: `yf/src/yieldforge/baseline/__init__.py`
- Modify: `yf/tests/baseline/test_replay.py`

**Step 1: Write failing public-seam tests**

Require complete standard/remnant descriptors, the exact frozen-policy fallback, one-action
execution from an arbitrary cursor, and continuation from a nonzero event position.

```python
def test_catalog_contains_exact_m7_fallback(replay_fixture):
    cursor = initial_m7_cursor(replay_fixture.replay_input)
    catalog = enumerate_m7_action_catalog(
        replay_fixture.runtime,
        cursor=cursor,
        event_position=0,
    )
    fallback = select_m7_fallback(catalog, policy=replay_fixture.replay_input.policy)
    assert fallback.action_id in {item.action_id for item in catalog.actions}
    assert len(catalog.actions) == catalog.standard_action_count + catalog.remnant_action_count


def test_nonzero_continuation_matches_complete_replay(replay_fixture):
    complete = run_m7_replay(**replay_fixture.arguments)
    cursor = cursor_after_event(complete, sequence=0)
    continued = run_m7_continuation(
        replay_fixture.runtime,
        cursor=cursor,
        next_event_position=1,
    )
    assert continued.events == complete.events[1:]
    assert continued.final_costs == complete.terminal.cumulative_costs
```

**Step 2: Run the focused test and confirm failure**

Run: `cd yf && uv run pytest tests/baseline/test_replay.py -q`

Expected: FAIL because the cursor, catalog, fallback, transition, and continuation APIs do not exist.

**Step 3: Implement the minimal seam**

Add immutable `M7ReplayCursor`, `M7ActionDescriptor`, `M7ActionCatalog`, and `M7StepResult` types.
Extract `initial_m7_cursor`, `enumerate_m7_action_catalog`, `select_m7_fallback`,
`apply_m7_action_descriptor`, and `run_m7_continuation`. Keep `run_m7_replay` as the compatibility
caller and preserve its persisted schema and lazy M7 winner materialization.

**Step 4: Verify M7 compatibility**

Run: `cd yf && uv run pytest tests/baseline/test_replay.py tests/baseline/test_experiment.py -q`

Expected: PASS with fixture-level pre/post-refactor content identity.

Run: `cd yf && uv run ruff check src/yieldforge/baseline tests/baseline`

Expected: PASS.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/baseline yf/tests/baseline
git commit -m "refactor: expose exact M7 transition seam"
```

### Task 2: Build strict M8 contracts and the slow reference oracle

**Files:**
- Create: `yf/src/yieldforge/oracle/__init__.py`
- Create: `yf/src/yieldforge/oracle/contracts.py`
- Create: `yf/src/yieldforge/oracle/reference.py`
- Create: `yf/src/yieldforge/oracle/visibility.py`
- Create: `yf/tests/oracle/__init__.py`
- Create: `yf/tests/oracle/fixtures.py`
- Create: `yf/tests/oracle/test_contracts.py`
- Create: `yf/tests/oracle/test_reference.py`
- Create: `yf/tests/oracle/test_visibility.py`

**Step 1: Write failing contract and information-isolation tests**

Bind the M0/M6/M7 identities, complete remaining horizon, full and known-only visibility, exact M7
fallback tie preference, no candidate cap, and strict failure evidence. M6 known-only must expose an
empty future because it has no pre-release `known_at` field.

```python
def test_m6_known_only_has_no_visible_suffix(m8_fixture):
    provider = KnownOnlyVisibility(m8_fixture.stream)
    assert provider.visible_suffix(current_position=0) == ()


def test_hidden_suffix_does_not_change_known_only(m8_fixture):
    left = KnownOnlyVisibility(m8_fixture.stream)
    right = KnownOnlyVisibility(m8_fixture.mutate_hidden_suffix())
    assert left.visible_suffix(current_position=3) == right.visible_suffix(current_position=3)
```

**Step 2: Write failing reference-oracle tests**

The reference must materialize every current action, replay the complete visible suffix through M7,
apply the registered terminal ledger, and select by
`(final_net_cost, action_id != baseline_action_id, action_id)`.

```python
def test_reference_delayed_reuse_case(delayed_reuse_case):
    result = score_reference_event(delayed_reuse_case.request)
    assert result.scores_by_action == {
        "myopic-cheap": 200.0,
        "keep-future-fit": 101.0,
    }
    assert result.selected_action_id == "keep-future-fit"


def test_reference_exact_tie_prefers_m7_fallback(no_signal_case):
    result = score_reference_event(no_signal_case.request)
    assert result.selected_action_id == result.baseline_action_id
```

**Step 3: Run and confirm failure**

Run: `cd yf && uv run pytest tests/oracle/test_contracts.py tests/oracle/test_visibility.py tests/oracle/test_reference.py -q`

Expected: FAIL because the oracle package does not exist.

**Step 4: Implement contracts, visibility providers, and slow reference**

Define strict content-addressed request, score, decision, stream, and failure models. Full visibility
returns the exact post-current suffix; known-only requires explicit `known_at` evidence. Implement
the straightforward per-action full replay with no performance shortcuts. Label it test/reference
only so production evaluation cannot invoke it accidentally.

**Step 5: Verify and commit**

Run: `cd yf && uv run pytest tests/oracle/test_contracts.py tests/oracle/test_visibility.py tests/oracle/test_reference.py -q`

Expected: PASS.

Run: `cd yf && uv run ruff check src/yieldforge/oracle tests/oracle`

Expected: PASS.

```bash
git add yf/src/yieldforge/oracle yf/tests/oracle
git commit -m "feat: add M8 reference oracle"
```

### Task 3: Compile standard winners and safe relevance certificates

**Files:**
- Create: `yf/src/yieldforge/oracle/compiled.py`
- Create: `yf/tests/oracle/test_compiled.py`
- Modify: `yf/src/yieldforge/baseline/geometry.py`
- Modify: `yf/tests/baseline/test_geometry.py`

**Step 1: Write failing compiled-standard tests**

For every test problem, compile the `age_regularity` winner among standard profiles and require it to
equal ordinary M7 selection for empty inventory.

```python
def test_compiled_standard_winner_matches_m7(compiled_fixture):
    compiled = compile_standard_winner(compiled_fixture.problem)
    ordinary = compiled_fixture.select_with_empty_inventory()
    assert compiled.action_id == ordinary.action_id
    assert compiled.decision_key == ordinary.decision_key
```

**Step 2: Write failing safe-rejection tests**

Reject a fixed-orientation rigid layout only when material differs, footprint area exceeds remnant
area beyond tolerance, footprint width exceeds remnant width, or footprint height exceeds remnant
height. Every rejected pair must produce no fit under the registered exact search; only geometric
bound rejects are expected to produce an empty translation sequence. Survivors are unknown, never
presumed feasible.

```python
@pytest.mark.parametrize("case", SAFE_REJECTION_CASES)
def test_safe_rejection_has_no_registered_fit(case):
    certificate = certify_translation_impossible(case.layout, case.remnant, case.config)
    assert certificate.impossible
    assert search_layout_translation(**case.arguments).status is NO_WITNESS


def test_prefilter_never_rejects_known_fit(known_fit_case):
    certificate = certify_translation_impossible(
        known_fit_case.layout,
        known_fit_case.remnant,
        known_fit_case.config,
    )
    assert not certificate.impossible
```

**Step 3: Run and confirm failure**

Run: `cd yf && uv run pytest tests/oracle/test_compiled.py tests/baseline/test_geometry.py -q`

Expected: FAIL because compiled winners and certificates do not exist.

**Step 4: Implement compilation and certificates**

Persist exact candidate/profile hashes with each compiled winner. Add a strict
`TranslationRejectionCertificate` recording the first valid rejection reason and all compared
values. Compile a suffix index by material and footprint bounds so impossible event/candidate pairs
can be skipped without GEOS or Jagua.

**Step 5: Differentially audit against existing M7 fixtures**

Run every archived test layout/remnant pair through both the prefilter and registered search. Require
zero false rejections and identical winners.

Run: `cd yf && uv run pytest tests/oracle/test_compiled.py tests/baseline/test_geometry.py tests/baseline/test_replay.py -q`

Expected: PASS.

**Step 6: Commit**

```bash
git add yf/src/yieldforge/oracle/compiled.py yf/tests/oracle/test_compiled.py \
  yf/src/yieldforge/baseline/geometry.py yf/tests/baseline/test_geometry.py
git commit -m "feat: compile exact M8 future relevance"
```

### Task 4: Implement sparse delta replay and zero-mismatch tests

**Files:**
- Create: `yf/src/yieldforge/oracle/sparse.py`
- Create: `yf/tests/oracle/test_sparse.py`
- Modify: `yf/src/yieldforge/oracle/contracts.py`

**Step 1: Write failing passive-delta tests**

Prove that a branch-only remnant with complete no-fit certificates across the suffix changes only
storage and terminal liquidation. Added/removed remnant deltas must reconcile against the reference.

```python
def test_passive_remnant_is_charged_without_future_replay(passive_case):
    sparse = score_sparse_event(passive_case.request)
    reference = score_reference_event(passive_case.request)
    assert sparse.decision == reference.decision
    assert sparse.scores == reference.scores
    assert sparse.metrics.skipped_passive_event_count > 0
    assert sparse.metrics.exact_branch_event_count == 0
```

**Step 2: Write failing branch/rejoin tests**

Require exact replay at the first potentially relevant event, exact M7 policy comparison when a fit
exists, continuation after consumption, and memoization only after canonical state equality.

```python
def test_sparse_branch_matches_reference_at_first_future_fit(future_fit_case):
    sparse = score_sparse_event(future_fit_case.request)
    reference = score_reference_event(future_fit_case.request)
    assert sparse.scores == reference.scores
    assert sparse.selected_action_id == reference.selected_action_id
    assert sparse.metrics.exact_branch_event_count == 1
```

**Step 3: Run and confirm failure**

Run: `cd yf && uv run pytest tests/oracle/test_sparse.py -q`

Expected: FAIL because the sparse evaluator does not exist.

**Step 4: Implement minimal exact delta evaluation**

Represent branch state as added/removed inventory plus ledger delta against the common M7 cursor.
Jump across a future interval only when every delta remnant has a complete no-fit certificate and no
removed remnant is selected by the common path in that interval. Add storage and terminal effects
with the existing M0 rounding functions. At a potentially relevant event, reconstruct the exact
cursor, invoke registered fit search, execute M7's actual winner, and continue from the new delta.

**Step 5: Differentially compare all registered small cases**

For every action in every toy event, compare final score, selected action, action evidence, inventory,
ledger, and terminal result against the reference. Do not compare wall-clock/cache metrics.

Run: `cd yf && uv run pytest tests/oracle/test_sparse.py tests/oracle/test_reference.py -q`

Expected: PASS with zero mismatch.

Run: `cd yf && uv run ruff check src/yieldforge/oracle tests/oracle`

Expected: PASS.

**Step 6: Commit**

```bash
git add yf/src/yieldforge/oracle yf/tests/oracle
git commit -m "feat: add sparse exact M8 delta replay"
```

### Task 5: Execute the 20x calibration-prefix proof

**Files:**
- Create: `yf/src/yieldforge/oracle/experiment.py`
- Create: `yf/tests/oracle/test_experiment.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/test_cli.py`
- Create: `yf/experiments/results/m8-sparse-proof-<id>.json`

**Step 1: Write failing proof-contract tests**

Freeze one short prefix from the first calibration stream in every regime, all current actions in
those prefixes, reference/sparse semantic equality, runtime fields, and fail-closed decisions:
`pass_sparse_exact`, `redesign_sparse_exact`, or `require_distributed_exact`.

**Step 2: Run and confirm failure**

Run: `cd yf && uv run pytest tests/oracle/test_experiment.py tests/test_cli.py -q`

Expected: FAIL because the sparse proof command and artifact do not exist.

**Step 3: Implement `benchmark m8-sparse-proof`**

Verify candidate evidence once, execute reference then sparse in the frozen order, compare every
semantic field, and report action count, brute and sparse event executions, rejection counts, exact
searches, state rejoins, elapsed seconds, speedup, and seven-day held-out projection. The command
must never load an M8 evaluation stream.

**Step 4: Execute the bounded proof**

Run the six registered prefixes within a maximum 24-hour proof budget. Publish all completed cells
and failures; never replace a prefix or seed.

**Step 5: Apply the hard gate**

Continue locally only when:

- semantic mismatch count is zero;
- end-to-end sparse speedup is at least 20x;
- every registered proof cell completes; and
- the conservative eight-worker held-out projection is no more than seven calendar days.

Otherwise stop before persistent-cache or full-stream work and choose further exact optimization or
a separately frozen distributed execution plan.

**Step 6: Verify and commit**

Run: `cd yf && uv run pytest tests/oracle/test_experiment.py tests/test_cli.py -q`

Expected: PASS.

Run: `cd yf && uv run ruff check src/yieldforge tests`

Expected: PASS.

```bash
git add yf/src/yieldforge/oracle yf/src/yieldforge/cli.py yf/tests/oracle \
  yf/tests/test_cli.py yf/experiments/results/m8-sparse-proof-*.json
git commit -m "experiment: prove sparse exact M8 replay"
```

### Task 6: Add persistent caches and complete-stream checkpoints

**Gate:** Do not begin unless Task 5 records `pass_sparse_exact`.

**Files:**
- Create: `yf/src/yieldforge/oracle/cache.py`
- Create: `yf/src/yieldforge/oracle/stream.py`
- Create: `yf/tests/oracle/test_cache.py`
- Create: `yf/tests/oracle/test_stream.py`

**Step 1: Write failing cache tests**

Cover cold miss, validated warm hit, identical concurrent insertion, same-key conflict, payload
tampering, disabled cache, interrupted resume, and one/two/eight-worker semantic identity.

```python
def test_same_key_different_payload_fails_closed(tmp_path):
    cache = ExactRuntimeCache(tmp_path / "m8.sqlite3")
    cache.put(kind="fit_search", key={"geometry": "a"}, payload={"fit": True})
    with pytest.raises(CacheConflictError, match="same key has different payload"):
        cache.put(kind="fit_search", key={"geometry": "a"}, payload={"fit": False})
```

**Step 2: Write failing complete-stream tests**

Require re-scoring at every real event, M7-only hypothetical continuations, full and known-only
actual trajectories, candidate parity, fallback presence, deterministic resume, and fail-closed
partial streams.

**Step 3: Implement SQLite cache and stream runner**

Use WAL mode and canonical `(kind, key_sha256)` identities. Persist key JSON, payload JSON, payload
SHA-256, schema, and engine identity. Checkpoint after every scored action and completed event.
Validate every loaded value; corruption is not a miss.

**Step 4: Verify and commit**

Run: `cd yf && uv run pytest tests/oracle/test_cache.py tests/oracle/test_stream.py -q`

Expected: PASS.

```bash
git add yf/src/yieldforge/oracle yf/tests/oracle
git commit -m "feat: persist sparse M8 execution"
```

### Task 7: Run the six-stream pilot and freeze held-out execution

**Files:**
- Modify: `yf/src/yieldforge/oracle/experiment.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/oracle/test_experiment.py`
- Modify: `yf/tests/test_cli.py`
- Create: `yf/experiments/results/m8-calibration-pilot-<id>.json`
- Create: `yf/experiments/results/m8-rollout-freeze-v1.json`

**Step 1: Write failing pilot/freeze tests**

Require one complete calibration stream per regime, full and known-only modes, zero evaluation
streams, complete action scoring, repeated semantic identity, runtime/cache metrics, and a
conservative projection no greater than seven days.

**Step 2: Implement and execute `benchmark m8-pilot`**

Run the six streams with at most eight workers, then repeat from the warm cache. Persist all failures
and exact proof bindings.

**Step 3: Freeze or stop**

Publish `m8-rollout-freeze-v1.json` only if every stream passes and the complete-pilot projection
remains within seven days. Otherwise preserve the pilot and stop local execution.

**Step 4: Verify and commit**

Run: `cd yf && uv run pytest tests/oracle tests/test_cli.py -q`

Expected: PASS.

```bash
git add yf/src/yieldforge/oracle yf/src/yieldforge/cli.py yf/tests/oracle \
  yf/tests/test_cli.py yf/experiments/results/m8-*.json
git commit -m "experiment: freeze sparse M8 execution"
```

### Task 8: Execute evaluation, publish paired evidence, and prepare M9

**Gate:** Do not begin unless Task 7 published a valid frozen execution artifact.

**Files:**
- Modify: `yf/src/yieldforge/oracle/experiment.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/oracle/test_experiment.py`
- Modify: `yf/tests/test_cli.py`
- Modify: `Docs/Milestones/M8 - Rollout oracle.md`
- Modify: `Docs/Milestones/M9 - Search validation.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`
- Modify: `Docs/Current Work.md`
- Create: `yf/experiments/results/m8-evaluation-<id>.json`

**Step 1: Write failing evaluation-boundary tests**

Require the exact 36-stream evaluation partition, immutable M7 baseline, both visibility modes, no
calibration reselection, no substitution, complete failure denominator, repeated semantic identity,
and deterministic M0 paired summaries.

**Step 2: Reproduce the M7 compatibility identity**

Through the refactored seam, require M7 evaluation reproducibility SHA-256
`47cc40ff16ab71f70163df23bb1a346c061d2765d2e2113eca5f0c06e5756cf8`.

**Step 3: Execute frozen held-out M8**

Run full and known-only on all 36 streams, checkpoint every action/event, and abort rather than exceed
the frozen seven-day execution boundary. A boundary breach produces no M8 savings result and moves
execution to the distributed-exact path; it never truncates a stream.

**Step 4: Publish paired evidence**

Compute `OracleSavings`, `UnknownFutureContribution`, the frozen 10,000-resample stratified bootstrap
with seed zero, no-signal and concentration diagnostics, action divergence, immediate sacrifice,
reuse realization, and explicit claim ceilings. Do not emit M10's final project verdict.

**Step 5: Run final verification**

Run: `cd yf && uv run pytest -q`

Expected: the full suite passes; environment-gated skips are reported.

Run: `cd yf && uv run ruff check src tests`

Expected: PASS.

Run: `cd yf && uv build`

Expected: source distribution and wheel build successfully.

**Step 6: Commit**

```bash
git add yf/src/yieldforge/oracle yf/src/yieldforge/cli.py yf/tests/oracle \
  yf/tests/test_cli.py yf/experiments/results/m8-evaluation-*.json Docs
git commit -m "docs: close sparse M8 and prepare search validation"
```
