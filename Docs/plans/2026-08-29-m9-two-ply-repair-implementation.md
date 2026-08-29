# M9 Two-Ply Search Repair Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the minimum future-search extension that passes the existing bounded M9 action-selection
gate on all 45 registered cases under both terminal objectives.

**Architecture:** Extend the calibration-only M9 module with a fixed two-ply scorer that enumerates
the current and next decisions, then returns to frozen M7 continuation. Compare that scorer with the
independent exact solver, enforce the frozen structural compute boundary, run the matrix twice, and
publish a new immutable repair artifact while preserving the original one-step failure artifact.

**Tech Stack:** Python 3.12, frozen M7 replay APIs, dataclasses, canonical JSON/SHA-256, pytest, Ruff.

---

### Task 1: Add the fixed two-ply scorer

**Files:**
- Modify: `yf/src/yieldforge/oracle/search_validation.py`
- Modify: `yf/tests/oracle/test_search_validation.py`

**Step 1: Write the failing counterexample test**

Add a focused test for
`remnant_first-one-match-fit-unequal-high-retrieval-three` that imports the missing
`score_two_ply_reoptimization` and requires:

- repair depth exactly `2`;
- both standard root actions scored at `400.0`;
- both remnant root actions scored at `500.0`;
- a standard root action selected through a strict score advantage;
- the existing M7 fallback retained as the exact-tie fallback only;
- complete catalogs and zero truncation;
- positive explicit-transition and continuation telemetry; and
- no call or import from `solve_exact_search` inside the scorer.

Also add a two-event control assertion proving that when the second explicit decision reaches the
visible stop, the scorer terminalizes directly rather than replaying an event.

**Step 2: Run the focused test to verify RED**

Run:

```bash
cd yf
.venv/bin/pytest -q tests/oracle/test_search_validation.py -k two_ply_counterexample
```

Expected: collection failure because `score_two_ply_reoptimization` does not exist.

**Step 3: Implement the minimum fixed-depth scorer**

Add frozen records for:

- one bounded root score: action ID, action kind, bounded objective cost;
- two-ply work telemetry: catalog count, explicit transition count, continuation event count,
  continuation call count, direct terminalization count, peak branching factor, truncation count,
  and total event-transition count; and
- one two-ply result: objective identity, depth, baseline action, selected action, root vector,
  completeness, and telemetry.

Implement only depth 2:

```python
for root_descriptor in complete_root_catalog.actions:
    root_step = apply_m7_action_descriptor(...)
    if root_step.cursor.next_event_position == stop:
        root_cost = terminalize(root_step.cursor)
    else:
        second_catalog = enumerate_m7_action_catalog(
            runtime,
            cursor=root_step.cursor,
            complete=True,
        )
        root_cost = min(
            continue_frozen_m7(apply(second_descriptor).cursor)
            for second_descriptor in second_catalog.actions
        )
```

Every catalog must be complete and fail on truncation or emptiness. Count every explicitly applied
root/second transition and every event executed by the frozen continuation. Apply the named terminal
objective exactly as the current one-step and exact scorers do. Select with:

```python
(bounded_objective_cost, action_id != baseline_action_id, action_id)
```

Do not make the depth configurable and do not call the exact solver.

**Step 4: Run the focused tests to verify GREEN**

Run:

```bash
cd yf
.venv/bin/pytest -q tests/oracle/test_search_validation.py -k 'two_ply_counterexample or two_ply_terminal'
.venv/bin/ruff check src/yieldforge/oracle/search_validation.py tests/oracle/test_search_validation.py
```

Expected: focused tests and Ruff pass.

**Step 5: Commit the scorer**

Stage only the two owned files and commit:

```bash
git add yf/src/yieldforge/oracle/search_validation.py \
  yf/tests/oracle/test_search_validation.py
git commit -m "feat: add M9 two-ply search repair"
```

### Task 2: Evaluate the repaired policy against the exact gate

