# M5 Deterministic Replay Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build, execute, and publish a fail-closed deterministic chronological replay that carries
exact stock and recursive remnants through a hand-computable two-order fixture, then prepare the
bounded M6 temporal-benchmark boundary.

**Architecture:** Add a pure `yieldforge.replay` state machine that consumes strict content-addressed
input, calls the existing M4 exact fit/consumption boundary, and emits immutable event and cost
evidence. Keep canonical fixture preparation and independent artifact revalidation in
`experiments.deterministic_replay`; keep benchmark population construction deferred to M6.

**Tech Stack:** Python 3.12, Pydantic 2, Shapely 2.1.2, existing YieldForge residual/reuse contracts,
pytest, Ruff, `uv`, canonical JSON and SHA-256.

---

### Task 1: Define strict replay contracts and identities

**Files:**
- Create: `yf/src/yieldforge/replay/__init__.py`
- Create: `yf/src/yieldforge/replay/contracts.py`
- Create: `yf/tests/replay/__init__.py`
- Create: `yf/tests/replay/test_contracts.py`

**Step 1: Write the failing contract tests**

Add tests that construct the wished-for API:

```python
def test_replay_input_is_strict_content_addressed_and_chronological() -> None:
    replay_input = build_test_input()
    assert replay_input.input_id == f"yfrpi-{replay_input.content_sha256[7:31]}"
    assert tuple(order.sequence for order in replay_input.orders) == (0, 1)
    assert replay_input.orders[0].released_at < replay_input.orders[1].released_at
    assert replay_input.orders[-1].released_at < replay_input.horizon_end


def test_rate_manifest_and_cost_ledger_reject_nonfinite_or_unreconciled_values() -> None:
    with pytest.raises(ValidationError):
        ReplayRateManifest(purchase_cost_per_area=float("inf"), ...)
    with pytest.raises(ValidationError, match="net cost"):
        ReplayCostLedger(..., net_cost=999.0)
```

Cover engine/policy version identity, policy seed `0`, exact M0/M4 bindings, aware UTC timestamps,
strictly increasing single-order releases, standard-sheet/material consistency, six-decimal rate
rounding, sorted unique inventory IDs, event-stage order, action-kind invariants, cumulative cost
reconciliation, terminal evidence, summary counts, and result identity.

**Step 2: Run the contract tests to verify RED**

Run:

```bash
cd yf
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups \
  pytest tests/replay/test_contracts.py -q
```

Expected: collection fails because `yieldforge.replay.contracts` does not exist.

**Step 3: Implement the minimal strict contracts**

Create frozen, strict, finite models for:

- `ReplayEngineIdentity(name="yieldforge.deterministic-replay", version="1.0.0",
  shapely_version=...)`;
- `ReplayPolicyIdentity(name="first_fit_remnant_then_standard_sheet", version="1.0.0",
  seed=0, information_set="released_order_and_current_inventory_only")`;
- `ReplayRateManifest` with generated cost/area/hour units and the five M0 rate fields;
- `StandardSheetSpec`, `ReplayOrder`, and `ReplayInput` (`yfrpi-*`);
- `InventoryItem`, `ReplayActionKind`, `ReplayActionEvidence`, and `ReplayEventRecord`;
- `ReplayCostLedger`, `ReplayTerminalRecord`, `ReplaySummary`, and `ReplayResult` (`yfrpr-*`).

Use `semantic_sha256` for input/result identity. Canonicalize timestamps to UTC. Store the exact M0
ordered stages as a literal tuple. Make `ReplayCostLedger` recompute
`purchase + storage + return + retrieval - scrap - terminal_credit` in its validator.

**Step 4: Run RED-to-GREEN verification**

Run the Task 1 test command again. Expected: all contract tests pass.

**Step 5: Refactor and lint**

Run:

```bash
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups \
  ruff check src/yieldforge/replay tests/replay
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups \
  ruff format --check src/yieldforge/replay tests/replay
```

**Step 6: Commit**

