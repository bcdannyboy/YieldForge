---
source: YieldForge Proposal v1
status: source
converted: 2026-08-17
---

> [!note] Source status
> This note is a working Markdown conversion of the original DOCX. The preserved DOCX remains the source artifact.

# Appendix B. Acceptance-Test Matrix

Tests required before a milestone is considered complete.

| Test | Acceptance criterion | Milestone |
| --- | --- | --- |
| Geometry import | Known polygons preserve area, orientation, holes, and coordinates across JSON -> Sparrow -> internal form. | M1 |
| Transform round-trip | Placement rotation/translation reconstructed from solver output matches rendered geometry. | M1 |
| Candidate determinism | Pinned commit, seed, and config reproduce the same canonical candidate archive. | M2 |
| Diversity deduplication | Near-identical residual states collapse; materially different topology remains. | M2 |
| Boolean accounting | Stock = parts + process loss + retained remnants + scrap within tolerance. | M3 |
| Adversarial geometry | Holes, touching boundaries, narrow channels, concavity, and precision fixtures do not corrupt output. | M3 |
| Irregular fit positive | Known future part fits an irregular remnant and returns a valid placement. | M4 |
| Irregular fit negative | Area/bounding-box-compatible but geometrically impossible part is rejected. | M4 |
| Recursive provenance | Remnant cut from remnant retains parent chain and correct accounting. | M4 |
| Replay determinism | Same manifest produces identical events, decisions, inventory, and totals. | M5 |
| Information isolation | Baseline cannot access unreleased future events; oracle can access only through explicit interface. | M5 |
| Baseline sanity | Remnant-first/best-fit policies dominate obviously weak policies on constructed cases. | M6 |
| Rollout sanity | Oracle A selects a known future-beneficial candidate in constructed examples. | M7 |
| No-signal sanity | Oracle advantage approaches zero across sufficiently large independent-demand controls. | M7/M9 |
| Beam exactness | Beam search matches exhaustive optimum on small instances at specified beam width. | M8 |
| Beam degradation | Approximation gap is measured as beam width is reduced. | M8 |
| Terminal invariance | Primary conclusion does not reverse under scrap-only versus zero terminal option credit. | M9/M10 |
| Decision reproducibility | Final tables and figures regenerate from immutable manifests and source hashes. | M10 |
