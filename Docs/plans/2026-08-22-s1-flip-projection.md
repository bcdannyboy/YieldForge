# Assumption-backed `s1` Flip Projection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make strict pure-`s1` flip-bearing Lectra tasks runnable under an exact recorded-transform assumption, add an explicitly derived no-flip ablation, and exercise matched arms through the real local browser, FastAPI, job supervisor, Spyrrow, immutable archives, and run comparison.

**Architecture:** Preserve the source-lossless catalog and introduce versioned solver-projection evidence that lowers one uniform per-part flip basis into a derived polygon before passing rotation alternatives to Spyrrow. Store recorded and no-flip projections in the Postgres read model, bind every job/archive to its projection mode and hash, and allow exactly one bounded two-arm matched experiment to run concurrently. Keep non-`s1` tasks blocked and label the no-flip arm as a derived intervention rather than a source task.

**Tech Stack:** Python 3.12, Pydantic 2, Shapely 2, Spyrrow 0.9, FastAPI, asyncio subprocess supervision, PostgreSQL/psycopg, React 19, TypeScript, Vitest, Playwright, Docker-qualified Lectra evidence.

---

Implementation stays on `main` because the user explicitly prohibited a worktree. Preserve unrelated working-tree changes, use `apply_patch` for edits, and commit after each coherent task.

### Task 1: Define projection modes, evidence, and exact transform semantics

**Files:**
- Modify: `yf/src/yieldforge/domain.py`
- Modify: `yf/src/yieldforge/datasets/projection.py`
- Test: `yf/tests/test_domain.py`
- Test: `yf/tests/datasets/test_projection.py`

**Step 1: Write failing contract tests**

Add tests for:

```python
ProjectionMode.SOURCE_AS_RECORDED
ProjectionMode.FORCE_FLIP_X_ZERO
SolverProjectionBinding(
    mode=ProjectionMode.SOURCE_AS_RECORDED,
    transform_convention="local_x_coordinate_negation_before_rotation",
    projection_sha256="a" * 64,
    assumption_codes=(
        "interpret_s1_degenerate_entries_as_allowed_rotations",
        "interpret_s1_flip_x_as_local_x_coordinate_negation_before_rotation",
    ),
    intervention_codes=(),
    source_flip_part_count=1,
)
```

Require sorted unique codes, a canonical SHA-256, source-preserving/intervention consistency, and
backward-compatible `SourceTaskBinding` parsing when `solver_projection` is absent.

**Step 2: Write failing geometry/projection tests**

Use an asymmetric polygon and assert the exact source-recorded lowering:

```python
source = [(1.0, 2.0), (4.0, 2.0), (2.0, 5.0), (1.0, 2.0)]
assert reflect_local_x(source) == [
    (-1.0, 2.0), (-4.0, 2.0), (-2.0, 5.0), (-1.0, 2.0)
]
```

Assert reflection is an involution, preserves absolute area and ring closure, never mutates source
geometry, happens before solver rotation, and produces deterministic projection hashes. Add strict
tests for binary flips, equal sequence lengths, uniform flip values within one row, duplicate
orientation-state rejection, and mixed flip-state rejection.

**Step 3: Run RED**

Run:

```bash
cd yf
uv run pytest tests/test_domain.py tests/datasets/test_projection.py -q
```

Expected: failures for missing projection contracts and flip support.

**Step 4: Implement minimal projection contracts**

Add:

```python
class ProjectionMode(StrEnum):
    SOURCE_AS_RECORDED = "source_as_recorded"
    FORCE_FLIP_X_ZERO = "force_flip_x_zero"

class SolverProjectionBinding(ContractModel):
    schema_version: Literal["yieldforge.solver-projection-binding.v1"] = ...
    mode: ProjectionMode
    transform_convention: Literal["local_x_coordinate_negation_before_rotation"]
    projection_sha256: Sha256
    assumption_codes: tuple[AssumptionCode, ...]
    intervention_codes: tuple[AssumptionCode, ...] = ()
    source_flip_part_count: StrictInt = Field(ge=0)

class ProjectedTask(ContractModel):
    schema_version: Literal["yieldforge.projected-task.v1"] = ...
    problem: StripPackingProblem
    projection: SolverProjectionBinding
```

