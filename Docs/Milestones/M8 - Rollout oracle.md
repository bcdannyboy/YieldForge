# M8 — Rollout oracle

**Status:** First go/no-go complete — exactness passed; certificate coverage/runtime redesign required

M8 measures the value of knowing the realized future when choosing today's action. For each current candidate, the oracle executes it virtually and replays the remainder under the frozen strong baseline.

> **Question:** Does future demand information change today's best nesting decision enough to matter?

## Acceptance boundary

Hand-computed toy cases, information-isolation tests, paired candidate checks, and no-signal sanity cases pass.

Rollout is not a mathematical upper bound and may miss value requiring coordinated future decisions.

## Entry state

- Frozen baseline: `yfm7freeze-5c13c3fe531828d8cd986c39`.
- Baseline evaluation: `yfm7eval-f2cb310c4b7e879d119e8f94`, 36 streams and 864 events reproduced
  twice with identical content.
- The rollout arm must receive the same candidate/action evidence while future access remains
  isolated from the baseline arm.
- No oracle advantage or paired savings estimate exists. The certificate generator, independent
  checker, exhaustive differential kernel, and calibration-only v3 gate are implemented; the first
  gate artifact is complete and evaluation remains sealed.

## Planning boundary

The approved design uses the complete remaining registered stream for every current action, frozen
M7 for each hypothetical continuation, the exact common action set, the M7 action as the tie-preferred
fallback, M0 scrap-only terminal treatment, and strict paired failure handling. Naive branch-by-branch
replay is operationally rejected. The primary engine compiles inventory-independent M7 standard
winners, evaluates exact inventory deltas, skips only intervals covered by complete no-fit
certificates, and uses registered exact search for every survivor.

The implementation plan begins by extracting an arbitrary-state M7 transition seam and proving that
published M7 identities do not change. A slow full-replay reference then audits the sparse engine on
toys and registered calibration prefixes. Zero mismatch, at least 20x speedup, and a conservative
held-out projection no greater than seven days are hard gates before persistent caching, the
six-stream pilot, or M8 evaluation.

See [[plans/2026-08-24-m8-rollout-oracle-design]] and
[[plans/2026-08-24-m8-rollout-oracle-implementation]].

## First go/no-go execution — 2026-08-25

The certificate kernel is exact on the implemented evidence boundary: 45 exhaustive finite cases
produced 140 proofs and 270 witnesses with zero reference mismatches. The latest runtime changes are
committed through `fbfa062`; 314 baseline/oracle tests and 222 directly affected tests passed, with
independent adversarial review of proof commitments, cursor provenance, capability cleanup, and
generator/checker separation.

The canonical calibration-only execution then established a single-process runtime no-go:

- candidate verification completed for all eight required problems;
- `no_signal` completed 428 full action proofs in `163.15911` seconds, including cyclic-GC cleanup;
- `exact_recurrence` remained inside the exact common-transition remnant geometry search after more
  than 480 seconds; the run was stopped rather than spending the same unbounded local path across all
  six cells; and
- no M8 result artifact was published and `evaluation_partition_opened` remained false.

This is a runtime-feasibility decision, not a semantic failure and not an M8 savings result. The
complete three-way v2 artifact was not emitted because the exactness/audit phases after the six full
generators were not reached. The next authorized branch is distributed exact execution: shard the
six calibration cells and, where necessary, exact branch/action work while preserving the frozen
action catalog, proof identities, independent checker, sampled reference audit, and single final
projection calculation. Further local threshold changes or evaluation access remain prohibited.

## First distributed execution — 2026-08-25

The cell-sharded exact generator completed all six regimes in `1523.938529` wall seconds and emitted
3,469 proofs. A fresh cell-sharded checker then checked all 3,469 proofs in `1491.160716` wall seconds
without reporting a checker failure. The pre-timing audit freeze selected 12 actions with sample hash
`sha256:2ee9b5d22261c7bf6d7cb5115bdccc329016fb58af5723ab2663792e0215adb1`.

The former combined audit phase reached its fixed 1,800-second deadline, terminated its owned worker
groups, and published no artifact. This localizes the remaining runtime problem to audit scheduling,
not full certificate generation or independent checking. The implementation now runs the frozen 12
actions through separate certificate, checker, and brute-force reference phases; every phase uses
the same action keys and up to eight processes, and deterministic assembly rejects any missing or
duplicate action. The next action is a canonical calibration rerun. Evaluation remains sealed, so
there is still no M8 oracle-advantage or savings result.

