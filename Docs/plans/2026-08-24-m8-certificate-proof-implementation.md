# M8 Certificate-Based Exactness Proof Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the infeasible full-prefix brute-force gate with checkable per-action certificates
that prove exact equivalence to frozen M7 continuation semantics, then execute the revised first M8
go/no-go without opening evaluation streams.

**Architecture:** Compute the frozen fallback continuation once, describe each alternative current
action as an exact inventory and ledger delta, and prove future events passive by no-fit or exact
policy-dominance witnesses. Execute the registered M7 transition only where passivity cannot be
proved, validate every emitted proof with a fail-closed checker, and retain slow reference replay for
exhaustive small cases plus a deterministic stratified real audit.

**Tech Stack:** Python 3.12, Pydantic v2, Shapely/GEOS, frozen Jagua helper, pytest, Ruff, existing
M0/M6/M7 content-addressed contracts.

---

## Execution rules

- Work only in `/Users/danielbloom/Desktop/YieldForge/.worktrees/m6-temporal-benchmark` on
  `codex/m8-rollout-preparation`.
- Preserve the complete current action catalog and full visible suffix.
- Treat registered Shapely/Jagua outcomes as authoritative geometry semantics.
- Never open an M8 evaluation stream during this plan.
- Use `@superpowers:test-driven-development` for every behavior change.
- Use `@superpowers:systematic-debugging` before changing code in response to a failure.
- Use `@superpowers:verification-before-completion` before publishing the gate decision.
- Commit only task-owned files at each checkpoint; preserve unrelated dirty-tree changes.

### Task 1: Expose exact policy comparison and selected transition context

**Files:**
- Modify: `yf/src/yieldforge/baseline/policies.py:105-185`
- Modify: `yf/src/yieldforge/baseline/replay.py:529-540`
- Modify: `yf/src/yieldforge/baseline/replay.py:1552-1694`
- Test: `yf/tests/baseline/test_policies.py`
- Test: `yf/tests/baseline/test_replay.py`

**Step 1: Write failing policy-rank tests**

Add tests covering every registered M7 policy. Require a public rank helper to return the same
winner and exact decision evidence as `select_policy_action`, including stable candidate, stock, and
action identity tails.

```python
def test_public_policy_rank_matches_pairwise_selection() -> None:
    left, right = policy_context_pair()
    left_rank = rank_policy_action(M7PolicyName.AGE_REGULARITY, left)
    right_rank = rank_policy_action(M7PolicyName.AGE_REGULARITY, right)
    selected = select_policy_action(M7PolicyName.AGE_REGULARITY, (left, right))
    assert min((left_rank, left), (right_rank, right))[1].action_id == selected.action_id
    assert selected.decision_key == min(left_rank, right_rank).decision_key
```

**Step 2: Write failing continuation-context tests**

Require every `M7StepResult` to expose the selected `ActionPolicyContext`, and every continuation to
retain a context aligned one-to-one with its events.

```python
def test_continuation_retains_exact_selected_contexts() -> None:
    result = run_m7_continuation(runtime, cursor=initial_m7_cursor(runtime.replay_input))
    assert len(result.selected_contexts) == len(result.events)
    assert tuple(item.action_id for item in result.selected_contexts) == tuple(
        item.action.action_id for item in result.events
    )
```

**Step 3: Run the focused tests and confirm failure**

Run:

```bash
cd yf && uv run pytest \
  tests/baseline/test_policies.py \
  tests/baseline/test_replay.py -q
```

Expected: FAIL because `rank_policy_action` and selected continuation contexts do not exist.

**Step 4: Implement the public rank without changing selection semantics**

Introduce an orderable frozen result and make `select_policy_action` delegate to it.

```python
@dataclass(frozen=True, order=True)
class PolicyRank:
    comparison_key: tuple[object, ...]
    decision_key: tuple[str, ...] = field(compare=False)


def rank_policy_action(policy: M7PolicyName, item: ActionPolicyContext) -> PolicyRank:
    # Move the current ranked(...) branches here byte-for-byte.
    return PolicyRank(comparison_key=key, decision_key=evidence)
```

Extend runtime-only results:

