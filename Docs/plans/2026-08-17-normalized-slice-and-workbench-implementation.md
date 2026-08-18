# Normalized Slice and Research Workbench Implementation Plan

> **For Claude:** REQUIRED SUB-SKILLS: Use `superpowers:subagent-driven-development`,
> `superpowers:test-driven-development`, and `superpowers:verification-before-completion` to
> implement this plan task by task.

**Goal:** Turn the qualified Lectra corpus into one source-faithful visible slice, a truthful
progressive Spyrrow workbench, and deterministic hybrid order-book experiments.

**Architecture:** The locked qualifier emits a bounded passive JSON slice whose source rows remain
lossless and checksum-bound. A separate projection converts only explicitly eligible tasks into the
existing solver contract and records every assumption. FastAPI owns bounded corpus queries and
killable subprocess solver jobs; a React/Vite/TypeScript client renders exact SVG geometry and the
provenance/assumption boundary. Order books reuse normalized source tasks and add only versioned,
seeded, provenance-labelled chronology and economic fields.

**Tech stack:** Python 3.12, Pydantic 2, Shapely 2, Spyrrow 0.9, FastAPI, Uvicorn, pytest,
React, Vite, TypeScript, Vitest, Testing Library, Playwright, native SVG, Server-Sent Events.

**Execution:** The user explicitly chose the existing `main` checkout rather than a worktree and
requested subagent-driven execution. Keep commits small and never commit raw pickle files, passive
audit output, solver runtime state, or generated full-size corpora.

---

## Non-negotiable evidence boundary

- The observed source column is `part_id`, not the public prose's `parts_id`.
- Preserve `raw`, `sizes`, every task field, and every opaque constraint field exactly.
- Keep the literal unit label `m^-4` with `interpretation: null`; never display mm or inches.
- Pairing adjacent `raw` scalars and closing a polygon ring are derived, reversible operations.
- `s1` rows equal part rows globally, but their semantics are not established.
- No task is currently certified `directly_supported`.
- A runnable task is `source_lossless` plus `runnable_with_explicit_assumptions`.
- `sheet_length == -1` is never silently replaced.
- A failed strict selector falls back to the existing static smoke fixture; it does not weaken rules.

## Task 1: Passive normalized-slice contract

**Files:**

- Create: `yf/src/yieldforge/datasets/normalized_slice.py`
- Modify: `yf/src/yieldforge/datasets/passive_report.py`
- Create: `yf/tests/datasets/test_normalized_slice.py`
- Modify: `yf/tests/datasets/test_passive_report.py`

1. Write failing tests for strict, frozen models with schema version
   `yieldforge.normalized-slice.v1`.
2. Model source identity, task rows, part rows, shape rows, typed opaque constraint values, derived
   geometry facts, provenance families, support classification, solver projection status, and
   sorted reason/assumption codes.
3. Require the pinned dataset ID, source checksums, source-manifest SHA-256, audit-report SHA-256,
   DOI, license, source row indexes, and a conversion-ruleset version.
4. Add a strict passive loader using the existing single-descriptor, no-link, duplicate-key,
   finite-number, mutation, and size-bound policy.
5. Test exact `raw`/`sizes` round-trip, missing/list/scalar constraint distinctions, source order,
   hash binding, malformed input rejection, and pandas/pickle-free imports.
6. Run focused tests and Ruff; commit `feat: define normalized Lectra slice`.

## Task 2: Isolated candidate selection and slice export

**Files:**

- Create: `yf/src/yieldforge/datasets/lectra_slice.py`
- Modify: `yf/tools/lectra/qualify.py`
- Modify: `yf/tools/lectra/run_qualifier.py`
- Modify: `yf/tools/lectra/Dockerfile`
- Modify: `yf/.dockerignore`
- Modify: `yf/tools/lectra/README.md`
- Create: `yf/tools/lectra/export_slice.py`
- Modify: `yf/tools/lectra/make_trusted_fixture.py`
- Create: `yf/tests/datasets/test_lectra_slice.py`
- Modify: `yf/tests/tools/test_lectra_qualifier_boundary.py`

1. Write failing tests for a deterministic selector whose strict runnable rule is: training split,
   positive physical `sheet_length`, 20–50 part rows, finite flat-even single-sequence geometry,
   exactly one well-formed `s1` reference per part, no non-`s1` constraints, and valid, simple,
   nonzero polygons without repair. Rank by distance to the observed medians (35 parts, 9 unique
   shapes, 23 repeated rows), then `tasks_index`.
2. Select one separate non-`s1` task as a view-only exclusion example.
3. Extend the one trusted pickle entrypoint with an explicit `audit` or `slice` mode. Keep sealed
   memfd staging, exact source verification, fail-fast table schemas, no network, and no writable
   host mount.
4. Emit only bounded JSON stdout. Extend the trusted runner to validate, canonicalize, source-bind,
   and atomically publish exactly `lectra-slice.json` for slice mode.
5. Preserve all selected source records and opaque values; add only declared derived geometry facts.
6. Test row-order-independent selection, no eligible-task failure, adversarial pickle containment,
   exact publication, and that `read_pickle` still appears only in `qualify.py`.