Add optional `solver_projection: SolverProjectionBinding | None = None` to
`SourceTaskBinding` so existing local archives remain readable; require it for newly created API
jobs later.

**Step 5: Implement exact lowering**

In `projection.py`, introduce constants:

```python
S1_ORIENTATION_ASSUMPTION = "interpret_s1_degenerate_entries_as_allowed_rotations"
S1_FLIP_ASSUMPTION = (
    "interpret_s1_flip_x_as_local_x_coordinate_negation_before_rotation"
)
NO_FLIP_ABLATION = "force_s1_flip_x_zero_for_ablation"
```

Refactor `_constraint_orientations` to return `(part_id, rotations, flip_x)`. Accept strict integer
zero or one only; require all alternatives in a row to use the same flag. For
`source_as_recorded`, negate every source x coordinate when the uniform flag is one. For
`force_flip_x_zero`, retain the original polygon and record the intervention. Canonically hash the
mode, convention, codes, flip count, and projected problem. Keep `project_task(...,
mode=ProjectionMode.SOURCE_AS_RECORDED)` fail-closed.

**Step 6: Run GREEN and quality checks**

```bash
cd yf
uv run pytest tests/test_domain.py tests/datasets/test_projection.py -q
uv run ruff check src/yieldforge/domain.py src/yieldforge/datasets/projection.py tests/test_domain.py tests/datasets/test_projection.py
uv run ruff format --check src/yieldforge/domain.py src/yieldforge/datasets/projection.py tests/test_domain.py tests/datasets/test_projection.py
```

**Step 7: Commit**

```bash
git add yf/src/yieldforge/domain.py yf/src/yieldforge/datasets/projection.py yf/tests/test_domain.py yf/tests/datasets/test_projection.py
git commit -m "feat: project explicit s1 flip states"
```

### Task 2: Requalify strict pure-`s1` tasks without weakening the gate

**Files:**
- Modify: `yf/src/yieldforge/datasets/lectra_slice.py`
- Modify: `yf/src/yieldforge/datasets/corpus.py`
- Test: `yf/tests/datasets/test_lectra_slice.py`
- Test: `yf/tests/workbench/test_corpus.py`

**Step 1: Write failing qualifier tests**

Replace tests that expect every `flip_x=1` row to fail. Prove that:

- binary, uniform flips pass strict `s1` validation;
- a flip-bearing task receives both sorted assumption codes;
- a zero-flip task retains only the existing rotation assumption;
- non-binary, mixed, interval, malformed, duplicate-part, unrelated-field, and non-`s1` tasks
  remain blocked;
- task `25801` remains `contains_non_s1_constraints` regardless of its flip rows.

**Step 2: Run RED**

```bash
cd yf
uv run pytest tests/datasets/test_lectra_slice.py tests/workbench/test_corpus.py -q
```

**Step 3: Implement qualification facts**

Make `_validate_s1_task` return a small immutable result containing `flip_part_count`. Accept only
strict binary uniform flips. Build assumption codes with:

```python
codes = (S1_ASSUMPTION,) if flip_part_count == 0 else tuple(sorted((
    S1_ASSUMPTION,
    S1_FLIP_ASSUMPTION,
)))
```

Bump only the catalog conversion ruleset from `lectra-catalog-rules.v1` to
`lectra-catalog-rules.v2`; preserve the two-task slice ruleset and task `13958` behavior.

**Step 4: Add browser-safe projection choices and diagnostics**

Add strict DTOs to `corpus.py`:

```python
class ProjectionOptionDto(CorpusDto):
    mode: ProjectionMode
    source_preserving: StrictBool
    assumption_codes: tuple[StrictStr, ...]
    intervention_codes: tuple[StrictStr, ...]

class S1ProjectionDiagnosticsDto(CorpusDto):
    orientation_state_count: SafeJsonInt
    flip_constraint_count: SafeJsonInt
    flip_part_count: SafeJsonInt
    mixed_flip_constraint_count: SafeJsonInt
```