```python
@dataclass(frozen=True)
class M7StepResult:
    descriptor: M7ActionDescriptor
    selected_context: ActionPolicyContext
    event: M7ReplayEvent
    cursor: M7ReplayCursor


@dataclass(frozen=True)
class M7ContinuationResult:
    events: tuple[M7ReplayEvent, ...]
    selected_contexts: tuple[ActionPolicyContext, ...]
    terminal: ReplayTerminalRecord
    final_costs: ReplayCostLedger
```

Resolve the context by action ID in `apply_m7_action_descriptor`, fail on missing or duplicate
context, and append `step.selected_context` in `run_m7_continuation`.

**Step 5: Verify exact M7 behavior**

Run:

```bash
cd yf && uv run pytest tests/baseline/test_policies.py tests/baseline/test_replay.py \
  tests/baseline/test_experiment.py -q
cd yf && uv run ruff check src/yieldforge/baseline tests/baseline
```

Expected: PASS. Existing persisted M7 models and identities remain unchanged because the new fields
are runtime-only dataclasses.

**Step 6: Commit**

```bash
git add yf/src/yieldforge/baseline/policies.py yf/src/yieldforge/baseline/replay.py \
  yf/tests/baseline/test_policies.py yf/tests/baseline/test_replay.py
git commit -m "refactor: expose exact M7 policy witnesses"
```

### Task 2: Add strict certificate proof contracts

**Files:**
- Create: `yf/src/yieldforge/oracle/proofs.py`
- Modify: `yf/src/yieldforge/oracle/__init__.py`
- Create: `yf/tests/oracle/test_proofs.py`

**Step 1: Write failing content-address and completeness tests**

Cover all four witness kinds, ordered suffix coverage, exact action identity, final cost, and
tamper rejection.

```python
def test_action_proof_requires_one_ordered_witness_per_future_event() -> None:
    with pytest.raises(ValueError, match="complete ordered suffix"):
        build_action_proof(
            action_id="m7-standard:candidate-a",
            baseline_action_id="m7-standard:candidate-b",
            start_event_position=0,
            stop_event_position=3,
            witnesses=(event_witness(position=1),),
            final_net_cost=12.5,
        )


def test_action_proof_rejects_tampered_hash() -> None:
    proof = valid_action_proof()
    with pytest.raises(ValueError, match="SHA-256"):
        proof.model_copy(update={"content_sha256": "sha256:" + "0" * 64})
```

**Step 2: Run and confirm failure**

Run: `cd yf && uv run pytest tests/oracle/test_proofs.py -q`

Expected: FAIL because proof contracts do not exist.

**Step 3: Implement minimal immutable contracts**

Use strict Pydantic models and semantic hashes:

```python
class M8InfluenceWitness(BaselineContractModel):
    remnant_id: StrictStr
    candidate_id: StrictStr | None = None
    classification: Literal["no_fit", "policy_dominated"]
    evidence_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    common_action_id: StrictStr
    competing_action_id: StrictStr | None = None
    common_decision_key: tuple[StrictStr, ...]
    competing_decision_key: tuple[StrictStr, ...] = ()


class M8EventWitness(BaselineContractModel):
    event_position: StrictInt = Field(ge=0)
    classification: Literal[
        "state_rejoin", "no_fit", "policy_dominated", "exact_transition"
    ]
    common_action_id: StrictStr
    branch_action_id: StrictStr
    state_before_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    state_after_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    influences: tuple[M8InfluenceWitness, ...] = ()


class M8ActionProof(BaselineContractModel):
    schema_version: Literal["yieldforge.m8-action-proof.v1"]
    proof_id: StrictStr = Field(pattern=r"^yfm8ap-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    action_id: StrictStr
    baseline_action_id: StrictStr
    start_event_position: StrictInt = Field(ge=0)
    stop_event_position: StrictInt = Field(ge=0)
    suffix_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    witnesses: tuple[M8EventWitness, ...]
    final_net_cost: StrictFloat
```

The model validator requires event positions to equal
`range(start_event_position + 1, stop_event_position)`, validates classification-specific fields,
and recomputes the content hash and ID. Add builders so callers never hand-assemble identities.

**Step 4: Run tests and lint**

Run:

```bash
cd yf && uv run pytest tests/oracle/test_proofs.py -q
cd yf && uv run ruff check src/yieldforge/oracle/proofs.py tests/oracle/test_proofs.py
```

Expected: PASS.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/oracle/proofs.py yf/src/yieldforge/oracle/__init__.py \
  yf/tests/oracle/test_proofs.py
