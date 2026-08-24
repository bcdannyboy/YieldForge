# M8 Rollout Oracle Preparation

**Prepared:** 2026-08-24

**Entry state:** M7 baseline calibrated, frozen, evaluated, and twice reproduced

**Decision:** Build the canonical M8 arm as an exact full-remaining-horizon one-step rollout over
every common current action. Use only exact persistent caching and byte-equivalent state coalescing
to reduce runtime.

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

M7 recorded 550,542 action alternatives across 864 evaluation events. Full rollout compounds that
work across remaining suffixes, so M8 begins with:

1. a reusable arbitrary-state M7 transition/continuation seam whose output is regression-locked to
   the published M7 identities;
2. a strict local persistent cache for standard profiles, prepared footprints, fit searches,
   exact transitions, and exact continuation results;
3. checkpoints after each scored action and completed event;
4. exact coalescing only for byte-equivalent state, ledger, time, suffix, and engine identities; and
5. a six-stream calibration-only runtime pilot before an evaluation execution manifest is frozen.

The canonical calculation has no semantic timeout or truncation. Operational concurrency begins at
no more than eight local workers and must not affect content identity. If the calibration pilot does
not establish a practical exact completion path, M8 remains in preparation while caching,
incremental replay, or distribution is improved.

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

### M8.1 — Add persistent exact caches

Create strict content-addressed runtime storage with conflict detection, process-safe access,
checkpoint/resume, and cold/warm/disabled differential tests.

### M8.2 — Implement oracle contracts and isolation

Bind the M0/M6/M7 identities, full and known-only visibility modes, complete horizon, fallback tie
rule, per-action scores, failures, runtime manifest, and claim ceiling.

### M8.3 — Prove the rollout kernel on small cases

Pass hand-computed delayed-reuse, no-signal, terminal-accounting, policy-improvement, candidate
parity, information-isolation, state-coalescing, and determinism tests.

### M8.4 — Run the calibration-only runtime pilot

Execute one calibration stream per regime, record exact workload and cache behavior, reproduce its
content, and freeze the evaluation execution manifest only if an exact completion path exists.

### M8.5 — Hold before evaluation

Do not open M8 evaluation until the freeze artifact validates and the M7 regression identity is
reproduced. Full held-out execution and paired summaries are separate implementation-plan tasks.

## Immediate next action

Start TDD Task 1: extract and test the arbitrary-state M7 transition seam without changing any
published M7 artifact identity.
