from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from yieldforge.temporal_benchmark.contracts import TemporalRegime


def _bindings():  # type: ignore[no-untyped-def]
    from yieldforge.oracle.experiment import M8AuditActionBinding

    classifications = (
        "exact_transition",
        "no_fit",
        "policy_dominated",
        "state_rejoin",
    )
    bindings = []
    for offset, regime in enumerate(TemporalRegime):
        stream_id = "yfts-" + f"{offset + 1:x}" * 24
        for action_offset, (kind, classification) in enumerate(
            zip(
                ("standard", "remnant", "standard", "remnant"),
                classifications,
                strict=True,
            )
        ):
            bindings.append(
                M8AuditActionBinding(
                    regime=regime,
                    temporal_seed=2026082300 + offset,
                    stream_id=stream_id,
                    future_event_count=1,
                    semantic_runtime_sha256="sha256:" + f"{offset + 1:x}" * 64,
                    catalog_action_id=f"m7-{kind}:audit-{offset}-{action_offset}",
                    action_kind=kind,
                    proof_id="yfm8ap-" + f"{offset + action_offset + 1:x}" * 24,
                    witness_classifications=(classification,),
                )
            )
    return tuple(bindings)


def _cells(*, bindings=None):  # type: ignore[no-untyped-def]
    from yieldforge.oracle.experiment import M8CertificateProofCell, audit_sample_sha256

    selected = _bindings() if bindings is None else bindings
    cells = []
    for offset, regime in enumerate(TemporalRegime):
        cell_bindings = tuple(
            sorted(
                (item for item in selected if item.regime is regime),
                key=lambda item: item.catalog_action_id,
            )
        )
        current_action_ids = tuple(
            sorted(
                (
                    *(item.catalog_action_id for item in cell_bindings),
                    *(
                        f"m7-standard:filler-{offset}-{index:03}"
                        for index in range(100 - len(cell_bindings))
                    ),
                )
            )
        )
        cells.append(
            M8CertificateProofCell(
                regime=regime,
                temporal_seed=2026082300 + offset,
                stream_id="yfts-" + f"{offset + 1:x}" * 24,
                prefix_event_count=2,
                future_event_count=1,
                semantic_runtime_sha256="sha256:" + f"{offset + 1:x}" * 64,
                audit_action_ids=tuple(item.catalog_action_id for item in cell_bindings),
                audit_sample_sha256=audit_sample_sha256(cell_bindings),
                current_action_kinds=("remnant", "standard"),
                current_action_ids=current_action_ids,
                proof_catalog_action_ids=current_action_ids,
                current_action_count=100,
                checked_action_count=100,
                valid_proof_count=100,
                checker_failure_count=0,
                audit_mismatch_count=0,
                witness_classifications=(
                    "exact_transition",
                    "no_fit",
                    "policy_dominated",
                    "state_rejoin",
                ),
                certified_event_count=20,
                exact_escape_count=10,
                state_rejoin_count=10,
                certificate_elapsed_seconds=0.25,
                checker_elapsed_seconds=0.25,
                sampled_certificate_elapsed_seconds=0.01,
                sampled_checker_elapsed_seconds=0.01,
                sampled_checker_failure_count=0,
                sampled_reference_elapsed_seconds=0.5,
            )
        )
    return tuple(cells)


def _finalize(*, cells=None, bindings=None):  # type: ignore[no-untyped-def]
    from yieldforge.oracle.experiment import finalize_certificate_proof

    selected = _bindings() if bindings is None else bindings
    return finalize_certificate_proof(
        m0_contract_id="yfm0-" + "1" * 24,
        m0_contract_sha256="sha256:" + "2" * 64,
        m6_contract_id="yfm6-" + "3" * 24,
        m6_contract_sha256="sha256:" + "4" * 64,
        m6_population_id="yftp-" + "5" * 24,
        m6_population_sha256="sha256:" + "6" * 64,
        problem_index_id="yfm7i-" + "7" * 24,
        problem_index_sha256="sha256:" + "8" * 64,
        freeze_id="yfm7freeze-" + "9" * 24,
        freeze_sha256="sha256:" + "a" * 64,
        calibration_view_id="yfm7cv-" + "b" * 24,
        calibration_view_sha256="sha256:" + "c" * 64,
        cells=_cells(bindings=selected) if cells is None else cells,
        audit_bindings=selected,
    )