git commit -m "feat: add M8 certificate proof contracts"
```

### Task 3: Implement no-fit and policy-dominance event certificates

**Files:**
- Create: `yf/src/yieldforge/oracle/certificates.py`
- Modify: `yf/src/yieldforge/oracle/compiled.py`
- Modify: `yf/src/yieldforge/baseline/replay.py`
- Create: `yf/tests/oracle/test_certificates.py`
- Modify: `yf/tests/oracle/fixtures.py`

**Step 1: Write failing safe-certificate tests**

Cover:

- material, area, and bounding-box rejection;
- an authoritative registered-search no-fit after the cheap bounds survive;
- a feasible branch-only remnant whose policy rank loses to the common winner;
- a feasible branch-only remnant that wins and therefore remains unresolved;
- a removed common winner that remains unresolved; and
- exact tie tails.

```python
def test_feasible_branch_remnant_is_passive_when_common_policy_rank_wins() -> None:
    result = certify_event_passivity(policy_dominated_case())
    assert result.passive
    assert result.witness.classification == "policy_dominated"


def test_branch_remnant_that_beats_common_winner_is_not_certified() -> None:
    result = certify_event_passivity(influential_case())
    assert not result.passive
    assert result.witness is None
```

**Step 2: Run and confirm failure**

Run: `cd yf && uv run pytest tests/oracle/test_certificates.py -q`

Expected: FAIL because the event certifier does not exist.

**Step 3: Expose a single-remnant exact catalog helper**

Add a runtime-only helper in `baseline/replay.py` that creates the exact event catalog for one
inventory item while reusing standard profiles, prepared layouts, and fit-search caches. It must call
the existing `_generate_actions` path with `retain_all_remnant_actions=False`; it may not implement a
second geometry or policy engine.

```python
def enumerate_m7_single_remnant_competitor(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
    cursor_template: M7ReplayCursor,
) -> tuple[M7ActionDescriptor | None, ActionPolicyContext | None]:
    ...
```

Return the exact best feasible remnant action and context, or `(None, None)` when the registered
search produces no remnant action.

**Step 4: Implement the event certifier**

`certify_event_passivity` receives the common event/context plus the branch inventory delta.

```python
@dataclass(frozen=True)
class BranchInventoryDelta:
    added: tuple[InventoryItem, ...]
    removed: tuple[InventoryItem, ...]


@dataclass(frozen=True)
class EventPassivityResult:
    passive: bool
    witness: M8EventWitness | None
    exact_search_count: int
```

Algorithm:

1. Fail unresolved if a removed remnant is the common selected stock.
2. For every added remnant and every verified candidate, record the existing safe cheap rejection.
3. If all candidates reject, emit a `no_fit` influence witness.
4. Otherwise invoke the frozen single-remnant catalog helper.
5. If no exact remnant action exists, emit authoritative `no_fit` evidence keyed by the frozen
   geometry/search inputs and result.
6. Compare the exact competitor and common context with `rank_policy_action`.
7. Emit `policy_dominated` only when the common rank is less than or equal to the competitor rank.
8. Return unresolved on any competitor that can win.

The evidence digest must bind geometry, material, candidate, remnant, fit/search configuration,
collision backend, policy, and both exact decision ranks.

**Step 5: Verify**

Run:

```bash
cd yf && uv run pytest tests/oracle/test_certificates.py tests/oracle/test_compiled.py \
  tests/baseline/test_replay.py -q
cd yf && uv run ruff check src/yieldforge/oracle/certificates.py \
  src/yieldforge/oracle/compiled.py src/yieldforge/baseline/replay.py \
  tests/oracle/test_certificates.py
```

Expected: PASS with no false passive certificate.

**Step 6: Commit**

```bash
git add yf/src/yieldforge/oracle/certificates.py yf/src/yieldforge/oracle/compiled.py \
  yf/src/yieldforge/baseline/replay.py yf/tests/oracle/test_certificates.py \
  yf/tests/oracle/fixtures.py
