from __future__ import annotations

import importlib
import json
import weakref
from collections import Counter
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

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
from yieldforge.realistic_falsification.gate3_backend_impl import (
    AdapterGate3BackendError,
)
from yieldforge.reuse.contracts import ReuseAccounting


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


class _FakeObservation:
    pass


def _observation_from_receipt(receipt):  # type: ignore[no-untyped-def]
    observation = _FakeObservation()
    observation.roots = receipt.roots
    observation.corpus_id = receipt.corpus_id
    observation.stream_id = receipt.stream_id
    observation.policy_id = receipt.policy_id
    observation.observation_id = receipt.observation_id
    observation.content_sha256 = receipt.observation_content_sha256
    observation.final_costs = receipt.final_costs
    observation.full_sheet_opening_count = receipt.full_sheet_opening_count
    observation.exact_event_census = receipt.exact_event_census
    observation._receipt = receipt
    return observation


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
    late_checkpoint_competitor_position: int | None = None,
    late_manifest_competitor: bool = False,
    late_sidecar_fault_position: int | None = None,
    late_sidecar_fault_call: int = 2,
    late_sidecar_fault_detail: str = "synthetic late sidecar fault",
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
    observation_refs: dict[int, weakref.ReferenceType[_FakeObservation]] = {}
    checkpoint_discovery_counts: Counter[int] = Counter()
    sidecar_load_counts: Counter[int] = Counter()
    manifest_discovery_count = 0
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
        checkpoint_discovery_counts[position] += 1
        if (
            position == late_checkpoint_competitor_position
            and checkpoint_discovery_counts[position] == 2
        ):
            raise resolution.EconomicResolutionEvidenceError("synthetic late competing checkpoint")
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
        observation_refs[position] = weakref.ref(observation)
        return output_directory / receipt.sidecar_name, receipt

    def load_sidecar(path: Path, *, receipt: Any, **kwargs: Any) -> object:
        position = position_by_binding[(receipt.corpus_id, receipt.stream_id, receipt.policy_id)]
        log.append(("load_sidecar", position))
        sidecar_load_counts[position] += 1
        assert path == tmp_path / "output" / receipt.sidecar_name
        assert kwargs["expected_source_lineage"] == "repaired_runtime"
        if position == bad_sidecar_position:
            raise Gate3EconomicEvidenceError("synthetic bad sidecar")
        if (
            position == late_sidecar_fault_position
            and sidecar_load_counts[position] == late_sidecar_fault_call
        ):
            raise Gate3EconomicEvidenceError(late_sidecar_fault_detail)
        if position in observation_refs:
            assert observation_refs[position]() is None
        return _observation_from_receipt(receipt)

    def publish_checkpoint(output_directory: Path, checkpoint: Any) -> Path:
        position = checkpoint.execution_position
        log.append(("publish_checkpoint", position))
        path = output_directory / (
            f"checkpoint-{position}-{checkpoint.content_sha256.removeprefix('sha256:')}.json"
        )
        checkpoint_paths[path] = checkpoint
        resumed[position] = checkpoint
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
        nonlocal manifest_discovery_count
        assert protocol == resolution.build_economic_resolution_protocol()
        assert legacy_scan == scan
        assert len(checkpoints) == 96
        log.append(("discover_manifest", None))
        manifest_discovery_count += 1
        if late_manifest_competitor and manifest_discovery_count == 2:
            raise resolution.EconomicResolutionEvidenceError("synthetic late competing manifest")
        if existing_manifest is None:
            if not manifest_paths:
                return None
            return next(iter(manifest_paths.items()))
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
            "discover_checkpoint": 192,
            "execute": 36,
            "publish_sidecar": 36,
            "release": 36,
            "load_sidecar": 108,
            "publish_checkpoint": 96,
            "load_checkpoint": 96,
            "discover_manifest": 2,
            "publish_manifest": 1,
            "load_manifest": 1,
        }
    )
    discovery_indices = [
        index for index, item in enumerate(log) if item[0] == "discover_checkpoint"
    ]
    last_discovery = discovery_indices[95]
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
        "discover_checkpoint",
        "load_sidecar",
        "load_sidecar",
    ]
    assert [name for name, position in log if position == 0] == [
        "discover_checkpoint",
        "publish_checkpoint",
        "load_checkpoint",
        "discover_checkpoint",
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
        "discover_checkpoint",
        "load_sidecar",
        "load_sidecar",
    ]
    assert [name for name, position in log if position == 51] == [
        "discover_checkpoint",
        "discover_checkpoint",
    ]
    resumed_read = log.index(("load_sidecar", 50))
    discovery_indices = [
        index for index, item in enumerate(log) if item[0] == "discover_checkpoint"
    ]
    last_discovery = discovery_indices[95]
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
    assert counts["load_sidecar"] == 108
    assert counts["execute"] == 0
    assert counts["publish_sidecar"] == 0
    assert counts["publish_checkpoint"] == 0
    assert counts["publish_manifest"] == 0
    assert counts["load_manifest"] == 0
    assert counts["discover_checkpoint"] == 192
    assert counts["discover_manifest"] == 2


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


