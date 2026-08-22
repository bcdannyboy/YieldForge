# Nest Lab Completed-Run History Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a newest-first, completed-archive-only run history to Nest Lab, with strict provenance, safe archive switching, and real browser proof through FastAPI and Spyrrow.

**Architecture:** Persist the immutable solve settings already held by each job into `JobSnapshot`, then expose a dedicated task-bound completed-run API that reopens and verifies every returned archive before publishing its hash. The React client strictly parses that contract and treats browsing an archived run as state distinct from an active streamed solve, with request-generation guards preventing stale candidate or geometry responses from crossing selections.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, PostgreSQL-backed `SolverJobService`, pytest, React 19, TypeScript, Vitest/Testing Library, Playwright.

**Execution note:** Work directly on `main` in the existing checkout because the user explicitly prohibited a worktree. Preserve unrelated changes and make small verified commits.

---

### Task 1: Preserve immutable run settings in job snapshots

**Files:**
- Modify: `yf/src/yieldforge/workbench/contracts.py`
- Modify: `yf/src/yieldforge/workbench/jobs.py`
- Test: `yf/tests/workbench/test_jobs.py`
- Test fixture: `yf/tests/workbench/test_api.py`

**Step 1: Write the failing snapshot test**

Add an assertion to the job-service lifecycle test that both live and recovered `JobSnapshot` values expose the exact strict `SpyrrowRunConfig` and `max_runtime_seconds` from the submitted `SolveRequest`:

```python
assert snapshot.config == request.config
assert snapshot.max_runtime_seconds == request.max_runtime_seconds
```

Update only fixture construction required to let collection reach the new assertion.

**Step 2: Run the focused test and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest yf/tests/workbench/test_jobs.py -q
```

Expected: failure because `JobSnapshot` has no `config` or `max_runtime_seconds` fields.

**Step 3: Add strict snapshot fields**

Add these immutable fields to `JobSnapshot`:

```python
config: SpyrrowRunConfig
max_runtime_seconds: StrictFloat = Field(gt=0)
```

Populate them in `SolverJobService._snapshot`:

```python
config=state.request.config,
max_runtime_seconds=state.request.max_runtime_seconds,
```

Update static test snapshots with `SpyrrowRunConfig(seed=23, total_computation_time=1, num_workers=1)` and `max_runtime_seconds=2.0`. Do not expose either internal archive paths or worker PIDs through `JobView`.

**Step 4: Run focused job tests and verify GREEN**

Run the Task 1 command again. Expected: all focused job tests pass.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/workbench/contracts.py yf/src/yieldforge/workbench/jobs.py yf/tests/workbench/test_jobs.py yf/tests/workbench/test_api.py
git commit -m "feat: preserve solver run settings"
```

### Task 2: Add the verified completed-run API contract

**Files:**
- Modify: `yf/src/yieldforge/workbench/api_contracts.py`
- Modify: `yf/src/yieldforge/workbench/app.py`
- Test: `yf/tests/workbench/test_api.py`

**Step 1: Write failing API tests**

Add focused tests for `GET /api/tasks/13958/completed-runs?limit=20` that require:

- schema `yieldforge.api-completed-run-page.v1`;
- newest-first job IDs and a maximum accepted limit of 50;
- exact task/source-slice filtering;
- nested strict settings with seed, computation seconds, worker count, early termination, separation, and hard runtime;
- archive identity `{schema_version: "yieldforge.candidate-archive.v1", batch_sha256: <batch_content_hash>}`;
- no `archive_path` or `worker_pid` anywhere in the response;
- a structured `archive_integrity` error if a completed snapshot has no readable verified batch.

Use two snapshots with distinct timestamps/settings to prove ordering rather than relying on fixture insertion order.

**Step 2: Run the focused tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest yf/tests/workbench/test_api.py -k completed_run -q
```

Expected: 404 for the new route or import failures for the new response models.

**Step 3: Add strict public models**

Define extra-forbidding models in `api_contracts.py`:

```python
class CompletedRunSettings(ApiContract):
    seed: StrictInt
    total_computation_time: StrictInt = Field(gt=0)
    num_workers: Literal[1]
    early_termination: StrictBool
    min_items_separation: StrictFloat | None = Field(default=None, ge=0)
    max_runtime_seconds: StrictFloat = Field(gt=0)


class CompletedArchiveIdentity(ApiContract):
    schema_version: Literal["yieldforge.candidate-archive.v1"]
    batch_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class CompletedRunView(ApiContract):
    schema_version: Literal["yieldforge.api-completed-run.v1"] = "yieldforge.api-completed-run.v1"
    job: JobView
    settings: CompletedRunSettings
    archive: CompletedArchiveIdentity