git commit -m "feat: certify M8 branch passivity"
```

### Task 4: Generate and independently check per-action proofs

**Files:**
- Create: `yf/src/yieldforge/oracle/checker.py`
- Modify: `yf/src/yieldforge/oracle/sparse.py`
- Modify: `yf/src/yieldforge/oracle/reference.py`
- Modify: `yf/tests/oracle/test_sparse.py`
- Create: `yf/tests/oracle/test_checker.py`

**Step 1: Write failing proof-generation tests**

Require the passive toy case to produce complete no-fit/policy witnesses and the influential case to
produce an exact transition witness. Require the proof score and selection to match reference replay.

```python
def test_sparse_result_contains_complete_checkable_action_proofs() -> None:
    request = passive_request()
    result = score_sparse_event(request)
    assert len(result.proofs) == result.decision.scored_action_count
    assert all(check_action_proof(request, proof).valid for proof in result.proofs)
    assert result.decision == score_reference_event(request).decision
```

**Step 2: Write failing tamper tests**

Mutate each classification, event position, state hash, decision key, score, suffix hash, and runtime
binding independently. Each mutation must fail the checker.

**Step 3: Run and confirm failure**

Run:

```bash
cd yf && uv run pytest tests/oracle/test_sparse.py tests/oracle/test_checker.py -q
```

Expected: FAIL because sparse proofs and the checker do not exist.

**Step 4: Add single-action reference scoring**

Factor `score_reference_event` through:

```python
def score_reference_action(request: M8OracleRequest, *, action_id: str) -> M8ActionScore:
    catalog = enumerate_m7_action_catalog(request.runtime, cursor=request.cursor)
    descriptor = require_descriptor(catalog, action_id)
    step = apply_m7_action_descriptor(...)
    continuation = run_m7_continuation(...)
    return M8ActionScore(action_id=action_id, final_net_cost=continuation.final_costs.net_cost)
```

The all-action reference remains available for small cases but delegates to this function.

**Step 5: Replace all-or-nothing passive scoring with per-event proof generation**

Add `score_certificate_action(request, action_id, common)` and make `score_sparse_event` aggregate
those exact scores and proofs. For each future position:

1. Rejoin immediately on byte-identical exact cursors.
2. Attempt `certify_event_passivity` against the common event and context.
3. On success, advance the exact branch cursor with the recorded common action using a new
   `apply_m7_frozen_action_evidence` seam that reuses existing `_execute_action`, `_storage_cost`,
   `_ledger`, and rounding functions without enumerating losing actions.
4. On failure, enumerate the exact branch catalog with `complete=False`, select and apply its M7
   winner, and emit `exact_transition`.
5. Continue against the ordered common path; never approximate-merge states.
6. Apply the existing terminal storage and liquidation functions and build the action proof.

The fallback action receives a state-rejoin proof bound to the common continuation rather than
being trusted implicitly.

**Step 6: Implement the checker as a second control flow**

`check_action_proof` must not call `score_certificate_action` or `score_reference_action`. It:

- validates all content identities and suffix coverage;
- reconstructs the initial branch action;
- validates each no-fit or dominance witness with `certify_event_passivity`;
- validates every exact transition by enumerating one exact M7 winner;
- advances the cursor using the recorded action;
- recomputes terminal accounting; and
- requires exact final cost and final state hashes.

Return a strict result containing `valid`, checked event count, certificate count, exact transition
count, and a failure code. Any exception or missing evidence is invalid.

**Step 7: Verify**

Run:

```bash
cd yf && uv run pytest tests/oracle/test_sparse.py tests/oracle/test_checker.py \
  tests/oracle/test_reference.py -q
cd yf && uv run ruff check src/yieldforge/oracle tests/oracle
```

Expected: PASS; all toy action proofs validate and match full reference replay.

**Step 8: Commit**

```bash
git add yf/src/yieldforge/oracle/checker.py yf/src/yieldforge/oracle/sparse.py \
  yf/src/yieldforge/oracle/reference.py yf/src/yieldforge/baseline/replay.py \
  yf/tests/oracle/test_sparse.py yf/tests/oracle/test_checker.py \
  yf/tests/oracle/test_reference.py
