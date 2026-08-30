from __future__ import annotations

import importlib
import json
from collections import Counter
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tests.realistic_falsification.test_economic_resolution import (
    _official_references,
    _official_roots,
    _repaired_receipt,
    _valid_checkpoints,
)
from yieldforge.realistic_falsification import economic_resolution as resolution
from yieldforge.realistic_falsification.economic_evidence_store import (
    Gate3EconomicEvidenceError,
)


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return importlib.import_module("tools.run_m11_economic_resolution")


def _scan():  # type: ignore[no-untyped-def]
    return resolution.build_official_legacy_calibration_scan(_official_references(resolution))


def _checkpoint_for_reference(reference, *, outcome: str):  # type: ignore[no-untyped-def]
    common = {
        "protocol": resolution.build_economic_resolution_protocol(),
        "roots": reference.roots,
        "execution_position": reference.execution_position,
        "corpus_id": reference.corpus_id,
        "stream_id": reference.stream_id,
        "policy_id": reference.policy_id,
    }
    if reference.status == "success":
        assert outcome == "legacy"
        return resolution.build_gate3_calibration_attempt_checkpoint(
            **common,
            legacy_reference=reference,
        )
    if outcome == "repaired":
        return resolution.build_gate3_calibration_attempt_checkpoint(
            **common,
            replaced_legacy_failure_reference=reference,
            repaired_receipt=_repaired_receipt(
                resolution,
                position=reference.execution_position,
                corpus_id=reference.corpus_id,
                stream_id=reference.stream_id,
                policy_id=reference.policy_id,
            ),
        )
    assert outcome == "failure"
    return resolution.build_gate3_calibration_attempt_checkpoint(
        **common,
        replaced_legacy_failure_reference=reference,
        failure_type="builtins.ValueError",
        failure_detail="bounded repaired failure",
    )


def _observation_from_receipt(receipt):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        roots=receipt.roots,
        corpus_id=receipt.corpus_id,
        stream_id=receipt.stream_id,
        policy_id=receipt.policy_id,
        observation_id=receipt.observation_id,
        content_sha256=receipt.observation_content_sha256,
        final_costs=receipt.final_costs,
        full_sheet_opening_count=receipt.full_sheet_opening_count,
        exact_event_census=receipt.exact_event_census,
        _receipt=receipt,
    )


