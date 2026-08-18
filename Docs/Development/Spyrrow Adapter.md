# Spyrrow adapter

The adapter makes Spyrrow a candidate generator behind YieldForge-owned contracts. Downstream components consume `CandidateBatch`; they do not depend directly on Spyrrow objects.

## Current boundary

1. Validate a fixed-height strip problem and a physical maximum sheet length.
2. Map parts, demand, and allowed orientations into Spyrrow.
3. Run with an explicit seed, duration, worker count, and separation setting;
4. drain progressive solver reports while the native solve runs;
5. keep only exploration-feasible, compression-feasible, and final solutions;
6. reject solutions longer than the physical sheet;
7. normalize placements and deduplicate identical layouts;
8. return solver-independent contracts that can be written to an immutable archive.

## Reproducibility identity

The environment pins `spyrrow==0.9.0` in `uv.lock`. Every archive also records the Sparrow source revision declared by that Spyrrow release, the full run configuration, the canonical problem, and a SHA-256 hash of the batch.

This is necessary provenance, not yet a proof that independent runs produce M2's required stable population. M2 still needs a multi-seed protocol and explicit near-tie and material-diversity rules.

## Intentional limits

- Input parts are simple polygons; holes are not supported at this boundary.
- Spyrrow solves strip packing. The adapter enforces the current finite sheet length afterward.
- A candidate currently contains placements, width, density, solver provenance, and report type. Exact residual geometry begins in M3.
- Shapely is pinned for the coming geometry work but is not used to validate residual truth yet.

See [[../Research/Technical Sources|Technical Sources]] and [[../Milestones/M2 - Static data and Sparrow|M2 — Static data and Sparrow]].