def test_certificate_gate_passes_only_complete_exact_fast_result() -> None:
    result = _finalize()

    assert result.schema_version == "yieldforge.m8-certificate-proof.v2"
    assert result.checked_action_count == result.current_action_count
    assert result.valid_proof_count == result.current_action_count
    assert result.audit_mismatch_count == 0
    assert result.checker_failure_count == 0
    assert result.sampled_checker_failure_count == 0
    assert result.sampled_speedup == 25.0
    assert result.missing_full_run_witness_classifications == ()
    assert result.missing_audit_witness_classifications == ()
    assert result.certified_event_count > 0
    assert result.exact_escape_count > 0
    assert result.state_rejoin_count > 0
    assert result.projected_held_out_calendar_days <= 7.0
    assert result.configured_worker_count == 8
    assert result.measured_process_count == 1
    assert result.evaluation_partition_opened is False
    assert result.technical_decision == "pass_certificate_exact"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("checker_failure_count", 1),
        ("audit_mismatch_count", 1),
        ("sampled_checker_failure_count", 1),
    ],
)
def test_certificate_gate_redesigns_for_each_exactness_failure(
    field: str,
    value: int,
) -> None:
    cells = _cells()
    update = {field: value}
    if field == "checker_failure_count":
        update["valid_proof_count"] = 99
    cells = (cells[0].model_copy(update=update), *cells[1:])

    assert _finalize(cells=cells).technical_decision == "redesign_certificate_proof"


def test_certificate_gate_redesigns_for_slow_matched_sample() -> None:
    cells = tuple(
        item.model_copy(update={"sampled_certificate_elapsed_seconds": 0.04})
        for item in _cells()
    )

    result = _finalize(cells=cells)

    assert result.sampled_speedup < 20.0
    assert result.technical_decision == "redesign_certificate_proof"


def test_certificate_gate_redesigns_when_present_witness_stratum_is_not_audited() -> None:
    bindings = tuple(
        item
        for item in _bindings()
        if not (
            item.regime is TemporalRegime.NO_SIGNAL
            and item.witness_classifications == ("state_rejoin",)
        )
    )

    result = _finalize(bindings=bindings)

    assert result.uncovered_witness_classifications == ("state_rejoin",)
    assert result.technical_decision == "redesign_certificate_proof"


def test_certificate_gate_rejects_exact_only_full_run_and_audit() -> None:
    bindings = tuple(
        item.model_copy(update={"witness_classifications": ("exact_transition",)})
        for item in _bindings()
    )
    cells = tuple(
        item.model_copy(
            update={
                "witness_classifications": ("exact_transition",),
                "certified_event_count": 0,
                "state_rejoin_count": 0,
            }
        )
        for item in _cells(bindings=bindings)
    )

    result = _finalize(cells=cells, bindings=bindings)

    assert result.missing_full_run_witness_classifications == (
        "no_fit",
        "policy_dominated",
        "state_rejoin",
    )
    assert result.missing_audit_witness_classifications == (
        "no_fit",
        "policy_dominated",
        "state_rejoin",
    )
    assert result.technical_decision == "redesign_certificate_proof"


def test_certificate_gate_rejects_exact_only_frozen_audit_boundary() -> None:
    bindings = tuple(
        item.model_copy(update={"witness_classifications": ("exact_transition",)})
        for item in _bindings()
    )

    result = _finalize(bindings=bindings)

    assert result.missing_full_run_witness_classifications == ()
    assert result.missing_audit_witness_classifications == (
        "no_fit",
        "policy_dominated",
        "state_rejoin",
    )
    assert result.technical_decision == "redesign_certificate_proof"


def test_certificate_gate_rejects_no_positive_state_rejoin_evidence() -> None:
    cells = tuple(
        item.model_copy(update={"state_rejoin_count": 0}) for item in _cells()
    )

    result = _finalize(cells=cells)

    assert result.state_rejoin_count == 0
    assert result.technical_decision == "redesign_certificate_proof"


def test_certificate_gate_rejects_no_positive_exact_escape_evidence() -> None:
    cells = tuple(
        item.model_copy(update={"exact_escape_count": 0}) for item in _cells()
    )

    result = _finalize(cells=cells)

    assert result.exact_escape_count == 0
    assert result.technical_decision == "redesign_certificate_proof"


def test_certificate_gate_rejects_no_positive_passive_certificate_evidence() -> None:
    cells = tuple(
        item.model_copy(update={"certified_event_count": 0}) for item in _cells()
    )

    result = _finalize(cells=cells)

    assert result.certified_event_count == 0
    assert result.technical_decision == "redesign_certificate_proof"


def test_certificate_gate_requires_distributed_only_for_projection() -> None:
    cells = tuple(
        item.model_copy(
            update={
                "certificate_elapsed_seconds": 50.0,
            }
        )
        for item in _cells()
    )

    result = _finalize(cells=cells)

    assert result.sampled_speedup >= 20.0
    assert result.projected_held_out_calendar_days > 7.0
    assert result.technical_decision == "require_distributed_exact"