**Files:**
- Modify: `yf/src/yieldforge/oracle/search_validation.py`
- Modify: `yf/tests/oracle/test_search_validation.py`

**Step 1: Write failing repair-matrix tests**

Add tests for a missing `evaluate_two_ply_repair_validation` API. Require deterministic ordered
records for all 45 cases under `scrap_only` and `zero_total_terminal_credit`, including:

- baseline and repaired selected action;
- complete two-ply root scores and exact root scores;
- exact-optimal first-action set;
- exact cost after the repaired selected action;
- exact absolute/relative first-action regret;
- bounded selected-action score and its signed/absolute error versus exact reachable cost;
- two-ply telemetry and compute-budget reconciliation;
- honest `tiny_information_null` control labels; and
- every counterexample preserved in fixture order.

The aggregate test must require:

- `45/45` repaired selections globally optimal under both objectives;
- maximum first-action regret exactly `0.0`;
- zero counterexamples;
- all five controls passing;
- 185 catalogs, 650 explicit transitions, 500 frozen continuation-event transitions, and 1,150
  total event transitions per objective;
- at most 200 catalogs, 700 explicit transitions, and 1,200 total transitions;
- zero truncations;
- `pass_decision_feasibility`; and
- the original `evaluate_search_validation` result still equal to `fail_search_gap` with the
  preserved counterexample.

Keep value calibration separate from action regret. The repaired bounded values are known to remain
nonexact in some cases, so a pass must not require or imply exact value equality.

**Step 2: Run the matrix tests to verify RED**

Run:

```bash
cd yf
.venv/bin/pytest -q tests/oracle/test_search_validation.py -k 'repair_matrix or repair_compute or original_failure'
```

Expected: failure because the repair evaluator and result records do not exist.

**Step 3: Implement the repair comparison and aggregate gate**

Add separate repair result records rather than changing the original M9 failure result. Reuse the
existing exact solver only in the comparison layer. For each repaired selection, obtain exact
selected-first reachable cost from the exact root vector and calculate regret with exact six-decimal
arithmetic.

Derive the aggregate decision from data:

```python
pass_decision_feasibility = (
    primary_complete
    and sensitivity_complete
    and all_selected_actions_exact_optimal
    and max_absolute_regret == 0.0
    and information_null_controls_pass
    and compute_budget_pass
    and terminal_conclusion_does_not_reverse
)
```

Do not add tolerances, special-case the high-retrieval fixture, or require a particular member of an
exact-optimal tie.

**Step 4: Run the complete focused suite**

Run:

```bash
cd yf
.venv/bin/pytest -q tests/oracle/test_search_validation.py
.venv/bin/ruff check src/yieldforge/oracle/search_validation.py tests/oracle/test_search_validation.py
```

Expected: the suite passes; the original result remains a bounded failure and the repaired result is
a bounded pass.

**Step 5: Commit the repair evaluator**

Stage only the two owned files and commit:

```bash
git add yf/src/yieldforge/oracle/search_validation.py \
  yf/tests/oracle/test_search_validation.py
git commit -m "feat: validate M9 two-ply repair"
```

### Task 3: Execute twice and publish the passing repair artifact

**Files:**
- Modify: `yf/tools/run_m9_minimal_search_validation.py`
- Modify: `yf/tests/oracle/test_search_validation.py`
- Create: `yf/experiments/results/m9-two-ply-repair-validation-<result-id>.json`

**Step 1: Write failing runner tests**

Extend the checkout-local runner tests for a missing two-ply repair entry point. Require:

- two calls to freshly rebuild all 45 fixtures;
- byte-identical repaired semantic payloads before publication;
- operational wall times outside artifact bytes and identity;
- `evaluation_partition_opened: false`;
- a schema and result-ID namespace distinct from the original failure artifact;
- an explicit binding to original failure result `yfm9-97e032de7a09247cc83e6c5a`;
- the complete compute boundary and value-error summary;
- content-derived result ID and semantic SHA-256;
- immutable identical-file reuse and refusal to replace different bytes; and
- process success for `pass_decision_feasibility` only when the aggregate gate reconciles.

