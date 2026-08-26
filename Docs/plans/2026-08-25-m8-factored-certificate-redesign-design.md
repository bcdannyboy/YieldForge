# M8 Factored Certificate Redesign

**Status:** Approved 2026-08-25

## Purpose

Redesign the first M8 calibration gate so its exact certificate method can satisfy the frozen
20x sampled-speedup and seven-day held-out projection thresholds without opening evaluation or
weakening independent verification.

The redesign replaces repeated branch-by-branch proof reconstruction with a content-addressed proof
DAG. It factors common M7 transitions, candidate-set rejection facts, and branch-influence facts into
shared lemmas that are verified once. A mathematical Pareto frontier compresses the conservative
area/width/height no-fit test. Exact M7 search remains the mandatory fallback whenever the frontier
cannot prove rejection.

This is a software exactness and runtime design. It is not an oracle-advantage result, savings
evidence, physical validation, or commercial evidence.

## Observed boundary

Canonical artifact `yfm8proof-b296ba919c07d55ece14c6db` completed the first go/no-go with:

- 3,469/3,469 valid action proofs;
- zero full-checker failures;
- zero sampled-checker failures;
- zero mismatches across 12 independently replayed brute-reference actions;
- sampled certificate-plus-checker work of `10354.963529` process-seconds;
- sampled reference work of `11634.541856` process-seconds;
- sampled speedup of `1.123571x` against a required `20x`;
- certificate pipeline wall time of `3024.244298` seconds;
- a held-out projection of `127.766536` days against a seven-day limit;
- observed witness classes `no_fit` and `state_rejoin`; and
- missing required witness classes `exact_transition` and `policy_dominated`.

The independently bounded reference redesign succeeded. The remaining no-go is certificate
architecture and calibration coverage, not reference scheduling or an observed semantic mismatch.

The timing shape localizes the primary problem. The 12-action sampled generator consumed about 92%
of the process work of the 3,469-action generator. The sampled checker consumed about 94% of the
full checker work. Fixed per-regime common-transition, exact-search, and geometry preparation work
therefore dominates marginal action scoring.

## Preserved gates

The redesign does not alter these registered decisions:

1. sampled certificate-plus-checker speedup must be at least `20x` over matched exact reference;
2. held-out execution must project to no more than seven calendar days;
3. full and sampled checkers must report zero failures;
4. sampled certificate scores must equal full certificate scores and independent reference scores;
5. all required witness classifications, registered action kinds, horizons, and regimes must be
   covered;
6. the M7 baseline freeze, M0 accounting, action parity, future-information isolation, and exact
   fallback semantics remain unchanged; and
7. evaluation remains sealed until the calibration gate and full-horizon calibration pilot pass.

The current arithmetic implies two concrete limits:

- sampled certificate-plus-checker work must fall to at most `581.727093` process-seconds;
- under the existing projection formula, full certificate pipeline wall time must fall to at most
  `165.690569` seconds.

An abbreviated engineering checkpoint targets `25x` and five projected days to leave margin. The
official gate remains exactly `20x` and seven days.

## Considered approaches

### Memoize the current generator and checker

Retaining prepared layouts and fit-search results would reduce repeated setup. It is useful as a
supporting optimization, but the current checker still regenerates expensive common transitions and
passivity conclusions. Shared mutable results would also blur checker independence. Memoization
alone is not the primary design.

### Add CPUs or GPU execution

The canonical runner already observed six useful concurrent processes inside an eight-worker
envelope. Adding two processes cannot supply the required improvement, and earlier profiling placed
the slow work in authoritative exact geometry/search rather than an active GPU-compatible collision
prefilter. Hardware scaling is not the primary design.

### Factor a proof DAG and compress no-fit evidence

Shared, content-addressed lemmas remove repeated common work. A Pareto-minimal set of necessary fit
requirements replaces candidate-by-candidate scalar rejection without changing its logic. A
checker can verify those lemmas algebraically and invoke exact search only for survivors. This is the
selected design.

## Architecture

### 1. Verified candidate scalar facts

Candidate verification already reconstructs and validates every exact layout footprint before M8
execution. The redesigned boundary retains the rejection-only measurements produced by that work
instead of discarding them and rebuilding them in each generator/checker process.

One portable candidate fact binds:

- problem ID and SHA-256;
- candidate-set ID and SHA-256;
- candidate ID;
- layout area;
- layout width and height;
- exact layout bounds;
- fit configuration identity; and
- the source archive commitments that were already verified.

Facts are immutable, strictly validated, canonically ordered, and content-addressed. They contain no
future-aware choice, branch state, policy outcome, or oracle score.

### 2. Pareto-minimal rejection frontier

For candidates with the same material requirement, define the necessary-fit vector:

`R(candidate) = (area, width, height)`

