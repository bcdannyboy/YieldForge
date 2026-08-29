# M9 — Search validation

**Status:** Registered finite decision gate passed with the minimal two-ply repair —
`pass_decision_feasibility` (2026-08-29)

M9 determines whether the scalable oracle is strong enough to interpret. Tiny streams are solved exhaustively, rollout is compared with the exact optimum, and beam search is added only if longer-horizon reoptimization could change the decision.

> **Question:** Is the measured opportunity genuine, or could our search procedure be hiding or distorting it?

## Acceptance boundary

Exact small-instance references are independently checked, approximation gaps are reported, and any scalable search is adequate under a declared compute budget.

## Decision context

The user accepted M8 as feasible enough to continue decision sequencing. That acceptance did not
pass M8's original formal performance/evaluation gate, open the sealed evaluation partition, or
produce an M8 oracle-savings result. M9 therefore tested only the distinct bounded question of
whether M8's one-step rollout chooses a globally optimal first action on the registered finite
cases.

## Original one-step test (preserved failure)

Canonical result `yfm9-97e032de7a09247cc83e6c5a` evaluated all 45 registered cases under both the
primary scrap-only objective and a zero-total-terminal-credit sensitivity. The exact solver
reoptimized every future decision from the complete M7 action catalog; the comparator scored the
same first actions with M8's frozen one-step continuation. The five separated two-event no-fit,
equal-cost cases served as tiny information-null controls. The evaluation partition stayed sealed.

Each objective completed 3,110 transitions and 2,440 terminal leaves, reached a peak branching
factor of 10, and reported zero truncated catalogs. All five controls passed. A fresh-fixture
repeat produced the same semantic result, bound by evaluator reproducibility SHA
`sha256:327163a6d1bbb8fd6479f3a3227b522c4379a5f6882fc3d420388d07b9ac92f6`.
The original publication runs took 15.2690 and 16.0135 seconds; those wall times are operational
observations outside semantic identity.

## Original result

The one-step rollout selected a globally optimal first action in 44 of 45 primary cases. The sole
counterexample is `remnant_first-one-match-fit-unequal-high-retrieval-three`:

- exact reoptimization opens a standard sheet first and reaches a cost of `300.0`;
- after the rollout-selected remnant action, the best reachable exact cost is `400.0`; and
- the frozen one-step continuation scores all four root actions at `500.0`, so its baseline-first
  tie rule selects a remnant action.

The resulting first-action regret is `100.0`, or `33.3333%` of the exact optimum. The same case and
gap remain under zero total terminal credit, so the conclusion is not an artifact of terminal
inventory valuation.

The minimal M9 test therefore did not pass even at the bounded decision level and returns
`fail_search_gap`. This is a bounded search-quality counterexample: it shows that frozen one-step
continuation can hide a better coordinated sequence of future choices. It does not establish
failure on the M6 population, universal policy suboptimality, physical recoverability, savings,
buyer demand, or commercial value.

Semantic content SHA:
`sha256:97e032de7a09247cc83e6c5a7140c67ea988712a1b493ca92b6513d95ea98dca`.

## Minimal two-ply repair

The minimum repair enumerates the current and next decisions from complete M7 action catalogs,
then returns to frozen M7 continuation. Search depth is fixed at `2`; selection uses bounded cost,
then the original baseline action on an exact tie, then action ID. The repair scorer does not call
the exact solver. Exact search remains an independent comparison layer for the finite gate.

Canonical repair result `yfm9r-db0829451b1b0393f2d22559` is explicitly bound to the unchanged
original failure `yfm9-97e032de7a09247cc83e6c5a` and its raw-file SHA
`sha256:9ae6d7fdf2252023a96de8773877bb50f3786f2f7c8b1c6c4bcb5a7de1ca82e3`.
Two fresh fixture builds produced the same repaired semantic result. The evaluation partition
remained sealed.

Under both scrap-only and zero-total-terminal-credit objectives, the repair:

- selected an exact-optimal first action in all `45/45` registered cases;
- produced zero first-action regret and zero counterexamples;
- passed all five tiny information-null controls; and
- completed, per objective, `185` catalogs, `650` explicit transitions, `500` frozen-continuation
  event transitions, `1,150` total event transitions, `480` continuation calls, and `30` direct
  terminalizations, with peak branching factor `8` and zero truncation.

The observed work passes the registered limits of at most `200` catalogs, `700` explicit
transitions, and `1,200` total event transitions. The conclusion does not reverse under the
terminal-credit sensitivity.

On the original high-retrieval-cost counterexample, two-ply scoring separates standard-first at
`400.0` from remnant-first at `500.0`, so it selects a standard action through a strict advantage.
Independent exact search values those same branches at `300.0` and `400.0`. The repair therefore
fixes the action ordering without claiming exact value calibration: its selected-action estimate is
exact in `42/45` cases and conservatively high in the other three, with maximum error `100.0` under
the primary objective and `101.0` under the sensitivity.

The canonical semantic content SHA is
`sha256:db0829451b1b0393f2d2255990ade1ce783b27a8527f73f3c7bf07e6716438ba`, the evaluator
reproducibility SHA is
`sha256:9fc0664eaf4b97c67376def10b0dd83534eb5605ebc26823a2a7d5a2522e1d65`, and the immutable raw-file
SHA is `sha256:16444e2e0f1a6fa5fb57398b290a7b0b66fda271997a48202a403c499060b858`.
Publication repeats took approximately `19.576` and `19.542` seconds; an independent rerun took
approximately `19.499` and `19.574` seconds. These wall times are operational observations outside
semantic identity.

## Bounded decision

M9 now returns `pass_decision_feasibility` at the registered finite decision level. The supported
claim is only that fixed two-ply reoptimization selected an exact-optimal first action on these 45
registered cases, under both named terminal treatments, within the declared structural work budget.
It is not a universal policy-optimality result, an exact-value guarantee, an M6 evaluation result,
or evidence of physical recoverability, savings, buyer demand, or commercial value.

M8's original formal performance/evaluation gate remains unresolved, no sealed evaluation data was
opened, and no oracle-savings claim exists.

## Next decision

The minimal M9 repair is complete; no broader or configurable beam search is required for this
bounded gate. M10 is a separate decision and is not automatically authorized by the M9 pass.
