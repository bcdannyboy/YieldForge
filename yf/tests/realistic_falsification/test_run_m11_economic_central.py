from __future__ import annotations

import importlib
import importlib.util
import json
import os
from collections import Counter
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tests.realistic_falsification.test_economic_central import (
    _checkpoints_for,
    _receipts,
    _upstream,
)
from yieldforge.experiments.contracts import semantic_sha256


def test_economic_central_runner_module_exists() -> None:
    assert importlib.util.find_spec("tools.run_m11_economic_central") is not None


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return importlib.import_module("tools.run_m11_economic_central")


class _FakeCell:
    def __init__(self, receipt: Any, freeze: Any) -> None:
        self._receipt = receipt
        self.roots = receipt.roots
        self.corpus_id = receipt.corpus_id
        self.stream_id = receipt.stream_id
        self.regime = receipt.regime
        self.baseline_freeze = freeze
        self.baseline_freeze_id = freeze.freeze_id
        self.baseline_freeze_content_sha256 = freeze.content_sha256
        self.cell_id = receipt.cell_id
        self.content_sha256 = receipt.cell_content_sha256


class _FakeBackend:
    def __init__(
        self,
        *,
        receipts: tuple[Any, ...],
        freezes: dict[str, Any],
        log: list[tuple[str, str]],
        failure_stream: str | None,
    ) -> None:
        self.receipts = {item.stream_id: item for item in receipts}
        self.freezes = freezes
        self.log = log
        self.failure_stream = failure_stream

    def confirmation_stream_ids(self, corpus_id: str) -> tuple[str, ...]:
        self.log.append(("registry", corpus_id))
        return tuple(
            item.stream_id for item in self.receipts.values() if item.corpus_id == corpus_id
        )

    def execute_central_stream(self, **kwargs: Any) -> _FakeCell:
        stream_id = kwargs["stream_id"]
        self.log.append(("execute", stream_id))
        if stream_id == self.failure_stream:
            raise RuntimeError("synthetic central execution failure")
        return _FakeCell(self.receipts[stream_id], self.freezes[kwargs["corpus_id"]])

    def release_central_stream_evidence(self, **kwargs: Any) -> None:
        stream_id = kwargs["stream_id"]
        receipt = self.receipts[stream_id]
        assert kwargs["expected_cell_id"] == receipt.cell_id
        assert kwargs["expected_cell_content_sha256"] == receipt.cell_content_sha256
        self.log.append(("release", stream_id))

    def discard_incomplete_central_stream_evidence(self, **kwargs: Any) -> None:
        self.log.append(("discard", kwargs["stream_id"]))


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
    *,
    loco_f: str = "3.000000",
    loco_k: str = "2.000000",
    lectra_f: str = "3.000000",
    lectra_k: str = "2.000000",
    resumed_count: int = 0,
    orphan_position: int | None = None,
    failure_position: int | None = None,
    validity_status: str = "valid",
) -> tuple[Any, Any, Any, _FakeBackend, list[tuple[str, str]], dict[str, Any]]:
    calibration, validity, addendum = _upstream(validity_status=validity_status)
    loco = _receipts(f_values=(loco_f,) * 20, k_values=(loco_k,) * 20)
    lectra = _receipts(
        corpus_id="lectra-m3-m4",
        f_values=(lectra_f,) * 20,
        k_values=(lectra_k,) * 20,
    )
    receipts = loco + lectra
    freezes = {item.corpus_id: item for item in calibration.baseline_freezes}
    log: list[tuple[str, str]] = []
    failure_stream = None if failure_position is None else receipts[failure_position].stream_id
    backend = _FakeBackend(
        receipts=receipts,
        freezes=freezes,
        log=log,
        failure_stream=failure_stream,
    )
    state: dict[str, Any] = {
        "checkpoints": {},
        "checkpoint_competitors": [],
        "summaries": {},
        "summary_competitors": [],
        "manifest": None,
        "sidecars": {},
    }
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    for position, checkpoint in enumerate(_checkpoints_for(receipts[:resumed_count], start=0)):
        path = tmp_path / "output" / f"checkpoint-{position}.json"
        state["checkpoints"][path] = checkpoint
        receipt = receipts[position]
        sidecar_path = tmp_path / "output" / receipt.sidecar_name
        sidecar_path.write_bytes(receipt.content_sha256.encode())
        state["sidecars"][receipt.sidecar_name] = sidecar_path
    if orphan_position is not None:
        receipt = receipts[orphan_position]
        sidecar_path = tmp_path / "output" / receipt.sidecar_name
        sidecar_path.write_bytes(receipt.content_sha256.encode())
        state["sidecars"][receipt.sidecar_name] = sidecar_path

    monkeypatch.setattr(runner, "verify_economic_resolution_runtime_lineage", lambda *_: None)
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
        runner, "load_gate3_calibration_manifest", lambda *_args, **_kw: calibration
    )
    monkeypatch.setattr(
        runner, "load_gate3_validity_stage_manifest", lambda *_args, **_kw: validity
    )
    calibration_path = tmp_path / ("m11-economic-calibration-manifest-" + "a" * 64 + ".json")
    validity_path = tmp_path / ("m11-economic-validity-stage-" + "b" * 64 + ".json")
    monkeypatch.setattr(
        runner,
        "discover_gate3_calibration_manifest",
        lambda *_args, **_kwargs: (calibration_path, calibration),
    )
    monkeypatch.setattr(
        runner,
        "discover_gate3_validity_stage_manifest",
        lambda *_args, **_kwargs: (validity_path, validity),
    )
    monkeypatch.setattr(runner, "build_adapter_gate3_backend", lambda **_kwargs: backend)

    def discover_checkpoints(*_args: Any, **_kwargs: Any):
        log.append(("discover_checkpoints", "all"))
        normal = tuple(
            sorted(
                state["checkpoints"].items(),
                key=lambda item: item[1].execution_position,
            )
        )
        return normal + tuple(state["checkpoint_competitors"])

    def publish_checkpoint(_output: Path, checkpoint: Any, **_kwargs: Any) -> Path:
        log.append(("publish_checkpoint", checkpoint.stream_id))
        path = tmp_path / "output" / f"checkpoint-{checkpoint.execution_position}.json"
        state["checkpoints"][path] = checkpoint
        return path

    monkeypatch.setattr(runner, "discover_gate3_central_cell_checkpoints", discover_checkpoints)
    monkeypatch.setattr(runner, "publish_gate3_central_cell_checkpoint", publish_checkpoint)
    monkeypatch.setattr(
        runner,
        "load_gate3_central_cell_checkpoint",
        lambda path, **_kwargs: state["checkpoints"][path],
    )

    def publish_sidecar(_output: Path, cell: _FakeCell, **_kwargs: Any):
        receipt = cell._receipt
        log.append(("publish_sidecar", receipt.stream_id))
        path = tmp_path / "output" / receipt.sidecar_name
        path.write_bytes(receipt.content_sha256.encode())
        state["sidecars"][receipt.sidecar_name] = path
        return path, receipt

    def load_sidecar(path: Path, *, receipt: Any, **_kwargs: Any) -> _FakeCell:
        assert state["sidecars"][receipt.sidecar_name] == path
        log.append(("load_sidecar", receipt.stream_id))
        return _FakeCell(receipt, freezes[receipt.corpus_id])

    monkeypatch.setattr(runner, "publish_gate3_central_cell_evidence", publish_sidecar)
    monkeypatch.setattr(runner, "load_gate3_central_cell_evidence", load_sidecar)
    monkeypatch.setattr(
        runner,
        "_discover_central_sidecars",
        lambda _output: tuple(sorted(state["sidecars"].values())),
    )

    def recover(path: Path, **kwargs: Any):
        receipt = next(item for item in receipts if item.sidecar_name == path.name)
        if kwargs["expected_regime"] != receipt.regime:
            raise ValueError("wrong synthetic regime")
        log.append(("recover", receipt.stream_id))
        return receipt

    monkeypatch.setattr(runner, "recover_gate3_central_cell_receipt", recover)

    def discover_summaries(*_args: Any, **_kwargs: Any):
        log.append(("discover_summaries", "all"))
        order = ("loco-2dics", "lectra-m3-m4")
        normal = tuple(
            (tmp_path / "output" / f"summary-{corpus}.json", state["summaries"][corpus])
            for corpus in order
            if corpus in state["summaries"]
        )
        return normal + tuple(state["summary_competitors"])

    def publish_summary(_output: Path, summary: Any, **_kwargs: Any) -> Path:
        log.append(("publish_summary", summary.corpus_id))
        state["summaries"][summary.corpus_id] = summary
        return tmp_path / "output" / f"summary-{summary.corpus_id}.json"

    monkeypatch.setattr(runner, "discover_gate3_economic_segment_summaries", discover_summaries)
    monkeypatch.setattr(runner, "publish_gate3_economic_segment_summary", publish_summary)

    def discover_manifest(*_args: Any, **_kwargs: Any):
        log.append(("discover_manifest", "terminal"))
        if state["manifest"] is None:
            return None
        return tmp_path / "output" / "manifest.json", state["manifest"]

    def publish_manifest(_output: Path, manifest: Any, **_kwargs: Any) -> Path:
        log.append(("publish_manifest", manifest.status))
        state["manifest"] = manifest
        return tmp_path / "output" / "manifest.json"

    monkeypatch.setattr(runner, "discover_gate3_economic_central_manifest", discover_manifest)
    monkeypatch.setattr(runner, "publish_gate3_economic_central_manifest", publish_manifest)
    return calibration, validity, addendum, backend, log, state


