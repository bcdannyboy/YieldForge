# Current Work

## Active milestone

[[M0 - Experiment contract]]

## Current objective

Use the local research workbench to inspect the committed 256-task Lectra catalog, exercise bounded assumption-backed Spyrrow projections, and inspect deterministic generated order books without crossing the experiment's evidence boundary. The reusable implementation remains in the single `yf/` project.

## Next step

Keep M0's outcome and comparison contract ahead of new experiment capabilities. The next bounded local-product task is a read-only two-run comparison over the immutable history already exposed in Nest Lab: recorded settings and descriptive candidate/archive fields only, with no ranking, solver-optimality inference, or economic preference claim. Do not begin arbitrary sheet-and-parts input, residual geometry, remnant reuse, simulation, or oracle UI yet.

## Evidence bridge completed

- One persistent `yf/` Python project, managed with `uv` and Python 3.12.
- Spyrrow 0.9.0 mapped behind a YieldForge-owned adapter.
- Lectra release 1.1 pinned, fetched, qualified, and documented in [[Lectra Corpus Audit]].
- A bounded, source-lossless two-task fixture committed and documented in [[Lectra Representative Slice]], plus a deterministic 256-task catalog produced through the same sealed qualifier boundary.
- Task `13958` projected through a fail-closed solver boundary under one explicit `s1` orientation assumption; task `25801` remains view-only because its `c8` semantics are unresolved.
- Progressive feasible solutions normalized, fixed-sheet filtered, deduplicated, archived, and exposed through the adapter callback boundary.
- A local FastAPI service owns corpus queries, solver-job lifecycle, SSE replay, completed candidate archives, verified completed-run history with exact settings and archive hashes, derived placement geometry, and deterministic order-book publication.
- A dedicated local Postgres read model serves the validated committed catalog with signed pagination, server-owned filtering, lazy detail loading, strict startup identity checks, and no database authority over the evidence artifact.
- Corpus Explorer, Nest Lab, and Order Book Lab are implemented under `yf/web/`; the real browser suite exercises multiple corpus pages, creates two distinct frontend-to-API-to-Spyrrow runs, selects the newer completed archive, reopens the older archive, renders its geometry, and verifies deterministic order-book generation.
- The Postgres-backed visible corpus is exactly the 256-task committed selection; the no-database fallback remains the two original tasks. Order-book chronology/economics are generated and material is assumed; the UI labels those fields and treats full manifests as analysis-only.

## Experiment status

This local workbench slice still does not pass M0 or M2. The primary outcome, cost accounting, oracle comparison, remnant rules, and success threshold remain unresolved. No residual-geometry proof, remnant reuse, chronological simulator, future-aware policy, economic benchmark, or validated savings claim exists yet.

See [[Research Workbench]] for local setup, runtime storage, and verification commands.
