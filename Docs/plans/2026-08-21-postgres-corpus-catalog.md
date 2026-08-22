# Postgres-backed Corpus Catalog Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Export, validate, import, and serve a deterministic 256-task source-lossless Lectra catalog through Postgres, then expose real pagination and later-task geometry in Corpus Explorer without weakening solver or evidence gates.

**Architecture:** A new catalog mode in the existing sealed qualifier produces a bounded canonical `NormalizedSlice` with 256 fully qualified display tasks. A trusted importer validates that artifact, constructs a hash-checked Postgres read model, and a new corpus service implements the existing DTO/API surface with SQL pagination and task-detail retrieval. The React client consumes the unchanged API with append-only cursor pagination and state separation between the list and selected task.

**Tech Stack:** Python 3.12, Pydantic 2, pandas/Shapely only inside the qualifier, psycopg 3, PostgreSQL 17, FastAPI, React 19, TypeScript, Vitest, Playwright, Docker Compose.

---

### Task 1: Define deterministic 256-task catalog selection

**Files:**
- Modify: `yf/src/yieldforge/datasets/lectra_slice.py`
- Modify: `yf/tests/datasets/test_lectra_slice.py`

**Step 1: Write failing selection tests**

Add tests that build more than 256 trusted frame tasks and assert:

```python
selection = select_catalog_task_ids(frames, target_count=256)
assert len(selection.task_ids) == 256
assert selection.task_ids == select_catalog_task_ids(reordered_frames, target_count=256).task_ids
assert 13958 in selection.task_ids
assert 25801 in selection.task_ids
```

Add rejection tests for insufficient display-safe tasks, invalid shape references, malformed opaque
values, and missing continuity tasks. Assert strict `s1` tasks get the existing assumption code and
other tasks get explicit view-only reason codes.

**Step 2: Verify RED**

Run:

```bash
cd yf
uv run pytest tests/datasets/test_lectra_slice.py -q
```

Expected: FAIL because catalog selection/export functions do not exist.

**Step 3: Implement the minimal selector/exporter**

Add immutable selection records and these public functions:

```python
def select_catalog_task_ids(
    frames: Mapping[str, Any], *, target_count: int = 256
) -> CatalogTaskSelection: ...

def export_catalog_slice(
    frames: Mapping[str, Any],
    *,
    manifest: DatasetSourceManifest,
    source_manifest_sha256: str,
    audit_report_sha256: str,
    target_count: int = 256,
) -> NormalizedSlice: ...
```

Share the existing source validators and extraction functions. Scan deterministic ranked candidates
until exactly `target_count` tasks pass full display validation. Classify strict `s1` projection
tasks as assumption-backed and all others as view-only. Require continuity members and use a new
`lectra-catalog-rules.v1` ruleset.

**Step 4: Verify GREEN and refactor**

Run the focused test again, then:

```bash
uv run pytest tests/datasets/test_lectra_slice.py tests/datasets/test_normalized_slice.py -q
uv run ruff check src/yieldforge/datasets/lectra_slice.py tests/datasets/test_lectra_slice.py
```

Expected: PASS with no lint findings.

### Task 2: Add a separately bounded qualifier catalog mode

**Files:**
- Modify: `yf/tools/lectra/qualify.py`
- Modify: `yf/tools/lectra/run_qualifier.py`
- Modify: `yf/tests/tools/test_lectra_qualifier_boundary.py`
- Modify: `yf/tools/lectra/README.md`

**Step 1: Write failing boundary tests**

Add tests proving `catalog` mode:

- requires exact manifest/audit hashes;
- calls only `export_catalog_slice`;
- permits at most `64 * 1024 * 1024` bytes while audit/slice remain at 4 MiB;
- validates and canonicalizes through `NormalizedSlice`;
- publishes exactly `lectra-catalog.json` with no-clobber semantics;
- fails on overflow, wrong evidence, extra output, or cleanup uncertainty.

**Step 2: Verify RED**

```bash
cd yf
uv run pytest tests/tools/test_lectra_qualifier_boundary.py -q
```

Expected: FAIL because `catalog` is not an accepted mode.

**Step 3: Implement the catalog mode**

Introduce mode-specific artifact names and limits:

```python
CATALOG_NAME = "lectra-catalog.json"
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_CATALOG_BYTES = 64 * 1024 * 1024
type QualifierMode = Literal["audit", "slice", "catalog"]
```

Preserve the existing Docker command and cleanup boundary. Select the capture/validation/publication
limit from the explicit mode; never lift the audit or representative-slice ceiling.

**Step 4: Verify GREEN**

Run the focused boundary tests and Ruff on both qualifier modules.

### Task 3: Generate and pin the real 256-task catalog

**Files:**
- Create: `yf/datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json`
- Create conditionally: `yf/datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json`
- Modify: `.gitignore`
- Modify: `Docs/Research/Lectra Representative Slice.md`

**Step 1: Build the locked qualifier image**

```bash
cd yf
docker build --pull --file tools/lectra/Dockerfile \
  --tag yieldforge-lectra-qualifier:7030786-v1.1 .
```

**Step 2: Run catalog export to a new ignored directory**

```bash
mkdir -p var/data/catalogs/lectra-7030786-v1.1
uv run python tools/lectra/run_qualifier.py \
  --mode catalog \
  --image yieldforge-lectra-qualifier:7030786-v1.1 \
  --input "$PWD/var/data/raw/lectra-7030786-v1.1" \
  --output "$PWD/var/data/catalogs/lectra-7030786-v1.1" \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --audit-report "$PWD/var/data/reports/lectra-7030786-v1.1/lectra-audit.json" \
  --timeout-seconds 900
```

Expected: one canonical `lectra-catalog.json`, exactly 256 tasks, including 13958 and 25801.

**Step 3: Inspect size and content**

Validate the artifact with the strict loader, report exact row/capability counts, and inspect a sample
of runnable and view-only tasks. If it is under a reasonable Git artifact threshold, commit the
canonical payload; otherwise keep it ignored and commit a manifest containing its exact SHA-256,
byte size, counts, ruleset, source-manifest hash, and audit hash.

**Step 4: Reproduce the hash**

Export into a second empty directory and assert byte-for-byte identity. Do not proceed if it drifts.

### Task 4: Add Postgres runtime, schema, and trusted importer

**Files:**
- Modify: `yf/pyproject.toml`
- Modify: `yf/uv.lock`
- Create: `yf/compose.yaml`
- Create: `yf/src/yieldforge/datasets/postgres_catalog.py`
- Create: `yf/tests/datasets/test_postgres_catalog.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/test_cli.py`

**Step 1: Add dependency and write failing real-Postgres tests**

Add `psycopg[binary]` to the locked runtime. Start the dedicated Compose service and write tests for:

```python
manifest = import_catalog(database_url, catalog_path, evidence_paths)
assert manifest.task_count == 256
assert import_catalog(database_url, catalog_path, evidence_paths) == manifest
```

Also assert refusal of a different catalog identity, wrong row hashes, partial schema, duplicate
source keys, summary/detail mismatch, and ineligible problem payloads.

**Step 2: Verify RED**

Run only the Postgres tests against the dedicated test database. Expected: FAIL because the module,
schema, and CLI command do not exist.

**Step 3: Implement schema and importer**

Create a versioned schema with `yieldforge_catalog` and `yieldforge_catalog_task`. Use a single
transaction and parameterized statements. Store canonical JSON strings or JSONB plus per-record
hashes; do not serialize Python reprs. Refuse mutation of a populated different identity.

Add CLI:

```bash
yieldforge datasets catalog-import \
  --catalog PATH \
  --manifest PATH \
  --audit-report PATH \
  --database-url URL
```

**Step 4: Verify GREEN**

Run Postgres tests, CLI tests, Ruff, and format checks for the new module.

### Task 5: Serve the Postgres catalog through the existing API

**Files:**
- Modify: `yf/src/yieldforge/datasets/corpus.py`
- Create: `yf/src/yieldforge/datasets/postgres_corpus.py`
- Modify: `yf/src/yieldforge/workbench/app.py`
- Modify: `yf/tests/workbench/test_corpus.py`
- Modify: `yf/tests/workbench/test_api.py`

**Step 1: Write failing service/API tests**

