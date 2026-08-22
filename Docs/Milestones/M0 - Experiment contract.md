# M0 — Experiment contract

**Status:** Passed — economic constitution frozen; 10-second geometry budget selected

M0 decides what experiment we are actually running before observed results can influence the
rules. It gives precise meanings to savings, future information, usable remnants, fair policy
comparison, failure, reporting, and success.

The approved reasoning is preserved in
[[../plans/2026-08-22-m0-experiment-contract-design|M0 experiment-contract design]]. The controlling
machine-readable artifacts are:

- `yf/experiments/m0-contract-v1.json` — ID `yfm0-29b7efe8ac2a0a9995c4f907`, semantic
  SHA-256 `29b7efe8ac2a0a9995c4f907a56d7ce0cb9b61217b167f0737f6973c648b9a5f`;
- `yf/experiments/pure-geometry-calibration-v1.json` — ID
  `yfgp-49906e93ed9ff0446705247b`, semantic SHA-256
  `49906e93ed9ff0446705247bf6f2519588265ccbd9e6d1c9676e98ad7ed05737`;
- `yf/experiments/results/pure-geometry-calibration-yfgcr-c333f934c363abc0d78082ec.json` —
  ID `yfgcr-c333f934c363abc0d78082ec`, semantic SHA-256
  `c333f934c363abc0d78082ecdb60d8020ee0be8a08992b9e80e5caf4e349cbec`;
- `yf/experiments/pure-geometry-confirmation-v2.json` — ID
  `yfgp-392644d98bb7035fdc218512`, semantic SHA-256
  `392644d98bb7035fdc218512c9c28cd5f3120a38f8add211fd9b953456166b31`;
- the committed Lectra catalog — artifact SHA-256
  `0e5c3d8aa39846fc69a1c662d01f0a0a9a1761f5d7ce0fbb10efdcf759fc55ad`.

The validator pins approved semantic hashes, rejects ambiguous JSON, checks cross-artifact
identities, and rederives all task populations and selections from the catalog. Executable
contracts and their tests control over this prose if they disagree.

## Acceptance boundary and claim ceiling

M0 passed after all 612 registered calibration cells completed with verified archives and the
frozen selector selected 10 seconds per seed. Protocol v2 has `status = confirmation_ready`,
`selected_seconds_per_seed = 10`, and `confirmation_enabled = true`. The 203-task geometry
evaluation is now authorized under that exact protocol.

Passing M0 will prove only that the experiment was defined before confirmation. It will not prove
candidate richness, residual geometry, remnant utility, material savings, solver optimality,
physical recoverability, or commercial value.

## Primary economic outcome

For policy `P`:

`NetCost(P) = full-sheet purchases + storage + remnant-return handling + remnant-retrieval handling - realized scrap proceeds - terminal scrap credit`

`OracleSavings = (BaselineCost - FullOracleCost) / BaselineCost`

`BaselineCost` must be positive. Immediate utilization is a diagnostic only.

| Cost term | Frozen treatment |
| --- | --- |
| Full stock | Charge the full acquisition cost when a standard sheet is opened. |
| Storage | `retained remnant area × elapsed time × declared rate` over half-open intervals. |
| Return handling | Charge once when an eligible remnant enters inventory. |
| Retrieval handling | Charge once when a remnant leaves inventory for cutting. |
| Scrap proceeds | Credit only when material is actually scrapped. |
| Process loss | Kerf and clearance affect geometry and purchases, not a separate money term. |
| Terminal inventory | Liquidate at scrap value with zero option value beyond scrap. |

Later economic benchmark manifests must freeze exact nonnegative prices, rates, and units before
evaluation. M0 excludes cutting labor/time, energy, tool wear, setup, lateness, financing, tax,
overhead, purchasing lead time, and stockout costs because no source-backed model exists.

Required terminal sensitivities are zero total credit and bounded continuation credit no greater
than pro-rata virgin value. A green conclusion may not depend on continuation credit and may not
reverse between scrap-only and zero-credit treatments.

## Information sets and attribution

