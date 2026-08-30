from __future__ import annotations

import importlib
import json
import weakref
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tests.realistic_falsification.test_economic_validity import (
    _calibration_manifest,
    _compact_evidence,
    _validity,
)


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return importlib.import_module("tools.run_m11_economic_validity")


class _FullReceipt:
    __slots__ = (
        "roots",
        "receipt_id",
        "content_sha256",
        "status",
        "failure_codes",
        "diagnosis_codes",
        "exact_control_census",
        "raw_controls_revalidated",
        "__weakref__",
    )

    def __init__(self, evidence: Any) -> None:
        self.roots = evidence.roots
        self.receipt_id = evidence.validity_receipt_id
        self.content_sha256 = evidence.validity_receipt_content_sha256
        self.status = evidence.status
        self.failure_codes = evidence.failure_codes
        self.diagnosis_codes = evidence.diagnosis_codes
        self.exact_control_census = True
        self.raw_controls_revalidated = True


class _FakeBackend:
    def __init__(
        self,
        *,
        evidence: Any,
        log: list[str],
        receipt_ref: list[weakref.ReferenceType[_FullReceipt]],
        execute_error: BaseException | None,
    ) -> None:
        self.evidence = evidence
        self.log = log
        self.receipt_ref = receipt_ref
        self.execute_error = execute_error

    def execute_validity_controls(self, *, roots: Any, baseline_freezes: Any) -> _FullReceipt:
        self.log.append("execute")
        assert roots == self.evidence.roots
        if self.execute_error is not None:
            raise self.execute_error
        receipt = _FullReceipt(self.evidence)
        self.receipt_ref.append(weakref.ref(receipt))
        return receipt

    def release_validity_controls_evidence(
        self,
        *,
        roots: Any,
        baseline_freezes: Any,
        expected_receipt_id: str,
        expected_receipt_content_sha256: str,
    ) -> None:
        self.log.append("release")
        assert self.receipt_ref[-1]() is not None
        assert roots == self.evidence.roots
        assert (expected_receipt_id, expected_receipt_content_sha256) == (
            self.evidence.validity_receipt_id,
            self.evidence.validity_receipt_content_sha256,
        )


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
    *,
    status: str = "valid",
    resume: bool = False,
    execute_error: BaseException | None = None,
    sidecar_error: Exception | None = None,
    late_competitor: bool = False,
    late_sidecar_competitor: bool = False,
    calibration_override: Any = None,
):  # type: ignore[no-untyped-def]
    calibration = _calibration_manifest()
    loaded_calibration = calibration if calibration_override is None else calibration_override
    evidence = _compact_evidence(calibration, status=status)
    validity = _validity()
    stage = validity.build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=evidence,
    )
    log: list[str] = []
    receipt_ref: list[weakref.ReferenceType[_FullReceipt]] = []
    backend = _FakeBackend(
        evidence=evidence,
        log=log,
        receipt_ref=receipt_ref,
        execute_error=execute_error,
    )
    state = SimpleNamespace(
        stage=(tmp_path / "resumed-stage.json", stage) if resume else None,
        discovery_count=0,
        sidecar_census_count=0,
    )

    def verify(*_args: Any, **_kwargs: Any) -> None:
        log.append("lineage")

    def authenticate(**_kwargs: Any) -> Any:
        log.append("auth")
        return SimpleNamespace(
            roots=calibration.roots,
            gate1_artifact=object(),
            gate2_artifact=object(),
            gate3_config=object(),
        )

    def load_calibration(*_args: Any, **_kwargs: Any) -> Any:
        log.append("load_calibration")
        return loaded_calibration

    def build_backend(**_kwargs: Any) -> _FakeBackend:
        log.append("build_backend")
        return backend

    def discover_stage(*_args: Any, **_kwargs: Any):  # type: ignore[no-untyped-def]
        log.append("discover_stage")
        state.discovery_count += 1
        if late_competitor and state.discovery_count == 2:
            competing = validity.build_gate3_validity_stage_manifest(
                calibration_manifest=calibration,
                validity_evidence=_compact_evidence(calibration, status="invalid"),
            )
            return tmp_path / "competing-stage.json", competing
        return state.stage

    def publish_sidecar(_output: Path, receipt: _FullReceipt, **_kwargs: Any):  # type: ignore[no-untyped-def]
        log.append("persist_sidecar")
        assert receipt is receipt_ref[-1]()
        if sidecar_error is not None:
            raise sidecar_error
        return tmp_path / evidence.sidecar_name, evidence

    def load_sidecar(_path: Path, **kwargs: Any) -> _FullReceipt:
        log.append("read_sidecar")
        if receipt_ref:
            assert receipt_ref[-1]() is None
        assert kwargs["expected_status"] == evidence.status
        return _FullReceipt(evidence)

    def publish_stage(
        _output: Path,
        manifest: Any,
        *,
        calibration_manifest: Any,
    ) -> Path:
        log.append("publish_stage")
        assert calibration_manifest == calibration
        state.stage = (tmp_path / "published-stage.json", manifest)
        return state.stage[0]

    def require_sidecar_census(
        _output: Path,
        *,
        expected_evidence: Any,
    ) -> Path | None:
        state.sidecar_census_count += 1
        log.append("sidecar_census_empty" if expected_evidence is None else "sidecar_census_exact")
        if late_sidecar_competitor and state.sidecar_census_count == 2:
            raise validity.Gate3ValidityStageEvidenceError(
                "validity sidecar census has competing candidates"
            )
        return None if expected_evidence is None else tmp_path / evidence.sidecar_name

    def load_stage(_path: Path, **_kwargs: Any) -> Any:
        log.append("load_stage")
        assert state.stage is not None
        return state.stage[1]

    monkeypatch.setattr(runner, "verify_economic_resolution_runtime_lineage", verify)
    monkeypatch.setattr(runner, "authenticate_official_gate3_early_inputs", authenticate)
    monkeypatch.setattr(runner, "load_gate3_calibration_manifest", load_calibration)
    monkeypatch.setattr(runner, "build_adapter_gate3_backend", build_backend)
    monkeypatch.setattr(runner, "discover_gate3_validity_stage_manifest", discover_stage)
    monkeypatch.setattr(runner, "publish_gate3_validity_evidence", publish_sidecar)
    monkeypatch.setattr(runner, "load_gate3_validity_evidence", load_sidecar)
    monkeypatch.setattr(runner, "publish_gate3_validity_stage_manifest", publish_stage)
    monkeypatch.setattr(runner, "load_gate3_validity_stage_manifest", load_stage)
    monkeypatch.setattr(runner, "require_gate3_validity_sidecar_census", require_sidecar_census)
    monkeypatch.setattr(runner, "_strict_validity_receipt", lambda item: item)
    return SimpleNamespace(
        calibration=calibration,
        evidence=evidence,
        stage=stage,
        log=log,
        state=state,
        receipt_ref=receipt_ref,
    )


