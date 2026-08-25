# M8 Certificate-Based Exactness Proof Design

**Date:** 2026-08-24
**Status:** Approved

## Purpose

Replace the operationally infeasible full-reference calibration-prefix comparison with a
certificate-based proof that the sparse M8 evaluator is semantically identical to the frozen M7
continuation engine. The complete current action catalog, full realized future horizon, registered
collision and fit behavior, frozen M7 policy, and exact M0 cost ledger remain unchanged.

The proof is relative to the registered Shapely/Jagua geometry semantics. It does not independently
prove the geometry engines, global nesting optimality, physical validity, or commercial value.

## Evidence motivating the change

The deliberately slow reference evaluator replays the complete future once per current action. On
the first two-event real calibration prefix, a single reference cell did not complete after extended
execution with eight workers. The first no-signal calibration stream contains 13,971 M7 action
alternatives, so using exhaustive real-prefix replay as the proof mechanism recreates the production
complexity that sparse replay is intended to remove.

No evaluation stream was opened and no partial proof artifact was published. The stopped run is
diagnostic evidence about validation cost, not an M8 result.

## Proof boundary

For a frozen M8 request, let the **common path** be the future M7 continuation after taking the
registered current-event M7 fallback. Let an alternative current action create a branch state. The
branch is represented exactly as:

- inventory items added relative to the common state;
- inventory items removed relative to the common state;
- any changed inventory records;
- the cumulative M0 ledger delta; and
- the current event position and visible suffix identity.

The proof must establish that every reported M8 action score equals the score produced by exact
branch replay under the same frozen M7 transition semantics. A branch may skip a future transition
only when a checkable certificate proves that the branch and common path choose the same action at
that transition.

## Inductive branch-passivity theorem

At a future event, the branch and common path choose the same frozen M7 action when all of the
following hold:

1. Every common-path remnant action removed by the branch is proven not to be the common winner.
2. Every branch-only or changed remnant is either infeasible under the registered fit result or has
   a frozen M7 policy key strictly worse than the common winner.
3. Standard-sheet action profiles are identical because they depend on frozen candidate evidence,
   rates, rules, and policy rather than branch inventory.
4. The tie rule is evaluated exactly, including the registered action, candidate, stock, and
   remnant identity fields.

If the conditions hold, applying the common action produces the same event decision on both paths.
The inventory delta then advances deterministically. Repeating this argument over the suffix proves
by induction that the entire future action sequence is identical. Storage and terminal liquidation
differences can therefore be calculated directly from the exact inventory and ledger delta with the
existing M0 rounding functions.

This theorem does not require a branch-only remnant to be globally impossible to fit. A feasible
remnant is still passive when its complete frozen M7 policy key cannot beat the common winner.

## Exact escape and recursive continuation

If any condition cannot be certified at an event, the evaluator must not assume passivity. It
reconstructs the exact branch cursor, enumerates the registered M7 catalog, executes the exact
winner, updates the delta, and continues from the next event. It may rejoin the common path only when
the full canonical cursor, ledger, inventory, suffix position, and lineage are byte-equivalent.

The resulting algorithm is exact but output-sensitive: it pays for one common continuation, cheap
certificates for passive branch-event pairs, and exact transitions only at genuine or unresolved
influence points.

## Proof objects

Every scored action produces a content-addressed proof object containing:

- the M0, M6, M7 freeze, candidate archive, and geometry runtime identities;
- the current action descriptor and common fallback identity;
- the exact starting state and visible suffix identity;
- one ordered event witness per future event;
- each witness classification: `state_rejoin`, `no_fit`, `policy_dominated`, or `exact_transition`;
- the inputs and outputs needed to replay each classification;
- the analytic storage and terminal-ledger reconciliation; and
- the final action score and proof hash.

A separate checker validates the object without invoking the full branch-reference evaluator. It
recomputes hashes, policy keys, tie decisions, delta transitions, cost rounding, and terminal
reconciliation. It may invoke the frozen registered geometry lookup for a recorded fit result.
Unknown classifications, missing events, inconsistent state deltas, or incomplete evidence fail
closed.

## Validation strategy

The proof checker is validated with three complementary layers:

1. **Exhaustive finite cases:** enumerate all registered small/toy inventories, actions, suffixes,
   ties, added and removed remnants, fit/no-fit outcomes, and terminal conditions. Compare every
   score and selected action with full reference replay.
2. **Stratified real audit:** run the slow reference on a bounded deterministic sample covering all
   six regimes, action kinds, certificate reasons, survivor paths, exact escapes, state rejoins, and
   horizon lengths. Require zero semantic mismatches.
3. **Full calibration certificate execution:** score every current action in the selected real
   calibration prefixes with the sparse evaluator and validate every produced proof object. This
   step does not replay every branch through the slow reference.

Sampling may validate the checker, but sampling is never used to fill a missing proof. Every action
in the full calibration certificate execution must have complete checkable evidence or an exact
transition trace.

## Revised first M8 go/no-go

The gate passes only when:

- all exhaustive finite cases match the full reference;
- the frozen stratified real audit records zero semantic mismatches;
- every full calibration action has a valid complete proof object;
- no event is skipped without a validated certificate;
- matched sampled cases demonstrate at least 20x end-to-end speedup over full reference replay; and
- measured full-certificate throughput projects the frozen held-out M8 execution at no more than
  seven calendar days on the declared resources.

The gate fails to `redesign_certificate_proof` for any mismatch, invalid proof, uncovered action, or
speedup below 20x. It fails to `require_distributed_exact` when exactness passes but the conservative
held-out projection exceeds seven days. Evaluation remains closed in either case.

## Failure handling and auditability

Proof generation and checking are deterministic for frozen inputs. A geometry result, policy key,
state transition, or ledger value that conflicts under the same content-addressed key is corruption
and fails closed. Checkpoint/resume may persist completed per-action proofs but may not replace a
stream, action, suffix, runtime, seed, or candidate archive.

The proof artifact reports certificate coverage, exact escape count, unmatched classifications,
checker failures, sampled-reference mismatches, observed speedup, throughput, and held-out
projection. It retains the existing claim ceiling: M8 evidence is software evidence about oracle
advantage under the registered replay semantics, not physical or commercial proof.

## Non-goals

- Independently proving Shapely, Jagua, GEOS, or floating-point geometry correctness.
- Reducing the current action set or future horizon.
- Approximate state merging or heuristic pruning.
- Proving a global nesting optimum.
- Opening M8 evaluation before the revised gate passes.
