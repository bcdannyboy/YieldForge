# M0 experiment-contract design

**Approved:** 2026-08-22  
**Status:** Approved for implementation  
**Scope:** Executable M0 constitution and pre-registered pure-geometry calibration protocol

## Decision boundary

This design freezes what can count as evidence before confirmatory results exist. It does not
claim that M0 has passed, that a useful candidate population exists, that residual geometry is
useful, or that YieldForge saves material. Calibration may select only the practical frozen
Spyrrow time budget. Confirmatory geometry evaluation remains prohibited until that selection is
published in a new validated protocol artifact.

The implementation uses two small, content-addressed JSON artifacts and strict Python validation:

- one M0 economic constitution;
- one pure-geometry calibration protocol that references the M0 contract and the pinned Lectra
  catalog;
- one narrow validator following the repository's immutable Pydantic and canonical-JSON
  conventions.

A monolithic contract was rejected because it would couple later economic amendments to the
immediate geometry protocol. Prose-only or JSON-Schema-only validation was rejected because it
would not provide the cross-artifact and catalog-bound checks already customary in the repository.

## M0 economic constitution

### Primary outcome and accounting

For policy `P`:

`NetCost(P) = purchases + storage + return handling + retrieval handling - realized scrap proceeds - terminal scrap credit`

`OracleSavings = (BaselineCost - FullOracleCost) / BaselineCost`

The denominator must be positive. A full sheet incurs its full acquisition cost when opened.
Storage accrues on retained remnant area over half-open time intervals. Return handling accrues
when an eligible remnant enters inventory; retrieval handling accrues when it leaves. Scrap
proceeds accrue only when material is actually scrapped. Kerf and clearance affect geometry and
therefore purchases, but are not a separate monetary term.

Cutting labor and time, energy, tool wear, setup, lateness, financing, tax, overhead, purchasing
lead time, and stockout costs are excluded because the current program has no source-backed model
for them. Later economic benchmark manifests must freeze exact nonnegative rates and units before
evaluation.

The primary terminal rule liquidates every remaining remnant at scrap value with no option value
beyond scrap. Zero total terminal credit and bounded continuation credit no greater than pro-rata
virgin value are mandatory sensitivities. A green conclusion may not depend on continuation
credit and may not reverse between scrap-only and zero-credit treatments.

### Comparator and information attribution

The strongest commercial as-of-time baseline is selected on calibration only and then frozen. It
may use released work and firmly scheduled work known at the decision timestamp. A known-only
oracle control uses the same rollout or beam implementation and compute as the full oracle, but
masks events not known as of that timestamp. The full oracle sees the realized future.

`UnknownFutureContribution = (KnownOnlyOracleCost - FullOracleCost) / BaselineCost`

This same-algorithm information ablation separates future-information value from a better search
method. The myopic policy sees only the released batch, current inventory, and immediate candidate
facts. The commercial policy additionally sees current age, regularity, and cost state. The
known-order baseline sees released and firmly scheduled work but cannot cut future work early.

### Event timing

At timestamp `t`, the simulator:

1. accrues storage from the prior timestamp to `t`;
2. reveals orders with `release_at = t`;
3. co-nests all compatible work released at `t`;
4. chooses and immediately executes an action that fulfills that work;
5. records purchase, handling, and scrap events; and
6. returns eligible resulting remnants to inventory at `t`.

Firmly scheduled work with `known_at <= t` may inform planning but cannot be advanced. The horizon
closes after the final registered action and storage accrual to an explicit `horizon_end`, followed
by terminal liquidation.

### Candidate parity

Every baseline and oracle uses identical verified candidate-archive hashes, source projection,
solver seeds and configuration, ordinary compute budget, stock/remnant eligibility, and feasible
action set. The baseline action remains available as a fallback. Expanded search is a separately
reported search-gap arm and is never an oracle-only advantage.

### Remnant eligibility

The primary operational rule retains only exterior-connected residual components satisfying all
of these requirements:

- area at least 1% of the parent standard-sheet area;
- effective width at least 2% of the sheet short side, evaluated by a nonempty inward buffer at
  half that width;
- exterior-access length at least 2% of the sheet short side;
- exact material, grade, thickness, surface, and grain compatibility; and
- immutable, acyclic lineage with no duplicate inventory use.

Holes are preserved exactly and excluded from material area. Interior void components are scrap.
The required sensitivity grid is permissive `0.25% / 0.5% / exterior touch` and conservative
`2.5% / 5% / 5%` for area, effective width, and access respectively. Relative thresholds are used
because the current source unit is preserved literally but is not physically interpreted.

### Failures

Pre-registered blocked source tasks are exclusions, not missing observations. Geometry
zero-candidate, invalid-candidate, and archive failures remain in the task denominator as
nonqualifying. An economic stream with missing actions, infeasible demand, archive-integrity
failure, or incomplete replay is invalid and prevents a confirmatory verdict; it remains visible
in the registered-stream report.

One identical retry is allowed only for transient worker failure or an outer timeout. Zero
candidates, invalid geometry, archive rejection, and outcome-based seed replacement are never
retried.

### Statistical and decision rules

The paired stream is the economic analysis unit. Every registered regime and seed is reported.
The report includes mean and median paired savings with deterministic 10,000-resample stratified
percentile-bootstrap 95% intervals using seed 0; P10 and worst-decile mean; positive-stream
fraction with a Wilson interval; and top-10-stream and top-10-decision concentration.

Mandatory controls include a no-signal regime, common persisted seeds, the known-only information
ablation, registered sensitivities and ablations, and exact enumeration on registered small
cases. A green result requires beam search to match the exact optimum on every registered small
case within accounting tolerance.

