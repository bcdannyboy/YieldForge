# YieldForge repository closeout design

Date: 2026-09-01

## Purpose

Close the active YieldForge research phase without erasing either the work or the negative result. The repository should explain the hypothesis, the sequence of tests, what was learned, why the result was not dismissed as a sample-size problem, and what genuinely new evidence would justify reopening the idea.

The closeout also prepares the tracked tree as an owner-approved public research archive while
keeping the GitHub visibility change as a separate explicit action.

## Chosen approach

Use an outcome-first research-archive README rather than a chronological lab notebook or a minimal landing page. The README will:

1. mark the project as paused and not authorized for productization;
2. explain the future-aware remnant hypothesis in semantic terms;
3. explain why perfect future information was used as an intentionally favorable upper-bound test;
4. summarize M0 through M11 as escalating falsification stages;
5. distinguish the M10 roadmap stop from the later M11 quantitative result;
6. report the full-future and known-only results without calling forecast headroom a deployable saving;
7. explain why the stop decision rests on magnitude, prevalence, cross-segment replication, and deployability rather than sample size alone;
8. state bounded conclusions and implications with necessary caveats; and
9. define a concrete reopen bar that does not begin by rebuilding Sparrow.

Detailed milestone records and immutable evidence remain in the `Docs/` vault. Historical notes are not rewritten to make the path look cleaner than it was.

## Evidence and claim boundaries

M10 result `yfm10-931b3a95fe84cd96cff799f2` stopped productization and further virtual-oracle investment but did not compute a numeric economic band. The later M11 economic-resolution lineage produced the quantitative disposition.

The canonical M11 result compared a separately calibrated baseline, a full-future arm, and a known-only arm across 40 held-out semi-synthetic streams in two segments. Full-future savings were `0%` on LOCo and `0.536368330506%` mean on Lectra; the Lectra median was `0%`, only 3 of 20 streams were positive, and the 95% paired-bootstrap interval was `[0%, 1.098250947619%]`. Known-only savings were exactly `0%` in both segments.

The README may infer that exact residual geometry can differ and occasionally create reusable remnants, that this geometric mechanism did not become reliable modeled economic value under the tested algorithms, and that the small Lectra-only full-future signal suggests segment dependence or narrow forecast headroom. It may not infer factory prevalence, physical recoverability, realized ROI, buyer demand, or impossibility for every future algorithm.

The sample-size explanation must remain bounded: more streams from the same modeled distribution could refine precision, but the observed result failed on effect size, median, positive-stream prevalence, cross-segment replication, and deployability. Even the Lectra full-future interval's upper endpoint was below the frozen `2.5%` opportunity threshold, and the deployable known-only arm was zero on every held-out stream.

## Public-release boundary

The closeout branch may be merged to the current private `main`. The tracked tree and history are
within the owner-approved publication boundary, while changing repository visibility remains a
separate external action.

- Add complete CC BY 4.0 attribution for the Lectra-derived data and state the transformations.
- Record the LOCo citation, official source, pinned archive checksum, and YieldForge transformations
  without claiming that YieldForge created the upstream observations.
- Record the owner's approval of the proposal, six diagrams, tracked source-derived material,
  YieldForge name, and existing Git author metadata.
- Clarify that the local research workbench source is publishable but its services are not hardened
  for network exposure.
- Track compact M11 manifests that contain derived receipts and aggregate results without
  recoverable source geometry or demand. Preserve source-rich raw evidence outside Git.
- Exclude ignored `yf/var`, the external archive, raw/resumable packets, environments, dependencies,
  caches, browser output, and credentials from the publication boundary.
- Remove absolute local paths from the current tree where they are operational residue, while preserving the semantic content of historical plans.

## Packaging and artifact policy

The Python source-distribution boundary must explicitly include only intended package material and exclude local `var/`, build output, caches, browser dependencies, test output, and native build artifacts. A regression test will fail before this configuration exists and will verify the corrected archive contents afterward.

Canonical, bounded result artifacts may remain tracked when tests and reports authenticate them.
Raw resumable execution state and source-rich local evidence remain external or explicitly ignored.
The artifact policy must explain this distinction and disclose when a clean public clone cannot
reproduce a source-bound validation step without the separately preserved packet.

## Local preservation and Git integration

All committed milestone branches form one linear history ending at M11. The closeout branch starts at that tip; after verification, `main` can fast-forward to the closeout tip without a merge conflict.

Before removing historical worktrees:

- preserve the unique M8 uncommitted patch;
- preserve the obsolete lowercase Obsidian scaffold as an archive rather than merging a duplicate `docs/` tree;
- preserve the complete M11 local evidence packet, including the two oversized raw artifacts, outside the repository; and
- write and verify a manifest of the preserved material.

Only after preservation and verification may merged worktrees and branch refs be removed. Generated caches and build outputs may be deleted when they are reproducible and are not evidence.

## Verification

Verification covers:

- Python unit, evidence, and integration tests, with source-bound Postgres tests run against the pinned local audit input and tight multiprocessing timeout tests run separately from the long suite;
- Ruff lint and format checks;
- web tests, TypeScript checks, and production build;
- a source-distribution content and size check;
- Markdown link resolution and public-path/secret scans;
- authentication of the three selected M11 manifests;
- archive hashes and preserved-worktree manifests;
- branch ancestry, remote freshness, and a clean `main` after integration; and
- the same relevant checks again on the merged `main` tip.

## Non-goals

This closeout does not restart algorithm development, rebuild Sparrow, run a new economic
experiment, claim commercial proof, change repository visibility, expose the local workbench to
untrusted networks, or rewrite Git history.
