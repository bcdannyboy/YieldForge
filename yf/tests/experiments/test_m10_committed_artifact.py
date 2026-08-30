from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

YF_ROOT = Path(__file__).resolve().parents[2]
VERIFIER = YF_ROOT / "tools/verify_m10_minimum_verdict.py"
ARTIFACT_GLOB = "m10-minimum-investment-verdict-yfm10-*.json"
EXPECTED_RESULT_ID = "yfm10-931b3a95fe84cd96cff799f2"
EXPECTED_CONTENT_SHA256 = "sha256:931b3a95fe84cd96cff799f2fd697c6a31393442cdd0da9903d1aa6eaf27385d"
EXPECTED_RAW_FILE_SHA256 = "sha256:63f0bfccb6d74ff7d6f6dbdde324a1061e60319eac33da1895b3b52355ece847"
EXPECTED_ARTIFACT_NAME = "m10-minimum-investment-verdict-yfm10-931b3a95fe84cd96cff799f2.json"
PARENT_PATHS = (
    "experiments/m0-contract-v1.json",
    "benchmarks/temporal/m6-contract-v1.json",
    "benchmarks/temporal/m6-population-v1.json",
    "experiments/results/m7-evaluation-yfm7eval-f2cb310c4b7e879d119e8f94.json",
    "experiments/results/m8-gate3-decision-yfm8g3decision-c13ec320e9fcd02873bf649c.json",
    "experiments/results/m9-two-ply-repair-validation-yfm9r-db0829451b1b0393f2d22559.json",
)
ALLOWED_VERIFIER_IMPORTS = {
    "argparse",
    "hashlib",
    "json",
    "math",
    "os",
    "pathlib",
    "stat",
}
EXPECTED_ARTIFACT_KEYS = {
    "additional_virtual_oracle_investment",
    "claim_ceiling",
    "content_sha256",
    "decision_basis",
    "evidence",
    "formal_economic_band",
    "formal_numeric_m10_complete",
    "green_eligible",
    "investment_verdict",
    "missing_formal_measurements",
    "missing_required_controls",
    "productization_decision",
    "reopen_conditions",
    "result_id",
    "roadmap_decision_complete",
    "schema_version",
}