def _run(runner: ModuleType, tmp_path: Path):  # type: ignore[no-untyped-def]
    calibration = _calibration_manifest()
    digest = calibration.content_sha256.removeprefix("sha256:")
    return runner.run_economic_validity_stage(
        repository_root=tmp_path,
        gate1_artifact_path=tmp_path / "gate1.json",
        gate2_artifact_path=tmp_path / "gate2.json",
        gate3_config_path=tmp_path / "config.json",
        calibration_manifest_path=(tmp_path / f"m11-economic-calibration-manifest-{digest}.json"),
        output_directory=tmp_path,
    )


@pytest.mark.parametrize(
    ("status", "authorized"),
    [("valid", True), ("diagnosis_required", False), ("invalid", False)],
)
def test_runner_executes_once_and_returns_exact_scientific_decision(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    authorized: bool,
) -> None:
    world = _install_fakes(monkeypatch, runner, tmp_path, status=status)

    result = _run(runner, tmp_path)

    assert result.manifest.status == status
    assert result.manifest.central_authorized is authorized
    assert world.log == [
        "lineage",
        "auth",
        "load_calibration",
        "build_backend",
        "discover_stage",
        "sidecar_census_empty",
        "execute",
        "persist_sidecar",
        "release",
        "sidecar_census_exact",
        "read_sidecar",
        "discover_stage",
        "publish_stage",
        "load_stage",
        "discover_stage",
        "sidecar_census_exact",
        "read_sidecar",
    ]
    assert world.receipt_ref[-1]() is None


