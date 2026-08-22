# M2 API-orchestrated geometry calibration design

**Approved:** 2026-08-22
**Status:** Approved for implementation and registered execution
**Scope:** The 51-task solver-budget calibration registered by
`yf/experiments/pure-geometry-calibration-v1.json`

## Decision boundary

This design executes only the calibration population already fixed by protocol
`yfgp-49906e93ed9ff0446705247b`. It may select a practical seconds-per-seed value from
`1`, `3`, and `10`. It may not change the population, projection, seeds, solver settings,
near-tie envelope, metrics, failure treatment, thresholds, or claim ceiling.

The 203 evaluation tasks remain prohibited. The run does not calculate residual geometry,
remnant utility, material savings, solver optimality, or commercial value.

## Chosen approach

Use the existing FastAPI workbench as the execution authority and add a narrow sequential API
orchestrator. The server continues to own source-task projection, subprocess supervision, durable
job history, and immutable candidate archives. The orchestrator owns protocol validation, exact
cell enumeration, submission persistence, retry decisions, resume, candidate collection, and the
calibration result.

This was selected over two alternatives:

- Directly invoking the adapter would bypass the existing server-owned job and archive evidence.
- An ad hoc shell loop around the single-problem CLI would not provide protocol binding, safe
  resume, registered retry handling, or a content-addressed result.

## Execution environment

Run a dedicated local API against the committed 256-task catalog and a new ignored workbench root
under `yf/var/experiments/`. The API process and the orchestrator use that root only for this
calibration. Existing interactive job history is not reused.

The current API outer-runtime ceiling is 10 seconds. Extend that validation ceiling to 60 seconds
without changing the solver budget itself. Every calibration request uses:

- projection `source_as_recorded`;
- the task's exact server-owned assumption codes;
- no intervention codes;
- one of seeds `[0, 1, 2, 3]`;
- one of solver durations `[1, 3, 10]` seconds;
- one solver worker;
- no early termination;
- default separation; and
- a 60-second outer runtime.

Submit only one job at a time so simultaneous solver processes cannot compete for the registered
wall-time budget.

## Registered cells and persistence

The run contains exactly `51 tasks × 4 seeds × 3 durations = 612` registered cells before any
allowed retry. Cell identity is the parent protocol ID, task ID, projection, seed, and solver
duration.

For every cell the orchestrator:

1. writes the intended request identity before submission;
2. submits it through `POST /api/solver-jobs`;
3. writes the returned job ID before polling;
4. waits for a terminal job view;
5. for a completed job, retrieves all paginated candidate summaries and the completed-run archive
   identity; and
6. writes one terminal attempt record.

Writes are atomic and refuse destructive replacement. If execution is interrupted, resume first
reattaches to any recorded job ID. It never submits a second job merely because the orchestrator
lost its polling connection.

The committed result manifest records each registered cell, all attempt identities and terminal
states, exact archive hashes for completed attempts, candidate IDs, widths, densities, and the
selected terminal attempt. Large workbench archives remain local and ignored, but every archive
is verified by the API before its identity can enter the result.

## Failure and retry rules

One identical retry is allowed only after these terminal conditions:

- job status `timed_out`; or
- failure code `worker_spawn`, `worker_protocol`, `solver_failure`, or `supervisor_failure`.

The following never retry:

- a completed archive with zero candidates;
- `archive_failure`;
- cancellation;
- malformed, missing, or inconsistent API evidence; or
- any failure not explicitly classified above.

Every failure remains in the registered denominator. API unavailability before submission is an
orchestration interruption rather than an experimental attempt; after submission, the persisted
job ID remains authoritative.

## Calibration summaries

For each task and duration, pool the four seed archives after exact candidate-ID deduplication.
The best accepted width is the minimum pooled width. A task qualifies when at least two distinct
accepted candidate IDs are no more than 0.5% above that duration's best width.

Valid-archive rate is calculated over all 204 registered task/seed cells at a duration after the
single allowed retry. A completed, verified empty archive is valid archive evidence but makes no
candidate contribution and does not qualify the task.

Compare each shorter duration with the 10-second reference:

- qualifying-rate gap is the absolute percentage-point difference across all 51 tasks;
- per-task best-length degradation is
  `max(0, (short_best - reference_best) / reference_best × 100)`;
- a missing shorter-duration best has infinite degradation;
- a task with no 10-second best invalidates calibration and leaves confirmation disabled;
- median is the ordinary median over all 51 task degradations; and
- P95 is the nearest-rank order statistic at `ceil(0.95 × 51)`.

Select the smallest registered duration whose qualifying-rate gap is at most 2 percentage points,
median degradation is at most 0.1%, P95 degradation is at most 0.5%, and valid-archive rate is at
least 95%. If neither 1 nor 3 seconds satisfies every condition, select 10 seconds, provided the
reference is valid. Missing reference evidence prevents selection rather than silently choosing a
budget.

## Result and confirmation protocol

The calibration result is strict, canonical JSON with a semantic SHA-256 and an ID derived from
that digest. It binds:

- the M0 and parent geometry protocol identities;
- the catalog identity;
- the exact registered cells and attempts;
- every aggregate used by the selector;
- the selected duration or explicit invalid status; and
- the bounded claim that this is solver-budget calibration, not confirmatory geometry evidence.

If calibration is valid, publish a new content-addressed geometry protocol. It preserves every
frozen v1 rule, adds the calibration-result identity, sets only the selected seconds per seed,
changes status to confirmation-ready, and enables confirmation. Validation must reject any other
drift. If calibration is invalid, publish the result but keep the v1 protocol and M0 Active.

## Interfaces

Add a CLI command shaped as:

```bash
yieldforge experiments calibrate-geometry-api \
  --m0 experiments/m0-contract-v1.json \
  --geometry experiments/pure-geometry-calibration-v1.json \
  --catalog datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json \
  --catalog-manifest datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json \
  --api-origin http://127.0.0.1:8000 \
  --output var/experiments/yfgp-49906e93ed9ff0446705247b
```

The command validates the complete bundle and the API corpus identity before creating or resuming
state. It refuses any protocol with confirmation already enabled and refuses to enumerate an
evaluation task.

## Verification

Before the registered run:

- unit-test exact cell construction, API request bodies, sequential execution, pagination,
  submission persistence, resume, retry classification, and fail-closed evidence handling;
- unit-test selector boundary and missing-data behavior;
- integration-test the API's 60-second outer-runtime validation while preserving one solver
  worker; and
- run the complete Python, Ruff, frontend contract, typecheck, and production-build gates.

After the run, validate the result against the runtime evidence, then add and test the exact
confirmation-ready protocol if selection succeeded. Browser Playwright is required only if the
interactive browser workflow changes; the orchestrator adds no UI.
