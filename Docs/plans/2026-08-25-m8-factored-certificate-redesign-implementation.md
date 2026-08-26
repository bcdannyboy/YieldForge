# M8 Factored Certificate Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace repeated M8 common-path proof reconstruction with a factored, independently checked certificate system that can pass the unchanged 20x runtime and seven-day projection gates without evaluation data.

**Architecture:** Retain conservative rejection scalars while building each verified candidate, reduce them to a Pareto-minimal frontier, prove common transition facts once, and let action certificates reference those facts. Keep the current v1 certificate path as an exact differential oracle until the new v2 path passes equivalence, adversarial, coverage, and performance gates. The checker evaluates stored algebraic facts and references independently; it must not call the certificate generator or reconstruct full action catalogs.

**Tech Stack:** Python 3.12, Pydantic/dataclasses used by the existing YieldForge contracts, pytest, existing M7/M8 replay infrastructure, deterministic JSON artifacts, `time.process_time_ns` and `time.perf_counter_ns` for profiling.

---

## Execution rules

- Work in `/Users/danielbloom/Desktop/YieldForge/.worktrees/m6-temporal-benchmark` on `codex/m8-rollout-preparation`.
- Preserve the dirty tree if one appears. Do not rewrite or delete existing M8 v3 artifacts.
- Use test-driven development for every behavioral change: add the failing test, observe the intended failure, implement the minimum change, and rerun the focused test.
- Keep commits task-scoped. Do not combine profiling, proof semantics, and runner changes into one commit.
- Treat current v1 proof generation as the differential oracle, not as the implementation to copy into the independent checker.
- Stop at any explicit no-go gate. Record the failure and profile evidence before redesigning.
- Do not inspect, load, score, or summarize evaluation-split outcomes in this sequence.
- Do not claim material savings, physical feasibility, production readiness, buyer demand, or commercial validity from M8 software evidence.

## Frozen success criteria

The official M8 decision thresholds remain unchanged:

- sampled pipeline process time `<= 581.7270928` seconds;
- sampled pipeline wall time `<= 165.69056927773784` seconds;
- projected canonical runtime `<= 7` days;
- required speedup `>= 20x` against the frozen reference;
- checker/generator mismatch count `== 0`;
- evaluation access `false`;
- proof coverage complete for every emitted action certificate.

The engineering checkpoint is deliberately stricter:

- observed speedup `>= 25x` on the abbreviated calibration checkpoint;
- projected canonical runtime `<= 5` days;
- zero semantic, differential, and adversarial-checker failures.

The extra margin is required so normal machine noise does not turn an engineering pass into an official borderline failure.

### Task 1: Add scoped phase profiling

**Files:**

- Create: `yf/src/yieldforge/oracle/profiling.py`
- Create: `yf/tests/oracle/test_m8_profiling.py`
- Modify: `yf/src/yieldforge/oracle/compiled.py`
- Modify: `yf/src/yieldforge/oracle/certificates.py`
- Modify: `yf/src/yieldforge/oracle/sparse.py`
- Modify: `yf/src/yieldforge/oracle/checker.py`
- Modify: `yf/src/yieldforge/experiment/m8_certificate_gate.py`
- Modify: the existing YieldForge CLI registration module located with `rg "m8-certificate" yf/src`

**Step 1: Write the failing timing-contract tests**

Add tests that require:

- nested named phases;
- both process and wall nanoseconds;
- exact integer counts for events, candidates, frontier entries, actions, facts, and fallbacks;
- deterministic JSON serialization after durations are normalized out;
- zero overhead behavior when profiling is disabled.

**Step 2: Run the focused test and confirm it fails**

```bash
cd yf
uv run pytest tests/oracle/test_m8_profiling.py -q
```

Expected: import or contract failures because `profiling.py` does not exist.

**Step 3: Implement the profiler**

Create a small context-manager API with explicit phase names. Instrument, at minimum:

- problem/candidate verification;
- scalar frontier construction;
- standard layout materialization;
- common-transition derivation;
- action-catalog enumeration;
- fact serialization;
- checker load;
- checker algebra;
- reference audit;
- artifact write.

Do not add logging inside per-placement geometry loops.

**Step 4: Add a calibration-only profiling command**