@pytest.mark.parametrize(
    "update",
    [
        {"checked_action_count": 99},
        {"valid_proof_count": 99},
        {"valid_proof_count": 99, "checker_failure_count": 2},
    ],
)
def test_certificate_cell_rejects_nonlocal_proof_count_reconciliation(
    update: dict[str, int],
) -> None:
    cell = _cells()[0]

    with pytest.raises(ValidationError, match="cell proof counts do not reconcile"):
        type(cell).model_validate(
            {**cell.model_dump(mode="python"), **update}, strict=True
        )


def test_certificate_cell_rejects_incomplete_proof_action_ids() -> None:
    cell = _cells()[0]

    with pytest.raises(ValidationError, match="proof action IDs"):
        type(cell).model_validate(
            {
                **cell.model_dump(mode="python"),
                "proof_catalog_action_ids": cell.proof_catalog_action_ids[:-1],
            },
            strict=True,
        )


def test_projection_uses_observed_single_process_time_without_worker_division() -> None:
    result = _finalize()
    observed_action_events = sum(
        item.current_action_count * item.future_event_count for item in result.cells
    )
    expected_seconds = (
        result.certificate_pipeline_elapsed_seconds
        / observed_action_events
        * result.held_out_action_count
        * 11.5
        * 2.0
    )

    assert result.projected_held_out_calendar_days == round(
        expected_seconds / 86_400.0, 6
    )


def test_certificate_gate_runtime_does_not_install_an_unmeasured_executor() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment

    source = two_problem_runtime(first_width=4.0, second_width=4.0)
    measured = experiment._runtime(  # noqa: SLF001
        source.replay_input,
        source.runtime_candidates,
        source.rules,
        source.jagua_executable,
    )

    assert measured.standard_profile_executor is None


def test_measured_proof_phase_suspends_gc_and_includes_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment

    events = []
    times = iter((10.0, 13.5))

    monkeypatch.setattr(experiment.gc, "isenabled", lambda: True)
    monkeypatch.setattr(experiment.gc, "collect", lambda: events.append("collect"))
    monkeypatch.setattr(experiment.gc, "disable", lambda: events.append("disable"))
    monkeypatch.setattr(experiment.gc, "enable", lambda: events.append("enable"))

    def clock() -> float:
        events.append("clock")
        return next(times)

    monkeypatch.setattr(experiment, "perf_counter", clock)
    result, elapsed = experiment._measure_proof_phase(  # noqa: SLF001
        lambda: events.append("operation") or "result"
    )

    assert result == "result"
    assert elapsed == 3.5
    assert events == [
        "collect",
        "disable",
        "clock",
        "operation",
        "enable",
        "collect",
        "clock",
    ]


def test_measured_proof_phase_preserves_prior_gc_disable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment

    events = []
    monkeypatch.setattr(experiment.gc, "isenabled", lambda: False)
    monkeypatch.setattr(experiment.gc, "collect", lambda: events.append("collect"))
    monkeypatch.setattr(experiment.gc, "disable", lambda: events.append("disable"))
    monkeypatch.setattr(experiment.gc, "enable", lambda: events.append("enable"))
    monkeypatch.setattr(experiment, "perf_counter", lambda: 1.0)

    def fail() -> None:
        events.append("operation")
        raise RuntimeError("forced proof failure")

    with pytest.raises(RuntimeError, match="forced proof failure"):
        experiment._measure_proof_phase(fail)  # noqa: SLF001

    assert events == ["operation"]


def test_measured_proof_phase_restores_gc_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment

    events = []
    monkeypatch.setattr(experiment.gc, "isenabled", lambda: True)
    monkeypatch.setattr(experiment.gc, "collect", lambda: events.append("collect"))
    monkeypatch.setattr(experiment.gc, "disable", lambda: events.append("disable"))
    monkeypatch.setattr(experiment.gc, "enable", lambda: events.append("enable"))
    monkeypatch.setattr(experiment, "perf_counter", lambda: 1.0)

    def fail() -> None:
        events.append("operation")
        raise RuntimeError("forced proof failure")

    with pytest.raises(RuntimeError, match="forced proof failure"):
        experiment._measure_proof_phase(fail)  # noqa: SLF001

    assert events == ["collect", "disable", "operation", "enable", "collect"]


