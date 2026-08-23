# M5 — Deterministic replay

**Status:** Next — M4 passed; replay boundary pending

M5 gives the experiment memory and time. Orders arrive, information is revealed, inventory changes, remnants age, purchases and handling costs accrue, and the terminal rule closes the horizon.

> **Question:** Can we reproduce the consequences of a sequence of material decisions accurately and deterministically?

## Acceptance boundary

Identical manifests, component versions, policies, and seeds produce canonically identical event histories and cost totals.

The first implementation slice will freeze event order, inventory state transitions, information
visibility, failure handling, the terminal rule, and canonical history identity around a
hand-computable manifest. It will reuse M4's exact Shapely fit and consumption boundary. A second
collision runtime is optional and must be justified by measured repeated-query performance, not by
the correctness gate.