```bash
git add yf/src/yieldforge/replay yf/tests/replay
git commit -m "feat: define deterministic replay contracts"
```

### Task 2: Implement exact full-sheet execution and as-of-time action selection

**Files:**
- Create: `yf/src/yieldforge/replay/engine.py`
- Create: `yf/tests/replay/test_engine.py`

**Step 1: Write failing geometry and policy tests**

Add tests for:

```python
def test_policy_opens_sheet_then_reuses_first_sorted_compatible_remnant() -> None:
    first = select_action(current_order=order_one, inventory=(), ...)
    assert first.kind is ReplayActionKind.OPEN_STANDARD_SHEET

    second = select_action(current_order=order_two, inventory=(returned,), ...)
    assert second.kind is ReplayActionKind.CONSUME_REMNANT
    assert second.selected_remnant_id == returned.remnant.remnant_id


def test_policy_signature_has_no_manifest_or_future_orders() -> None:
    assert tuple(inspect.signature(select_action).parameters) == (
        "current_order", "inventory", "sheet", "fit_config", "search_config"
    )
```

Also cover sorted-ID tie breaking, material-incompatible inventory, bounded no-witness fallback,
full-sheet infeasibility, exact 10x10 minus 4x10 accounting, one generation-1 6x10 returned remnant,
remnant consumption producing one generation-2 3x10 child, and duplicate/missing inventory failure.

**Step 2: Run the engine tests to verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups \
  pytest tests/replay/test_engine.py -q
```

Expected: import or attribute failure for the missing engine functions.

**Step 3: Implement minimal action selection**

Implement `select_action` with only current-order inputs. Iterate material-compatible inventory by
remnant ID and call `search_fit_witness`. Select the first exact fit. If none is found, build a
transient exact sheet polygon and require a bounded exact sheet witness. Preserve the bounded-miss
label; never convert it into a no-fit claim.

**Step 4: Implement exact full-sheet execution**

Implement a private full-sheet execution helper that:

1. builds a transient exact stock polygon;
2. calls `consume_remnant` under the M0 primary rules;
3. retains its exact accounting and placed-polygon identity; and
4. re-roots every retained residual as a generation-1 remnant bound to the sheet-opening action.

Remnant execution must remove one selected inventory record and use `consume_remnant` without
rewriting its generation or ancestry.

**Step 5: Run the engine tests to verify GREEN**

Run the Task 2 test command. Expected: all tests pass.

**Step 6: Run the reuse regression suite**

```bash
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups \
  pytest tests/reuse tests/replay -q
```

**Step 7: Commit**

```bash
git add yf/src/yieldforge/replay/engine.py yf/tests/replay/test_engine.py
git commit -m "feat: select and execute replay stock actions"
```

### Task 3: Implement chronological state transitions and cost accounting

**Files:**
- Modify: `yf/src/yieldforge/replay/engine.py`
- Modify: `yf/tests/replay/test_engine.py`

**Step 1: Write failing replay tests**

Add one assertion group at a time and run it RED before implementation:

- event stages exactly match M0 order;
- event 0 opens one sheet, returns one remnant, and reaches cost `102.0`;
- event 1 accrues storage `0.6`, retrieval `3.0`, return `2.0`, and reaches `107.6`;
- horizon close accrues storage `0.3`, applies terminal credit `3.0`, and ends at `104.9`;
- final inventory contains one generation-2 3x10 child;
- half-open storage has no same-timestamp charge;
- replaying the same input twice yields equal models and canonical JSON;
- changed rates or release time change input/result identity;
- nonmonotone time, duplicate inventory use, geometry error, and partial execution fail closed.

**Step 2: Run each new test and verify RED**

Use focused node IDs from `tests/replay/test_engine.py`. Confirm each fails because the replay state
transition is missing, not because the fixture is malformed.

**Step 3: Implement `run_replay`**

Use an immutable runtime state containing current timestamp, sorted inventory, event records, and
cumulative ledger. For each order:

1. accrue storage from the previous state timestamp to the release;
2. reveal the single released order and form the bounded one-part batch;
3. call `select_action` with no future data;
4. execute geometry atomically into local values;
5. accrue purchase/retrieval/return/scrap terms;
6. append a complete event record; and
7. replace inventory only after every validator succeeds.

After the final event, accrue storage to `horizon_end`, liquidate remaining inventory at the primary
scrap-only rate, and build the content-addressed result and summary. Round each monetary accrual
half-up to six decimal places.

**Step 4: Verify GREEN and regressions**

```bash
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups \
  pytest tests/replay tests/reuse tests/residuals -q
