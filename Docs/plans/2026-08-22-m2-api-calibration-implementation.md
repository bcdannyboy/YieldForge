# M2 API Calibration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a strict, resumable API orchestrator for the 612 registered M2 calibration cells,
execute only those cells, and publish content-addressed calibration evidence plus a
confirmation-ready geometry protocol if the frozen selector succeeds.

**Architecture:** FastAPI remains authoritative for source projection, subprocess supervision,
durable job state, and candidate archives. A new `yieldforge.experiments.calibration` module
validates the parent bundle, enumerates exact cells, drives one API job at a time, persists every
submission/outcome atomically, resumes without duplication, and calculates the frozen selector.
Runtime archives stay under ignored `yf/var/experiments/`; the bounded result and any approved v2
protocol are committed under `yf/experiments/`.

**Tech Stack:** Python 3.12, Pydantic 2.12, FastAPI, urllib/http.client, pytest, Ruff, React contract
tests, Docker Postgres, existing Spyrrow 0.9 worker and archive services.

**Execution constraints:** Work directly on the existing `main` checkout. Use TDD for every new
behavior. Do not submit an evaluation task, no-flip sensitivity, expanded-search seed, or any
solver cell until the implementation and preflight verification are committed and clean.

---

### Task 1: Permit the registered 60-second API outer timeout

**Files:**
- Modify: `yf/src/yieldforge/workbench/contracts.py`
- Modify: `yf/src/yieldforge/workbench/api_contracts.py`
- Test: `yf/tests/workbench/test_api.py`
- Test: `yf/tests/workbench/test_jobs.py`

**Step 1: Write failing boundary tests**

Add tests proving single and matched public requests, internal `SolveRequest`, `JobSnapshot`, and
completed-run settings accept `60.0` but reject values above 60. Keep solver computation time
bounded at 10 seconds and require it to fit inside the outer runtime.

```python
def test_solver_request_accepts_registered_outer_timeout() -> None:
    request = request_payload(max_runtime_seconds=60.0, total_computation_time=10)
    assert CreateSolverJobRequest.model_validate(request).max_runtime_seconds == 60.0

def test_solver_request_rejects_outer_timeout_above_registered_limit() -> None:
    with pytest.raises(ValidationError):
        CreateSolverJobRequest.model_validate(
            request_payload(max_runtime_seconds=60.000001, total_computation_time=10)
        )
```

**Step 2: Run the focused tests and confirm RED**

Run from `yf/`:

```bash
uv run pytest tests/workbench/test_api.py tests/workbench/test_jobs.py -q
```

Expected: the 60-second cases fail at the existing `le=10` validation ceiling.

**Step 3: Implement the minimal ceiling change**

Define one shared `MAX_OUTER_RUNTIME_SECONDS = 60` constant in workbench contracts and use it for:

- `SolveRequest.max_runtime_seconds`;
- `JobSnapshot.max_runtime_seconds`;
- `CreateSolverJobRequest.max_runtime_seconds`;
- `CreateMatchedSolverJobsRequest.max_runtime_seconds`; and
- `CompletedRunSettings.max_runtime_seconds`.

Do not change `total_computation_time <= 10`, worker count, or any browser behavior.

**Step 4: Run focused tests and Ruff**

```bash
uv run pytest tests/workbench/test_api.py tests/workbench/test_jobs.py -q
uv run ruff check src/yieldforge/workbench tests/workbench/test_api.py tests/workbench/test_jobs.py
```

Expected: focused tests and Ruff pass.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/workbench/contracts.py \
  yf/src/yieldforge/workbench/api_contracts.py \
  yf/tests/workbench/test_api.py yf/tests/workbench/test_jobs.py
