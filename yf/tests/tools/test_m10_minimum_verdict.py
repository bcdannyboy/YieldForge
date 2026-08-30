from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

from yieldforge.experiments.m10_verdict import (
    M10EvidenceSnapshot,
    build_minimum_investment_verdict,
)

YF_ROOT = Path(__file__).resolve().parents[2]


def _load_runner_module() -> ModuleType:
    module_name = "yieldforge_m10_runner_test_target"
    spec = importlib.util.spec_from_file_location(
        module_name,
        YF_ROOT / "tools/run_m10_minimum_verdict.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("M10 runner test target could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner_module()

EXPECTED_PARENT_SPECS = (
    (
        "m0_contract",
        "experiments/m0-contract-v1.json",
        "yieldforge.m0-contract.v1",
        "contract_id",
        "yfm0-29b7efe8ac2a0a9995c4f907",
        "sha256:29b7efe8ac2a0a9995c4f907a56d7ce0cb9b61217b167f0737f6973c648b9a5f",
        "sha256:8ad20ca2ffaa4873588a4829d0d4fccfc85269429c6e2363b59d29be150d1c99",
    ),
    (
        "m6_contract",
        "benchmarks/temporal/m6-contract-v1.json",
        "yieldforge.temporal-benchmark-contract.v1",
        "contract_id",
        "yfm6-3eeda3f4feb80813807c501a",
        "sha256:3eeda3f4feb80813807c501ae71299a2add07ed76b75009e2f744daddae5a8aa",
        "sha256:461220a0a0860a7d31df48f50c3c5f8034a0c5fb5896ab40ae72bd74578bdd35",
    ),
    (
        "m6_population",
        "benchmarks/temporal/m6-population-v1.json",
        "yieldforge.temporal-population.v1",
        "population_id",
        "yftp-49bd7ce5fd34b2779440c52f",
        "sha256:49bd7ce5fd34b2779440c52fabdf2acb8ef80f39b025cdf5a9c6f8a1d2c958f9",
        "sha256:f864f10b6d3dced65d0ba6acd2c7201b3b01d06865fec748b028bbc4802a1e5b",
    ),
    (
        "m7_evaluation",
        "experiments/results/m7-evaluation-yfm7eval-f2cb310c4b7e879d119e8f94.json",
        "yieldforge.m7-evaluation-result.v1",
        "result_id",
        "yfm7eval-f2cb310c4b7e879d119e8f94",
        "sha256:f2cb310c4b7e879d119e8f940d5a3dc88cd4b26d48087b46323b7be848144931",
        "sha256:ba8fdbeaddb2ec9c289ead27627fca6a59f83012cd0093f557848b35c710b91f",
    ),
    (
        "m8_gate3",
        "experiments/results/m8-gate3-decision-yfm8g3decision-c13ec320e9fcd02873bf649c.json",
        "yieldforge.m8-gate3-decision.v2",
        "decision_id",
        "yfm8g3decision-c13ec320e9fcd02873bf649c",
        "sha256:c13ec320e9fcd02873bf649c4f8d84a66c48fb5c4a8e67ebf2fb2f5de268b03c",
        "sha256:8e1ca24321b5fd15445e06ebd680225ee42e10d895207cfbb496544d1613b551",
    ),
    (
        "m9_repair",
        "experiments/results/m9-two-ply-repair-validation-yfm9r-db0829451b1b0393f2d22559.json",
        "yieldforge.m9-two-ply-repair-validation.v1",
        "result_id",
        "yfm9r-db0829451b1b0393f2d22559",
        "sha256:db0829451b1b0393f2d2255990ade1ce783b27a8527f73f3c7bf07e6716438ba",
        "sha256:16444e2e0f1a6fa5fb57398b290a7b0b66fda271997a48202a403c499060b858",
    ),
)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def _copy_evidence(tmp_path: Path) -> Path:
    evidence_root = tmp_path / "evidence"
    for spec in runner.M10_PARENT_SPECS:
        source = YF_ROOT / spec.repository_path
        destination = evidence_root / spec.repository_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return evidence_root


def _artifact_payload(path: Path) -> dict[str, object]:
    return json.loads(path.read_bytes())


def test_parent_specs_are_the_exact_frozen_six_parent_census() -> None:
    observed = tuple(
        (
            spec.role,
            spec.repository_path,
            spec.schema_version,
            spec.semantic_id_field,
            spec.semantic_id,
            spec.content_sha256,
            spec.raw_file_sha256,
        )
        for spec in runner.M10_PARENT_SPECS
    )

    assert observed == EXPECTED_PARENT_SPECS


def test_importing_runner_does_not_import_other_m8_oracle_modules() -> None:
    runner_path = str(YF_ROOT / "tools/run_m10_minimum_verdict.py")
    script = f"""
import importlib.util
import json
import sys

name = "isolated_m10_runner"
spec = importlib.util.spec_from_file_location(
    name,
    {runner_path!r},
)
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)
print(json.dumps(sorted(
    loaded
    for loaded in sys.modules
    if loaded.startswith("yieldforge.oracle.")
    and loaded != "yieldforge.oracle.artifact_publisher"
)))
"""

    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=YF_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []


def test_runner_loads_twice_builds_twice_and_publishes_canonical_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evidence_root = _copy_evidence(tmp_path)
    output_directory = tmp_path / "results"
    real_load = runner._load_evidence_pass
    real_build = runner.build_minimum_investment_verdict
    loaded_ids: list[int] = []
    built_evidence_ids: list[int] = []

    def record_load(root: Path) -> runner._EvidencePass:
        loaded = real_load(root)
        loaded_ids.append(id(loaded.evidence))
        return loaded

    def record_build(evidence: M10EvidenceSnapshot):  # type: ignore[no-untyped-def]
        built_evidence_ids.append(id(evidence))
        return real_build(evidence)

    monkeypatch.setattr(runner, "_load_evidence_pass", record_load)
    monkeypatch.setattr(runner, "build_minimum_investment_verdict", record_build)

    outcome = runner.run_m10_minimum_verdict(
        evidence_root=evidence_root,
        output_directory=output_directory,
    )

    assert len(loaded_ids) == 2
    assert len(set(loaded_ids)) == 2
    assert built_evidence_ids == loaded_ids
    assert outcome.artifact_path.name == (
        f"m10-minimum-investment-verdict-{outcome.result_id}.json"
    )
    assert outcome.investment_verdict == "acquire_real_manufacturer_history"
    assert outcome.productization_decision == "do_not_productize"
    assert outcome.formal_economic_band == "not_computed"
    assert len(outcome.evidence_pass_wall_seconds) == 2
    assert all(value >= 0.0 for value in outcome.evidence_pass_wall_seconds)

    artifact_bytes = outcome.artifact_path.read_bytes()
    payload = _artifact_payload(outcome.artifact_path)
    assert artifact_bytes == _canonical(payload)
    assert payload["result_id"] == outcome.result_id
    assert payload["content_sha256"] == outcome.content_sha256
    assert outcome.result_id == f"yfm10-{outcome.content_sha256[7:31]}"
    assert "wall_seconds" not in artifact_bytes.decode()
    assert tuple(
        parent["role"] for parent in payload["evidence"]["parents"]  # type: ignore[index]
    ) == tuple(spec.role for spec in runner.M10_PARENT_SPECS)


def test_loaded_evidence_extracts_only_the_frozen_current_state(tmp_path: Path) -> None:
    loaded = runner._load_evidence_pass(_copy_evidence(tmp_path))
    evidence = loaded.evidence

    assert evidence.geometry_corpus_ids == ("lectra-7030786-v1.1",)
    assert evidence.required_positive_geometry_corpus_count == 2
    assert evidence.chronology_provenance == "generated"
    assert evidence.economics_provenance == "generated"
    assert evidence.material_provenance == "assumed"
    assert evidence.baseline_stream_count == 36
    assert evidence.baseline_repeat_count == 2
    assert evidence.baseline_repeat_identity_match is True
    assert evidence.m8_decision == "hold_performance"
    assert evidence.oracle_evaluation_opened is False
    assert evidence.oracle_savings_percent is None
    assert evidence.unknown_future_contribution_percentage_points is None
    assert evidence.m9_decision == "pass_decision_feasibility"
    assert tuple(
        hashlib.sha256(value).hexdigest() for value in loaded.parent_raw_bytes
    ) == tuple(spec.raw_file_sha256[7:] for spec in runner.M10_PARENT_SPECS)


def test_semantic_parent_validation_rejects_schema_id_hash_and_recomputed_drift(
    tmp_path: Path,
) -> None:
    evidence_root = _copy_evidence(tmp_path)
    spec = runner.M10_PARENT_SPECS[0]
    raw = runner._read_regular_file_no_follow(
        evidence_root,
        spec.repository_path,
        max_bytes=runner.MAX_PARENT_BYTES,
    )
    payload = runner._parse_canonical_json(raw, label=spec.role)

    for mutation in (
        {"schema_version": "wrong"},
        {spec.semantic_id_field: "wrong"},
        {"content_sha256": f"sha256:{'0' * 64}"},
        {"status": "semantic-drift"},
    ):
        changed = {**payload, **mutation}
        with pytest.raises(runner.M10RunnerError):
            runner._validate_parent_semantics(spec, changed)


def test_parent_raw_byte_drift_fails_before_parsing_and_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evidence_root = _copy_evidence(tmp_path)
    output_directory = tmp_path / "results"
    first_parent = evidence_root / runner.M10_PARENT_SPECS[0].repository_path
    real_load = runner._load_evidence_pass
    load_count = 0

    def mutate_after_first_load(root: Path) -> runner._EvidencePass:
        nonlocal load_count
        loaded = real_load(root)
        load_count += 1
        if load_count == 1:
            first_parent.write_bytes(first_parent.read_bytes() + b" ")
        return loaded

    monkeypatch.setattr(runner, "_load_evidence_pass", mutate_after_first_load)

    with pytest.raises(runner.M10RunnerError, match="raw SHA-256"):
        runner.run_m10_minimum_verdict(
            evidence_root=evidence_root,
            output_directory=output_directory,
        )

    assert not output_directory.exists()


def test_reconciler_rejects_different_parent_bytes_without_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evidence_root = _copy_evidence(tmp_path)
    output_directory = tmp_path / "results"
    real_load = runner._load_evidence_pass
    count = 0

    def return_drift(root: Path) -> runner._EvidencePass:
        nonlocal count
        count += 1
        loaded = real_load(root)
        if count == 2:
            raw = list(loaded.parent_raw_bytes)
            raw[0] += b"drift"
            return replace(loaded, parent_raw_bytes=tuple(raw))
        return loaded

    monkeypatch.setattr(runner, "_load_evidence_pass", return_drift)

    with pytest.raises(runner.M10RunnerError, match="parent bytes differ"):
        runner.run_m10_minimum_verdict(
            evidence_root=evidence_root,
            output_directory=output_directory,
        )

    assert not output_directory.exists()


def test_reconciler_rejects_different_semantic_results_without_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    evidence_root = _copy_evidence(tmp_path)
    output_directory = tmp_path / "results"
    real_build = build_minimum_investment_verdict
    count = 0

    def return_semantic_drift(evidence: M10EvidenceSnapshot):  # type: ignore[no-untyped-def]
        nonlocal count
        count += 1
        if count == 2:
            payload = evidence.model_dump(mode="python")
            payload["parents"][0]["repository_path"] = "experiments/drift.json"
            evidence = M10EvidenceSnapshot.model_validate(payload, strict=True)
        return real_build(evidence)

    monkeypatch.setattr(runner, "build_minimum_investment_verdict", return_semantic_drift)

    with pytest.raises(runner.M10RunnerError, match="semantic results differ"):
        runner.run_m10_minimum_verdict(
            evidence_root=evidence_root,
            output_directory=output_directory,
        )

    assert not output_directory.exists()


def test_publication_is_idempotent_and_refuses_conflicting_bytes(tmp_path: Path) -> None:
    evidence_root = _copy_evidence(tmp_path)
    output_directory = tmp_path / "results"

    first = runner.run_m10_minimum_verdict(
        evidence_root=evidence_root,
        output_directory=output_directory,
    )
    original = first.artifact_path.read_bytes()
    identity = first.artifact_path.stat().st_dev, first.artifact_path.stat().st_ino
    second = runner.run_m10_minimum_verdict(
        evidence_root=evidence_root,
        output_directory=output_directory,
    )

    assert second.artifact_path == first.artifact_path
    assert (second.artifact_path.stat().st_dev, second.artifact_path.stat().st_ino) == identity
    assert second.artifact_path.read_bytes() == original

    conflict_directory = tmp_path / "conflict"
    conflict_directory.mkdir()
    conflict = conflict_directory / first.artifact_path.name
    conflict.write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="immutable artifact differs"):
        runner.run_m10_minimum_verdict(
            evidence_root=evidence_root,
            output_directory=conflict_directory,
        )
    assert conflict.read_bytes() == b"{}\n"