Register `m8-certificate-profile`. It must accept explicit regime, seed, event count, and output path, default to no evaluation access, and refuse an evaluation split argument.

**Step 5: Run two representative probes**

Profile one `no_signal` stream and one `regime_shift` stream. Save the reports under the existing ignored experiment-output location, not under source control.

**Gate 1:** Continue only if named phases account for at least 90% of measured pipeline process time. If not, add one level of instrumentation before moving on.

**Step 6: Verify and commit**

```bash
cd yf
uv run pytest tests/oracle/test_m8_profiling.py -q
git add yf/src/yieldforge/oracle/profiling.py yf/src/yieldforge/oracle/compiled.py yf/src/yieldforge/oracle/certificates.py yf/src/yieldforge/oracle/sparse.py yf/src/yieldforge/oracle/checker.py yf/src/yieldforge/experiment/m8_certificate_gate.py yf/tests/oracle/test_m8_profiling.py
git commit -m "perf: profile M8 certificate phases"
```

### Task 2: Retain verified rejection scalars

**Files:**

- Modify: `yf/src/yieldforge/baseline/archives.py`
- Modify: the contract module containing `VerifiedProblemCandidates`
- Modify: relevant baseline archive tests found with `rg "VerifiedProblemCandidates" yf/tests`
- Modify: any serialized fixtures directly affected by the additive field

**Step 1: Write failing retention tests**

For every accepted candidate, require retained values for:

- material identifier;
- occupied area;
- footprint width;
- footprint height;
- stable candidate identifier;
- source orientation or transform identifier needed to reproduce the measurement.

Require equality between retained scalars and a fresh `prepare_layout_footprint` computation. Include empty, rotated, flipped, and multi-piece candidates already represented in fixtures.

**Step 2: Confirm failure**

```bash
cd yf
uv run pytest tests/baseline -q -k "candidate and scalar"
```

**Step 3: Add an immutable rejection-layout record**

Extend `VerifiedProblemCandidates` additively with `rejection_layouts`. Populate it at the point where candidate geometry is already prepared. Do not perform a second geometry pass.

Validate that numbers are finite and non-negative and that every accepted candidate has exactly one matching record. Preserve older artifact loading by giving the new field a safe default only where backward compatibility is required.

**Step 4: Verify and commit**

```bash
cd yf
uv run pytest tests/baseline -q
git add yf/src/yieldforge/baseline yf/tests/baseline
git commit -m "perf: retain verified rejection scalars"
```

### Task 3: Build and verify the Pareto frontier

**Files:**

- Create: `yf/src/yieldforge/oracle/frontier.py`
- Create: `yf/tests/oracle/test_frontier.py`
- Modify: `yf/src/yieldforge/oracle/compiled.py`

**Step 1: Write failing frontier tests**

Cover:

- material partitions;
- duplicate scalar triples;
- strict and non-strict dominance;
- deterministic tie-breaking by stable candidate ID;
- candidates incomparable on width versus height;
- empty input;
- invalid scalar rejection;
- order independence.

Define candidate `a` as dominating candidate `b` within the same material when `a.area <= b.area`, `a.width <= b.width`, and `a.height <= b.height`, with at least one strict inequality or deterministic duplicate selection.

**Step 2: Add the core contracts and algorithm**

Create immutable `RejectionScalar` and `ParetoFrontier` contracts. Prefer the simplest deterministic implementation first; corpus sizes do not justify a complex spatial index until measured.

Return both retained entries and a map from each eliminated candidate to its dominating retained entry so the proof artifact remains auditable.

**Step 3: Add differential property tests**

For fixed-seed generated candidate sets and existing corpus fixtures, compare:

- full-set conservative rejection; and
- frontier-only conservative rejection.

The boolean result must match for all tested sheet states. Also assert that no retained frontier member dominates another retained member.

**Step 4: Integrate as a compiled problem field**

Compute the frontier once per verified problem. Do not change certificate behavior yet.

**Step 5: Verify and commit**

```bash
cd yf
uv run pytest tests/oracle/test_frontier.py tests/oracle/test_compiled.py -q
git add yf/src/yieldforge/oracle/frontier.py yf/src/yieldforge/oracle/compiled.py yf/tests/oracle/test_frontier.py
git commit -m "feat: add M8 rejection frontier"
```

### Task 4: Prove the fast common-transition path

**Files:**