```

**Step 5: Commit**

```bash
git add yf/src/yieldforge/replay/engine.py yf/tests/replay/test_engine.py
git commit -m "feat: replay deterministic inventory and costs"
```

### Task 4: Bind M5 to canonical evidence and publish immutable artifacts

**Files:**
- Create: `yf/src/yieldforge/experiments/deterministic_replay.py`
- Create: `yf/tests/experiments/test_deterministic_replay.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/test_cli.py`

**Step 1: Write failing experiment tests**

Define wished-for functions:

```python
replay_input = prepare_m5_replay_input(m4_pack, m4_result, m0)
result = evaluate_m5_replay(replay_input, m0)
path = publish_m5_result(tmp_path, result)
assert load_m5_result(path, replay_input=replay_input, m0=m0) == result
```

Cover exact M0/M4 binding, independent canonical M4 result validation, Shapely version binding,
generated/assumed provenance labels, exact canonical fixture values, immutable publication,
regular-file/no-symlink/size bounds, canonical JSON, input/result tampering, changed event order,
changed costs, changed geometry, changed M4 identity, and loader replay mismatch.

Add CLI tests for:

- `experiments prepare-deterministic-replay`; and
- `experiments evaluate-deterministic-replay`.

**Step 2: Run the focused tests and verify RED**

```bash
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups \
  pytest tests/experiments/test_deterministic_replay.py tests/test_cli.py -q
```

Expected: imports/commands are missing.

**Step 3: Implement canonical input preparation**

Load and validate M0 plus canonical M4 input/result. Build the generated fixture with:

- 10x10 standard sheet;
- part `m5-part-a`, 4x10, released at `2026-01-01T00:00:00Z`;
- part `m5-part-b`, 3x10, released one hour later;
- horizon at `2026-01-01T02:00:00Z`;
- purchase `1.0` per area, storage `0.01` per area-hour, return `2.0`, retrieval `3.0`,
  scrap credit `0.1` per area; and
- the M4 zero-clearance fit config and registered search config.

Build `yfrpi-*` only after all source evidence validates.

**Step 4: Implement immutable publication and independent loading**

Use canonical sorted/indented JSON plus newline, bounded non-following regular-file reads, atomic
write-once publication, exact-byte idempotence, and semantic identity checks. Result loading must
rerun `run_replay` and require exact equality.

**Step 5: Add CLI commands and verify GREEN**

Run the Step 2 command until green, then:

```bash
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups \
  pytest tests/replay tests/experiments/test_deterministic_replay.py tests/test_cli.py -q
```

**Step 6: Commit**

```bash
git add yf/src/yieldforge/experiments/deterministic_replay.py \
  yf/src/yieldforge/cli.py yf/tests/experiments/test_deterministic_replay.py \
  yf/tests/test_cli.py
git commit -m "feat: publish deterministic replay evidence"
```

### Task 5: Execute and pin the canonical M5 result

**Files:**
- Create: `yf/experiments/results/deterministic-replay-input-<input-id>.json`
- Create: `yf/experiments/results/deterministic-replay-result-<result-id>.json`
- Modify: `yf/tests/experiments/test_deterministic_replay.py`

**Step 1: Execute the canonical input publisher**

```bash
cd yf
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups yieldforge \
  experiments prepare-deterministic-replay \
  --m0 experiments/m0-contract-v1.json \
  --m4-input experiments/results/remnant-reuse-input-yfri-26460ffca19eebfc9e479d01.json.gz \
  --m4-result experiments/results/remnant-reuse-result-yfrr-b8b1578fc5e0225f00c4386e.json \
  --output experiments/results