Expose options on `SolveCapabilityDto` and diagnostics on `TaskDetailDto`. Eligible flip tasks offer
`source_as_recorded` and `force_flip_x_zero`; eligible zero-flip tasks offer recorded only; blocked
tasks offer no projection option. Derive diagnostics from source constraints, never from frontend
inference.

**Step 5: Run GREEN and commit**

```bash
cd yf
uv run pytest tests/datasets/test_lectra_slice.py tests/workbench/test_corpus.py -q
uv run ruff check src/yieldforge/datasets tests/datasets tests/workbench/test_corpus.py
git add yf/src/yieldforge/datasets/lectra_slice.py yf/src/yieldforge/datasets/corpus.py yf/tests/datasets/test_lectra_slice.py yf/tests/workbench/test_corpus.py
git commit -m "feat: qualify uniform s1 flip tasks"
```

### Task 3: Store both projection arms in the validated Postgres read model

**Files:**
- Modify: `yf/src/yieldforge/datasets/postgres_catalog.py`
- Modify: `yf/src/yieldforge/datasets/postgres_corpus.py`
- Test: `yf/tests/datasets/test_postgres_catalog.py`
- Test: `yf/tests/workbench/test_postgres_corpus.py`

**Step 1: Write failing read-model tests**

Require schema v2, a `solver_projections_json` JSONB object keyed by projection mode, exact
`ProjectedTask` validation, record/root hashing over the complete map, and no projection payload for
blocked tasks. Require recorded-only maps for zero-flip tasks and two exact maps for flip-bearing
eligible tasks. Reject missing arms, extra arms, wrong hashes, mismatched problem names, and
catalog/task identity drift.

**Step 2: Run RED**

```bash
cd yf
uv run pytest tests/datasets/test_postgres_catalog.py tests/workbench/test_postgres_corpus.py -q
```

**Step 3: Implement schema v2**

Change `READ_MODEL_SCHEMA_VERSION` to `yieldforge.postgres-catalog.v2`. Replace
`solver_problem_json` with non-executable passive `solver_projections_json` in `_TaskRecord`, table
DDL/signatures, insert/revalidation, record hashes, and read-model root hashes. During import, call
the same `project_task` function for each server-owned projection option and persist the complete
`ProjectedTask.model_dump(mode="json")` result.

**Step 4: Extend the corpus protocol**

Replace `project_problem` with:

```python
def project_task(
    self,
    tasks_index: int,
    *,
    mode: ProjectionMode,
    acknowledged_assumption_codes: tuple[str, ...],
    acknowledged_intervention_codes: tuple[str, ...],
) -> ProjectedTask: ...
```

Both file-backed and Postgres services require exact option codes and return only a hash-validated
projection. Postgres must compare each runtime row with its startup-validated record hash.

**Step 5: Run GREEN and commit**

```bash
cd yf
uv run pytest tests/datasets/test_postgres_catalog.py tests/workbench/test_postgres_corpus.py -q
uv run ruff check src/yieldforge/datasets/postgres_catalog.py src/yieldforge/datasets/postgres_corpus.py tests/datasets/test_postgres_catalog.py tests/workbench/test_postgres_corpus.py
git add yf/src/yieldforge/datasets/postgres_catalog.py yf/src/yieldforge/datasets/postgres_corpus.py yf/tests/datasets/test_postgres_catalog.py yf/tests/workbench/test_postgres_corpus.py
git commit -m "feat: persist validated projection arms"
```

### Task 4: Regenerate and bind the committed catalog evidence

**Files:**
- Modify: `yf/datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json`
- Modify: `yf/datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json`
- Modify: pinned hashes/counts in `yf/src/yieldforge/datasets/postgres_catalog.py`
- Modify: committed expectations in `yf/src/yieldforge/datasets/postgres_corpus.py`
- Modify: catalog identity tests under `yf/tests/datasets/` and `yf/tests/workbench/`

**Step 1: Build the locked qualifier and export twice**