git commit -m "feat: allow registered calibration timeout"
```

### Task 2: Freeze calibration cells and selector semantics

**Files:**
- Create: `yf/src/yieldforge/experiments/calibration.py`
- Create: `yf/tests/experiments/test_calibration.py`

**Step 1: Write failing cell-enumeration tests**

Load the committed protocol and assert exact order and cardinality:

```python
def test_registered_cells_are_exactly_calibration_population() -> None:
    cells = registered_cells(protocol)
    assert len(cells) == 612
    assert {cell.tasks_index for cell in cells} == set(protocol.split.calibration_task_ids)
    assert {cell.seed for cell in cells} == {0, 1, 2, 3}
    assert {cell.seconds_per_seed for cell in cells} == {1, 3, 10}
    assert all(cell.projection_mode == "source_as_recorded" for cell in cells)
    assert not set(protocol.split.evaluation_task_ids) & {cell.tasks_index for cell in cells}
```

Add rejection tests for duplicate cells, evaluation-task injection, changed seed/time, unordered
attempts, unregistered retry, extra fields, nonfinite metrics, and forged content identities.

**Step 2: Write failing selector tests**

Cover:

- exact 2 percentage-point qualifying gap;
- exact 0.1% median and 0.5% P95 boundaries;
- exact 95% archive validity;
- improvement clipped to zero degradation;
- missing shorter best as infinity/failure;
- missing 10-second reference invalidating the run;
- nearest-rank P95 at `ceil(0.95 * 51)`; and
- fallback to 10 seconds when neither shorter budget qualifies.

Use small pure helper fixtures where possible, but include one full 51-task boundary fixture.

**Step 3: Run focused tests and confirm RED**

```bash
uv run pytest tests/experiments/test_calibration.py -q
```

Expected: collection fails because the calibration module does not exist.

**Step 4: Implement strict models and pure functions**

Add immutable, extra-forbidding, finite models for:

- `CalibrationCell`;
- `CalibrationCandidateObservation`;
- `CalibrationAttemptRecord`;
- `CalibrationCellResult`;
- per-task/per-duration and per-duration summaries;
- selector comparisons;
- `GeometryCalibrationResult` with semantic SHA-256 and derived result ID.

Implement `registered_cells`, exact candidate pooling/deduplication, qualifying calculation,
nearest-rank percentile, archive-rate calculation, degradation calculation, and selection. Keep
all of these independent from network and filesystem code.

**Step 5: Run RED/GREEN tests and Ruff**

```bash
uv run pytest tests/experiments/test_calibration.py -q
uv run ruff check src/yieldforge/experiments/calibration.py \
  tests/experiments/test_calibration.py
uv run ruff format --check src/yieldforge/experiments/calibration.py \
  tests/experiments/test_calibration.py
```

Expected: all focused checks pass.

**Step 6: Commit**

```bash
git add yf/src/yieldforge/experiments/calibration.py \
  yf/tests/experiments/test_calibration.py
git commit -m "feat: freeze M2 calibration selector"
```

### Task 3: Add fail-closed API evidence retrieval

**Files:**
- Modify: `yf/src/yieldforge/experiments/calibration.py`
- Modify: `yf/tests/experiments/test_calibration.py`

**Step 1: Write failing client tests with a local HTTP stub**

Test real serialized requests rather than mocking internal methods. Required cases:

- corpus summary must match dataset/catalog identity;
- task detail supplies exact source assumptions;
- submission body fixes source projection, no interventions, one worker-owned solve, registered
  seed/time, and 60-second outer runtime;
- polling stops only on a strict terminal `JobView`;
- candidate pages continue until `next_cursor` is null and reject cursor cycles/duplicate IDs;
- completed-run history must contain the same job, settings, projection binding, and archive hash;
- HTTP errors, unknown fields, malformed JSON, and inconsistent identities fail closed.

**Step 2: Run focused tests and confirm RED**

```bash
uv run pytest tests/experiments/test_calibration.py -q
```

Expected: failures because `CalibrationApiClient` is absent.

**Step 3: Implement the minimal synchronous client**

Use the Python standard library with explicit connect/read timeouts. Validate every response
through the existing API Pydantic contracts. Expose methods for summary, task detail, submit, poll,
all-candidate pagination, and completed-run archive lookup. Never accept an archive path from the
transport.

**Step 4: Verify GREEN and commit**

```bash
uv run pytest tests/experiments/test_calibration.py -q
uv run ruff check src/yieldforge/experiments/calibration.py \
  tests/experiments/test_calibration.py