7. Build the pinned image, run trusted-fixture Docker integration, and run the full suite.
8. Commit `feat: export a bounded Lectra slice`.

## Task 3: Produce and commit the real representative slice

**Files:**

- Create: `yf/datasets/fixtures/lectra-representative-slice.json`
- Create: `Docs/Research/Lectra Representative Slice.md`
- Modify: `Docs/Current Work.md`

1. Build the reviewed qualifier image from exact pinned digests.
2. Run the trusted slice exporter against the four verified ignored source files.
3. Validate the passive artifact independently, confirm its source and audit hashes, and require a
   bounded committed size.
4. Inspect and document selected task IDs, source fields, constraint inventory, polygon checks,
   explicit assumptions, and exclusion reasons. Do not infer unit scale.
5. Commit only the canonical slice and documentation as `data: add representative Lectra slice`.

## Task 4: Fail-closed solver projection

**Files:**

- Create: `yf/src/yieldforge/datasets/projection.py`
- Create: `yf/tests/datasets/test_projection.py`
- Modify: `yf/benchmarks/static/m0-smoke.json` only if a schema migration is necessary

1. Write failing tests for adjacent-scalar pairing, reversible ring closure, and projection refusal
   for sentinel length, non-`s1`, malformed, multi-sequence, unlabelled orientation, or unlabelled
   ignored-constraint assumptions.
2. Create one solver `Part` per source `(tasks_index, part_id)`, `demand=1`, so placement identity is
   never lost through shape grouping.
3. Map `strip_height=sheet_width` and `sheet_length=sheet_length` without scaling.
4. Permit free rotation only through an explicit `assume_free_rotation` policy and record it beside
   `ignore_opaque_s1`. Never mutate source geometry.
5. Golden-test a 90-degree rotation around `(0,0)`, followed by translation, then SVG y-flip
   `render_y = sheet_width - y`.
6. Run focused/native integration tests and commit `feat: project explicit Lectra assumptions`.

## Task 5: Progressive Spyrrow adapter result

**Files:**

- Modify: `yf/src/yieldforge/spyrrow_adapter.py`
- Modify: `yf/src/yieldforge/domain.py`
- Modify: `yf/tests/test_spyrrow_adapter.py`
- Modify: `yf/tests/test_spyrrow_integration.py`

1. Write failing tests for a `run(..., on_candidate=...)` API while preserving
   `generate(...) -> CandidateBatch` byte-for-byte.
2. Normalize, physical-sheet-filter, and content-dedupe each progress report as it is drained; invoke
   the callback only for accepted unique candidates in batch order.
3. Return batch, `final_candidate_id`, native/ignored report counts, duplicate count, and sheet
   overflow count. Track final identity separately when a native Final duplicates an earlier layout.
4. Do not add a misleading cancel callback or progress percentage.
5. Run unit and native integration tests; commit `feat: stream normalized Spyrrow progress`.

## Task 6: Killable solver worker and durable job service

**Files:**

- Create: `yf/src/yieldforge/workbench/contracts.py`
- Create: `yf/src/yieldforge/workbench/solver_worker.py`
- Create: `yf/src/yieldforge/workbench/jobs.py`
- Create: `yf/tests/workbench/test_solver_worker.py`
- Create: `yf/tests/workbench/test_jobs.py`
- Modify: `yf/.gitignore` only if new runtime paths need ignoring

1. Define strict job and event contracts with monotonic sequence IDs and statuses `queued`,
   `running`, `cancelling`, `cancelled`, `timed_out`, `failed`, and `completed`.
2. Supervise one `asyncio.create_subprocess_exec` worker per solve with newline-delimited JSON stdout,
   separate bounded stderr, no shell, explicit seed, one worker, and API budget <=10 seconds.
3. Coalesce candidate notifications to 5–10 Hz while retaining the complete validated terminal batch.
4. On cancel: terminate, wait a bounded grace period, kill if needed, prove process exit, and never
   create a complete candidate archive. Apply the same rule to hard timeout.
5. Persist immutable request JSON, append-only sampled events, and terminal metadata in ignored
   server-assigned job directories. Mark completion only after `CandidateArchive.create` succeeds.
6. On startup, mark unterminated prior jobs failed with `supervisor_restart`; do not act on stale PIDs.
7. Test fake worker complete/fail/hang/ignore-term modes, cancel/complete race, timeout, no orphan,
   bounded output, archive truth, and restart recovery.
8. Commit `feat: supervise killable solver jobs`.

## Task 7: Workbench API and corpus query service

**Files:**

- Modify: `yf/pyproject.toml`
- Create: `yf/src/yieldforge/datasets/corpus.py`
- Create: `yf/src/yieldforge/workbench/app.py`
- Create: `yf/src/yieldforge/workbench/__init__.py`
- Create: `yf/tests/workbench/test_corpus.py`
- Create: `yf/tests/workbench/test_api.py`

1. Add locked FastAPI/Uvicorn development dependencies without adding pandas to the normal runtime.
2. Implement `GET /api/corpus/summary`, bounded cursor `GET /api/tasks`, and
   `GET /api/tasks/{tasks_index}` over the committed slice.
