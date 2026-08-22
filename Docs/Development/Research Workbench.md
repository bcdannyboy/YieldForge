# Research workbench

YieldForge's workbench is a local research instrument under `yf/`; it is not a hosted or production application. The Python service owns source evidence, solver eligibility, process supervision, immutable archives, and order-book generation. The React client renders those browser-safe contracts without reinterpreting source constraints.

## Current visible slice

- Task `13958` is runnable only after acknowledging `interpret_s1_degenerate_entries_as_allowed_rotations` exactly.
- Task `25801` is source-lossless and view-only because its non-`s1` `c8` semantics remain unresolved.
- No other corpus tasks are visible in this committed slice.

Corpus geometry and task composition are source-observed. Candidate placement geometry is derived from a verified immutable archive. Order-book chronology and economics are generated, while material assignment is assumed. Full order-book manifests reveal future events and generator-only regime labels, so the UI marks them analysis-only rather than baseline-facing.

## Views

- **Corpus Explorer** lists the committed tasks, source rows, constraint types, support state, exclusion reasons, and source polygon geometry.
- **Nest Lab** enforces the server-owned solve gate, streams durable job events, shows progressive candidates, reconciles the complete terminal archive, renders derived SVG placements, and rediscovers the latest completed archive for a task.
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

## Local runtime

The default factory stores mutable state under ignored `yf/var/workbench/` directories for jobs, candidate archives, and generated order books. Set `YIELDFORGE_WORKBENCH_ROOT` before starting FastAPI to use another local runtime root. These files are execution evidence, not committed source data.

Install and start the API from `yf/`:

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
```

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

This slice proves local inspection, bounded job execution, transport contracts, immutable candidate/archive browsing, and deterministic provenance-labeled order-book generation. It does not provide residual geometry, remnant reuse, inventory conservation, a chronological simulator, a strong baseline, an oracle, material savings, production fitness, or buyer value.