def test_timed_cell_reuses_preflight_full_batch_and_checks_it_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle import experiment
    from yieldforge.oracle.reference import M8OracleRequest
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    preflight = experiment.score_sparse_event(request)
    audit = experiment._freeze_audit_bindings(  # noqa: SLF001
        experiment._audit_candidates_for_cell(  # noqa: SLF001
            runtime.replay_input.instances,
            preflight,
        )
    )
    counts = {"full": 0, "sample": 0, "reference": 0, "checker": []}
    original_sample = experiment.score_certificate_actions
    original_checker = experiment.check_action_proofs
    original_reference = experiment.score_reference_actions

    def counted_full(request):  # type: ignore[no-untyped-def]
        counts["full"] += 1
        return preflight

    def counted_sample(request, *, action_ids):  # type: ignore[no-untyped-def]
        counts["sample"] += 1
        return original_sample(request, action_ids=action_ids)

    def counted_checker(request, proofs):  # type: ignore[no-untyped-def]
        counts["checker"].append(len(proofs))
        return original_checker(request, proofs)

    def counted_reference(request, *, action_ids):  # type: ignore[no-untyped-def]
        counts["reference"] += 1
        assert action_ids == tuple(item.catalog_action_id for item in audit)
        return original_reference(request, action_ids=action_ids)

    monkeypatch.setattr(experiment, "score_sparse_event", counted_full)
    monkeypatch.setattr(experiment, "score_certificate_actions", counted_sample)
    monkeypatch.setattr(experiment, "check_action_proofs", counted_checker)
    monkeypatch.setattr(experiment, "score_reference_actions", counted_reference)
    monkeypatch.setattr(experiment, "_request_for_cell", lambda *args, **kwargs: request)
    cell = SimpleNamespace(stream=runtime.replay_input.instances)
    progress: list[str] = []

    result = experiment._execute_timed_cell(  # noqa: SLF001
        cell,
        rules=None,
        jagua_executable=tmp_path / "unused",
        audit_bindings=audit,
        full_sparse=preflight,
        full_certificate_seconds=0.125,
        progress=progress.append,
    )

    assert counts == {
        "full": 0,
        "sample": 1,
        "reference": 1,
        "checker": [result.current_action_count, len(audit)],
    }
    assert result.certificate_elapsed_seconds == 0.125
    assert result.sampled_certificate_elapsed_seconds > 0
    assert result.sampled_checker_elapsed_seconds > 0
    assert [item.split()[0] for item in progress] == [
        "phase_start",
        "phase_complete",
        "phase_start",
        "phase_complete",
        "phase_start",
        "phase_complete",
        "phase_start",
        "phase_complete",
    ]


def test_audit_freeze_is_independent_of_preflight_elapsed_time() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle import experiment
    from yieldforge.oracle.reference import M8OracleRequest
    from yieldforge.oracle.visibility import FullRealizedVisibility

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    request = M8OracleRequest(
        runtime=runtime,
        cursor=initial_m7_cursor(runtime.replay_input),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    sparse = experiment.score_sparse_event(request)
    cell = SimpleNamespace(stream=runtime.replay_input.instances)
    fast = experiment._SparsePreflightResult(  # noqa: SLF001
        cell=cell,
        sparse=sparse,
        elapsed_seconds=0.001,
    )
    slow = experiment._SparsePreflightResult(  # noqa: SLF001
        cell=cell,
        sparse=sparse,
        elapsed_seconds=100_000.0,
    )

    assert experiment._freeze_preflight_audit((fast,)) == (  # noqa: SLF001
        experiment._freeze_preflight_audit((slow,))  # noqa: SLF001
    )


def test_certificate_result_rejects_missing_registered_regime() -> None:
    with pytest.raises(ValueError, match="all six registered cells"):
        _finalize(cells=_cells()[:-1])


def test_audit_sample_identity_is_order_independent_and_semantically_bound() -> None:
    from yieldforge.oracle.experiment import audit_sample_sha256

    bindings = _bindings()
    assert audit_sample_sha256(bindings) == audit_sample_sha256(tuple(reversed(bindings)))
    mutated = (
        bindings[0].model_copy(update={"catalog_action_id": "m7-standard:changed"}),
        *bindings[1:],
    )
    assert audit_sample_sha256(bindings) != audit_sample_sha256(mutated)


def test_certificate_artifact_strictly_reloads_and_refuses_conflicting_content(
    tmp_path: Path,
) -> None:
    from yieldforge.oracle.experiment import (
        M8CertificateProofResult,
        publish_sparse_proof,
    )

    result = _finalize()
    path = publish_sparse_proof(tmp_path, result)
    assert path.name == f"m8-certificate-proof-{result.proof_id}.json"
    assert M8CertificateProofResult.model_validate_json(
        path.read_bytes(), strict=True
    ) == result
    assert publish_sparse_proof(tmp_path, result) == path

    path.write_text("{}\n")
    with pytest.raises(ValueError, match="immutable and differs"):
        publish_sparse_proof(tmp_path, result)
