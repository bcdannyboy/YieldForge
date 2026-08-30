# M11 Gate 3 Bounded-Catalog Integrity Repair

## Decision

Gate 3 will measure the product-realistic algorithm over the already registered,
deterministic placement search (at most 256 placement candidates per geometry
query). It will require complete enumeration of every action discovered by that
registered search, but it will not claim exhaustive enumeration of every
possible geometric translation.

This is a one-time test-harness repair made before any central confirmation
outcome was executed or inspected. The original failed control remains part of
the audit trail.

## Why the repair is necessary

The frozen Gate 3 contract said `complete_no_truncation`, while the inherited M7
product search deliberately caps placement generation at 256. A realistic
Lectra remnant generated roughly 16,300 possible placements, so the exact-audit
control stopped before testing YieldForge's economic hypothesis. The stop was
fail-closed and exposed an internal contract mismatch; it was not evidence of a
wrong optimization decision or insufficient savings.

## Alternatives considered

1. **Recommended: exact over the registered bounded product search.** Keep the
   256-placement algorithm identical across baseline, full-future, and
   known-only arms. Preserve telemetry showing whether the wider geometric
   space was truncated. This directly tests the implementation that a product
   could run and avoids changing strict M9 defaults.
2. **Enumerate all observed placements.** Raise the cap above 16,300 and retain
   the original formal claim. This changes the product algorithm, multiplies
   two-ply branching and runtime, and can still encounter a larger future case.
3. **Treat the invalid control as an abandon verdict.** This is fast but answers
   whether the proof harness was internally consistent, not whether YieldForge
   creates useful economic value.

## Architecture

- Strict M9 search remains fail-closed by default.
- Search functions gain an explicit catalog-completeness policy. Only the new
  Gate 3 mode may accept geometry-budget truncation while retaining all actions
  discovered by the registered bounded search.
- Gate 3 contracts, runtime roots, traces, and exact-audit receipts bind the
  bounded policy and its observed truncation telemetry. No result may call the
  bounded catalog geometrically exhaustive.
- Gate 3 controls and B/F/K execution use the same bounded policy. Exact audits
  solve the complete decision tree induced by that bounded catalog.
- Parent Gate 1 and Gate 2 artifacts remain unchanged. The Gate 3 configuration
  is regenerated and re-frozen because its semantic contract changes.

## Data flow and failure handling

The adapter continues to project authenticated source events and candidates.
At each decision, geometry search produces the deterministic first 256 placement
candidates. M9 enumerates every resulting standard/remnant action and records
geometry truncation separately. Any empty action set, incomplete action
materialization, inconsistent B/F/K policy, malformed roots, or exact-tree
incompleteness still fails closed.

## Test strategy

- First prove strict M9 still rejects truncated geometry catalogs.
- Add a failing test showing Gate 3 bounded mode can score the same catalog and
  exposes nonzero geometry-truncation telemetry.
- Add Gate 3 contract and artifact tests proving the bounded claim is frozen and
  cannot be mislabeled as exhaustive.
- Re-run focused M9, Gate 3 backend, confirmation, controls, runner, and contract
  suites before executing the official test.

