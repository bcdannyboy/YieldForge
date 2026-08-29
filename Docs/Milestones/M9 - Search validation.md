# M9 — Search validation

**Status:** Minimal bounded decision test complete — `fail_search_gap` (2026-08-29)

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

## Minimal test

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

## Result

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

## Next decision

No beam-search repair was built. The next explicit choice is to add a bounded beam or shallow
reoptimization that addresses the counterexample, or to accept the measured one-step search risk.
Only after that choice should M10 be considered separately. This result authorizes neither a repair
nor M10 execution.