Use two new `mktemp -d` output directories and the existing pinned raw/audit evidence:

```bash
cd yf
docker build --file tools/lectra/Dockerfile --tag yieldforge-lectra-qualifier:7030786-v1.1 .
uv run python tools/lectra/run_qualifier.py --mode catalog \
  --image yieldforge-lectra-qualifier:7030786-v1.1 \
  --input "$PWD/var/data/raw/lectra-7030786-v1.1" \
  --output NEW_EMPTY_OUTPUT_A \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --audit-report "$PWD/var/data/reports/lectra-7030786-v1.1/lectra-audit.json" \
  --timeout-seconds 900
```

Repeat into output B. Expected: byte-identical `lectra-catalog.json` files.

**Step 2: Validate the new census**

Require exactly 256 tasks, 8,358 parts, 745 shapes, and 8,398 constraints. Expect task `13958` to
retain one assumption, task `6669` to become eligible with two assumptions and six flip parts, and
task `25801` to remain blocked. The expected capability ceiling is 254 assumption-backed and two
view-only only if the strict requalification output proves that exact count.

**Step 3: Promote with no-clobber review**

Generate a new manifest from exact artifact bytes, update all committed SHA-256/logical/root
constants from executable import evidence, then use `apply_patch` for small manifests/constants and
a mechanical copy only for the generated catalog artifact. Never hand-edit normalized source rows.

**Step 4: Run catalog/import tests**

```bash
cd yf
uv run pytest tests/datasets/test_lectra_slice.py tests/datasets/test_postgres_catalog.py tests/workbench/test_postgres_corpus.py -q
```

**Step 5: Commit**

```bash
git add yf/datasets/catalogs/lectra-7030786-v1.1 yf/src/yieldforge/datasets/postgres_catalog.py yf/src/yieldforge/datasets/postgres_corpus.py yf/tests
git commit -m "data: requalify uniform flip catalog tasks"
```

### Task 5: Bind projections and matched-arm identity through jobs and archives

**Files:**
- Modify: `yf/src/yieldforge/workbench/contracts.py`
- Modify: `yf/src/yieldforge/workbench/jobs.py`
- Modify: `yf/src/yieldforge/archive.py`
- Test: `yf/tests/workbench/test_jobs.py`
- Test: `yf/tests/test_archive.py`
- Test: `yf/tests/workbench/test_solver_worker.py`

**Step 1: Write failing persistence tests**

Add optional matched experiment fields to `SolveRequest`, `JobSnapshot`, and completed evidence:

```python
experiment_pair_id: str | None
experiment_arm: Literal["source_as_recorded", "force_flip_x_zero"] | None
```

Require both together or neither, require the arm to equal the projection binding mode, persist them
in request JSON, recover them after restart, and verify archive manifests still exactly match the
full source/projection binding. Add legacy recovery tests for requests without projection evidence.

**Step 2: Run RED**

```bash
cd yf
uv run pytest tests/test_archive.py tests/workbench/test_jobs.py tests/workbench/test_solver_worker.py -q
```

**Step 3: Implement immutable projection/pair persistence**

New API-created requests must bind the returned `ProjectedTask.problem` and
`ProjectedTask.projection` inside `SourceTaskBinding`. Candidate archive creation and recovery use
the complete binding without exposing filesystem paths. Candidate geometry continues to render the
archived projected polygon, so placement remains rotation plus translation.

**Step 4: Run GREEN and commit**

```bash
cd yf
uv run pytest tests/test_archive.py tests/workbench/test_jobs.py tests/workbench/test_solver_worker.py -q
git add yf/src/yieldforge/workbench/contracts.py yf/src/yieldforge/workbench/jobs.py yf/src/yieldforge/archive.py yf/tests/test_archive.py yf/tests/workbench/test_jobs.py yf/tests/workbench/test_solver_worker.py
git commit -m "feat: bind solver projection provenance"
```

### Task 6: Add bounded two-arm concurrent supervision

**Files:**
- Modify: `yf/src/yieldforge/workbench/jobs.py`
- Test: `yf/tests/workbench/test_jobs.py`

