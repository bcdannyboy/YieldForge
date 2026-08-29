# M9 Minimal Search Validation Design

**Status:** Approved for implementation on 2026-08-29

## Decision being tested

M9 asks whether M8's one-step rollout selects a globally optimal current action on the already
registered finite semantic cases. This is a decision-feasibility test, not a proof of universal
optimality, a production performance gate, or a savings result.

M8 already proves that its sparse/factored implementations reproduce the slow one-step reference.
M9 must test the distinct missing property: reoptimizing every future decision may produce a lower
cost than following the frozen M7 continuation after the first action.

## Scope

Use all 45 cases returned by `tests.oracle.fixtures.exhaustive_certificate_cases()`. They cross the
five frozen M7 policies with nine deterministic two-to-four-event scenarios and cover fit/no-fit,
material match/mismatch, zero-to-two starting remnants, equal/unequal costs, same/separated times,
and positive/zero terminal inventory.

Do not open evaluation data, change M0 economics, build beam search, port code to Rust, or extend
the generalized M8 execution/evidence system.

## Exact reference search

Add a small calibration-only M9 module that accepts an exact `M8OracleRequest` and recursively:

1. enumerates the complete current catalog with `enumerate_m7_action_catalog(..., complete=True)`;
2. applies every descriptor with `apply_m7_action_descriptor`;
3. re-enumerates every legal action at the next event;
4. terminates at the request's visible stop position using the existing M7 terminal storage and
   liquidation calculation; and
5. returns every globally optimal first-action ID, the optimal terminal cost, the complete root
   score vector, and bounded search telemetry.

The exact reference may share the frozen M7 transition/accounting primitives because those are the
semantic constitution already accepted in M5/M7. It must not invoke M8 sparse, factored, reference,
or checker scoring while computing the optimum.

## Comparison

For each case, compute:

- M8's current one-step rollout decision with `score_reference_event`;
- the exact multi-step optimum;
- whether the rollout-selected action is in the globally optimal first-action set;
- the exact cost reachable after the rollout-selected first action when all later decisions are
  reoptimized;
- absolute and relative optimality gaps; and
- explored transitions, terminal leaves, and peak branching.

Exact ties are valid. A selected action passes when it belongs to the optimal first-action set.

## Controls

- Validate the recursive solver against one hand-computed two-event case.
- Require a repeated full 45-case run to produce the same semantic result.
- Treat the five registered separated two-event no-fit/equal-cost cases as tiny information-null
  controls and require zero action-value gap. Do not represent them as the M6 `no_signal` regime.
- Re-score every exact terminal leaf under zero total terminal credit by adding back only the
  terminal-credit ledger field. Do not set the global scrap rate to zero because that would also
  remove realized scrap proceeds. Report whether the primary pass/fail conclusion reverses.

The terminal arm is a sensitivity result. It may not overwrite a primary failure.

## Decision rule

Return `pass_decision_feasibility` only when:

- all 45 primary cases complete;
- every rollout-selected action is globally optimal;
- maximum absolute optimality gap is exactly zero;
- every no-signal control has zero gap;
- the deterministic repeat is identical; and
- the zero-terminal-credit arm does not reverse the conclusion.

Otherwise return `fail_search_gap`, preserve the smallest deterministic counterexample, and stop.
Beam search is a separate, explicitly authorized response to an observed gap; it is not part of the
minimal test.

## Evidence and claim ceiling

The implementation emits one canonical JSON result under `yf/experiments/results/` and a focused
test report. The result binds the ordered case IDs, per-case decisions and gaps, aggregate counts,
control outcomes, and a content hash.

A pass supports only:

> On every registered finite decision case, one-step rollout selected a globally optimal first
> action with zero observed approximation gap.

A failure identifies a bounded search-quality counterexample. Neither outcome proves universal
optimality, physical recoverability, savings, buyer demand, or commercial value.
