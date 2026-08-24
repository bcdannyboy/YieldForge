# M8 Full-Horizon Rollout Oracle Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build, validate, freeze, and execute an exact full-remaining-horizon rollout policy over
the M6 streams using the frozen M7 policy as every hypothetical continuation.

**Architecture:** First expose M7's existing action generation and state transition as a reusable
arbitrary-state seam while preserving published M7 identities. Add a strict persistent cache, then
build an isolated rollout kernel that scores every common current action through frozen-M7
continuation, with full and known-only visibility supplied by separate providers. Calibration-only
acceptance and runtime evidence freeze the execution manifest before held-out evaluation opens.

**Tech Stack:** Python 3.12, Pydantic v2, Shapely 2.1.2, pinned Jagua 0.7.0 extension, standard
library SQLite, pytest, Ruff, existing argparse CLI and content-addressed JSON artifact conventions.

---

### Task 1: Expose exact M7 actions and arbitrary-state continuation

**Files:**
- Modify: `yf/src/yieldforge/baseline/contracts.py`
- Modify: `yf/src/yieldforge/baseline/replay.py`
- Modify: `yf/src/yieldforge/baseline/__init__.py`
- Modify: `yf/tests/baseline/test_replay.py`

**Step 1: Write the failing descriptor and transition tests**

Add tests that require the public seam to enumerate every standard candidate plus every feasible
remnant candidate, retain the existing M7-selected fallback, apply one descriptor from an arbitrary
state, and continue from a nonzero event position.

```python
def test_action_catalog_contains_exact_m7_fallback(replay_fixture):
    cursor = initial_m7_cursor(replay_fixture.replay_input)
    catalog = enumerate_m7_action_catalog(
        replay_fixture.runtime,
        cursor=cursor,
        event_position=0,
    )
    fallback = select_m7_fallback(catalog, policy=replay_fixture.replay_input.policy)
    assert fallback.action_id in {item.action_id for item in catalog.actions}
    assert len(catalog.actions) == catalog.standard_action_count + catalog.remnant_action_count


def test_continuation_from_selected_action_matches_complete_replay(replay_fixture):
    complete = run_m7_replay(**replay_fixture.arguments)
    cursor = initial_m7_cursor(replay_fixture.replay_input)
    first = apply_m7_action_descriptor(
        replay_fixture.runtime,
        cursor=cursor,
        event_position=0,
        descriptor=descriptor_for(complete.events[0].action),
    )
    continued = run_m7_continuation(
        replay_fixture.runtime,
        cursor=first.cursor_after,
        next_event_position=1,
    )
    assert continued.final_costs == complete.terminal.cumulative_costs
    assert continued.events == complete.events[1:]
```

**Step 2: Run the focused tests and verify the expected failure**

Run: `cd yf && uv run pytest tests/baseline/test_replay.py -q`

Expected: FAIL because the public cursor, catalog, descriptor application, and continuation APIs do
not exist.

**Step 3: Implement the minimal public seam**

Add immutable `M7ReplayCursor`, `M7ActionDescriptor`, `M7ActionCatalog`, and `M7StepResult` types.
Refactor the existing private generation path so M7 can keep lazy winner materialization while M8
can iterate every exact descriptor. Add `initial_m7_cursor`, `enumerate_m7_action_catalog`,
`apply_m7_action_descriptor`, and `run_m7_continuation`. Keep `run_m7_replay` as the compatibility
caller and do not change its persisted schema.

**Step 4: Verify focused and published-result compatibility**

Run: `cd yf && uv run pytest tests/baseline/test_replay.py tests/baseline/test_experiment.py -q`

Expected: PASS, including fixture-level byte identity for the pre-refactor replay.

Run: `cd yf && uv run ruff check src/yieldforge/baseline tests/baseline`

Expected: PASS.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/baseline yf/tests/baseline
git commit -m "refactor: expose exact M7 transition seam"
```

### Task 2: Add strict persistent exact caching

**Files:**
- Create: `yf/src/yieldforge/oracle/__init__.py`
- Create: `yf/src/yieldforge/oracle/cache.py`
- Create: `yf/tests/oracle/__init__.py`
- Create: `yf/tests/oracle/test_cache.py`
- Modify: `yf/src/yieldforge/baseline/replay.py`

**Step 1: Write failing cache tests**

Cover cold miss, validated warm hit, identical concurrent insertion, same-key conflict, payload
tampering, schema/version mismatch, disabled-cache behavior, and checkpoint resume.

```python
def test_same_key_different_payload_fails_closed(tmp_path):
    cache = ExactRuntimeCache(tmp_path / "m8.sqlite3")
    cache.put(kind="fit_search", key={"geometry": "a"}, payload={"fit": True})
    with pytest.raises(CacheConflictError, match="same key has different payload"):
        cache.put(kind="fit_search", key={"geometry": "a"}, payload={"fit": False})