**Step 1: Write failing concurrency tests**

Test `start_pair((recorded_request, ablation_request))` for:

- exactly two validated requests with one shared server-generated `pair_id`;
- identical source task, seed, computation time, runtime limit, worker count, separation, and early
  termination;
- distinct recorded/ablation arms and distinct projection hashes;
- atomic capacity validation before either job directory is created;
- two concurrently running subprocesses, independent streams and immutable archives;
- cancellation/failure of one arm not rewriting the other arm;
- rejection of a third job while the pair owns both bounded slots;
- slot release only after both runners are terminal;
- safe restart recovery of either incomplete arm.

**Step 2: Run RED**

```bash
cd yf
uv run pytest tests/workbench/test_jobs.py -q
```

**Step 3: Implement pair reservation**

Replace the singleton `_active_job_id` with a bounded set and a private `_start_validated_locked`
helper. Preserve `start()` semantics: it still rejects whenever any job is active. Add
`start_pair()` that acquires the service lock once, validates both requests and empty capacity, then
creates and schedules exactly two states before releasing the lock. Remove each ID independently in
runner cleanup.

**Step 4: Run GREEN and commit**

```bash
cd yf
uv run pytest tests/workbench/test_jobs.py -q
git add yf/src/yieldforge/workbench/jobs.py yf/tests/workbench/test_jobs.py
git commit -m "feat: supervise matched solver arms"
```

### Task 7: Expose strict single and matched projection APIs

**Files:**
- Modify: `yf/src/yieldforge/workbench/api_contracts.py`
- Modify: `yf/src/yieldforge/workbench/app.py`
- Test: `yf/tests/workbench/test_api.py`

**Step 1: Write failing API tests**

Change the single request to `yieldforge.api-solver-job-request.v2` with required
`projection_mode` and `acknowledged_intervention_codes`. Add:

```python
class CreateMatchedSolverJobsRequest(ApiContract): ...
class MatchedSolverJobsView(ApiContract):
    schema_version: Literal["yieldforge.api-matched-solver-jobs.v1"]
    experiment_pair_id: StrictStr
    source_as_recorded: JobView
    force_flip_x_zero: JobView
```

Cover exact code mismatch, unsupported mode, no ablation on zero-flip tasks, task `6669` success,
task `25801` rejection, paired equal budgets, active-capacity conflicts, and projection evidence in
job/completed-run responses.

**Step 2: Run RED**

```bash
cd yf
uv run pytest tests/workbench/test_api.py -q
```

**Step 3: Implement projection-owned request construction**

Centralize request validation in one helper that fetches task detail, selects an authoritative
projection option, requires exact assumptions/interventions, obtains a `ProjectedTask`, constructs
the source binding, and returns `SolveRequest`. Use it for `POST /api/solver-jobs` and new
`POST /api/matched-solver-jobs`. The paired route creates both requests from one shared settings
object and calls `jobs.start_pair`.

Publish projection mode/hash, interventions, pair ID, and arm only through strict public contracts.
Completed history stays task-bound and archive-verified.

**Step 4: Run GREEN and commit**

```bash
cd yf
uv run pytest tests/workbench/test_api.py -q
uv run ruff check src/yieldforge/workbench tests/workbench
git add yf/src/yieldforge/workbench/api_contracts.py yf/src/yieldforge/workbench/app.py yf/tests/workbench/test_api.py
git commit -m "feat: expose matched projection jobs"
```

### Task 8: Extend frontend contracts and API client fail-closed

**Files:**
- Modify: `yf/web/src/contracts.ts`
- Modify: `yf/web/src/api.ts`
- Modify: `yf/web/src/test/fixtures.ts`
- Test: `yf/web/src/contracts.test.ts`

**Step 1: Write failing parser tests**

Require exact projection options, diagnostics, projection binding, interventions, pair identity,
single-request v2, and matched-response schemas. Reject unknown mode, invalid hashes, unsorted codes,
recorded projections carrying interventions, ablations missing their intervention, and pair-arm
mismatches. Preserve parsing of legacy completed runs whose binding lacks solver projection.

