# M8 Technical-Pass Goal-Mode Execution Specification

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move YieldForge from the sealed M8 Phase-B performance hold to an official technical M8
certificate pass without changing the frozen semantics, thresholds, checker independence, eight-slot
ceiling, or evaluation boundary. Then prepare—but do not silently authorize—the full-horizon pilot
and held-out evaluation needed to complete the M8 milestone.

**Architecture:** Keep Python as the orchestration, evidence, and fail-closed boundary. First finish
the bounded C0 NumPy frontier batch. Then replace the universal producer/checker hot paths with
separately attested, immutable columnar execution images; independent producer, checker, and
translation-auditor implementations; exact source-faithful geometry; and a streaming codec that
preserves the existing portable bytes. Every optimization is admitted by exact differential evidence
before timing and by a preregistered gate after timing.

**Tech Stack:** Python 3.12, NumPy 2.5.2 CPU arrays, Pydantic strict contracts, Shapely 2.1.2/GEOS,
Spyrrow 0.9.0 and Jagua 0.7.0 where already source-faithful, pytest, Ruff, canonical JSON/SHA-256,
and—only when a measured Python boundary warrants it—a separately attested Rust/native CPU module.

**Required workflow:** Apply `@superpowers:test-driven-development` to every new behavior,
`@superpowers:systematic-debugging` to every unexpected failure, and
`@superpowers:verification-before-completion` before each gate or completion claim. Use read-only
specification and quality reviews after every implementation task. Once reviewed, committed, and
bound by `M8GoalExecutionAuthorityV1`, this document prospectively amends the successor sequencing
in the older M8 plans: a failed narrow C0 arm may proceed only to the separately defined universal
compiled feasibility path. It never reinterprets a historical gate, artifact, or observed result.

---

## 1. What “M8 pass” means

There are five distinct states. They must never be collapsed into one claim.

| State | Meaning | Evaluation state |
|---|---|---|
| C0 pass | The frontier-columnar subpath is exact and fast enough to retain inside a wider engine. | Sealed |
| Phase-C engineering pass | The complete two-probe portable pipeline fits the fully charged `30.261404s` engineering envelope. | Sealed |
| Phase-D authorization pass | Same-session paired reference timing proves the engineering margin and authorizes the official six-cell run. | Sealed |
| Official technical M8 pass | The canonical six-cell certificate gate passes its frozen exactness, coverage, speed, and projection rules. | Sealed |
| M8 milestone completion | A separately bounded full-horizon pilot passes, an explicit authorization artifact opens evaluation, and a valid 36-stream M8 result is published. | Opened only by authorization |

The active goal-mode completion boundary is the **official technical M8 pass**. This is deliberately
a technical-pass goal, matching the active goal objective; it is not a claim that the M8 milestone's
held-out experiment has completed. C0 and Phase C are
intermediate gates. The full-horizon pilot and evaluation work are specified in Sections 16–17 so
the roadmap is complete, but they require a separately created continuation goal and an explicit
authorization artifact. G10 completes only the current technical-pass goal; valid G12 evidence
completes the M8 milestone. Implementing and source-freezing the G11/G12 software in G7A is active-
goal preparation; only executing G11/G12 and opening evaluation are deferred.

A valid final M8 result may show zero, weak, or positive oracle value. Because the exact M7 action
is mandatory and tie-preferred, materially negative `OracleSavings` beyond the declared numeric
tolerance is an integrity failure and makes the result invalid. Engineering can make the
implementation exact and feasible; it cannot engineer a favorable economic result.

## 2. Live recovery point and immutable lineage

### Accepted source state

- Worktree: `/Users/danielbloom/Desktop/YieldForge/.worktrees/m6-temporal-benchmark`
- Package root: `/Users/danielbloom/Desktop/YieldForge/.worktrees/m6-temporal-benchmark/yf`
- Branch: `codex/m8-rollout-preparation`
- Accepted source base at specification creation: `47c1eaf` (the reviewed plan-only commit may
  advance HEAD without accepting the inherited source draft)
- Accepted C0 kernel: `bc3518d`
- Sealed Phase-B evidence: `17a202e`

### Quarantined inherited work

Five files contain an uncommitted Task-2 draft: `certificates.py`, `compiled.py`, `prepared.py`,
`test_compiled.py`, and `test_fact_capture.py`. The 682 added lines are preserved work, not accepted
implementation. A prior worker-reported test run is not gate evidence. Do not use `reset`,
`checkout`, `clean`, `stash`, amend/rebase, branch switching, force operations, broad formatting,
dependency upgrades, unrelated-process termination, or deletion to recover this state. Cleanup is
limited to process groups and temporary files demonstrably owned by the active task.

### Frozen evidence chain

Every successor artifact must bind the exact IDs and content hashes, not merely filenames:

| Evidence | ID | SHA-256 payload |
|---|---|---|
| M0 contract | `yfm0-29b7efe8ac2a0a9995c4f907` | `29b7efe8ac2a0a9995c4f907a56d7ce0cb9b61217b167f0737f6973c648b9a5f` |
| M6 contract | `yfm6-3eeda3f4feb80813807c501a` | `3eeda3f4feb80813807c501ae71299a2add07ed76b75009e2f744daddae5a8aa` |
| M6 population | `yftp-49bd7ce5fd34b2779440c52f` | `49bd7ce5fd34b2779440c52fabdf2acb8ef80f39b025cdf5a9c6f8a1d2c958f9` |
| M7 problem index | `yfm7i-116c24d7fce8ce415d46533e` | `116c24d7fce8ce415d46533e0bcaa05369bc628d066ba2c5d7cdd03f15e38481` |
| M7 freeze | `yfm7freeze-5c13c3fe531828d8cd986c39` | `5c13c3fe531828d8cd986c3980104afebb2d959a2885ed4c3b61de29961bf7a8` |
| M7 calibration view | `yfm7cv-13a41bf4b7e2813608241c1b` | `13a41bf4b7e2813608241c1b503b47c8771c7d51d7c2b42d0d8e99ed0e389fb1` |
| M7 evaluation | `yfm7eval-f2cb310c4b7e879d119e8f94` | `f2cb310c4b7e879d119e8f940d5a3dc88cd4b26d48087b46323b7be848144931` |
| Portable Gate 3 | `yfm8gate3-ea8a12969396172d7dbc4774` | `ea8a12969396172d7dbc4774bd239532e2907e637ddb44b1d5505c7b9011d117` |
| Gate-3 decision | `yfm8g3decision-c13ec320e9fcd02873bf649c` | `c13ec320e9fcd02873bf649c4f8d84a66c48fb5c4a8e67ebf2fb2f5de268b03c` |
| Phase-B profile | `yfm8profile-ffbf978a466f6e98768a7556` | `ffbf978a466f6e98768a7556d223e61bbbca85737b04b845a6da32709ac85e87` |
| Six-cell v3 parent | `yfm8proof-b296ba919c07d55ece14c6db` | `b296ba919c07d55ece14c6dbb6ecbce1aa4a24e612dd1a251757e7a3b739739d` |

Historical evidence remains immutable. New schemas and artifacts get new identities and lineage.

## 3. Frozen semantics and non-negotiable invariants

No optimization may change any of the following:

1. Score every exact current action over the complete remaining registered horizon.
2. Use the exact common action catalog and preserve candidate parity and canonical ordering.
3. Apply the frozen M7 continuation after each hypothetical current action.
4. Prefer the frozen M7 action on exact-cost ties, then lexical action ID.
5. Preserve M0 scrap, remnant, failure, terminal, accounting, and reporting rules bit-for-bit.
6. Preserve current information isolation: only the full-oracle arm sees the realized suffix.
7. Preserve exact material, geometry, fit/search configuration, state, suffix, rate, and policy bindings.
8. Preserve the independent checker. It may share wire types and primitive definitions, but it may
   not call or trust the producer kernel, producer cache, producer capability, or producer conclusion.
9. Preserve the separate translation-count authority. It must not trust Jagua collision masks or
   reuse the generator algorithm as its proof.
10. Preserve the guarded Jagua/Shapely collision contract for every survivor requiring authoritative
    geometry. C0 may bypass collision only where complete scalar no-fit evidence proves that no
    placement can exist; it may not turn collision output into proof authority.
11. Preserve complete portable bytes, root/layer/decision identities, costs, state hashes, and
    classifications for the two official probes unless an explicitly approved new schema says otherwise.
12. Count every actively computing process, native thread, BLAS thread, and descendant against the
    maximum of eight compute slots. An orchestration-only controller is captured but consumes no
    compute lease while quiescent; if it performs compute concurrently, it must acquire a slot.
    Default native/NumPy thread pools to one unless explicitly accounted.
13. Treat malformed membership, identity, ordering, or capability drift as an integrity error.
    `unsupported` is reserved for a legitimately incomplete representation, and is always counted.
14. Keep exact fallback explicit and counted. The official two-probe path requires zero fallback.
15. Keep evaluation sealed through the official technical M8 pass and the full-horizon pilot.
16. Never move candidate/event-derived work into an uncharged prelude merely to improve timing.
    Derived execution images are either built inside the charged boundary or separately frozen and
    independently audited through a versioned gate amendment.
17. Never relax a threshold, change a timing boundary, or choose a statistic after observing a run.

Prohibited shortcuts include action caps, candidate pre-ranking, shortened horizons, approximate
state merges, oracle-only search expansion, hidden warm caches, unreported preprocessing, and
relabeling semantic drift as a fallback.

## 4. Architecture decision

### Verdict

**Approve with conditions.** Finish C0, then use measured exclusive profiles to introduce a wider
compiled CPU boundary across both probes. C0-only, object reuse, and whole-root quotienting are
already disproven as sufficient strategies.

### Why the wider boundary is mandatory

- Phase-B hard-arm total: `592.221873s`, or `19.570205x` the entire two-probe budget.
- Eliminating both measured hard-arm traversal phases still leaves `260.991228s` before repeat
  generation, or `8.62x` the entire budget.
- `no_signal` has zero translation batches yet consumes `581.993756s` across first generation,
  repeat generation, and checker. Therefore C0 cannot address the universal bottleneck.
- The current hard-arm costs include catalog construction (`48.990843s`), checker authority/context
  reconstruction (`105.110645s` combined), common verification (`66.238186s`), checker traversal
  (`95.247443s`), and portable object assembly/parse.

### Option policy

| Option | Decision | Revisit trigger |
|---|---|---|
| C0 NumPy frontier batch | Implement now | Retain only if exact and its frozen C0 gate passes |
| Broader Python/NumPy/Shapely batching | Preferred first wider step | Escalate when exclusive profiling shows Python/object overhead dominates |
| Rust+GEOS native CPU producer/checker/auditor | Conditional preferred compiled boundary | Use after a bounded source-faithful feasibility spike; attest toolchain and binary; extend Spyrrow only for an already source-faithful Spyrrow path |
| GPU | Deferred, not prohibited | Reconsider only if at least 40% of remaining charged time is one uniform numeric kernel and transfer/setup projects below 20% of that kernel |
| Separately frozen compiled candidate pack | Contract amendment only | Consider only if charged source-faithful compilation cannot fit the fixed envelope |
| More slots, weaker semantics, looser thresholds | Not authorized | Requires an explicit new contract before evaluation, never a retroactive interpretation |

## 5. Goal-mode operating protocol

### Execution ledger

Create `Docs/experiments/m8-goal-execution-ledger.md` in G0. Each row records:

- task and gate ID;
- start and completion commit;
- owned files;
- RED evidence or inherited-work exception;
- focused, regression, full-suite, lint, and native-test evidence;
- specification-review and quality-review verdicts;
- artifact ID/hash, if any;
- gate result and authorized successor;
- observed, derived, generated, and assumed values in distinct fields.

Only one task may be `in_progress`. A gate failure closes that arm and activates only its
preregistered branch. Passing a component test never advances a later gate.

The execution sequence has two legitimate control outcomes: an official technical pass, or an
immutable earliest no-go/hold at a frozen gate. Only the pass completes the active goal. A no-go is
reported as the scoped evidence result and leaves the goal active for an authorized alternative;
if the same external/hardware/contract impasse persists for the platform's required blocked audit,
the goal is marked blocked rather than falsely completed.

### Per-task lifecycle

For every behavior-changing task:

