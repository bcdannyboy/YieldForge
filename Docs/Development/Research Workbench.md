# Research workbench

YieldForge's workbench is a local research instrument under `yf/`; it is not a hosted or production application. The Python service owns source evidence, solver eligibility, process supervision, immutable archives, and order-book generation. The React client renders those browser-safe contracts without reinterpreting source constraints.

## Current visible catalog

- Task `13958` is runnable only after acknowledging `interpret_s1_degenerate_entries_as_allowed_rotations` exactly.
- Task `25801` is source-lossless and view-only because its non-`s1` `c8` semantics remain unresolved.
- With the documented Postgres read model enabled, Corpus Explorer exposes exactly 256 fully exported tasks: 69 assumption-backed and 187 view-only. This deterministic selection is not a prevalence sample of the full Lectra release.
- Without `YIELDFORGE_DATABASE_URL`, the API deliberately retains the original two-task committed-fixture fallback.

Corpus geometry and task composition are source-observed. Candidate placement geometry is derived from a verified immutable archive. Order-book chronology and economics are generated, while material assignment is assumed. Full order-book manifests reveal future events and generator-only regime labels, so the UI marks them analysis-only rather than baseline-facing.

## Views

- **Corpus Explorer** pages through the committed catalog, filters on server-owned facets, and shows source rows, constraint types, support state, exclusion reasons, and source polygon geometry.
- **Nest Lab** enforces the server-owned solve gate, streams durable job events, shows progressive candidates, and reconciles complete terminal archives. Its completed-run history is newest-first, exposes exact recorded solver settings and the verified batch SHA-256 without internal paths, and can reopen any listed immutable archive to render derived SVG placements. A read-only comparison uses the open archive as Run A and one chosen completed archive as Run B; it shows exact recorded values with neutral Same/Different relations only. Archived candidate count is inventory, not quality.
- **Order Book Lab** lists and opens the three committed deterministic fixtures, displays chronology/diagnostics/provenance, links events to corpus and eligible nest views, and publishes deterministic local books without overwriting an existing identity.

## Reproduce the source evidence

The four raw Lectra files are ignored and must never be opened by the normal host process. From `yf/`, fetch their manifest-pinned bytes, build the locked qualifier, and publish a new audit into an empty ignored directory:

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run yieldforge datasets fetch \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --output var/data/raw/lectra-7030786-v1.1

docker build --pull \
  --file tools/lectra/Dockerfile \
  --tag yieldforge-lectra-qualifier:7030786-v1.1 \
  .

mkdir -p var/data/reports/lectra-7030786-v1.1-repro
uv run python tools/lectra/run_qualifier.py \
  --image yieldforge-lectra-qualifier:7030786-v1.1 \
  --input "$PWD/var/data/raw/lectra-7030786-v1.1" \
  --output "$PWD/var/data/reports/lectra-7030786-v1.1-repro" \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --timeout-seconds 900
```

The publisher is write-once: choose another new, empty output directory if that example already contains `lectra-audit.json`. Export the fixed two-task slice through the same sealed boundary:

```bash
mkdir -p var/data/slices/lectra-7030786-v1.1-repro
uv run python tools/lectra/run_qualifier.py \
  --mode slice \
  --image yieldforge-lectra-qualifier:7030786-v1.1 \
  --input "$PWD/var/data/raw/lectra-7030786-v1.1" \
  --output "$PWD/var/data/slices/lectra-7030786-v1.1-repro" \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --audit-report "$PWD/var/data/reports/lectra-7030786-v1.1-repro/lectra-audit.json" \
  --timeout-seconds 900
```

The generated `var/data/raw/`, `var/data/reports/`, and `var/data/slices/` trees stay ignored. The canonical committed slice and hand-inspectable order-book fixtures live under `datasets/fixtures/` and are immutable inputs to tests and the local API. See [`tools/lectra/README.md`](../../yf/tools/lectra/README.md) for the qualifier's security boundary, resource limits, expected artifacts, and trusted-fixture smoke.

The committed 256-task catalog was produced with the qualifier's separate `catalog` mode and 64 MiB publication ceiling. To reproduce it, choose another new empty directory and bind the same audit bytes:

```bash
mkdir -p var/data/catalogs/lectra-7030786-v1.1-repro
uv run python tools/lectra/run_qualifier.py \
  --mode catalog \
  --image yieldforge-lectra-qualifier:7030786-v1.1 \
  --input "$PWD/var/data/raw/lectra-7030786-v1.1" \
  --output "$PWD/var/data/catalogs/lectra-7030786-v1.1-repro" \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --audit-report "$PWD/var/data/reports/lectra-7030786-v1.1-repro/lectra-audit.json" \
  --timeout-seconds 900