**Step 2: Run RED**

```bash
cd yf/web
npm test -- --run src/contracts.test.ts
```

**Step 3: Implement strict types/parsers/client**

Add `ProjectionMode`, `ProjectionOption`, `S1ProjectionDiagnostics`, `SolverProjectionBinding`,
`CreateMatchedJobsInput`, and `MatchedJobsView`. Extend `WorkbenchClient` with
`createMatchedJobs(input)` posting to `/api/matched-solver-jobs`. Keep every response parser strict
and browser-safe.

**Step 4: Run GREEN and commit**

```bash
cd yf/web
npm test -- --run src/contracts.test.ts
npm run typecheck
cd ../..
git add yf/web/src/contracts.ts yf/web/src/api.ts yf/web/src/test/fixtures.ts yf/web/src/contracts.test.ts
git commit -m "feat: parse projection experiment contracts"
```

### Task 9: Explain precise flip eligibility in Corpus Explorer

**Files:**
- Modify: `yf/web/src/corpus/CorpusExplorer.tsx`
- Modify: `yf/web/src/styles.css`
- Modify: `yf/web/src/App.test.tsx`

**Step 1: Write failing UI tests**

For task `6669`, require visible text equivalent to:

```text
Assumption-backed solver projection
6 parts use recorded flip_x = 1
Recorded transform and no-flip ablation available
```

For blocked tasks, require precise diagnostics and reason codes. Task `25801` must remain disabled
with its non-`s1` reason. Ensure labels distinguish source-observed constraints, derived projection,
and assumed semantics.

**Step 2: Run RED**

```bash
cd yf/web
npm test -- --run src/App.test.tsx
```

**Step 3: Implement accessible diagnostics**

Render server-owned counts/options in the task inspector, keep raw source geometry unchanged, and
update table support wording only from capability state. Use semantic notices and no color-only
meaning.

**Step 4: Run GREEN and commit**

```bash
cd yf/web
npm test -- --run src/App.test.tsx
npm run typecheck
cd ../..
git add yf/web/src/corpus/CorpusExplorer.tsx yf/web/src/styles.css yf/web/src/App.test.tsx
git commit -m "feat: explain flip projection eligibility"
```

### Task 10: Add projection modes and matched ablations to Nest Lab

**Files:**
- Modify: `yf/web/src/nest/NestLab.tsx`
- Modify: `yf/web/src/nest/RunComparison.tsx`
- Modify: `yf/web/src/styles.css`
- Modify: `yf/web/src/App.test.tsx`

**Step 1: Write failing interaction tests**

Cover:

- default `source_as_recorded` mode and exact two-code acknowledgement on task `6669`;
- a separate acknowledgement for the derived no-flip intervention;
- single-arm submission with mode and codes;
- matched action submitting one shared seed/budget and receiving two linked jobs;
- visible independent arm statuses and cancellation controls;
- progressive source-arm candidates while the ablation runs concurrently;
- automatic history refresh after both arms finish;
- auto-selected read-only pair comparison;
- projection mode/hash, assumptions, interventions, and matched-pair identity in history and
  comparison;
- neutral wording without winner, better, improvement, optimality, or savings claims;
- existing task `13958` single-run flow unchanged and no ablation offered.

**Step 2: Run RED**

```bash
cd yf/web
npm test -- --run src/App.test.tsx
```

**Step 3: Implement projection controls**

Store selected mode and acknowledgements per task. Add `Run recorded projection`, `Run no-flip
ablation`, and `Run matched experiment` actions only when authoritative options permit them. For a
matched response, stream the recorded arm through the existing candidate reducer, poll the sibling
job with bounded cleanup, show both status records, and refresh history when both are terminal.
Persist no mutable comparison result; reconstruct pair identity from completed request evidence.

**Step 4: Extend read-only comparison**

Add rows for projection mode, projection SHA-256, transform convention, interventions, pair ID, and
pair arm. Relations remain only `Same`/`Different`.

**Step 5: Run GREEN, accessibility unit coverage, and commit**

