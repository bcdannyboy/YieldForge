# M8 Rollout Oracle Preparation

**Prepared:** 2026-08-24

**Entry state:** M7 baseline calibrated, frozen, evaluated, and twice reproduced

**Decision:** Build the canonical M8 arm as an exact full-remaining-horizon one-step rollout over
every common current action. Reject naive branch-by-branch replay; compile the common M7 future and
evaluate only exact inventory deltas that can affect later actions.

## Purpose and claim ceiling

M8 measures whether realized future demand changes today's action enough to reduce modeled M0 net
cost against the immutable M7 baseline. It may produce a paired generated-world rollout advantage
and an unknown-future attribution. It cannot establish a mathematical upper bound, global
optimality, physical recovery, market demand, or commercial value.

## Bound entry evidence

- M0 economic rules and reporting thresholds are already frozen.
- M6 contract `yfm6-3eeda3f4feb80813807c501a` and population
  `yftp-49bd7ce5fd34b2779440c52f` provide 12 calibration and 36 evaluation streams.
- M7 problem index `yfm7i-116c24d7fce8ce415d46533e` binds 1,152 instances to 209 reusable
  candidate problems.
- M7 freeze `yfm7freeze-5c13c3fe531828d8cd986c39` pins policy, rates, action/search rules,
  runtime versions, Jagua backend, and exact Shapely fallback.
- M7 evaluation `yfm7eval-f2cb310c4b7e879d119e8f94` supplies the immutable paired baseline
  costs. Its aggregate action volume may size infrastructure, but its per-stream outcomes may not
  tune M8 semantics.

## Approved primary boundary

For each current action, M8 applies the action virtually and sends the entire remaining registered
suffix through frozen M7. It chooses the action with the lowest final terminal net cost, prefers the
actual M7 action on an exact tie, executes that action, and repeats at the next event.

The primary arm has no shortened horizon, candidate pre-ranking, action cap, approximate state
merge, or oracle-only expanded search. Those options remain labeled sensitivities or later M9 work.

## Runtime preparation

M7 recorded 550,542 action alternatives across 864 evaluation events. Naive full rollout projects
roughly 6.3 million continuation events and multi-week local execution, so that implementation is
rejected. M8 begins with:

1. a reusable arbitrary-state M7 transition/continuation seam whose output is regression-locked to
   the published M7 identities;
2. a deliberately slow full-replay reference used only for correctness checks;
3. compiled inventory-independent M7 standard winners and rejection-safe future-fit indexes;
4. sparse exact delta replay that skips passive future intervals and branches only on possible
   action-changing remnant events;
5. a calibration-prefix proof requiring zero mismatch, at least 20x speedup, and a conservative
   held-out projection no greater than seven days; and
6. persistent caching, checkpoints, and a six-stream pilot only after that proof passes.

The canonical calculation has no semantic truncation. Operational concurrency begins at no more
than eight local workers and must not affect content identity. If either runtime gate fails, local
evaluation remains closed while sparse execution is redesigned or a separate distributed-exact
manifest is prepared.

## Information controls

The full arm alone receives the realized suffix. The known-only arm uses the identical action and
scoring kernel but receives only work known before release. M6 has no pre-release `known_at` field,
so its known-only visible suffix is empty. The frozen M7 baseline has no oracle visibility channel.

Acceptance includes a hidden-suffix mutation test: it may change the full-oracle decision but must
not change the baseline or known-only current decision.

## Ordered entry plan

### M8.0 — Extract the exact M7 transition seam

Expose complete current action descriptors, one-action execution, and frozen-policy continuation
from arbitrary state. Preserve the existing fast M7 policy path and published replay identities.

### M8.1 — Build the slow exact reference

Score every current action by direct complete-suffix M7 replay on toys and short calibration
prefixes. This is correctness authority for the sparse implementation, not an evaluation engine.

### M8.2 — Compile sparse future relevance

Compile the M7 standard winner per problem. Add safe material, area, and footprint-bound rejection
certificates and a suffix relevance index. Survivors still require registered exact search.

### M8.3 — Prove sparse exact delta replay

Pass hand-computed cases, then compare every sparse score and transition with the reference on six
registered calibration prefixes. Require zero mismatch, at least 20x speedup, and a seven-day-or-less
held-out projection within a 24-hour proof budget.

### M8.4 — Add caches and run the calibration-only pilot

Only after M8.3 passes, add strict persistent caching and checkpoint/resume. Execute one complete
calibration stream per regime and freeze evaluation only if the seven-day projection survives.

### M8.5 — Hold before evaluation

Do not open M8 evaluation until the freeze artifact validates and the M7 regression identity is
reproduced. Full held-out execution and paired summaries are separate implementation-plan tasks.

## Immediate next action

Start TDD Task 1: extract and test the arbitrary-state M7 transition seam without changing any
published M7 artifact identity. The slow reference and sparse 20x proof come before cache or
complete-stream infrastructure.
