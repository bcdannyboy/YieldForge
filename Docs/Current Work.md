# Current Work

## Active milestone

[[M4 - Remnant reuse proof]]

## Current objective

Freeze the bounded M4 exact-fit experiment, then prove on adversarial fixtures that a retained
irregular residual can accept a later compatible source-observed shape while preserving strict
geometry and provenance. The reusable implementation remains in the single `yf/` project.

## Next step

Write the M4 design and implementation plan around exact polygon containment, allowed
source-backed orientation states, material compatibility, clearance, retained-component identity,
and immutable lineage. Begin with hand-verifiable favorable and unfavorable fixtures before any
corpus evaluation. Do not begin chronological simulation, baseline/oracle implementation, oracle
UI, savings, or commercial claims during this milestone.

## Evidence bridge completed

- One persistent `yf/` Python project, managed with `uv` and Python 3.12.
- Spyrrow 0.9.0 mapped behind a YieldForge-owned adapter.
- Lectra release 1.1 pinned, fetched, qualified, and documented in [[Lectra Corpus Audit]].
- A bounded, source-lossless two-task fixture committed and documented in [[Lectra Representative Slice]], plus a deterministic 256-task catalog produced through the same sealed qualifier boundary.
- Ruleset v2 classifies 254 catalog tasks as runnable with exact assumptions and keeps the two tasks containing non-`s1` constraints view-only. Task `13958` still requires the degenerate-`s1` orientation assumption; task `25801` remains blocked because its `c8` semantics are unresolved.
- The two blocked tasks are exactly `4365` and `25801`. A content-addressed pure-geometry protocol rederives their status, all 254 eligible IDs, 185 flip-bearing IDs, the 51/203 calibration/evaluation split, and the 20-task repeatability subset from the committed catalog.
- Uniform binary `s1` flip states are preserved. The source-recorded projection negates local x before rotation for recorded `flip_x = 1`; the separate `force_flip_x_zero` mode is an intervention-backed ablation, never a source rewrite. Mixed-within-row and nonbinary flip states remain fail-closed.
- Progressive feasible solutions normalized, fixed-sheet filtered, deduplicated, archived, and exposed through the adapter callback boundary.
- A local FastAPI service owns corpus queries, single and matched solver-job lifecycle, SSE replay, completed candidate archives, verified completed-run history with exact projection/settings/archive identities, derived placement geometry, and deterministic order-book publication. A matched submission starts source-recorded and no-flip arms with the same solver configuration and records their shared pair identity; Nest Lab's later comparison remains read-only and nonevaluative.
- A dedicated local Postgres read model serves the validated committed catalog with signed pagination, server-owned filtering, lazy detail loading, strict startup identity checks, and no database authority over the evidence artifact.
- Corpus Explorer, Nest Lab, and Order Book Lab are implemented under `yf/web/`; the real browser suite exercises multiple corpus pages, creates a task `6669` matched projection pair through the frontend-to-API-to-Spyrrow boundary, observes progressive candidates, verifies both immutable archives, compares their exact projection/settings/archive identities, reopens and renders both arms, and verifies deterministic order-book generation.
- The Postgres-backed visible corpus is exactly the 256-task committed selection; the no-database fallback remains the two original tasks. Order-book chronology/economics are generated and material is assumed; the UI labels those fields and treats full manifests as analysis-only.

## Experiment status

The M0 economic constitution is frozen and executable: net cost, information sets, event order, candidate parity, remnant eligibility, failures, reporting, and economic thresholds are explicit. The pure-geometry population, split, arms, calibration ladder, candidate identity, 0.5% primary outcome, supporting strata, and pass/redesign/stop rules are also pre-registered.

M0 is Passed. The registered calibration completed all 612 cells with 100% archive validity and no
retry, selecting 10 seconds per seed. M2 is Passed. Its registered confirmation completed all 812
cells with valid archives and found 203/203 qualifying tasks; the canonical result is
`yfgfr-47d42952e0003154baceee02`.

M3 is also Passed. The residual-blind input `yfgi-2fe5b848ea643d282c284f90` selected one pair for
each of the 203 qualifying tasks. The canonical result `yfgr-0ac2c37f0938d9d399e7a076` evaluated
all 203 pairs with no failures and zero maximum reconciliation delta. Exact residual geometry
differed for 202 pairs (99.5074%); the median symmetric difference was 0.1343% of sheet area, P95
was 3.5361%, and the maximum was 22.1654%. None of the 203 pairs changed classification under the
permissive, primary, or conservative M0 rule.

This proves exact residual accounting and widespread exact residual differences for the selected
pairs. It does not prove a later shape can use the difference. No remnant-reuse result,
chronological simulator, future-aware policy, economic benchmark, solver-optimality result, or
validated savings claim exists.

See [[Research Workbench]] for local setup, runtime storage, and verification commands.
