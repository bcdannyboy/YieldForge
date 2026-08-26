# M8 Distributed-Exact Calibration Gate Design

## Decision

Run the six independent M8 calibration cells concurrently in owned CPU processes. Generate the
complete proof batch for each cell in one process, close that process pool, then verify each batch in
a fresh process pool. Freeze the sampled-reference audit from those proofs, then execute certificate
generation and checking as fresh per-regime batches. Execute the brute reference as 12 fresh
single-action tasks on the same six-process ceiling so each independent branch receives its own
unchanged deadline. Every method receives the identical frozen action membership; reassemble the
reference actions into the six regime vectors before exact coverage, identity, checker, and audit
reconciliation can pass.

This is the selected first distributed step because it reduces elapsed time without changing an
action catalog, future horizon, collision predicate, proof hash, or policy rule. It also avoids
duplicating each cell's expensive common-transition geometry across action shards. Branch/action
sharding remains a measured fallback if the slowest single cell is still operationally unacceptable.

## Alternatives considered

1. **Cell-level process isolation (selected).** Six cells naturally saturate six CPU processes and
   preserve one common path per cell. The held-out population has enough streams to saturate all
   eight configured workers later.
2. **Action shards within every cell.** This was measured for certificate generation and rejected.
   Even
   with slow-first scheduling and a full per-task window, one isolated certificate action exceeded
   30 minutes under eight-process contention because every shard recomputed the same common path.
   Per-regime certificate/checker batches remove that duplication and reduce pressure to six
   simultaneous processes. Single-action tasks are retained only for the brute reference after its
   two sequential branches exceeded one task deadline; reference branches do not share a common
   certificate path.
3. **Threads or GPU execution.** The workload is branch-heavy Python and authoritative GEOS/Jagua
   geometry, not a uniform numerical kernel. Neither route is justified before measuring processes.

## Contract and timing

The unpublished single-process v2 result contract becomes a distributed v3 contract. It records the
fixed eight-worker configuration, the maximum number of processes actually exercised, generator
wall time, independent-checker wall time, full distributed pipeline wall time, and total
calibration-gate wall time. The calibration gate exercises at most six processes: one regime per
certificate/checker task and up to six independent reference actions. The held-out population can
use all eight later. Per-cell elapsed values remain worker work-time evidence; held-out projection
uses observed distributed pipeline wall time and does not divide by an unmeasured worker count.

The audit records per-regime certificate/checker batch elapsed time and sums the two independently
measured brute-reference action times for that regime. This topology is intentionally asymmetric:
the certificate method is an event-major batch algorithm, while the reference semantic oracle is
independent branch replay. Both operation timers start only after fresh request construction and end
after proof-operation cleanup, both receive identical frozen actions, and all phases use the same
six-process ceiling. The hard sampled speedup therefore compares measured method work, not phase
wall time or process startup. Audit wall time is the sum of the three observed phase walls; total
wall time remains at least generator, checker, and audit wall time combined.

Every cell must still contain the exact sorted current action vector and the exact sorted proof
action vector. Reassembly rejects missing cells, missing or duplicate actions, mismatched proof
runtime identities, invalid proofs, or sampled-reference mismatches. A worker exception cancels the
gate and publishes no artifact. Every started worker task receives a frozen 30-minute execution
window beginning only after its process-group handshake; queued tasks do not lose runtime merely
because every slot is occupied. Failure, timeout, or interruption terminates every process owned by
that phase before control returns. Audit batches are launched longest-regime-first using the already
observed full-generator worker times, after the audit action set is frozen. Scheduling can reduce
wall time but cannot change membership, per-action evidence, or work-time comparisons.

The split was selected after the first distributed run completed all six generators and all six
fresh checkers but the former combined audit phase reached its 1,800-second bound. A subsequent run
showed that a single phase-level deadline was incorrect for 12 tasks on eight slots: generation and
checking again completed, then the queued second audit wave lost part of its execution window. The
per-started-task deadline fixes that scheduling artifact without relaxing the 30-minute bound for
any action. A third run then showed that action isolation itself duplicated enough common geometry
for one task to exceed the unchanged bound. Per-regime batching is therefore retained for certificate
generation and checking because it shares their common path and lowers simultaneous CPU load. It was
also tested for reference scoring before the reference-only boundary below was observed.

The first matched per-regime execution cleared certificate generation and checking but localized a
remaining reference-only limit. Full generation completed in `1623.227121` seconds and full checking
in `1534.10437` seconds. The 12-action audit generator completed in `1400.985884` seconds and its
fresh checker in `1485.550346` seconds. The brute reference then reached one worker's unchanged
1,800-second limit; four heavy regime workers were still active at about 29 minutes, so no artifact
was published. This validates the six-batch topology while rejecting sequential whole-suffix replay
inside a heavy reference batch.

The selected exact correction advances the two independent reference branches event-major within
each regime batch. Every branch retains its own cursor, exact frozen-policy decision, branch-local
fit cache, and terminal accounting. Only prepared geometry and content-keyed caches are shared at
the same event before the bounded prepared-layout cache can evict them. The independent reference
does not consume certificate witnesses or checker conclusions, frozen action membership and the
six-process ceiling are unchanged, and repeated single-branch replay remains the semantic oracle in
the finite differential suite.

The event-major canonical rerun preserved every upstream result but the reference still reached the
same fixed limit. Full generation/checking completed in `1517.683438`/`1516.867955` seconds and the
audit generator/checker in `1389.675381`/`1469.414074` seconds. Event-major ordering alone therefore
does not reduce a two-branch reference batch below one task's limit. The exact correction makes one
brute reference action the task boundary, launches the 12 frozen actions slow-regime-first with six
simultaneous workers, and gives every started branch its own unchanged 1,800-second window. Scores
are reassembled into the six frozen per-regime action vectors before audit reconciliation; per-cell
reference work time is the sum of its two independent branch times.

The corrected canonical run completed. It published strict artifact
`yfm8proof-b296ba919c07d55ece14c6db` after `8298.220836` wall seconds, with 3,469/3,469 valid proofs,
zero full-checker failures, zero sampled-checker failures, and zero mismatches across the 12 exact
reference actions. This validates the independent-action scheduling correction and closes the
previous reference timeout.

The resulting hard decision is `redesign_certificate_proof`. The calibration boundary produced no
`exact_transition` or `policy_dominated` witness, sampled certificate-plus-checker work was only
`1.123571x` faster than reference, and the held-out projection was `127.766536` days. Further work
must address coverage and repeated common proof/check work; threshold relaxation and evaluation
access are not authorized.

## Isolation and claim boundary

Generator and checker pools never share mutable runtimes or private proof capabilities. Each worker
reconstructs its runtime from frozen calibration inputs. Evaluation partitions remain unopened. A
passing distributed calibration gate is software evidence about exactness and runtime feasibility;
it is not an M8 savings result, physical validation, or commercial evidence.
