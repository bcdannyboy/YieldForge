from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from yieldforge.realistic_falsification.gate3_contracts import (
    build_gate3_confirmation_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def gate3_runner() -> ModuleType | None:
    try:
        return importlib.import_module("yieldforge.realistic_falsification.gate3_runner")
    except ModuleNotFoundError:
        return None


@pytest.fixture(scope="module")
def gate3_config() -> Any:
    return build_gate3_confirmation_config(REPO_ROOT)


@dataclass
class _FailingCalibrationBackend:
    def calibration_stream_ids(self, corpus_id: str) -> tuple[str, ...]:
        return tuple(f"{corpus_id}-calibration-{position}" for position in range(8))

    def confirmation_stream_ids(self, corpus_id: str) -> tuple[str, ...]:
        return tuple(f"{corpus_id}-confirmation-{position}" for position in range(20))

    def execute_calibration_stream(self, **_kwargs: object) -> object:
        raise RuntimeError("registered synthetic backend failure")

    def execute_validity_controls(self, **_kwargs: object) -> object:
        raise AssertionError("validity must be skipped after failed calibration")

    def execute_central_stream(self, **_kwargs: object) -> object:
        raise AssertionError("central confirmation must be skipped after failed calibration")


def _fake_authenticated_parents(config: Any) -> tuple[SimpleNamespace, SimpleNamespace]:
    roots = {item.role: item for item in config.roots}
    gate1_result = SimpleNamespace(
        result_id="yfm11g1r-" + "4" * 24,
        content_sha256="sha256:" + "4" * 64,
        opens_gate_2=True,
    )
    gate1 = SimpleNamespace(
        run_id="yfm11g1run-" + "3" * 24,
        content_sha256="sha256:" + "3" * 64,
        contract_id=roots["m11_contract"].semantic_id,
        contract_content_sha256=roots["m11_contract"].semantic_content_sha256,
        population_id=roots["m11_population"].semantic_id,
        population_content_sha256=roots["m11_population"].semantic_content_sha256,
        status="gate_1_survived",
        disposition="OPEN_GATE_2",
        gate1_result=gate1_result,
    )
    gate2_result = SimpleNamespace(
        result_id="yfm11g2r-" + "6" * 24,
        content_sha256="sha256:" + "6" * 64,
        gate1_result_id=gate1_result.result_id,
        gate1_result_content_sha256=gate1_result.content_sha256,
        contract_id=gate1.contract_id,
        contract_content_sha256=gate1.contract_content_sha256,
        population_id=gate1.population_id,
        population_content_sha256=gate1.population_content_sha256,
        status="gate_2_survived",
    )
    gate2 = SimpleNamespace(
        run_id="yfm11g2run-" + "5" * 24,
        content_sha256="sha256:" + "5" * 64,
        gate1_run_id=gate1.run_id,
        gate1_run_content_sha256=gate1.content_sha256,
        gate1_result_id=gate1_result.result_id,
        gate1_result_content_sha256=gate1_result.content_sha256,
        status="gate_2_survived",
        disposition="OPEN_GATE_3",
        gate2_result=gate2_result,
    )
    return gate1, gate2


def _install_parent_loaders(
    monkeypatch: pytest.MonkeyPatch,
    gate3_runner: ModuleType,
    gate3_config: Any,
) -> tuple[SimpleNamespace, SimpleNamespace, list[tuple[str, Path]]]:
    gate1, gate2 = _fake_authenticated_parents(gate3_config)
    calls: list[tuple[str, Path]] = []

    def load_gate1(path: Path, *, repository_root: Path) -> SimpleNamespace:
        assert repository_root == REPO_ROOT
        calls.append(("gate1", Path(path)))
        return gate1

    def load_gate2(
        path: Path,
        *,
        repository_root: Path,
        gate1_artifact_path: Path,
    ) -> SimpleNamespace:
        assert repository_root == REPO_ROOT
        assert Path(gate1_artifact_path).name == "gate1.json"
        calls.append(("gate2", Path(path)))
        return gate2

    def load_config(path: Path, *, repository_root: Path) -> Any:
        assert repository_root == REPO_ROOT
        calls.append(("config", Path(path)))
        return gate3_config

    monkeypatch.setattr(gate3_runner, "load_official_gate1_run", load_gate1)
    monkeypatch.setattr(gate3_runner, "load_official_gate2_run", load_gate2)
    monkeypatch.setattr(gate3_runner, "load_gate3_confirmation_config", load_config)
    return gate1, gate2, calls


def test_adapter_runtime_config_hash_is_deterministic_and_domain_separated(
    gate3_runner: ModuleType | None,
    gate3_config: Any,
) -> None:
    assert gate3_runner is not None, "Gate 3 runner module is missing"
    first = gate3_runner.gate3_adapter_runtime_config_sha256(gate3_config)
    second = gate3_runner.gate3_adapter_runtime_config_sha256(gate3_config)

    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == 71
    assert first != gate3_config.content_sha256
    assert first == "sha256:dc71929bc462040f310c6e4570c75175ca0213645f53b1265015b907a08079d7"


def test_official_gate3_runner_authenticates_injects_publishes_and_reads_back(
    monkeypatch: pytest.MonkeyPatch,
    gate3_runner: ModuleType,
    gate3_config: Any,
    tmp_path: Path,
) -> None:
    gate1, gate2, parent_calls = _install_parent_loaders(
        monkeypatch,
        gate3_runner,
        gate3_config,
    )
    factory_calls: list[dict[str, object]] = []

    def backend_factory(**kwargs: object) -> _FailingCalibrationBackend:
        factory_calls.append(kwargs)
        return _FailingCalibrationBackend()

    artifact, path = gate3_runner.run_and_publish_official_gate3_early(
        repository_root=REPO_ROOT,
        gate1_artifact_path=tmp_path / "gate1.json",
        gate2_artifact_path=tmp_path / "gate2.json",
        gate3_config_path=tmp_path / "gate3-config.json",
        output_directory=tmp_path,
        backend_factory=backend_factory,
    )

    assert path.name == f"m11-gate3-early-{artifact.run_id}.json"
    assert path.read_bytes() == gate3_runner.canonical_gate3_early_run_bytes(artifact)
    assert artifact.gate1_run_id == gate1.run_id
    assert artifact.gate1_evaluation_result_id == gate1.gate1_result.result_id
    assert artifact.gate2_run_id == gate2.run_id
    assert artifact.gate2_evaluation_result_id == gate2.gate2_result.result_id
    assert artifact.gate3_config_id == gate3_config.config_id
    assert artifact.result_id == artifact.result.result_id
    assert artifact.result_content_sha256 == artifact.result.content_sha256
    assert artifact.result.roots == artifact.roots
    assert artifact.result.status == "invalid_test"
    assert artifact.result.disposition == "INVALID_NONZERO"
    assert artifact.result.terminal is True
    assert artifact.productization_authorized is False
    assert "timing" not in artifact.model_dump(mode="json")
    assert factory_calls == [
        {
            "repository_root": REPO_ROOT,
            "gate1_artifact": gate1,
            "gate2_artifact": gate2,
            "gate3_config": gate3_config,
            "roots": artifact.roots,
        }
    ]
    assert tuple(name for name, _path in parent_calls) == (
        "gate1",
        "gate2",
        "config",
        "gate1",
        "gate2",
        "config",
    )
    assert (
        gate3_runner.load_official_gate3_early_run(
            path,
            repository_root=REPO_ROOT,
            gate1_artifact_path=tmp_path / "gate1.json",
            gate2_artifact_path=tmp_path / "gate2.json",
            gate3_config_path=tmp_path / "gate3-config.json",
        )
        == artifact
    )


def test_gate3_readback_rejects_nested_result_tampering(
    monkeypatch: pytest.MonkeyPatch,
    gate3_runner: ModuleType,
    gate3_config: Any,
    tmp_path: Path,
) -> None:
    _install_parent_loaders(monkeypatch, gate3_runner, gate3_config)
    artifact, path = gate3_runner.run_and_publish_official_gate3_early(
        repository_root=REPO_ROOT,
        gate1_artifact_path=tmp_path / "gate1.json",
        gate2_artifact_path=tmp_path / "gate2.json",
        gate3_config_path=tmp_path / "gate3-config.json",
        output_directory=tmp_path,
        backend_factory=lambda **_kwargs: _FailingCalibrationBackend(),
    )
    tampered = tmp_path / "tampered.json"
    tampered.write_bytes(
        path.read_bytes().replace(
            artifact.result_id.encode("ascii"),
            ("yfm11g3early-" + "0" * 24).encode("ascii"),
            1,
        )
    )

    with pytest.raises(gate3_runner.M11Gate3RunnerError, match="canonical evidence"):
        gate3_runner.load_official_gate3_early_run(
            tampered,
            repository_root=REPO_ROOT,
            gate1_artifact_path=tmp_path / "gate1.json",
            gate2_artifact_path=tmp_path / "gate2.json",
            gate3_config_path=tmp_path / "gate3-config.json",
        )


def test_gate3_runner_rejects_parent_that_differs_from_frozen_config(
    monkeypatch: pytest.MonkeyPatch,
    gate3_runner: ModuleType,
    gate3_config: Any,
    tmp_path: Path,
) -> None:
    gate1, _gate2, _calls = _install_parent_loaders(monkeypatch, gate3_runner, gate3_config)
    gate1.contract_id = "yfm11c-" + "0" * 24

    with pytest.raises(gate3_runner.M11Gate3RunnerError, match="parents|config"):
        gate3_runner.run_and_publish_official_gate3_early(
            repository_root=REPO_ROOT,
            gate1_artifact_path=tmp_path / "gate1.json",
            gate2_artifact_path=tmp_path / "gate2.json",
            gate3_config_path=tmp_path / "gate3-config.json",
            output_directory=tmp_path,
            backend_factory=lambda **_kwargs: _FailingCalibrationBackend(),
        )


def test_gate3_runner_wraps_backend_factory_failure(
    monkeypatch: pytest.MonkeyPatch,
    gate3_runner: ModuleType,
    gate3_config: Any,
    tmp_path: Path,
) -> None:
    _install_parent_loaders(monkeypatch, gate3_runner, gate3_config)

    def failed_factory(**_kwargs: object) -> _FailingCalibrationBackend:
        raise RuntimeError("backend construction failed")

    with pytest.raises(gate3_runner.M11Gate3RunnerError, match="execution failed"):
        gate3_runner.run_and_publish_official_gate3_early(
            repository_root=REPO_ROOT,
            gate1_artifact_path=tmp_path / "gate1.json",
            gate2_artifact_path=tmp_path / "gate2.json",
            gate3_config_path=tmp_path / "gate3-config.json",
            output_directory=tmp_path,
            backend_factory=failed_factory,
        )