Cover summary count 256, stable first and second 50-row pages, status/constraint/part filters,
catalog-bound signed cursors, stale/forged/cross-filter cursors, later-task detail geometry, exact
task `25801` rejection, and task `13958` projection with only the exact assumption code.

**Step 2: Verify RED**

Run the focused workbench tests against Postgres. Expected: FAIL because the service does not exist.

**Step 3: Implement a shared corpus-service protocol and Postgres service**

Keep DTOs and success response schemas unchanged. Query task facets with fixed SQL, stop at
`limit + 1`, validate every JSON DTO read from the database, and bind cursors to the configured
catalog hash. Validate stored projection payloads before returning `StripPackingProblem`.

`create_default_app()` uses Postgres only when a nonempty `YIELDFORGE_DATABASE_URL` is configured;
connection or identity failure is fatal. Otherwise it retains the committed two-task fallback.

**Step 4: Verify GREEN**

Run focused corpus/API/job tests and Ruff.

### Task 6: Implement Corpus Explorer pagination and support semantics

**Files:**
- Modify: `yf/web/src/contracts.ts`
- Modify: `yf/web/src/contracts.test.ts`
- Modify: `yf/web/src/App.tsx`
- Modify: `yf/web/src/App.test.tsx`
- Modify: `yf/web/src/api.ts`
- Modify: `yf/web/src/corpus/CorpusExplorer.tsx`
- Modify: `yf/web/src/nest/NestLab.tsx`
- Modify: `yf/web/src/styles.css`
- Modify: `yf/web/src/test/fixtures.ts`

**Step 1: Write failing frontend tests**

Add tests proving:

- page two appends using the exact prior cursor and filters without duplicates;
- selecting a row does not refetch page one or discard appended rows;
- applying/clearing filters resets pages and cursors;
- late list/detail responses cannot overwrite current state;
- a deep-linked later task loads geometry independently;
- support labels are typed and distinct;
- directly supported tasks show no assumed acknowledgement UI;
- maximum-parts filtering reaches the API.

**Step 2: Verify RED**

```bash
cd yf/web
npm test
```

Expected: new tests FAIL for missing pagination and state separation.

**Step 3: Implement minimal UI changes**

Keep applied filters, accumulated page items, next cursor, selected task ID, and detail request state
separate. Pass `CorpusSummary` from `App`, derive filter options, add accessible result/loading text,
and render “Load next 50” only when a cursor exists.

**Step 4: Verify GREEN**

Run frontend tests, TypeScript, and production build.

### Task 7: Extend real browser coverage and documentation

**Files:**
- Modify: `yf/web/e2e/workbench.spec.ts`
- Modify: `README.md`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Development/Getting Started.md`
- Modify: `Docs/Development/Research Workbench.md`
- Modify: `yf/tools/lectra/README.md`

**Step 1: Write failing E2E assertions**

Require the real Postgres-backed API to report 256 tasks, load a second page, filter on a constraint
outside the original two-task slice, open and render a later task’s geometry, and reload its deep
link. Retain the exact 25801 block, 13958 acknowledgement, real Spyrrow/candidate archive, and
order-book provenance flow.

**Step 2: Update bounded documentation**

Document Docker Compose startup, catalog generation/import, database URL, fallback behavior,
verification, exact 256-task selection, and claim ceiling. Remove statements that say Corpus
Explorer exposes only two tasks while preserving that order books remain tied to the original
committed two-task slice.

**Step 3: Run real local integration**

Start Postgres, import the pinned catalog, restart FastAPI with `YIELDFORGE_DATABASE_URL`, run Vite,
and exercise the real E2E suite with one mutation worker.

### Task 8: Final verification, review, commit, and push

Run fresh commands and read their complete output:

```bash
cd yf
uv run --all-groups pytest
uv run ruff check .
uv run ruff format --check .

cd web
npm test
npm run typecheck
npm run build
YIELDFORGE_E2E_REAL_API=true YIELDFORGE_E2E_EXTERNAL=true npm run e2e -- --workers=1

cd ../..
git diff --check
git status --short --branch
git diff --stat
git diff
```

Review every generated artifact, staged path, schema claim, and ignored runtime file. Ask an
independent reviewer to inspect the integrated diff, then independently verify its findings. Commit
the coherent catalog slice to `main` and push only after every required check passes.
