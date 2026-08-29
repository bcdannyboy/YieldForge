# M9 Two-Ply Search Repair Design

**Status:** Approved for implementation on 2026-08-29 by the user's instruction to perform the
minimum work necessary to pass M9.

## Decision

Repair the bounded M9 search gap with two-ply reoptimization. For every current action, enumerate
and optimize one additional future decision, then hand the remaining suffix back to the unchanged
frozen M7 policy. This is the shallowest future-search extension that selects a globally optimal
first action on all 45 registered finite cases under both terminal objectives.

Depth counts explicitly optimized decisions including the root:

- depth 1 is the current M8 one-step rollout and remains the frozen failure control;
- depth 2 optimizes the current and next decisions, then follows frozen M7; and
- full exact search remains the independent reference only.

Do not change the M8 baseline-preferred root tie rule. Do not add a beam width, heuristic ranking,
case-specific exception, or terminal-value tolerance.

## Alternatives rejected

Several tie-only patches pass the finite matrix, including standard-sheet preference, lower
immediate-cost preference, and lexical action-ID preference. The lexical rule's success shows that
the observed flat tie can be patched post hoc without addressing the diagnosed coordination gap.
Changing the tie rule would also mix a new M7-visible secondary policy into an experiment intended
to measure value from future information.

A beam is unnecessary because depth 2 already closes the registered action-selection gap. Depth 3
nearly reaches full exact-search work and adds no acceptance value: M9 requires the selected action
to belong to the exact-optimal set, not to reproduce a particular member of an exact tie.

## Search semantics

For one request and named terminal objective:

1. Enumerate the complete root catalog.
2. Apply each root action with the frozen M7 transition and accounting primitives.
3. If the visible stop has been reached, terminalize the branch.
4. Otherwise enumerate the complete next-event catalog, apply every next action, and run the
   unchanged frozen M7 continuation from each resulting cursor to the visible stop.
5. Score each root action by the minimum terminal objective value reachable through its second-ply
   branches.
6. Select the root action with the existing key: bounded cost, baseline preference on an exact tie,
   then action ID.

Both catalogs must use `complete=True` and fail closed on any fit-search truncation. Same-time events
remain sequential through the existing cursor transition. The scrap-only objective uses final M7
net cost. The sensitivity adds back only terminal scrap credit; it does not change realized scrap
proceeds.

The implementation must be independent of the exact M9 solver while scoring the repaired policy.
The exact solver is used only afterward to determine whether the selected action is globally
optimal and to calculate first-action regret.

## Frozen finite gate

Run the repair on the same ordered 45 cases from
`tests.oracle.fixtures.exhaustive_certificate_cases()` under:

- `scrap_only`; and
- `zero_total_terminal_credit`.

Return `pass_decision_feasibility` only when:

- all 45 cases complete under both objectives;
- every two-ply-selected first action belongs to the exact-optimal first-action set;
- maximum absolute first-action regret is exactly zero;
- all five tiny information-null controls retain zero regret;
- no action catalog is truncated;
- two fresh complete executions have identical semantic bytes; and
- the terminal sensitivity does not reverse the primary conclusion.

Value estimates need not equal the exact optimum. The gate concerns selected-action optimality, not
value calibration. The artifact must separately report bounded-score error versus exact root values
so this distinction cannot be lost.

## Compute boundary

The read-only probe established the following structural budget per 45-case objective:

- at most 200 complete catalogs;
- at most 700 explicitly explored two-ply transitions;
- at most 1,200 total event transitions including frozen continuation; and
- zero truncated catalogs.

The observed probe used 185 catalogs, 650 explicit transitions, and 1,150 total event transitions.
This is approximately 2.80 times the depth-1 transition work and 37 percent of the full exact
transition work. Wall time is diagnostic and remains outside semantic identity because it is
environment-dependent.

## Evidence and publication

Preserve `yfm9-97e032de7a09247cc83e6c5a` as the immutable one-step failure artifact. Publish a separate
content-addressed repair artifact containing:

- repair semantics and frozen depth;
- both complete objective matrices;
- exact selected-action regret and bounded-score error;
- work telemetry and compute-budget reconciliation;
- ordered controls and counterexamples;
- deterministic repeat evidence; and
- `evaluation_partition_opened: false`.

The runner must rebuild fixtures twice, compare semantic bytes before publication, keep wall times
outside artifact identity, and refuse replacement of different existing bytes.

## Claim ceiling

A pass supports only:

> On every registered finite decision case, two-ply reoptimization selected a globally optimal
> first action with zero observed first-action regret under both declared terminal objectives and
> within the frozen finite compute boundary.

It does not prove exact oracle values, universal policy optimality, M6 evaluation performance,
physical recoverability, savings, buyer demand, or commercial value. It does not retroactively pass
M8's original performance gate or open the sealed evaluation partition.
