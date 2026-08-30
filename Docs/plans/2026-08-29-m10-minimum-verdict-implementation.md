# M10 Minimum Investment Verdict Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publish and independently verify the minimum honest M10 investment verdict from the
existing immutable YieldForge evidence chain without fabricating a formal numeric economic band.

**Architecture:** Add a small strict M10 decision model that distinguishes the roadmap investment
verdict from the uncomputed M0 economic band. A checkout-local runner strictly loads and binds the
frozen M0, M6, M7, M8, and M9 artifacts twice, derives the decision, and publishes one
content-addressed artifact through the shared immutable publisher. A separate stdlib-only verifier
recomputes parent raw hashes, result identity, decision predicates, and exact artifact bytes.

**Tech Stack:** Python 3.12, Pydantic v2, canonical JSON/SHA-256, shared immutable artifact
publisher, pytest, Ruff, stdlib independent verifier.

---

### Task 1: Add the strict evidence-ceiling decision contract

**Files:**
- Create: `yf/src/yieldforge/experiments/m10_verdict.py`
- Create: `yf/tests/experiments/test_m10_verdict.py`

**Step 1: Write the failing decision tests**

Create tests importing the missing API:

```python
from yieldforge.experiments.m10_verdict import (
    M10EvidenceSnapshot,
    M10ParentBinding,
    build_minimum_investment_verdict,
)
```

Build one complete current-state snapshot and require:

```python
assert result.formal_economic_band == "not_computed"
assert result.formal_numeric_m10_complete is False
assert result.investment_verdict == "acquire_real_manufacturer_history"
assert result.productization_decision == "do_not_productize"
assert result.additional_virtual_oracle_investment == "stop"
assert result.roadmap_decision_complete is True
assert result.green_eligible is False
```

Also require exact ordered parent bindings, exactly one geometry corpus against a required minimum
of two, generated chronology/economics, assumed material, complete/reproduced M7 baseline, M8
`hold_performance` with no oracle evaluation, and M9 `pass_decision_feasibility`.

Add one focused rejection test per decision-defining drift:

- formal oracle metrics supplied to this v1 runner;
- corpus count not below the green requirement;
- M7 evaluation incomplete or not reproducible;
- M8 decision no longer `hold_performance` or evaluation marked opened;
- M9 decision not passed;
- missing or duplicate parent role;
- noncanonical parent ordering; and
- a result ID/content hash not matching semantic content.

**Step 2: Run the tests to verify RED**

Run:

```bash
cd yf
uv run pytest -q tests/experiments/test_m10_verdict.py
```

Expected: collection fails because `yieldforge.experiments.m10_verdict` does not exist.

**Step 3: Implement the minimum model and decision builder**

Use strict frozen Pydantic models with `extra="forbid"` and finite values only. Implement:

```python
class M10ParentBinding(BaselineContractModel):
    role: Literal["m0_contract", "m6_contract", "m6_population", "m7_evaluation", "m8_gate3", "m9_repair"]
    repository_path: StrictStr
    schema_version: StrictStr
    semantic_id: StrictStr
    content_sha256: StrictStr
    raw_file_sha256: StrictStr


class M10EvidenceSnapshot(BaselineContractModel):
    parents: tuple[M10ParentBinding, ...]
    geometry_corpus_ids: tuple[StrictStr, ...]
    required_positive_geometry_corpus_count: Literal[2]
    chronology_provenance: Literal["generated"]
    economics_provenance: Literal["generated"]
    material_provenance: Literal["assumed"]
    baseline_stream_count: Literal[36]
    baseline_repeat_count: Literal[2]
    baseline_repeat_identity_match: Literal[True]
    m8_decision: Literal["hold_performance"]
    oracle_evaluation_opened: Literal[False]
    oracle_savings_percent: None = None
    unknown_future_contribution_percentage_points: None = None
    m9_decision: Literal["pass_decision_feasibility"]
```

Add a strict result model with content-derived `yfm10-` ID. Build semantic content first, hash
canonical compact JSON, then instantiate the result. The builder must derive every decision field;
callers may not pass a verdict.

The claim ceiling must state that this is an investment decision only and is not a formal M0
economic band, savings result, physical result, buyer result, or commercial proof.

**Step 4: Run focused tests and Ruff to verify GREEN**

Run:

```bash
cd yf
uv run pytest -q tests/experiments/test_m10_verdict.py
uv run ruff check src/yieldforge/experiments/m10_verdict.py \
  tests/experiments/test_m10_verdict.py
```

Expected: all focused tests and Ruff pass.

**Step 5: Commit Task 1**

```bash
git add yf/src/yieldforge/experiments/m10_verdict.py \
  yf/tests/experiments/test_m10_verdict.py
git commit -m "feat: derive minimum M10 investment verdict"
```

### Task 2: Add the strict two-pass runner and immutable publication