def test_runner_full_resume_fresh_loads_sidecar_without_execute(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _install_fakes(monkeypatch, runner, tmp_path, resume=True)

    result = _run(runner, tmp_path)

    assert result.manifest == world.stage
    assert world.log == [
        "lineage",
        "auth",
        "load_calibration",
        "build_backend",
        "discover_stage",
        "sidecar_census_exact",
        "read_sidecar",
    ]


@pytest.mark.parametrize(
    "update",
    [
        {"status": "complete_invalid"},
        {"success_count": 95, "failure_count": 1},
        {"baseline_freezes": ()},
    ],
)
def test_runner_rejects_calibration_before_backend_or_execution(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    update: dict[str, Any],
) -> None:
    calibration = _calibration_manifest()
    world = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        calibration_override=calibration.model_copy(update=update),
    )

    with pytest.raises(runner.M11EconomicValidityRunnerError, match="complete-valid"):
        _run(runner, tmp_path)

    assert world.log == ["lineage", "auth", "load_calibration"]


@pytest.mark.parametrize("error", [RuntimeError("boom"), KeyboardInterrupt()])
def test_runner_does_not_convert_execution_exception_or_interrupt_into_result(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    world = _install_fakes(monkeypatch, runner, tmp_path, execute_error=error)

    with pytest.raises(type(error), match="boom" if isinstance(error, RuntimeError) else None):
        _run(runner, tmp_path)

    assert "persist_sidecar" not in world.log
    assert "publish_stage" not in world.log


def test_runner_aborts_before_stage_on_sidecar_or_late_competitor(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        sidecar_error=RuntimeError("sidecar competitor"),
    )
    with pytest.raises(RuntimeError, match="sidecar competitor"):
        _run(runner, tmp_path)
    assert "release" not in world.log
    assert "publish_stage" not in world.log

    monkeypatch.undo()
    world = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        late_sidecar_competitor=True,
    )
    with pytest.raises(
        _validity().Gate3ValidityStageEvidenceError,
        match="competing",
    ):
        _run(runner, tmp_path)
    assert "read_sidecar" not in world.log
    assert "publish_stage" not in world.log

    monkeypatch.undo()
    world = _install_fakes(monkeypatch, runner, tmp_path, late_competitor=True)
    with pytest.raises(runner.M11EconomicValidityRunnerError, match="late competing"):
        _run(runner, tmp_path)
    assert "publish_stage" not in world.log


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [("valid", 0), ("diagnosis_required", 2), ("invalid", 2)],
)
def test_cli_emits_compact_terminal_json_and_scientific_exit_code(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    expected_exit: int,
) -> None:
    calibration = _calibration_manifest()
    evidence = _compact_evidence(calibration, status=status)
    stage = _validity().build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=evidence,
    )
    outcome = SimpleNamespace(
        protocol=calibration.protocol,
        calibration_manifest=calibration,
        manifest=stage,
        manifest_path=tmp_path / "stage.json",
    )
    monkeypatch.setattr(runner, "run_economic_validity_stage", lambda **_kwargs: outcome)

    exit_code = runner.main(
        [
            "--repository-root",
            str(tmp_path),
            "--calibration-manifest",
            "calibration.json",
        ]
    )

    assert exit_code == expected_exit
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "central_authorized": status == "valid",
        "manifest_content_sha256": stage.content_sha256,
        "manifest_id": stage.manifest_id,
        "manifest_path": str(tmp_path / "stage.json"),
        "status": status,
        "validity_receipt_content_sha256": evidence.validity_receipt_content_sha256,
        "validity_receipt_id": evidence.validity_receipt_id,
    }


def test_cli_reports_infrastructure_error_and_propagates_interrupt(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        runner,
        "run_economic_validity_stage",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("infra")),
    )
    args = [
        "--repository-root",
        str(tmp_path),
        "--calibration-manifest",
        "calibration.json",
    ]
    assert runner.main(args) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "infrastructure_error"

    monkeypatch.setattr(
        runner,
        "run_economic_validity_stage",
        lambda **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        runner.main(args)


def test_cli_calibration_manifest_discovery_is_unambiguous_and_fail_closed(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "experiments/results/m11-economic-resolution"
    output.mkdir(parents=True)
    first = output / f"m11-economic-calibration-manifest-{'1' * 64}.json"
    first.write_text("{}")
    captured: dict[str, Any] = {}

    def run(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise RuntimeError("stop after path capture")

    monkeypatch.setattr(runner, "run_economic_validity_stage", run)
    assert runner.main(["--repository-root", str(tmp_path)]) == 1
    assert captured["calibration_manifest_path"] == first

    second = output / f"m11-economic-calibration-manifest-{'2' * 64}.json"
    second.write_text("{}")
    captured.clear()
    assert runner.main(["--repository-root", str(tmp_path)]) == 1
    assert captured == {}