Candidate `a` dominates candidate `b` for rejection purposes when every component of `R(a)` is less
than or equal to the corresponding component of `R(b)`. If the easier candidate `a` fails a remnant
on area, width, or height, the harder candidate `b` fails on the same component.

The frontier retains every undominated vector and records, for each removed candidate, a retained
candidate that dominates it. Equal vectors are deduplicated by canonical candidate ID while keeping
complete candidate membership.

The frontier checker verifies:

1. every frozen candidate ID occurs exactly once as a retained or dominated member;
2. every dominance edge compares facts from the same problem, candidate set, material, and fit
   configuration;
3. every claimed edge satisfies componentwise dominance exactly;
4. every retained fact belongs to the frozen candidate set; and
5. the frontier and membership hashes reconcile.

For a remnant, the no-fit evaluator applies the existing conservative rules to each frontier entry:

- material mismatch;
- footprint area exceeds remnant area plus the registered tolerance;
- footprint width exceeds remnant width plus coordinate tolerance; or
- footprint height exceeds remnant height plus coordinate tolerance.

Only when every frontier entry is rejected may the evaluator prove the complete candidate set
impossible. If any frontier entry survives, the evaluator must run the unchanged registered exact
search. Frontier membership can never authorize a fit, select an action, or suppress a survivor.

### 3. Common-transition lemmas

The frozen M7 continuation is shared by all hypothetical current-action branches. Each future event
gets one portable common-transition lemma binding:

- M0, M6, M7, replay-input, and semantic-runtime identities;
- cursor before and after;
- event binding and sequence;
- selected action evidence and policy rank;
- inventory before and after;
- candidate frontier identities used to eliminate remnant competitors;
- exact-search evidence for any frontier survivor; and
- the complete cost and lineage transition.

The generator constructs the common path once in event order. The checker validates the portable
lemma once and grants only a process-local checked capability. Action proofs reference the lemma by
content hash.

The common-transition checker must not call the generator entry point. For frontier-certified
events, it verifies the frontier, remnant measurements, rejection inequalities, policy rank, state
transition, and cost commitments. For survivors, it invokes the existing exact M7 path.

### 4. Branch-influence lemmas

One influence lemma binds a unique combination of:

- common-transition lemma;
- branch state before and after;
- added or removed remnant identity;
- future event and candidate set;
- influence classification; and
- frontier or exact-search evidence.

Classifications retain their existing meanings:

- `no_fit`: every candidate is eliminated by a verified frontier or by complete registered exact
  no-witness evidence;
- `policy_dominated`: an exact remnant competitor exists but the common action wins the frozen policy
  comparison;
- `exact_transition`: passivity cannot be proved, so the exact branch transition is executed; and
- `state_rejoin`: common and branch cursors are identical and apply the same frozen action.

Identical influence keys share one lemma. Action proofs cannot embed an unregistered private cache
or generator capability.

### 5. Reference-based action proofs

The next unpublished action-proof schema stores:

- initial action and baseline fallback commitments;
- start and stop positions;
- suffix and semantic-runtime commitments;
- ordered common-transition and influence lemma IDs;
- state-before and state-after hashes at each step;
- final net cost; and
- final state hash.

The proof DAG is acyclic and topologically ordered. Every reference resolves exactly once. Missing,
duplicate, cross-runtime, cross-stream, or unused facts fail validation.

### 6. Independent checker

The checker proceeds in four layers:

1. validate strict schemas, content hashes, frozen identities, canonical order, and complete sets;
2. validate each candidate scalar fact and rejection frontier once;
3. validate each common-transition and influence lemma once; and
4. traverse every action proof through checked lemma references and reconcile terminal state/cost.

The checker must have no call path to generator-only capability creation. Mutation tests must show
that changing a scalar, dominance edge, candidate membership, inequality reason, policy rank, state
hash, action binding, lemma reference, or terminal value fails closed.

Exact search remains available to the checker for explicit survivors. Performance evidence must
report how many checker facts required exact search; hidden fallback is prohibited.

## Calibration-only coverage pack

The six current runtime cells use the first two events of one calibration stream per regime and
therefore observed only a one-event future. Coverage must not depend on those prefixes accidentally
containing rare branch behavior.

The revised gate separates runtime cells from a real calibration coverage pack. Selection is frozen
before results using only the 12 calibration streams and the following deterministic order:

1. regime order from `TemporalRegime`;
2. temporal seed;
3. event position;
4. registered horizon ladder;
5. action kind; and
6. catalog action ID.

The scanner may inspect witness classification for coverage selection. It may not inspect oracle
savings, advantage magnitude, M8 evaluation data, or any outcome used by a later commercial or
economic decision.

The first canonical real-calibration example of each missing classification is frozen by complete
input and action identity. Each selected example must pass the independent checker and matched brute
reference. Synthetic exhaustive cases remain supporting kernel evidence and cannot substitute for
a missing real-calibration classification.

