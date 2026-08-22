# Getting started

YieldForge has one implementation tree: `yf/`. Every milestone extends this package rather than creating milestone-numbered copies.

## Requirements

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- A platform supported by the pinned Spyrrow wheel
- Node.js and npm
- Docker with Compose for the expanded 256-task corpus
- Chromium installed through the repository's Playwright script

## Install and verify

From `yf/`:

```bash
uv sync --locked --all-groups
uv run --all-groups pytest
uv run ruff check .
uv run ruff format --check .

cd web
npm ci
npm run playwright:install
npm test
npm run typecheck
npm run build
```

`uv.lock` is committed. Do not manually install a different Spyrrow version into the project environment.

## Prepare the expanded corpus

The committed catalog is the evidence artifact; Postgres is only its local indexed read model. Start the dedicated loopback-only service from `yf/`, then import the catalog using the validated audit already reproduced under the ignored `var/data/` tree:

```bash
docker compose up -d postgres
export YIELDFORGE_DATABASE_URL=postgresql://yieldforge:yieldforge-local@127.0.0.1:55433/yieldforge
uv run yieldforge datasets catalog-import \
  --catalog datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --audit-report var/data/reports/lectra-7030786-v1.1/lectra-audit.json \
  --database-url "$YIELDFORGE_DATABASE_URL"
```

The import is transactional and idempotent only for the identical pinned catalog. A clean clone must first reproduce the audit using the sealed acquisition steps in [[Research Workbench]]; the raw pickle-bearing files remain ignored and are never opened by the normal host process. If `YIELDFORGE_DATABASE_URL` is absent, the API deliberately serves only the original two-task fixture.

## Validate the frozen experiment contract

From `yf/`, validate the approved M0 constitution and calibration-pending geometry protocol against
the committed catalog:

```bash
uv run yieldforge experiments validate \
  --m0 experiments/m0-contract-v1.json \
  --geometry experiments/pure-geometry-calibration-v1.json \
  --catalog datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json \
  --catalog-manifest datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json
```

The current output must identify M0 `yfm0-29b7efe8ac2a0a9995c4f907`, geometry protocol
`yfgp-49906e93ed9ff0446705247b`, the catalog SHA-256, 51 calibration tasks, 203 evaluation tasks,
and `confirmation=disabled`. Validation is a pre-registration check; it is not a calibration or
geometry result.

## Run the local workbench

Start the API from `yf/`:

```bash
uv run uvicorn yieldforge.workbench.app:create_default_app \
  --factory --host 127.0.0.1 --port 8000
```

In another terminal, start Vite from `yf/web/`:

```bash
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to `http://127.0.0.1:8000` by default. If that port is occupied, start FastAPI on another port and pass its origin to Vite, for example:

```bash
YIELDFORGE_API_URL=http://127.0.0.1:8765 npm run dev
```

With the real API and frontend running, execute the browser suite from `yf/web/`:

```bash
YIELDFORGE_E2E_REAL_API=true \
YIELDFORGE_E2E_EXTERNAL=true \
npm run e2e
```

The mutating desktop scenario loads the full committed catalog, confirms task `6669` has six recorded flip-bearing parts and both projection options, acknowledges the two exact assumptions plus the no-flip intervention, and submits one matched experiment. It observes a progressive source-arm candidate, verifies that both jobs traverse the real API/Spyrrow boundary and finish with immutable archives, renders both archives, and checks their shared pair identity and distinct projection identities in the neutral read-only comparison. It then exercises deterministic order-book generation. The suite separately proves task `25801` remains blocked and task `13958` retains its exact one-assumption gate. The corresponding mobile mutation is deliberately skipped so the same local state is not mutated twice.

## Generate a smoke archive

```bash
uv run yieldforge candidates generate \
  --input benchmarks/static/m0-smoke.json \
  --output var/archives/m0-smoke-seed-0 \
  --seed 0 --seconds 1 --workers 1
```

An archive contains:

- `manifest.json` — problem, exact solver identity, run settings, count, and content hash;
- `candidates.jsonl` — one normalized feasible candidate per line.

Archives are write-once: rerunning against an existing output directory fails. Generated archives under `yf/var/archives/` are ignored by Git; preserve intentionally important runs in durable experiment storage when we define that policy.

## Current package map

- `src/yieldforge/domain.py` — strict persisted contracts.
- `src/yieldforge/spyrrow_adapter.py` — native solver boundary.
- `src/yieldforge/archive.py` — deterministic immutable archive writer.
- `src/yieldforge/cli.py` — command-line workflow.
- `src/yieldforge/datasets/` — pinned corpus contracts, sealed catalog/import boundary, Postgres read model, projection boundary, and two-task fallback.
- `src/yieldforge/workbench/` — FastAPI contracts, solver-job supervision, SSE, verified completed-run/candidate archive views, and order-book service.
- `src/yieldforge/order_books/` — deterministic hybrid order-book contracts, generator, and immutable archive.
- `src/yieldforge/experiments/` — strict M0/geometry contracts, canonical loading, catalog-bound population validation, and deterministic split derivation.
- `experiments/` — committed content-addressed M0 and pure-geometry protocol artifacts.
- `benchmarks/static/` — small authored static fixtures.
- `web/` — React/Vite Corpus Explorer, Nest Lab, Order Book Lab, unit tests, and Playwright E2E.
- `tests/` — contract, adapter, native integration, archive, API, and CLI tests.

Generated runtime state is server-owned and ignored under `yf/var/workbench/`. Nest Lab history reads those local immutable job archives; it does not promote them into the committed corpus. Committed corpus and order-book fixtures remain immutable evidence, and generated books are likewise not silently promoted.

See [[Research Workbench]] for exact source fetch, locked qualification, fixed-slice export, direct solver-job/order-book API, runtime, and verification commands. See also [[Spyrrow Adapter]] and [[../Milestones/M0 - Experiment contract|M0 — Experiment contract]].