def test_disabled_and_warm_cache_are_semantically_identical(oracle_fixture, tmp_path):
    uncached = oracle_fixture.run(cache=None)
    cold = oracle_fixture.run(cache=ExactRuntimeCache(tmp_path / "m8.sqlite3"))
    warm = oracle_fixture.run(cache=ExactRuntimeCache(tmp_path / "m8.sqlite3"))
    assert uncached == cold == warm
```

**Step 2: Run and confirm failure**

Run: `cd yf && uv run pytest tests/oracle/test_cache.py -q`

Expected: FAIL because `yieldforge.oracle.cache` does not exist.

**Step 3: Implement the cache**

Use SQLite WAL mode with a table keyed by `(kind, key_sha256)`. Persist canonical key JSON,
canonical payload JSON, payload SHA-256, schema version, and engine identity. Use transactions plus
`INSERT OR IGNORE`; validate an ignored row is byte-identical. Strictly validate every returned
payload. Add checkpoint rows keyed by freeze, stream, visibility, event position, and action
position. Never treat corruption as a miss.

Integrate optional cache adapters for standard profiles, prepared footprints, and fit-search
results without changing M7 content identities.

**Step 4: Verify cache and M7 regression tests**

Run: `cd yf && uv run pytest tests/oracle/test_cache.py tests/baseline/test_replay.py -q`

Expected: PASS.

Run: `cd yf && uv run ruff check src/yieldforge/oracle src/yieldforge/baseline tests/oracle`

Expected: PASS.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/oracle yf/src/yieldforge/baseline/replay.py yf/tests/oracle
git commit -m "feat: persist exact M8 runtime caches"
```

### Task 3: Freeze M8 contracts and future-visibility isolation

**Files:**
- Create: `yf/src/yieldforge/oracle/contracts.py`
- Create: `yf/src/yieldforge/oracle/visibility.py`
- Create: `yf/tests/oracle/test_contracts.py`
- Create: `yf/tests/oracle/test_visibility.py`

**Step 1: Write failing strict-contract tests**

Require content-addressed identities, complete remaining horizon, M7 fallback tie preference, no
candidate cap, maximum eight local workers in the preparation manifest, and exact binding to the
M0/M6/M7 artifacts. Reject extra fields, nonfinite numbers, altered hashes, and unregistered
visibility modes.

```python
def test_m6_known_only_has_no_visible_future(m8_fixture):
    provider = KnownOnlyVisibility(m8_fixture.stream)
    assert provider.visible_suffix(current_position=0) == ()


def test_hidden_suffix_never_reaches_known_only(m8_fixture):
    left = KnownOnlyVisibility(m8_fixture.stream)
    right = KnownOnlyVisibility(m8_fixture.mutate_hidden_suffix())
    assert left.visible_suffix(current_position=3) == right.visible_suffix(current_position=3)
```

**Step 2: Run and confirm failure**

Run: `cd yf && uv run pytest tests/oracle/test_contracts.py tests/oracle/test_visibility.py -q`

Expected: FAIL because the contracts and visibility providers do not exist.

**Step 3: Implement contracts and providers**

Define strict models for `M8OracleContract`, `M8RuntimeManifest`, `M8ActionRolloutScore`,
`M8EventDecision`, `M8StreamResult`, and failure evidence. Define only
`full_realized_future` and `known_only` visibility modes. Full visibility returns the exact
post-current suffix; known-only requires explicit `known_at` evidence and returns empty for M6.
Neither provider exposes regime labels to the action policy.

**Step 4: Verify**

Run: `cd yf && uv run pytest tests/oracle/test_contracts.py tests/oracle/test_visibility.py -q`

Expected: PASS.

Run: `cd yf && uv run ruff check src/yieldforge/oracle tests/oracle`

Expected: PASS.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/oracle yf/tests/oracle
git commit -m "feat: freeze M8 oracle and visibility contracts"
```

### Task 4: Implement the one-step rollout kernel with hand-computed cases

**Files:**
- Create: `yf/src/yieldforge/oracle/rollout.py`
- Create: `yf/tests/oracle/test_rollout.py`
- Create: `yf/tests/oracle/fixtures.py`

**Step 1: Write failing independent kernel tests**

Build a tiny injected action graph with hand-computed ledgers. Test delayed reuse, fallback tie,
policy improvement, complete horizon, terminal liquidation, exact state coalescing, and deterministic
action order before invoking real polygon machinery.

```python
def test_delayed_reuse_beats_cheaper_current_layout(delayed_reuse_case):
    result = choose_rollout_action(delayed_reuse_case.request)
    assert result.selected_action_id == "keep-future-fit"
    assert result.scores_by_action == {
        "myopic-cheap": 200.0,
        "keep-future-fit": 101.0,
    }