git add yf/src/yieldforge/experiments/calibration.py \
  yf/tests/experiments/test_calibration.py
git commit -m "feat: retrieve calibration evidence through API"
```

### Task 4: Add write-once resume and registered retry orchestration

**Files:**
- Modify: `yf/src/yieldforge/experiments/calibration.py`
- Modify: `yf/tests/experiments/test_calibration.py`
- Modify: `.gitignore`

**Step 1: Write failing persistence and resume tests**

Use a temporary output directory and a scripted local API stub. Prove:

- run identity is written before the first submission;
- intent is written before POST and job ID immediately after POST;
- resume polls a saved job rather than posting again;
- completed records cannot be overwritten;
- only the registered worker/timeout classes receive one retry;
- archive failure, cancellation, zero candidates, and unknown failure do not retry;
- retry request bytes are identical to the first request;
- orchestration is strictly sequential; and
- an output root bound to another protocol/API corpus is rejected.

**Step 2: Run focused tests and confirm RED**

```bash
uv run pytest tests/experiments/test_calibration.py -q
```

**Step 3: Implement atomic state and orchestration**

Add atomic create/write helpers using same-directory temporary files, `fsync`, and rename without
destructive replacement. Store each cell/attempt under a canonical cell key. Reconstruct state
from strict persisted contracts on resume. Record API unavailability without manufacturing an
experimental attempt.

Add `yf/var/experiments/` to `.gitignore`.

**Step 4: Verify and commit**

```bash
uv run pytest tests/experiments/test_calibration.py -q
uv run ruff check src/yieldforge/experiments/calibration.py \
  tests/experiments/test_calibration.py
git add .gitignore yf/src/yieldforge/experiments/calibration.py \
  yf/tests/experiments/test_calibration.py
git commit -m "feat: orchestrate resumable M2 calibration"
```

### Task 5: Expose the bounded CLI and preflight gate

**Files:**
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/test_cli.py`
- Modify: `Docs/Development/Getting Started.md`
- Modify: `Docs/Current Work.md`

**Step 1: Write failing CLI tests**

Assert `experiments calibrate-geometry-api` requires all five evidence paths/locations, validates
the parent bundle before contacting the API, rejects a confirmation-enabled protocol, and prints
only bounded progress/result summaries. Inject the orchestration callable at the outer boundary so
CLI tests do not run Spyrrow.

**Step 2: Confirm RED, implement, and verify GREEN**

```bash
uv run pytest tests/test_cli.py -q
uv run ruff check src/yieldforge/cli.py tests/test_cli.py
```

Add the command exactly as documented in the approved design. Update setup docs with the dedicated
Postgres/API root, resume behavior, and claim ceiling.

**Step 3: Commit**

```bash
git add yf/src/yieldforge/cli.py yf/tests/test_cli.py \
  'Docs/Development/Getting Started.md' 'Docs/Current Work.md'
git commit -m "feat: add registered M2 calibration command"
```

### Task 6: Preflight the exact implementation before spending compute

**Files:**
- Review: all changes since `bbb50cf`

**Step 1: Run focused and full backend verification**

```bash
cd yf
uv run pytest tests/experiments/test_calibration.py tests/test_cli.py \
  tests/workbench/test_api.py tests/workbench/test_jobs.py
uv run --all-groups pytest
uv run ruff check .
uv run ruff format --check .
```

**Step 2: Run frontend compatibility gates**

The outer runtime validation ceiling is a shared API contract change even though no UI is added:

```bash
cd web
npm test
npm run typecheck
npm run build
```

Real Playwright is not required unless implementation changes the interactive browser workflow.

**Step 3: Validate bundle and audit the diff**

```bash
cd ..
uv run yieldforge experiments validate \
  --m0 experiments/m0-contract-v1.json \
  --geometry experiments/pure-geometry-calibration-v1.json \
  --catalog datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json \
  --catalog-manifest datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json
cd ..
git diff --check bbb50cf..HEAD
git status --short --branch
```