```

The successful output is only `lectra-catalog.json`; it must match the logical and byte identities in the committed `datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json` before it can replace any evidence artifact.

## Local runtime

The default factory stores mutable state under ignored `yf/var/workbench/` directories for jobs, candidate archives, and generated order books. Set `YIELDFORGE_WORKBENCH_ROOT` before starting FastAPI to use another local runtime root. These files are execution evidence, not committed source data.

Start the dedicated Postgres service and populate its read model from `yf/`. This example uses the canonical audit already present in the ignored reproduction directory:

```bash
docker compose up -d postgres
export YIELDFORGE_DATABASE_URL=postgresql://yieldforge:yieldforge-local@127.0.0.1:55433/yieldforge
uv run yieldforge datasets catalog-import \
  --catalog datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --audit-report var/data/reports/lectra-7030786-v1.1/lectra-audit.json \
  --database-url "$YIELDFORGE_DATABASE_URL"
```

Then install and start the API with that environment still set:

```bash
uv sync --locked --all-groups
uv run uvicorn yieldforge.workbench.app:create_default_app \
  --factory --host 127.0.0.1 --port 8000
```

Install and start the client from `yf/web/`:

```bash
npm ci
npm run playwright:install
npm run dev
```

Then open `http://127.0.0.1:5173`.

## Direct API smoke

With FastAPI running, these calls exercise the same server-owned gates as the browser. Task `25801` remains inspectable but cannot be submitted. Task `13958` accepts exactly the one listed assumption:

```bash
API_ORIGIN=http://127.0.0.1:8000
curl -fsS "$API_ORIGIN/api/corpus/summary"
curl -fsS "$API_ORIGIN/api/tasks/25801"
curl -fsS -X POST "$API_ORIGIN/api/solver-jobs" \
  -H 'Content-Type: application/json' \
  --data '{"schema_version":"yieldforge.api-solver-job-request.v1","tasks_index":13958,"acknowledged_assumption_codes":["interpret_s1_degenerate_entries_as_allowed_rotations"],"seed":23,"total_computation_time":3,"early_termination":false,"min_items_separation":null,"max_runtime_seconds":5.0}'
```

Use the returned `job_id` to observe the durable stream and, after completion, browse its immutable archive and derived placement geometry:

```bash
curl -N "$API_ORIGIN/api/solver-jobs/JOB_ID/events"
curl -fsS "$API_ORIGIN/api/solver-jobs/JOB_ID"
curl -fsS "$API_ORIGIN/api/solver-jobs/JOB_ID/candidates?limit=25"
curl -fsS "$API_ORIGIN/api/solver-jobs/JOB_ID/candidates/CANDIDATE_ID/geometry"
curl -fsS "$API_ORIGIN/api/tasks/13958/completed-runs?limit=20"
```

The completed-run response contains only task/source-bound completed jobs whose archives can be reopened and verified. It is newest-first and publishes recorded solver settings plus the archive batch SHA-256; it never publishes the server archive path or worker PID. Nest Lab performs pairwise inspection entirely over this already validated response, with no second archive fetch, mutation, score, winner, or economic calculation.

List the committed and locally generated immutable order books, then publish an idempotent deterministic request:

```bash
curl -fsS "$API_ORIGIN/api/order-books?limit=20"
curl -fsS -X POST "$API_ORIGIN/api/order-books" \
  -H 'Content-Type: application/json' \
  --data '{"regime":"exact_recurrence","seed":424242,"event_count":3,"starts_at":"2026-02-03T04:05:00Z","interval_minutes":37}'
curl -fsS "$API_ORIGIN/api/order-books/ORDER_BOOK_ID"
```

Replace `JOB_ID`, `CANDIDATE_ID`, and `ORDER_BOOK_ID` with identifiers returned by the preceding calls. Repeating the identical order-book request returns the same identity and content hash; it does not overwrite another archive.

## Verification

With FastAPI and Vite running:

```bash
cd yf
uv run --all-groups pytest
uv run ruff check .
uv run ruff format --check .

cd web
npm test
npm run typecheck
npm run build
YIELDFORGE_E2E_REAL_API=true YIELDFORGE_E2E_EXTERNAL=true npm run e2e

cd ../..
git diff --check
git status --short --branch
```

The real E2E is a completion gate only when it reaches the actual FastAPI service and Spyrrow worker. A skipped real-API suite or a mocked frontend test does not establish that path.

## Claim ceiling

This catalog workbench proves local inspection of a bounded 256-task selection, bounded assumption-backed job execution, transport contracts, task-bound completed-run rediscovery, descriptive pairwise inspection, immutable candidate/archive browsing, and deterministic provenance-labeled order-book generation. Pairwise inspection shows recorded settings, source/archive identities, timestamps, assumptions, and candidate inventory; it does not compare runs scientifically, measure candidate quality, or prove solver optimality. The workbench does not establish corpus representativeness or source-unit meaning, and it does not provide residual geometry, remnant reuse, inventory conservation, a chronological simulator, a strong baseline, an oracle, material savings, production fitness, or buyer value.