- Modify: `yf/src/yieldforge/oracle/compiled.py`
- Modify: `yf/src/yieldforge/oracle/certificates.py`
- Modify: the baseline replay module that currently materializes M7 action catalogs
- Create: `yf/tests/oracle/test_fast_common_transition.py`

**Step 1: Write exact-equivalence tests**

For each frozen calibration regime and multiple fixed seeds, compare the current authoritative common-transition derivation with the proposed fast path. Require exact equality for:

- outcome kind;
- transition identity;
- conservative-rejection reason;
- material and dimensional witness values;
- remaining state used by downstream action proofs.

Include cases that pass scalar rejection and therefore require standard-layout materialization.

**Step 2: Implement the rejection-first path**

Use the compiled Pareto frontier before constructing a fresh runtime or enumerating the full M7 action catalog. If all future candidates are conservatively rejected, emit the exact common-transition fact from retained scalar witnesses.

For non-rejected cases, call an explicit authoritative fallback that materializes only what the standard action needs. Never silently treat an uncertain fast-path result as a rejection.

**Step 3: Add fallback and observability constraints**

Increment exact counters for:

- frontier-rejected transitions;
- standard-only materializations;
- full-authoritative fallbacks;
- mismatches discovered by differential mode.

Provide a development-only differential switch that computes both paths and fails on the first mismatch.

**Step 4: Benchmark the heavy phase**

Use the Task 1 probe inputs and compare common-transition process time, excluding fixture load and artifact writes.

**Gate 2, the early implementation go/no-go:**

- semantic mismatches must be zero;
- heavy-path process time must improve by at least `10x` on both representative probes;
- full-authoritative fallback rate must be explained and low enough for the official target to remain plausible.

If any condition fails, stop. Preserve the profile and mismatch fixture; do not build the new proof schema on an unproven fast path.

**Step 5: Verify and commit**

```bash
cd yf
uv run pytest tests/oracle/test_fast_common_transition.py tests/oracle/test_certificates.py -q
git add yf/src/yieldforge/oracle/compiled.py yf/src/yieldforge/oracle/certificates.py yf/tests/oracle/test_fast_common_transition.py
git commit -m "perf: factor M8 common transitions"
```

### Task 5: Define the fact-DAG certificate contracts

**Files:**

- Create: `yf/src/yieldforge/oracle/facts.py`
- Create: `yf/tests/oracle/test_facts.py`
- Modify: the existing M8 artifact contract module

**Step 1: Write failing schema tests**

Specify an additive v2 proof schema with:

- content-addressed fact IDs;
- candidate scalar facts;
- dominance facts;
- common-transition facts;
- branch-influence facts;
- action proofs containing fact references rather than copied common evidence;
- deterministic topological serialization;
- explicit schema version and hash-domain prefix.

Tests must reject dangling references, duplicate IDs with unequal content, cycles, unknown fact kinds, unsorted serialization, and non-canonical numeric encodings.

**Step 2: Implement canonical fact hashing**

Hash semantic content only. Exclude profiling durations, output paths, timestamps, and insertion order. Normalize enums, integers, and decimal encodings through one canonical JSON function.

**Step 3: Preserve v1 compatibility**

Strict-load the frozen v3 artifact and its v1 action proofs unchanged. The new contracts must be additive and selected explicitly by schema version.

**Step 4: Verify and commit**

```bash
cd yf
uv run pytest tests/oracle/test_facts.py tests/oracle/test_contracts.py -q
git add yf/src/yieldforge/oracle/facts.py yf/tests/oracle/test_facts.py
git commit -m "feat: define M8 fact DAG"
```

### Task 6: Implement the factored certificate generator

**Files:**

- Create: `yf/src/yieldforge/oracle/factored.py`
- Create: `yf/tests/oracle/test_factored_generator.py`
- Modify: `yf/src/yieldforge/oracle/certificates.py`
- Modify: `yf/src/yieldforge/oracle/sparse.py`

**Step 1: Write failing generator tests**

Require that the generator:

- emits each candidate scalar fact once per problem;
- emits each common-transition fact once per event/state;
- reuses fact IDs across action proofs;
- represents exact-transition and policy-dominated branches explicitly when present;
- produces byte-identical semantic output on repeated runs;
- never accesses evaluation data.