1. Confirm the exact base, owned files, and dependencies.
2. Write a failing behavioral test and observe the intended RED.
3. Implement the minimum source-faithful change.
4. Run the focused GREEN suite and relevant mutation/regression suites.
5. Run Ruff on touched Python files and `git diff --check`.
6. Obtain one read-only specification review and one separate read-only code-quality review.
7. Fix findings, rerun affected checks, and re-review blockers.
8. Root independently reruns claimed checks.
9. Commit only the frozen task-owned allowlist with a task-scoped message. Before committing,
   require `git diff --cached --name-only` to equal that allowlist exactly; pre-existing staged work
   is a stop condition.
10. Update and commit the ledger in a separate ledger-only commit. A sealed measurement may not
    start until both commits exist and the worktree is clean.

The inherited Task-2 lines are the only exception to step 2: record that their original RED/GREEN
history cannot be independently replayed. Every new repair in G0.2—including production fingerprint
binding, integrity-versus-unsupported ownership, complete row binding, and multi-item eligibility—
must begin with a fresh failing test.

### Long-running measurement protocol

- Begin only from a reviewed, committed, clean source state and an absent output path.
- Perform one compact gate-relevant contamination check; do not add broad ritual.
- Freeze commit, full source tree, lockfile, interpreter, dependencies, native binaries, CPU/OS,
  power/thermal state, thread settings, inputs, process topology, timing boundary, and decision rule.
- No source-writing agent or dependency change may run while source-attested work is active.
- Process exit is not success. Require cleanup, strict artifact load, external identity/math
  recomputation, source/runtime postchecks, and no surviving descendants/registries/temp outputs.
- A contaminated, drifted, timed-out, or slot-breaching run publishes nothing and restarts only at a
  fresh absent output path.
- Normalize `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
  `VECLIB_MAXIMUM_THREADS`, `NUMEXPR_NUM_THREADS`, and `RAYON_NUM_THREADS` through the execution
  contract. An executor lease counts controller processes, workers, descendants, and native threads
  continuously and fails closed if the eight-slot ceiling is exceeded.
- The 50 ms ownership/RSS/load monitor is orchestration-only while it merely samples metadata and
  therefore holds no compute lease. Its CPU/process time is still reported and charged to outer
  wall. If it performs semantic, numeric, geometry, hashing, compression, or publication work, it
  must acquire a compute lease like any other active participant.

### Final artifact publisher

All C0, census, feasibility, Phase-C, coverage, paired-timing, Phase-E, pilot, authorization, and
evaluation artifacts use one hardened publisher. It must strict-validate canonical content before
writing; traverse parents without following symlinks; use same-directory owned staging; require a
regular absent destination for a fresh result; atomically install without overwrite; bind
device/inode/size/hash; fsync the file and parent directory; strict-reread and externally recompute
the installed bytes; accept an existing destination only when bytes are identical; reject every
different-byte or replacement race; and clean only owned staging files. No final artifact is
installed before source/runtime postchecks and process/registry cleanup pass.

### Timing rules

- Existing profile nodes are inclusive. Never add a child duration to its parent.
- Add explicit exclusive accounting: `exclusive = parent - sum(nonoverlapping children)`.
- Reject negative, overlapping, or unreconciled timing trees.
- Require at least 95% of wall time attributed before allocating wider performance work.
- Keep controller wall, worker wall, worker process, serialization, handoff, exit validation, and
  cleanup distinct, then reconcile them to the charged outer boundary.
- Cross-run measurements are diagnostics only. A speedup claim requires paired execution under one
  captured machine/load/runtime identity.

## 6. Gate hierarchy and immutable decisions

| Gate | Class | Machine-evaluated decision rule | A pass authorizes | Failure branch |
|---|---|---|---|---|
| G0 Task-2 acceptance | Integrity gate | Semantic requirements pass; production role binding is exercised; integrity drift cannot become unsupported/fallback; tests and both reviews pass | C0 integration | Fix forward from preserved draft |
| G0B measurement foundation | Integrity/operability gate | Real compute leases enforce eight slots and the reusable hardened publisher passes every race/no-follow/fsync/reload test | First new measurement | No C0 artifact execution |
| G0A execution authority | Prospective amendment | Strict authority artifact binds this reviewed plan/hash and explicitly authorizes the universal G2–G7 path after a C0-specific no-go | Amended successor DAG | No work beyond the older C0 sequence |
| G1 C0 | Previously frozen intermediate gate | Exact hard-arm identity, repeat exact, zero mismatch/fallback, traversal `<=8.566559s` | Retain C0 in wider engine | Close C0; continue only when G0A explicitly authorizes the universal path |
| G2 exclusive census | Instrumentation control | Both probes exact; zero mismatch/fallback; exclusive wall attribution `>=95%` | G2C and component allocation | Repair instrumentation; no optimization verdict |
| G2C coverage existence | Independent feasibility gate | The deterministic bounded calibration scan returns `coverage_exists` with at least one eligible real example for every required missing witness atom | G3 engineering work | `coverage_absent`: request contract redesign; `coverage_search_incomplete`: request a versioned larger cap |
| G3 fused-action feasibility | Arm-selection checkpoint | An admissible arm has zero candidate/footprint/accounting mismatch, zero unsupported official rows, and slots `<=8`; `select_preferred_arm` additionally requires worst-of-three `g3_core_triplet_wall_seconds <=14.0`, while `select_best_exact_arm_for_full_gate` records a `>14.0s` miss without treating it as infeasibility | G4 with the preferred or fastest exact arm | If no admissible exact arm exists, correctness no-go; otherwise G6 alone makes the first full-pipeline performance decision |
| G4 execution images | Correctness/independence gate | Producer and checker images reconstruct all official rows exactly, have distinct semantic-builder source identities, share raw DTOs only, and reject every binding mutation | Root kernels | Fix the role-specific builder; no timing claim |
| G5 root/audit checkpoint | Correctness/independence gate | Exactness, role/dependency guards, and mutations are mandatory; `2.0/2.5/4.5/1.5s` component values are recorded planning targets, not official or terminal thresholds | G6 codec/handoff work | Correct the failed semantic or independence boundary; performance misses are recorded and adjudicated only by G6 |
| G6 codec/handoff | Correctness and viability gate | Official bytes/hashes are exact; handoff races reject; one complete fully charged diagnostic two-probe triplet is `<=30.261404s` | Downstream control-plane freeze | Reprofile and activate the next preregistered admissible arm; publish a scoped no-go only after every such arm has an exact full-pipeline miss |
| G7 pre-seal | Preregistered engineering checkpoint | Exactly three valid runs; worst fully charged total `<=30.261404s`; all exactness/mutation/integrity rules pass. `<=27.0s` is green margin only | Sealed Phase C | Above target returns only to the dominant measured component |
| Phase C | Previously frozen engineering gate | Sealed fully charged two-probe total `<=30.261404s`; all proof/mutation/integrity rules pass | Final coverage pack and paired timing | No Phase D, six-cell, pilot, or evaluation |
| G8 coverage | Official prerequisite | Successor full and audit coverage, reconstructed from the six cells plus the independently regenerated/check/reference-matched pack, has no frozen-rule gap | Phase-D authorization decision | Gate closed; no evaluation discovery; request calibration-contract redesign |
| Phase D | Stricter authorization margin | Comparable paired timing, named Phase-D sample, speedup `>=25x`, projection `<=5` days, and passing Phase-C/G8 artifacts | Official six-cell execution | Hold performance |
| Phase E | Official M8 technical gate | Frozen 12-action sampled certificate-plus-checker process `<=581.7270928s`, full generator-plus-checker wall `<=165.69056927773784s`, speedup `>=20x`, projection `<=7` days, complete successor coverage/exactness | Technical M8 pass; pilot preparation | M8 technical no-go; no pilot/evaluation |
| G11 pilot | Separately authorized operability gate | Exact cold/warm schedule, semantic equality, zero mismatch/fallback, all frozen caps, and worse-repetition projection `<=7` days | Immutable execution-freeze candidate | No evaluation authorization |
| G12 authorization | Held-out access gate | Independent strict load binds the exact view, two-repetition/mode schedule, counts, caps, tolerance, terminal arms, geometry corpora, and M0 decision rules | Open only the registered 36-stream view | Evaluation remains sealed |
| G12 evaluation | M8 milestone evidence gate | Both complete repetitions reconcile, semantic outputs match, invalid-stream count is zero, and the frozen M0 result/band rules are applied without post-hoc changes | Valid M8 result; M9/M10 work | Preserve failures; publish no valid aggregate verdict |

The `30.261404s`, `25x`, and five-day rules are deliberately stricter engineering margins. They do
not replace the official `20x`/seven-day six-cell gate.

## 7. Dependency graph

```text
G0 recover/accept Task 2
  -> G0.4 reusable hardened publisher
  -> G0.5 baseline compute lease
  -> G0.6/G0A freeze execution authority and gate manifest
  -> G1 finish C0 -> sealed C0 decision
  -> G2 two-arm exclusive census
  -> G2B implement/freeze coverage-existence scanner
  -> G2C early real-calibration coverage-existence census
  -> G3 fused-action feasibility
  -> G4 independent execution images/catalog compilation
  -> G5 producer root kernel + checker root kernel + count auditor
  -> G6 exact streaming codec + immutable handoff
  -> G7.1 successor Phase-C evidence + G7.2 mutation freeze
  -> G7A implement/review/freeze every downstream contract and runner through G12
  -> G7.3 three-run pre-seal
  -> G7.4 sealed Phase C
  -> G8 execute deterministic real-calibration coverage pack with frozen code
  -> G9 same-session paired Phase D + authorization decision
  -> G10 official six-cell Phase E -> technical M8 decision
  -> [separate continuation] G11 full-horizon pilot
  -> [explicit authorization] G12 36-stream evaluation and M8 result
