# M7 — Strong baseline

**Status:** Later

M7 builds the opponent YieldForge must honestly beat. The baseline uses current released work, current inventory, remnant age and regularity, storage costs, and already-known orders without seeing unavailable future information.

> **Question:** Can future information outperform a competent system using everything legitimately known at the time?

## Acceptance boundary

Baseline variants are selected only on calibration streams. The winning deterministic policy is frozen before evaluation and receives the same feasible actions and compute accounting as the oracle.
