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
    _real_official_validity_case,
    _validity,
)
from yieldforge.realistic_falsification.validity_evidence_store import (
    publish_gate3_validity_evidence,
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
        state: Any,
    ) -> None:
        self.evidence = evidence
        self.log = log
        self.receipt_ref = receipt_ref
        self.execute_error = execute_error
        self.state = state

    def execute_validity_controls(self, *, roots: Any, baseline_freezes: Any) -> _FullReceipt:
        self.log.append("execute")
        self.state.execute_count += 1
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
        self.state.release_count += 1
        assert self.receipt_ref[-1]() is not None
        assert roots == self.evidence.roots
        assert (expected_receipt_id, expected_receipt_content_sha256) == (
            self.evidence.validity_receipt_id,
            self.evidence.validity_receipt_content_sha256,
        )
        if self.state.release_failures_remaining:
            self.state.release_failures_remaining -= 1
            raise RuntimeError("release fault")


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
    shared_state: Any = None,
    release_failures: int = 0,
    sidecar_load_failures: int = 0,
    stage_publish_failures: int = 0,
    late_stage_competitor_on_call: int | None = None,
    late_checkpoint_competitor_on_call: int | None = None,
    late_sidecar_competitor_on_call: int | None = None,
):  # type: ignore[no-untyped-def]
    calibration = _calibration_manifest()
    loaded_calibration = calibration if calibration_override is None else calibration_override
    evidence = _compact_evidence(calibration, status=status)
    validity = _validity()
    stage = validity.build_gate3_validity_stage_manifest(
        calibration_manifest=calibration,
        validity_evidence=evidence,
    )
    checkpoint = validity.build_gate3_validity_evidence_checkpoint(
        calibration_manifest=calibration,
        validity_evidence=evidence,
    )
    state = shared_state
    if state is None:
        state = SimpleNamespace(
            stage=None,
            checkpoint=None,
            sidecar_present=False,
            stage_discovery_count=0,
            checkpoint_discovery_count=0,
            sidecar_discovery_count=0,
            sidecar_census_count=0,
            sidecar_load_count=0,
            execute_count=0,
            release_count=0,
            release_failures_remaining=release_failures,
            sidecar_load_failures_remaining=sidecar_load_failures,
            stage_publish_failures_remaining=stage_publish_failures,
            log=[],
        )
        if resume:
            state.stage = (tmp_path / "resumed-stage.json", stage)
            state.checkpoint = (tmp_path / "resumed-checkpoint.json", checkpoint)
            state.sidecar_present = True
    else:
        state.release_failures_remaining += release_failures
        state.sidecar_load_failures_remaining += sidecar_load_failures
        state.stage_publish_failures_remaining += stage_publish_failures
    log: list[str] = state.log
    receipt_ref: list[weakref.ReferenceType[_FullReceipt]] = []
    backend = _FakeBackend(
        evidence=evidence,
        log=log,
        receipt_ref=receipt_ref,
        execute_error=execute_error,
        state=state,
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
        state.stage_discovery_count += 1
        competitor_call = 2 if late_competitor else late_stage_competitor_on_call
        if competitor_call is not None and state.stage_discovery_count == competitor_call:
            competing = validity.build_gate3_validity_stage_manifest(
                calibration_manifest=calibration,
                validity_evidence=_compact_evidence(calibration, status="invalid"),
            )
            return tmp_path / "competing-stage.json", competing
        return state.stage

    def discover_checkpoint(*_args: Any, **_kwargs: Any):  # type: ignore[no-untyped-def]
        log.append("discover_checkpoint")
        state.checkpoint_discovery_count += 1
        if (
            late_checkpoint_competitor_on_call is not None
            and state.checkpoint_discovery_count == late_checkpoint_competitor_on_call
        ):
            competing = validity.build_gate3_validity_evidence_checkpoint(
                calibration_manifest=calibration,
                validity_evidence=_compact_evidence(calibration, status="invalid"),
            )
            return tmp_path / "competing-checkpoint.json", competing
        return state.checkpoint

    def discover_sidecar(*_args: Any, **_kwargs: Any) -> Path | None:
        log.append("discover_sidecar")
        state.sidecar_discovery_count += 1
        return tmp_path / evidence.sidecar_name if state.sidecar_present else None

    def publish_sidecar(_output: Path, receipt: _FullReceipt, **_kwargs: Any):  # type: ignore[no-untyped-def]
        log.append("persist_sidecar")
        assert receipt is receipt_ref[-1]()
        if sidecar_error is not None:
            raise sidecar_error
        state.sidecar_present = True
        return tmp_path / evidence.sidecar_name, evidence

    def load_sidecar(_path: Path, **kwargs: Any) -> _FullReceipt:
        log.append("read_sidecar")
        state.sidecar_load_count += 1
        if state.sidecar_load_failures_remaining:
            state.sidecar_load_failures_remaining -= 1
            raise RuntimeError("sidecar readback fault")
        if receipt_ref:
            assert receipt_ref[-1]() is None
        assert state.sidecar_present
        assert kwargs["expected_status"] == evidence.status
        return _FullReceipt(evidence)

    def recover_sidecar(_path: Path, **_kwargs: Any) -> Any:
        log.append("recover_sidecar")
        assert state.sidecar_present
        return evidence

    def publish_checkpoint(
        _output: Path,
        candidate: Any,
        *,
        calibration_manifest: Any,
    ) -> Path:
        log.append("publish_checkpoint")
        assert calibration_manifest == calibration
        state.checkpoint = (tmp_path / "published-checkpoint.json", candidate)
        return state.checkpoint[0]

    def load_checkpoint(_path: Path, **_kwargs: Any) -> Any:
        log.append("load_checkpoint")
        assert state.checkpoint is not None
        return state.checkpoint[1]

    def publish_stage(
        _output: Path,
        manifest: Any,
        *,
        calibration_manifest: Any,
    ) -> Path:
        log.append("publish_stage")
        assert calibration_manifest == calibration
        if state.stage_publish_failures_remaining:
            state.stage_publish_failures_remaining -= 1
            raise RuntimeError("stage publication fault")
        state.stage = (tmp_path / "published-stage.json", manifest)
        return state.stage[0]

    def require_sidecar_census(
        _output: Path,
        *,
        expected_evidence: Any,
    ) -> Path | None:
        state.sidecar_census_count += 1
        log.append("sidecar_census_empty" if expected_evidence is None else "sidecar_census_exact")
        competitor_call = 2 if late_sidecar_competitor else late_sidecar_competitor_on_call
        if competitor_call is not None and state.sidecar_census_count == competitor_call:
            raise validity.Gate3ValidityStageEvidenceError(
                "validity sidecar census has competing candidates"
            )
        if expected_evidence is None:
            if state.sidecar_present:
                raise validity.Gate3ValidityStageEvidenceError(
                    "validity sidecar census has an unbound candidate"
                )
            return None
        if not state.sidecar_present:
            raise validity.Gate3ValidityStageEvidenceError(
                "validity sidecar census is missing its expected receipt"
            )
        return tmp_path / evidence.sidecar_name

    def load_stage(_path: Path, **_kwargs: Any) -> Any:
        log.append("load_stage")
        assert state.stage is not None
        return state.stage[1]

    monkeypatch.setattr(runner, "verify_economic_resolution_runtime_lineage", verify)
    monkeypatch.setattr(runner, "authenticate_official_gate3_early_inputs", authenticate)
    monkeypatch.setattr(runner, "load_gate3_calibration_manifest", load_calibration)
    monkeypatch.setattr(runner, "build_adapter_gate3_backend", build_backend)
    monkeypatch.setattr(runner, "discover_gate3_validity_stage_manifest", discover_stage)
    monkeypatch.setattr(
        runner,
        "discover_gate3_validity_evidence_checkpoint",
        discover_checkpoint,
    )
    monkeypatch.setattr(runner, "discover_sole_gate3_validity_sidecar", discover_sidecar)
    monkeypatch.setattr(
        runner,
        "recover_gate3_validity_evidence_receipt",
        recover_sidecar,
    )
    monkeypatch.setattr(runner, "publish_gate3_validity_evidence", publish_sidecar)
    monkeypatch.setattr(runner, "load_gate3_validity_evidence", load_sidecar)
    monkeypatch.setattr(
        runner,
        "publish_gate3_validity_evidence_checkpoint",
        publish_checkpoint,
    )
    monkeypatch.setattr(
        runner,
        "load_gate3_validity_evidence_checkpoint",
        load_checkpoint,
    )
    monkeypatch.setattr(runner, "publish_gate3_validity_stage_manifest", publish_stage)
    monkeypatch.setattr(runner, "load_gate3_validity_stage_manifest", load_stage)
    monkeypatch.setattr(runner, "require_gate3_validity_sidecar_census", require_sidecar_census)
    monkeypatch.setattr(runner, "_strict_validity_receipt", lambda item: item)
    return SimpleNamespace(
        calibration=calibration,
        evidence=evidence,
        checkpoint=checkpoint,
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
        "discover_checkpoint",
        "discover_sidecar",
        "sidecar_census_empty",
        "execute",
        "persist_sidecar",
        "release",
        "sidecar_census_exact",
        "read_sidecar",
        "publish_checkpoint",
        "load_checkpoint",
        "discover_checkpoint",
        "discover_stage",
        "publish_stage",
        "load_stage",
        "discover_stage",
        "read_sidecar",
        "discover_stage",
        "discover_checkpoint",
        "sidecar_census_exact",
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
        "discover_checkpoint",
        "sidecar_census_exact",
        "read_sidecar",
        "discover_stage",
        "discover_checkpoint",
        "sidecar_census_exact",
    ]
    assert world.state.execute_count == 0


def test_runner_real_filesystem_orphan_recovery_and_terminal_resume_never_execute(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validity = _validity()
    calibration, freezes, receipt = _real_official_validity_case(monkeypatch)
    sidecar_path, evidence = publish_gate3_validity_evidence(
        tmp_path,
        receipt,
        baseline_freezes=freezes,
    )
    calls: list[str] = []

    class NeverExecuteBackend:
        def execute_validity_controls(self, **_kwargs: Any) -> Any:
            pytest.fail("terminal resume must not execute validity controls")

    monkeypatch.setattr(
        runner,
        "verify_economic_resolution_runtime_lineage",
        lambda *_args, **_kwargs: calls.append("lineage"),
    )
    monkeypatch.setattr(
        runner,
        "authenticate_official_gate3_early_inputs",
        lambda **_kwargs: SimpleNamespace(
            roots=calibration.roots,
            gate1_artifact=object(),
            gate2_artifact=object(),
            gate3_config=object(),
        ),
    )
    monkeypatch.setattr(
        runner,
        "load_gate3_calibration_manifest",
        lambda *_args, **_kwargs: calibration,
    )
    monkeypatch.setattr(
        runner,
        "build_adapter_gate3_backend",
        lambda **_kwargs: NeverExecuteBackend(),
    )

    recovered = _run(runner, tmp_path)
    resumed = _run(runner, tmp_path)

    assert recovered.manifest.validity_evidence == evidence
    assert resumed == recovered
    assert validity.discover_gate3_validity_evidence_checkpoint(
        tmp_path,
        protocol=calibration.protocol,
        roots=calibration.roots,
        calibration_manifest=calibration,
    ) == (recovered.evidence_checkpoint_path, recovered.evidence_checkpoint)
    assert validity.discover_gate3_validity_stage_manifest(
        tmp_path,
        protocol=calibration.protocol,
        roots=calibration.roots,
        calibration_manifest=calibration,
    ) == (recovered.manifest_path, recovered.manifest)
    assert sidecar_path == tmp_path / evidence.sidecar_name
    assert calls == ["lineage", "lineage"]


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
    with pytest.raises(runner.M11EconomicValidityRunnerError, match="differs"):
        _run(runner, tmp_path)
    assert "publish_stage" not in world.log


@pytest.mark.parametrize(
    "failure_phase",
    ["release", "readback", "stage_publication"],
)
def test_runner_resumes_after_post_persistence_fault_without_reexecution(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    world = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        release_failures=1 if failure_phase == "release" else 0,
        sidecar_load_failures=1 if failure_phase == "readback" else 0,
        stage_publish_failures=1 if failure_phase == "stage_publication" else 0,
    )

    with pytest.raises(RuntimeError, match="fault"):
        _run(runner, tmp_path)

    assert world.state.sidecar_present is True
    assert world.state.stage is None
    if failure_phase == "stage_publication":
        assert world.state.checkpoint is not None
    first_log_length = len(world.log)
    monkeypatch.undo()
    resumed = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        shared_state=world.state,
    )

    result = _run(runner, tmp_path)

    assert result.manifest == world.stage
    assert resumed.state.execute_count == 1
    assert resumed.state.release_count == 1
    resumed_log = resumed.log[first_log_length:]
    assert "execute" not in resumed_log
    assert "persist_sidecar" not in resumed_log
    if failure_phase in {"release", "readback"}:
        assert "recover_sidecar" in resumed_log
        assert "publish_checkpoint" in resumed_log
    else:
        assert "recover_sidecar" not in resumed_log


@pytest.mark.parametrize(
    ("resume", "late_stage_call", "late_checkpoint_call", "late_sidecar_call"),
    [
        (False, 4, None, None),
        (False, None, 3, None),
        (False, None, None, 3),
        (True, 2, None, None),
        (True, None, 2, None),
        (True, None, None, 2),
    ],
)
def test_runner_final_sidecar_read_is_followed_by_all_three_competitor_checks(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resume: bool,
    late_stage_call: int | None,
    late_checkpoint_call: int | None,
    late_sidecar_call: int | None,
) -> None:
    world = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        resume=resume,
        late_stage_competitor_on_call=late_stage_call,
        late_checkpoint_competitor_on_call=late_checkpoint_call,
        late_sidecar_competitor_on_call=late_sidecar_call,
    )

    with pytest.raises(
        (
            runner.M11EconomicValidityRunnerError,
            _validity().Gate3ValidityStageEvidenceError,
        )
    ):
        _run(runner, tmp_path)

    last_read = len(world.log) - 1 - world.log[::-1].index("read_sidecar")
    expected_suffix = ["discover_stage"]
    if late_stage_call is None:
        expected_suffix.append("discover_checkpoint")
    if late_stage_call is None and late_checkpoint_call is None:
        expected_suffix.append("sidecar_census_exact")
    assert world.log[last_read + 1 :] == expected_suffix


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