The economic bands are:

- red if target savings are below 1.5% or unknown-future contribution is below 0.5 percentage
  points;
- yellow if not red and target savings are 1.5% to below 2.5%, or unknown-future contribution is
  0.5 to below 1.5 percentage points; and
- green only at target savings of at least 2.5% and unknown-future contribution of at least 1.5
  percentage points, with every supporting gate passing.

Supporting green gates are mean immediate sacrifice no greater than 0.5%, ordinary candidate
availability at least 60%, opportunity frequency at least 20%, remnant realization at least 60%,
top-10-decision concentration no greater than 25%, median savings above zero, lower 95% mean bound
above zero, positive-stream fraction above 50%, positive evidence in more than one geometry
corpus, terminal-band invariance, and a no-signal mean from 0% through 0.3%. A no-signal mean above
0.5%, or a materially negative oracle saving despite the baseline fallback, invalidates
interpretation; above 0.3% through 0.5% requires investigation.

Thresholds cannot be lowered or waived after evaluation begins. Any change creates a new contract
version and experiment identity while preserving old artifacts.

## Pure-geometry protocol

### Population and split

The population is the 254 ruleset-v2 tasks classified as runnable with explicit assumptions.
Tasks `4365` and `25801` remain blocked because of non-`s1` constraints. The protocol binds to the
committed catalog content hash.

The calibration set contains 51 tasks and the evaluation set contains 203. Eligible task IDs are
ranked by SHA-256 of a fixed protocol salt, the catalog hash, and the task ID; the lowest 51 hashes
are calibration. Both exact lists are persisted before solving. Source partition flags are not
used because every catalog task is marked as train.

### Projection arms

`source_as_recorded` is primary. Every flip-bearing task also receives a matched
`force_flip_x_zero` sensitivity with the exact source assumptions and intervention acknowledgement.
The sensitivity never enters the primary denominator.

### Calibration and budgets

Ordinary seeds are `[0, 1, 2, 3]`, with one worker, no early termination, and default separation.
Calibration evaluates 1, 3, and 10 seconds per seed. The smallest time is selected when, relative
to 10 seconds, its primary qualifying-task rate is within 2 percentage points, median best-length
degradation is at most 0.1%, P95 degradation is at most 0.5%, and valid-archive rate is at least
95%. Otherwise 10 seconds is selected.

Expanded search uses seeds 0 through 15 and the same selected seconds per seed and all other
settings. It is only a search-gap arm. The outer timeout is the greater of 60 seconds and three
times the configured solver seconds. One identical retry is allowed only for worker failure or
outer timeout.

### Candidate definition

The primary immediate-performance envelope is a 0.5% used-length gap from the best ordinary
candidate for the task. The full diagnostic grid is 0%, 0.1%, 0.25%, 0.5%, 1%, and 2%.

Accepted candidates must have a Spyrrow feasible report type, finite and complete placements,
valid part-instance identities, a verified immutable archive, and used length no greater than the
fixed sheet length plus the existing `1e-9` adapter tolerance. Canonical identity is the task,
projection hash, and existing exact candidate ID, which hashes exact used length and exact
placement transforms after sorting placements by part ID. Placement reporting order does not
create a difference; any exact rotation or position change does. Tolerance clustering and residual
equivalence are not candidate identity and are deferred to M3.

Supporting diversity diagnostics include normalized positional RMS, rotation difference,
seed-exclusive contribution, multi-seed contribution, leave-one-seed-out qualification agreement,
and best-length spread. A pre-registered 20-task evaluation subset is rerun identically for archive
Jaccard, best-length delta, and pass-status repeatability.

### Outcome and interpretation

The primary geometry outcome is the proportion of all 203 evaluation tasks for which ordinary
source-recorded archives contain at least two canonically distinct accepted candidates within
0.5% of that task's best ordinary candidate. Every failure counts as nonqualifying. A Wilson 95%
interval is descriptive rather than a claim about a universal population.

Supporting reporting covers every envelope, feasible and deduplicated counts, best used length and
density, gap distribution, multi-seed contribution, repeatability, concentration, runtime,
failures, and the matched projection sensitivity. Predeclared strata are source flip presence;
part-count bands `27-30`, `31-32`, `33-36`, and `37-40`; unique-shape bands `3-6`, `7`, `8-9`, and
`10-18`; maximum orientation states `1` or `2`; and sheet-aspect quartile.

Proceed to M3 when the primary proportion is at least 60% and valid-archive rate is at least 95%.
Redesign the solver or protocol at 40% to below 60%, below 95% archive validity, or when expanded
search reaches 60% but ordinary search does not. Stop the current Spyrrow path when ordinary is
below 40% and expanded search remains below 60%.

A positive result permits only the conclusion that a sufficiently rich near-tied geometric action
space exists. It cannot establish residual diversity, remnant utility, material savings, solver
optimality, physical feasibility, or commercial value.

## Validation and files

Implementation will add:

- `yf/experiments/m0-contract-v1.json`;
- `yf/experiments/pure-geometry-calibration-v1.json`;
- strict, immutable experiment models and a small validation entry point under
  `yf/src/yieldforge/experiments/`; and
- focused contract tests under `yf/tests/experiments/`.

Validation will reject duplicate JSON keys, noncanonical bytes, unknown fields, nonfinite values,
content-identity drift, catalog mismatch, eligibility drift, split overlap or omissions,
inconsistent projection arms, unordered thresholds, illegal retries, and cross-contract mismatch.
The calibration manifest has confirmation disabled. M0 remains Active until calibration selects
and freezes seconds per seed in a new validated protocol version.