def _canonical_pretty(value: object) -> bytes:
    return (json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()


def _canonical_compact(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _committed_artifact() -> Path:
    matches = sorted((YF_ROOT / "experiments/results").glob(ARTIFACT_GLOB))
    assert len(matches) == 1, (
        f"expected exactly one committed M10 verdict artifact, found {len(matches)}"
    )
    return matches[0]


def _copy_case(tmp_path: Path) -> tuple[Path, Path]:
    evidence_root = tmp_path / "evidence"
    for repository_path in PARENT_PATHS:
        source = YF_ROOT / repository_path
        destination = evidence_root / repository_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    artifact = tmp_path / _committed_artifact().name
    shutil.copyfile(_committed_artifact(), artifact)
    return evidence_root, artifact


def _invoke_verifier(artifact: Path, evidence_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(VERIFIER),
            str(artifact),
            "--evidence-root",
            str(evidence_root),
        ],
        cwd=YF_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _assert_rejected(completed: subprocess.CompletedProcess[str]) -> None:
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "M10 verification failed:" in completed.stderr


def _identity_consistent_artifact(path: Path, payload: dict[str, object]) -> Path:
    semantic = dict(payload)
    semantic.pop("result_id", None)
    semantic.pop("content_sha256", None)
    digest = hashlib.sha256(_canonical_compact(semantic)).hexdigest()
    payload["content_sha256"] = f"sha256:{digest}"
    payload["result_id"] = f"yfm10-{digest[:24]}"
    destination = path.with_name(f"m10-minimum-investment-verdict-{payload['result_id']}.json")
    destination.write_bytes(_canonical_pretty(payload))
    if destination != path and path.exists():
        path.unlink()
    return destination


def test_exactly_one_committed_artifact_and_verifier_are_present() -> None:
    artifact = _committed_artifact()

    assert artifact.is_file()
    assert VERIFIER.is_file(), "expected independent M10 verifier is missing"


def test_verifier_source_uses_only_the_approved_stdlib_imports() -> None:
    source = VERIFIER.read_text()
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None
            roots.add(node.module.split(".", 1)[0])

    assert roots <= ALLOWED_VERIFIER_IMPORTS
    assert "from yieldforge" not in source
    assert "import yieldforge" not in source
    assert "run_m10_minimum_verdict" not in source
    assert "pydantic" not in source.lower()


def test_committed_artifact_has_exact_canonical_content_identity() -> None:
    artifact = _committed_artifact()
    raw = artifact.read_bytes()
    payload = json.loads(raw)
    semantic = dict(payload)
    semantic.pop("result_id")
    semantic.pop("content_sha256")
    digest = hashlib.sha256(_canonical_compact(semantic)).hexdigest()

    assert set(payload) == EXPECTED_ARTIFACT_KEYS
    assert raw == _canonical_pretty(payload)
    assert payload["content_sha256"] == f"sha256:{digest}"
    assert payload["result_id"] == f"yfm10-{digest[:24]}"
    assert artifact.name == (f"m10-minimum-investment-verdict-{payload['result_id']}.json")
    assert artifact.name == EXPECTED_ARTIFACT_NAME
    assert payload["result_id"] == EXPECTED_RESULT_ID
    assert payload["content_sha256"] == EXPECTED_CONTENT_SHA256
    assert f"sha256:{hashlib.sha256(raw).hexdigest()}" == EXPECTED_RAW_FILE_SHA256


def test_isolated_verifier_accepts_the_committed_artifact_with_json_only_output() -> None:
    artifact = _committed_artifact()
    payload = json.loads(artifact.read_bytes())

    completed = _invoke_verifier(artifact, YF_ROOT)

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    summary = json.loads(completed.stdout)
    assert summary == {
        "artifact_path": str(artifact),
        "formal_economic_band": "not_computed",
        "investment_verdict": "acquire_real_manufacturer_history",
        "productization_decision": "do_not_productize",
        "result_id": payload["result_id"],
        "verified": True,
    }


def test_verifier_rejects_modified_parent_bytes(tmp_path: Path) -> None:
    evidence_root, artifact = _copy_case(tmp_path)
    parent = evidence_root / PARENT_PATHS[0]
    parent.write_bytes(parent.read_bytes() + b" ")

    _assert_rejected(_invoke_verifier(artifact, evidence_root))


@pytest.mark.parametrize(
    "replacement",
    [
        lambda raw: raw + b" ",
        lambda raw: raw.rstrip(b"\n"),
        lambda raw: json.dumps(json.loads(raw), sort_keys=True).encode(),
        lambda _raw: b'{"value": 1, "value": 2}\n',
        lambda _raw: b'{"value": NaN}\n',
        lambda _raw: b'{"value": Infinity}\n',
    ],
    ids=[
        "trailing-bytes",
        "missing-terminal-newline",
        "noncanonical-json",
        "duplicate-key",
        "nan",
        "infinity",
    ],
)
def test_verifier_rejects_modified_or_non_strict_artifact_bytes(
    replacement: Callable[[bytes], bytes],
    tmp_path: Path,
) -> None:
    evidence_root, artifact = _copy_case(tmp_path)
    artifact.write_bytes(replacement(artifact.read_bytes()))

    _assert_rejected(_invoke_verifier(artifact, evidence_root))


@pytest.mark.parametrize("target", ["parent", "artifact"])
@pytest.mark.parametrize("entry_kind", ["symlink", "directory"])
def test_verifier_rejects_symlink_and_nonregular_inputs(
    target: str,
    entry_kind: str,
    tmp_path: Path,
) -> None:
    evidence_root, artifact = _copy_case(tmp_path)
    path = evidence_root / PARENT_PATHS[0] if target == "parent" else artifact
    original = tmp_path / f"original-{target}.json"
    shutil.copyfile(path, original)
    path.unlink()
    if entry_kind == "symlink":
        path.symlink_to(original)
    else:
        path.mkdir()

    _assert_rejected(_invoke_verifier(artifact, evidence_root))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["evidence"]["parents"].__setitem__(  # type: ignore[index,union-attr]
            slice(0, 2),
            list(reversed(payload["evidence"]["parents"][:2])),  # type: ignore[index]
        ),
        lambda payload: payload["evidence"]["parents"][0].__setitem__(  # type: ignore[index,union-attr]
            "repository_path",
            "experiments/other.json",
        ),
        lambda payload: payload["evidence"]["parents"][0].__setitem__(  # type: ignore[index,union-attr]
            "semantic_id",
            "yfm0-000000000000000000000000",
        ),
        lambda payload: payload["evidence"]["parents"][0].__setitem__(  # type: ignore[index,union-attr]
            "content_sha256",
            f"sha256:{'0' * 64}",
        ),
        lambda payload: payload["evidence"]["parents"][0].__setitem__(  # type: ignore[index,union-attr]
            "raw_file_sha256",
            f"sha256:{'0' * 64}",
        ),
    ],
    ids=["order", "path", "semantic-id", "content-sha", "raw-sha"],
)
def test_verifier_rejects_identity_consistent_parent_binding_drift(
    mutation: Callable[[dict[str, object]], object],
    tmp_path: Path,
) -> None:
    evidence_root, artifact = _copy_case(tmp_path)
    payload = json.loads(artifact.read_bytes())
    mutation(payload)
    artifact = _identity_consistent_artifact(artifact, payload)

    _assert_rejected(_invoke_verifier(artifact, evidence_root))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["evidence"].__setitem__(  # type: ignore[union-attr]
            "oracle_savings_percent",
            1.0,
        ),
        lambda payload: payload["evidence"].__setitem__(  # type: ignore[union-attr]
            "m8_decision",
            "proceed_evaluation",
        ),
        lambda payload: payload.__setitem__("investment_verdict", "productize"),
        lambda payload: payload["evidence"].__setitem__(  # type: ignore[union-attr]
            "geometry_corpus_ids",
            ["lectra-7030786-v1.1", "forged-second-corpus"],
        ),
        lambda payload: payload["evidence"].__setitem__(  # type: ignore[union-attr]
            "chronology_provenance",
            "source_observed",
        ),
        lambda payload: payload["evidence"].__setitem__(  # type: ignore[union-attr]
            "baseline_stream_count",
            True,
        ),
    ],
    ids=[
        "supplied-formal-metric",
        "m8-predicate",
        "decision-output",
        "corpus-census",
        "provenance",
        "strict-type",
    ],
)
def test_verifier_rejects_identity_consistent_predicate_or_output_drift(
    mutation: Callable[[dict[str, object]], object],
    tmp_path: Path,
) -> None:
    evidence_root, artifact = _copy_case(tmp_path)
    payload = json.loads(artifact.read_bytes())
    mutation(payload)
    artifact = _identity_consistent_artifact(artifact, payload)

    _assert_rejected(_invoke_verifier(artifact, evidence_root))


