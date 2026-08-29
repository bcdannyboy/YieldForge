# M9 Minimal Search Validation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Determine whether M8 one-step rollout selects a globally optimal first action on all 45
registered finite cases and publish the bounded decision-level M9 result.

**Architecture:** Add one baseline-only recursive exact-search module that enumerates complete M7
catalogs and reoptimizes at every event. A small checkout-local runner compares that independent
optimum with one-step rollout under scrap-only and zero-total-terminal-credit objectives, repeats the
matrix for determinism, and emits one canonical result. Existing dirty G0.5 files remain untouched.

**Tech Stack:** Python 3.12, frozen M7 replay APIs, dataclasses, canonical JSON/SHA-256, pytest, Ruff.

---

### Task 1: Add the exact multi-step search kernel

**Files:**
- Create: `yf/src/yieldforge/oracle/search_validation.py`
- Create: `yf/tests/oracle/test_search_validation.py`

**Step 1: Write the failing hand-computed test**

Use the existing case
`remnant_first-one-match-fit-unequal-high-retrieval-three`. Assert that the missing public function
`solve_exact_search` will return:

- optimal terminal cost `300.0`;
- both standard-sheet catalog actions as optimal first actions;
- each remnant first action at `400.0`;
- no truncated action catalog; and
- positive transition/leaf telemetry.

Also assert the fixture's terminal credit is zero so this hand calculation is independent of the
terminal sensitivity arm.

**Step 2: Run the test to verify RED**

Run:

```bash
cd yf
.venv/bin/pytest -q tests/oracle/test_search_validation.py::test_exact_search_matches_hand_computed_high_retrieval_case
```

Expected: collection/import failure because `yieldforge.oracle.search_validation` does not exist.

**Step 3: Implement the minimum recursive solver**

Create exact frozen result records for root scores and telemetry. Implement this semantic core:

```python
def _terminal_cost(runtime, cursor, stop, *, include_terminal_credit):
    terminal = run_m7_continuation(
        runtime,
        cursor=cursor,
        stop_event_position=stop,
    )
    if terminal.events:
        raise RuntimeError("M9 exact terminalization replayed an event")
    cost = terminal.final_costs.net_cost
    if not include_terminal_credit:
        cost = rounded_cost(cost + terminal.final_costs.terminal_scrap_credit)
    return cost

def recurse(runtime, cursor, stop):
    if cursor.next_event_position == stop:
        return _terminal_cost(...)
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=True)
    require_no_truncation(catalog)
    return min(
        recurse(
            runtime,
            apply_m7_action_descriptor(
                runtime,
                cursor=cursor,
                catalog=catalog,
                descriptor=descriptor,
                decision_key=(f"m9_exact_action_id={descriptor.action_id}",),
            ).cursor,
            stop,
        )
        for descriptor in catalog.actions
    )
```

At the root, retain the complete action-to-cost vector and all action IDs tied at the exact minimum.
Derive `stop` from the request's visible suffix. Do not import or call M8 sparse, factored, reference,
checker, or proof code from the exact kernel. Do not memoize or parallelize this finite search.

**Step 4: Run the focused test to verify GREEN**

Run the Step 2 command.

Expected: one pass.

**Step 5: Commit the kernel and hand check**

```bash
git add yf/src/yieldforge/oracle/search_validation.py \
  yf/tests/oracle/test_search_validation.py
git commit -m "feat: add exact M9 finite search"
```

### Task 2: Compare all registered cases and both terminal objectives

**Files:**
- Modify: `yf/src/yieldforge/oracle/search_validation.py`
- Modify: `yf/tests/oracle/test_search_validation.py`

**Step 1: Write failing matrix and control tests**

Add tests that build all 45 cases and require the comparison API to report:

- exactly 45 unique ordered case IDs;
- complete, untruncated catalogs;
- rollout-selected action, exact optimal action set, exact optimal cost, reoptimized cost reachable
  from the selected first action, absolute/relative regret, and search telemetry per case;
- both `scrap_only` and `zero_total_terminal_credit` objectives;
- the five `zero-no-fit-equal-separated-two` cases labeled only as tiny information-null controls;
- zero gap, tied action values, and baseline selection for those five controls under both objectives;
  and
- no imports from the M8 rollout implementation inside the recursive exact-search function.

The experiment classification must be data-derived. Based on the independently reproduced dry run,
the expected primary result is `fail_search_gap` with exactly one counterexample, not a forced pass.

**Step 2: Run the matrix tests to verify RED**

Run:

```bash
cd yf
.venv/bin/pytest -q tests/oracle/test_search_validation.py -k 'matrix or information_null or terminal'
```