class _FakeBackend:
    def __init__(
        self,
        *,
        scan: Any,
        log: list[tuple[str, int | None]],
        execution_errors: dict[int, BaseException],
        discard_errors: dict[int, BaseException],
        registry_mismatch: bool,
    ) -> None:
        self.scan = scan
        self.log = log
        self.execution_errors = execution_errors
        self.discard_errors = discard_errors
        self.registry_mismatch = registry_mismatch
        self.by_binding = {
            (item.corpus_id, item.stream_id, item.policy_id): item
            for item in scan.attempt_references
        }

    def calibration_stream_ids(self, corpus_id: str) -> tuple[str, ...]:
        self.log.append(("registry", None))
        expected = dict(self.scan.calibration_stream_census)[corpus_id]
        return (*expected[:-1], "wrong-stream") if self.registry_mismatch else expected

    def execute_calibration_stream(
        self,
        *,
        corpus_id: str,
        stream_id: str,
        policy_id: str,
    ) -> object:
        reference = self.by_binding[(corpus_id, stream_id, policy_id)]
        position = reference.execution_position
        self.log.append(("execute", position))
        if position in self.execution_errors:
            raise self.execution_errors[position]
        receipt = _repaired_receipt(
            resolution,
            position=position,
            corpus_id=corpus_id,
            stream_id=stream_id,
            policy_id=policy_id,
        )
        return _observation_from_receipt(receipt)

    def release_calibration_stream_evidence(
        self,
        *,
        corpus_id: str,
        stream_id: str,
        policy_id: str,
        expected_observation_id: str,
        expected_observation_content_sha256: str,
    ) -> None:
        reference = self.by_binding[(corpus_id, stream_id, policy_id)]
        receipt = _repaired_receipt(
            resolution,
            position=reference.execution_position,
            corpus_id=corpus_id,
            stream_id=stream_id,
            policy_id=policy_id,
        )
        assert (
            expected_observation_id,
            expected_observation_content_sha256,
        ) == (receipt.observation_id, receipt.observation_content_sha256)
        self.log.append(("release", reference.execution_position))

    def discard_incomplete_calibration_stream_evidence(
        self,
        *,
        corpus_id: str,
        stream_id: str,
        policy_id: str,
    ) -> None:
        reference = self.by_binding[(corpus_id, stream_id, policy_id)]
        position = reference.execution_position
        self.log.append(("discard", position))
        if position in self.discard_errors:
            raise self.discard_errors[position]


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
    *,
    resumed: dict[int, Any] | None = None,
    existing_manifest: Any | None = None,
    execution_errors: dict[int, BaseException] | None = None,
    discard_errors: dict[int, BaseException] | None = None,
    registry_mismatch: bool = False,
    bad_sidecar_position: int | None = None,
    sidecar_publication_error_position: int | None = None,
) -> tuple[Any, _FakeBackend, list[tuple[str, int | None]]]:
    scan = _scan()
    log: list[tuple[str, int | None]] = []
    resumed = {} if resumed is None else resumed
    execution_errors = {} if execution_errors is None else execution_errors
    discard_errors = {} if discard_errors is None else discard_errors
    backend = _FakeBackend(
        scan=scan,
        log=log,
        execution_errors=execution_errors,
        discard_errors=discard_errors,
        registry_mismatch=registry_mismatch,
    )
    checkpoint_paths: dict[Path, Any] = {}
    manifest_paths: dict[Path, Any] = {}
    position_by_binding = {
        (item.corpus_id, item.stream_id, item.policy_id): item.execution_position
        for item in scan.attempt_references
    }

    def verify_lineage(root: Path, protocol: Any) -> None:
        assert root == tmp_path.resolve()
        assert protocol == resolution.build_economic_resolution_protocol()
        log.append(("lineage", None))

    def authenticate(**kwargs: Any) -> SimpleNamespace:
        assert kwargs["repository_root"] == tmp_path.resolve()
        log.append(("authenticate", None))
        return SimpleNamespace(
            roots=_official_roots(),
            gate1_artifact=object(),
            gate2_artifact=object(),
            gate3_config=object(),
        )

    def scan_legacy(path: Path) -> Any:
        assert path == tmp_path / "legacy.json"
        log.append(("scan", None))
        return scan

    def build_backend(**kwargs: Any) -> _FakeBackend:
        assert kwargs["roots"] == _official_roots()
        log.append(("build_backend", None))
        return backend

    def discover_checkpoint(
        output_directory: Path,
        *,
        protocol: Any,
        legacy_reference: Any,
    ) -> tuple[Path, Any] | None:
        assert output_directory == tmp_path / "output"
        assert protocol == resolution.build_economic_resolution_protocol()
        position = legacy_reference.execution_position
        log.append(("discover_checkpoint", position))
        checkpoint = resumed.get(position)
        return None if checkpoint is None else (output_directory / f"resume-{position}", checkpoint)

    def publish_sidecar(
        output_directory: Path,
        observation: Any,
        *,
        source_lineage: str,
    ) -> tuple[Path, Any]:
        receipt = observation._receipt
        position = position_by_binding[(receipt.corpus_id, receipt.stream_id, receipt.policy_id)]
        log.append(("publish_sidecar", position))
        assert source_lineage == "repaired_runtime"
        if position == sidecar_publication_error_position:
            raise RuntimeError("synthetic publication fault")
        return output_directory / receipt.sidecar_name, receipt

    def load_sidecar(path: Path, *, receipt: Any, **kwargs: Any) -> object:
        position = position_by_binding[(receipt.corpus_id, receipt.stream_id, receipt.policy_id)]
        log.append(("load_sidecar", position))
        assert path == tmp_path / "output" / receipt.sidecar_name
        assert kwargs["expected_source_lineage"] == "repaired_runtime"
        if position == bad_sidecar_position:
            raise Gate3EconomicEvidenceError("synthetic bad sidecar")
        return _observation_from_receipt(receipt)

    def publish_checkpoint(output_directory: Path, checkpoint: Any) -> Path:
        position = checkpoint.execution_position
        log.append(("publish_checkpoint", position))
        path = output_directory / (
            f"checkpoint-{position}-{checkpoint.content_sha256.removeprefix('sha256:')}.json"
        )
        checkpoint_paths[path] = checkpoint
        return path

    def load_checkpoint(path: Path, **_kwargs: Any) -> Any:
        checkpoint = checkpoint_paths[path]
        log.append(("load_checkpoint", checkpoint.execution_position))
        return checkpoint

    def discover_manifest(
        output_directory: Path,
        *,
        protocol: Any,
        legacy_scan: Any,
        checkpoints: tuple[Any, ...],
    ) -> tuple[Path, Any] | None:
        assert protocol == resolution.build_economic_resolution_protocol()
        assert legacy_scan == scan
        assert len(checkpoints) == 96
        log.append(("discover_manifest", None))
        if existing_manifest is None:
            return None
        return output_directory / "existing-manifest.json", existing_manifest

    def publish_manifest(output_directory: Path, manifest: Any) -> Path:
        log.append(("publish_manifest", None))
        path = output_directory / "manifest.json"
        manifest_paths[path] = manifest
        return path

    def load_manifest(path: Path, **_kwargs: Any) -> Any:
        log.append(("load_manifest", None))
        return manifest_paths[path]

    monkeypatch.setattr(runner, "verify_economic_resolution_runtime_lineage", verify_lineage)
    monkeypatch.setattr(runner, "authenticate_official_gate3_early_inputs", authenticate)
    monkeypatch.setattr(runner, "scan_official_legacy_gate3_calibration_artifact", scan_legacy)
    monkeypatch.setattr(runner, "build_adapter_gate3_backend", build_backend)
    monkeypatch.setattr(
        runner,
        "discover_gate3_calibration_attempt_checkpoint",
        discover_checkpoint,
    )
    monkeypatch.setattr(runner, "publish_gate3_calibration_observation_evidence", publish_sidecar)
    monkeypatch.setattr(runner, "load_gate3_calibration_observation_evidence", load_sidecar)
    monkeypatch.setattr(runner, "publish_gate3_calibration_attempt_checkpoint", publish_checkpoint)
    monkeypatch.setattr(runner, "load_gate3_calibration_attempt_checkpoint", load_checkpoint)
    monkeypatch.setattr(runner, "discover_gate3_calibration_manifest", discover_manifest)
    monkeypatch.setattr(runner, "publish_gate3_calibration_manifest", publish_manifest)
    monkeypatch.setattr(runner, "load_gate3_calibration_manifest", load_manifest)
    return scan, backend, log