```

Record the emitted input ID and SHA-256.

**Step 2: Execute the canonical replay**

```bash
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups yieldforge \
  experiments evaluate-deterministic-replay \
  --m0 experiments/m0-contract-v1.json \
  --input experiments/results/deterministic-replay-input-<input-id>.json \
  --output experiments/results
```

Expected technical decision: `pass`; two fulfilled orders; one sheet opening; one remnant retrieval;
final net cost `104.9`; one terminal remnant.

**Step 3: Add committed-artifact regression assertions**

Pin input/result IDs, semantic SHA-256 values, exact event/action sequence, lineage generations,
cost components, inventory IDs, summary, and claim ceiling. Load the committed result through the
independent replay validator.

**Step 4: Measure current collision workload**

Time the canonical replay and a deterministic repeated-query diagnostic. Record elapsed time as
noncanonical development evidence. Do not add `jagua-rs` unless the measured collision loop is the
dominant M5 runtime; defer M6-scale judgment until stream cardinality is frozen.

**Step 5: Verify and commit artifacts**

```bash
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups \
  pytest tests/replay tests/experiments/test_deterministic_replay.py tests/test_cli.py -q
git add yf/experiments/results/deterministic-replay-*.json \
  yf/tests/experiments/test_deterministic_replay.py
git commit -m "data: publish M5 deterministic replay result"
```

### Task 6: Close M5 and prepare M6

**Files:**
- Modify: `README.md`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Milestones/M5 - Deterministic replay.md`
- Modify: `Docs/Milestones/M6 - Temporal benchmark data.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`
- Create: `Docs/plans/2026-08-23-m6-temporal-benchmark-preparation.md`

**Step 1: Write the M6 preparation package**

Inventory the three committed order-book fixture IDs and provenance. State that they are schema
prototypes, not confirmation data. Define the required M6 freeze decisions:

- stream regimes, counts, seeds, horizon, and calibration/evaluation split;
- material mapping, standard stock, numeric rates, and terminal sensitivities;
- candidate archive and task-projection bindings;
- no-signal and recurrence negative/positive controls;
- failure cells and denominator treatment;
- source-observed, derived, generated, and assumed field families;
- second-geometry-corpus gap required by the eventual M0 supporting gate; and
- M6 handoff requirements for M7 action parity.

**Step 2: Update only supported milestone truth**

If and only if the canonical result independently replays and every M5 gate passes, mark M5 Passed
and M6 Next. Record exact artifact IDs, event/action counts, hand-computable totals, verification,
collision-backend decision, provenance, limitations, and non-goals. Otherwise leave M5 open with
the exact failed gate.

**Step 3: Commit documentation**

```bash
git add README.md Docs/Current\ Work.md Docs/Milestones \
  Docs/plans/2026-08-23-m6-temporal-benchmark-preparation.md
git commit -m "docs: record M5 replay result and prepare M6"
```

### Task 7: Review and verify the complete repository

**Files:**
- Review all M5/M6 changes

**Step 1: Review the plan line by line**

Confirm every acceptance item is implemented and no M6 benchmark, M7 baseline, M8 oracle, savings,
physical, or commercial claim slipped into M5.

**Step 2: Run fresh full verification**

```bash
cd yf
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups pytest -q
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups ruff check .
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv run --all-groups ruff format --check .
UV_CACHE_DIR=/private/tmp/yieldforge-uv-cache-full uv build

cd web
npm ci
npm test
npm run typecheck
npm run build

cd ../..
git diff --check
git status --short --branch
```

The real-browser suite is not applicable unless implementation changes browser/API behavior.

**Step 3: Complete the development branch**

Use `superpowers:finishing-a-development-branch`, report exact commits/artifacts/verification and
present the required integration options. Do not merge or push before the user chooses.