If all calibration streams contain no real `exact_transition` or `policy_dominated` example, the
gate remains closed and the calibration evidence design must be reconsidered. Evaluation is not a
coverage-discovery source.

## Runtime accounting

Instrumentation records, per regime and phase:

- candidate scalar fact loading;
- frontier construction and verification;
- common-transition construction and checking;
- remnant measurement;
- frontier rejection;
- exact-search fallback;
- influence-lemma construction and checking;
- action-proof construction and traversal;
- serialization/hashing;
- cleanup; and
- cold/warm cache hits and misses.

The existing official sampled-speedup and projection calculations remain unchanged for the next
gate. Required compilation or verification performed inside generator/checker phases remains timed.
Preflight candidate/archive verification retains its existing measurement boundary and is reported
in total wall time. No unreported warm cache may cross a fresh-process boundary.

Before another canonical run, an abbreviated checkpoint must demonstrate:

- zero semantic mismatch on exhaustive fixtures and the frozen 12-action sample;
- at least `25x` sampled speedup;
- no more than five projected days;
- all required real-calibration witness classifications; and
- deterministic cleanup with no surviving workers or private capabilities.

Failure stops the full canonical rerun.

## Error handling

The redesign remains fail closed:

- invalid or inconsistent scalar facts reject the whole proof batch;
- incomplete frontier membership rejects the candidate set;
- a failed dominance comparison restores the candidate to the frontier rather than rejecting it;
- any surviving frontier candidate triggers exact search;
- missing or duplicate lemma references invalidate the action proof;
- checker exceptions are recorded as proof failures, never validity;
- worker exceptions cancel owned work, reap process groups, and publish no partial artifact; and
- an existing output path with different bytes remains an immutable-artifact error.

## Test strategy

### Unit and property tests

- strict scalar-fact and frontier contracts;
- canonical identities and ordering;
- duplicate and missing membership rejection;
- Pareto dominance properties;
- equality/tolerance boundary cases;
- material mismatch behavior;
- survivor preservation; and
- deterministic content hashes.

### Differential tests

- frontier rejection equals the existing candidate-by-candidate conservative rejection on every
  selected calibration candidate/remnant pair;
- every frontier survivor receives unchanged exact search;
- factored scores, selections, transitions, costs, and terminal states equal the current certificate
  and slow reference on the exhaustive finite suite; and
- the frozen 12-action audit reproduces exactly.

### Checker-adversarial tests

- mutate every committed scalar and identity field;
- remove candidate membership;
- forge a dominance edge;
- change an inequality reason or tolerance;
- swap common or influence lemma references;
- alter policy rank, action binding, cursor, or cost; and
- attempt to reuse facts across a stream, runtime, process, or closed capability.

Every mutation must fail closed.

### Integration and performance tests

- one light and one heavy real calibration regime;
- full six-cell abbreviated checkpoint;
- fresh generator and checker processes;
- exact process-count and cleanup assertions;
- cold and warm measurements reported separately; and
- no canonical execution until the internal performance margin passes.

## Execution gates

1. **Instrumentation gate:** at least 90% of heavy-regime runtime is attributed to named phases.
2. **Frontier spike gate:** zero rejection differences and at least 10x improvement on the measured
   heavy common-transition path.
3. **Proof-kernel gate:** exhaustive equality, mutation rejection, and zero hidden exact search for
   frontier-certified no-fit cases.
4. **Coverage gate:** all four witness classes appear in real calibration evidence and matched audit.
5. **Abbreviated performance gate:** at least 25x and no more than five projected days.
6. **Canonical certificate gate:** at least 20x, no more than seven projected days, zero failures,
   zero mismatches, complete coverage, and evaluation unopened.
7. **Full-horizon pilot gate:** one complete calibration stream per regime reproduces twice with
   bounded runtime and no mismatch.
8. **Evaluation authorization:** only the preceding passing artifacts may open the 36-stream sealed
   evaluation partition.

## Artifact migration

The completed v3 result remains immutable evidence of the failed first gate. The redesign introduces
new unpublished versions for scalar/frontier facts, proof DAG nodes, action proofs, and the aggregate
certificate result. It does not rewrite `yfm8proof-b296ba919c07d55ece14c6db`.

Every new aggregate artifact records:

- parent v3 proof identity;
- M0/M6/M7/freeze identities;
- coverage-pack identity;
- frontier and lemma counts;
- exact-fallback counts;
- phase-level timings;
- speedup and projection arithmetic;
- evaluation-partition state;
- technical decision; and
- the unchanged claim ceiling.

## Claim ceiling

A passing redesigned certificate gate proves only that the implemented M8 rollout scorer is exact
on the registered software evidence boundary and computationally feasible under the declared local
execution contract. It does not establish oracle advantage, savings, global optimality, physical
recoverability, production value, or commercial demand.
