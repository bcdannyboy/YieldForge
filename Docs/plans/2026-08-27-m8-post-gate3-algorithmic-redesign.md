# M8 Post-Gate-3 Algorithmic Traversal Redesign

**Status:** Phase B bounded reuse complete — correctness pass, performance no-go; C0
frontier-columnar influence kernel authorized next

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

1. Batched columnar event traversal: evaluate the hard arm's complete candidate/rejection matrix
   and shared suffix transition in one runtime-, event-, candidate-, geometry-, and action-bound
   kernel while retaining every exact root commitment.
2. Compiled deterministic CPU verification: move the measured branch/state-chain loops across both
   producer and independent checker into a native boundary, keeping the current Python path as the
   reference oracle and fail-closed fallback.
3. If that spike succeeds, extend the same boundary to authority reconstruction, catalog
   construction, and common verification. Eliminating traversal alone cannot meet the aggregate
   target.
4. GPU work only if a later profile exposes a substantially larger uniform numeric kernel. The
   current 459-by-459 exact comparison matrix is dominated by Python object and hashing overhead,
   so native CPU/SIMD is the lower-risk next boundary.

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

**Executed result — 2026-08-27:** The hardened, strict, source-attested one-arm v3 profile published
`yfm8profile-ffbf978a466f6e98768a7556` with content hash
`sha256:ffbf978a466f6e98768a7556d223e61bbbca85737b04b845a6da32709ac85e87`.
It retained the official `regime_shift` identity: 459 roots, 2,297 fixed nodes, 43,520,933
semantic bytes, the exact bundle and decision hashes, byte-identical repeated generation, and zero
fallback. Three distinct fresh workers were source-attested in a fresh bytecode-cache scope; the
six-cell calibration remained unauthorized and evaluation remained unopened.

The bounded implementation builds standard-sheet actions once per catalog and retains full
prepared layouts behind process-local bindings that commit runtime, event, candidate order, fit
configuration, and geometry. Prepared consumers use a proof-owned semantic runtime snapshot;
retained actions, loaded worker code, source mirrors, output publication, and mutation cleanup are
fail-closed. Jagua results and translation audits were not cached away. The externally recomputed
source-tree hash is
`sha256:63825133dee1927d753d2bbcd74a49bee241c347eeb5c0b594cbff73e2929eac`; controller and all workers
match runtime hash
`sha256:bce0d552132a3de7ca12eb98599800e34a0d78cf8e0bc5440efb7faa28a45508`. Focused correctness,
integrity, mutation, publisher-race, strict-load, and source/runtime identity checks pass.

The exact one-arm controller pipeline took `592.221873` seconds: first generation `158.964633`,
repeat generation `156.515162`, and checker `276.558218`. This is not a full two-probe Gate-3 rerun;
it is a Phase-B lower bound. It alone is `19.570205x` the entire `30.261404`-second full-pipeline
budget, so Phase B is a definitive performance no-go. Diagnostic hard-arm worker time fell from
`683.808438` to `587.867289` seconds (`14.030413%`), but checker time did not improve. The dominant
remaining inclusives are producer traversal `76.083734`, catalog enumeration `48.990843`, checker
action traversal `95.247443`, common verification `66.238186`, context preparation `59.613163`, and
authority reconstruction `45.497482` seconds.

Whole-root quotienting and bounded object reuse are now closed as sufficient strategies. Proceed to
the compiled columnar event kernel. Do not infer a new full-pipeline speedup or held-out-day
projection from this one-arm run.

### Phase C0 — frontier-columnar influence kernel

Replace the hard arm's repeated 459-by-459 rejection-certificate expansion with one producer-side
batch over 459 branch queries and the exact 13-member Pareto frontier. The numeric boundary accepts
validated Python float/boolean records, evaluates local NumPy `float64`/boolean arrays, and returns
exact Python booleans plus opaque row identities. It owns no runtime objects, geometry, facts,
capabilities, or hashes. NumPy and the explicit kernel identity must be added to a new C0 runtime
contract; historical Phase-B runtime evidence remains unchanged.

The fresh checker does not trust the batch result or call NumPy. For compact all-impossible groups,
it reconstructs the prepared frontier, validates the complete 459-member scalar-reference set once,
and proves each row through the existing scalar frontier predicate. Every survivor or unsupported
row follows the unchanged exact path. A numeric mismatch is an integrity failure, not fallback.

The current producer/checker action traversal totals `171.331178` seconds. Go to a wider compiled
boundary only if the C0 hard-arm run preserves exact bundle/decision identity, byte-identical repeat
generation, complete membership, source/runtime attestation, zero fallback, and reduces that
combined traversal to at most `8.566559` seconds (at least `20x`). Passing C0 does not pass the
`30.261404`-second full-pipeline gate.

**State:** Authorized next; implementation not yet executed.

### Phase C — unchanged two-probe performance rerun

Rerun both portable probes twice, charge every phase, and repeat the complete 12-action proof and
mutation gates. The only passing performance condition is full charged wall at or below
`30.261404` seconds. Phase allocations may move; the aggregate boundary may not.

**State:** Unexecuted and unauthorized. Phase C0 must first clear its bounded go-to-expand gate.

### Phase D — paired timing identity

Only after Phase C clears the absolute projection, capture portable and reference timing under one
bound machine/load/runtime identity. Until then, reference-equivalent speedup remains diagnostic.

**State:** Unexecuted and unauthorized.

### Phase E — six-cell authorization decision

Authorize the official six-cell calibration only if exactness, mutations, absolute projection, and
paired performance all pass their frozen contracts. Evaluation remains sealed until a later
explicit gate authorizes it.

**State:** Unexecuted and unauthorized.

## Claim ceiling

All work in this plan is calibration-software evidence. It cannot establish oracle advantage,
material savings, global optimality, physical feasibility, production readiness, buyer demand, or
commercial value.
