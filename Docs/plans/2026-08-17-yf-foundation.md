# YieldForge Foundation Implementation Plan

> **For Codex:** Use test-driven development for every behavioral component and verify the live Spyrrow integration before completion.

**Goal:** Establish one long-lived `yf/` application with a pinned Spyrrow dependency, a typed candidate-generation adapter, reproducible candidate archives, a runnable CLI, and concise developer documentation in `Docs/`.

**Architecture:** Python owns the experiment-facing domain contracts, orchestration, and immutable JSON archives. `SpyrrowAdapter` is an infrastructure boundary: it converts canonical strip-packing problems into Spyrrow objects, accepts only explicitly feasible progress reports, normalizes placements into solver-independent candidates, deduplicates them by content hash, and writes them through an archive service. The interface remains stable while residual geometry, remnant fit, replay, and policy modules are added in later milestones.

**Tech Stack:** Python 3.12, uv, Pydantic 2, Spyrrow 0.9.0 (Sparrow/Jagua native core), Shapely 2.1.2, pytest, Ruff.

---

### Task 1: Normalize the repository layout and bootstrap `yf/`

**Files:**
- Rename: `docs/` to `Docs/`
- Create: `yf/pyproject.toml`
- Create: `yf/.python-version`
- Create: `yf/src/yieldforge/__init__.py`
- Create: `yf/tests/__init__.py`
- Modify: `.gitignore`

**Steps:**
1. Rename the existing Obsidian vault without changing its contents.
2. Add a packaged Python 3.12 project with exact runtime dependency pins and bounded development dependencies.
3. Generate and commit `yf/uv.lock`.
4. Sync the environment and verify `spyrrow`, `shapely`, and `pydantic` import successfully.

### Task 2: Define the minimal candidate-generation contracts

**Files:**
- Create: `yf/tests/test_domain.py`
- Create: `yf/src/yieldforge/domain.py`
- Create: `yf/benchmarks/static/m0-smoke.json`

**Steps:**
1. Write failing tests for polygon closure, unique part IDs, positive demand, fixed-sheet length, and canonical JSON loading.
2. Run the tests and confirm failures are caused by missing domain code.
3. Implement only the models required by the adapter: part, strip-packing problem, solver configuration, placement, candidate, and candidate batch.
4. Run the domain tests and confirm they pass.

### Task 3: Build immutable candidate archives

**Files:**
- Create: `yf/tests/test_archive.py`
- Create: `yf/src/yieldforge/archive.py`
- Create: `yf/var/archives/.gitkeep`

**Steps:**
1. Write failing tests for archive creation, canonical `manifest.json`, JSONL candidate records, collision refusal, and deterministic content hashes.
2. Run the tests and confirm the archive behavior is missing.
3. Implement atomic archive creation with caller-supplied output directories and no silent overwrite.
4. Run the archive tests and confirm they pass.

### Task 4: Add the Spyrrow adapter

**Files:**
- Create: `yf/tests/test_spyrrow_adapter.py`
- Create: `yf/tests/test_spyrrow_integration.py`
- Create: `yf/src/yieldforge/spyrrow_adapter.py`

**Steps:**
1. Write failing adapter tests using small protocol-compatible solver objects.
2. Prove the tests fail because conversion, feasible-report filtering, normalization, and deduplication are absent.
3. Implement conversion to Spyrrow items/configuration, background progress draining, feasible-report filtering (`ExplFeas`, `CmprFeas`, `Final`), fixed-sheet rejection, normalization, and content-hash deduplication.
4. Run the unit tests and confirm they pass.
5. Add a live integration test using the installed Spyrrow wheel and a one-second toy solve.
6. Run the integration test and confirm at least one feasible archived candidate is produced.

### Task 5: Add the runnable candidate-generation command

**Files:**
- Create: `yf/tests/test_cli.py`
- Create: `yf/src/yieldforge/cli.py`
- Create: `yf/src/yieldforge/__main__.py`

**Steps:**
1. Write a failing CLI test for generating an archive from a benchmark manifest.
2. Implement `yieldforge candidates generate --input ... --output ...` with explicit solver settings and useful errors.
3. Run the CLI tests.
4. Run the command against `m0-smoke.json` and inspect the generated manifest and candidate records.

### Task 6: Document the durable foundation

**Files:**
- Create: `Docs/Development/Getting Started.md`
- Create: `Docs/Development/Spyrrow Adapter.md`
- Create: `Docs/Research/Technical Sources.md`
- Modify: `Docs/Home.md`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Milestones/M0 - Experiment contract.md`
- Modify: `README.md`

**Steps:**
1. Document setup, commands, module responsibilities, archive contents, dependency provenance, and known M0 limitations.
2. State clearly that generated M0 outputs are engineering evidence, not benchmark evidence.
3. Update repository links from `docs/` to `Docs/`.
4. Keep the notes concise and linked from the vault home.

### Task 7: Verify the repository

**Steps:**
1. Run the full pytest suite including the live Spyrrow integration test.
2. Run Ruff checks and formatting verification.
3. Run the CLI smoke test in a temporary archive directory.
4. Validate all local Markdown links after the `Docs/` rename.
5. Inspect `git diff --check`, `git status`, and the final tree.
6. Commit and push only after all checks pass.