@pytest.mark.parametrize("field", ["claim_ceiling", "missing_required_controls"])
def test_verifier_rejects_missing_decision_fields(field: str, tmp_path: Path) -> None:
    evidence_root, artifact = _copy_case(tmp_path)
    payload = json.loads(artifact.read_bytes())
    payload.pop(field)
    artifact = _identity_consistent_artifact(artifact, payload)

    _assert_rejected(_invoke_verifier(artifact, evidence_root))


def test_verifier_rejects_extra_decision_fields(tmp_path: Path) -> None:
    evidence_root, artifact = _copy_case(tmp_path)
    payload = json.loads(artifact.read_bytes())
    payload["fabricated_measurement"] = 1.0
    artifact = _identity_consistent_artifact(artifact, payload)

    _assert_rejected(_invoke_verifier(artifact, evidence_root))


@pytest.mark.parametrize("field", ["content_sha256", "result_id"])
def test_verifier_rejects_wrong_content_identity(field: str, tmp_path: Path) -> None:
    evidence_root, artifact = _copy_case(tmp_path)
    payload = json.loads(artifact.read_bytes())
    payload[field] = f"sha256:{'0' * 64}" if field == "content_sha256" else (f"yfm10-{'0' * 24}")
    artifact.write_bytes(_canonical_pretty(payload))

    _assert_rejected(_invoke_verifier(artifact, evidence_root))


def test_verifier_rejects_correct_content_under_wrong_filename(tmp_path: Path) -> None:
    evidence_root, artifact = _copy_case(tmp_path)
    wrong_name = artifact.with_name("m10-minimum-investment-verdict-yfm10-" + "0" * 24 + ".json")
    artifact.rename(wrong_name)

    _assert_rejected(_invoke_verifier(wrong_name, evidence_root))


def test_verifier_rejects_artifact_parent_binding_to_modified_parent(
    tmp_path: Path,
) -> None:
    evidence_root, artifact = _copy_case(tmp_path)
    parent = evidence_root / PARENT_PATHS[0]
    changed = json.loads(parent.read_bytes())
    changed["status"] = "forged"
    semantic = dict(changed)
    semantic.pop("contract_id")
    semantic.pop("content_sha256")
    digest = hashlib.sha256(_canonical_compact(semantic)).hexdigest()
    changed["content_sha256"] = f"sha256:{digest}"
    changed["contract_id"] = f"yfm0-{digest[:24]}"
    parent.write_bytes(_canonical_pretty(changed))
    payload = json.loads(artifact.read_bytes())
    binding = payload["evidence"]["parents"][0]  # type: ignore[index]
    binding["semantic_id"] = changed["contract_id"]
    binding["content_sha256"] = changed["content_sha256"]
    binding["raw_file_sha256"] = (  # type: ignore[index]
        "sha256:" + hashlib.sha256(parent.read_bytes()).hexdigest()
    )
    artifact = _identity_consistent_artifact(artifact, payload)

    _assert_rejected(_invoke_verifier(artifact, evidence_root))


def test_verifier_rejects_parent_json_that_is_duplicate_nonfinite_or_noncanonical(
    tmp_path: Path,
) -> None:
    evidence_root, artifact = _copy_case(tmp_path)
    parent = evidence_root / PARENT_PATHS[0]

    for raw in (
        b'{"value": 1, "value": 2}\n',
        b'{"value": NaN}\n',
        json.dumps(json.loads(YF_ROOT.joinpath(PARENT_PATHS[0]).read_bytes())).encode(),
    ):
        parent.write_bytes(raw)
        _assert_rejected(_invoke_verifier(artifact, evidence_root))