**Step 2: Implement the v2 generator**

Construct the fact store in dependency order:

1. verified candidate scalar facts;
2. Pareto dominance facts;
3. common-transition facts;
4. action-specific branch-influence facts;
5. action proof records referencing the needed facts.

Keep action-specific claims separate from shared facts. Do not embed large candidate or transition payloads in every action proof.

**Step 3: Add v1 differential mode**

On bounded calibration fixtures, generate both v1 and v2 proofs and compare their normalized semantic verdicts. The normalization must be test-only and independently reviewed; it may not erase witness differences that affect passivity.

**Step 4: Verify and commit**

```bash
cd yf
uv run pytest tests/oracle/test_factored_generator.py tests/oracle/test_certificates.py tests/oracle/test_sparse.py -q
git add yf/src/yieldforge/oracle/factored.py yf/src/yieldforge/oracle/certificates.py yf/src/yieldforge/oracle/sparse.py yf/tests/oracle/test_factored_generator.py
git commit -m "feat: generate factored M8 certificates"
```

### Task 7: Build the independent algebraic checker

**Files:**

- Create: `yf/src/yieldforge/oracle/fact_checker.py`
- Create: `yf/tests/oracle/test_fact_checker.py`
- Modify: `yf/src/yieldforge/oracle/checker.py`

**Step 1: Write failing independence tests**

Patch or remove the generator entry points during checker tests. The v2 checker must still validate a stored certificate. Assert specifically that it does not call:

- `certify_event_passivity`;
- the factored generator;
- M7 action-catalog enumeration;
- full geometry placement or replay code.

**Step 2: Specify each algebraic rule in tests**

Test the checker independently for:

- scalar-witness consistency;
- same-material dominance;
- Pareto-frontier completeness relative to the stored candidate fact set;
- conservative area/width/height rejection implications;
- common-transition identity and dependency validity;
- action branch-influence implications;
- final passivity verdict construction.

The checker may validate supplied arithmetic and hashes. It must not rediscover facts through the generator’s control flow.

**Step 3: Implement fail-closed checking**

Unknown schemas, fact kinds, dependencies, numeric encodings, or action branches must produce an explicit failure. Report the first failing fact ID plus stable error code; do not silently downgrade to a warning.

**Step 4: Add adversarial mutation tests**

Starting from valid v2 fixtures, mutate one field at a time:

- candidate material;
- one scalar value;
- dominance direction;
- common transition;
- branch influence;
- fact reference;
- content hash;
- action verdict;
- topological ordering.

Every mutation must fail for the intended reason. Include mutations that recompute the outer artifact hash so validation cannot rely only on transport integrity.

**Step 5: Differentially check v1 and v2 verdicts**

For the bounded calibration corpus, require equal event/action passivity verdicts and zero checker/reference disagreements.

**Gate 3:** Continue only with zero differential mismatches and 100% rejection of the adversarial mutation suite.

**Step 6: Verify and commit**

```bash
cd yf
uv run pytest tests/oracle/test_fact_checker.py tests/oracle/test_checker.py -q
git add yf/src/yieldforge/oracle/fact_checker.py yf/src/yieldforge/oracle/checker.py yf/tests/oracle/test_fact_checker.py
git commit -m "feat: independently check M8 fact proofs"
```

### Task 8: Construct the deterministic calibration coverage pack

**Files:**

- Create: `yf/src/yieldforge/oracle/coverage.py`
- Create: `yf/tests/oracle/test_m8_coverage.py`
- Modify: the CLI registration module
- Modify: M8 artifact contracts to carry the coverage manifest

**Step 1: Write failing selection tests**

Define coverage keys using calibration metadata only:

- regime;
- seed;
- future event index;
- horizon position;
- action kind;
- stable action identifier;
- proof branch kind.

Require deterministic ordering and deterministic minimal selection. Never order by result quality, savings, or evaluation outcome.

**Step 2: Implement a two-stage scanner**

Stage A performs a cheap calibration-only metadata scan and records which proof branches each possible case can exercise. Stage B selects the smallest deterministic set that covers all observed required keys, using stable lexicographic tie-breaking.

The scanner must refuse evaluation paths and must emit an access audit showing `evaluation_accessed: false`.

**Step 3: Add explicit rare-branch handling**

The coverage report must distinguish:

- branch covered;
- branch constructible but not selected;
- branch absent from the frozen calibration corpus;
- branch unsupported by current implementation.

An absent branch is not equivalent to proof coverage. If `exact_transition` or `policy_dominated` is absent, report it as an unresolved corpus-coverage condition rather than fabricating a passing example.

**Step 4: Add the coverage CLI command**

Register `m8-certificate-coverage` with explicit calibration manifest, output path, and selection budget. The output is a reusable manifest with input hashes.

**Gate 4:** Continue only when every branch actually emitted by the generator is covered and all expected action kinds are represented. Carry truly absent branches as explicit limitations into the gate artifact.

**Step 5: Verify and commit**

```bash
cd yf
uv run pytest tests/oracle/test_m8_coverage.py -q
git add yf/src/yieldforge/oracle/coverage.py yf/tests/oracle/test_m8_coverage.py
git commit -m "feat: select deterministic M8 coverage"
```

### Task 9: Add the v4 factored M8 gate runner

**Files:**

- Modify: `yf/src/yieldforge/experiment/m8_certificate_gate.py`
- Modify: the M8 CLI module
- Create: `yf/tests/experiment/test_m8_factored_gate.py`
- Modify: M8 schemas and strict-load tests

**Step 1: Write failing end-to-end gate tests**

Require the runner to:

- consume the frozen compatible bundle and calibration coverage manifest;
- run generation and checking as isolated phases;
- capture process and wall time for each phase;
- record phase exit status, input hashes, and exact counters;
- aggregate without inspecting evaluation data;
- compute the official projection formula unchanged;
- apply the unchanged 20x and seven-day decisions;
- strict-load the final artifact.

**Step 2: Implement resumable, content-addressed shards**

Each shard should be determined by regime, seed, event range, and input hash. Completed shards may be reused only if strict validation succeeds. A failed or partial shard must never be counted as complete.

Keep generation, checker, and reference-audit process accounting separate so parent-process CPU accounting cannot hide child work.

**Step 3: Implement explicit failure semantics**

The run fails closed on:

- a missing shard;
- a corrupt fact store;
- a checker crash;
- a mismatch;
- incomplete coverage;
- evaluation access;
- timing fields that cannot be reconciled;
- a projection or speed gate failure.

**Step 4: Preserve historical artifact loading**

Strict-load the canonical v3 artifact `yfm8proof-b296ba919c07d55ece14c6db` without rewriting it. v4 must be a new artifact lineage with explicit parent references.

**Step 5: Verify and commit**

```bash
cd yf
uv run pytest tests/experiment/test_m8_factored_gate.py tests/experiment/test_m8_certificate_gate.py -q
git add yf/src/yieldforge/experiment/m8_certificate_gate.py yf/tests/experiment/test_m8_factored_gate.py
git commit -m "feat: run factored M8 certificate gate"
```

### Task 10: Run the abbreviated engineering checkpoint

**Files:**

- Create: `Docs/experiments/m8-factored-checkpoint.md`
- Generate: ignored v4 checkpoint artifacts in the existing experiment-output directory
- Modify tests or implementation only if the checkpoint reveals a reproducible defect

**Step 1: Run the complete focused test suite**

```bash
cd yf
uv run pytest tests/oracle tests/experiment/test_m8_factored_gate.py -q
```

**Step 2: Run light and heavy differential cases**

Use at least one cheap rejection-dominated case and one case that reaches standard-layout materialization. Execute each twice to expose non-determinism and cache-sensitive timing.

**Step 3: Run the six-cell checkpoint**

Use the frozen representative regimes, fixed seeds, and the deterministic coverage pack. Capture generator, checker, audit, and end-to-end timings separately.

Do not tune the official baseline or omit slow cells after seeing results.

**Step 4: Apply the internal checkpoint gate**

Require all of:

- zero v1/v2 semantic mismatches;
- zero checker/reference mismatches;
- 100% expected emitted-branch coverage;
- evaluation access false;
- observed speedup at least `25x`;
- projected runtime no more than `5` days;
- byte-identical semantic artifacts across the two repeated runs.

**Gate 5, the main go/no-go:** If any condition fails, do not launch the canonical run. Diagnose the dominant failed phase, record the no-go, and return to the narrowest responsible task.

**Step 5: Record and commit the checkpoint**

