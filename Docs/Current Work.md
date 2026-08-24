# Current Work

## Active milestone

[[M8 - Rollout oracle]]

## Current objective

Implement the prepared exact full-horizon rollout oracle against the immutable M7 baseline freeze.
Preserve complete action parity, baseline information masking, exact accounting, and paired evidence
boundaries while making the computation practical through semantics-preserving persistent caches.

## Next step

Execute Task 1 of [[plans/2026-08-24-m8-rollout-oracle-implementation]]: expose complete M7 action
descriptors, one-action execution, and frozen-policy continuation from arbitrary state while
regression-locking all published M7 content identities. Then add the strict persistent cache before
the rollout kernel. Do not open M8 evaluation until the hand-computed gates and six-stream
calibration-only runtime pilot support a frozen exact execution manifest.

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
- M5 deterministic replay now carries exact recursive remnants, inventory lineage, event staging,
  and generated costs through time; canonical result `yfrpr-3e53070d65447bef0e7bcc24` fulfills
  2/2 toy orders and reconciles the cost path `102.0 → 107.6 → 104.9`.
- M6 contract `yfm6-3eeda3f4feb80813807c501a` binds the full 254-runnable-task catalog pool,
  six regimes, eight common seeds, and immutable 12/36 calibration/evaluation partitions.
- M6 population `yftp-49bd7ce5fd34b2779440c52f` contains 48/48 reproducible streams, 1,152
  events, and 37,247 source-part references. All streams lower into 1,024 exact compatible batches.
- M6 lowering pilot `yfm6p-c5852afe0dd52f41689f3ed6` validated 4,769 part instances with
  14,307 exact Shapely geometry queries and zero invalid geometry. It contained no repeated
  fit/search calls, so the optional Jagua spike remained gated on measured M7 replay workload.
- M7 problem index `yfm7i-116c24d7fce8ce415d46533e` binds 1,152 instances to 209 reusable
  problems. The first-seed feasibility slice reverified 51 problems and 204 ordinary M2 archives.
- M7 differential `yfm7d-055c3aa7a09c85fbee2f1ca2` validated exact Rust/Python translation
  parity and zero guarded Jagua/Shapely result mismatches across 174,626 translations. Jagua is now
  the registered rejection prefilter; Shapely remains authoritative for accepted witnesses and
  residual/accounting geometry.
- M7 feasibility `yfm7f-7edef2fa8719168941e431d2` completed 144/144 events, evaluated 89,936
  actions, and selected 144 standard-sheet openings with zero remnant retrievals. Fit search used
  3,225.582819 of 3,864.895624 replay seconds and projects 644.149271 calibration minutes.
- M7 calibration `yfm7cal-172006fc66891ceee0c41d49` executed all five policies on all 12
  calibration streams. Four exact policies tied numerically; `age_regularity` won only the frozen
  lexical tie-break. The amended runtime freeze is `yfm7freeze-5c13c3fe531828d8cd986c39`.
- M7 evaluation `yfm7eval-f2cb310c4b7e879d119e8f94` reverified 198 problems and 792 archives,
  then replayed all 36 streams and 864 events twice with identical content. It opened 826 sheets,
  retrieved 38 remnants, and recorded mean net cost 29,733.304499. One Jagua-unrepresentable guard
  used the registered unfiltered Shapely fallback; policy and exact replay semantics were unchanged.
- M8 preparation freezes the complete-remaining-horizon rollout design, exact M7 fallback tie rule,
  full-versus-known-only visibility boundary, semantics-preserving cache rules, acceptance gates,
  calibration-only runtime pilot, and held-out evaluation hold point. No M8 oracle execution or
  savings result exists yet.

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

M4 is Passed. The input `yfri-26460ffca19eebfc9e479d01` froze 406 exact M3 remnants, 6,607
future source-shape roles, and 1,331,906 eligible generated-order pairs. The canonical result
`yfrr-b8b1578fc5e0225f00c4386e` found the first exact witness after 123 pair attempts. The part
from task `2531` fits a task `147` remnant, creates one reconciled child remnant, and avoids one
full-sheet opening in the declared one-order toy state.

This proves exact remnant reuse is possible in the modeled M4 one-part geometry. M7 has now passed:
its calibrated and frozen baseline reproduced all 36 evaluation streams and selected 38 remnant
retrievals. M5 proves deterministic mechanism replay, and M6 proves controlled temporal
construction, partition integrity, regeneration, and lossless lowering. No observed chronology,
future-aware policy, oracle comparison, paired advantage estimate, general reuse-frequency estimate,
economic verdict, solver-optimality result, physical-recovery result, or validated savings claim
exists.

See [[Research Workbench]] for local setup, runtime storage, and verification commands.