```

C0 failure may reach G2/G3 only through the separately content-addressed G0A prospective amendment;
without it, the older plans stop at C0. G0A does not convert a C0 failure into a pass. A semantic
failure at any gate blocks all timing interpretation.

## 8. G0 — Recover, repair, and accept the Task-2 draft

### G0.1 Preserve and inventory the inherited state

**Precondition:** This reviewed specification is already committed. The only remaining worktree
changes are the five expected inherited Task-2 files.

**Files:** No source edits. Create the execution ledger only.

1. Record HEAD, branch, porcelain status, changed-file list, diff stat, and `git diff --check`.
2. Save the exact draft with `git diff --binary --output=/tmp/yieldforge-m8-task2-47c1eaf.patch`.
3. Record `/tmp` patch SHA-256 and the five expected modified paths in the ledger.
4. If any unexpected file appears, stop writes and identify ownership; do not reset it.
5. Verify there was no pre-existing staged work, stage only the ledger, require the staged-file list
   to contain only `Docs/experiments/m8-goal-execution-ledger.md`, and commit
   `docs: start M8 goal execution ledger`. The five source files remain unstaged and preserved.

### G0.2 Repair the known integrity blockers

**Files:**

- Modify: `yf/src/yieldforge/oracle/certificates.py`
- Modify: `yf/src/yieldforge/oracle/__init__.py`
- Modify: `yf/src/yieldforge/oracle/compiled.py`
- Modify: `yf/src/yieldforge/oracle/prepared.py`
- Modify: `yf/src/yieldforge/oracle/sparse.py`
- Modify: `yf/src/yieldforge/oracle/checker.py`
- Modify: `yf/src/yieldforge/oracle/fact_checker.py`
- Modify: `yf/src/yieldforge/oracle/factored.py`
- Modify: `yf/src/yieldforge/oracle/facts.py`
- Modify: `yf/src/yieldforge/oracle/reference.py`
- Modify: `yf/tests/oracle/test_compiled.py`
- Modify: `yf/tests/oracle/test_fact_capture.py`
- Modify: `yf/tests/oracle/test_sparse.py`
- Modify: `yf/tests/oracle/test_checker.py`
- Modify: `yf/tests/oracle/test_fact_checker.py`
- Modify: `yf/tests/oracle/test_factored_generator.py`
- Modify: `yf/tests/oracle/test_certificates.py`
- Modify: `yf/tests/oracle/test_frontier.py`
- Modify: `yf/tests/oracle/test_reference.py`

This narrow allowlist amendment records the typed fact-checker propagation and certificate
regressions discovered during RED-team repair. It also records the necessary frontier audit-copy
identity regression: prepared accessors now return detached audit values, so equality remains exact
while object identity must not be preserved. `factored.py` is included because the public unchecked
bundle wrapper must be captured before generator authority and may not dispatch caller methods from
inside the prepared context. `reference.py` and `test_reference.py` are included because the public
correctness oracle must capture the request, cursor, visibility, and action identifiers before its
authoritative scope. `oracle/__init__.py` is included because the public package facade must expose
the newly hardened batched reference scorer consistently with the reference module. `facts.py` is
included to make the portable replay-cursor schema faithfully
represent the frozen M7 initial cursor sentinel (`timestamp_group_sequence == -1`). `profiling.py`
and `test_m8_profiling.py` remain
unchanged; the profiling suite remains a required regression execution below.

Required corrections:

1. C0 generator contexts explicitly bind the C0 mode and exact kernel identity. Scalar checker
   contexts bind a distinct checker mode; they do not claim producer NumPy authority.
2. Production registry/capability tests prove those fingerprints are issued and checked by real
   callers, not merely by a direct optional helper invocation.
3. Legitimately incomplete representation may produce `unsupported`. Duplicate/reordered
   membership, bound problem/candidate-set/layout/config mismatch, identity/hash drift, or prepared
   capability drift raises a typed integrity error before cursor mutation and without incrementing fallback.
4. Row authority binds and verifies event, catalog/root action, branch-before cursor hash,
   common-before cursor hash, direction, exact inventory delta, remnant measurement, and partition.
   An integer branch ID is an index, not proof authority.
5. Multi-item compact eligibility requires every changed item to be supported and impossible.

### G0.3 Independently accept and commit Task 2

Run from `yf`:

```bash
.venv/bin/pytest -q tests/oracle/test_compiled.py tests/oracle/test_fact_capture.py tests/oracle/test_fact_checker.py tests/oracle/test_factored_generator.py tests/oracle/test_certificates.py tests/oracle/test_frontier.py
.venv/bin/pytest -q tests/oracle/test_sparse.py tests/oracle/test_checker.py tests/oracle/test_reference.py tests/oracle/test_m8_profiling.py
.venv/bin/ruff check src/yieldforge/oracle/__init__.py src/yieldforge/oracle/certificates.py src/yieldforge/oracle/checker.py src/yieldforge/oracle/compiled.py src/yieldforge/oracle/fact_checker.py src/yieldforge/oracle/factored.py src/yieldforge/oracle/facts.py src/yieldforge/oracle/prepared.py src/yieldforge/oracle/reference.py src/yieldforge/oracle/sparse.py tests/oracle/test_certificates.py tests/oracle/test_checker.py tests/oracle/test_compiled.py tests/oracle/test_fact_capture.py tests/oracle/test_fact_checker.py tests/oracle/test_factored_generator.py tests/oracle/test_frontier.py tests/oracle/test_reference.py tests/oracle/test_sparse.py
git diff --check
```

Require both reviews to pass. Commit only accepted Task-2 files:

```bash
git add -- src/yieldforge/oracle/__init__.py \
  src/yieldforge/oracle/certificates.py \
  src/yieldforge/oracle/checker.py \
  src/yieldforge/oracle/compiled.py \
  src/yieldforge/oracle/fact_checker.py \
  src/yieldforge/oracle/factored.py \
  src/yieldforge/oracle/facts.py \
  src/yieldforge/oracle/prepared.py \
  src/yieldforge/oracle/reference.py \
  src/yieldforge/oracle/sparse.py \
  tests/oracle/test_certificates.py \
  tests/oracle/test_checker.py \
  tests/oracle/test_compiled.py \
  tests/oracle/test_fact_capture.py \
  tests/oracle/test_fact_checker.py \
  tests/oracle/test_factored_generator.py \
  tests/oracle/test_frontier.py \
  tests/oracle/test_reference.py \
  tests/oracle/test_sparse.py
