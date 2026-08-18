# Lectra Corpus Qualification Implementation Plan

**Execution status:** Qualification complete 2026-08-17; see [[Lectra Corpus Audit]].

> [!note] Implemented boundary and evidence
> The final implementation is stricter than the original command sketches below. The trusted runner gives the container no host-writable output mount, validates a single bounded stdout report, and publishes it with no-clobber semantics. The production ceiling is 16 GiB, not the originally proposed 8 GiB; the canonical run peaked at 9,721,896,960 bytes. The observed parts key is `part_id`, while the public schema text names `parts_id`. The aggregate audit proves corpus-wide counts and distributions but cannot prove representative task IDs or task-level direct-support coverage; those require a separate bounded passive export.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Acquire the pinned Lectra/Lallier 100,000-task release safely and produce a reproducible capability census that determines the first Corpus Explorer and Nest Lab slice.

**Architecture:** YieldForge will commit a strict source manifest and a tested streaming fetcher, but not the roughly 188 MB raw corpus. Gzip-compressed pickle files will be loaded only inside a locked, network-disabled Docker container with read-only source access; the container emits passive JSON audit results. The plan stops at a review gate before normalized schemas or frontend behavior are implemented because those decisions depend on the observed contours, constraints, units, and join structure.

**Tech Stack:** Python 3.12, uv, Pydantic 2, pandas in an isolated data toolchain, Docker, pytest, Ruff, JSON.

---

## Scope boundary

This plan implements only the qualification stage approved in [[2026-08-17-dataset-workbench-design]]. It does not:

- normalize the full dataset;
- extend the canonical geometry model;
- run untrusted pickle data in the normal YieldForge environment;
- build the API or frontend;
- generate order books;
- infer chronology from `tasks_index` or source row order.

After the capability census is reviewed, write the next plan for the representative normalized slice plus Corpus Explorer and Nest Lab.

### Task 1: Add the pinned source manifest

**Files:**

- Create: `yf/datasets/sources/lectra-7030786-v1.1.json`
- Create: `yf/src/yieldforge/datasets/__init__.py`
- Create: `yf/src/yieldforge/datasets/source_manifest.py`
- Test: `yf/tests/datasets/test_source_manifest.py`

**Step 1: Write the failing manifest test**

```python
from pathlib import Path

from yieldforge.datasets.source_manifest import DatasetSourceManifest


def test_lectra_manifest_matches_published_release() -> None:
    path = Path(__file__).parents[2] / "datasets" / "sources" / "lectra-7030786-v1.1.json"
    manifest = DatasetSourceManifest.model_validate_json(path.read_text())

    assert manifest.dataset_id == "lectra-7030786-v1.1"
    assert manifest.doi == "10.5281/zenodo.7030786"
    assert manifest.license == "CC-BY-4.0"
    assert sum(file.size_bytes for file in manifest.files) == 187_809_072
    assert {file.name for file in manifest.files} == {
        "tasks.gz",
        "parts.gz",
        "shapes.gz",
        "constraints.gz",
    }
```

**Step 2: Run the test to verify it fails**

Run from `yf/`:

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest tests/datasets/test_source_manifest.py -q
```

Expected: FAIL because `yieldforge.datasets.source_manifest` does not exist.

**Step 3: Implement the strict manifest model**

Create immutable Pydantic models for:

```python
class SourceFile(ContractModel):
    name: str
    url: str
    size_bytes: int = Field(gt=0)
    checksum_algorithm: Literal["md5"]
    checksum: str = Field(pattern=r"^[0-9a-f]{32}$")


class DatasetSourceManifest(ContractModel):
    schema_version: Literal["yieldforge.dataset-source.v1"]
    dataset_id: str
    title: str
    doi: str
    version: str
    license: str
    source_page: str
    files: list[SourceFile] = Field(min_length=1)
```

Populate the JSON manifest with the four file URLs, sizes, and MD5 values published by the Zenodo API record:

| File | Bytes | MD5 |
| --- | ---: | --- |
| `parts.gz` | 22,476,589 | `d8b51403f0cab79ec990b95a40911c1c` |
| `constraints.gz` | 16,299,551 | `e12581851bd2a357145a9dfccdad5363` |
| `shapes.gz` | 147,824,458 | `ff1623f24adf031710450a30e72984f2` |
| `tasks.gz` | 1,208,474 | `ac18fc58408a3fc832cfd6757b4b16ca` |

**Step 4: Run the test and lint**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest tests/datasets/test_source_manifest.py -q
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff check src/yieldforge/datasets tests/datasets
```

Expected: PASS.

**Step 5: Commit**

```bash
git add yf/datasets/sources/lectra-7030786-v1.1.json yf/src/yieldforge/datasets yf/tests/datasets
git commit -m "feat: pin Lectra dataset source"
```

### Task 2: Build a verified streaming fetcher

**Files:**

