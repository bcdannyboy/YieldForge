# M8 Post-Gate-3 Algorithmic Traversal Redesign

**Status:** Phase A root-level quotient census complete — no-go; batched/compiled spike next

## Objective

Reduce the unchanged two-probe portable-fact pipeline from `686.535011` charged wall-seconds to at
most `30.261404` seconds without weakening exact M7 semantics, independent checking, mutation
coverage, source identity, concurrency limits, or the sealed evaluation boundary.

The required reduction is `22.686819x`. Removing only the repeat-generation arm is insufficient:
first generation plus checking still costs `475.393859` seconds and projects `78.547885` held-out
days, requiring `15.709577x` more improvement.

## Bound evidence

- Portable result: `yfm8gate3-ea8a12969396172d7dbc4774`.
- Complete decision: `yfm8g3decision-c13ec320e9fcd02873bf649c`.
- Checked roots: 887/887; exact fallback: 0.
- Charged phases: first generation `210.306280` s, repeat generation `211.049242` s, checker
  `265.087579` s.
- Hard-arm checker inclusives: common verification `67.099414` s, authority reconstruction
  `45.674499` s, action traversal `92.026047` s. These measurements overlap and are diagnostic, not
  additive budgets.
- Four-way audit: 12/12 actions, zero mismatch.
- Executed mutations: 16/16 rejected.
- Evaluation: unopened.

## Design constraint

This cannot be solved by a single serialization or scheduling tweak. The full target is smaller
than any one current major phase, and the first-generation-plus-checker sensitivity also fails by
more than fifteenfold. The next design must reduce repeated per-action work algorithmically across
both producer and checker.

Candidate mechanisms, in priority order:

1. Exact state-quotient discovery: group action roots only when their complete future-replay state,
   inventory lineage, economic state, suffix, and runtime commitments are identical; replay one
   representative and persist a membership proof for every root.
2. Batched algebraic action traversal: replace repeated object-by-object state-chain and cost
   reconciliation with a content-addressed columnar transition over shared suffix evidence, while
   retaining one exact root commitment and terminal-cost check per action.
3. Compiled pure verification kernel: move only the measured canonical state-chain/cost operations
   into a deterministic compiled boundary after the first two mechanisms establish that semantic
   sharing alone cannot reach the target.
4. GPU work only if profiling identifies a large, uniform numeric kernel with transfer costs below
   the phase budget. GPU use is not itself a gate or a substitute for the algorithmic proof.

## Execution phases and go/no-go gates

### Phase A — exact structural census

On the same two frozen calibration probes, count exact equivalence classes for every proposed
state-quotient key and report class-size distributions separately for `no_signal` and
`regime_shift`. Do not alter execution or inspect evaluation.

Go only if the key is proven sufficient to reconstruct every existing root commitment and either:

- compresses the hard arm enough to support the required end-to-end target; or
- identifies a narrower repeated kernel whose measured elimination could support that target.

Otherwise stop the quotient arm and proceed directly to a compiled/batched checker design. Do not
relax the key to manufacture compression.

**Executed result — 2026-08-27:** The persisted complete 887-root manifest was censused without
opening evaluation. In `no_signal`, all 428 roots had unique initial-state, future-input,
terminal-state, and combined replay commitments. In `regime_shift`, all 459 roots were likewise
unique for every one of those keys. Duplicate groups, duplicate roots, and excess roots were zero;
maximum class size was one in both regimes. Whole-root exact state quotienting is therefore a
no-go. Do not weaken the state key. Proceed to a measured batched-suboperation or compiled pure
verification spike.

### Phase B — one-arm executable spike

Implement the smallest unchecked producer plus fresh checker path for `regime_shift`. Require:

- 459/459 root and final-cost equality with the current portable result;
- zero hidden exact fallback;
- byte-identical repeated output;
- source-attested fresh workers and the unchanged eight-slot ceiling; and
- targeted mutation rejection for every new aggregation/membership binding.

The spike is a no-go if projected full-pipeline wall, using measured unchanged companion work,
cannot plausibly fit `30.261404` seconds.

### Phase C — unchanged two-probe performance rerun

Rerun both portable probes twice, charge every phase, and repeat the complete 12-action proof and
mutation gates. The only passing performance condition is full charged wall at or below
`30.261404` seconds. Phase allocations may move; the aggregate boundary may not.

### Phase D — paired timing identity

Only after Phase C clears the absolute projection, capture portable and reference timing under one
bound machine/load/runtime identity. Until then, reference-equivalent speedup remains diagnostic.

### Phase E — six-cell authorization decision

Authorize the official six-cell calibration only if exactness, mutations, absolute projection, and
paired performance all pass their frozen contracts. Evaluation remains sealed until a later
explicit gate authorizes it.

## Claim ceiling

All work in this plan is calibration-software evidence. It cannot establish oracle advantage,
material savings, global optimality, physical feasibility, production readiness, buyer demand, or
commercial value.