Add a test that a semantic mismatch between pass one and pass two writes no artifact.

**Step 2: Run runner tests to verify RED**

Run:

```bash
cd yf
.venv/bin/pytest -q tests/oracle/test_search_validation.py -k two_ply_runner
```

Expected: failure because the repair runner entry point does not exist.

**Step 3: Implement and execute the runner**

Reuse the existing runner's canonical serialization and immutable publication boundary. Keep the
original one-step entry point unchanged. Add a separate repair semantic core and identity prefix,
then execute:

```bash
cd yf
PYTHONPATH=src:. .venv/bin/python tools/run_m9_minimal_search_validation.py \
  --two-ply-repair \
  --output-directory experiments/results
```

Expected: one new content-addressed repair JSON path and decision
`pass_decision_feasibility`. The original failure artifact remains byte-identical.

**Step 4: Strict-load the artifact independently**

Without using runner validation helpers, recompute:

- canonical JSON bytes and semantic identity;
- evaluator repeat digest;
- both 45-case censuses and ordered IDs;
- exact regrets and counterexamples;
- control census;
- all work totals and compute-budget predicates;
- bounded-value errors versus exact reachable costs;
- terminal-sensitivity conclusion; and
- final decision.

Expected: every field reconciles and the original failure artifact hash is unchanged.

**Step 5: Commit runner, tests, and artifact**

Stage exact paths only and commit:

```bash
git add yf/tools/run_m9_minimal_search_validation.py \
  yf/tests/oracle/test_search_validation.py \
  yf/experiments/results/m9-two-ply-repair-validation-*.json
git commit -m "data: publish passing M9 two-ply validation"
```

### Task 4: Record the bounded M9 pass and verify regressions

**Files:**
- Modify: `Docs/Milestones/M9 - Search validation.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`

**Step 1: Run final technical verification**

Run:

```bash
cd yf
.venv/bin/pytest -q tests/oracle/test_search_validation.py \
  tests/oracle/test_exhaustive_certificate_kernel.py
.venv/bin/ruff check src/yieldforge/oracle/search_validation.py \
  tests/oracle/test_search_validation.py tools/run_m9_minimal_search_validation.py
git diff --check
```

Expected: all tests and Ruff pass. Only the four unrelated G0.5 files remain uncommitted.

**Step 2: Obtain independent semantic review**

Require a reviewer to reconcile the committed repair artifact directly and confirm:

- the original one-step failure is preserved;
- the repair scorer is independent of exact search;
- 45/45 exact-optimal membership and zero regret under both objectives;
- structural compute budget pass;
- nonexact value estimates are disclosed; and
- the claim ceiling and sealed evaluation boundary remain intact.

**Step 3: Update the milestone notes**

Append the repair after the original failure rather than rewriting history. Record the repair ID and
hash, deterministic repeat, work totals, observed operational timings, value-calibration limits,
and the exact bounded claim. Mark M9 passed only at the registered finite decision level. State that
M8's original performance gate remains unresolved and M10 is a separate decision.

**Step 4: Verify and commit documentation**

Run:

```bash
git diff --check -- 'Docs/Milestones/M9 - Search validation.md' \
  'Docs/Milestones/Milestone Roadmap.md'
```

Stage only the two milestone files and commit:

```bash
git add 'Docs/Milestones/M9 - Search validation.md' \
  'Docs/Milestones/Milestone Roadmap.md'
git commit -m "docs: record bounded M9 two-ply pass"
```

**Step 5: Final worktree audit**

Confirm the intended commit sequence, no staged files, the original and repair artifacts both load,
and the four unrelated G0.5 modifications remain untouched.