- Create: `yf/src/yieldforge/datasets/fetch.py`
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/.gitignore`
- Test: `yf/tests/datasets/test_fetch.py`
- Test: `yf/tests/test_cli.py`

**Step 1: Write failing unit tests**

Use a temporary local source file to prove that the fetcher:

- streams into a `.partial` file;
- verifies byte count and MD5;
- atomically promotes a verified file;
- accepts an already verified destination without downloading again;
- deletes the partial file and raises `DatasetIntegrityError` on mismatch;
- refuses an existing destination whose checksum differs.

Core test shape:

```python
def test_fetch_file_verifies_and_promotes(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"verified source")
    destination = tmp_path / "downloads" / "source.bin"
    spec = SourceFile(
        name="source.bin",
        url=source.as_uri(),
        size_bytes=15,
        checksum_algorithm="md5",
        checksum="4d64c45c7c72ad3d120259ca06a8f83d",
    )

    fetch_file(spec, destination)

    assert destination.read_bytes() == b"verified source"
    assert not destination.with_suffix(".bin.partial").exists()
```

Calculate the fixture checksum in the test instead of copying the illustrative value above.

**Step 2: Run the focused tests to verify they fail**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest tests/datasets/test_fetch.py -q
```

Expected: FAIL because the fetcher is absent.

**Step 3: Implement minimal verified fetching**

Implement `fetch_file(spec, destination, chunk_size=1024 * 1024)` with `urllib.request.urlopen`, incremental MD5 calculation, explicit content-length-independent byte counting, atomic rename, and no overwrite of an unverified destination.

Add:

```text
yieldforge datasets fetch \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --output var/data/raw/lectra-7030786-v1.1
```

The command must print one status per file and never interpret the downloaded bytes.

Add these ignore rules:

```gitignore
yf/var/data/raw/
yf/var/data/normalized/
yf/var/data/reports/
```

**Step 4: Run tests and lint**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run pytest tests/datasets/test_fetch.py tests/test_cli.py -q
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff check .
```

Expected: PASS.

**Step 5: Commit**

```bash
git add .gitignore yf/src/yieldforge/datasets/fetch.py yf/src/yieldforge/cli.py yf/tests
git commit -m "feat: fetch verified dataset sources"
```

### Task 3: Add the isolated qualification toolchain

**Files:**

- Modify: `yf/pyproject.toml`
- Modify: `yf/uv.lock`
- Create: `yf/tools/lectra/Dockerfile`
- Create: `yf/tools/lectra/qualify.py`
- Create: `yf/tools/lectra/README.md`
- Create: `yf/src/yieldforge/datasets/lectra_audit.py`
- Modify: `yf/src/yieldforge/cli.py`
- Test: `yf/tests/datasets/test_lectra_audit.py`
- Test: `yf/tests/test_cli.py`

**Step 1: Add and lock data-tool dependencies**

Run from `yf/`:

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv add --group data pandas
```

Expected: `pyproject.toml` declares a `data` dependency group and `uv.lock` pins exact versions.

Do not import pandas from the normal solver, archive, or CLI modules.

**Step 2: Write failing audit tests using trusted synthetic frames**

Build tiny DataFrames matching the four published tables. Test pure audit functions for:

- required-column presence;
- unique task keys;
- parts-to-task join integrity;
- shapes-to-parts join integrity;
- constraints-to-task and part join integrity;
- shape contour counts from `raw` plus `sizes`;
- per-constraint-type frequency;
- task-size, unique-shape, and repeated-shape summaries;
- bounded example identifiers for every failure category;
- JSON serialization with `allow_nan=False`.
- CLI validation of a passive audit report without loading source files.

Do not make unit tests load arbitrary pickle files. The frames constructed by the test are already trusted Python objects.

**Step 3: Run tests to verify they fail**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run --group data pytest tests/datasets/test_lectra_audit.py -q
```

Expected: FAIL because `lectra_audit` is absent.

**Step 4: Implement the pure audit module**

Return a strict `LectraAuditReport` containing:

```python
class LectraAuditReport(ContractModel):
    schema_version: Literal["yieldforge.lectra-audit.v1"]
    dataset_id: str
    source_checksums: dict[str, str]
    table_rows: dict[str, int]
    columns: dict[str, list[str]]
    join_failures: dict[str, int]
    task_size_summary: NumericSummary
    shape_recurrence_summary: NumericSummary
    contour_count_frequency: dict[str, int]
    constraint_type_frequency: dict[str, int]
    malformed_counts: dict[str, int]
    bounded_examples: dict[str, list[str]]
```

The audit reports observed facts only. It must not guess whether extra contours represent holes, infer units beyond the published source label, or treat row order as chronology.

Add `yieldforge datasets audit-check --report ...` as a thin Pydantic validation command. It must not import pandas or regenerate the report.

**Step 5: Implement the container entry point**

`qualify.py` is the only code allowed to call `pandas.read_pickle`. It reads exactly four expected files from `/input`, calls the pure audit module, and writes only `/output/lectra-audit.json`.

The container must:

- use a Python 3.12 base image pinned during implementation;
- install from the committed `uv.lock`;
- run as a non-root numeric user;
- accept read-only input and a separate writable output mount;
- have no network at runtime;
- contain no repository credentials or user home mount.

Document the exact build and run commands in `tools/lectra/README.md`.

**Step 6: Run unit checks**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run --group data pytest tests/datasets/test_lectra_audit.py tests/test_cli.py -q
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff format --check .
```