**Files:**
- Create: `yf/tools/run_m10_minimum_verdict.py`
- Create: `yf/tests/tools/test_m10_minimum_verdict.py`

**Step 1: Write failing evidence-loader and runner tests**

Add tests for a missing `run_m10_minimum_verdict` API. Using a temporary evidence root copied from
the canonical fixtures, require:

- strict JSON loading that rejects duplicate keys, NaN/infinity, non-regular files, symlinks,
  directories, and oversized inputs;
- exact expected filename, schema, semantic ID, content SHA, decision fields, and raw-file SHA for
  all six parents;
- M6 provenance extraction and one-corpus census;
- M7 36-stream/two-repeat identity extraction;
- M8 `hold_performance`, unopened oracle evaluation, and absent oracle metrics;
- M9 nested `pass_decision_feasibility` extraction;
- two fresh evidence loads and two builder calls;
- byte-identical semantic results before publication;
- operational wall times excluded from artifact bytes;
- content-derived `yfm10-` identity and canonical pretty JSON with terminal newline;
- immutable identical-file reuse through `publish_immutable_artifact`;
- refusal to replace different bytes; and
- no publication when pass-one/pass-two evidence differs.

The CLI test must require JSON-only stdout containing artifact path, result ID, final investment
verdict, productization decision, and explicit `formal_economic_band: not_computed`.

**Step 2: Run runner tests to verify RED**

Run:

```bash
cd yf
uv run pytest -q tests/tools/test_m10_minimum_verdict.py
```

Expected: collection fails because `tools.run_m10_minimum_verdict` does not exist.

**Step 3: Implement strict parent loading and two-pass reconciliation**

Keep orchestration in the tool and decision semantics in the experiment module. Define immutable
parent specifications for:

```text
experiments/m0-contract-v1.json
benchmarks/temporal/m6-contract-v1.json
benchmarks/temporal/m6-population-v1.json
experiments/results/m7-evaluation-yfm7eval-f2cb310c4b7e879d119e8f94.json
experiments/results/m8-gate3-decision-yfm8g3decision-c13ec320e9fcd02873bf649c.json
experiments/results/m9-two-ply-repair-validation-yfm9r-db0829451b1b0393f2d22559.json
```

For each parent, verify exact raw SHA-256 before parsing and then reconcile semantic keys. Extract
only the evidence fields named in Task 1. Do not import or execute M8 proof machinery.

Run the complete load/build path twice. Compare canonical compact semantic bytes and parent raw
bytes before constructing publication bytes. Use the shared
`yieldforge.oracle.artifact_publisher.publish_immutable_artifact` with a validator that strict-loads
and returns the same canonical pretty bytes.

**Step 4: Run focused tests and shared publisher regressions**

Run:

```bash
cd yf
uv run pytest -q tests/tools/test_m10_minimum_verdict.py \
  tests/experiments/test_m10_verdict.py \
  tests/oracle/test_artifact_publisher.py
uv run ruff check src/yieldforge/experiments/m10_verdict.py \
  tools/run_m10_minimum_verdict.py \
  tests/experiments/test_m10_verdict.py \
  tests/tools/test_m10_minimum_verdict.py
```

Expected: all tests and Ruff pass.

**Step 5: Commit Task 2**

```bash
git add yf/tools/run_m10_minimum_verdict.py \
  yf/tests/tools/test_m10_minimum_verdict.py
git commit -m "feat: publish minimum M10 verdict"
```

### Task 3: Execute, independently verify, and commit the verdict artifact

**Files:**
- Create: `yf/tools/verify_m10_minimum_verdict.py`
- Create: `yf/tests/experiments/test_m10_committed_artifact.py`
- Create: `yf/experiments/results/m10-minimum-investment-verdict-<result-id>.json`

**Step 1: Write the failing independent-verifier test**

The test must invoke the verifier as an isolated subprocess and must not import the M10 runner or
decision module. Require rejection for:

- modified parent bytes;
- modified artifact bytes;
- duplicate JSON keys or non-finite values;
- wrong parent ordering, paths, IDs, or hashes;
- a supplied formal economic metric;
- a changed decision predicate or output;
- wrong semantic SHA/result ID; and
- noncanonical pretty bytes.

Before the artifact exists, the committed-artifact test should fail with the expected missing-file
message.

**Step 2: Run the verifier test to verify RED**

Run:

```bash
cd yf
uv run pytest -q tests/experiments/test_m10_committed_artifact.py
```

Expected: failure because the verifier and committed artifact do not exist.

**Step 3: Implement the stdlib-only verifier**

Use only `argparse`, `hashlib`, `json`, `math`, `os`, `stat`, and `pathlib`. Independently:

1. strict-load every bound parent and artifact;
2. recompute each raw parent hash and reconcile extracted evidence;
3. reject duplicate keys, non-finite numbers, extra/missing decision fields, and non-regular paths;
4. recompute the exact decision predicates;
5. recompute canonical compact semantic bytes, `content_sha256`, and `yfm10-` ID;
6. regenerate canonical pretty JSON plus terminal newline; and
7. print JSON-only verification output.

The verifier must contain no import from `yieldforge` or `tools.run_m10_minimum_verdict`.

**Step 4: Execute the canonical runner and verifier**

Run:

```bash
cd yf
PYTHONPATH=src:. uv run python tools/run_m10_minimum_verdict.py \
  --evidence-root . \
  --output-directory experiments/results
```

Expected: one immutable `m10-minimum-investment-verdict-yfm10-<24 hex>.json` with
`acquire_real_manufacturer_history`, `do_not_productize`, and `not_computed` formal band.

Then run:

```bash
uv run python -I tools/verify_m10_minimum_verdict.py \
  experiments/results/m10-minimum-investment-verdict-yfm10-<24 hex>.json \
  --evidence-root .
```

Expected: JSON-only success with the same result ID and verdict.

**Step 5: Run the complete focused verification and commit**

Run:

```bash
cd yf
uv run pytest -q tests/experiments/test_m10_verdict.py \
  tests/tools/test_m10_minimum_verdict.py \
  tests/experiments/test_m10_committed_artifact.py \
  tests/oracle/test_artifact_publisher.py
uv run ruff check src/yieldforge/experiments/m10_verdict.py \
  tools/run_m10_minimum_verdict.py \
  tools/verify_m10_minimum_verdict.py \
  tests/experiments/test_m10_verdict.py \
  tests/tools/test_m10_minimum_verdict.py \
  tests/experiments/test_m10_committed_artifact.py
git diff --check
```

Stage exact files and commit:

```bash
git add yf/tools/verify_m10_minimum_verdict.py \
  yf/tests/experiments/test_m10_committed_artifact.py \
  yf/experiments/results/m10-minimum-investment-verdict-yfm10-*.json
git commit -m "data: publish final M10 investment verdict"
```

### Task 4: Record the final roadmap decision and complete independent review

**Files:**
- Modify: `Docs/Milestones/M10 - Experiment and verdict.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`
- Modify: `Docs/Current Work.md`

**Step 1: Obtain independent artifact and code review**

Require reviewers to confirm:

- exact parent raw and semantic bindings;
- no M8 proof/evaluation execution or economic-band fabrication;
- deterministic derivation of the middle investment verdict;
- explicit distinction between roadmap completion and formal numeric M10 incompleteness;
- productization closed and optional data-acquisition reopen conditions;
- independent verifier does not import implementation code; and
- no changes to the original M0/M6-M9 artifacts.

Fix every critical or important finding and rerun focused verification before documentation.

**Step 2: Update milestone and current-state notes**

Record the artifact ID/hash, exact decision, bound evidence, and claim ceiling. Mark M10:

```text
Passed — final minimum investment verdict issued: acquire real manufacturer history;
do not productize; formal numeric economic band not computed.
```

State prominently that this is not a formal red/yellow/green M0 result. Preserve the original M8
performance hold and M9 bounded claim. Record that no more virtual oracle work is authorized by the
current roadmap decision.

**Step 3: Run fresh final verification**

Run:

```bash
cd yf
uv run pytest -q tests/experiments/test_m10_verdict.py \
  tests/tools/test_m10_minimum_verdict.py \
  tests/experiments/test_m10_committed_artifact.py \
  tests/oracle/test_artifact_publisher.py \
  tests/oracle/test_search_validation.py \
  tests/baseline/test_experiment.py
uv run ruff check src/yieldforge/experiments/m10_verdict.py \
  tools/run_m10_minimum_verdict.py \
  tools/verify_m10_minimum_verdict.py \
  tests/experiments/test_m10_verdict.py \
  tests/tools/test_m10_minimum_verdict.py \
  tests/experiments/test_m10_committed_artifact.py
uv run python -I tools/verify_m10_minimum_verdict.py \
  experiments/results/m10-minimum-investment-verdict-yfm10-<24 hex>.json \
  --evidence-root .
git diff --check
git status --short
```

Expected: all tests and Ruff pass, the independent verifier returns success, diff check is clean,
and only the three documentation files remain changed.

**Step 4: Commit documentation**

```bash
git add 'Docs/Milestones/M10 - Experiment and verdict.md' \
  'Docs/Milestones/Milestone Roadmap.md' \
  'Docs/Current Work.md'
git commit -m "docs: record final YieldForge verdict"
```

**Step 5: Final audit**

Confirm the intended commit sequence, no staged or unstaged changes, original parent raw hashes
unchanged, committed artifact independently verifies, and the final verdict is reported without
upgrading its claim ceiling.
