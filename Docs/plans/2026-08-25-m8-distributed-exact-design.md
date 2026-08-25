# M8 Distributed-Exact Calibration Gate Design

## Decision

Run the six independent M8 calibration cells concurrently in owned CPU processes. Generate the
complete proof batch for each cell in one process, close that process pool, then verify each batch in
a fresh process pool. Run the frozen sampled-reference audit in a third fresh pool and reassemble the
six results only after exact action coverage, proof identity, checker, and audit reconciliation pass.

This is the selected first distributed step because it reduces elapsed time without changing an
action catalog, future horizon, collision predicate, proof hash, or policy rule. It also avoids
duplicating each cell's expensive common-transition geometry across action shards. Branch/action
sharding remains a measured fallback if the slowest single cell is still operationally unacceptable.

## Alternatives considered

1. **Cell-level process isolation (selected).** Six cells naturally saturate six CPU processes and
   preserve one common path per cell. The held-out population has enough streams to saturate all
   eight configured workers later.
2. **Action shards within every cell.** This can shorten one cell but makes every shard recompute the
   same exact common-transition geometry. It is reserved for a demonstrated residual straggler.
3. **Threads or GPU execution.** The workload is branch-heavy Python and authoritative GEOS/Jagua
   geometry, not a uniform numerical kernel. Neither route is justified before measuring processes.

## Contract and timing

The unpublished single-process v2 result contract becomes a distributed v3 contract. It records the
fixed eight-worker configuration, the number of processes actually exercised, generator wall time,
independent-checker wall time, full distributed pipeline wall time, and total calibration-gate wall
time. Per-cell elapsed values remain worker work-time evidence; held-out projection uses observed
distributed pipeline wall time and does not divide by an unmeasured worker count.

Every cell must still contain the exact sorted current action vector and the exact sorted proof
action vector. Reassembly rejects missing cells, missing or duplicate actions, mismatched proof
runtime identities, invalid proofs, or sampled-reference mismatches. A worker exception cancels the
gate and publishes no artifact. Each distributed phase has a frozen 30-minute deadline; failure,
timeout, or interruption terminates every process owned by that phase before control returns.

## Isolation and claim boundary

Generator and checker pools never share mutable runtimes or private proof capabilities. Each worker
reconstructs its runtime from frozen calibration inputs. Evaluation partitions remain unopened. A
passing distributed calibration gate is software evidence about exactness and runtime feasibility;
it is not an M8 savings result, physical validation, or commercial evidence.