@pytest.mark.parametrize(
    "data",
    [
        b'{"value": 1, "value": 2}\n',
        b'{"value": NaN}\n',
        b'{"value": 1e999}\n',
        b'{"value": 1}\n',
        b'{\n  "z": 1,\n  "a": 2\n}\n',
        b"[]\n",
    ],
)
def test_strict_json_parser_rejects_duplicate_nonfinite_and_noncanonical_data(
    data: bytes,
) -> None:
    with pytest.raises(runner.M10RunnerError):
        runner._parse_canonical_json(data, label="adversarial fixture")


def test_strict_json_parser_accepts_only_canonical_finite_mapping() -> None:
    expected = {"nested": {"finite": 1.25}, "value": 1}

    assert runner._parse_canonical_json(
        _canonical(expected),
        label="canonical fixture",
    ) == expected


@pytest.mark.parametrize("entry_kind", ["symlink", "directory", "fifo"])
def test_no_follow_loader_rejects_nonregular_parent_entries(
    entry_kind: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    target = root / "target.json"
    target.write_bytes(_canonical({"value": 1}))
    entry = root / "parent.json"
    if entry_kind == "symlink":
        entry.symlink_to(target)
    elif entry_kind == "directory":
        entry.mkdir()
    else:
        os.mkfifo(entry)

    with pytest.raises(runner.M10RunnerError, match="regular file"):
        runner._read_regular_file_no_follow(
            root,
            "parent.json",
            max_bytes=runner.MAX_PARENT_BYTES,
        )


def test_no_follow_loader_rejects_symlinked_root_and_intermediate_directory(
    tmp_path: Path,
) -> None:
    real_root = tmp_path / "real"
    nested = real_root / "nested"
    nested.mkdir(parents=True)
    (nested / "parent.json").write_bytes(_canonical({"value": 1}))
    root_alias = tmp_path / "root-alias"
    root_alias.symlink_to(real_root, target_is_directory=True)
    nested_alias = real_root / "nested-alias"
    nested_alias.symlink_to(nested, target_is_directory=True)

    with pytest.raises(runner.M10RunnerError, match="directory"):
        runner._read_regular_file_no_follow(
            root_alias,
            "nested/parent.json",
            max_bytes=runner.MAX_PARENT_BYTES,
        )
    with pytest.raises(runner.M10RunnerError, match="directory"):
        runner._read_regular_file_no_follow(
            real_root,
            "nested-alias/parent.json",
            max_bytes=runner.MAX_PARENT_BYTES,
        )


@pytest.mark.parametrize("repository_path", ["../outside.json", "/absolute.json", "."])
def test_no_follow_loader_rejects_paths_outside_the_evidence_root(
    repository_path: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    root.mkdir()

    with pytest.raises(runner.M10RunnerError, match="repository path"):
        runner._read_regular_file_no_follow(
            root,
            repository_path,
            max_bytes=runner.MAX_PARENT_BYTES,
        )


def test_bounded_loader_rejects_oversized_parent(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "oversized.json").write_bytes(b"x" * (runner.MAX_PARENT_BYTES + 1))

    with pytest.raises(runner.M10RunnerError, match="size limit"):
        runner._read_regular_file_no_follow(
            root,
            "oversized.json",
            max_bytes=runner.MAX_PARENT_BYTES,
        )


def test_artifact_validator_rejects_noncanonical_and_extra_content(tmp_path: Path) -> None:
    outcome = runner.run_m10_minimum_verdict(
        evidence_root=_copy_evidence(tmp_path),
        output_directory=tmp_path / "results",
    )
    canonical = outcome.artifact_path.read_bytes()
    payload = _artifact_payload(outcome.artifact_path)

    assert runner._validate_artifact_bytes(canonical) == canonical
    with pytest.raises(runner.M10RunnerError):
        runner._validate_artifact_bytes(canonical.rstrip(b"\n"))
    payload["unexpected"] = True
    with pytest.raises(runner.M10RunnerError):
        runner._validate_artifact_bytes(_canonical(payload))


def test_cli_prints_one_json_only_summary(tmp_path: Path) -> None:
    evidence_root = _copy_evidence(tmp_path)
    output_directory = tmp_path / "results"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = f"{YF_ROOT / 'src'}:{YF_ROOT}"

    completed = subprocess.run(
        [
            sys.executable,
            str(YF_ROOT / "tools/run_m10_minimum_verdict.py"),
            "--evidence-root",
            str(evidence_root),
            "--output-directory",
            str(output_directory),
        ],
        cwd=YF_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    summary = json.loads(completed.stdout)
    assert set(summary) == {
        "artifact_path",
        "formal_economic_band",
        "investment_verdict",
        "productization_decision",
        "result_id",
    }
    assert summary["formal_economic_band"] == "not_computed"
    assert summary["investment_verdict"] == "acquire_real_manufacturer_history"
    assert summary["productization_decision"] == "do_not_productize"
    assert Path(summary["artifact_path"]).is_file()