git diff --cached --name-only
git commit -m "feat: bind M8 C0 queries to prepared evidence"
```

**G0 decision:** No C0 integration until the committed tree is clean and all listed blocker categories are proven
closed. If the draft is structurally unsound, fix forward from the saved patch rather than discarding it.

### G0.4 Generalize and seal the hardened publisher before machine artifacts

**Files:** create `yf/src/yieldforge/oracle/artifact_publisher.py` and
`yf/tests/oracle/test_artifact_publisher.py`; adapt `experiment.py`, `gate3_execution.py`, and their
tests through compatibility wrappers without changing historical bytes or identities.

Write RED tests for symlinked parents/targets, non-regular files, final/temp races, same-byte
idempotence, different-byte collision, inode replacement, fsync failure, strict-reread mismatch, and
ownership-safe cleanup. Implement the reusable contract from Section 5, then run:

```bash
.venv/bin/pytest -q tests/oracle/test_artifact_publisher.py tests/oracle/test_experiment.py tests/oracle/test_gate3_execution.py -k 'publish or publisher or race or fsync or symlink'
.venv/bin/ruff check src/yieldforge/oracle/artifact_publisher.py src/yieldforge/oracle/experiment.py src/yieldforge/oracle/gate3_execution.py tests/oracle/test_artifact_publisher.py tests/oracle/test_experiment.py tests/oracle/test_gate3_execution.py
git diff --check
```

Obtain both reviews and root verification. Stage only the six listed source/test paths that changed,
verify the staged list, and commit `feat: harden M8 artifact publication`. Commit the G0.4 ledger row
separately. G6.2 later owns transport handoff only. The execution ledger and this plan are tracked
documentation, not machine evidence artifacts; every new machine authority, measurement, decision,
authorization, and evaluation artifact after G0.4 must use this publisher.

### G0.5 Enforce the baseline compute lease before measurement

**Files:** modify `yf/src/yieldforge/oracle/concurrency.py` and current execution seams in
`experiment.py`; extend `test_m8_concurrency.py` and focused experiment tests.

Write RED tests, then implement lease tokens for every actively computing process or native thread.
Normalize NumPy/BLAS/Accelerate pool variables, require explicit one-thread C0 kernel execution,
sample owned processes/threads while work is active, and reconcile the observed peak to the artifact.
The controller is captured but consumes a token only when it performs computation; with eight worker
tokens held it must remain orchestration-only. Prove that undeclared threads, a ninth token, forged
peak evidence, or cleanup drift rejects and publishes nothing. Run:

```bash
.venv/bin/pytest -q tests/oracle/test_m8_concurrency.py tests/oracle/test_experiment.py -k 'slot or thread or lease or cleanup'
.venv/bin/ruff check src/yieldforge/oracle/concurrency.py src/yieldforge/oracle/experiment.py tests/oracle/test_m8_concurrency.py tests/oracle/test_experiment.py
git diff --check
```

Obtain both reviews and root verification, stage only the four listed paths that changed, verify the
staged list, and commit `feat: enforce M8 compute leases`. Commit the G0.5 ledger row separately.

### G0.6 Freeze machine-readable goal authority and gates

**Files:**

- Create: `yf/src/yieldforge/oracle/goal_gates.py`
- Create: `yf/tests/oracle/test_goal_gates.py`
- Create: `yf/experiments/m8-goal-gates-v1.json`
- Modify afterward in a separate commit: `Docs/experiments/m8-goal-execution-ledger.md`

Write failing strict-contract tests, then implement `M8GoalExecutionAuthorityV1` and a gate evaluator.
Publish the content-addressed authority with the already accepted G0.4 publisher. It binds this
plan's committed blob hash, the frozen lineage, each gate ID/class/prerequisite, logical CLI command,
output schema/path pattern, exact decision expression, successor, failure successor, and completion-
commit requirement. It explicitly records that a C0 no-go may continue only into the universal
G2–G7 path; it does not authorize Phase C, six-cell, or evaluation by itself. Any later amendment
requires a new authority version before the affected gate.

Run:

```bash
.venv/bin/pytest -q tests/oracle/test_goal_gates.py tests/oracle/test_artifact_publisher.py
.venv/bin/ruff check src/yieldforge/oracle/goal_gates.py tests/oracle/test_goal_gates.py
git diff --check
```

Obtain both reviews and root verification. First stage exactly `goal_gates.py` and
`test_goal_gates.py`, verify the staged list, and commit `feat: add M8 goal gate evaluator`. From that
clean committed source, use G0.4 to publish and independently strict-load
`m8-goal-gates-v1.json`; stage only that artifact and commit
`docs: freeze M8 goal execution authority`. Then stage only the ledger, verify the one-file staged
list, and commit `docs: record G0A execution authority`.

## 9. G1 — Complete and seal C0

This section executes Tasks 3–7 of
`Docs/plans/2026-08-27-m8-c0-frontier-columnar-implementation.md`. The detailed older task steps
remain binding except where this umbrella spec adds stricter integrity and evidence requirements.

### G1.1 Event-major producer batch

**Files:** `certificates.py`, `sparse.py`, `test_fact_capture.py`, `test_sparse.py`.

- One batch call per common event regardless of action count.
- Validate the complete batch before mutating any cursor.
- Proven rows bypass per-branch certificate construction, influence-source calculation, and
  evidence hashing.
- Mixed survivor/unsupported rows enter the unchanged authoritative path exactly once.
- Emit a distinct compact internal capture; never fabricate a 459-certificate legacy capture.
- Preserve selected-stock removal, state-rejoin, survivor search, policy dominance, exact fallback,
  and attempted-influence ordering.
- Keep the legacy producer as a private development differential, not a hidden timed fallback.

Commit: `feat: batch M8 C0 producer traversal`.

### G1.2 Byte-identical portable assembly

**Files:** `factored.py`, `test_factored_generator.py`.

- Cache the complete canonical scalar-reference tuple once per exact semantic partition.
- Convert compact capture directly to the unchanged portable group schema.
- Require legacy/C0 equality for semantic bytes, bundle hash, decision, costs, classifications,
  root commitments, and state hashes.
- Require complete sorted candidate membership and no search/competitor evidence for true
  all-impossible groups.
- Preserve explicit survivor/exact evidence.

Commit: `feat: assemble compact M8 C0 facts`.

### G1.3 Independent checker fast path

**Files:** `compiled.py`, `fact_checker.py`, `test_fact_checker.py`.

- Detect a compact group before full certificate construction.
- Reconstruct the proof-owned frontier and complete candidate/scalar bijection independently.
- Use the existing scalar predicate; do not import or call `columnar.py`, the producer wrapper, or a
  producer cache.
- Cache only the independently validated canonical membership tuple inside checker-owned state.
- Missing, extra, duplicate, reordered, cross-partition, or coherently rehashed evidence rejects as
  the stable semantic owner; scalar false rejects and does not fall through.
- Add a static import guard and runtime monkeypatch tests proving checker independence.

Commit: `feat: verify M8 C0 groups without expansion`.

### G1.4 C0 counters, mutation freeze, and evidence contract

**Files:** create `c0_evidence.py` and tests; modify `experiment.py`, CLI registration, experiment/CLI
tests, and profiling counters.

The new contract must algebraically reconcile:

- rows offered, supported, compact, survivor, unsupported, and legacy-routed;
- branches offered and compact-eligible;
- frontier width and predicate count;
- producer scalar differentials and numeric mismatches;
- checker scalar proofs and membership validations;
- kernel invocations, exact fallbacks, and incomplete-representation paths;
- distinct generator/repeat/checker workers, topology, peak slots, and cleanup.

Bind official Gate-3 identity, source tree, C0 kernel source identity, Python/NumPy versions, loaded
NumPy build/configuration and CPU-feature dispatch, loaded NumPy native-extension hash, transitive
BLAS/Accelerate native-library identities, `pyproject.toml` and `uv.lock` hashes, dtypes, byte order,
CPU/OS architecture, normalized thread settings, accepted load/power/thermal observations,
controller/worker runtime identities, evaluation false, six-cell authorization false, and the
unchanged claim ceiling. Do not reinterpret Phase-B v3.

Freeze mutations for kernel rows; frontier/candidate membership; material/config/geometry/rates;
event/cursor/delta; compact flags; fallback counters; source/runtime/native drift; worker failure;
publisher races; and cleanup. Every mutation must reach the intended real boundary, reject with a
typed owner, publish nothing, leak nothing, and leave evaluation sealed.

Commit: `feat: bind M8 C0 profile evidence`.

### G1.5 C0 closure verification

Run focused C0 suites, all oracle tests, the full repository suite, Ruff, and diff checks. Only the
three previously documented environment skips may remain. Strict-load every historical Gate-3 and
Phase-B artifact to prove no schema reinterpretation.

### G1.6 Sealed C0 measurement and decision

Freeze the hard arm: `regime_shift`, seed `2026082300`, stream
`yfts-f320978a2d55802395294150`, 459 roots, 2,297 fixed nodes, 43,520,933 semantic bytes, 13 frontier
members, and 5,967 predicates. Require the official bundle/semantic-byte/decision identities,
byte-identical repeat, zero mismatch/fallback, source/runtime attestation, `peak_compute_count <= 8`,
cleanup, and evaluation false.

Primary pass rule: first-generation producer traversal plus fresh-checker traversal is
`<=8.566559s`. Repeat generation is charged in later Phase C but is not double-counted into this C0
comparison. Publish either a content-addressed pass or no-go; never overwrite Phase B.

## 10. G2 — Produce a two-arm exclusive cost census

### G2.1 Add reconciled exclusive instrumentation

**Files:** modify `profiling.py`, `experiment.py`, `profile_evidence.py`, existing profiling tests;
create `phase_c_census.py` and `test_phase_c_census.py` if a distinct strict contract is cleaner.

Add nonoverlapping regions for:

- runtime/source capture and validation;
- standard placement transforms;
- GEOS residual overlay/classification;
- action-model construction;
- prepared-layout construction;
- initial cursor transition and cursor/event hashing;
- common-transition and translation-sequence generation;
- counted-translation audit;
- influence classification;
- root terminal state/cost computation;
- fact-node creation and canonical encoding/hash;
- strict load/index; and
- worker startup, handoff, exit validation, and cleanup.

Test exclusive reconciliation and reject overlap or negative residuals. Instrumentation is
calibration-only and must not enter semantic normalization.

### G2.2 Execute and seal the census

Run one first-generation/fresh-checker pair for both official probes under one captured environment.
Require exact identities, zero fallback/mismatch, and `>=95%` wall attribution. Publish
`m8-phase-c-two-arm-census-v1` as a diagnostic artifact with no speedup or M8 claim.

### G2.3 Freeze component budgets

Derive budgets only from mutually exclusive census regions. The aggregate `30.261404s` rule remains
the hard engineering gate; component envelopes are explicitly diagnostic planning targets. Record
each observed mandatory-exclusive cost and the exact outer accounting model. An observed duration is
an implementation measurement, not a mathematical lower bound, and may never support an
infeasibility verdict. Allocate work to the largest exclusive region; only the complete G6 outer-
wall gate makes the first full-pipeline performance decision.

### G2B — Implement the coverage-existence scanner before performance work

**Files:** create `coverage_pack.py` and `test_coverage_pack.py`; add a calibration-only CLI entry
point and its tests. This is the coverage portion of G7A pulled forward deliberately; its schema,
selection order, input boundary, and evaluation refusal are frozen here and may not change after
Phase C without invalidating the downstream cascade.

Write RED tests for exact calibration-only input acceptance, evaluation refusal, deterministic
ordering, complete horizon enumeration, identity-only output, interruption cleanup, and strict
no-savings fields. Implement and review the scanner, run its focused tests/Ruff/diff checks, commit
the exact allowlist, then commit the ledger update.

Run from `yf`:

```bash
.venv/bin/pytest -q tests/oracle/test_coverage_pack.py tests/test_cli.py -k 'coverage or evaluation_refusal or ordering or horizon or cleanup'
.venv/bin/ruff check src/yieldforge/oracle/coverage_pack.py src/yieldforge/cli.py tests/oracle/test_coverage_pack.py tests/test_cli.py
git diff --check
```

After both reviews and root verification, stage only the four listed scanner/CLI source-test paths,
verify the staged list, and commit `feat: add calibration-only M8 coverage scanner`. Commit the G2B
ledger row separately.

### G2C — Run the early calibration-only coverage-existence census

**Files:** Use only the committed G2B scanner; no source edits during the scan. G3 may edit its owned
source afterward. Rerun G2C only if the scanner, frozen calibration inputs, witness semantics, or
proof/check/reference authority changes.

Freeze the eligible horizon ladder as exact complete-remaining-suffix counts `(0, 1, ..., 23)`; no
suffix is shortened to manufacture coverage. At event position 23, zero future events is a valid
exactness case with an empty continuation witness; it must be checked and may satisfy only the
separate zero-future exactness atom, never a transition, escape, rejoin, or policy witness atom.
Enumerate only the 12 sealed calibration streams in
regime, seed, event position, horizon count, action kind, and catalog-action order. The scanner may
inspect witness classification and proof validity, but never savings, advantage magnitude, or
evaluation. It records an identity-only candidate manifest and proves whether at least one
independently checkable/reference-matchable real example exists for every witness atom missing from
the canonical six-cell evidence, especially `policy_dominated` and `exact_transition`.

Run two stages. Stage 1 inspects only existing sealed calibration proofs/manifests. Stage 2 scans the
remaining ordered live candidates and checkpoints a strict identity cursor every 250 evaluated
actions. Stop successfully as soon as every missing witness atom has at least one independently
checked/reference-matched real example. The frozen Stage-2 cap is the earlier of 20,000 evaluated
actions or six controller hours; resume is allowed only from a strict checkpoint with the same
source/runtime/input identity and the cap remains cumulative across resumes.

Publish exactly one content-addressed decision before G3:

- `coverage_exists` when all missing atoms are found;
- `coverage_absent` only after the entire ordered eligible corpus is exhausted; or
- `coverage_search_incomplete` when the action/time cap is exhausted first.

Only `coverage_exists` advances. `coverage_absent` requests a calibration-contract redesign.
`coverage_search_incomplete` is inconclusive and requests a separately versioned larger search cap;
it may not be relabeled as absence. Do not use evaluation or a synthetic fixture to fill the gap.

## 11. G3 — Run the decisive fused-action feasibility spike

This is the first project-level architecture arm-selection checkpoint. It can issue a correctness
no-go, but it cannot infer performance infeasibility from a component timing; G6 owns that decision.

### G3.1 Specify a source-faithful candidate template

**Files:** create `execution_image_wire.py`, `producer_image.py`, `checker_image.py`,
`fused_action_spike.py`, role-specific tests, and a strict feasibility-evidence contract; minimally
modify baseline action/geometry seams without changing published M7 identities.

The shared wire module contains only validated raw frozen primitives, offsets, dtypes, and canonical
DTOs. It may not build or expose a proof conclusion. The producer template builder derives exact
placed polygons, residual components/WKB, reuse accounting, returned-remnant templates, policy/rate
scalars, rejection bounds/frontier scalars, and source commitments. The separately attested checker
builder independently recomputes every proof-critical placement, residual, action, and accounting
conclusion from the raw source primitives. Event material, lineage, action IDs, and ledgers remain
separately instantiated. No role imports the other's semantic builder.

The image must support variable candidate counts, frontier widths, event counts, ordering,
empty/singleton frontiers, multi-item deltas, survivors, material mismatches, incomplete archives,
and exact paths. Production logic may not encode 459, 13, two events, or `regime_shift`.

The bounded `fused_action_spike` instantiates both role-specific images and computes the universal
initial/root state and cost records needed by the G3 timing formula, but publishes no portable fact
and cannot become production authority merely by passing. G4/G5 promote only reviewed pieces after
the differential and gate pass.

### G3.2 Compare bounded implementation arms

Try in order, stopping when the gate is satisfied:

1. batched/threaded Shapely using the exact loaded GEOS semantics;
2. process-batched Shapely if thread scaling is poor;
3. a Rust+GEOS FFI/vectorized-GEOS sidecar if Python orchestration remains dominant.

Native work gets a versioned ABI, source tree, compiler/version/flags, target triple, features,
binary hash, CPU dispatch, thread count, and fail-closed timeout/output contract. Do not replace exact
Shapely residual semantics with Jagua/Spyrrow collision semantics. Spyrrow may be extended only for
an already source-faithful Spyrrow path. If selected, create an owned
`yf/native/m8_cpu_kernel/Cargo.toml` plus lockfile, golden ABI fixtures, and tests for build
reproducibility, crash/timeout, truncation, reordering, binary replacement, and deterministic
non-adoption/fallback. Required commands are:

```bash
cargo test --locked --manifest-path native/m8_cpu_kernel/Cargo.toml
cargo build --release --locked --manifest-path native/m8_cpu_kernel/Cargo.toml
```

### G3.3 Differential and mutation proof

Every candidate must exactly match `build_standard_sheet_action` and every footprint must match
`prepare_layout_footprint`. Require exact action IDs/order, WKB hashes, accounting bits, returned
remnants, policy contexts, candidate membership, and source bindings. Mutate offsets, WKB, material,
rates, geometry, order, binary, and runtime.

### G3.4 Feasibility decision

Define, per diagnostic triplet and probe, `generation_core_wall` as the nonoverlapping charged wall
covering raw-source capture, role-specific candidate-template/image construction, and producer
root-state computation. Define `checker_core_wall` identically for the independently built checker
image and checker root computation. Then compute:

```text
g3_core_triplet_wall_seconds =
    max(no_signal_generation_1_core, regime_shift_generation_1_core)
  + max(no_signal_generation_2_core, regime_shift_generation_2_core)
  + max(no_signal_checker_core, regime_shift_checker_core)