The note must include exact commands, input hashes, machine information, timing table, coverage limitations, mismatch counts, verdict, and claim ceiling.

```bash
git add Docs/experiments/m8-factored-checkpoint.md
git commit -m "experiment: record M8 factored checkpoint"
```

### Task 11: Execute the canonical v4 M8 gate

**Files:**

- Generate: canonical ignored M8 v4 artifact directory
- Create: `Docs/experiments/m8-factored-canonical-gate.md`
- Modify: canonical experiment index or manifest used by existing M8 documentation

**Step 1: Freeze the run inputs**

Record before launch:

- code commit;
- compatible-bundle hash;
- calibration manifest hash;
- coverage-pack hash;
- schema versions;
- machine and worker configuration;
- official projection formula and thresholds.

Do not change these after timing begins.

**Step 2: Run generation, checking, and reference audit**

Use isolated phases with resumable validated shards. If interrupted, resume only strictly validated completed shards and report the interruption.

**Step 3: Strict-load and reconcile the artifact**

Independently sum shard counts and timings and compare them with the parent manifest. Require all proof references to resolve and all declared coverage cells to be present.

**Step 4: Apply the official decision**

The official M8 pass requires all frozen criteria at the top of this plan. Neither the internal `25x` target nor the `5`-day projection replaces the official `20x` and seven-day wording; they are engineering margin only.

**Gate 6:** A canonical failure remains a failure. Do not reinterpret it as a conditional pass or retune the threshold after observation.

**Step 5: Document and commit**

The canonical note must distinguish observed from projected timings, list absent proof branches, state that evaluation remained sealed, and repeat the software-only claim ceiling.

```bash
git add Docs/experiments/m8-factored-canonical-gate.md
git commit -m "experiment: complete factored M8 gate"
```

### Task 12: Prepare the full-horizon calibration pilot

**Precondition:** Task 11 passed. If M8 did not pass, do not begin this task.

**Files:**

- Create: `yf/src/yieldforge/experiment/m8_full_horizon_pilot.py`
- Create: `yf/tests/experiment/test_m8_full_horizon_pilot.py`
- Create: `Docs/experiments/m8-full-horizon-calibration-pilot.md`

**Step 1: Write failing pilot-contract tests**

Require one frozen 24-event calibration stream per regime, two repeated runs, deterministic seeds, artifact hash equality, and stratified exact-reference audit selection.

The pilot must refuse evaluation input and must not compute or expose material-savings outcomes.

**Step 2: Implement the calibration-only runner**

Reuse the v4 generator and checker without adding a parallel proof path. Record memory high-water mark, shard retry behavior, output size, and per-event time distribution.

**Step 3: Run the pilot**

The pilot is operational preparation, not another opportunity to alter the completed M8 decision. Diagnose whether runtime, memory, or artifact size scales unexpectedly at full horizon.

**Gate 7:** Proceed to a separately authorized evaluation plan only if repeated semantic artifacts match, checker mismatches remain zero, resource behavior is bounded, and evaluation access remains false.

**Step 4: Document and commit**

```bash
git add yf/src/yieldforge/experiment/m8_full_horizon_pilot.py yf/tests/experiment/test_m8_full_horizon_pilot.py Docs/experiments/m8-full-horizon-calibration-pilot.md
git commit -m "experiment: prepare M8 full-horizon pilot"
```

## Final verification checklist

- The canonical v3 M8 artifact still strict-loads unchanged.
- The v2 checker passes while generator entry points are disabled.
- Every candidate scalar is derived once from verified geometry and retained.
- Pareto-frontier and full-set conservative rejection agree on all differential fixtures.
- The fast path has zero semantic mismatches against the authoritative path.
- Fact IDs and semantic artifact bytes are deterministic across repeats.
- Every emitted action proof resolves all referenced facts.
- Every adversarial mutation is rejected by the intended checker rule.
- Calibration coverage is deterministic and explicitly reports absent branches.
- Generation, checking, and reference audit timings are independently accounted.
- The official `20x`, seven-day, and evaluation-sealed gates are unchanged.
- The engineering `25x`, five-day checkpoint is used only as safety margin.
- No evaluation outcome or savings metric was inspected during this sequence.
- Documentation reports software evidence without physical, savings, buyer, or commercial promotion.
