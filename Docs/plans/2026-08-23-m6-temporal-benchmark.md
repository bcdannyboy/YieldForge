# M6 Temporal Benchmark Implementation Plan

> Execute in order with red-green-refactor cycles and a reviewable commit after each task.

**Goal:** Publish and validate a 48-stream, source-faithful, content-addressed temporal benchmark
that closes the M6 acceptance gate and hands exact replay-ready demand to M7.

**Architecture:** Preserve the legacy two-task order-book v1 subsystem. Add a dedicated
`yieldforge.temporal_benchmark` package whose strict contracts bind the frozen M0 contract and full
Lectra catalog. The generator selects only the 254 runnable tasks, realizes six measured regimes,
and publishes immutable stream/population artifacts. The lowerer resolves every event through the
existing source-recorded projection adapter and forms M0-compatible batches.

**Stack:** Python 3.12, Pydantic 2, existing normalized-catalog/parser and projection code, Shapely
2.1.2, pytest, Ruff, canonical JSON repository conventions.

---

## Task 1: Freeze strict benchmark contracts

**Files:**

- Create: `yf/src/yieldforge/temporal_benchmark/__init__.py`
- Create: `yf/src/yieldforge/temporal_benchmark/contracts.py`
- Create: `yf/tests/temporal_benchmark/test_contracts.py`

**Red:** Write failing tests for strict/frozen models, exact M0/catalog bindings, six regimes,
48 unique population cells, 12/36 partition counts, common seeds, generated/assumed provenance,
finite nonnegative rates, registered candidate budget, canonical content identity, and tamper
rejection.

**Green:** Implement the smallest versioned Pydantic contracts and semantic identity helpers that
satisfy those tests.

**Verify:**

```bash
uv run --all-groups pytest tests/temporal_benchmark/test_contracts.py -q
uv run ruff check src/yieldforge/temporal_benchmark tests/temporal_benchmark
```

**Commit:** `feat: freeze M6 temporal benchmark contract`

## Task 2: Validate and index the full source catalog

**Files:**

- Create: `yf/src/yieldforge/temporal_benchmark/catalog.py`
- Create: `yf/tests/temporal_benchmark/test_catalog.py`

**Red:** Test exact artifact/manifest identity, 256/254/2 task census, visible exclusions, stable
task/part/shape indexes, stock signatures, family signatures, source-recorded projection, and
fail-closed behavior for a wrong path, hash, missing geometry, or view-only task.

**Green:** Reuse the bounded passive parser and catalog manifest evidence. Build immutable in-memory
indexes and delegate solver lowering to the existing `project_task` implementation.

**Verify:**

```bash
uv run --all-groups pytest tests/temporal_benchmark/test_catalog.py -q
```

**Commit:** `feat: index M6 source catalog evidence`

## Task 3: Implement six measured regimes

**Files:**

- Create: `yf/src/yieldforge/temporal_benchmark/generator.py`
- Create: `yf/tests/temporal_benchmark/test_generator.py`

**Red:** Add one deterministic generation test per regime, byte-identical repeatability tests,
different-seed identity tests, threshold rederivation tests, no-signal incompatibility, exact-task
recurrence, family recurrence without exact-task recurrence, three-event compatible bundles,
high-mix uniqueness, measured regime shift, and generation failure when a source pool cannot meet a
construction.

**Green:** Implement deterministic catalog selection, event construction, diagnostics, threshold
checks, baseline-safe as-of views, and analysis-only oracle views. Never expose labels or future
events through the baseline view.

**Verify:**

```bash
uv run --all-groups pytest tests/temporal_benchmark/test_generator.py -q
```

**Commit:** `feat: generate measured M6 temporal regimes`

## Task 4: Implement replay-ready source lowering

**Files:**

- Create: `yf/src/yieldforge/temporal_benchmark/lowering.py`
- Create: `yf/tests/temporal_benchmark/test_lowering.py`

**Red:** Test exact source task and projection identities, complete part counts, preserved
orientations and flip projections, unique namespaced part IDs, all-and-only compatible batching,
deterministic incompatible sub-batch ordering, and fail-closed catalog/stream mismatch.

**Green:** Resolve every event through `project_task(..., source_as_recorded)`, namespace parts,
combine only matching timestamp/material/stock work, and emit a strict lowering report with count
reconciliation.

**Verify:**

```bash
uv run --all-groups pytest tests/temporal_benchmark/test_lowering.py -q
```

**Commit:** `feat: lower M6 streams into replay batches`

## Task 5: Generate, archive, and validate the population

**Files:**

- Create: `yf/src/yieldforge/temporal_benchmark/population.py`
- Create: `yf/tests/temporal_benchmark/test_population.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/test_cli.py`
- Create: `yf/benchmarks/temporal/m6-contract-v1.json`
- Create: `yf/benchmarks/temporal/m6-population-v1.json`
- Create: `yf/benchmarks/temporal/streams/*.json`

**Red:** Test safe immutable publication, idempotence, collision/tamper rejection, exactly 48
streams, exact partition coverage, regeneration identity, validation CLI success, and visible
invalid/missing/unexpected cell failures.

**Green:** Implement population generation/publication/loading/validation and `yieldforge benchmark
m6-generate` / `m6-validate` commands. Generate the registered artifacts only after all contract
tests pass.

**Verify:**

```bash
uv run --all-groups pytest tests/temporal_benchmark/test_population.py tests/test_cli.py -q
uv run yieldforge benchmark m6-validate \
  --contract benchmarks/temporal/m6-contract-v1.json \
  --population benchmarks/temporal/m6-population-v1.json \
  --stream-root benchmarks/temporal/streams
```

**Commit:** `feat: publish M6 temporal benchmark population`

## Task 6: Run the stratified lowering and geometry pilot

**Files:**

- Create: `yf/src/yieldforge/temporal_benchmark/pilot.py`
- Create: `yf/tests/temporal_benchmark/test_pilot.py`
- Create: `yf/experiments/results/m6-lowering-pilot-*.json`

**Red:** Test one evaluation stream per regime, deterministic sample selection, exact event/task/
part/batch reconciliation, Shapely geometry validity accounting, runtime fields, failure visibility,
and the collision-backend decision rule.

**Green:** Profile catalog loading, source projection, exact geometry validation, and compatible
batch construction. Record that validation-only geometry time cannot trigger a fit/search backend
replacement; preserve the 30% / 15-minute Jagua gate for M7's actual repeated-search pilot.

**Verify:**

```bash
uv run --all-groups pytest tests/temporal_benchmark/test_pilot.py -q
```

**Commit:** `experiment: profile M6 lowering feasibility`

## Task 7: Close M6 and prepare M7

**Files:**

- Modify: `Docs/Milestones/M6 - Temporal benchmark data.md`
- Modify: `Docs/Milestones/M7 - Strong baseline.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`
- Modify: `Docs/Current Work.md`
- Create: `Docs/plans/2026-08-23-m7-strong-baseline-preparation.md`

Record canonical contract, population, stream, lowering, and pilot identities; regime diagnostics;
partition counts; exclusions; verification results; non-goals; and the exact M7 candidate-archive
and policy work still absent.

**Final verification:**

```bash
uv run --all-groups pytest -q
uv run ruff check .
uv build
git diff --check
git status --short --branch
```

**Commit:** `docs: close M6 and prepare strong baseline`