def test_exact_tie_prefers_frozen_baseline_action(no_signal_case):
    result = choose_rollout_action(no_signal_case.request)
    assert result.selected_action_id == result.baseline_action_id
    assert result.selected_score == result.baseline_score
```

**Step 2: Run and confirm failure**

Run: `cd yf && uv run pytest tests/oracle/test_rollout.py -q`

Expected: FAIL because the rollout kernel does not exist.

**Step 3: Implement the minimal kernel**

For each descriptor, apply the current transition, obtain the visibility-provided suffix, run the
frozen M7 continuation, and score the final M0 ledger. Select by
`(final_net_cost, action_id != baseline_action_id, action_id)`. Hash the exact cursor and suffix for
continuation caching. Coalesce only identical hashes after full canonical equality validation.

**Step 4: Add real M7 integration tests**

Use small existing Shapely fixtures to prove the M7 selected action is present, every scored action
can execute, current action counts match, and changing hidden future geometry affects only full
visibility.

**Step 5: Verify and commit**

Run: `cd yf && uv run pytest tests/oracle/test_rollout.py tests/baseline/test_replay.py -q`

Expected: PASS.

Run: `cd yf && uv run ruff check src/yieldforge/oracle tests/oracle`

Expected: PASS.

```bash
git add yf/src/yieldforge/oracle yf/tests/oracle
git commit -m "feat: add exact M8 rollout kernel"
```

### Task 5: Execute complete full and known-only stream policies

**Files:**
- Create: `yf/src/yieldforge/oracle/stream.py`
- Create: `yf/tests/oracle/test_stream.py`
- Modify: `yf/src/yieldforge/oracle/contracts.py`

**Step 1: Write failing stream tests**

Test that the oracle re-scores at every real event, hypothetical futures remain M7-only, equal
timestamps preserve source subsequence, actual known-only costs use the realized stream without
revealing it early, and any branch/parity/fallback failure invalidates the stream.

```python
def test_oracle_reoptimizes_only_the_real_current_action(stream_case):
    result = run_m8_stream(stream_case.full_request)
    assert len(result.events) == len(stream_case.instances)
    assert all(event.hypothetical_policy == "age_regularity" for event in result.events)
    assert tuple(event.sequence for event in result.events) == tuple(range(len(result.events)))


def test_missing_fallback_invalidates_stream(stream_case):
    request = stream_case.without_fallback_at(sequence=1)
    with pytest.raises(M8ParityError, match="baseline fallback"):
        run_m8_stream(request)
```

**Step 2: Run and confirm failure**

Run: `cd yf && uv run pytest tests/oracle/test_stream.py -q`

Expected: FAIL because stream execution is absent.

**Step 3: Implement stream execution and resume**

Execute selected actions on one real cursor, rebuild the current catalog at every event, and persist
per-action and per-event checkpoints. Resume only after validating the entire bound request and
checkpoint hashes. Persist full decision evidence but keep wall-clock/cache observations outside
semantic result identity.

**Step 4: Verify deterministic replay**

Run each toy stream cache-disabled, cold, warm, and with worker counts one and two. Require identical
stream result IDs and event decisions.

Run: `cd yf && uv run pytest tests/oracle/test_stream.py tests/oracle/test_cache.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/oracle yf/tests/oracle
git commit -m "feat: add deterministic M8 stream replay"
```

### Task 6: Run the calibration-only runtime pilot and freeze execution

**Files:**
- Create: `yf/src/yieldforge/oracle/experiment.py`
- Create: `yf/tests/oracle/test_experiment.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/test_cli.py`
- Create: `yf/experiments/results/m8-calibration-pilot-<id>.json`
- Create: `yf/experiments/results/m8-rollout-freeze-v1.json`

**Step 1: Write failing pilot and freeze tests**

Require one immutable calibration stream per regime, zero evaluation streams, both visibility modes,
complete action scoring, runtime/cache/storage measurements, idempotent publication, and a freeze
that cannot exist until all acceptance gates pass. Reject any horizon or candidate truncation.

**Step 2: Run and confirm failure**

Run: `cd yf && uv run pytest tests/oracle/test_experiment.py tests/test_cli.py -q`

Expected: FAIL because the M8 commands and artifacts do not exist.

**Step 3: Implement pilot orchestration and publishers**

Add `benchmark m8-pilot` and `benchmark m8-freeze`. Reuse the canonical M7 archive loader and verify
all problem/candidate evidence before replay. Record semantic results separately from runtime
observations. Freeze exact horizon, tie rule, cache schema, worker ceiling, retry rule, and bound
artifact hashes.

**Step 4: Execute the six-stream calibration pilot**

Run the smallest exact smoke prefix first to estimate checkpoint volume, then execute the six full
registered calibration streams. Do not inspect or execute M8 evaluation streams. Repeat from the
warm cache and require identical semantic content.

**Step 5: Apply the execution-readiness gate**

If all six streams complete and the measured projection has a practical exact local or distributed
completion path, publish `m8-rollout-freeze-v1.json`. Otherwise publish only the pilot with
`technical_decision = optimize_exact_runtime` and continue performance work without weakening the
primary semantics.

**Step 6: Verify and commit**

Run: `cd yf && uv run pytest tests/oracle tests/test_cli.py -q`

Expected: PASS.

Run: `cd yf && uv run ruff check src/yieldforge tests`

Expected: PASS.

```bash
git add yf/src/yieldforge/oracle yf/src/yieldforge/cli.py yf/tests/oracle \
  yf/tests/test_cli.py yf/experiments/results/m8-*.json