git commit -m "feat: generate and check exact M8 action proofs"
```

### Task 5: Exhaustively differential-test the finite semantic kernel

**Files:**
- Create: `yf/tests/oracle/test_exhaustive_certificate_kernel.py`
- Modify: `yf/tests/oracle/fixtures.py`

**Step 1: Build a finite registered case generator**

Generate deterministic two- and three-event cases over:

- all five frozen policy names;
- material match and mismatch;
- fit and no-fit widths;
- zero, one, and two inventory remnants;
- added and removed current-action remnants;
- equal and unequal costs;
- equal policy prefixes resolved by identity tails;
- same-time and separated events; and
- positive and zero terminal inventory.

Keep the matrix finite and print its semantic case count.

**Step 2: Write the exhaustive differential**

```python
@pytest.mark.parametrize("case", exhaustive_certificate_cases(), ids=lambda case: case.case_id)
def test_certificate_kernel_matches_full_reference(case) -> None:
    sparse = score_sparse_event(case.request)
    reference = score_reference_event(case.request)
    assert sparse.decision == reference.decision
    assert all(check_action_proof(case.request, proof).valid for proof in sparse.proofs)
```

Also assert that the matrix exercises every witness kind and at least one exact escape followed by a
state rejoin.

**Step 3: Run and debug only from minimized failing cases**

Run:

```bash
cd yf && uv run pytest tests/oracle/test_exhaustive_certificate_kernel.py -q
```

Expected: PASS with zero score, selected-action, state, ledger, or proof-check mismatches.

**Step 4: Run the complete oracle and M7 regression set**

Run:

```bash
cd yf && uv run pytest tests/oracle tests/baseline/test_policies.py \
  tests/baseline/test_replay.py tests/baseline/test_experiment.py -q
cd yf && uv run ruff check src/yieldforge/oracle src/yieldforge/baseline \
  tests/oracle tests/baseline
```

Expected: PASS.

**Step 5: Commit**

```bash
git add yf/tests/oracle/test_exhaustive_certificate_kernel.py yf/tests/oracle/fixtures.py
git commit -m "test: exhaust M8 certificate semantics"
```

### Task 6: Replace the brute prefix runner with the revised gate

**Files:**
- Modify: `yf/src/yieldforge/oracle/experiment.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/oracle/test_experiment.py`
- Modify: `yf/tests/test_cli.py`

**Step 1: Write failing v2 gate-contract tests**

Replace the unpublished v1 proof model with `yieldforge.m8-certificate-proof.v2`. Require:

- all six registered regimes;
- deterministic audit action IDs and sample hash;
- full action count and valid proof count equality;
- zero checker failures and audit mismatches;
- witness-class coverage;
- sampled reference and certificate elapsed time;
- full certificate throughput;
- at least 20x matched-sample speedup;
- held-out projection at most seven days; and
- `evaluation_partition_opened=False`.

```python
def test_certificate_gate_passes_only_complete_exact_fast_result() -> None:
    result = finalize_certificate_proof(cells=passing_cells(), bindings=frozen_bindings())
    assert result.checked_action_count == result.current_action_count
    assert result.audit_mismatch_count == 0
    assert result.checker_failure_count == 0
    assert result.sampled_speedup >= 20.0
    assert result.projected_held_out_calendar_days <= 7.0
    assert result.technical_decision == "pass_certificate_exact"
```

Test each failure independently. Use `redesign_certificate_proof` for semantic, coverage, checker,
or speed failures and `require_distributed_exact` only for an otherwise exact projection over seven
days.

**Step 2: Run and confirm failure**

Run:

```bash
cd yf && uv run pytest tests/oracle/test_experiment.py tests/test_cli.py -q
```

Expected: FAIL because the runner still requires full reference scoring for every current action.

**Step 3: Freeze deterministic stratified audit selection**

Select action IDs before timing, using only calibration data. Bind the sample to a semantic hash and
cover, where present:

- standard and remnant current actions;
- each certificate classification;
- at least one exact escape and one state rejoin;
- minimum, median, and maximum future horizon among proof cells; and
- every temporal regime.

Fail closed when a required present stratum is omitted. Do not substitute a different stream, seed,
or action after seeing runtime or mismatch outcomes.

**Step 4: Implement two-phase execution**

For each of six calibration prefixes:

1. Build and verify frozen candidate/runtime bindings.
2. Compute the common continuation once.
3. Score every current action with `score_certificate_action` and immediately check its proof.
4. Record full certificate throughput and coverage.
5. Run `score_reference_action` only for the frozen stratified audit IDs.
6. Compare audited scores exactly and calculate matched-sample speedup.
7. Publish no cell until its proofs, checker results, and audit reconcile.

Retain eight-worker identity in the artifact but allow a test-only worker override that cannot be
used by the canonical CLI.

**Step 5: Revise the CLI without changing its external command name**

Keep `benchmark m8-sparse-proof` for continuity, but make its output schema and progress text
explicitly certificate-based. It must refuse any existing output path with different content and
must never load evaluation partitions.

**Step 6: Verify**

Run:

```bash
cd yf && uv run pytest tests/oracle/test_experiment.py tests/test_cli.py -q
cd yf && uv run ruff check src/yieldforge/oracle/experiment.py \
  src/yieldforge/cli.py tests/oracle/test_experiment.py tests/test_cli.py