def _run(runner: ModuleType, tmp_path: Path):  # type: ignore[no-untyped-def]
    return runner.run_economic_resolution_calibration(
        repository_root=tmp_path,
        gate1_artifact_path=tmp_path / "gate1.json",
        gate2_artifact_path=tmp_path / "gate2.json",
        gate3_config_path=tmp_path / "config.json",
        legacy_gate3_artifact_path=tmp_path / "legacy.json",
        output_directory=tmp_path / "output",
    )


def test_fresh_run_reuses_60_and_executes_only_36_with_exact_success_order(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _scan_result, _backend, log = _install_fakes(monkeypatch, runner, tmp_path)

    outcome = _run(runner, tmp_path)

    counts = Counter(name for name, _position in log)
    assert outcome.manifest.status == "complete_valid"
    assert (outcome.manifest.success_count, outcome.manifest.failure_count) == (96, 0)
    assert len(outcome.manifest.baseline_freezes) == 2
    assert counts == Counter(
        {
            "lineage": 1,
            "authenticate": 1,
            "scan": 1,
            "build_backend": 1,
            "registry": 2,
            "discover_checkpoint": 96,
            "execute": 36,
            "publish_sidecar": 36,
            "release": 36,
            "load_sidecar": 36,
            "publish_checkpoint": 96,
            "load_checkpoint": 96,
            "discover_manifest": 1,
            "publish_manifest": 1,
            "load_manifest": 1,
        }
    )
    last_discovery = max(
        index for index, item in enumerate(log) if item[0] == "discover_checkpoint"
    )
    first_mutation = min(
        index
        for index, item in enumerate(log)
        if item[0] in {"execute", "publish_sidecar", "publish_checkpoint"}
    )
    assert last_discovery < first_mutation
    assert [name for name, position in log if position == 50] == [
        "discover_checkpoint",
        "execute",
        "publish_sidecar",
        "release",
        "load_sidecar",
        "publish_checkpoint",
        "load_checkpoint",
    ]
    assert [name for name, position in log if position == 0] == [
        "discover_checkpoint",
        "publish_checkpoint",
        "load_checkpoint",
    ]


def test_partial_resume_validates_sidecar_before_any_mutation_and_skips_completed_work(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    scan = _scan()
    resumed = {
        0: _checkpoint_for_reference(scan.attempt_references[0], outcome="legacy"),
        50: _checkpoint_for_reference(scan.attempt_references[50], outcome="repaired"),
        51: _checkpoint_for_reference(scan.attempt_references[51], outcome="failure"),
    }
    _scan_result, _backend, log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        resumed=resumed,
    )

    outcome = _run(runner, tmp_path)

    counts = Counter(name for name, _position in log)
    assert outcome.manifest.status == "complete_invalid"
    assert (outcome.manifest.success_count, outcome.manifest.failure_count) == (95, 1)
    assert counts["execute"] == 34
    assert counts["publish_checkpoint"] == 93
    assert [name for name, position in log if position == 50] == [
        "discover_checkpoint",
        "load_sidecar",
    ]
    assert [name for name, position in log if position == 51] == ["discover_checkpoint"]
    resumed_read = log.index(("load_sidecar", 50))
    last_discovery = max(
        index for index, item in enumerate(log) if item[0] == "discover_checkpoint"
    )
    first_mutation = min(
        index
        for index, item in enumerate(log)
        if item[0] in {"execute", "publish_sidecar", "publish_checkpoint"}
    )
    assert last_discovery < resumed_read < first_mutation


def test_full_resume_reuses_existing_equal_manifest_without_writes_or_execution(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    scan = _scan()
    checkpoints = _valid_checkpoints(resolution)
    manifest = resolution.build_gate3_calibration_manifest(checkpoints, legacy_scan=scan)
    _scan_result, _backend, log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        resumed={item.execution_position: item for item in checkpoints},
        existing_manifest=manifest,
    )

    outcome = _run(runner, tmp_path)

    counts = Counter(name for name, _position in log)
    assert outcome.manifest == manifest
    assert counts["load_sidecar"] == 36
    assert counts["execute"] == 0
    assert counts["publish_sidecar"] == 0
    assert counts["publish_checkpoint"] == 0
    assert counts["publish_manifest"] == 0
    assert counts["load_manifest"] == 0
    assert counts["discover_manifest"] == 1


def test_bad_resumed_sidecar_aborts_after_all_discovery_and_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    scan = _scan()
    resumed = {50: _checkpoint_for_reference(scan.attempt_references[50], outcome="repaired")}
    _scan_result, _backend, log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        resumed=resumed,
        bad_sidecar_position=50,
    )

    with pytest.raises(Gate3EconomicEvidenceError, match="bad sidecar"):
        _run(runner, tmp_path)

    counts = Counter(name for name, _position in log)
    assert counts["discover_checkpoint"] == 96
    assert counts["execute"] == 0
    assert counts["publish_sidecar"] == 0
    assert counts["publish_checkpoint"] == 0