git commit -m "experiment: freeze M8 rollout execution"
```

### Task 7: Reproduce M7 and execute held-out M8 evaluation

**Gate:** Do not begin this task unless Task 6 published a valid frozen M8 execution artifact.

**Files:**
- Modify: `yf/src/yieldforge/oracle/experiment.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/oracle/test_experiment.py`
- Modify: `yf/tests/test_cli.py`
- Create: `yf/experiments/results/m8-evaluation-<id>.json`

**Step 1: Write failing evaluation-boundary tests**

Require the frozen M8 artifact, exact 36-stream evaluation partition, immutable M7 paired baseline,
both visibility modes, no calibration reselection, no per-stream substitution, complete failure
denominator, and repeated semantic identity.

**Step 2: Reproduce the M7 compatibility identity**

Regenerate the canonical M7 evaluation semantic content through the refactored seam and require
reproducibility SHA-256 `47cc40ff16ab71f70163df23bb1a346c061d2765d2e2113eca5f0c06e5756cf8`.

**Step 3: Implement and execute frozen evaluation**

Run full and known-only M8 over all 36 evaluation streams. Checkpoint every action/event, retain all
failures, and publish no savings for an invalid paired stream. Repeat from the validated warm cache
and require identical content.

**Step 4: Verify and commit**

Run: `cd yf && uv run pytest tests/oracle/test_experiment.py tests/test_cli.py -q`

Expected: PASS.

```bash
git add yf/src/yieldforge/oracle yf/src/yieldforge/cli.py yf/tests/oracle \
  yf/tests/test_cli.py yf/experiments/results/m8-evaluation-*.json
git commit -m "experiment: execute frozen M8 rollout evaluation"
```

### Task 8: Publish paired M8 evidence and prepare M9

**Files:**
- Modify: `yf/src/yieldforge/oracle/experiment.py`
- Modify: `yf/tests/oracle/test_experiment.py`
- Modify: `Docs/Milestones/M8 - Rollout oracle.md`
- Modify: `Docs/Milestones/M9 - Search validation.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`
- Modify: `Docs/Current Work.md`

**Step 1: Write failing paired-summary tests**

Require `OracleSavings`, `UnknownFutureContribution`, every M0 registered summary, no-signal and
concentration diagnostics, action divergence, immediate sacrifice, reuse realization, and explicit
claim ceilings. Verify the publisher cannot emit the final M10 green/yellow/red verdict.

**Step 2: Implement deterministic summaries**

Use the paired stream as the unit and the frozen 10,000-resample stratified percentile bootstrap
with seed zero. Keep baseline, full, and known-only costs individually auditable. Report all invalid
streams before any aggregate interpretation.

**Step 3: Update milestone evidence**

Record measured results, failures, runtime, cache behavior, information-control results, and exact
limitations. Move M9 to planning only if M8 acceptance passes.

**Step 4: Run final verification**

Run: `cd yf && uv run pytest -q`

Expected: the full suite passes; environment-gated skips remain explicitly reported.

Run: `cd yf && uv run ruff check src tests`

Expected: PASS.

Run: `cd yf && uv build`

Expected: source distribution and wheel build successfully.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/oracle yf/tests/oracle Docs
git commit -m "docs: close M8 and prepare search validation"
```