| Policy or control | Visible information |
| --- | --- |
| Myopic geometry | Released batch, current inventory, immediate candidate facts. |
| Commercial baseline | Myopic view plus remnant age, regularity, and declared cost state. |
| Known-order lookahead | Commercial view plus released or firmly scheduled work known at the timestamp; no early cutting. |
| Known-only oracle control | Same rollout/beam algorithm and compute as the full oracle, with unknown future events masked. |
| Perfect-information rollout | Entire realized future for the current action; frozen strong policy thereafter. |
| Perfect-information beam | Entire realized future at every decision inside the registered beam. |

The baseline is selected by lowest mean net cost on registered calibration streams and then
frozen. Evaluation streams never choose a comparator ex post. Later realized demand, hidden regime
labels, and oracle-only diagnostics are forbidden to baselines.

`UnknownFutureContribution = (KnownOnlyOracleCost - FullOracleCost) / BaselineCost`

This same-algorithm information ablation prevents better search from masquerading as future value.

## Event timing

At timestamp `t`, the simulator must:

1. accrue storage to `t`;
2. reveal orders released at `t`;
3. batch all and only compatible work released at `t`;
4. select and atomically execute an action that fulfills it immediately;
5. record purchase, handling, and scrap; and
6. return eligible remnants at `t`.

Firmly scheduled work with `known_at <= t` may inform planning but cannot be advanced. Multi-sheet
work is deterministically decomposed before policy comparison. After the final registered action,
storage accrues to explicit `horizon_end`, then terminal liquidation occurs.

## Candidate parity

Every comparator shares exact verified candidate-archive hashes, source projection, solver seeds
and configuration, ordinary compute, stock/remnant eligibility, and action set. The baseline action
is always available as a fallback. A mismatch invalidates the paired stream. Expanded search is a
separate search-gap arm, never an oracle-only advantage.

## Remnant eligibility

The primary rule retains only exterior-connected residual components with:

- area at least 1% of parent sheet area;
- effective width at least 2% of the short side, tested by a nonempty inward buffer at half that
  width;
- exterior-access length at least 2% of the short side;
- exact material, grade, thickness, surface, and grain compatibility; and
- immutable acyclic lineage with no duplicate inventory use.

Holes remain holes and contribute no material area. Interior void components are scrap.

| Rule | Minimum area | Effective width | Exterior access |
| --- | ---: | ---: | ---: |
| Permissive sensitivity | 0.25% | 0.5% | Exterior touch |
| Primary nominal | 1% | 2% | 2% |
| Conservative sensitivity | 2.5% | 5% | 5% |

Relative thresholds are necessary because the source unit remains physically uninterpreted.

## Failure handling

Blocked source semantics are pre-registered exclusions. Geometry timeout, zero-candidate, worker,
invalid-candidate, and archive failures remain in the task denominator as nonqualifying.

An economic stream with timeout, zero candidates, worker failure, invalid geometry, archive
failure, infeasible demand, missing baseline action, or incomplete replay is invalid. It remains
visible, produces no numeric savings, and prevents a verdict.

One identical retry is allowed only for worker failure or outer timeout. Zero candidates, invalid
evidence, infeasible work, missing actions, and incomplete streams are not retried. Seeds are never
replaced after observing an outcome.

## Statistical and decision rules

The paired stream is the economic unit. Every registered regime and seed is reported, including
failures. Required summaries are mean and median with deterministic 10,000-resample stratified
paired percentile-bootstrap 95% intervals using seed 0; P10; worst-decile mean; positive-stream
fraction with Wilson interval; and top-10-stream and top-10-decision concentration.

Mandatory controls are no-signal demand, common persisted seeds, known-only information ablation,
exact small cases, terminal/remnant sensitivities, ordinary versus expanded search, rollout versus
beam, and strong versus myopic baseline. Green requires beam to match the exact optimum on every
registered small case within accounting tolerance. The scalable oracle is a policy benchmark, not
a mathematical upper bound.

| Band | Frozen rule |
| --- | --- |
| Red | Savings below 1.5%, **or** unknown-future contribution below 0.5 percentage points. |
| Yellow | Not red, and savings 1.5% to below 2.5%, or unknown future 0.5 to below 1.5 points. |
| Green | Savings at least 2.5% **and** unknown future at least 1.5 points, with all supporting gates. |

Supporting green gates are mean immediate sacrifice no greater than 0.5%; ordinary availability at
least 60%; opportunity frequency at least 20%; remnant realization at least 60%; top-10 decisions
no more than 25% of savings; median and lower 95% mean bound above zero; more than 50% positive
streams; positive evidence in at least two corpora; terminal-band invariance; and no-signal mean
from 0% through 0.3%. No-signal above 0.3% through 0.5% requires investigation; above 0.5%, or
materially negative oracle savings despite fallback, invalidates interpretation.

