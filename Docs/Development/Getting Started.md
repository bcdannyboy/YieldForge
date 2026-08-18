# Getting started

YieldForge has one implementation tree: `yf/`. Every milestone extends this package rather than creating milestone-numbered copies.

## Requirements

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- A platform supported by the pinned Spyrrow wheel

## Install and verify

From `yf/`:

```bash
uv sync
uv run pytest
uv run ruff check .
```

`uv.lock` is committed. Do not manually install a different Spyrrow version into the project environment.

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
- `benchmarks/static/` — small authored static fixtures.
- `tests/` — contract, adapter, native integration, archive, and CLI tests.

See [[Spyrrow Adapter]] and [[../Milestones/M0 - Experiment contract|M0 — Experiment contract]].