Expected: PASS.

**Step 7: Build and smoke-test the container with trusted fixtures**

Create the trusted fixture files in a temporary directory, then run the qualifier with:

```bash
docker build -f tools/lectra/Dockerfile -t yieldforge-lectra-qualifier .
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 128 --memory 2g --cpus 2 \
  --mount type=bind,src=/tmp/yieldforge-lectra-fixture/input,dst=/input,readonly \
  --mount type=bind,src=/tmp/yieldforge-lectra-fixture/output,dst=/output \
  yieldforge-lectra-qualifier
```

Expected: exit 0 and a valid passive JSON report in the output directory.

**Step 8: Commit**

```bash
git add yf/pyproject.toml yf/uv.lock yf/tools/lectra yf/src/yieldforge/datasets/lectra_audit.py yf/src/yieldforge/cli.py yf/tests/datasets/test_lectra_audit.py yf/tests/test_cli.py
git commit -m "feat: add isolated Lectra qualification"
```

### Task 4: Fetch and qualify the published corpus

**Files:**

- Generated and ignored: `yf/var/data/raw/lectra-7030786-v1.1/`
- Generated and ignored: `yf/var/data/reports/lectra-7030786-v1.1/lectra-audit.json`
- Create after review: `Docs/Research/Lectra Corpus Audit.md`

**Step 1: Verify free disk and Docker availability**

```bash
df -h /Users/danielbloom/Desktop/YieldForge
docker version
```

Expected: at least 2 GB free for downloads, images, and conversion work; Docker client and daemon available. If either condition fails, stop and report the blocker. Do not load the pickle files outside the container as a fallback.

**Step 2: Download and verify all source files**

Run from `yf/`:

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run yieldforge datasets fetch \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --output var/data/raw/lectra-7030786-v1.1
```

Expected: all four files report verified byte counts and MD5 checksums.

**Step 3: Run the locked qualifier**

```bash
mkdir -p var/data/reports/lectra-7030786-v1.1
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 128 --memory 8g --cpus 4 \
  --mount type=bind,src=/Users/danielbloom/Desktop/YieldForge/yf/var/data/raw/lectra-7030786-v1.1,dst=/input,readonly \
  --mount type=bind,src=/Users/danielbloom/Desktop/YieldForge/yf/var/data/reports/lectra-7030786-v1.1,dst=/output \
  yieldforge-lectra-qualifier
```

Expected: exit 0 and `lectra-audit.json`; the raw source directory remains unchanged.

**Step 4: Validate the passive report outside the container**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run yieldforge datasets audit-check \
  --report var/data/reports/lectra-7030786-v1.1/lectra-audit.json
```

Expected: schema validation passes, all source checksums match the manifest, and no non-finite numbers appear.

**Step 5: Write the human audit note**

Create `Docs/Research/Lectra Corpus Audit.md` from the verified JSON. Report:

- source version and checksums;
- table counts and join failures;
- observed columns and source-unit label;
- contour and constraint inventory;
- recurrence and task-size distributions;
- malformed or ambiguous records;
- estimated current Spyrrow coverage, with the exact rule used;
- representative task IDs only if a bounded passive artifact proves the selection; otherwise record why the aggregate audit cannot provide them;
- unresolved questions that must block normalization.

Do not label inferred contours as holes without source evidence. Do not describe task index order as chronological.

**Step 6: Run full verification**

```bash
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv sync --locked
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run --group data pytest
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/yieldforge-uv-cache uv run ruff format --check .
git diff --check
```

Expected: all tests and checks pass. `git status --short` must not list raw pickle files or generated reports.

**Step 7: Commit the capability census**

```bash
git add Docs/Research/Lectra\ Corpus\ Audit.md
git commit -m "docs: publish Lectra corpus capability census"
```

### Task 5: Stop at the evidence checkpoint

**Step 1: Review the audit before planning normalization**

Answer these questions from the observed report:

1. What does `sizes` mean for each contour pattern?
2. What coordinate unit does `m^-4` represent in practice?
3. Which constraint types map losslessly to Spyrrow?
4. What percentage of tasks are immediately solvable without semantic loss?
5. Which task IDs exercise the most common supported geometry?
6. How often do exact `shape_hash` values recur across tasks?
7. Are task bundles and shape hashes sufficient to construct the approved recurrence regimes?

**Step 2: Produce the next plan, not implementation guesses**

Use `@superpowers:brainstorming` only if the audit forces a material design change. Otherwise use `@superpowers:writing-plans` to create the representative-slice, Corpus Explorer, and Nest Lab implementation plan from the verified schema.

Do not begin normalization or frontend implementation in this plan.