Expected: failure because the comparison/result APIs do not exist.

**Step 3: Implement one-step and exact comparison**

Implement a separate one-step scorer that applies each current action, follows frozen M7 for future
events, and applies the same terminal objective transform. Preserve M8's baseline-preferred exact
tie rule. Implement the case comparison and aggregate decision:

```python
decision = (
    "pass_decision_feasibility"
    if every_selected_action_is_globally_optimal
    and max_absolute_regret == 0.0
    and information_null_controls_pass
    and terminal_conclusion_does_not_reverse
    else "fail_search_gap"
)
```

Do not add an approximation tolerance. Preserve every counterexample in deterministic case order.

**Step 4: Run the complete focused suite**

Run:

```bash
cd yf
.venv/bin/pytest -q tests/oracle/test_search_validation.py
```

Expected: all tests pass while the experiment result itself is classified `fail_search_gap` if the
preliminary counterexample reproduces.

**Step 5: Commit the matrix evaluator**

```bash
git add yf/src/yieldforge/oracle/search_validation.py \
  yf/tests/oracle/test_search_validation.py
git commit -m "feat: evaluate minimal M9 search gap"
```

### Task 3: Execute twice and publish the deterministic result

**Files:**
- Create: `yf/tools/run_m9_minimal_search_validation.py`
- Create: `yf/experiments/results/m9-minimal-search-validation-<result-id>.json`
- Modify: `yf/tests/oracle/test_search_validation.py`

**Step 1: Write the failing runner test**

Test that the runner:

- executes the 45 cases twice from freshly rebuilt fixtures;
- compares the deterministic semantic payloads byte-for-byte;
- records measured wall time outside the semantic identity;
- records `evaluation_partition_opened: false`;
- derives the result ID from canonical semantic JSON; and
- refuses to replace an existing different artifact.

**Step 2: Run the runner test to verify RED**

Run:

```bash
cd yf
.venv/bin/pytest -q tests/oracle/test_search_validation.py -k runner
```

Expected: failure because the runner does not exist.

**Step 3: Implement and execute the runner**

The runner may import `tests.oracle.fixtures` because this is a checkout-local calibration tool, not
a product API. It must call the source evaluator rather than reimplement search. Execute:

```bash
cd yf
PYTHONPATH=src:. .venv/bin/python tools/run_m9_minimal_search_validation.py \
  --output-directory experiments/results
```

Expected: one content-addressed JSON path and the explicit decision. A search-gap decision is a
valid completed experiment, not a process failure.

**Step 4: Strict-load and independently summarize the artifact**

Run a read-only loader that recomputes the semantic hash, case census, objective census, maximum
regret, information-null controls, and counterexample list directly from the saved JSON.

Expected: all fields reconcile with the live run.

**Step 5: Commit runner, tests, and artifact**

```bash
git add yf/tools/run_m9_minimal_search_validation.py \
  yf/tests/oracle/test_search_validation.py \
  yf/experiments/results/m9-minimal-search-validation-*.json
git commit -m "data: publish minimal M9 search validation"
```

### Task 4: Verify and record the M9 decision

**Files:**
- Modify: `Docs/Milestones/M9 - Search validation.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`

**Step 1: Run final focused and regression verification**

Run:

```bash
cd yf
.venv/bin/pytest -q tests/oracle/test_search_validation.py \
  tests/oracle/test_exhaustive_certificate_kernel.py
.venv/bin/ruff check src/yieldforge/oracle/search_validation.py \
  tests/oracle/test_search_validation.py tools/run_m9_minimal_search_validation.py
git diff --check
```

Expected: tests and Ruff pass. Existing unrelated G0.5 modifications remain unstaged.

**Step 2: Obtain one independent semantic review**

The reviewer must recompute the exact counterexample from baseline APIs, confirm the recursive
solver never invokes M8 scoring, and confirm that the written claim matches the result.

**Step 3: Record the bounded outcome**

Document observed counts, exact counterexamples, terminal/control results, runtime, artifact
identity, and claim ceiling. If any positive gap exists, record M9 as a bounded fail and identify
beam search as optional follow-on—not as work already authorized.

**Step 4: Commit documentation only**

```bash
git add 'Docs/Milestones/M9 - Search validation.md' \
  'Docs/Milestones/Milestone Roadmap.md'
git commit -m "docs: record minimal M9 search result"
```

**Step 5: Report the semantic result**

Explain whether M9 passed or failed, what the smallest counterexample means, and the minimum next
choice: accept one-step search risk, add a bounded beam search, or stop the search architecture.
