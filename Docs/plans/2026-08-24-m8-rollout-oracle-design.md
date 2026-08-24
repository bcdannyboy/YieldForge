# M8 Full-Horizon Rollout Oracle Design

**Approved direction:** Evaluate every exact current action over the complete remaining registered
stream, use the frozen M7 policy for each hypothetical continuation, and accelerate only through
semantics-preserving caches and exact state coalescing.

## Claim boundary

M8 asks whether knowing the realized future changes today's material decision enough to reduce the
modeled closed-horizon net cost relative to frozen M7. A pass may establish a reproducible paired
rollout-policy advantage in the generated M6 worlds and may attribute a portion of that advantage to
information unavailable at decision time.

M8 does not establish a mathematical opportunity ceiling, global optimality, factory
representativeness, physical remnant recoverability, buyer demand, or commercial value. M9 must
measure the rollout policy's search gap, and M10 must apply the complete evidence and decision gates.

## Immutable inputs and blindness

The canonical implementation binds:

- M0 economic contract and its primary remnant, failure, terminal, and reporting rules;
- M6 contract `yfm6-3eeda3f4feb80813807c501a`, population
  `yftp-49bd7ce5fd34b2779440c52f`, and immutable 12/36 calibration/evaluation split;
- M7 problem index `yfm7i-116c24d7fce8ce415d46533e`;
- amended M7 freeze `yfm7freeze-5c13c3fe531828d8cd986c39` and its frozen
  `age_regularity` continuation policy; and
- the exact verified M7 candidate-set identities, action rules, rates, collision backend, and
  runtime compatibility rule.

The already-published aggregate M7 action volume and timing may size exact runtime infrastructure.
M8 must not use per-stream M7 evaluation outcomes to tune a horizon, candidate filter, tie-breaker,
regime rule, or policy parameter. M8 evaluation worlds remain closed until the oracle contract,
acceptance tests, calibration-only runtime evidence, and freeze artifact are complete.

## Selected architecture

### 1. One-step full-horizon policy improvement

At every real event, the full oracle receives the current M7 state and the complete realized suffix.
It performs the following deterministic calculation:

1. accrue the same storage interval and enumerate the exact common M7 current action set;
2. virtually apply each current action to an isolated copy of state;
3. replay every remaining registered event through the frozen M7 policy from that post-action
   state;
4. apply the same registered horizon end and scrap-only terminal liquidation;
5. score the action by final M0 net cost; and
6. choose the lowest-cost action, preferring the frozen M7 action on an exact cost tie and then the
   lexical action identity.

The chosen current action is executed on the oracle's real trajectory. At the next event the oracle
performs a fresh one-step rollout from its new state. Hypothetical future decisions use M7, not the
oracle. This is the proposal's tractable rollout policy, not multi-step beam search.

The horizon is always the complete remaining registered stream. There is no primary candidate cap,
lookahead-event cap, heuristic pruning, approximate dominance, or early terminal substitution.

### 2. Shared action catalog and transition seam

M7 currently combines action generation, policy selection, transition execution, and full-stream
replay. M8 needs those same semantics from an arbitrary state and suffix. The implementation will
extract a public, versioned seam with three responsibilities:

- enumerate lazy descriptors for every standard-sheet action and every exact feasible remnant
  action;
- materialize and apply one descriptor, producing the exact action evidence, inventory transition,
  ledger delta, and next state; and
- run the unchanged frozen M7 policy from an arbitrary state over a supplied suffix.

Standard-sheet descriptors refer to the existing verified archived candidate. Remnant descriptors
also bind the selected remnant and registered exact fit-search witness. Expensive polygon evidence
is materialized only when an action is applied, but every descriptor remains content-addressed and
auditable.

The existing M7 replay remains a caller of this seam. Before M8 work proceeds, all published M7
replay identities must reproduce byte-for-byte. M8's current action count, candidate IDs, stock
eligibility, and baseline selected action must match the corresponding M7 event.

### 3. Information isolation and known-only control

The rollout engine receives future events only through an explicit visibility provider:

- `full_realized_future` exposes the registered post-current M6 suffix to the action scorer;
- `known_only` exposes only future work carrying `known_at <= current_time`; and
- the frozen M7 baseline receives no oracle visibility provider at all.

M6 has no pre-release `known_at` field. Its known-only visible suffix is therefore empty rather than
inferred from realized chronology or regime labels. The known-only arm still enumerates and scores
the identical current actions through the same transition and terminal engine. This measures the
benefit of the rollout machinery without unavailable future demand, although its downstream work is
naturally smaller because no future events are visible.

Hidden regime labels, future task IDs, future geometries, future material identities, future action
availability, and future fit outcomes are inaccessible outside the full oracle scorer. Persisted
decision evidence records the visibility mode and the exact visible-suffix identity.

### 4. Sparse exact delta replay

M7 evaluated 550,542 current actions across 864 held-out events, about 637 per event. Replaying every
remaining event from scratch for every action would require roughly 6.3 million continuation-event
executions and is rejected as the production M8 architecture.

The frozen M7 policy exposes an exact reusable decomposition:

- every standard-sheet action's policy terms depend on its verified candidate profile and frozen
  rates, not on current inventory;
- inventory can alter the future choice only by supplying an exact feasible remnant action; and
- storage and terminal liquidation for a remnant that cannot affect a future choice are direct M0
  ledger calculations.

M8 therefore compiles the frozen best standard action once for every reusable problem and evaluates
each hypothetical current action as a delta from the common continuation:

1. Materialize the current action once and identify the added, removed, and unchanged remnants.
2. Carry the common frozen-M7 future trajectory without rebuilding standard candidate profiles.
3. Use rejection-safe material, area, and axis-aligned footprint-bound tests to identify future
   problem/candidate pairs that cannot fit under the frozen translation-only search.
4. Accrue storage and terminal value analytically for branch-only remnants with a complete no-fit
   certificate over the remaining suffix.
5. Invoke the registered Jagua/Shapely exact fit path only for surviving branch-remnant/event pairs.
6. Recompute the future decision only when a branch remnant has an exact feasible action that can
   compete with the compiled standard or common-remnant winner.
7. Memoize and rejoin only byte-equivalent exact states; never merge approximately similar polygons.

The sparse evaluator and a deliberately slow full-replay reference implement the same score and
tie rule. Toy cases and calibration prefixes compare every action score, selected action, inventory
transition, and terminal ledger. A single mismatch rejects the sparse evaluator.

The persistent cache uses strict content-addressed keys and validated payload hashes for compiled
standard winners, prepared footprints, safe rejection certificates, remnant fit-search results,
materialized transitions, exact state deltas, and continuation results. Every key includes the
relevant schema, M7 freeze/runtime identity, geometry and candidate hashes, rules, fit/search
configuration, collision backend, exact state, suffix, and horizon. Corruption or a
same-key/different-value conflict fails closed.

Checkpoints occur after each scored action and completed event. The canonical computation retains
the full horizon and complete candidate set; it has no semantic timeout, heuristic pruning, or
approximate merge. Parallelism begins at no more than eight local workers and must not affect result
identity.

Before the six-stream pilot, a bounded calibration-prefix proof must show zero semantic mismatches
and at least a 20x end-to-end speedup over the full-replay reference. Its measured projection must
support completing held-out M8 within seven calendar days on the declared execution resources. If
either gate fails, local evaluation remains closed and the next permitted path is exact distributed
execution or further semantics-preserving optimization.

### 5. Contracts and evidence

M8 adds strict content-addressed contracts for:

- the oracle freeze and bound M7 inputs;
- exact rollout state and action descriptors;
- per-action rollout scores and continuation identities;
- per-event oracle decisions, including the matching M7 fallback action;
- per-stream full and known-only results; and
- paired experiment summaries and reproducibility evidence.

The primary paired stream quantities are:

`OracleSavings = (BaselineCost - FullOracleCost) / BaselineCost`

`UnknownFutureContribution = (KnownOnlyOracleCost - FullOracleCost) / BaselineCost`