3. Implement `POST /api/solver-jobs`, job status, idempotent cancellation, candidate paging,
   candidate geometry, and SSE events with `Last-Event-ID` replay.
4. Reject unsupported/over-limit solve requests with 422 before spawning a process. Compute every
   runtime/archive path server-side.
5. Test bounded filters, cursor stability, assumption propagation, SSE ordering/replay, subscriber
   disconnect, slow subscribers, terminal stream closure, cancellation, and no pandas/pickle import.
6. Commit `feat: expose the local research workbench API`.

## Task 8: Corpus Explorer and Nest Lab

**Files:**

- Create: `yf/web/package.json`, `yf/web/package-lock.json`, `yf/web/tsconfig.json`,
  `yf/web/vite.config.ts`, and `yf/web/index.html`
- Create: `yf/web/src/` application, API, corpus, nest, and SVG geometry modules
- Create: `yf/web/src/**/*.test.tsx`
- Create: `yf/web/e2e/workbench.spec.ts`

1. Establish a small design system for a serious local research instrument: source-real, derived,
   generated, and assumed provenance must be visually distinct without relying on color alone.
2. Build a two-view shell: Corpus Explorer and Nest Lab. Show task facts, source rows, geometry,
   support status, exclusion reasons, and a persistent assumptions warning.
3. Disable solve for blocked tasks. Require explicit acknowledgement before solving an
   assumption-backed task.
4. Render source polygons and placed candidates in native SVG. Use explicit point transformation,
   not ambiguous nested SVG transforms; render sheet X as length and Y as width.
5. Stream job events, show phase/elapsed/candidate counts without invented percentage, support
   cancel, and browse the completed immutable candidate batch.
6. Test accessibility, provenance labels, blocked/assumed states, exact golden geometry, streaming,
   cancel, error/timeout, responsive layout, production build, and a real local Playwright flow.
7. Commit `feat: add Corpus Explorer and Nest Lab`.

## Task 9: Deterministic hybrid order-book contracts and generator

**Files:**

- Create: `yf/src/yieldforge/order_books/domain.py`
- Create: `yf/src/yieldforge/order_books/generator.py`
- Create: `yf/src/yieldforge/order_books/archive.py`
- Create: `yf/tests/order_books/test_domain.py`
- Create: `yf/tests/order_books/test_generator.py`
- Create: `yf/datasets/fixtures/order-books/*.json`

1. Model immutable manifests, source-task references, timestamped events, field-family provenance,
   diagnostics, generator identity/version, seed, and content hash.
2. Implement tiny deterministic `no_signal`, `exact_recurrence`, and `high_mix` regimes first.
   Chronology/material/economic fields are generated or assumed; geometry and task composition remain
   observed. Never treat `tasks_index` as time.
3. Measure realized recurrence, uniqueness, concentration, load, and task-size distributions; reject
   a book that misses its declared construction bounds.
4. Keep future events and generator-only labels out of baseline-facing projections.
5. Test same manifest+seed identity, different-seed divergence, invariants, leakage boundary,
   canonical no-clobber archive, and hand-inspectable fixtures.
6. Commit `feat: generate provenance-aware order books`.

## Task 10: Order Book Lab

**Files:**

- Modify: `yf/src/yieldforge/workbench/app.py`
- Create: `yf/src/yieldforge/workbench/order_books.py`
- Create: `yf/tests/workbench/test_order_book_api.py`
- Create: `yf/web/src/order-books/` modules and tests
- Modify: `yf/web/e2e/workbench.spec.ts`

1. Add bounded generate/open/detail APIs returning immutable manifests and diagnostics.
2. Build a chronological timeline with source task links, recurrence/load diagnostics, and visible
   observed/derived/generated/assumed labels.
3. Let a user jump from an order event to its corpus task and available candidate nests.
4. Test deterministic UI results, provenance, filters, bad-regime rejection, and navigation.
5. Commit `feat: add Order Book Lab`.

## Task 11: Documentation and completion gate

**Files:**

- Modify: `README.md`
- Modify: `Docs/Home.md`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Development/Getting Started.md`
- Create: `Docs/Development/Research Workbench.md`
- Modify: relevant milestone notes only where current status changed

1. Document exact fetch, qualify, slice export, API, web, solver-job, order-book, and verification
   commands; explain ignored evidence paths and immutable committed fixtures.
2. Record current capability and claim ceilings without marking later residual/replay/oracle work done.
3. Run the full Python suite, Ruff check/format check, Docker boundary and real slice smoke, frontend
   unit tests, production build, Playwright smoke, `git diff --check`, and a secret/large-file scan.
4. Inspect the final diff and confirm raw corpus/reports/runtime state remain ignored.
5. Commit `docs: explain the YieldForge research workbench`, push `main`, and report exact evidence.

## Review checkpoints

After every implementation task, use a fresh subagent for specification review, fix any gaps, then
use a fresh subagent for code-quality review before proceeding. A successful command is not enough:
reviewers must verify evidence boundaries, no hidden semantic reinterpretation, no unsafe pickle
path, no fake cancellation/progress, and no provenance leakage.