class CompletedRunPage(ApiContract):
    schema_version: Literal["yieldforge.api-completed-run-page.v1"] = "yieldforge.api-completed-run-page.v1"
    items: tuple[CompletedRunView, ...]
```

**Step 4: Implement verified newest-first assembly**

In `app.py`, resolve the task through the existing catalog, filter `snapshots_for_source_task` to completed snapshots, take the newest bounded window, reverse it, and for each snapshot:

```python
batch = jobs.completed_batch(snapshot.job_id)
if batch is None:
    raise ApiProblem(500, "archive_integrity", "Completed run archive is unavailable or invalid")
```

Build the archive hash from `batch_content_hash(batch)`. Execute filesystem archive reads through `run_in_threadpool`. Return no partial page if any selected completed archive fails verification.

**Step 5: Run API and workbench tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest yf/tests/workbench/test_api.py yf/tests/workbench/test_jobs.py -q
```

Expected: all selected tests pass.

**Step 6: Commit**

```bash
git add yf/src/yieldforge/workbench/api_contracts.py yf/src/yieldforge/workbench/app.py yf/tests/workbench/test_api.py
git commit -m "feat: expose completed solver archives"
```

### Task 3: Add strict TypeScript completed-run parsing

**Files:**
- Modify: `yf/web/src/contracts.ts`
- Modify: `yf/web/src/api.ts`
- Modify: `yf/web/src/test/fixtures.ts`
- Test: `yf/web/src/contracts.test.ts`

**Step 1: Write failing parser tests**

Add a valid completed-run page fixture and tests that reject:

- an unknown schema version;
- missing or extra settings fields;
- non-one worker count;
- invalid nullable separation;
- uppercase, short, or non-hex archive hashes;
- extra archive fields such as a filesystem path.

**Step 2: Run parser tests and verify RED**

Run:

```bash
npm --prefix yf/web test -- --run src/contracts.test.ts
```

Expected: failure because `CompletedRun` and `parseCompletedRunPage` do not exist.

**Step 3: Implement types and strict parsers**

Export `CompletedRunSettings`, `CompletedArchiveIdentity`, `CompletedRun`, and `CompletedRunPage`. Parse exact object keys, exact schema literals, finite numeric ranges, ISO timestamps through the existing `JobView` parser, and `/^[0-9a-f]{64}$/` hashes.

Extend `WorkbenchClient` with:

```ts
listCompletedRuns(tasksIndex: number): Promise<CompletedRunPage>;
```

Implement it as:

```ts
return parseCompletedRunPage(await this.get(`/api/tasks/${tasksIndex}/completed-runs?limit=20`));
```

Keep the existing `listTaskJobs` method for compatibility, but stop using it in Nest Lab.

**Step 4: Run parser tests and TypeScript and verify GREEN**

Run:

```bash
npm --prefix yf/web test -- --run src/contracts.test.ts
npm --prefix yf/web run typecheck
```

Expected: parser tests and type checking pass.

**Step 5: Commit**

```bash
git add yf/web/src/contracts.ts yf/web/src/api.ts yf/web/src/test/fixtures.ts yf/web/src/contracts.test.ts
git commit -m "feat: parse completed run history"
```

### Task 4: Build safe Nest Lab run-history browsing

**Files:**
- Modify: `yf/web/src/nest/NestLab.tsx`
- Modify: `yf/web/src/styles.css`
- Modify: `yf/web/src/App.test.tsx`

**Step 1: Write failing interaction tests**

Replace the old implicit archive-rediscovery test with tests requiring:

- a `Completed run history` region with newest-first cards;
- the newest run selected on load;
- visible completion time, settings, assumptions, full job ID, and full SHA-256;
- selecting an older run fetches its candidate pages and geometry;
- an empty history explanation;
- an independent history error that does not erase task controls;
- history buttons disabled during an active streamed solve;
- post-completion refresh selecting the new archived run;
- stale candidate and geometry promises from an old run cannot overwrite the current selection.

**Step 2: Run UI tests and verify RED**

Run:

```bash
npm --prefix yf/web test -- --run src/App.test.tsx
```

Expected: failures because the history panel and `listCompletedRuns` behavior are absent.

**Step 3: Separate live-job and archived-run state**

Add state for `completedRuns`, `selectedRunId`, and `historyError`. Load task and history independently so either result can render if the other fails. A selection helper must:

```ts
dispatch({ type: "reset" });
lastSequence.current = 0;
setTerminalCandidates([]);
setSelectedCandidate(null);
setGeometry(null);
manualSelection.current = false;
setSelectedRunId(run.job.job_id);
setJob(run.job);
```