Baseline cost must be positive. M8 reports every registered stream and the M0 mean, median,
stratified paired bootstrap interval, P10, worst-decile mean, positive fraction, and concentration
diagnostics. It also reports action divergence, immediate sacrifice, reuse realization, cache
behavior, and runtime. M8 does not issue the final green/yellow/red project verdict.

### 6. Failure treatment

The baseline action is mandatory in every oracle action catalog. Missing or changed candidate
evidence, unavailable fallback, action-count mismatch, infeasible transition, invalid geometry,
accounting mismatch, information leak, continuation failure, corrupted cache, incomplete stream, or
failed reproducibility check invalidates the paired stream and prevents a numeric savings result.

Only the existing M0-identical outer retry for worker failure or outer timeout is permitted. A retry
uses identical inputs. Candidate, seed, state, horizon, or policy substitution is forbidden.

## Acceptance and testing

Before any M8 evaluation stream is opened, executable gates must cover:

1. **Hand-computed delayed reuse:** a slightly worse current action creates a remnant that avoids a
   known later sheet purchase, and full rollout chooses it with the reconciled terminal cost.
2. **No-signal tie:** when the visible suffix cannot distinguish actions, the exact M7 fallback wins
   the tie and advantage is zero.
3. **Information isolation:** changing a hidden suffix may change the full-oracle decision but cannot
   change the baseline or known-only current decision.
4. **Candidate parity:** the M7 action set and fallback bind exactly; deleting or adding an action
   invalidates the event.
5. **Policy improvement:** on small deterministic cases, full rollout cannot cost more than replaying
   the available M7 fallback.
6. **Terminal accounting:** storage through the registered horizon and scrap-only liquidation match
   hand calculations.
7. **Exact state coalescing:** identical states replay once; any ledger, lineage, geometry, time, or
   suffix change prevents coalescing.
8. **Cache differential:** cache off, cold cache, warm cache, interrupted resume, and different
   worker counts produce identical semantic results.
9. **Published-M7 regression:** refactored M7 replay regenerates the frozen evaluation content
   identity before oracle evaluation is authorized.
10. **Sparse-reference differential:** every score, choice, transition, and terminal ledger matches
    the slow full-replay reference on all registered small cases and calibration proof prefixes.
11. **Runtime gate:** the sparse path demonstrates at least 20x end-to-end speedup and projects no
    more than seven calendar days for the frozen held-out execution resources.

A deterministic calibration-only runtime proof precedes the six-stream pilot. The proof uses short
registered prefixes against the slow reference; the pilot then uses one complete calibration stream
per regime. Both record action/state counts, rejection certificates, exact searches, branch points,
cache hits, storage, wall time, and projected held-out runtime. The final execution manifest is
frozen only after the zero-mismatch, speed, and seven-day projection gates pass.

## Alternatives and sensitivities

- **Fixed event or time horizon:** cheaper and useful as a sensitivity, but it can miss delayed
  remnant reuse and is not the canonical M8 question.
- **Pre-ranked or capped candidates:** substantially cheaper, but it weakens candidate parity and is
  an explicitly labeled candidate-budget ablation.
- **Approximate state similarity:** may help M9 beam diversity but cannot merge M8 rollout states.
- **Naive branch-by-branch replay:** source-faithful but operationally rejected because measured M7
  rates project multi-week or multi-month local execution without sparse exact reuse.
- **Distributed exact replay:** the fallback if sparse local execution remains over seven days. It
  preserves semantics but requires a separately frozen worker/runtime manifest.
- **GPU prefilter:** remains a separate optimization experiment. It cannot replace authoritative
  Shapely topology and is not required for M8 correctness.
- **Beam or exhaustive multi-step oracle:** belongs to M9 search validation and must not be folded
  into the M8 rollout result.

## Exit boundary

M8 preparation is complete when this sparse exact design and its revised implementation plan are
committed. M8 implementation first builds the full-replay reference and sparse differential proof.
The persistent cache, six-stream pilot, and execution freeze follow only after the 20x zero-mismatch
gate passes. Held-out execution remains closed unless its frozen projection is seven days or less.