def test_registry_mismatch_aborts_before_discovery_writes_or_execution(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _scan_result, _backend, log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        registry_mismatch=True,
    )

    with pytest.raises(runner.M11EconomicResolutionRunnerError, match="registry"):
        _run(runner, tmp_path)

    assert Counter(name for name, _position in log) == Counter(
        {
            "lineage": 1,
            "authenticate": 1,
            "scan": 1,
            "build_backend": 1,
            "registry": 1,
        }
    )


def test_domain_execution_failure_is_bounded_checkpoint_then_run_continues_invalid(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _scan_result, _backend, log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        execution_errors={50: RuntimeError("synthetic domain failure")},
    )

    outcome = _run(runner, tmp_path)

    checkpoint = outcome.manifest.checkpoints[50]
    assert outcome.manifest.status == "complete_invalid"
    assert (outcome.manifest.success_count, outcome.manifest.failure_count) == (95, 1)
    assert checkpoint.outcome_kind == "repaired_runtime_failure"
    assert checkpoint.failure_type == "builtins.RuntimeError"
    assert checkpoint.failure_detail == "synthetic domain failure"
    assert [name for name, position in log if position == 50] == [
        "discover_checkpoint",
        "execute",
        "discard",
        "publish_checkpoint",
        "load_checkpoint",
    ]
    assert ("execute", 51) in log