Increment candidate and geometry request-generation refs during every run/candidate reset and compare the captured generation and job/candidate IDs before applying asynchronous results.

**Step 4: Render accessible immutable history cards**

Add a labelled history panel after controls. Render newest-first buttons with `aria-pressed`, disable them while a non-terminal live job is active, and display all public settings plus the full batch hash. Label every item as immutable archive evidence. Do not add rerun, delete, comparison ranking, or economic language.

**Step 5: Reconcile newly completed jobs**

On solve start, clear only the selected history ID and archive view while retaining history cards. When the event stream reaches completed, refresh `listCompletedRuns`, find the live job ID, set the refreshed history, and select that exact run once. Keep cancellation and failure behavior unchanged.

**Step 6: Run UI tests and verify GREEN**

Run:

```bash
npm --prefix yf/web test -- --run src/App.test.tsx
npm --prefix yf/web run typecheck
```

Expected: interaction tests and type checking pass.

**Step 7: Commit**

```bash
git add yf/web/src/nest/NestLab.tsx yf/web/src/styles.css yf/web/src/App.test.tsx
git commit -m "feat: browse Nest Lab run history"
```

### Task 5: Prove two-run browsing through the real stack

**Files:**
- Modify: `yf/web/e2e/workbench.spec.ts`
- Modify: `README.md`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Development/Getting Started.md`
- Modify: `Docs/Development/Research Workbench.md`

**Step 1: Extend the real E2E test**

In the existing mutation-enabled task `13958` test, create two runs with distinct seeds. Capture both POST response job IDs, wait for each exact run to become a selected immutable history card, then select the older job ID. Assert that its full batch hash, candidate archive, and geometry SVG are visible. Continue to use the real API and do not mock Spyrrow.

**Step 2: Restart the real services and verify RED or integration gaps**

Restart FastAPI at `127.0.0.1:8765` and Vite at `127.0.0.1:5173`, then run:

```bash
YIELDFORGE_E2E_REAL_API=true YIELDFORGE_E2E_EXTERNAL=true npm --prefix yf/web run e2e -- --workers=1
```

Expected before final fixes: the new two-run flow either passes or exposes a real integration mismatch to fix before proceeding.

**Step 3: Fix only evidence-backed integration defects**

Apply the smallest contract, request-order, accessibility, or timing fix justified by the browser failure. Add or strengthen a focused unit/API regression test before changing production behavior.

**Step 4: Update bounded documentation**

Document the completed-run endpoint, Nest Lab history behavior, and local run instructions. State explicitly that history is local immutable archive evidence for the 256-task catalog, only qualified task `13958` is runnable, task `25801` remains view-only, and no residual geometry, remnant reuse, simulator, oracle, economic comparison, or savings result exists.

**Step 5: Re-run real E2E and verify GREEN**

Run the Task 5 E2E command again. Expected: all real tests pass, except only an explicitly documented environment-dependent skip if one remains unavoidable.

**Step 6: Commit**

```bash
git add yf/web/e2e/workbench.spec.ts README.md "Docs/Current Work.md" Docs/Development/Getting\ Started.md Docs/Development/Research\ Workbench.md
git commit -m "test: prove completed run browsing"
```

### Task 6: Full verification, integrated review, and delivery

**Files:**
- Review: all files changed since design commit `77e68e5`

**Step 1: Run Python verification**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run --all-groups pytest
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff format --check .
```

Expected: full suite passes with only documented environment-dependent skips; Ruff check and format check pass.

**Step 2: Run frontend verification**

```bash
npm --prefix yf/web test -- --run
npm --prefix yf/web run typecheck
npm --prefix yf/web run build
```

Expected: all unit tests, TypeScript, and production build pass.

**Step 3: Run the real browser suite again**

```bash
YIELDFORGE_E2E_REAL_API=true YIELDFORGE_E2E_EXTERNAL=true npm --prefix yf/web run e2e -- --workers=1
```

Expected: real frontend-to-FastAPI-to-PostgreSQL-to-Spyrrow flow passes.

**Step 4: Review repository hygiene**

```bash
git diff --check
git status --short
git diff 77e68e5 --stat
git diff 77e68e5
```

Confirm no internal path leaks, unrelated edits, weakened tests, generated runtime data, or claims beyond the design ceiling.

**Step 5: Commit any final mechanical fixes and push**

```bash
git add <only reviewed files>
git commit -m "fix: stabilize Nest Lab run history"
git push origin main
```

Leave FastAPI and Vite running locally for the user and report exact test counts, skips, limitations, and the next bounded product task.