@pytest.mark.parametrize(
    "domain_error",
    (
        AdapterGate3BackendError("synthetic deterministic backend failure"),
        pytest.param(None, id="legacy-reuse-accounting-validation"),
    ),
)
def test_explicit_domain_execution_failure_is_bounded_then_run_continues_invalid(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
    domain_error: BaseException | None,
) -> None:
    if domain_error is None:
        with pytest.raises(ValidationError) as captured:
            ReuseAccounting(
                parent_remnant_area=1.0,
                placed_area=0.25,
                process_loss_area=0.0,
                retained_child_area=0.0,
                scrap_area=0.0,
                reconciliation_delta=0.5,
                area_tolerance=1.0,
            )
        domain_error = captured.value
    _scan_result, _backend, log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        execution_errors={50: domain_error},
    )

    outcome = _run(runner, tmp_path)

    checkpoint = outcome.manifest.checkpoints[50]
    assert outcome.manifest.status == "complete_invalid"
    assert (outcome.manifest.success_count, outcome.manifest.failure_count) == (95, 1)
    assert checkpoint.outcome_kind == "repaired_runtime_failure"
    assert checkpoint.failure_type in {
        "yieldforge.realistic_falsification.gate3_backend_impl.AdapterGate3BackendError",
        "pydantic_core._pydantic_core.ValidationError",
    }
    assert [name for name, position in log if position == 50] == [
        "discover_checkpoint",
        "execute",
        "discard",
        "publish_checkpoint",
        "load_checkpoint",
        "discover_checkpoint",
    ]
    assert ("execute", 51) in log