def _run(runner: ModuleType, tmp_path: Path):  # type: ignore[no-untyped-def]
    return runner.run_economic_central_stage(
        repository_root=tmp_path,
        gate1_artifact_path=tmp_path / "gate1.json",
        gate2_artifact_path=tmp_path / "gate2.json",
        gate3_config_path=tmp_path / "config.json",
        calibration_manifest_path=tmp_path
        / ("m11-economic-calibration-manifest-" + "a" * 64 + ".json"),
        validity_manifest_path=tmp_path / ("m11-economic-validity-stage-" + "b" * 64 + ".json"),
        output_directory=tmp_path / "output",
    )


def test_loco_causal_executes_exactly_twenty_and_stops_before_lectra(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _calibration, _validity, _addendum, _backend, log, _state = _install_fakes(
        monkeypatch, runner, tmp_path
    )

    outcome = _run(runner, tmp_path)

    assert outcome.manifest.status == "adverse_confirmation_required"
    assert outcome.manifest.next_actions == ("CONTINUE_ADVERSE_LOCO",)
    assert [value for name, value in log if name == "execute"] == [
        f"loco-confirmation-{index:02d}" for index in range(20)
    ]
    assert not any(value.startswith("lectra") for name, value in log if name == "execute")
    assert Counter(name for name, _ in log)["release"] == 20
    for index in range(20):
        stream = f"loco-confirmation-{index:02d}"
        ordered = [name for name, value in log if value == stream]
        assert ordered.index("publish_sidecar") < ordered.index("publish_checkpoint")
        assert ordered.index("publish_checkpoint") < ordered.index("release")


def test_both_red_executes_loco_then_lectra_and_resolves_scoped_value(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        loco_f="1.000000",
        loco_k="0.000000",
        lectra_f="1.000000",
        lectra_k="0.000000",
    )

    outcome = _run(runner, tmp_path)

    assert outcome.manifest.status == "insufficient_current_modeled_value"
    assert outcome.manifest.economic_value_resolved is True
    assert tuple(item.corpus_id for item in outcome.manifest.segment_summaries) == (
        "loco-2dics",
        "lectra-m3-m4",
    )


def test_partial_resume_and_orphan_recovery_skip_completed_execution(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _calibration, _validity, _addendum, _backend, log, _state = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        resumed_count=5,
        orphan_position=5,
    )

    _run(runner, tmp_path)

    executed = [value for name, value in log if name == "execute"]
    assert executed == [f"loco-confirmation-{index:02d}" for index in range(6, 20)]
    assert ("recover", "loco-confirmation-05") in log


def test_terminal_resume_fresh_reads_every_sidecar_without_execution_or_writes(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _calibration, _validity, _addendum, _backend, log, _state = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
    )
    first = _run(runner, tmp_path)
    log.clear()

    resumed = _run(runner, tmp_path)

    assert resumed.manifest == first.manifest
    counts = Counter(name for name, _value in log)
    assert counts["load_sidecar"] == 40
    assert counts["execute"] == 0
    assert counts["publish_sidecar"] == 0
    assert counts["publish_checkpoint"] == 0
    assert counts["publish_summary"] == 0
    assert counts["publish_manifest"] == 0


@pytest.mark.parametrize("competitor_kind", ("checkpoint", "summary"))
def test_terminal_resume_rejects_checkpoint_or_summary_inserted_during_sidecar_load(
    competitor_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _calibration, _validity, _addendum, _backend, _log, state = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
    )
    _run(runner, tmp_path)
    original_load = runner.load_gate3_central_cell_evidence
    inserted = False

    def insert_competitor(path: Path, **kwargs: Any):
        nonlocal inserted
        loaded = original_load(path, **kwargs)
        if not inserted:
            inserted = True
            if competitor_kind == "checkpoint":
                checkpoint = next(iter(state["checkpoints"].values()))
                state["checkpoint_competitors"].append(
                    (tmp_path / "output" / "late-checkpoint.json", checkpoint)
                )
            else:
                summary = state["summaries"]["loco-2dics"]
                state["summary_competitors"].append(
                    (tmp_path / "output" / "late-summary.json", summary)
                )
        return loaded

    monkeypatch.setattr(runner, "load_gate3_central_cell_evidence", insert_competitor)

    with pytest.raises(runner.M11EconomicCentralRunnerError, match="changed"):
        _run(runner, tmp_path)


def test_terminal_resume_rejects_sidecar_inserted_during_sidecar_load(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _calibration, _validity, _addendum, _backend, _log, state = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
    )
    _run(runner, tmp_path)
    original_load = runner.load_gate3_central_cell_evidence
    inserted = False
    load_count = 0

    def insert_sidecar(path: Path, **kwargs: Any):
        nonlocal inserted, load_count
        loaded = original_load(path, **kwargs)
        load_count += 1
        if not inserted and load_count == 21:
            inserted = True
            late = tmp_path / "output" / "late-sidecar.json.gz"
            late.write_bytes(b"late")
            state["sidecars"]["late-sidecar.json.gz"] = late
        return loaded

    monkeypatch.setattr(runner, "load_gate3_central_cell_evidence", insert_sidecar)

    with pytest.raises(runner.M11EconomicCentralRunnerError, match="competing"):
        _run(runner, tmp_path)


def test_terminal_resume_rejects_same_name_sidecar_replaced_during_later_load(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _calibration, _validity, _addendum, _backend, _log, state = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
    )
    _run(runner, tmp_path)
    target = next(iter(state["sidecars"].values()))
    original_load = runner.load_gate3_central_cell_evidence
    load_count = 0

    def replace_earlier_sidecar(path: Path, **kwargs: Any):
        nonlocal load_count
        loaded = original_load(path, **kwargs)
        load_count += 1
        if load_count == 22:
            replacement = tmp_path / "replacement-sidecar.json.gz"
            replacement.write_bytes(target.read_bytes())
            os.replace(replacement, target)
        return loaded

    monkeypatch.setattr(runner, "load_gate3_central_cell_evidence", replace_earlier_sidecar)

    with pytest.raises(runner.M11EconomicCentralRunnerError, match="changed"):
        _run(runner, tmp_path)


def test_competing_unbound_sidecars_abort_before_any_execution(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _calibration, _validity, _addendum, _backend, log, state = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
    )
    state["sidecars"]["foreign-a.json.gz"] = tmp_path / "output" / "foreign-a.json.gz"
    state["sidecars"]["foreign-b.json.gz"] = tmp_path / "output" / "foreign-b.json.gz"
    state["sidecars"]["foreign-a.json.gz"].write_bytes(b"foreign-a")
    state["sidecars"]["foreign-b.json.gz"].write_bytes(b"foreign-b")

    with pytest.raises(runner.M11EconomicCentralRunnerError, match="competing"):
        _run(runner, tmp_path)

    assert not any(name == "execute" for name, _value in log)


def test_invalid_validity_aborts_before_backend_or_economic_classification(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _install_fakes(monkeypatch, runner, tmp_path, validity_status="invalid")

    with pytest.raises(runner.M11EconomicCentralRunnerError, match="validity"):
        _run(runner, tmp_path)


def test_execution_failure_persists_bounded_chained_receipts_before_discard_and_rethrows(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    calibration, validity, addendum, backend, log, state = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        failure_position=3,
    )
    output = tmp_path / "output"
    failing_stream = "loco-confirmation-03"

    def execute_with_huge_failure(**kwargs: Any) -> _FakeCell:
        stream_id = kwargs["stream_id"]
        log.append(("execute", stream_id))
        if stream_id == failing_stream:
            raise RuntimeError("X" * 1_000_000)
        return _FakeCell(backend.receipts[stream_id], backend.freezes[kwargs["corpus_id"]])

    receipt_censuses_at_discard: list[tuple[Path, ...]] = []

    def require_receipt_before_discard(**kwargs: Any) -> None:
        receipts = tuple(sorted(output.glob("m11-economic-central-cell-failure-*.json")))
        receipt_censuses_at_discard.append(receipts)
        assert len(receipts) == (1, 1, 2)[len(receipt_censuses_at_discard) - 1]
        log.append(("discard", kwargs["stream_id"]))

    monkeypatch.setattr(backend, "execute_central_stream", execute_with_huge_failure)
    monkeypatch.setattr(
        backend,
        "discard_incomplete_central_stream_evidence",
        require_receipt_before_discard,
    )

    for _attempt in range(2):
        with pytest.raises(RuntimeError, match="X{1000}"):
            _run(runner, tmp_path)

    central = importlib.import_module("yieldforge.realistic_falsification.economic_central")
    discovered = central.discover_gate3_central_cell_failure_receipts(
        output,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    receipts = tuple(item for _path, item in discovered)
    checkpoints = tuple(
        checkpoint
        for _path, checkpoint in sorted(
            state["checkpoints"].items(),
            key=lambda item: item[1].execution_position,
        )
    )
    freeze = next(item for item in calibration.baseline_freezes if item.corpus_id == "loco-2dics")

    assert tuple(item.attempt_number for item in receipts) == (1, 2)
    assert tuple(item.execution_position for item in receipts) == (3, 3)
    assert tuple(item.stream_id for item in receipts) == (failing_stream, failing_stream)
    assert receipts[0].previous_failure_receipt_id is None
    assert receipts[0].previous_failure_receipt_content_sha256 is None
    assert receipts[1].previous_failure_receipt_id == receipts[0].failure_receipt_id
    assert receipts[1].previous_failure_receipt_content_sha256 == receipts[0].content_sha256
    assert all(item.roots == calibration.roots for item in receipts)
    assert all(item.calibration_manifest_id == calibration.manifest_id for item in receipts)
    assert all(item.validity_manifest_id == validity.manifest_id for item in receipts)
    assert all(item.decision_addendum_id == addendum.addendum_id for item in receipts)
    assert all(item.baseline_freeze_id == freeze.freeze_id for item in receipts)
    checkpoint_prefix_sha = "sha256:" + semantic_sha256(
        tuple((item.checkpoint_id, item.content_sha256) for item in checkpoints)
    )
    assert all(item.completed_checkpoint_count == len(checkpoints) for item in receipts)
    assert all(
        item.completed_checkpoint_prefix_content_sha256 == checkpoint_prefix_sha
        for item in receipts
    )
    assert all(item.status == "infrastructure_failure" for item in receipts)
    assert all(item.economic_value_resolved is False for item in receipts)
    assert all(len(item.failure_detail) == 1000 for item in receipts)
    assert all(path.stat().st_size < 16 * 1024 for path, _item in discovered)
    assert tuple(len(paths) for paths in receipt_censuses_at_discard) == (1, 1, 2)
    assert tuple(value for name, value in log if name == "discard") == (
        failing_stream,
        failing_stream,
        failing_stream,
    )
    assert state["manifest"] is None
    assert not tuple(output.glob("m11-economic-central-manifest-*.json"))

    tampered = bytearray(discovered[0][0].read_bytes())
    detail_offset = tampered.index(b"X")
    tampered[detail_offset] = ord("Y")
    discovered[0][0].write_bytes(tampered)
    with pytest.raises(central.Gate3EconomicCentralEvidenceError):
        central.discover_gate3_central_cell_failure_receipts(
            output,
            calibration_manifest=calibration,
            validity_manifest=validity,
            decision_addendum=addendum,
        )


@pytest.mark.parametrize(
    "failure_phase,error_match",
    (
        ("returned_cell_validation", "exact stream bindings"),
        ("sidecar_publication", "synthetic sidecar publication failure"),
        ("checkpoint_publication", "synthetic checkpoint publication failure"),
    ),
)
def test_precheckpoint_failure_records_before_exact_completed_cache_release(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
    failure_phase: str,
    error_match: str,
) -> None:
    _calibration, _validity, _addendum, backend, _log, state = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
    )
    failing_stream = "loco-confirmation-03"
    output = tmp_path / "output"
    original_execute = backend.execute_central_stream
    original_sidecar_publish = runner.publish_gate3_central_cell_evidence
    original_checkpoint_publish = runner.publish_gate3_central_cell_checkpoint
    original_release = backend.release_central_stream_evidence
    failed_stream_released = False

    if failure_phase == "returned_cell_validation":

        def invalid_cell(**kwargs: Any) -> _FakeCell:
            cell = original_execute(**kwargs)
            if kwargs["stream_id"] == failing_stream:
                cell.stream_id = "wrong-stream"
            return cell

        monkeypatch.setattr(backend, "execute_central_stream", invalid_cell)
    elif failure_phase == "sidecar_publication":

        def fail_sidecar(output_directory: Path, cell: _FakeCell, **kwargs: Any):
            if cell.stream_id == failing_stream:
                raise RuntimeError("synthetic sidecar publication failure")
            return original_sidecar_publish(output_directory, cell, **kwargs)

        monkeypatch.setattr(runner, "publish_gate3_central_cell_evidence", fail_sidecar)
    else:

        def fail_checkpoint(output_directory: Path, checkpoint: Any, **kwargs: Any):
            if checkpoint.stream_id == failing_stream:
                raise RuntimeError("synthetic checkpoint publication failure")
            return original_checkpoint_publish(output_directory, checkpoint, **kwargs)

        monkeypatch.setattr(runner, "publish_gate3_central_cell_checkpoint", fail_checkpoint)

    def require_receipt_before_release(**kwargs: Any) -> None:
        nonlocal failed_stream_released
        if kwargs["stream_id"] == failing_stream:
            assert len(tuple(output.glob("m11-economic-central-cell-failure-*.json"))) == 1
            failed_stream_released = True
        original_release(**kwargs)

    monkeypatch.setattr(backend, "release_central_stream_evidence", require_receipt_before_release)

    with pytest.raises(Exception, match=error_match):
        _run(runner, tmp_path)

    assert failed_stream_released is True
    assert state["manifest"] is None
    assert len(tuple(output.glob("m11-economic-central-cell-failure-*.json"))) == 1


def test_successful_retry_checkpoint_anchors_history_and_detects_later_deletion(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _calibration, _validity, _addendum, backend, _log, state = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        failure_position=3,
    )

    with pytest.raises(RuntimeError, match="synthetic central execution failure"):
        _run(runner, tmp_path)

    failure_path = next((tmp_path / "output").glob("m11-economic-central-cell-failure-*.json"))
    backend.failure_stream = None
    outcome = _run(runner, tmp_path)
    retry_checkpoint = outcome.checkpoints[3]

    assert retry_checkpoint.prior_failure_attempt_count == 1
    assert retry_checkpoint.prior_failure_receipt_id is not None
    assert retry_checkpoint.prior_failure_receipt_content_sha256 is not None
    assert state["manifest"] is not None

    failure_path.unlink()
    with pytest.raises(runner.M11EconomicCentralRunnerError, match="anchor"):
        _run(runner, tmp_path)


def test_terminal_resume_rejects_same_name_failure_receipt_replacement_between_phases(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _calibration, _validity, _addendum, backend, _log, _state = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        failure_position=3,
    )

    with pytest.raises(RuntimeError, match="synthetic central execution failure"):
        _run(runner, tmp_path)
    backend.failure_stream = None
    _run(runner, tmp_path)

    failure_path = next((tmp_path / "output").glob("m11-economic-central-cell-failure-*.json"))
    replacement = tmp_path / "same-bytes-replacement.json"
    replacement.write_bytes(failure_path.read_bytes())
    original_load = runner._load_cell_sidecar
    replaced = False

    def replace_during_later_sidecar_load(*args: Any, **kwargs: Any):
        nonlocal replaced
        if not replaced:
            replaced = True
            os.replace(replacement, failure_path)
        return original_load(*args, **kwargs)

    monkeypatch.setattr(runner, "_load_cell_sidecar", replace_during_later_sidecar_load)

    with pytest.raises(runner.M11EconomicCentralRunnerError, match="failure.*changed"):
        _run(runner, tmp_path)


def test_orphan_success_recovery_preserves_prior_failure_history_anchor(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    _calibration, _validity, _addendum, backend, _log, state = _install_fakes(
        monkeypatch,
        runner,
        tmp_path,
        failure_position=3,
    )

    with pytest.raises(RuntimeError, match="synthetic central execution failure"):
        _run(runner, tmp_path)

    receipt = backend.receipts["loco-confirmation-03"]
    orphan_path = tmp_path / "output" / receipt.sidecar_name
    orphan_path.write_bytes(receipt.content_sha256.encode())
    state["sidecars"][receipt.sidecar_name] = orphan_path
    backend.failure_stream = None

    outcome = _run(runner, tmp_path)
    recovered = outcome.checkpoints[3]

    assert recovered.stream_id == "loco-confirmation-03"
    assert recovered.prior_failure_attempt_count == 1
    assert recovered.prior_failure_receipt_id is not None
    assert recovered.prior_failure_receipt_content_sha256 is not None


def test_main_prints_only_terminal_status_and_next_action(
    monkeypatch: pytest.MonkeyPatch,
    runner: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = SimpleNamespace(
        manifest_id="yfm11econcentral-" + "1" * 24,
        content_sha256="sha256:" + "1" * 64,
        status="adverse_confirmation_required",
        global_disposition="CONTINUE_ADVERSE_SEGMENT_CONFIRMATION",
        next_actions=("CONTINUE_ADVERSE_LOCO",),
        economic_value_resolved=False,
    )
    monkeypatch.setattr(
        runner,
        "run_economic_central_stage",
        lambda **_kwargs: SimpleNamespace(manifest=manifest, manifest_path=Path("manifest.json")),
    )

    assert (
        runner.main(
            [
                "--repository-root",
                "/tmp",
                "--calibration-manifest",
                "calibration.json",
                "--validity-manifest",
                "validity.json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "economic_value_resolved": False,
        "global_disposition": "CONTINUE_ADVERSE_SEGMENT_CONFIRMATION",
        "manifest_content_sha256": "sha256:" + "1" * 64,
        "manifest_id": "yfm11econcentral-" + "1" * 24,
        "manifest_path": "manifest.json",
        "next_actions": ["CONTINUE_ADVERSE_LOCO"],
        "status": "adverse_confirmation_required",
    }