Do not begin calibration unless all applicable gates pass and `confirmation=disabled` remains.

### Task 7: Start a dedicated API and execute the registered calibration

**Files:**
- Runtime only: `yf/var/experiments/yfgp-49906e93ed9ff0446705247b/`

**Step 1: Verify/import the committed catalog**

Start the existing Postgres service, import the exact committed catalog if needed, and verify the
API summary reports 256 tasks with 254 eligible and the registered catalog identity.

**Step 2: Start the dedicated API**

Use a new workbench root under the calibration output. Record the implementation commit before
launch. Do not reuse interactive job archives.

**Step 3: Dry-run preflight without submitting a job**

Add/use a `--preflight-only` mode that validates bundle, API identity, output identity, and exact
612-cell enumeration, then exits. Confirm no job or archive was created.

**Step 4: Execute or resume all registered cells**

Run the command without `--preflight-only`. Monitor progress at bounded intervals. If the process
or API stops, repair only the operational fault and resume from persisted submission state. Never
delete or overwrite an attempt.

Expected configured solver time: approximately 48 minutes before overhead/retries.

**Step 5: Revalidate runtime evidence**

Require 612 terminal registered cell results, no evaluation IDs, no sensitivity projection, no
unregistered seed/time, and exact archive identities for every completed attempt.

### Task 8: Commit the calibration result and, if valid, freeze protocol v2

**Files:**
- Create: `yf/experiments/results/pure-geometry-calibration-<result-id>.json`
- Conditionally create: `yf/experiments/pure-geometry-confirmation-v2.json`
- Modify: `yf/src/yieldforge/experiments/contracts.py`
- Modify: `yf/src/yieldforge/experiments/calibration.py`
- Modify: `yf/tests/experiments/test_contracts.py`
- Modify: `yf/tests/experiments/test_calibration.py`

**Step 1: Copy only the canonical bounded result into source control**

Verify its semantic hash and result ID before adding it. Do not commit local archive directories,
API job directories, PIDs, or filesystem paths.

**Step 2: If selector valid, write failing confirmation-protocol tests**

Require the new protocol to preserve all v1 frozen rules, bind the result hash, set exactly the
selected seconds, and enable confirmation. Mutating any population, seed, setting, envelope,
metric, retry, threshold, or parent identity must fail even after re-identification.

If selector invalid, instead test that confirmation publication is rejected and leave v1 active.

**Step 3: Implement the exact post-result contract and artifact**

Pin the reviewed semantic identity in code, as with v1. Extend validation so confirmation-ready
protocols require the exact committed parent and calibration result.

**Step 4: Verify and commit**

```bash
uv run pytest tests/experiments/test_contracts.py tests/experiments/test_calibration.py -q
uv run ruff check src/yieldforge/experiments tests/experiments
uv run ruff format --check src/yieldforge/experiments tests/experiments
git add yf/experiments yf/src/yieldforge/experiments yf/tests/experiments
git commit -m "data: publish M2 calibration result"
```

### Task 9: Update milestone truth and run final verification

**Files:**
- Modify: `README.md`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Milestones/M0 - Experiment contract.md`
- Modify: `Docs/Milestones/M2 - Static data and Sparrow.md`
- Modify: `Docs/Development/Getting Started.md`

**Step 1: Document only observed bounded results**

Report cell/archive validity, selector comparisons, selected duration or invalid status, and exact
claim ceiling. Mark M0 Passed only if a valid confirmation-ready protocol exists. Keep M2 Active
until the 203-task evaluation is executed under that protocol.

**Step 2: Repeat all applicable verification**

Run the complete backend suite, Ruff checks, frontend tests/typecheck/build, result/protocol
validation, `git diff --check`, clean status, and full diff review. Record environment-dependent
skips exactly.

**Step 3: Commit and push**

```bash
git add README.md Docs
git commit -m "docs: record M2 calibration outcome"
git push origin main
```

Push only if every applicable gate passes. The next task after a valid result is the 203-task
confirmatory geometry evaluation; do not start it in this plan.
