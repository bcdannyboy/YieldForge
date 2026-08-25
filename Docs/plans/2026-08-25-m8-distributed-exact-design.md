# M8 Distributed-Exact Calibration Gate Design

## Decision

Run the six independent M8 calibration cells concurrently in owned CPU processes. Generate the
complete proof batch for each cell in one process, close that process pool, then verify each batch in
a fresh process pool. Freeze the sampled-reference audit from those proofs, then execute its
certificate generator, independent checker, and brute-force reference as three fresh action-level
process phases over the identical frozen `(regime, action_id)` task set. Reassemble the six results
only after exact action coverage, proof identity, checker, and audit reconciliation pass.

This is the selected first distributed step because it reduces elapsed time without changing an
action catalog, future horizon, collision predicate, proof hash, or policy rule. It also avoids
duplicating each cell's expensive common-transition geometry across action shards. Branch/action
sharding remains a measured fallback if the slowest single cell is still operationally unacceptable.

## Alternatives considered

1. **Cell-level process isolation (selected).** Six cells naturally saturate six CPU processes and
   preserve one common path per cell. The held-out population has enough streams to saturate all
   eight configured workers later.
2. **Action shards within every cell.** This can shorten one cell but makes every shard recompute the
   same exact common-transition geometry. Full generator/checker action sharding remains reserved;
   the measured 30-minute audit straggler justified action-level sharding only for the small frozen
   audit, where all three compared methods use the same task topology.
3. **Threads or GPU execution.** The workload is branch-heavy Python and authoritative GEOS/Jagua
   geometry, not a uniform numerical kernel. Neither route is justified before measuring processes.

## Contract and timing

The unpublished single-process v2 result contract becomes a distributed v3 contract. It records the
fixed eight-worker configuration, the maximum number of processes actually exercised, generator wall time,
independent-checker wall time, full distributed pipeline wall time, and total calibration-gate wall
time. Per-cell elapsed values remain worker work-time evidence; held-out projection uses observed
distributed pipeline wall time and does not divide by an unmeasured worker count.

The matched audit sums per-action worker elapsed time separately for certificate generation,
checking, and reference scoring. Each side receives exactly the same frozen action keys and the same
eight-process budget, so the speedup is not inflated by sharding only the slow reference side. Audit
wall time is the sum of the three observed phase walls; total wall time remains at least generator,
checker, and audit wall time combined.

Every cell must still contain the exact sorted current action vector and the exact sorted proof
action vector. Reassembly rejects missing cells, missing or duplicate actions, mismatched proof
runtime identities, invalid proofs, or sampled-reference mismatches. A worker exception cancels the
gate and publishes no artifact. Every started worker task receives a frozen 30-minute execution
window beginning only after its process-group handshake; queued tasks do not lose runtime merely
because all eight slots are occupied. Failure, timeout, or interruption terminates every process
owned by that phase before control returns. Audit tasks are launched longest-regime-first using the
already observed full-generator worker times, after the audit action set is frozen. Scheduling can
reduce wall time but cannot change membership, per-action evidence, or work-time comparisons.

The split was selected after the first distributed run completed all six generators and all six
fresh checkers but the former combined audit phase reached its 1,800-second bound. A subsequent run
showed that a single phase-level deadline was incorrect for 12 tasks on eight slots: generation and
checking again completed, then the queued second audit wave lost part of its execution window. The
per-started-task deadline fixes that scheduling artifact without relaxing the 30-minute bound for
any action.

## Isolation and claim boundary

Generator and checker pools never share mutable runtimes or private proof capabilities. Each worker
reconstructs its runtime from frozen calibration inputs. Evaluation partitions remain unopened. A
passing distributed calibration gate is software evidence about exactness and runtime feasibility;
it is not an M8 savings result, physical validation, or commercial evidence.