The first split-audit rerun again completed all 3,469 generators and all 3,469 fresh checks. Generator
wall time was `1573.015449` seconds and checker wall time was `1578.396955` seconds; the same 12-action
audit sample and SHA-256 reproduced. The action-level certificate phase then reached 1,800 seconds
because its timer covered the entire 12-task queue on eight slots, shortchanging the four queued
actions. No artifact was published. The supervisor now applies the unchanged 1,800-second limit to
each task from its confirmed start and schedules the measured slow regimes first. This is a process
scheduling correction, not a semantic or threshold change.

The slow-first per-task rerun completed generation in `1590.819549` wall seconds and all 3,469 fresh
checks in `1561.136582` wall seconds, again reproducing the identical audit sample. One isolated
certificate action then exceeded its own 1,800-second window. This demonstrates that action
isolation duplicated the expensive regime common path and increased eight-process CPU contention;
it does not indicate a proof mismatch. No artifact was published. The matched audit now uses one
batch per regime for certificate, checker, and reference, sharing common geometry and exercising six
processes. Evaluation remains sealed and no M8 advantage or savings result exists.

The matched per-regime rerun generated all 3,469 proofs in `1623.227121` wall seconds and checked all
3,469 in `1534.10437` seconds without a reported failure. It reproduced the same 12-action audit
hash. The per-regime audit generator then completed all 12 actions in `1400.985884` seconds and its
fresh checker completed them in `1485.550346` seconds, clearing the phase that previously failed.
The final brute reference reached one worker's unchanged 1,800-second limit while four heavy regime
workers were still active near 29 minutes. Cleanup completed, and no partial artifact was published.

The remaining runtime boundary is therefore reference ordering, not certificate generation,
checking, frozen sample identity, or process supervision. The reference batch now advances its two
independent exact branches event-major so same-event prepared geometry remains reusable, while each
branch retains its own cursor and frozen M7 decisions. The finite differential suite must remain
equal to repeated isolated single-action replay. Evaluation remains sealed.

The event-major canonical rerun again reproduced the same 12-action audit hash. It completed all
3,469 generators in `1517.683438` seconds, all 3,469 fresh checks in `1516.867955` seconds, the audit
generator in `1389.675381` seconds, and the audit checker in `1469.414074` seconds. The final
event-major reference still reached one worker's unchanged 1,800-second limit. Cleanup completed and
no artifact was published. Event ordering alone is therefore insufficient.

The next exact correction executes each of the 12 frozen brute-reference actions as its own fresh
task, capped at the six already measured audit processes and launched slow-regime-first. Each branch
gets the unchanged 1,800-second window from its confirmed start, then exact scores are restored into
their frozen two-action regime vectors before audit reconciliation. Generator and checker batches
remain per-regime because they already pass and benefit from shared common geometry. Evaluation
remains sealed.

## Completed first go/no-go — 2026-08-25

The independent-action reference correction completed the full canonical gate and published
`yf/experiments/results/m8-certificate-proof-yfm8proof-b296ba919c07d55ece14c6db.json`. Its strict
identity is `yfm8proof-b296ba919c07d55ece14c6db` with content hash
`sha256:b296ba919c07d55ece14c6dbb6ecbce1aa4a24e612dd1a251757e7a3b739739d`.

The semantic and audit evidence passed:

- all six calibration cells completed in `distributed_exact` mode;
- 3,469/3,469 current actions produced valid proofs and a fresh checker reported zero failures;
- the frozen 12-action audit reproduced with zero sampled-checker failures and zero brute-reference
  mismatches;
- all six regimes, registered action kinds, and registered future-event counts were covered;
- six `state_rejoin` witnesses and 3,463 certified future events were recorded; and
- evaluation remained unopened.

The hard gate returned `redesign_certificate_proof`, not `pass_certificate_exact`:

- only `no_fit` and `state_rejoin` occurred, so required `exact_transition` and
  `policy_dominated` witness classes were absent and `exact_escape_count` was zero;
- sampled certificate-plus-checker work took `10354.963529` process-seconds versus
  `11634.541856` process-seconds for exact reference, only `1.123571x` faster than the registered
  `20x` minimum; and
- measured certificate throughput projected the 550,542 held-out actions to `127.766536` calendar
  days, above the seven-day boundary.

Generator wall time was `1501.688347` seconds, fresh-checker wall time was `1522.555951` seconds,
audit wall time was `5202.790937` seconds, and total wall time was `8298.220836` seconds. The runner
honored the six-process measured ceiling despite the configured eight-worker envelope.

This result closes the first M8 go/no-go with a precise no-go for the present certificate
architecture. It is not a failure of exact semantics: the independent checker and sampled brute
reference both agree. The next bounded work is to add calibration-only cases that exercise the two
missing witness classes and to eliminate repeated common certificate/checker work until the same
hard gate reaches 20x and projects within seven days. Evaluation, savings claims, physical claims,
and commercial claims remain out of scope.