@pytest.mark.parametrize(
    "error",
    (
        OSError("disk unavailable"),
        MemoryError("memory exhausted"),
        TimeoutError("deadline exceeded"),
        KeyboardInterrupt(),
        SystemExit(7),
    ),
)
def test_infrastructure_and_interrupt_execution_faults_abort_without_failure_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
    error: BaseException,
) -> None:
    _scan_result, _backend, log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        execution_errors={50: error},
    )

    with pytest.raises(type(error)):
        _run(runner, tmp_path)

    assert [name for name, position in log if position == 50] == [
        "discover_checkpoint",
        "execute",
        "discard",
    ]
    assert ("execute", 51) not in log


def test_failed_required_discard_and_publication_fault_never_become_failure_checkpoints(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _scan_result, _backend, log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        execution_errors={50: RuntimeError("domain failure")},
        discard_errors={50: RuntimeError("cleanup failed")},
    )

    with pytest.raises(RuntimeError, match="cleanup failed"):
        _run(runner, tmp_path)
    assert ("publish_checkpoint", 50) not in log

    monkeypatch.undo()
    _scan_result, _backend, log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        sidecar_publication_error_position=50,
    )
    with pytest.raises(RuntimeError, match="publication fault"):
        _run(runner, tmp_path)
    assert [name for name, position in log if position == 50] == [
        "discover_checkpoint",
        "execute",
        "publish_sidecar",
    ]


def test_existing_manifest_must_equal_fresh_derivation(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    scan = _scan()
    checkpoints = _valid_checkpoints(resolution)
    valid = resolution.build_gate3_calibration_manifest(checkpoints, legacy_scan=scan)
    invalid_checkpoints = list(checkpoints)
    reference = scan.attempt_references[50]
    invalid_checkpoints[50] = _checkpoint_for_reference(reference, outcome="failure")
    unequal = resolution.build_gate3_calibration_manifest(
        tuple(invalid_checkpoints),
        legacy_scan=scan,
    )
    _scan_result, _backend, _log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        resumed={item.execution_position: item for item in checkpoints},
        existing_manifest=unequal,
    )

    with pytest.raises(runner.M11EconomicResolutionRunnerError, match="differs"):
        _run(runner, tmp_path)
    assert valid != unequal


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    (("complete_valid", 0), ("complete_invalid", 2)),
)
def test_main_uses_exact_project_relative_defaults_and_emits_compact_terminal_json(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    capsys: pytest.CaptureFixture[str],
    status: str,
    expected_exit: int,
) -> None:
    captured: dict[str, Path] = {}
    manifest = SimpleNamespace(
        manifest_id="yfm11econcalman-" + "a" * 24,
        content_sha256="sha256:" + "a" * 64,
        status=status,
        success_count=96 if status == "complete_valid" else 95,
        failure_count=0 if status == "complete_valid" else 1,
        baseline_freezes=(SimpleNamespace(freeze_id="yfm11g3bf-" + "b" * 24),)
        if status == "complete_valid"
        else (),
    )
    protocol = resolution.build_economic_resolution_protocol()

    def run(**kwargs: Path) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(protocol=protocol, manifest=manifest)

    monkeypatch.setattr(runner, "run_economic_resolution_calibration", run)

    assert runner.main([]) == expected_exit

    streams = capsys.readouterr()
    assert streams.err == ""
    assert "\n" not in streams.out.rstrip("\n")
    payload = json.loads(streams.out)
    assert payload["status"] == status
    assert payload["protocol_id"] == protocol.protocol_id
    root = Path(runner.__file__).resolve().parents[1]
    assert captured == {
        "repository_root": root,
        "gate1_artifact_path": root
        / "experiments/results/m11-gate1-yfm11g1run-c35f10fa4f4d7b6b01c59c29.json",
        "gate2_artifact_path": root
        / "experiments/results/m11-gate2-yfm11g2run-7419e46b74e411aff5c27ee1.json",
        "gate3_config_path": root / "benchmarks/falsification/m11-gate3-config-v1.json",
        "legacy_gate3_artifact_path": root
        / "experiments/results/m11-gate3-early-yfm11g3run-3dd87efab6f64ada4c5bd09c.json",
        "output_directory": root / "experiments/results/m11-economic-resolution",
    }


def test_main_returns_one_for_infrastructure_but_does_not_catch_interrupts(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def infrastructure(**_kwargs: Path) -> None:
        raise RuntimeError("runner fault")

    monkeypatch.setattr(runner, "run_economic_resolution_calibration", infrastructure)
    assert runner.main([]) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "infrastructure_error"

    def interrupt(**_kwargs: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_economic_resolution_calibration", interrupt)
    with pytest.raises(KeyboardInterrupt):
        runner.main([])
