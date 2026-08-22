# M0 Experiment Contract Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a strict, content-addressed M0 constitution and a catalog-bound pure-geometry calibration protocol that fail closed on ambiguity or drift.

**Architecture:** Two committed JSON artifacts live under `yf/experiments/`. Immutable Pydantic models under `yieldforge.experiments` validate their internal rules, canonical encoding, semantic hashes, and cross-artifact references; a bundle validator also binds the geometry population and deterministic split to the committed Lectra catalog. A narrow CLI command exposes validation without adding a runner, simulator, UI, or confirmatory evaluation path.

**Tech Stack:** Python 3.12, Pydantic 2.12, hashlib/json, argparse, pytest, Ruff, repository-native Markdown and JSON.

**Execution constraint:** Work directly on the existing `main` checkout as explicitly requested. Preserve unrelated work. Do not run calibration or confirmatory evaluation.

---

### Task 1: Strict canonical artifact loading

**Files:**
- Create: `yf/src/yieldforge/experiments/__init__.py`
- Create: `yf/src/yieldforge/experiments/contracts.py`
- Create: `yf/tests/experiments/__init__.py`
- Create: `yf/tests/experiments/test_contracts.py`

**Step 1: Write failing loader tests**

Add tests proving that a tiny frozen model round-trips through the canonical pretty JSON encoding and that the loader rejects duplicate keys, unknown fields, noncanonical bytes, oversized files, nonfinite values, and the wrong schema version.

```python
def test_load_frozen_json_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"yieldforge.m0-contract.v1","schema_version":"x"}\n')
    with pytest.raises(ExperimentContractError, match="duplicate JSON key"):
        load_frozen_json(path, M0ExperimentContract)
```

**Step 2: Run the focused test and confirm failure**

Run from `yf/`:

```bash
uv run pytest tests/experiments/test_contracts.py -q
```

Expected: collection failure because `yieldforge.experiments.contracts` does not exist.

**Step 3: Implement the strict foundation**

Create:

```python
class FrozenExperimentModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )

class ExperimentContractError(ValueError):
    pass

def canonical_pretty_json_bytes(value: BaseModel) -> bytes:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
```

Implement a bounded regular-file reader with symlink rejection, duplicate-key rejection through `object_pairs_hook`, strict model validation, and byte equality against `canonical_pretty_json_bytes`.

**Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/experiments/test_contracts.py -q
uv run ruff check src/yieldforge/experiments tests/experiments
```

Expected: loader tests pass and Ruff reports no findings.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/experiments yf/tests/experiments
git commit -m "feat: add strict experiment contract loader"
```

### Task 2: Executable M0 economic constitution

**Files:**
- Modify: `yf/src/yieldforge/experiments/contracts.py`
- Modify: `yf/tests/experiments/test_contracts.py`
- Create: `yf/experiments/m0-contract-v1.json`

**Step 1: Write failing M0 invariant tests**

Build a valid contract fixture and parameterize mutations that must fail:

- cost equation omits an approved term;
- purchase timing is not `when_standard_sheet_opened`;
- excluded process costs differ from the frozen set;
- terminal primary is not scrap-only;
- information-ablation formula changes;
- future visibility leaks into a baseline;
- parity no longer shares archive hashes, seeds, configuration, compute, or stock eligibility;
- remnant primary/sensitivity values drift;
- failure retry count or allowed causes change;
- bootstrap seed/resample count/interval changes;
- threshold boundary ordering or supporting gate values change; and
- mutable-after-start or claim-ceiling fields change.

```python
@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("decision_gates", "green", "minimum_oracle_savings_percent"), 2.49),
        (("failure_handling", "maximum_identical_retries"), 2),
        (("terminal_inventory", "primary_treatment"), "bounded_continuation_credit"),
    ],
)
def test_m0_contract_rejects_frozen_rule_drift(path, replacement) -> None:
    payload = valid_m0_payload()
    set_nested(payload, path, replacement)
    with pytest.raises(ValidationError):
        M0ExperimentContract.model_validate(payload)
```

**Step 2: Confirm tests fail**

Run:

```bash
uv run pytest tests/experiments/test_contracts.py -q
```

Expected: failures because the nested M0 models and validators are absent.

**Step 3: Implement the minimal M0 model hierarchy**

Add strict nested models for:

- accounting formula, timing, included/excluded terms, and later rate-manifest gate;
- scrap-only terminal treatment and sensitivities;
- myopic, commercial, known-order, known-only-oracle, rollout, and beam information sets;
- event timing;
- candidate parity and expanded-search separation;
- primary/permissive/conservative remnant rules, compatibility, lineage, and holes;
- geometry and economic failure behavior;
- paired statistical reporting and controls;
- red/yellow/green thresholds and every approved supporting gate; and
- immutability, M0 status, and bounded claims.

The top-level model uses:

```python
class M0ExperimentContract(FrozenExperimentModel):
    schema_version: Literal["yieldforge.m0-contract.v1"]
    contract_id: str = Field(pattern=r"^yfm0-[0-9a-f]{24}$")
    content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["frozen_pending_geometry_calibration"]
    # nested approved sections
```

Compute the semantic hash from canonical compact JSON with `contract_id` and `content_sha256` excluded. Derive `contract_id` from the first 24 digest characters. Validate both after-model construction.

**Step 4: Add and validate the committed M0 JSON artifact**

Add every approved value from the design. Use canonical pretty JSON, calculate its semantic hash without changing any rule, and load the committed file in a test.

**Step 5: Run focused tests and lint**

Run:

```bash
uv run pytest tests/experiments/test_contracts.py -q
uv run ruff check src/yieldforge/experiments tests/experiments
uv run ruff format --check src/yieldforge/experiments tests/experiments
```

Expected: all focused tests pass; Ruff checks pass.

**Step 6: Commit**

```bash
git add yf/src/yieldforge/experiments yf/tests/experiments yf/experiments/m0-contract-v1.json
git commit -m "feat: freeze executable M0 constitution"
```

### Task 3: Catalog-bound pure-geometry calibration protocol

**Files:**
- Modify: `yf/src/yieldforge/experiments/contracts.py`
- Modify: `yf/tests/experiments/test_contracts.py`
- Create: `yf/experiments/pure-geometry-calibration-v1.json`

**Step 1: Write failing protocol and catalog-binding tests**

Test that the committed protocol:

- references the exact M0 semantic hash and catalog artifact hash;
- lists 254 eligible tasks and exactly blocked tasks `4365` and `25801`;
- derives the 51/203 split with no overlap or omission;
- prelists all flip-bearing sensitivity tasks and a deterministic 20-task repeatability subset;
- keeps confirmation disabled while seconds-per-seed is unset;
- freezes calibration seeds, time ladder, selection tolerances, solver settings, expanded seeds,
  timeout, and retry policy;
- freezes the envelope grid and 0.5% primary envelope;
- freezes candidate acceptance/identity and supporting strata; and
- freezes the primary outcome and green/redesign/stop thresholds.

Add deliberate rejection tests for one missing task, one moved task, a forged catalog hash,
`confirmation_enabled=true`, a non-source primary arm, pooling the no-flip sensitivity into
primary, a modified seed, adaptive retry, and reordered/altered envelope values.

**Step 2: Confirm protocol tests fail**

Run:

```bash
uv run pytest tests/experiments/test_contracts.py -q
```

Expected: failures because the geometry protocol and bundle validator are absent.

**Step 3: Implement deterministic selection helpers and protocol models**

Use a fully specified ranking function:

```python
def rank_task_ids(task_ids: Iterable[int], *, salt: str, catalog_sha256: str) -> tuple[int, ...]:
    return tuple(
        sorted(
            task_ids,
            key=lambda task_id: (
                hashlib.sha256(
                    f"{salt}:{catalog_sha256}:{task_id}".encode("utf-8")
                ).hexdigest(),
                task_id,
            ),
        )
    )
```

Use one fixed split salt and a separate repeatability salt. Add strict nested protocol models and
cross-field checks for counts, disjointness, sorted uniqueness where ordering is semantic,
calibration-pending state, budget relationships, primary-envelope membership, and decision-band
ordering.

**Step 4: Implement bundle validation against the committed catalog**

Load the catalog as `NormalizedSlice`, verify the file SHA-256 against both its adjacent manifest
and the protocol, derive eligible/blocked/flip-bearing IDs from task dispositions, rederive both
task selections, and compare all persisted lists exactly. Reject catalog ruleset or count drift.

```python
@dataclass(frozen=True)
class ValidatedExperimentBundle:
    m0: M0ExperimentContract
    geometry: PureGeometryCalibrationProtocol
    catalog_sha256: str
```

**Step 5: Add the committed geometry protocol**

Generate the exact task lists only from pre-solve catalog metadata, paste them into canonical JSON,
calculate the semantic identity, and keep `confirmation_enabled` false and
`selected_seconds_per_seed` null.

**Step 6: Run focused tests and lint**

Run:

```bash
uv run pytest tests/experiments/test_contracts.py -q
uv run ruff check src/yieldforge/experiments tests/experiments
uv run ruff format --check src/yieldforge/experiments tests/experiments
```

Expected: all protocol, invalid-contract, and catalog-binding tests pass.

**Step 7: Commit**

```bash
git add yf/src/yieldforge/experiments yf/tests/experiments yf/experiments/pure-geometry-calibration-v1.json
git commit -m "feat: preregister pure geometry calibration"
```

### Task 4: Executable CLI validation

**Files:**
- Modify: `yf/src/yieldforge/cli.py`
- Modify: `yf/tests/test_cli.py`

**Step 1: Write failing CLI tests**

Add a test invoking:

```python
result = main([
    "experiments", "validate",
    "--m0", str(m0_path),
    "--geometry", str(geometry_path),
    "--catalog", str(catalog_path),
    "--catalog-manifest", str(catalog_manifest_path),
])
assert result == 0
```

Assert the summary names both identities, `calibration=51`, `evaluation=203`, and
`confirmation=disabled`. Add one tampered-input test that fails closed.

**Step 2: Confirm CLI tests fail**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: parser error because `experiments validate` does not exist.

**Step 3: Implement the validation command**

Add an `experiments` command group with a `validate` subcommand and four required paths. The
handler calls `validate_experiment_bundle`, prints a bounded factual summary, and returns zero only
after every check passes. Do not add a generator or calibration runner.

**Step 4: Run CLI and contract tests**

Run:

```bash
uv run pytest tests/test_cli.py tests/experiments/test_contracts.py -q
uv run yieldforge experiments validate \
  --m0 experiments/m0-contract-v1.json \
  --geometry experiments/pure-geometry-calibration-v1.json \
  --catalog datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json \
  --catalog-manifest datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json
```

Expected: focused tests pass; command reports the frozen bundle with confirmation disabled.

**Step 5: Commit**

```bash
git add yf/src/yieldforge/cli.py yf/tests/test_cli.py
git commit -m "feat: validate frozen experiment bundle"
```

### Task 5: Make human and executable contracts agree

**Files:**
- Modify: `Docs/Milestones/M0 - Experiment contract.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Development/Getting Started.md`
- Modify: `README.md`

**Step 1: Add documentation assertions to tests where useful**

Add narrow assertions that the CLI help and committed paths remain stable. Avoid brittle tests of
prose.

**Step 2: Expand the M0 milestone note**

Document every frozen economic decision, formula, information set, event order, parity rule,
remnant threshold, failure rule, reporting statistic, decision gate, and claim ceiling. Link the
approved design and exact JSON artifact.

Keep M0 **Active**, explaining that the constitution is frozen but the geometry seconds-per-seed
selection is pending the registered calibration.

**Step 3: Update current-state and developer documentation**

Update README and Current Work to distinguish:

- frozen executable M0 rules;
- calibration-pending pure-geometry protocol;
- no confirmatory result;
- blocked tasks `4365` and `25801`;
- no residual/economic implementation.

Add the exact validation command and artifact map to Getting Started. Update the milestone roadmap
only to explain the M0-to-M2 calibration handoff; do not replan later milestones.

**Step 4: Check docs and diff**

Run:

```bash
git diff --check
git diff -- README.md Docs/
```

Expected: no whitespace errors; prose and artifact values agree.

**Step 5: Commit**

```bash
git add README.md Docs
git commit -m "docs: publish frozen M0 contract"
```

### Task 6: Full verification and final evidence review

**Files:**
- Review: all changed files since `9f2dbea`

**Step 1: Run the full applicable verification suite**

From `yf/`:

```bash
uv run --all-groups pytest
uv run ruff check .
uv run ruff format --check .
```

Frontend/browser contracts are unchanged, so frontend tests, typecheck, build, and Playwright are
not applicable. If any shared browser contract changes unexpectedly, run all of them before
continuing.

**Step 2: Re-run the executable contract validator**

Run:

```bash
uv run yieldforge experiments validate \
  --m0 experiments/m0-contract-v1.json \
  --geometry experiments/pure-geometry-calibration-v1.json \
  --catalog datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json \
  --catalog-manifest datasets/catalogs/lectra-7030786-v1.1/catalog-manifest.json
```

Expected: exact identities validate; 51 calibration, 203 evaluation; confirmation disabled.

**Step 3: Audit repository evidence**

Run from the repository root:

```bash
git diff --check 9f2dbea..HEAD
git status --short --branch
git log --oneline --decorate 9f2dbea..HEAD
git diff --stat 9f2dbea..HEAD
git diff 9f2dbea..HEAD
```

Confirm that no calibration archive, geometry evaluation, residual model, simulator, baseline,
oracle, savings result, UI, or unrelated file entered the diff.

**Step 4: Commit any verification-only correction**

If review finds a defect, add a regression test first, fix it, rerun all gates, and make a focused
commit. Otherwise make no empty commit.

**Step 5: Push only after all gates pass**

```bash
git push origin main
```

Expected: `origin/main` advances to the fully verified local `main` tip.

## Exact next M2 task after this plan

Run only the 51 prelisted calibration tasks for seeds `[0,1,2,3]` at 1, 3, and 10 seconds per
seed under `source_as_recorded`; persist every attempt and verified archive; apply the frozen
budget-selection rule; publish a new content-addressed geometry protocol with the selected seconds
and confirmation enabled only after validation. Calibration artifacts are excluded from the 203
task evaluation and cannot change the population, 0.5% primary envelope, seeds, metrics, or gates.