Thresholds cannot be lowered or waived after evaluation begins. Any semantic change creates a new
contract version and experiment ID while retaining old artifacts.

## Frozen pure-geometry calibration protocol

This protocol tests only whether a useful immediate geometric action space exists.

### Population and split

- Exactly 254 ruleset-v2 eligible tasks.
- Tasks `4365` and `25801` remain blocked for `contains_non_s1_constraints`.
- 185 tasks are source flip-bearing.
- `source_as_recorded` is primary; `force_flip_x_zero` is a matched sensitivity on every eligible
  flip task and never enters primary.

The validator derives these sets from the catalog. Task `13958` retains the exact degenerate-`s1`
assumption. Flip tasks retain the local-x-negation assumption; the sensitivity adds the no-flip
intervention code.

Eligible IDs are ranked by SHA-256 of `salt:catalog_sha256:tasks_index`, using salt
`yieldforge.pure-geometry.split.v1`. The lowest 51 are calibration and the remaining 203 are
evaluation. Source partition flags are unused because all catalog tasks are marked train. A second
salt preselects 20 evaluation tasks for identical-run repeatability.

### Solver budget

Frozen settings are seeds `[0, 1, 2, 3]`, one worker, no early termination, and default separation.
Calibration runs 1, 3, and 10 seconds per seed. It chooses the smallest duration that, relative to
10 seconds, is within 2 percentage points on qualifying-task rate, within 0.1% on median best
length, within 0.5% on P95 best length, and at least 95% valid archives; otherwise it chooses 10.

Expanded search uses seeds `0` through `15` with the selected seconds and identical settings. The
outer timeout is `max(60 seconds, 3 × selected seconds)` with one identical retry only for worker
failure or outer timeout.

### Candidate identity and outcome

Gap is `(used length - best ordinary used length) / best ordinary used length`. The primary
envelope is 0.5%; diagnostics retain 0%, 0.1%, 0.25%, 0.5%, 1%, and 2%.

Accepted candidates require a feasible Spyrrow report, finite complete placements, valid part IDs,
a verified archive, and used length within the fixed sheet plus `1e-9`. Identity is task plus
projection hash plus the existing exact candidate ID. Placement order is ignored; any exact
rotation or position change is distinct. Tolerance clustering is diagnostic only; residual
equivalence belongs to M3.

The primary outcome is the proportion of all 203 evaluation tasks with at least two canonically
distinct accepted ordinary source candidates within 0.5% of that task's best ordinary candidate.
Every failure counts as no. The Wilson 95% interval is descriptive for this bounded population.

Supporting results cover every envelope, counts, best length/density, gaps, seed contribution,
repeatability, concentration, runtime, failures, projection sensitivity, and predeclared strata:
flip presence; part counts `27-30/31-32/33-36/37-40`; unique shapes `3-6/7/8-9/10-18`; maximum
orientation states `1/2`; and sheet-aspect quartile.

| Decision | Frozen rule |
| --- | --- |
| Proceed to M3 | Primary at least 60% and valid archives at least 95%. |
| Redesign | Primary 40% to below 60%, validity below 95%, or expanded search rescues ordinary. |
| Stop Spyrrow path | Ordinary below 40% and expanded remains below 60%. |

A positive result may claim only that a sufficiently rich near-tied geometric action space exists.
It cannot claim remnant utility, savings, optimality, physical recovery, or commercial value.

## Validate and continue

From `yf/`:

```bash
uv run yieldforge experiments validate \
  --m0 experiments/m0-contract-v1.json \
  --geometry experiments/pure-geometry-calibration-v1.json \
  --catalog datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json \
  --catalog-manifest datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json
```

The immutable v1 artifact still reports `confirmation=disabled` because it is the preregistration
input. The committed calibration result and protocol v2 record the observed handoff without
rewriting v1.

The next experiment is the 203-task confirmatory geometry evaluation under `source_as_recorded`,
seeds `[0, 1, 2, 3]`, and 10 seconds per seed. It may not change the population, split, seeds,
0.5% envelope, metrics, or gates.