```

This core formula excludes separately reported codec, handoff, startup, final publication, and
translation-audit regions; none may overlap a core region. Execute exactly three triplets from fresh
role processes with a 120-second outer timeout per triplet, the fixed probe order, cold role-owned
caches, normalized one-thread native pools, and the same captured machine/load contract. Invalid or
contaminated execution invalidates the three-run set; it is not replaced selectively. The gate uses
the worst of the three valid triplets and is evaluated by the strict G0A gate runner.

The `14.0s` result is an arm-selection target, not a project-infeasibility proof. If the Python/
Shapely arm is exact but misses it, try the bounded native CPU arm. Emit exactly one machine decision:

- `select_preferred_arm` when the preferred admissible arm's worst valid core triplet is `<=14.0s`;
- `select_best_exact_arm_for_full_gate` when every preferred arm misses `14.0s` but at least one arm
  is exact, source-faithful, fully supported, and slot-compliant; select the minimum worst-of-three
  core wall with the frozen arm order as the tie-break; or
- `no_admissible_exact_arm` when every arm has a semantic, support, identity, or slot failure.

The first two decisions proceed to G4. The second preserves the target miss and makes no performance
or infeasibility claim. Only the fully assembled G6 charged outer-wall gate can reject the current
implementation on performance. Do not silently move compilation before timing.

## 12. G4–G6 — Build the wider exact engine

### G4.1 Finalize the charged immutable execution-image ABI

The shared image ABI contains read-only raw arrays/offset tables for exact source catalog order,
events/materials, cursors, frontier/full membership, configurations/rates/rules, suffix bindings,
and runtime/source/native identities. Role-specific images contain derived templates and expected
action conclusions. Capture canonical source bytes once, verify the digest before and after
construction, reject buffer alias/mutation, and use dense checked IDs.

### G4.2 Build independent producer and checker images

**Files:** `execution_image_wire.py`, `producer_image.py`, `checker_image.py`, `native_protocol.py`,
`compiled.py`, `prepared.py`, `sparse.py`, `fact_checker.py`, `experiment.py`, and focused tests.

Producer and checker each build a role-distinct semantic image from frozen raw source input. No
producer image, derived pack, placement, residual, action outcome, or accounting conclusion crosses
the checker process boundary. Deduplicate repeated full-object encoding/hash work while retaining
all boundary/source/output commitments and pre/post digest verification. Remove avoidable duplicate
catalog scans, deep Pydantic copies, and layout preparation while preserving exact identities. Add
dependency/import tests forbidding either semantic builder from importing or calling the other, and
bind their distinct source identities in every artifact.

### G4.3 Extend the accepted compute lease to new native kernels

**Files:** modify `concurrency.py` and `experiment.py`; create/extend concurrency tests.

Extend G0.5's already mandatory lease to every new Rust/GEOS or other native kernel. Pass explicit
native thread counts, sample owned descendants/threads, and reconcile the peak with the artifact.
RED tests must prove that a ninth compute token, undeclared Rayon/BLAS pool, or forged peak count
fails closed, reaps only owned work, and publishes nothing. G4.3 may not weaken or postpone the C0
lease that was required before G1.

### G5.1 Add the universal producer root-state kernel

**Files:** create `root_kernel.py` or a producer-native module; modify `sparse.py`, `certificates.py`,
`factored.py`; add `test_root_kernel.py`.

Batch all roots in both probes. Output exact root/action identities, initial/final commitments,
classification, inventory delta, final cost bits, compact references, and explicit unsupported/exact
disposition. Development mode differentially checks every row against scalar Python. Timed mode does
not run a hidden scalar differential; an unsupported official row is an explicit failure.

Diagnostic planning target: maximum per-probe producer-worker exclusive root traversal `<=2.0s`,
887 exact root states/costs across both probes, zero fallback.

### G5.2 Add the distinct checker root kernel

The checker consumes only submitted canonical bytes, frozen source input, and its independently
constructed image. It independently recomputes catalog membership, standard action outcomes,
initial/common transitions, scalar membership, state chain, terminal cost/state, and decision. It
must not call producer code or the C0 NumPy kernel as proof authority.

Diagnostic planning target: maximum per-probe checker-worker exclusive traversal `<=2.5s` and the
maximum-probe producer-plus-checker exclusive traversal `<=4.5s`; all correlated and coherently
rehashed mutations reject.

### G5.3 Replace the counted-translation audit

**Files:** create a separate CPU auditor module/native crate; modify
`translation_count_audit.py`; extend its tests and source/runtime evidence.

The auditor may import only the shared raw `execution_image_wire` DTO/primitives and general-purpose
standard/third-party libraries. It may not import, link, call, or depend on producer/checker image
builders, root kernels, translation generators, `certificates.py`, `compiled.py`, `sparse.py`, or a
producer/checker native crate. If native, it lives in a distinct
`yf/native/m8_count_auditor` crate with its own manifest, source tree, binary, and attested hash; its
Cargo metadata must show no producer/checker/native-kernel dependency. Add a static Python import-
graph and Cargo-metadata guard, a runtime link/import guard, and correlated-code mutations: replace
the producer translation generator while the auditor remains independently correct and detects the
discrepancy; then corrupt only the auditor and require the gate to reject. Bind distinct producer,
checker, and auditor source/binary identities in every successor artifact.

Reproduce raw `f64` inputs, bbox/vertex/grid order, signed-zero normalization, exact duplicate
identity, first foreign candidate, truncation, expected sequence membership, and generated/evaluated/
duplicate counts. Do not perform collision classification or call Jagua. Differentially cover all
official batches plus widths 1/2/4, `nextafter`, duplicates, signed zero, every truncation boundary,
foreign/reordered/missing points, and source/runtime mutations.

Diagnostic planning target: complete hard-arm worker-exclusive independent audit `<=1.5s`, zero
discrepancy. Failure to reproduce exact source order blocks G6. A timing miss is recorded and still
proceeds to G6 so the codec/handoff and complete outer wall—not an informal component model—make the
performance decision; if G6 then misses, return first to this historically dominant boundary.

### G6.1 Add an exact streaming portable codec

**Files:** create `fact_codec.py` and `test_fact_codec.py`; modify `factored.py`, `facts.py`, and
`fact_checker.py`.

Producer streams the unchanged canonical field order and SHA-256 from compact rows without building
thousands of Pydantic objects. The independent decoder validates schema, canonical JSON, ordering,
node/root hashes, complete fixed-layer membership, and dangling/unused references without trusting a
producer index. Repeated generation independently reconstructs bytes; it may not copy the first output.

Require byte-for-byte equality with both official bundles and rejection of every noncanonical alias
and fact mutation. Diagnostic planning targets, measured as maximum per-probe worker-exclusive
regions, are producer assembly/hash/encoding/validation `<=2.0s` and checker parse/index/hash
`<=1.5s`. If exact bytes cannot be preserved, this codec is not admissible under the unchanged gate.

### G6.2 Remove large-byte pickle transport

Use an owned, content-addressed temporary file or immutable descriptor handoff for the 44–48 MB
bundles. Require no-follow open, inode/device/size commitments, fsync, independent checker read or
mapping, replacement-race rejection, and ownership-safe cleanup. Charge creation, validation,
handoff, and cleanup. The diagnostic `<=1.5s` target is the reconciled outer-pipeline overhead across
both probes and all three phases beyond worker compute; it is not divided by process or slot count.

After G6.2, execute one complete diagnostic two-probe triplet with the actual Phase-C topology and
charged boundary. Start one controller `perf_counter` immediately before first-phase launch and stop
it only after all three phases, inter-phase orchestration, source/runtime postchecks, process exit,
registry/capability/temp cleanup, and diagnostic immutable publication finish. This single outer wall
is the viability decision value; the three phase walls and exclusive children reconcile to it but
are never substituted for it or added twice. Require exact official bytes/hashes, zero mismatch/
fallback/unsupported, race/cleanup evidence, and outer wall `<=30.261404s` before G7A. This stricter
diagnostic is a viability gate, not sealed Phase C and not a speedup claim; sealed Phase C still uses
its unchanged historical charged boundary.

### G4–G6 atomic ownership and verification matrix

Each row is a separate RED/GREEN/review/root-verification/source-commit/ledger-commit unit. The G0A
manifest supplies its result schema/path and evaluator; a missing evaluator is a blocker.

| Task | Exact primary ownership | Focused GREEN command from `yf` | Commit |
|---|---|---|---|
| G4.1 raw ABI | `execution_image_wire.py`, `test_execution_image_wire.py` | `.venv/bin/pytest -q tests/oracle/test_execution_image_wire.py tests/oracle/test_compiled.py tests/oracle/test_fact_capture.py` | `feat: add M8 raw execution-image ABI` |
| G4.2 role builders | `producer_image.py`, `checker_image.py`, `test_producer_image.py`, `test_checker_image.py` | `.venv/bin/pytest -q tests/oracle/test_producer_image.py tests/oracle/test_checker_image.py tests/oracle/test_fact_capture.py tests/oracle/test_fact_checker.py` | `feat: build independent M8 execution images` |
| G4.3 lease | `concurrency.py`, executor seams in `experiment.py`, concurrency tests | `.venv/bin/pytest -q tests/oracle/test_m8_concurrency.py tests/oracle/test_experiment.py -k 'slot or thread or cleanup'` | `feat: enforce M8 native compute leases` |
| G5.1 producer roots | producer `root_kernel.py` or producer-native crate, `sparse.py`, `certificates.py`, `factored.py`, `test_root_kernel.py` | `.venv/bin/pytest -q tests/oracle/test_root_kernel.py tests/oracle/test_fact_capture.py tests/oracle/test_factored_generator.py` | `feat: batch M8 producer root states` |
| G5.2 checker roots | checker-only kernel/crate, `fact_checker.py`, checker/mutation tests | `.venv/bin/pytest -q tests/oracle/test_fact_checker.py tests/oracle/test_gate3_mutations.py -k 'root or state_chain or compiled'` | `feat: add independent M8 checker root kernel` |
| G5.3 count auditor | auditor module/crate, `translation_count_audit.py`, `test_translation_count_audit.py`, `test_auditor_independence.py` | `.venv/bin/pytest -q tests/oracle/test_translation_count_audit.py tests/oracle/test_auditor_independence.py tests/oracle/test_fact_checker.py -k 'translation or counted or dependency or independent'` plus auditor-only Cargo tests and `cargo metadata --locked` dependency validation | `feat: compile independent M8 translation count audit` |
| G6.1 codec | `fact_codec.py`, `factored.py`, `facts.py`, `fact_checker.py`, codec tests | `.venv/bin/pytest -q tests/oracle/test_fact_codec.py tests/oracle/test_facts.py tests/oracle/test_factored_generator.py tests/oracle/test_fact_checker.py` | `feat: stream exact M8 portable facts` |
| G6.2 handoff | immutable transport seams in `experiment.py`, handoff/race tests; reuse G0.4 final publisher unchanged | `.venv/bin/pytest -q tests/oracle/test_experiment.py tests/oracle/test_gate3_execution.py -k 'handoff or publisher or race or cleanup'` | `perf: hand off M8 bundles without pickle copies` |

After each focused command, run selected native tests, Ruff on the exact touched Python allowlist,
`git diff --check`, the two reviews, and the staged-file allowlist check. If a listed test file does
not yet exist, its creation and initial RED are part of that task; do not silently substitute a
different semantic owner.

Each G4–G6 subtask follows the full TDD/review/commit lifecycle and reruns the exact two-probe
differential before its timing is interpreted.

## 13. G7 — Seal the complete two-probe engineering gate

### G7.1 Create the successor Phase-C evidence model

**Files:** create `yf/src/yieldforge/oracle/phase_c.py` and
`yf/tests/oracle/test_phase_c.py`. G7A.6 later owns CLI integration. Do not mutate
`M8Gate3PerformanceResult`: its current schema intentionally forces
`timing_environment_comparable=False`, `reference_speedup_gating=False`, and `hold_performance`.

Bind:

- parent Gate-3/decision/Phase-B/C0/census identities;
- exact two-probe inputs and outputs;
- Python, NumPy, Pydantic, Shapely/GEOS, Spyrrow/Jagua, and every native identity;
- compiler/toolchain/flags, ABI/protocol, architecture, CPU dispatch, dtypes, byte order, and threads;
- controller/worker source/runtime identities and fresh bytecode-cache scopes;
- execution-image and codec schemas;
- per-regime rows/templates/predicates/root/layer counts;
- exclusive and outer charged timing reconciliation;
- fallback, unsupported, numeric/native/codec mismatch counts;
- process/registry/capability/temp cleanup;
- a 50 ms monotonic sampler identity/hash, sample count and maximum gap over the complete recursively
  owned process-tree closure. Bind the expected and observed PID/parent/role/lifetime manifest and
  include controllers, producer/checker workers, the translation auditor, Jagua helpers, native
  sidecars, publishers, and every child/grandchild. Capture each process's peak resident set and the
  maximum simultaneous resident-set sum across the closure, physical-memory identity, and pre/post
  swap-pressure evidence. A missing, unknown, escaped, or unreconciled descendant invalidates the
  run even if it is shorter-lived than one sampler interval; the spawn registry supplies its
  lifetime and role evidence; and
- peak compute `<=8`, evaluation false, six-cell executed false, and claim ceiling.

Write RED contract/decision/identity/timing/RSS tests, implement the minimum model/evaluator, and run:

```bash
.venv/bin/pytest -q tests/oracle/test_phase_c.py tests/oracle/test_gate3_evidence.py
.venv/bin/ruff check src/yieldforge/oracle/phase_c.py tests/oracle/test_phase_c.py
git diff --check
```

After both reviews and root verification, stage only these two new files, commit
`feat: add successor M8 Phase C evidence`, then commit the G7.1 ledger row separately.

### G7.2 Freeze the successor mutation manifest

**Files:** create `yf/src/yieldforge/oracle/phase_c_mutations.py` and
`yf/tests/oracle/test_phase_c_mutations.py`; import historical recipes read-only.

Retain all historical 16 recipes, including all 12 real-checker root mutations with exact
`m8_state_chain_mismatch`. Add dimensions/offsets/order, candidate/frontier membership, template
WKB/accounting/action IDs, buffer aliasing, source/material/config/rates/state/suffix drift, native
output truncation/duplication/reorder, binary/runtime replacement, cross-role cache substitution,
codec/hash/reference corruption, hidden fallback forgery, worker crash, publisher race, and cleanup
failure. Freeze the recipe order and expected typed owner before timing.

Write one RED test per mutation owner and required error, then run:

```bash
.venv/bin/pytest -q tests/oracle/test_phase_c_mutations.py tests/oracle/test_gate3_mutations.py
.venv/bin/ruff check src/yieldforge/oracle/phase_c_mutations.py tests/oracle/test_phase_c_mutations.py
git diff --check
```

After both reviews and root verification, stage only these two new files, commit
`test: freeze successor M8 Phase C mutations`, then commit the G7.2 ledger row separately.

### G7A — Implement and freeze every downstream executable boundary

Before any pre-seal or sealed Phase-C timing, implement, test, review, and commit all executable
software that any later G8–G12 step will execute. Only data-derived manifests, decisions, and
artifacts may be instantiated after Phase C; no new runner, schema, evaluator, statistic, retry path,
or authorization logic may be added later.

Each row below is an atomic RED/GREEN/review/root-verification/source-commit/ledger-commit task. The
listed source/test paths are its exclusive primary ownership; shared wire contracts and the accepted
G0.4 publisher are imported unchanged. Run the command from `yf`, run Ruff on the same row's Python
allowlist plus `git diff --check`, obtain both reviews, stage exactly the changed paths in that row,
verify the staged list, make the stated source commit, then make a separate one-file ledger commit.

| Task | Exact primary ownership | Focused GREEN command | Source commit |
|---|---|---|---|
| G7A.1 coverage result | `coverage_pack.py`, new `coverage_evidence.py`, `test_coverage_pack.py`, new `test_coverage_evidence.py` | `.venv/bin/pytest -q tests/oracle/test_coverage_pack.py tests/oracle/test_coverage_evidence.py` | `feat: freeze M8 coverage evidence contract` |
| G7A.2 Phase D | new `phase_d.py`, new `test_phase_d.py` | `.venv/bin/pytest -q tests/oracle/test_phase_d.py tests/oracle/test_gate3_evidence.py -k 'paired or reference or projection or authorize or hold'` | `feat: add comparable M8 Phase D gate` |
| G7A.3 Phase E | new `phase_e.py`, `experiment.py`, new `test_phase_e.py`, `test_experiment.py` | `.venv/bin/pytest -q tests/oracle/test_phase_e.py tests/oracle/test_experiment.py -k 'six_cell or arithmetic or coverage or rss or decision'` | `feat: add successor M8 Phase E gate` |
| G7A.4 pilot | new `pilot.py`, new `test_pilot.py` | `.venv/bin/pytest -q tests/oracle/test_pilot.py -k 'manifest or cold or warm or audit or cap or refusal'` | `feat: freeze M8 full-horizon pilot runner` |
| G7A.5 evaluation | new `evaluation.py`, new `test_evaluation.py` | `.venv/bin/pytest -q tests/oracle/test_evaluation.py -k 'checkpoint or resume or invalid or statistic or terminal or band or repetition'` | `feat: freeze M8 held-out evaluation runner` |
| G7A.6 authority and CLI | new `downstream_authority.py`, new `test_downstream_authority.py`, `goal_gates.py`, `test_goal_gates.py`, `cli.py`, `test_cli.py` | `.venv/bin/pytest -q tests/oracle/test_downstream_authority.py tests/oracle/test_goal_gates.py tests/test_cli.py -k 'm8 or authorization or refusal or immutable'` | `feat: seal M8 downstream authorization paths` |

G7A.1 freezes the strict coverage result/reconciliation built on G2B. G7A.2 owns paired timing,
arithmetic, decision, and the Phase-D-to-Phase-E authorization. G7A.3 owns the six-cell runner,
audit/coverage merger, RSS evidence, exact arithmetic, and decision. G7A.4 owns the pilot manifest,
cold/warm schedule, resource-cap evaluator, cost-blind audit selector, and execution-freeze
candidate. G7A.5 owns stream/result, checkpoint/resume, invalid-stream, aggregate, terminal-
sensitivity, M0-band, and paired-statistics behavior. G7A.6 owns strict CLI/load/publish/refusal and
gate evaluators. If implemented paths differ from G0A v1, first make the G7A.6 source commit, then
use the clean committed source and G0.4 publisher to generate, strict-load, and commit a new
authority JSON in a separate artifact-only commit before the G7A.6 ledger-only commit; never edit
v1.

For every row, first prove each pass and independent failure rule RED. In G7A.4–G7A.6, exercise only
synthetic fixtures and calibration-only inputs; prove evaluation access impossible without the
future exact authorization ID/hash. No evaluation fixture may contain a registered held-out stream
payload.

### G7A.7 Seal the downstream control plane

With no source edit after G7A.6, run:

```bash
.venv/bin/pytest -q tests/oracle/test_phase_c.py tests/oracle/test_phase_c_mutations.py tests/oracle/test_coverage_pack.py tests/oracle/test_coverage_evidence.py tests/oracle/test_phase_d.py tests/oracle/test_phase_e.py tests/oracle/test_pilot.py tests/oracle/test_evaluation.py tests/oracle/test_downstream_authority.py tests/oracle/test_goal_gates.py tests/test_cli.py
.venv/bin/pytest -q tests/oracle
.venv/bin/pytest -q
.venv/bin/ruff check src tests
git diff --check
```

Run every selected native crate's locked tests and metadata dependency guard. Obtain final read-only
specification and code-quality reviews. A failure returns to its owning G7A row, repeats that row's
source/ledger commits, and reruns G7A.7. Record the final G7A.6 source commit, final ledger/execution
commit, source-tree hash, lockfile, and complete test evidence in a separate G7A.7 ledger commit.
This is the sealed-source boundary.

G8–G10 execute that code without source or dependency edits. A separately authorized G11/G12
continuation must strict-load the same source identity. Any later Python/native/lockfile change
invalidates all downstream timing evidence and requires the entire cascade to rerun from Phase C,
followed by coverage, Phase D, and Phase E under the new source identity. Documentation-only
evidence updates may occur after a run but must be committed before the next clean-source
measurement. If G11/G12 later needs any source change, rerun and pass that same cascade first.

### G7.3 Run a pre-seal checkpoint

Run focused suites, all oracle tests, the full repository suite, native tests, Ruff, diff checks,
two generations, fresh checker, the complete 12-action four-way audit, and every mutation. Execute
exactly three complete valid checkpoints in the preregistered order under the same captured
environment. There are no adaptive warmups or discretionary repeats; any invalid/contaminated run
invalidates the entire three-run set and requires diagnosis before a new set begins. The decision
uses the worst charged wall of the three.

Planning envelopes:

- first generation `<=8.5s`;
- repeat generation `<=8.5s`;
- checker `<=9.5s`;
- complete pipeline `<=27.0s`.

Worst-run `<=27.0s` is green engineering margin. Worst-run `27.0–30.261404s` is amber but still
qualifies for one sealed Phase-C attempt; it does not authorize extra diagnostics. Above
`30.261404s`, return only to the dominant exclusive component. Any mismatch/fallback/evaluation
access is a correctness no-go regardless of speed.

Any source fix after G7A.7 invalidates the checkpoint set. Repeat the owning G4–G7A task lifecycle,
rerun G7A.7 to establish a new sealed-source boundary, and start a new exactly-three-run pre-seal
set; never retain a favorable run from the superseded source.

### G7.4 Execute sealed Phase C

Exact probe expectations:

| Probe | Roots | Fixed nodes | Semantic bytes | Bundle SHA-256 | Semantic-byte SHA-256 |
|---|---:|---:|---:|---|---|
| `no_signal` | 428 | 2,236 | 48,390,175 | `sha256:6c443906b65384f134d351e660e5c9e01c7716c2b75326b8ff6f1ab61cb9625f` | `sha256:18ee39f3547c838c106566500fef0d885d9d193ef4b4d4026379771d994083a3` |
| `regime_shift` | 459 | 2,297 | 43,520,933 | `sha256:207a5fb36ae58a42e7f06e61de94df8b6b09dd613578de247778218cf06bb99f` | `sha256:6638999ad1ee81d78f8e795dff97ecf081c08b87a63ffd111302a80dd34cec18` |
| Total | 887 | 4,533 | 91,911,108 | — | — |

Run first generation, independent repeat generation, and fresh checker inside the unchanged charged
boundary. Re-run the cost-blind 12-action v1-generator/v1-checker/new-checker/brute-reference audit
identified as `yfm8g3sample-48f0614907ac3329622d2f52` with hash
`sha256:48f0614907ac3329622d2f52a0499b2cc949d47aec153d4fb54f178e79595a3d`, and all frozen mutations.
Require exact official bundle, semantic-byte, layer, decision, state, and cost identities;
byte-identical repeat; zero hidden fallback/unsupported/mismatch; attestation; cleanup;
`peak_compute_count <= 8`; and evaluation false. Do not substitute the distinct six-cell Phase-E
audit sample.

**Phase-C pass:** fully charged outer wall `<=30.261404s`. Publish a new immutable artifact and
decision, strict-load it, externally recompute identities/math, and commit evidence/docs. A C0 or
component benchmark is never substituted for this result.

## 14. G8 — Build the deterministic real-calibration coverage pack

The prior six-cell run had 3,469/3,469 valid proofs and zero checker/reference mismatch, but observed
only `no_fit` and `state_rejoin`; `exact_escape_count` was zero. Performance alone cannot pass M8.

### G8.1 Reconcile the frozen coverage universe

**Files:** Execute the G2B/G7A-reviewed coverage runner without source edits.

Scan only the 12 sealed calibration streams in this exact order:

1. `TemporalRegime` order;
2. temporal seed;
3. event position;
4. exact complete-suffix horizon ladder `(0, 1, ..., 23)`;
5. action kind;
6. catalog action ID.

The scanner may inspect witness classification for selection. It may not inspect savings, advantage
magnitude, M8 evaluation data, or any later economic outcome.

Build a machine-checkable coverage universe matching the successor of `_coverage_gaps`:

- each of the four witness classes in full evidence and independently audited evidence;
- for each canonical runtime cell, every class and action kind present in that cell also present in
  that cell's frozen audit—action kinds are required **where present**, not unconditionally;
- all six regimes represented by the canonical cells/audit; and
- exact horizon anchors `0`, `12`, and `23`, the min/middle/max of the frozen complete-suffix ladder,
  represented in independently audited real calibration evidence, with horizon zero represented by
  a checked empty continuation and never credited as a witness-bearing continuation.

Start with atoms already satisfied by the unchanged six canonical cells and their distinct
12-action Phase-E audit. For remaining atoms, enumerate all eligible calibration examples, attach
only identity/class/kind/regime/horizon labels, then run a stable greedy minimum-cover selection:
choose the candidate covering the most uncovered atoms, break ties by the frozen order above, and
repeat until complete or no candidate adds an atom. The pack is capped by the finite universe size
(54 atoms maximum, including the distinct zero-future exactness atom); every selected candidate must
add at least one previously uncovered atom. Bind
the eligible-manifest hash, universe, selection trace, selected IDs, and uncovered atoms.

### G8.2 Validate the selected pack

Regenerate every selected action under the sealed source, run the fresh independent checker, and
match brute reference. A selected row is included in both the successor full-evidence set and the
successor audit set; its generation/check/reference process times and identities are measured and
included in Phase E's outer audit/coverage wall reporting. They remain a separate coverage-pack
timing scope and do not alter the frozen 12-action sampled-speedup denominator or the 3,469-action
six-cell projection arithmetic. The Phase-E successor computes coverage as the explicit union of canonical six-cell full
evidence plus pack full evidence, and canonical 12-action audit plus pack audit. It does not pretend
that the unchanged two-event six cells produced `exact_transition` or `policy_dominated`.

Require the union to have all four witness classes in both full and audit evidence; every per-cell
present class/kind obligation; all six regimes; horizons `0/12/23`; positive certified-event,
exact-escape, and state-rejoin counts; and zero pack checker/reference mismatch. Synthetic exhaustive
fixtures remain supporting evidence and cannot replace a missing real class.

**Coverage decision:** If no real `policy_dominated` or `exact_transition` example exists in the 12
calibration streams, the gate remains closed. Do not search evaluation. Record the negative finding
and request an explicit calibration-contract redesign; do not fabricate or rename a witness.

## 15. G9–G10 — Paired authorization and official technical M8 pass

### G9.1 Strict-load the prebuilt comparable paired-performance contract

Use the G7A schema rather than the hard-coded historical v4 model. Bind the passing Phase-C artifact;
passing G8 coverage artifact ID/hash; Phase-D sample
`yfm8g3sample-48f0614907ac3329622d2f52` and hash
`sha256:48f0614907ac3329622d2f52a0499b2cc949d47aec153d4fb54f178e79595a3d`;
portable/reference source and implementation identities; complete Python/native/geometry runtime;
CPU/topology/OS/kernel; controlled-load protocol; pre/post process census; run ordering/timestamps;
thread/process widths; per-action timings; population-weighted reference expansion; and exact
projection arithmetic. G9 makes no source/dependency edit; any necessary fix restarts the cascade at
Phase C after a new sealed-source commit.

Freeze constants: 550,542 held-out actions, 11.5 mean future events, 2.0 safety factor, 86,400
seconds/day, one baseline plus five nonbaseline actions per regime, populations 428 and 459, and an
eight-slot denominator applied **only** to population-expanded reference worker seconds.

### G9.2 Execute paired timing and authorize or hold

Execute one fixed bracket, with no adaptive warmup or replacement: fresh portable `P1`, fresh
reference `R1`, fresh portable `P2`. `R1` uses exactly eight worker compute leases; its controller is
orchestration-only and may not compute while those leases are held. The `/8` in reference expansion
is the frozen mathematical equal-slot denominator and is not an extra division of controller wall.
Each invocation uses distinct fresh role processes, bytecode/
temporary scopes, and no role-owned cache from a preceding invocation. Capture the environment before
and after every invocation. Any source/runtime drift, power-state change, non-nominal thermal state,
unexpected process/thread, slot breach, worker failure, or load deviation beyond the preregistered
bound invalidates the whole `P1-R1-P2` bracket; no member is selectively rerun. Load gating uses only
sampled **non-owned** background CPU fraction, excluding every leased worker/controller descendant;
each invocation's pre/during/post background fraction may drift by no more than
`max(0.02, 0.10 * pre_value)`. Raw one-minute load average is diagnostic only because it includes the
benchmark's own work. Thermal state must be nominal at each invocation start and must not enter an
OS-reported throttled/serious state during the invocation.

Compute:

```text
portable_charged_wall = max(P1.charged_pipeline_wall, P2.charged_pipeline_wall)
reference_equal_8_slot_wall = population_expanded_reference_worker_seconds / 8
reference_equivalent_speedup = reference_equal_8_slot_wall / portable_charged_wall
projected_days = portable_charged_wall / 887 * 550542 * 11.5 * 2 / 86400
```

There is no `/8` in the portable projection. Require exact Phase-C and G8 proof/mutation/coverage
reconciliation, genuinely comparable timing, reference speedup gating true, speedup `>=25x`, and
projected runtime `<=5` days.

A passing decision may set `authorize_official_six_cell_calibration=true`; it must keep
`evaluation_opened=false` and `official_six_cell_executed=false`. A miss publishes `hold_performance`
and authorizes no six-cell work.

### G10.1 Strict-load the prebuilt successor six-cell contract

Use the reviewed G7A runner without source/dependency edits. Preserve the failed v3 artifact. Bind
parent v3, M0/M6/M7/freeze, Phase C, Phase D authorization, passing G8 coverage pack,
all six exact catalogs, generator/checker/reference identities, frontier/lemma/root/fallback/witness
counts, exact timings and projection math, the G7.1 owned-PID RSS/swap sampler fields, evaluation
false, and claim ceiling.

The six regimes in frozen order are `no_signal`, `exact_recurrence`, `family_similarity`,
`compatible_bundle`, `high_mix`, and `regime_shift`, using seed `2026082300`, the deterministic first
calibration stream, and the registered two-event prefix.

Freeze exactly six completed cells with current-action counts `428`, `709`, `709`, `459`, `705`, and
`459` in regime order, totaling `3,469`. Freeze configured topology at eight slots, measured
cell-process count at six, and enforce the captured peak. The distinct Phase-E performance audit is
12 actions with hash
`sha256:2ee9b5d22261c7bf6d7cb5115bdccc329016fb58af5723ab2663792e0215adb1`; it is not the Phase-D
`yfm8g3sample` sample. The successor must preserve this exact 12-action count/hash; changing it
requires a new separately authorized official-gate contract and is outside this plan. Historical
`11634.541856s` is the old exact-reference **numerator** that explains the threshold lineage. It is
not a denominator and may not be reused as current speedup evidence. Phase E reruns exact reference
on this same sample inside the same captured environment as the successor certificate/checker work.

### G10.2 Freeze and run the canonical gate

Before launch, freeze commit, compatible bundle, calibration and coverage manifests, schemas,
machine/runtime/workers, audit sample, projection formula, thresholds, mutation manifest, output
path, and cleanup rules. Run generation, fresh checking, matched reference audit, coverage
reconciliation, and publication in separately timed, fail-closed phases.

Retain every raw component timing, but compute decision fields with the following exact Python
`round(value, 6)` order and no alternate aggregation path:

```text
certificate_process_seconds = round(sum(cell.certificate_elapsed_seconds), 6)
checker_process_seconds = round(sum(cell.checker_elapsed_seconds), 6)
certificate_pipeline_process_seconds = round(
    certificate_process_seconds + checker_process_seconds, 6
)
sampled_reference_process_seconds = round(
    sum(cell.sampled_reference_elapsed_seconds), 6
)
sampled_certificate_process_seconds = round(
    sum(cell.sampled_certificate_elapsed_seconds), 6
)
sampled_checker_process_seconds = round(
    sum(cell.sampled_checker_elapsed_seconds), 6
)
sampled_certificate_pipeline_process_seconds = round(
    sampled_certificate_process_seconds + sampled_checker_process_seconds, 6
)
sampled_speedup = round(
    sampled_reference_process_seconds
    / sampled_certificate_pipeline_process_seconds,
    6,
)
certificate_pipeline_wall_seconds = round(
    generator_wall_seconds + checker_wall_seconds, 6
)
observed_action_event_count = sum(
    cell.current_action_count * max(1, cell.future_event_count) for cell in cells
)
projected_held_out_calendar_days = round(
    certificate_pipeline_wall_seconds
    / observed_action_event_count
    * 550542
    * 11.5
    * 2.0
    / 86400.0,
    6,
)
```

The strict validator independently recomputes this sequence from raw cell values. Threshold
comparisons use only these rounded decision fields. The reference numerator and sampled portable
denominator must cover the exact same frozen 12 actions under the same runtime/load capture.
Comparing six-decimal fields to the literal historical constants is intentionally conservative:
`581.727092` is the greatest passing sampled-process field and `165.690569` is the greatest passing
pipeline-wall field. Do not round either threshold upward or use raw values for the decision.

### G10.3 Apply the official decision

Pass only if all of the following are true:

- all current actions are checked and valid;
- full and sampled checker failures are zero;
- generator/checker/reference mismatches are zero;
- at least one certified event, exact escape, and state rejoin exist in the explicit canonical-plus-pack union;
- all four classes occur in both full and audit evidence; every class and action kind present in a
  canonical cell occurs in that cell's audit; all six regimes and frozen horizon anchors are covered;
- sampled certificate-plus-checker process time `<=581.7270928s`;
- full six-cell generator-plus-checker `certificate_pipeline_wall_seconds <=165.69056927773784s`;
- matched exact-reference speedup `>=20x`;
- held-out projection `<=7` days;
- completed cell count is six, two-event prefixes are exact, current/checked/valid counts all
  reconcile to `3,469`, and the frozen Phase-E audit count/hash is exact;
- evaluation remains unopened;
- source/runtime/native identities, slot cap, cleanup, and immutable publication all pass; and
- the 50 ms sampler has no gap above 100 ms, the expected/observed owned process-tree closure and
  PID-role manifest reconcile, every owned-process and aggregate RSS value is finite and positive,
  the aggregate is the maximum simultaneous closure sum rather than a sum of per-process maxima,
  and the run shows no unknown/escaped descendant, OS-reported swapping, or memory-pressure breach.

The 12-action sampled process/speedup fields and six-cell pipeline/projection fields exclude the
separately named coverage-pack timing exactly as the frozen arithmetic requires; total Phase-E outer
wall reports and reconciles both scopes so coverage work is never hidden.

Require the literal technical decision `pass_certificate_exact`. Strict-load the result and
externally recompute every aggregate, identity, and decision. Commit the
artifact and roadmap update. This is the active goal-mode completion boundary. It proves only that
the M8 scorer passes the registered calibration exactness gates and projected-feasibility gates on
the registered software evidence boundary. Full-horizon operability remains unproven until G11. It
does not prove oracle advantage, savings, global optimality, physical feasibility, production
readiness, buyer demand, or commercial value.

## 16. G11 — Separately bounded full-horizon pilot after technical pass

**Precondition:** G10 passes. This section prepares evaluation authorization; it is not part of the
active technical-pass goal and does not open evaluation.

### G11.1 Freeze executable resource caps

Before running, derive and freeze:

- per-owned-process RSS cap: the lesser of 60% of physical RAM and twice the maximum valid Phase-E
  per-process RSS across workers, auditors, Jagua/native helpers, publishers, and other descendants,
  with no swapping accepted;
- aggregate owned-process RSS cap: the lesser of 80% of physical RAM and twice the maximum valid
  Phase-E aggregate owned-process RSS;
- artifact cap: twice the Phase-E semantic bytes per action-event multiplied by the exact
  per-repetition pilot action-event count, rounded up to a 4 KiB boundary;
- controller-wall cap: twice the Phase-E valid **controller wall** per action-event multiplied by the
  exact per-repetition pilot action-event count, with no further slot division;
- worker-process cap: twice the Phase-E valid worker-process seconds per action-event multiplied by
  the per-repetition pilot action-event count; and
- at most one identical outer retry for the entire failed outer operation, only after worker failure
  or outer timeout. Semantic, geometry, archive, candidate, incomplete-stream, or integrity failure
  is never retried. No semantic substitution or truncation is allowed.

If any derived cap is internally inconsistent or below the measured minimum, revise the pilot design
before execution—not after observing a breach.

`pilot_action_event_count` is derived from the complete frozen schedule for one repetition before
timing. Each repetition must fit each cap independently, and the two-repetition aggregate may not
exceed twice the corresponding cap. Retry and resume work remains charged to the affected
repetition and to the aggregate; neither clock nor resource accounting resets.

### G11.2 Freeze the exact pilot manifest

Strict-load these six complete 24-event calibration streams in this order, each at temporal seed
`2026082300`:

| Regime | Stream ID | Canonical stream-file SHA-256 |
|---|---|---|
| `no_signal` | `yfts-2c3c3149da7bf901bcc11a79` | `ad7f34e13f37a3ca8c29217d9b98949f5367869d85b7d443566661ecd3a1ebc7` |
| `exact_recurrence` | `yfts-a8e3d36d04fa34b011976236` | `01cb0ad590d775ad2afb5eb8f2703fcce643c412840ba3d8be77f0942f1d9a4b` |
| `family_similarity` | `yfts-b78f34801bbd901c60ed6e82` | `3e06fd952f42a81f762ab598a29e168bb6cf8baf6f3f39b5ef47644901c32ea5` |
| `compatible_bundle` | `yfts-54cd4a5ea3e288d41e178f31` | `0574a6f31c4cd850b5bf2f0ff3e261273dd73ee5d65c36572e8275ce816f64cc` |
| `high_mix` | `yfts-66e8799d847d5343da79a9ce` | `3af19e980fe1a43462ce34f75d0675826bc39a478fe866aee0e1baf76081222f` |
| `regime_shift` | `yfts-f320978a2d55802395294150` | `ab9980d55feadc19d3b8e3351fd4a138e23407ee0d69b230f754a812723cc72a` |

Freeze repetition R1 as cold: begin with an empty authorized role cache and, for each stream in the
table order, execute `full` then `known_only`. Freeze R2 as warm: repeat the identical stream/mode
order and admit only immutable cache entries produced by R1 that strict-load against every source,
runtime, input, role, and semantic hash. Reusing an output or producer conclusion is prohibited.

Build the exact-reference audit without reading costs, savings, classifications, or outcomes. Its
required universe is every present tuple of regime, visibility mode, event-position anchor
`{0, 11, 23}`, and action kind. For each tuple, select the first action in canonical catalog order;
deduplicate by the complete action/event/mode identity and sort by regime order, `full` before
`known_only`, event position, action-kind enum order, and action ID. Before any pilot timer starts,
the immutable manifest must contain a nonzero exact audit count, every ordered binding, its
canonical content SHA-256, and a proof that the required universe is complete. No selected action
may be replaced after timing begins.

### G11.3 Strict-load and execute the prebuilt pilot

Use only the G7A-sealed runner. Execute R1 and R2 with complete remaining horizons, fresh checker
agreement, the frozen exact-reference audit, and the same proof path. Record exact hashes, every
owned process-tree member's and the simultaneous closure's memory high-water, PID-role
reconciliation, output size, charged retry behavior, per-event distribution, process cleanup, and
`evaluation_opened=false`. The pilot must not compute or expose
calibration savings or advantage. Require exact semantic equality between R1 and R2, all frozen
caps, zero fallback/unsupported/mismatch, and a held-out projection based on the worse valid
per-action-event controller wall of R1/R2 of `<=7` days.

### G11.4 Publish and authorize the execution freeze

Bind passing Phase E/pilot, exact code/runtime/native binaries, candidate archives, M0/M6/M7 inputs,
visibility rules, checkpoint/retry/failure rules, maximum execution boundaries, all 36 evaluation
stream IDs/hashes in frozen M7 order, output path, and claim ceiling. Separately reproduce the M7
evaluation semantic identity
`sha256:47cc40ff16ab71f70163df23bb1a346c061d2765d2e2113eca5f0c06e5756cf8`.

Before `evaluation_opened=true`, the immutable authorization payload and hash must also bind all of
the following non-null values; G12 may only enforce them, never add them:

- exactly two repetitions, `E1` then `E2`; each begins with fresh isolated role processes/caches and
  traverses the 36-stream M7 order, executing `full` then `known_only` for each stream without any
  suffix/cache leakage between modes or repetitions;
- per repetition: 36 unique streams, 864 unique events, 72 mode-stream executions, and 1,728 mode-
  event executions; across both repetitions: 72 unique-stream repetitions, 1,728 unique-event
  repetitions, 144 mode-stream executions, and 3,456 mode-event executions;
- unique-stream/event counts as view and statistical-unit reconciliation; mode-stream/event counts
  as the completeness, checkpoint, artifact/resource-cap, and runtime-projection denominators;
- a seven-calendar-day and seven-day cumulative charged-controller-wall cap per repetition, a
  14-day charged-controller-wall cap across both, and the rule that checkpoint/resume/retry and all
  terminal-sensitivity work remain charged without resetting a clock;
- `negative_oracle_savings_numeric_tolerance=1e-12` fractional units with canonical binary64 bits
  `3d719799812dea11`, and invalidity for any `OracleSavings < -1e-12`;
- primary `scrap_only`, matched `zero_total_credit`, and
  `bounded_continuation_credit_at_or_below_pro_rata_virgin_value` treatment IDs, exact per-material
  rates/values and canonical bits, and the rule that a Green verdict must be invariant between
  scrap-only and zero-credit and may not depend on bounded-continuation credit;
- the exact ordered geometry-corpus ID/hash tuple and the supporting-Green requirement for positive
  evidence in at least two distinct corpora; failure of this support rule does not invalidate an
  otherwise valid Red or Yellow result;
- M0 decision precedence `invalid_then_red_then_yellow_then_green`, exact Red/Yellow/Green core bands,
  every supporting-Green threshold, the no-signal bands, bootstrap/Wilson rules, and all required
  controls; and
- the exact repetition identity exclusions, invalid-stream behavior, retry rule, publisher target,
  and no-result-on-boundary-breach rule.

Use the G7A-sealed publisher/evaluator to publish an immutable execution-freeze candidate with
`evaluation_opened=false`. An independent reviewer strict-loads and recomputes it and publishes a
content-addressed review attestation. Only then may the sealed evaluator publish the final
authorization decision binding both candidate and review hashes. It may set
`evaluation_opened=true` only for the exact 36-stream manifest and only if every binding above
passes. Root strict-loads and recomputes the final decision before invoking an evaluation command.
No tool or view may expose evaluation before that exact final authorization ID/hash exists.

## 17. G12 — Held-out evaluation and M8 milestone result

**Precondition:** A separately reviewed, immutable authorization artifact explicitly opens exactly
the registered 36-stream evaluation view. This is outside the active technical-pass goal.

### G12.1 Strict-load the prebuilt contracts and authorization

Strict-load the G7A-sealed stream result, execution freeze, authorization, checkpoint/resume,
invalid-stream, aggregate-summary, terminal-sensitivity, and paired-statistics contracts and the
exact G11.4 authorization ID/hash. Independently verify every G11.4 field, including the literal
`1e-12` tolerance and bits. A value below `-1e-12` is materially negative; the tolerance is an
integrity guard, not permission to round a result to zero. The CLI must refuse every missing, stale,
mismatched, broader, incomplete, or differently hashed authorization.

### G12.2 Execute the frozen evaluation

- In each repetition, open exactly 36 unique streams / 864 unique events and execute exactly 72
  mode-streams / 1,728 mode-events. Across E1+E2 reconcile exactly 72 unique-stream repetitions /
  1,728 unique-event repetitions and 144 mode-stream / 3,456 mode-event executions. Use unique
  counts for view/statistical completeness and mode counts for resource/checkpoint/runtime
  completeness exactly as authorized.
- Use immutable M7 baseline costs and exact candidate parity.
- Follow the authorized order and fresh-isolation policy; run `full` then `known_only` with complete
  remaining horizons for each stream, with no role cache or realized suffix crossing a mode or
  repetition boundary.
- Checkpoint every action/event; never substitute stream, seed, action, candidates, or policy.
- Allow only the frozen M0-identical retry; abort rather than truncate on a boundary breach.
- For each repetition, abort rather than exceed either seven calendar days elapsed or seven days of
  cumulative charged controller wall. Checkpoint, resume, and the single permitted retry all count;
  neither clock resets; terminal-sensitivity work is inside the same boundary. The two-repetition
  cumulative charged controller wall may not exceed 14 days. A breach emits no M8 savings result
  and follows only the preregistered distributed-exact successor.
- Keep any invalid stream visible, give it no numeric savings, and prevent a valid aggregate verdict.
- Run the scrap-only terminal treatment as primary and the exact M0-required matched sensitivities:
  zero total terminal credit and bounded continuation credit no greater than pro-rata virgin value.
  Strict-load the already authorized treatment IDs, rates/values, and bits; never choose a terminal
  rule after reading savings.
- Count positive source-faithful geometry evidence by the prebound corpus IDs. Fewer than two
  positive corpora blocks only a Green verdict; it remains a reported support failure in a valid
  Red or Yellow result.
- Require exact semantic artifact identity between repetitions, excluding only preregistered
  runtime/measurement fields, before publishing.

### G12.3 Publish the valid M8 result

Report every stream/failure and require a positive baseline denominator before calculating the
preregistered `OracleSavings` and `UnknownFutureContribution`. Publish both repetition identities,
their exact semantic-identity comparison, the 36-stream/864-event per-repetition reconciliation,
and the aggregate mean/median. E2 is a reproducibility execution, not an additional independent
sample: after exact E1/E2 semantic equality, compute inferential statistics once over the 36 paired
stream results, never over 72 duplicated stream-repetition rows. Publish deterministic stratified
paired percentile 10,000-resample bootstrap with seed 0 and 95% interval; P10 and worst-decile mean;
positive-stream fraction with its 95% Wilson interval; both top-10-stream and top-10-decision
concentration; no-signal diagnostic; opportunity frequency; action divergence; immediate sacrifice;
remnant realization; ordinary candidate availability; the identities and positive-evidence counts
for every prebound geometry corpus; scrap-only, zero-credit, and bounded-continuation terminal
results; and runtime/reuse diagnostics.

Apply the exact M0 precedence and bands, using percent/percentage-point fields as registered:

1. **Invalid first.** Any invalid stream, nonpositive baseline denominator, identity/count/control
   failure, or `OracleSavings < -1e-12` prevents a valid aggregate interpretation. A no-signal mean
   above `0.5%` is `invalid_pending_diagnosis` and withholds the aggregate verdict until resolved.
2. **Red.** Mean OracleSavings below `1.5%` **or** UnknownFutureContribution below `0.5` percentage
   points.
3. **Yellow after Red.** Not Red, and the worst core metric lies in OracleSavings `1.5%` to below
   `2.5%` or UnknownFutureContribution `0.5` to below `1.5` percentage points.
4. **Green.** OracleSavings at least `2.5%` **and** UnknownFutureContribution at least `1.5`
   percentage points, and every supporting gate passes.

Supporting Green gates are: mean immediate sacrifice `<=0.5%`; ordinary candidate availability
`>=60%`; opportunity frequency `>=20%`; remnant realization `>=60%`; top-10 decisions contribute
`<=25%` of savings; median and lower 95% mean bound each strictly above zero; positive-stream
fraction strictly above `50%`; positive evidence in at least two geometry corpora; no-signal mean
from `0%` through `0.3%`; and a decision band invariant between scrap-only and zero-total-credit that
does not depend on bounded-continuation credit. No-signal above `0.3%` through `0.5%` requires
investigation and cannot support Green until resolved. Missing a supporting gate does not invalidate
a valid Red/Yellow result; if the core metrics are Green-range, publish
`core_metrics_green_range_but_green_not_supported` rather than inventing a lower band.

Strict-load or execute every M0-required control: no-signal, common persisted seeds, known-only,
exact small cases, terminal/remnant sensitivities, ordinary versus expanded search, rollout versus
beam, and strong versus myopic baseline. Green additionally requires beam to match the exact optimum
on every registered small case within accounting tolerance. Preserve search-quality and claim
ceilings. M9 evaluates search gap; M10 makes the frozen project decision.

## 18. Absolute failure and escalation rules

| Condition | Required action |
|---|---|
| Unexpected dirty file | Stop writes, preserve status/diff, identify ownership; never reset |
| Test or review blocker | Diagnose/fix within the task, rerun, and re-review before advancing |
| Semantic/byte/cost/state mismatch | Correctness no-go; timing is uninterpretable |
| Integrity drift becomes unsupported/fallback | Integrity no-go |
| Source/runtime/native drift or slot breach | Invalid run; reap owned work and publish nothing |
| Inclusive timing double-count or contamination | Invalid measurement; no performance claim |
| C0 misses | Close C0-specific arm; continue to the universal feasibility path only when the exact G0A amendment ID/hash authorizes it |
| Coverage-existence cap expires before proof | Publish `coverage_search_incomplete`; no performance build until a versioned larger cap is authorized |
| Every preregistered admissible current-CPU arm reaches an exact G6 full pipeline but misses `30.261404s`, and no authorized optimization branch remains | Publish a scoped current-contract no-go; request authority for a compiled-input amendment or different hardware |
| Phase C exceeds `30.261404s` | No Phase D, six-cell, pilot, or evaluation |
| Coverage pack lacks a real required witness | No official pass and no evaluation discovery |
| Phase D misses `25x` or five days | Hold performance; no six-cell run |
| Phase E misses any official criterion | Official technical M8 no-go; no pilot/evaluation |
| Pilot is nondeterministic or breaches frozen caps | No evaluation authorization |
| M7 compatibility identity differs | No evaluation authorization |
| Evaluation stream is invalid | No numeric aggregate M8 verdict |
| Publication target contains different bytes | Abort; never overwrite |
| Threshold/semantics change after evaluation opens | Invalidate the run and create a new contract/version |

“Infeasible” is scoped. C0 failure proves only that C0 is insufficient, and a G3 timing miss selects
an arm without proving a lower bound. A current-contract/current-CPU no-go requires every
preregistered admissible arm to reach an exact fully assembled G6 measurement, miss the unchanged
outer-wall gate, and leave no authorized optimization branch. This is exhaustion of the frozen plan,
not a theorem that faster software is impossible. Hardware, GPU, slot, schema, or threshold changes
require explicit authorization and a new runtime/measurement contract; they cannot retroactively
convert an old run into a pass.

A frozen no-go/hold is a valid execution result but not a completed pass goal. Preserve it, report
the scoped boundary, and activate only an authorized alternative. Never mark the goal complete merely
because the current path ended.

## 19. Completion checklist

Do not mark the active goal complete until all items through G10 are true:

- [ ] Inherited Task 2 is independently accepted and committed.
- [ ] G0A binds this committed specification and every executable gate/branch.
- [ ] C0 is completed and receives an immutable pass/no-go decision.
- [ ] Both-arm exclusive timing attribution is at least 95%.
- [ ] Early real-calibration census proves the missing witness classes exist without evaluation.
- [ ] Universal compiled architecture preserves exact semantics and role independence.
- [ ] Two official portable probes retain exact bytes, identities, costs, states, and decisions.
- [ ] Historical plus successor mutations all reject at the intended real boundary.
- [ ] Source/runtime/native/thread/slot/cleanup evidence passes.
- [ ] Every G8–G12 executable contract/runner/evaluator is reviewed and committed before sealed
      Phase C; no later source change escapes the Phase-C-to-Phase-E rerun cascade.
- [ ] Fully charged Phase C completes in at most `30.261404s`.
- [ ] Canonical cells plus the regenerated coverage pack satisfy every successor full/audit,
      per-cell present-class/kind, regime, horizon, and positive-count obligation.
- [ ] Comparable paired Phase D reaches at least `25x` and at most five projected days.
- [ ] Official six-cell Phase E reaches at least `20x`, at most seven days, and every exactness,
      coverage, process, wall, RSS, and integrity condition.
- [ ] Evaluation remains unopened.
- [ ] The official technical M8 artifact strict-loads and its identity/math are independently recomputed.
- [ ] Documentation states the software-only claim ceiling and separates observed from projected facts.

Passing G10 authorizes preparation of G11. It does not itself authorize evaluation or establish a
positive M8 economic result.