@pytest.mark.parametrize(
    "error",
    (
        OSError("disk unavailable"),
        MemoryError("memory exhausted"),
        TimeoutError("deadline exceeded"),
        AssertionError("programmer assertion"),
        KeyError("unexpected key"),
        TypeError("unexpected type"),
        IndexError("unexpected index"),
        RuntimeError("unexpected runtime fault"),
        ValueError("unclassified value error"),
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
        execution_errors={50: AdapterGate3BackendError("domain failure")},
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


def test_late_checkpoint_competitor_aborts_before_manifest_derivation(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _scan_result, _backend, log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        late_checkpoint_competitor_position=95,
    )

    with pytest.raises(
        resolution.EconomicResolutionEvidenceError,
        match="late competing checkpoint",
    ):
        _run(runner, tmp_path)

    counts = Counter(name for name, _position in log)
    assert counts["publish_checkpoint"] == 96
    assert counts["discover_checkpoint"] == 192
    assert counts["discover_manifest"] == 0
    assert counts["publish_manifest"] == 0


def test_late_manifest_competitor_aborts_after_publication_and_readback(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _scan_result, _backend, log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        late_manifest_competitor=True,
    )

    with pytest.raises(
        resolution.EconomicResolutionEvidenceError,
        match="late competing manifest",
    ):
        _run(runner, tmp_path)

    counts = Counter(name for name, _position in log)
    assert counts["discover_checkpoint"] == 192
    assert counts["discover_manifest"] == 2
    assert counts["publish_manifest"] == 1
    assert counts["load_manifest"] == 1


@pytest.mark.parametrize(
    ("fault_call", "detail", "expected_manifest_discoveries", "expected_loads"),
    (
        (2, "synthetic late deleted sidecar", 0, 37),
        (3, "synthetic late corrupted sidecar", 2, 73),
    ),
)
def test_terminal_sidecar_census_catches_late_delete_or_corruption(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
    fault_call: int,
    detail: str,
    expected_manifest_discoveries: int,
    expected_loads: int,
) -> None:
    _scan_result, _backend, log = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        late_sidecar_fault_position=50,
        late_sidecar_fault_call=fault_call,
        late_sidecar_fault_detail=detail,
    )

    with pytest.raises(Gate3EconomicEvidenceError, match=detail):
        _run(runner, tmp_path)

    counts = Counter(name for name, _position in log)
    assert counts["discover_checkpoint"] == 192
    assert counts["load_sidecar"] == expected_loads
    assert counts["discover_manifest"] == expected_manifest_discoveries
    assert counts["publish_manifest"] == (1 if fault_call == 3 else 0)


def test_real_immutable_sidecar_checkpoint_discovery_and_resume_wiring(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    from tests.realistic_falsification import test_confirmation as confirmation_test

    scan = _scan()
    reference = scan.attempt_references[50]
    monkeypatch.setattr(confirmation_test, "_roots", _official_roots)
    confirmation_test._fake_calibration_observation.cache_clear()
    observation = confirmation_test._fake_calibration_observation(
        reference.corpus_id,
        reference.stream_id,
        reference.policy_id,
    )
    confirmation_test._fake_calibration_observation.cache_clear()
    assert observation.roots == reference.roots

    class _OneObservationBackend:
        def __init__(self, value: Any) -> None:
            self.value = value
            self.released = False

        def execute_calibration_stream(self, **_kwargs: Any) -> Any:
            assert self.value is not None
            return self.value

        def release_calibration_stream_evidence(
            self,
            *,
            expected_observation_id: str,
            expected_observation_content_sha256: str,
            **_kwargs: Any,
        ) -> None:
            assert self.value.observation_id == expected_observation_id
            assert self.value.content_sha256 == expected_observation_content_sha256
            self.value = None
            self.released = True

        def discard_incomplete_calibration_stream_evidence(self, **_kwargs: Any) -> None:
            raise AssertionError("successful integration must not discard")

    backend = _OneObservationBackend(observation)
    del observation
    output = tmp_path / "real-evidence"
    protocol = resolution.build_economic_resolution_protocol()

    checkpoint = runner._execute_missing_failure(
        backend=backend,
        output_directory=output,
        protocol=protocol,
        reference=reference,
    )

    assert backend.released is True
    assert checkpoint.outcome_kind == "repaired_runtime_success"
    receipt = checkpoint.repaired_receipt
    assert receipt is not None
    assert (output / receipt.sidecar_name).is_file()
    discovered = resolution.discover_gate3_calibration_attempt_checkpoint(
        output,
        protocol=protocol,
        legacy_reference=reference,
    )
    assert discovered is not None
    assert discovered[1] == checkpoint
    runner._load_repaired_sidecar(output, receipt)
    resumed = resolution.discover_gate3_calibration_attempt_checkpoint(
        output,
        protocol=protocol,
        legacy_reference=reference,
    )
    assert resumed == discovered

    checkpoints = list(_valid_checkpoints(resolution))
    checkpoints[reference.execution_position] = checkpoint
    sidecar_path = output / receipt.sidecar_name
    sidecar_path.unlink()
    with pytest.raises(Gate3EconomicEvidenceError):
        runner._validate_terminal_repaired_sidecars(
            output_directory=output,
            checkpoints=tuple(checkpoints),
        )
    sidecar_path.write_bytes(b"corrupted-sidecar")
    with pytest.raises(Gate3EconomicEvidenceError):
        runner._validate_terminal_repaired_sidecars(
            output_directory=output,
            checkpoints=tuple(checkpoints),
        )


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
        return SimpleNamespace(
            protocol=protocol,
            manifest=manifest,
            manifest_path=Path("/evidence/manifest.json"),
        )

    monkeypatch.setattr(runner, "run_economic_resolution_calibration", run)

    assert runner.main([]) == expected_exit

    streams = capsys.readouterr()
    assert streams.err == ""
    assert "\n" not in streams.out.rstrip("\n")
    payload = json.loads(streams.out)
    assert payload["status"] == status
    assert payload["protocol_id"] == protocol.protocol_id
    assert payload["manifest_path"] == "/evidence/manifest.json"
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
