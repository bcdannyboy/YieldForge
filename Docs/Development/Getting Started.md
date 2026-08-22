# Getting started

YieldForge has one implementation tree: `yf/`. Every milestone extends this package rather than creating milestone-numbered copies.

## Requirements

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- A platform supported by the pinned Spyrrow wheel
- Node.js and npm
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

The mutating desktop scenario creates a real task `13958` job, observes progressive candidates, verifies the completed immutable archive and derived SVG geometry, then exercises deterministic order-book generation. It never enables task `25801`.

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
- `src/yieldforge/datasets/` — pinned two-task corpus contracts, projection boundary, and query service.
- `src/yieldforge/workbench/` — FastAPI contracts, solver-job supervision, SSE, candidate/archive views, and order-book service.
- `src/yieldforge/order_books/` — deterministic hybrid order-book contracts, generator, and immutable archive.
- `benchmarks/static/` — small authored static fixtures.
- `web/` — React/Vite Corpus Explorer, Nest Lab, Order Book Lab, unit tests, and Playwright E2E.
- `tests/` — contract, adapter, native integration, archive, API, and CLI tests.

Generated runtime state is server-owned and ignored under `yf/var/workbench/`. Committed corpus and order-book fixtures remain immutable evidence; local jobs and generated books are not silently promoted into the committed dataset.

See [[Research Workbench]] for exact source fetch, locked qualification, fixed-slice export, direct solver-job/order-book API, runtime, and verification commands. See also [[Spyrrow Adapter]] and [[../Milestones/M0 - Experiment contract|M0 — Experiment contract]].