```bash
cd yf/web
npm test -- --run src/App.test.tsx
npm run typecheck
cd ../..
git add yf/web/src/nest/NestLab.tsx yf/web/src/nest/RunComparison.tsx yf/web/src/styles.css yf/web/src/App.test.tsx
git commit -m "feat: run matched flip ablations"
```

### Task 11: Prove the real task-6669 browser-to-Spyrrow path

**Files:**
- Modify: `yf/web/e2e/workbench.spec.ts`
- Modify: `yf/web/playwright.config.ts` only if runtime setup requires it

**Step 1: Write the E2E scenario**

Against the real Postgres-backed API:

1. Load all 256 tasks and open task `6669`.
2. Assert six flip-bearing parts and two projection choices.
3. Navigate to Nest Lab and acknowledge both recorded assumptions plus the ablation intervention.
4. Launch the matched experiment with one seed and equal two-second budgets.
5. Assert two distinct linked jobs and actual progressive candidates.
6. Wait for both immutable archives.
7. Browse and render candidates from each archive.
8. Compare the pair and assert projection modes/hashes/intervention evidence.
9. Confirm task `25801` is still blocked and task `13958` retains its original acknowledgement.
10. Run Axe with no violations.

**Step 2: Recreate the derived Postgres read model**

Validate the new catalog first. Then remove only the dedicated local YieldForge Postgres derived
volume/schema after resolving its exact Docker target, recreate it, and import the new committed
catalog. Report that the derived read model was replaced and is reproducible from committed bytes.

**Step 3: Start real services and run E2E**

Start FastAPI on loopback with the Postgres URL and Vite on loopback. Run:

```bash
cd yf/web
YIELDFORGE_E2E_REAL_API=true YIELDFORGE_E2E_EXTERNAL=true npm run e2e
```

Expected: all desktop real-API scenarios pass; only the intentional duplicate mobile mutation
scenario may skip.

**Step 4: Commit**

```bash
git add yf/web/e2e/workbench.spec.ts yf/web/playwright.config.ts
git commit -m "test: prove real flip projection experiments"
```

### Task 12: Update bounded documentation and finish verification

**Files:**
- Modify: `README.md`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Development/Getting Started.md`
- Modify: `Docs/Development/Research Workbench.md`
- Modify: `Docs/Development/Spyrrow Adapter.md`
- Modify: `Docs/Research/Lectra Representative Slice.md`

**Step 1: Update documentation**

Record the exact committed artifact bytes/hashes and observed requalification counts. Document both
projection modes, exact assumption/intervention codes, direct API examples, matched equal-budget
semantics, task `6669` proof, task `25801` exclusion, Postgres schema recreation, and replay/archive
provenance. Keep the claim ceiling explicit.

**Step 2: Run focused and full Python verification**

```bash
cd yf
uv run --all-groups pytest
uv run ruff check .
uv run ruff format --check .
```

Record exact pass/skip counts and explain every skip.

**Step 3: Run frontend verification**

```bash
cd yf/web
npm test
npm run typecheck
npm run build
YIELDFORGE_E2E_REAL_API=true YIELDFORGE_E2E_EXTERNAL=true npm run e2e
```

Record exact unit/E2E counts and intentional skips.

**Step 4: Review repository integrity**

```bash
cd ../..
git diff --check
git status --short --branch
git diff --stat HEAD~12..HEAD
git log --oneline --decorate -15
```

Inspect every changed file, all generated artifact identities, and current runtime health. Do not
claim completion unless task `6669` reached Spyrrow through the real browser and both matched
archives rendered successfully.

**Step 5: Commit documentation and any verification-only fixes**

```bash
git add README.md Docs yf
git commit -m "docs: document flip projection experiments"
```

**Step 6: Push only after every gate is green**

```bash
git push origin main
```

Report committed work, exact verification results, environment-dependent skips, remaining product
limitations, and the recommended next M0 experiment task. Preserve that this is a local research
workbench over a bounded 256-task selection; no residual geometry, remnant reuse, chronology,
simulator, oracle, savings, production result, or commercial proof exists.