```

Expected: PASS.

**Step 7: Commit**

```bash
git add yf/src/yieldforge/oracle/experiment.py yf/src/yieldforge/cli.py \
  yf/tests/oracle/test_experiment.py yf/tests/test_cli.py
git commit -m "feat: gate M8 with certificate proof"
```

### Task 7: Execute the revised first M8 go/no-go

**Files:**
- Create: `yf/experiments/results/m8-certificate-proof-<id>.json`
- Modify: `Docs/Milestones/M8 - Rollout oracle.md`

**Step 1: Run all pre-execution verification**

Run:

```bash
cd yf && uv run pytest tests/oracle tests/baseline/test_policies.py \
  tests/baseline/test_replay.py tests/baseline/test_experiment.py tests/test_cli.py -q
cd yf && uv run ruff check src/yieldforge tests
git diff --check
```

Expected: all tests and lint pass; no whitespace errors.

**Step 2: Execute the calibration-only certificate gate**

Run:

```bash
cd yf && uv run yieldforge benchmark m8-sparse-proof \
  --m0 experiments/m0-contract-v1.json \
  --frozen-baseline experiments/results/m7-frozen-baseline-v1.0.1.json \
  --archive-root /Users/danielbloom/Desktop/YieldForge/yf/var/experiments/yfgp-49906e93ed9ff0446705247b/workbench/candidate-archives \
  --archive-root /Users/danielbloom/Desktop/YieldForge/yf/var/experiments/yfgp-49906e93ed9ff0446705247b-run-02/workbench/candidate-archives \
  --archive-root /Users/danielbloom/Desktop/YieldForge/yf/var/experiments/yfgp-392644d98bb7035fdc218512-confirmation-run-01/workbench/candidate-archives \
  --jagua-binary native/m7-jagua-spike/target/release/yieldforge-m7-jagua-spike \
  --output experiments/results
```

Expected: one immutable v2 proof artifact, all six calibration cells complete, evaluation unopened.

**Step 3: Independently validate the artifact**

Reload the JSON through its strict Pydantic model, recompute its semantic hash, reconcile every cell
and aggregate, verify the audit sample hash, and confirm the result path equals its proof ID.

Run the command a second time with identical inputs. Expected: identical output bytes or a safe
content-addressed no-op.

**Step 4: Apply the hard decision**

- `pass_certificate_exact`: first M8 go/no-go passes; proceed to persistent caches and the six-stream
  calibration pilot.
- `redesign_certificate_proof`: stop and report the exact failed semantic, coverage, audit, checker,
  or speed condition.
- `require_distributed_exact`: exactness passes, but stop local execution and design a separately
  frozen distributed plan.

Do not reinterpret a failed condition or open evaluation.

**Step 5: Record the milestone status**

Update `Docs/Milestones/M8 - Rollout oracle.md` with observed counts, timings, proof identity,
decision, limitations, and the fact that geometry engines are trusted rather than formally proven.

**Step 6: Final verification and commit**

Invoke `@superpowers:verification-before-completion`, then run:

```bash
cd yf && uv run pytest tests/oracle tests/baseline/test_policies.py \
  tests/baseline/test_replay.py tests/baseline/test_experiment.py tests/test_cli.py -q
cd yf && uv run ruff check src/yieldforge tests
git diff --check
git status --short
```

Commit only after inspecting the real result:

```bash
git add yf/experiments/results/m8-certificate-proof-*.json \
  "Docs/Milestones/M8 - Rollout oracle.md"
git commit -m "experiment: execute M8 certificate gate"
```

## Completion boundary

This plan completes only when the revised artifact exists and has been independently reloaded and
verified. A passing code suite without the six-cell artifact is implementation progress, not a
completed first go/no-go. A passing certificate gate remains software evidence only and does not
authorize claims of physical material savings, global optimality, or commercial demand.
