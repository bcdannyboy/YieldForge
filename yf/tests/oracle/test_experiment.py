from __future__ import annotations

import inspect
import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from yieldforge.temporal_benchmark.contracts import TemporalRegime


def _phase_names_from_report(report) -> set[str]:  # type: ignore[no-untyped-def]
    names: set[str] = set()

    def visit(phases) -> None:  # type: ignore[no-untyped-def]
        for phase in phases:
            names.add(phase.name)
            visit(phase.children)

    visit(report.phases)
    return names


def _phase_occurrence_count(report, name: str) -> int:  # type: ignore[no-untyped-def]
    count = 0

    def visit(phases) -> None:  # type: ignore[no-untyped-def]
        nonlocal count
        for phase in phases:
            count += phase.name == name
            visit(phase.children)

    visit(report.phases)
    return count


def _phase_test_operation(mode: str, value: int):  # type: ignore[no-untyped-def]
    if mode == "fail":
        raise RuntimeError("forced phase failure")
    if mode == "sleep":
        time.sleep(value)
    return value, os.getpid()


def _phase_spawn_descendant(pid_path: str) -> None:
    child = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    Path(pid_path).write_text(str(child.pid))
    time.sleep(30)


def _phase_leave_descendant_after_success(pid_path: str) -> int:
    child = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    Path(pid_path).write_text(str(child.pid))
    return os.getpid()


def _phase_fail_nested_audit_with_descendant(
    pid_path: str,
    audit_processes: int,
) -> None:
    from yieldforge.oracle.concurrency import (
        activate_m8_translation_audit_processes,
        require_m8_translation_audit_processes,
    )

    with activate_m8_translation_audit_processes(audit_processes):
        if require_m8_translation_audit_processes() != audit_processes:
            raise AssertionError("synthetic nested audit width differs")
        child = subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", "import time; time.sleep(30)"],
        )
        Path(pid_path).write_text(str(child.pid))
        raise RuntimeError("synthetic nested audit failure")


def _phase_record_pid(pid_path: str) -> int:
    Path(pid_path).write_text(str(os.getpid()))
    return os.getpid()


def _phase_mark_complete(marker_path: str) -> int:
    time.sleep(0.05)
    Path(marker_path).write_text("complete")
    return os.getpid()


def _phase_delayed_failure(delay_seconds: float) -> None:
    time.sleep(delay_seconds)
    raise RuntimeError("delayed phase failure")


def _phase_return_then_exit_nonzero() -> str:
    timer = threading.Timer(0.05, lambda: os._exit(7))
    timer.daemon = False
    timer.start()
    return "valid-payload"


def _phase_force_portable_checker_exception(*args):  # type: ignore[no-untyped-def]
    from yieldforge.oracle import experiment

    def forced_exception(_request):  # type: ignore[no-untyped-def]
        raise RuntimeError("forced Task6 checker exception")

    experiment.check_m8_fact_bundle = forced_exception
    return experiment._check_portable_fact_bundle_worker(*args)  # noqa: SLF001


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


def _finalize(  # type: ignore[no-untyped-def]
    *,
    cells=None,
    bindings=None,
    generator_wall_seconds=0.2,
    checker_wall_seconds=0.3,
    audit_wall_seconds=0.4,
    total_wall_seconds=1.0,
):
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
        measured_process_count=6,
        generator_wall_seconds=generator_wall_seconds,
        checker_wall_seconds=checker_wall_seconds,
        audit_wall_seconds=audit_wall_seconds,
        total_wall_seconds=total_wall_seconds,
    )


def _portable_gate3_result():  # type: ignore[no-untyped-def]
    from yieldforge.oracle import experiment

    registry = experiment.M8PortableRegistryEvidence()

    def cell(regime: TemporalRegime, digit: str, roots: int, byte_count: int):  # type: ignore[no-untyped-def]
        timing = experiment.M8PortableFactPhaseTiming(
            first_generation_worker_wall_seconds=0.4,
            second_generation_worker_wall_seconds=0.5,
            producer_bundle_serialization_wall_seconds=0.1,
            producer_handoff_serialization_wall_seconds=0.1,
            metadata_reconciliation_wall_seconds=0.05,
            authority_reconstruction_wall_seconds=0.05,
            checker_worker_wall_seconds=1.0,
            checker_strict_load_inclusive_wall_seconds=0.1,
            common_verification_inclusive_wall_seconds=0.2,
            action_traversal_inclusive_wall_seconds=0.3,
            exact_fallback_nested_exclusive_wall_seconds=0.0,
            capability_cleanup_inclusive_wall_seconds=0.1,
        )
        fixed = 1 + 1 + 1 + 1 + 2 + 3 + roots
        return experiment.M8PortableFactGate3Cell(
            regime=regime,
            temporal_seed=2026082300,
            stream_id="yfts-" + digit * 24,
            event_count=2,
            first_bundle_sha256="sha256:" + digit * 64,
            second_bundle_sha256="sha256:" + digit * 64,
            first_semantic_bundle_bytes_sha256="sha256:" + digit * 64,
            second_semantic_bundle_bytes_sha256="sha256:" + digit * 64,
            semantic_serialized_bytes=byte_count,
            repeated_semantic_serialized_bytes=byte_count,
            fixed_layer_node_count=fixed,
            translation_batch_count=1,
            candidate_scalar_fact_count=1,
            frontier_fact_count=1,
            standard_candidate_fact_count=1,
            common_lemma_count=2,
            influence_fact_count=3,
            generated_action_root_count=roots,
            checked_common_lemma_count=2,
            checked_influence_fact_count=3,
            checked_action_root_count=roots,
            decision_id="yfm8d-" + digit * 24,
            decision_content_sha256="sha256:" + digit * 64,
            producer_counted_inventory_evidence_row_count=3,
            producer_counted_search_lemma_count=2,
            counted_translation_audit_count=2,
            counted_translation_audit_call_count=2,
            influence_translation_audit_count=3,
            common_exact_fallback_wall_seconds=0.0,
            influence_exact_fallback_wall_seconds=0.0,
            total_exact_fallback_wall_seconds=0.0,
            timing=timing,
            first_generator_registry_state=registry,
            second_generator_registry_state=registry,
            checker_registry_state=registry,
        )

    cells = (
        cell(TemporalRegime.NO_SIGNAL, "1", 428, 1000),
        cell(TemporalRegime.REGIME_SHIFT, "2", 459, 1100),
    )
    payload = {
        "m0_contract_id": "yfm0-" + "1" * 24,
        "m0_contract_sha256": "sha256:" + "1" * 64,
        "m6_contract_id": "yfm6-" + "2" * 24,
        "m6_contract_sha256": "sha256:" + "2" * 64,
        "m6_population_id": "yftp-" + "3" * 24,
        "m6_population_sha256": "sha256:" + "3" * 64,
        "problem_index_id": "yfm7i-" + "4" * 24,
        "problem_index_sha256": "sha256:" + "4" * 64,
        "freeze_id": "yfm7freeze-" + "5" * 24,
        "freeze_sha256": "sha256:" + "5" * 64,
        "calibration_view_id": "yfm7cv-" + "6" * 24,
        "calibration_view_sha256": "sha256:" + "6" * 64,
        "cells": cells,
        "semantic_serialized_bytes": 2100,
        "fixed_layer_node_count": sum(item.fixed_layer_node_count for item in cells),
        "generated_action_root_count": 887,
        "checked_action_root_count": 887,
        "total_exact_fallback_count": 0,
        "total_exact_fallback_wall_seconds": 0.0,
        "first_generation_phase_wall_seconds": 1.0,
        "second_generation_phase_wall_seconds": 1.0,
        "checker_phase_wall_seconds": 1.5,
        "task_serialization_wall_seconds": 0.2,
        "result_serialization_wall_seconds": 0.2,
        "inbound_payload_handoff_wall_seconds": 0.1,
        "outbound_payload_handoff_wall_seconds": 0.2,
        "worker_payload_handoff_wall_seconds": 0.3,
        "process_exit_validation_wall_seconds": 0.1,
        "worker_task_payload_bytes": 300,
        "worker_result_payload_bytes": 400,
        "retained_first_generation_bundle_bytes": 2100,
        "checker_task_payload_bytes": 300,
        "total_pipeline_wall_seconds": 4.0,
        "controller_registry_state_before": registry,
        "controller_registry_state_after": registry,
    }
    draft = experiment.M8PortableFactGate3Result.model_construct(
        gate3_id="yfm8gate3-" + "0" * 24,
        content_sha256="sha256:" + "0" * 64,
        **payload,
    )
    digest = experiment.semantic_sha256(
        draft,
        excluded_fields={"gate3_id", "content_sha256"},
    )
    return experiment.M8PortableFactGate3Result(
        gate3_id=f"yfm8gate3-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **payload,
    )


def test_certificate_gate_passes_only_complete_exact_fast_result() -> None:
    result = _finalize()

    assert result.schema_version == "yieldforge.m8-certificate-proof.v3"
    assert result.execution_mode == "distributed_exact"
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
    assert result.measured_process_count == 6
    assert result.generator_wall_seconds == 0.2
    assert result.checker_wall_seconds == 0.3
    assert result.certificate_pipeline_wall_seconds == 0.5
    assert result.audit_wall_seconds == 0.4
    assert result.total_wall_seconds == 1.0
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


def test_distributed_gate_requires_action_sharding_only_for_projection() -> None:
    result = _finalize(
        generator_wall_seconds=29.0,
        checker_wall_seconds=1.0,
        total_wall_seconds=31.0,
    )

    assert result.sampled_speedup >= 20.0
    assert result.projected_held_out_calendar_days > 7.0
    assert result.technical_decision == "require_action_sharding"


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


def test_projection_uses_observed_distributed_wall_time_without_worker_division() -> None:
    result = _finalize()
    observed_action_events = sum(
        item.current_action_count * item.future_event_count for item in result.cells
    )
    expected_seconds = (
        result.certificate_pipeline_wall_seconds
        / observed_action_events
        * result.held_out_action_count
        * 11.5
        * 2.0
    )

    assert result.projected_held_out_calendar_days == round(
        expected_seconds / 86_400.0, 6
    )


def test_distributed_result_rejects_inconsistent_wall_time() -> None:
    result = _finalize()

    with pytest.raises(ValidationError, match="pipeline wall time"):
        type(result).model_validate(
            {
                **result.model_dump(mode="python"),
                "certificate_pipeline_wall_seconds": 0.6,
            },
            strict=True,
        )


def test_distributed_result_rejects_unmeasured_worker_claim() -> None:
    result = _finalize()

    with pytest.raises(ValidationError):
        type(result).model_validate(
            {
                **result.model_dump(mode="python"),
                "measured_process_count": 8,
            },
            strict=True,
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


def test_distributed_phase_reassembly_is_regime_ordered_and_complete() -> None:
    from yieldforge.oracle import experiment

    results = tuple(
        SimpleNamespace(regime=regime, value=regime.value)
        for regime in reversed(tuple(TemporalRegime))
    )

    ordered = experiment._order_worker_results(results)  # noqa: SLF001

    assert tuple(item.regime for item in ordered) == tuple(TemporalRegime)
    with pytest.raises(ValueError, match="exactly one result"):
        experiment._order_worker_results((*results[:-1], results[0]))  # noqa: SLF001


def test_distributed_phase_preserves_input_order_across_processes() -> None:
    from yieldforge.oracle import experiment

    results = experiment._run_process_phase(  # noqa: SLF001
        _phase_test_operation,
        (("return", 1), ("return", 2)),
        process_count=6,
        timeout_seconds=5.0,
    )

    assert tuple(value for value, _pid in results) == (1, 2)
    assert all(pid != os.getpid() for _value, pid in results)


def test_distributed_phase_can_report_explicit_worker_payload_handoff_timing() -> None:
    from yieldforge.oracle import experiment

    execution = experiment._run_process_phase(  # noqa: SLF001
        _phase_test_operation,
        (("return", 1), ("return", 2)),
        process_count=2,
        timeout_seconds=5.0,
        report_payload_handoff=True,
    )

    assert tuple(value for value, _pid in execution.results) == (1, 2)
    assert execution.inbound_payload_handoff_wall_seconds >= 0.0
    assert execution.outbound_payload_handoff_wall_seconds >= 0.0
    assert execution.worker_payload_handoff_wall_seconds == (
        execution.inbound_payload_handoff_wall_seconds
        + execution.outbound_payload_handoff_wall_seconds
    )


def test_distributed_phase_times_out_stalled_payload_handoff_and_joins_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment

    entered = threading.Event()

    def stall_until_closed(connection, payload):  # type: ignore[no-untyped-def]
        del payload
        entered.set()
        while not connection.closed:
            time.sleep(0.01)

    monkeypatch.setattr(experiment, "_send_connection_bytes", stall_until_closed)
    with pytest.raises(TimeoutError, match="exceeded"):
        experiment._run_process_phase(  # noqa: SLF001
            _phase_test_operation,
            (("return", 1),),
            process_count=1,
            timeout_seconds=1.0,
        )
    assert entered.is_set()
    assert not any(
        thread.name.startswith("m8-payload-sender-") and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_distributed_phase_times_out_stalled_result_receive_and_joins_receiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment

    entered = threading.Event()

    def stall_until_closed(connection, byte_cap, result_box):  # type: ignore[no-untyped-def]
        del byte_cap, result_box
        entered.set()
        while not connection.closed:
            time.sleep(0.01)

    monkeypatch.setattr(experiment, "_receive_connection_bytes", stall_until_closed)
    with pytest.raises(TimeoutError, match="result handoff"):
        experiment._run_process_phase(  # noqa: SLF001
            _phase_test_operation,
            (("return", 1),),
            process_count=1,
            timeout_seconds=1.0,
        )
    assert entered.is_set()
    assert not any(
        thread.name.startswith("m8-payload-receiver-") and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_distributed_phase_rejects_a_ready_handshake_after_startup_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment

    real_wait = experiment.wait
    delayed = False

    def delay_first_ready(connections, timeout):  # type: ignore[no-untyped-def]
        nonlocal delayed
        ready = real_wait(connections, timeout=timeout)
        if ready and not delayed:
            delayed = True
            time.sleep(0.08)
        return ready

    monkeypatch.setattr(experiment, "wait", delay_first_ready)
    with pytest.raises(TimeoutError, match="startup handshake"):
        experiment._run_process_phase(  # noqa: SLF001
            _phase_test_operation,
            (("return", 1),),
            process_count=1,
            timeout_seconds=0.05,
        )
    assert not multiprocessing.active_children()


def test_distributed_phase_rejects_a_result_header_after_task_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import experiment

    real_monotonic = experiment.monotonic
    marker = tmp_path / "worker-complete"

    def jump_clock_after_worker_completion() -> float:
        observed = real_monotonic()
        return observed + 10.0 if marker.exists() else observed

    monkeypatch.setattr(experiment, "monotonic", jump_clock_after_worker_completion)
    with pytest.raises(TimeoutError, match="task exceeded"):
        experiment._run_process_phase(  # noqa: SLF001
            _phase_mark_complete,
            ((str(marker),),),
            process_count=1,
            timeout_seconds=5.0,
        )
    assert not multiprocessing.active_children()


def test_distributed_phase_terminates_sibling_after_worker_failure() -> None:
    from yieldforge.oracle import experiment

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="forced phase failure"):
        experiment._run_process_phase(  # noqa: SLF001
            _phase_test_operation,
            (("fail", 0), ("sleep", 5)),
            process_count=2,
            timeout_seconds=10.0,
        )

    assert time.monotonic() - started < 2.0


def test_distributed_phase_rejects_payload_from_nonzero_worker_exit() -> None:
    from yieldforge.oracle import experiment

    with pytest.raises(RuntimeError, match=r"exited nonzero \(exit_code=7\)"):
        experiment._run_process_phase(  # noqa: SLF001
            _phase_return_then_exit_nonzero,
            ((),),
            process_count=1,
            timeout_seconds=5.0,
        )


def test_distributed_phase_terminates_workers_at_deadline() -> None:
    from yieldforge.oracle import experiment

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="exceeded"):
        experiment._run_process_phase(  # noqa: SLF001
            _phase_test_operation,
            (("sleep", 5),),
            process_count=1,
            timeout_seconds=0.1,
        )

    assert time.monotonic() - started < 2.0


def test_distributed_phase_gives_each_queued_task_a_full_timeout_window() -> None:
    from yieldforge.oracle import experiment

    started = time.monotonic()
    results = experiment._run_process_phase(  # noqa: SLF001
        _phase_test_operation,
        (("sleep", 0.5), ("sleep", 0.5)),
        process_count=1,
        timeout_seconds=0.8,
    )

    assert tuple(value for value, _pid in results) == (0.5, 0.5)
    assert time.monotonic() - started > 0.8


def test_distributed_phase_cleans_started_workers_when_later_start_fails() -> None:
    from yieldforge.oracle import experiment

    before = {child.pid for child in multiprocessing.active_children()}
    unpicklable = lambda: None  # noqa: E731
    try:
        with pytest.raises(Exception):  # noqa: B017
            experiment._run_process_phase(  # noqa: SLF001
                _phase_test_operation,
                (("sleep", 5), ("return", unpicklable)),
                process_count=2,
                timeout_seconds=10.0,
            )
        orphans = [
            child
            for child in multiprocessing.active_children()
            if child.pid not in before
        ]
        assert orphans == []
    finally:
        for child in multiprocessing.active_children():
            if child.pid not in before:
                child.terminate()
                child.join(timeout=1.0)


def test_distributed_phase_timeout_terminates_worker_descendants(
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import experiment

    pid_path = tmp_path / "descendant.pid"
    with pytest.raises(TimeoutError, match="exceeded"):
        experiment._run_process_phase(  # noqa: SLF001
            _phase_spawn_descendant,
            ((str(pid_path),),),
            process_count=1,
            timeout_seconds=1.0,
        )
    assert pid_path.exists()
    descendant_pid = int(pid_path.read_text())
    try:
        for _ in range(30):
            try:
                os.kill(descendant_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail("M8 worker descendant survived phase timeout")
    finally:
        try:
            os.kill(descendant_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_distributed_phase_rejects_success_with_surviving_descendant(
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import experiment

    pid_path = tmp_path / "successful-descendant.pid"
    with pytest.raises(RuntimeError, match="surviving process-group descendant"):
        experiment._run_process_phase(  # noqa: SLF001
            _phase_leave_descendant_after_success,
            ((str(pid_path),),),
            process_count=1,
            timeout_seconds=5.0,
        )
    descendant_pid = int(pid_path.read_text())
    try:
        for _ in range(30):
            try:
                os.kill(descendant_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail("M8 descendant survived a nominal worker success")
    finally:
        try:
            os.kill(descendant_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_nested_audit_failure_restores_context_and_terminates_descendant(
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import (
        activate_m8_translation_audit_processes,
        current_m8_translation_audit_processes,
        require_m8_translation_audit_processes,
    )

    pid_path = tmp_path / "nested-audit-descendant.pid"
    with activate_m8_translation_audit_processes(4):
        assert require_m8_translation_audit_processes() == 4
        with pytest.raises(RuntimeError, match="synthetic nested audit failure"):
            with activate_m8_translation_audit_processes(2):
                assert require_m8_translation_audit_processes() == 2
                experiment._run_process_phase(  # noqa: SLF001
                    _phase_fail_nested_audit_with_descendant,
                    ((str(pid_path), 2),),
                    process_count=1,
                    timeout_seconds=5.0,
                )
        assert require_m8_translation_audit_processes() == 4
    assert current_m8_translation_audit_processes() is None

    assert pid_path.exists()
    descendant_pid = int(pid_path.read_text())
    try:
        for _ in range(30):
            try:
                os.kill(descendant_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail("M8 nested-audit descendant survived worker failure")
    finally:
        try:
            os.kill(descendant_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_distributed_phase_never_signals_a_completed_worker_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import experiment

    pid_path = tmp_path / "completed.pid"
    signaled_groups = []
    monkeypatch.setattr(
        experiment.os,
        "killpg",
        lambda group_id, sent_signal: signaled_groups.append(
            (group_id, sent_signal)
        ),
    )

    with pytest.raises(RuntimeError, match="delayed phase failure"):
        experiment._run_process_phase(  # noqa: SLF001
            _phase_record_pid,
            ((str(pid_path),),),
            process_count=1,
            timeout_seconds=5.0,
        )
        experiment._run_process_phase(  # pragma: no cover  # noqa: SLF001
            _phase_delayed_failure,
            ((0.2,),),
            process_count=1,
            timeout_seconds=5.0,
        )

    completed_pid = int(pid_path.read_text())
    assert all(group_id != completed_pid for group_id, _signal in signaled_groups)


def test_distributed_generator_worker_round_trips_one_real_cell() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )

    (result,) = experiment._run_process_phase(  # noqa: SLF001
        experiment._generate_cell_worker,  # noqa: SLF001
        (
            (
                cell,
                runtime.rules,
                runtime.jagua_executable,
                M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
            ),
        ),
        process_count=1,
    )

    assert result.cell.stream == cell.stream
    assert result.sparse.proofs
    assert result.elapsed_seconds > 0


def test_portable_fact_workers_handoff_canonical_bytes_and_independent_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET
    from yieldforge.oracle.facts import M8UncheckedFactBundleV2

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )
    freeze_id = "yfm7freeze-" + "b" * 24
    freeze_sha256 = "sha256:" + "b" * 64
    generated = experiment._generate_portable_fact_bundle_worker(  # noqa: SLF001
        cell,
        runtime.rules,
        runtime.jagua_executable,
        freeze_id,
        freeze_sha256,
        None,
        M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
    )

    assert type(generated.semantic_bundle_bytes) is bytes
    assert not hasattr(generated, "bundle")
    strict = M8UncheckedFactBundleV2.model_validate_json(
        generated.semantic_bundle_bytes,
        strict=True,
    )
    assert generated.bundle_sha256 == strict.bundle_sha256
    assert generated.semantic_serialized_bytes == len(generated.semantic_bundle_bytes)
    assert generated.fixed_layer_node_count == sum(
        len(getattr(strict, layer))
        for layer in (
            "translation_batches",
            "candidate_scalar_facts",
            "frontier_facts",
            "standard_candidate_facts",
            "common_lemmas",
            "influence_facts",
            "action_roots",
        )
    )
    assert generated.registry_state_after.is_clean
    assert generated.registry_state_after.translation_audit_processes is None
    assert set(vars(generated.registry_state_after).values()) == {0, None}

    original_check = experiment.check_m8_fact_bundle
    seen_checker_bytes = []

    def observe_bytes(request):  # type: ignore[no-untyped-def]
        seen_checker_bytes.append(request.semantic_bundle_bytes)
        assert request.semantic_bundle_bytes is generated.semantic_bundle_bytes
        return original_check(request)

    monkeypatch.setattr(experiment, "check_m8_fact_bundle", observe_bytes)

    checked = experiment._check_portable_fact_bundle_worker(  # noqa: SLF001
        generated.semantic_bundle_bytes,
        cell,
        runtime.rules,
        runtime.jagua_executable,
        freeze_id,
        freeze_sha256,
        None,
        M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
    )

    assert checked.check.valid
    assert seen_checker_bytes == [generated.semantic_bundle_bytes]
    assert checked.bundle_sha256 == generated.bundle_sha256
    assert checked.semantic_serialized_bytes == generated.semantic_serialized_bytes
    assert checked.fixed_layer_node_count == generated.fixed_layer_node_count
    assert checked.layer_counts == (
        generated.translation_batch_count,
        generated.candidate_scalar_fact_count,
        generated.frontier_fact_count,
        generated.standard_candidate_fact_count,
        generated.common_lemma_count,
        generated.influence_fact_count,
        generated.action_root_count,
    )
    assert checked.metadata_reconciliation_wall_seconds >= 0.0
    assert checked.check.total_exact_fallback_count == 0
    assert checked.registry_state_after.is_clean
    assert checked.registry_state_after.translation_audit_processes is None
    assert set(vars(checked.registry_state_after).values()) == {0, None}
    assert {
        "fact_bundle_strict_load",
        "fact_bundle_common_verification",
        "fact_bundle_action_traversal",
        "fact_bundle_cleanup",
    } <= _phase_names_from_report(checked.profile)

    with pytest.raises(TypeError, match="serialized bytes"):
        experiment._check_portable_fact_bundle_worker(  # noqa: SLF001
            strict,  # type: ignore[arg-type]
            cell,
            runtime.rules,
            runtime.jagua_executable,
            freeze_id,
            freeze_sha256,
            None,
            M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
        )


def test_portable_checker_rejects_oversized_transport_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment

    monkeypatch.setattr(
        experiment,
        "check_m8_fact_bundle",
        lambda _request: pytest.fail("oversized bytes reached Task6"),
    )
    monkeypatch.setattr(experiment, "_M8_GATE3_MAX_BUNDLE_BYTES", 32)
    oversized = b"x" * 33

    with pytest.raises(ValueError, match="exceeds the frozen byte cap"):
        experiment._check_portable_fact_bundle_worker(  # noqa: SLF001
            oversized,
            object(),  # type: ignore[arg-type]
            object(),
            Path("jagua"),
            "yfm7freeze-" + "b" * 24,
            "sha256:" + "b" * 64,
            "sha256:" + "0" * 64,
            2,
        )


def test_portable_fact_handoff_uses_distinct_fresh_generator_and_checker_processes() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )
    freeze_id = "yfm7freeze-" + "b" * 24
    freeze_sha256 = "sha256:" + "b" * 64
    (generated,) = experiment._run_process_phase(  # noqa: SLF001
        experiment._generate_portable_fact_bundle_worker,  # noqa: SLF001
        (
            (
                cell,
                runtime.rules,
                runtime.jagua_executable,
                freeze_id,
                freeze_sha256,
                None,
                M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
            ),
        ),
        process_count=1,
    )
    (checked,) = experiment._run_process_phase(  # noqa: SLF001
        experiment._check_portable_fact_bundle_worker,  # noqa: SLF001
        (
            (
                generated.semantic_bundle_bytes,
                cell,
                runtime.rules,
                runtime.jagua_executable,
                freeze_id,
                freeze_sha256,
                None,
                M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
            ),
        ),
        process_count=1,
    )

    assert generated.worker_pid != os.getpid()
    assert checked.worker_pid != os.getpid()
    assert generated.worker_pid != checked.worker_pid
    assert checked.check.valid
    generator_fields = {item.name for item in fields(generated)}
    assert {
        "cell",
        "request",
        "bundle",
        "proof",
        "proofs",
        "capability",
        "capabilities",
    }.isdisjoint(generator_fields)
    assert type(generated.semantic_bundle_bytes) is bytes

    with pytest.raises(ValueError, match="does not reconcile"):
        experiment._reconcile_portable_fact_handoff(  # noqa: SLF001
            replace(
                generated,
                fixed_layer_node_count=generated.fixed_layer_node_count + 1,
            ),
            checked,
        )


def test_portable_parent_strictly_revalidates_untrusted_worker_models() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )
    freeze_id = "yfm7freeze-" + "b" * 24
    freeze_sha256 = "sha256:" + "b" * 64
    generated = experiment._generate_portable_fact_bundle_worker(  # noqa: SLF001
        cell,
        runtime.rules,
        runtime.jagua_executable,
        freeze_id,
        freeze_sha256,
        None,
        M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
    )
    checked = experiment._check_portable_fact_bundle_worker(  # noqa: SLF001
        generated.semantic_bundle_bytes,
        cell,
        runtime.rules,
        runtime.jagua_executable,
        freeze_id,
        freeze_sha256,
        None,
        M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
    )

    forged_check = replace(
        checked,
        check=checked.check.model_copy(update={"checked_common_lemma_count": -1}),
    )
    with pytest.raises(ValidationError):
        experiment._reconcile_portable_fact_handoff(  # noqa: SLF001
            generated,
            forged_check,
        )

    forged_telemetry = object.__new__(type(generated.telemetry))
    forged_telemetry.__dict__.update(vars(generated.telemetry))
    forged_telemetry.__dict__["serialization_seconds"] = float("nan")
    with pytest.raises(ValueError, match="serialization timing"):
        experiment._reconcile_portable_fact_handoff(  # noqa: SLF001
            replace(generated, telemetry=forged_telemetry),
            checked,
        )


def test_full_fact_checker_reports_actual_counted_translation_audit_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shapely import Polygon

    from tests.oracle.fixtures import inventory_item, two_problem_runtime
    from tests.oracle.test_fact_checker import _full_check_request
    from yieldforge.baseline.policies import M7PolicyName
    from yieldforge.baseline.replay import initial_m7_cursor
    from yieldforge.oracle import fact_checker
    from yieldforge.oracle.concurrency import activate_m8_translation_audit_processes
    from yieldforge.oracle.fact_checker import check_m8_fact_bundle
    from yieldforge.oracle.factored import (
        M8UncheckedBundleRequest,
        score_unchecked_fact_bundle,
    )
    from yieldforge.oracle.profiling import activate_m8_profile
    from yieldforge.oracle.reference import M8OracleRequest
    from yieldforge.oracle.visibility import FullRealizedVisibility
    from yieldforge.temporal_benchmark.contracts import FeasibilityRateManifest

    runtime = two_problem_runtime(
        first_width=9.0,
        second_width=4.0,
        policy=M7PolicyName.NET_COST,
        rates=FeasibilityRateManifest(
            purchase_cost_per_area=1.0,
            storage_cost_per_area_hour=0.0,
            return_handling_cost_per_remnant=0.0,
            retrieval_handling_cost_per_remnant=200.0,
            scrap_credit_per_area=0.0,
        ),
    )
    binding = runtime.replay_input.instances[1]
    counted_inventory = inventory_item(
        Polygon(((0, 0), (4, 0), (4, 1), (1, 1), (1, 10), (0, 10))),
        material=binding.material,
        token="gate3-counted-call",
    )
    oracle_request = M8OracleRequest(
        runtime=runtime,
        cursor=replace(
            initial_m7_cursor(runtime.replay_input),
            inventory=(counted_inventory,),
        ),
        visibility=FullRealizedVisibility(runtime.replay_input.instances),
    )
    unchecked = M8UncheckedBundleRequest(
        oracle_request=oracle_request,
        freeze_id="yfm7freeze-" + "b" * 24,
        freeze_sha256="sha256:" + "b" * 64,
    )
    generated = score_unchecked_fact_bundle(unchecked)
    request = _full_check_request(unchecked, generated.semantic_bytes)
    calls = 0
    original = fact_checker.audit_layout_translation_batch

    def observe_call(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(fact_checker, "audit_layout_translation_batch", observe_call)
    with (
        activate_m8_translation_audit_processes(2),
        activate_m8_profile() as success_profiler,
    ):
        result = check_m8_fact_bundle(request)
    success_profile = success_profiler.report()

    assert result.valid
    assert calls == 1
    assert result.counted_translation_audit_count == 1
    assert "counted_translation_audit_call_count" not in result.model_dump()
    assert _phase_occurrence_count(
        success_profile,
        "counted_translation_audit_call",
    ) == 1

    def reject_call(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise ValueError("forced counted audit rejection")

    monkeypatch.setattr(fact_checker, "audit_layout_translation_batch", reject_call)
    with (
        activate_m8_translation_audit_processes(2),
        activate_m8_profile() as rejected_profiler,
    ):
        rejected = check_m8_fact_bundle(request)
    rejected_profile = rejected_profiler.report()
    assert not rejected.valid
    assert rejected.failure_code == "translation_count_mismatch"
    assert rejected.counted_translation_audit_count == 0
    assert _phase_occurrence_count(
        rejected_profile,
        "counted_translation_audit_call",
    ) == 1

    def missing_process_count() -> int:
        raise RuntimeError("M8 translation audit process count is not configured")

    monkeypatch.setattr(
        fact_checker,
        "require_m8_translation_audit_processes",
        missing_process_count,
    )
    with activate_m8_profile() as missing_context_profiler:
        missing_context_result = check_m8_fact_bundle(request)
    assert not missing_context_result.valid
    assert missing_context_result.failure_code == "internal_checker_failure"
    assert _phase_occurrence_count(
        missing_context_profiler.report(),
        "counted_translation_audit_call",
    ) == 0


def test_spawned_portable_checker_corrupt_bytes_fail_closed_without_artifact(
    tmp_path: Path,
) -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )
    generated = experiment._generate_portable_fact_bundle_worker(  # noqa: SLF001
        cell,
        runtime.rules,
        runtime.jagua_executable,
        "yfm7freeze-" + "b" * 24,
        "sha256:" + "b" * 64,
        None,
        M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
    )
    task = (
        generated.semantic_bundle_bytes + b" ",
        cell,
        runtime.rules,
        runtime.jagua_executable,
        "yfm7freeze-" + "b" * 24,
        "sha256:" + "b" * 64,
        None,
        M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
    )

    with pytest.raises(RuntimeError, match="failure_code=noncanonical_bundle"):
        experiment._run_process_phase(  # noqa: SLF001
            experiment._check_portable_fact_bundle_worker,  # noqa: SLF001
            (task,),
            process_count=1,
        )
    assert not multiprocessing.active_children()
    assert experiment._portable_registry_state().is_clean  # noqa: SLF001
    assert tuple(tmp_path.iterdir()) == ()


def test_spawned_portable_checker_exception_fails_closed_without_artifact(
    tmp_path: Path,
) -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )
    generated = experiment._generate_portable_fact_bundle_worker(  # noqa: SLF001
        cell,
        runtime.rules,
        runtime.jagua_executable,
        "yfm7freeze-" + "b" * 24,
        "sha256:" + "b" * 64,
        None,
        M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
    )
    task = (
        generated.semantic_bundle_bytes,
        cell,
        runtime.rules,
        runtime.jagua_executable,
        "yfm7freeze-" + "b" * 24,
        "sha256:" + "b" * 64,
        None,
        M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
    )

    with pytest.raises(RuntimeError, match="forced Task6 checker exception"):
        experiment._run_process_phase(  # noqa: SLF001
            _phase_force_portable_checker_exception,
            (task,),
            process_count=1,
        )
    assert not multiprocessing.active_children()
    assert experiment._portable_registry_state().is_clean  # noqa: SLF001
    assert tuple(tmp_path.iterdir()) == ()


def test_portable_pipeline_rejects_dirty_controller_before_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    dirty = replace(
        experiment._PortableRegistryState(),  # noqa: SLF001
        validated_common=1,
    )
    monkeypatch.setattr(experiment, "_portable_registry_state", lambda: dirty)
    monkeypatch.setattr(
        experiment,
        "_run_process_phase",
        lambda *_args, **_kwargs: pytest.fail("dirty controller started a worker"),
    )

    with pytest.raises(ValueError, match="controller.*live registries"):
        experiment._execute_portable_fact_cells(  # noqa: SLF001
            (object(),),  # type: ignore[arg-type]
            rules=object(),
            jagua_executable=Path("jagua"),
            freeze_id="yfm7freeze-" + "b" * 24,
            freeze_sha256="sha256:" + "b" * 64,
            expected_jagua_sha256="sha256:" + "0" * 64,
            budget=M8_GATE3_CONCURRENCY_BUDGET,
        )


def test_portable_fact_pipeline_repeats_generation_then_checks_in_a_fresh_process() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )
    execution, retained_sources = experiment._execute_portable_fact_cells_with_sources(  # noqa: SLF001
        (cell,),
        rules=runtime.rules,
        jagua_executable=runtime.jagua_executable,
        freeze_id="yfm7freeze-" + "b" * 24,
        freeze_sha256="sha256:" + "b" * 64,
        expected_jagua_sha256=None,
        budget=M8_GATE3_CONCURRENCY_BUDGET,
    )

    assert len(execution.cells) == 1
    assert len(retained_sources) == 1
    evidence = execution.cells[0]
    retained = retained_sources[0]
    assert evidence.first_generation.bundle_sha256 == (
        evidence.second_generation.bundle_sha256
    )
    assert evidence.first_generation.semantic_bundle_bytes_sha256 == (
        evidence.second_generation.semantic_bundle_bytes_sha256
    )
    assert not hasattr(evidence.first_generation, "semantic_bundle_bytes")
    assert not hasattr(evidence.second_generation, "semantic_bundle_bytes")
    assert retained.first_generation == evidence.first_generation
    assert retained.check == evidence.check
    assert len(retained.semantic_bundle_bytes) == (
        retained.first_generation.semantic_serialized_bytes
    )
    assert retained.cell_identity == (
        evidence.first_generation.regime,
        evidence.first_generation.temporal_seed,
        evidence.first_generation.stream_id,
        evidence.first_generation.event_count,
    )
    assert evidence.check.check.valid
    assert evidence.first_generation.worker_pid != evidence.second_generation.worker_pid
    assert evidence.check.worker_pid not in {
        evidence.first_generation.worker_pid,
        evidence.second_generation.worker_pid,
        os.getpid(),
    }
    assert execution.first_generation_phase_wall_seconds > 0.0
    assert execution.second_generation_phase_wall_seconds > 0.0
    assert execution.checker_phase_wall_seconds > 0.0
    assert execution.worker_payload_handoff_wall_seconds >= 0.0
    assert execution.process_exit_validation_wall_seconds >= 0.0

    generated = experiment._PortableBundleWorkerResult(  # noqa: SLF001
        **vars(retained.first_generation),
        semantic_bundle_bytes=retained.semantic_bundle_bytes,
    )
    unreconciled = replace(
        retained.check,
        bundle_sha256="sha256:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="producer and checker metadata differ"):
        experiment._retain_portable_fact_checked_source(  # noqa: SLF001
            generated,
            unreconciled,
        )

    fallback_check = retained.check.check.model_copy(
        update={
            "common_exact_fallback_count": 1,
            "total_exact_fallback_count": 1,
            "common_exact_fallback_wall_seconds": 0.1,
            "total_exact_fallback_wall_seconds": 0.1,
        }
    )
    with pytest.raises(ValueError, match="forbidden exact fallback"):
        experiment._retain_portable_fact_checked_source(  # noqa: SLF001
            generated,
            replace(retained.check, check=fallback_check),
        )


def test_portable_fact_compatibility_wrapper_discards_private_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    pipeline = object()
    retained_sources = (object(),)
    monkeypatch.setattr(
        experiment,
        "_execute_portable_fact_cells_with_sources",
        lambda *_args, **_kwargs: (pipeline, retained_sources),
    )

    result = experiment._execute_portable_fact_cells(  # noqa: SLF001
        (object(),),  # type: ignore[arg-type]
        rules=object(),
        jagua_executable=None,
        freeze_id="yfm7freeze-" + "b" * 24,
        freeze_sha256="sha256:" + "b" * 64,
        expected_jagua_sha256=None,
        budget=M8_GATE3_CONCURRENCY_BUDGET,
    )

    assert result is pipeline
    assert "retained_sources" not in experiment.M8PortableFactGate3Result.model_fields
    assert "semantic_bundle_bytes" not in experiment.M8PortableFactGate3Result.model_fields


def test_portable_fact_source_capture_runs_one_generation_and_one_checker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )
    operations = []
    run_process_phase = experiment._run_process_phase  # noqa: SLF001

    def recording_phase(operation, *args, **kwargs):  # type: ignore[no-untyped-def]
        operations.append(operation)
        return run_process_phase(operation, *args, **kwargs)

    monkeypatch.setattr(experiment, "_run_process_phase", recording_phase)
    (source,) = experiment._capture_portable_fact_checked_sources(  # noqa: SLF001
        (cell,),
        rules=runtime.rules,
        jagua_executable=runtime.jagua_executable,
        freeze_id="yfm7freeze-" + "b" * 24,
        freeze_sha256="sha256:" + "b" * 64,
        expected_jagua_sha256=None,
        budget=M8_GATE3_CONCURRENCY_BUDGET,
    )

    assert operations == [
        experiment._generate_portable_fact_bundle_worker,  # noqa: SLF001
        experiment._check_portable_fact_bundle_worker,  # noqa: SLF001
    ]
    assert source.check.check.valid
    assert source.check.check.total_exact_fallback_count == 0
    assert source.first_generation.bundle_sha256 == source.check.bundle_sha256
    assert not multiprocessing.active_children()
    assert experiment._portable_registry_state().is_clean  # noqa: SLF001


def test_portable_fact_profile_cell_is_fresh_exact_and_wall_attributed() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET
    from yieldforge.oracle.source_attestation import capture_source_tree

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )

    (
        source,
        repeated_generation,
        generator_profile,
        generation_phase,
        repeat_generation_phase,
        checker_phase,
        timing,
    ) = (
        experiment._profile_portable_fact_cell(  # noqa: SLF001
            cell,
            rules=runtime.rules,
            jagua_executable=runtime.jagua_executable,
            freeze_id="yfm7freeze-" + "b" * 24,
            freeze_sha256="sha256:" + "b" * 64,
            expected_jagua_sha256=None,
            budget=M8_GATE3_CONCURRENCY_BUDGET,
            source_tree=capture_source_tree(),
        )
    )

    assert source.check.check.valid
    assert source.check.check.total_exact_fallback_count == 0
    assert source.first_generation.bundle_sha256 == source.check.bundle_sha256
    assert repeated_generation.bundle_sha256 == source.first_generation.bundle_sha256
    assert (
        repeated_generation.semantic_bundle_bytes_sha256
        == source.first_generation.semantic_bundle_bytes_sha256
    )
    assert repeated_generation.semantic_serialized_bytes == len(source.semantic_bundle_bytes)
    assert source.first_generation.worker_pid != source.check.worker_pid
    assert repeated_generation.worker_pid not in {
        source.first_generation.worker_pid,
        source.check.worker_pid,
    }
    assert source.first_generation.worker_pid != os.getpid()
    assert source.check.worker_pid != os.getpid()
    assert generator_profile.accounted_wall_fraction >= 0.90
    assert source.check.profile.accounted_wall_fraction >= 0.90
    assert {
        "fact_bundle_generator_authority_reconstruction",
        "fact_bundle_generation",
        "fact_bundle_handoff_serialization",
        "fact_bundle_prepared_context_session",
        "fact_bundle_unchecked_traversal",
        "fact_bundle_layer_assembly",
    } <= _phase_names_from_report(generator_profile)
    assert {
        "fact_bundle_metadata_reconciliation",
        "fact_bundle_authority_reconstruction",
        "fact_bundle_authority_session",
        "fact_bundle_action_traversal",
    } <= _phase_names_from_report(source.check.profile)
    assert generation_phase.result_payload_bytes > 0
    assert repeat_generation_phase.result_payload_bytes > 0
    assert checker_phase.task_payload_bytes > 0
    assert timing.first_generation_phase_wall_seconds > (
        source.first_generation.generation_wall_seconds
    )
    assert timing.second_generation_phase_wall_seconds > (
        repeated_generation.generation_wall_seconds
    )
    assert timing.checker_phase_wall_seconds > source.check.checker_wall_seconds
    assert timing.total_pipeline_wall_seconds >= (
        timing.first_generation_phase_wall_seconds
        + timing.second_generation_phase_wall_seconds
        + timing.checker_phase_wall_seconds
    )
    expected_runtime_sha256 = experiment._portable_worker_runtime_identity(  # noqa: SLF001
        runtime.jagua_executable,
        None,
    )[1]
    assert (
        timing.generator_runtime_content_sha256
        == timing.repeat_generator_runtime_content_sha256
        == timing.checker_runtime_content_sha256
        == expected_runtime_sha256
    )
    assert not multiprocessing.active_children()


def test_portable_profile_requires_the_committed_gate3_identity() -> None:
    from yieldforge.experiments.contracts import semantic_sha256
    from yieldforge.oracle import experiment

    portable_path = (
        Path(__file__).parents[2]
        / "experiments/results/"
        "m8-portable-fact-gate3-yfm8gate3-ea8a12969396172d7dbc4774.json"
    )
    official = experiment.M8PortableFactGate3Result.model_validate_json(
        portable_path.read_bytes(),
        strict=True,
    )
    draft = official.model_copy(
        update={
            "gate3_id": "yfm8gate3-" + "0" * 24,
            "content_sha256": "sha256:" + "0" * 64,
            "total_pipeline_wall_seconds": official.total_pipeline_wall_seconds + 1.0,
        }
    )
    digest = semantic_sha256(draft, excluded_fields={"gate3_id", "content_sha256"})
    forged = type(official).model_validate_json(
        draft.model_copy(
            update={
                "gate3_id": f"yfm8gate3-{digest[:24]}",
                "content_sha256": f"sha256:{digest}",
            }
        ).model_dump_json(),
        strict=True,
    )

    with pytest.raises(ValueError, match="committed freeze"):
        experiment._require_official_portable_profile_gate3(forged)  # noqa: SLF001


def test_portable_profile_rejects_repeat_generation_identity_mismatch() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )
    generated = experiment._generate_portable_fact_bundle_worker(  # noqa: SLF001
        cell,
        runtime.rules,
        runtime.jagua_executable,
        "yfm7freeze-" + "b" * 24,
        "sha256:" + "b" * 64,
        None,
        M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
    )
    repeated = experiment._discard_portable_bundle_bytes(generated)  # noqa: SLF001
    mismatched = replace(
        repeated,
        semantic_bundle_bytes_sha256="sha256:" + "0" * 64,
    )

    with pytest.raises(ValueError, match="repeated generation identity"):
        experiment._require_portable_profile_repeat(generated, mismatched)  # noqa: SLF001


def test_publish_portable_profile_rejects_untyped_self_rehashed_forgery(
    tmp_path: Path,
) -> None:
    from tests.oracle.test_profile_evidence import valid_profile_payload
    from yieldforge.oracle import experiment

    forged = valid_profile_payload()
    forged["official_gate3_id"] = "yfm8gate3-" + "0" * 24
    semantic = {
        key: value
        for key, value in forged.items()
        if key not in {"profile_id", "content_sha256"}
    }
    digest = experiment.semantic_sha256(semantic)
    forged["profile_id"] = f"yfm8profile-{digest[:24]}"
    forged["content_sha256"] = f"sha256:{digest}"

    with pytest.raises(TypeError, match="exact result model"):
        experiment.publish_portable_fact_profile(tmp_path / "forged.json", forged)


def test_publish_portable_profile_strictly_round_trips_the_v2_result(
    tmp_path: Path,
) -> None:
    from tests.oracle.test_profile_evidence import valid_profile_payload
    from yieldforge.oracle import experiment
    from yieldforge.oracle.profile_evidence import M8PortableHotspotProfileV2

    result = M8PortableHotspotProfileV2.model_validate(
        valid_profile_payload(),
        strict=True,
    )
    path = experiment.publish_portable_fact_profile(tmp_path / "profile.json", result)

    assert M8PortableHotspotProfileV2.model_validate_json(
        path.read_bytes(),
        strict=True,
    ) == result


def test_publish_portable_profile_is_immutable_and_idempotent(tmp_path: Path) -> None:
    from tests.oracle.test_profile_evidence import valid_profile_payload
    from yieldforge.oracle import experiment
    from yieldforge.oracle.profile_evidence import M8PortableHotspotProfileV2

    first = M8PortableHotspotProfileV2.model_validate(
        valid_profile_payload(),
        strict=True,
    )
    path = experiment.publish_portable_fact_profile(tmp_path / "profile.json", first)
    original = path.read_bytes()

    changed_payload = valid_profile_payload()
    changed_payload["generator_worker_wall_seconds"] = 1.1
    changed_payload["core_generation_plus_checker_worker_wall_seconds"] = 4.1
    changed_payload["repeated_generation_plus_checker_worker_wall_seconds"] = 6.1
    from tests.oracle.test_profile_evidence import _rehash

    changed = M8PortableHotspotProfileV2.model_validate(
        _rehash(changed_payload),
        strict=True,
    )

    assert experiment.publish_portable_fact_profile(path, first) == path
    with pytest.raises(ValueError, match="existing profile differs"):
        experiment.publish_portable_fact_profile(path, changed)
    assert path.read_bytes() == original

    symlink = tmp_path / "profile-link.json"
    symlink.symlink_to(path)
    with pytest.raises(ValueError, match="existing profile differs"):
        experiment.publish_portable_fact_profile(symlink, first)


def test_publish_portable_profile_rejects_symlinked_parent_directory(
    tmp_path: Path,
) -> None:
    from tests.oracle.test_profile_evidence import valid_profile_payload
    from yieldforge.oracle import experiment
    from yieldforge.oracle.profile_evidence import M8PortableHotspotProfileV2

    result = M8PortableHotspotProfileV2.model_validate(
        valid_profile_payload(),
        strict=True,
    )
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ValueError, match="parent directory"):
        experiment.publish_portable_fact_profile(alias_parent / "profile.json", result)

    assert not (real_parent / "profile.json").exists()


def test_publish_portable_profile_rejects_replaced_temporary_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.oracle.test_profile_evidence import valid_profile_payload
    from yieldforge.oracle import experiment
    from yieldforge.oracle.profile_evidence import M8PortableHotspotProfileV2

    result = M8PortableHotspotProfileV2.model_validate(
        valid_profile_payload(),
        strict=True,
    )
    original_link = experiment.os.link

    def replace_then_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
        follow_symlinks: bool,
    ) -> None:
        experiment.os.unlink(source, dir_fd=src_dir_fd)
        replacement = experiment.os.open(
            source,
            experiment.os.O_WRONLY | experiment.os.O_CREAT | experiment.os.O_EXCL,
            0o600,
            dir_fd=src_dir_fd,
        )
        try:
            experiment.os.write(replacement, b"forged publication\n")
        finally:
            experiment.os.close(replacement)
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(experiment.os, "link", replace_then_link)

    output = tmp_path / "profile.json"
    with pytest.raises(ValueError, match="publication integrity differs"):
        experiment.publish_portable_fact_profile(output, result)

    assert not output.exists()


@pytest.mark.parametrize("worker_index", (0, 1, 2))
def test_portable_profile_controller_rejects_each_worker_runtime_envelope(
    worker_index: int,
) -> None:
    from yieldforge.oracle import experiment

    expected = ("yieldforge-m8-gate3-runtime-v1", "sha256:" + "1" * 64)
    observed = [expected, expected, expected]
    observed[worker_index] = (expected[0], "sha256:" + "0" * 64)

    with pytest.raises(ValueError, match="worker runtime identity"):
        experiment._require_portable_profile_worker_runtime_identities(  # noqa: SLF001
            expected=expected,
            observed=tuple(observed),
        )


def test_portable_profile_runtime_must_match_the_frozen_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import platform

    from yieldforge.baseline.experiment import M7FrozenBaseline
    from yieldforge.oracle import experiment

    frozen = M7FrozenBaseline.model_validate_json(
        (
            Path(__file__).parents[2]
            / "experiments/results/m7-frozen-baseline-v1.json"
        ).read_bytes(),
        strict=True,
    )
    monkeypatch.setattr(platform, "python_version", lambda: "0.0.0")

    with pytest.raises(ValueError, match="runtime differs from the M7 freeze"):
        experiment._portable_profile_runtime_identity(  # noqa: SLF001
            frozen,
            jagua_executable_sha256=frozen.runtime.jagua_executable_sha256,
        )


def test_portable_gate3_finalizer_rejects_small_pipeline_evidence() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    runtime = two_problem_runtime(first_width=9.0, second_width=4.0)
    execution_cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )
    one = experiment._execute_portable_fact_cells(  # noqa: SLF001
        (execution_cell,),
        rules=runtime.rules,
        jagua_executable=runtime.jagua_executable,
        freeze_id="yfm7freeze-" + "b" * 24,
        freeze_sha256="sha256:" + "b" * 64,
        expected_jagua_sha256=None,
        budget=M8_GATE3_CONCURRENCY_BUDGET,
    )
    source = one.cells[0]

    def probe(regime: TemporalRegime, digit: str):  # type: ignore[no-untyped-def]
        identity = {
            "regime": regime,
            "temporal_seed": 2026082300,
            "stream_id": "yfts-" + digit * 24,
            "event_count": 2,
        }
        return replace(
            source,
            first_generation=replace(source.first_generation, **identity),
            second_generation=replace(source.second_generation, **identity),
            check=replace(source.check, **identity),
        )

    pipeline = replace(
        one,
        cells=(
            probe(TemporalRegime.NO_SIGNAL, "1"),
            probe(TemporalRegime.REGIME_SHIFT, "2"),
        ),
        outer_process_count=2,
        peak_compute_count=4,
        retained_first_generation_bundle_bytes=(
            2 * source.first_generation.semantic_serialized_bytes
        ),
    )
    with pytest.raises(ValueError, match="root count differs from the freeze"):
        experiment.finalize_portable_fact_gate3(
            m0_contract_id="yfm0-" + "1" * 24,
            m0_contract_sha256="sha256:" + "1" * 64,
            m6_contract_id="yfm6-" + "2" * 24,
            m6_contract_sha256="sha256:" + "2" * 64,
            m6_population_id="yftp-" + "3" * 24,
            m6_population_sha256="sha256:" + "3" * 64,
            problem_index_id="yfm7i-" + "4" * 24,
            problem_index_sha256="sha256:" + "4" * 64,
            freeze_id="yfm7freeze-" + "b" * 24,
            freeze_sha256="sha256:" + "b" * 64,
            calibration_view_id="yfm7cv-" + "5" * 24,
            calibration_view_sha256="sha256:" + "5" * 64,
            pipeline=pipeline,
        )


def test_portable_gate3_result_is_strict_separate_and_content_addressed() -> None:
    from yieldforge.oracle import experiment

    result = _portable_gate3_result()

    assert result.parent_v3_proof_id == "yfm8proof-b296ba919c07d55ece14c6db"
    assert result.generated_action_root_count == 887
    assert tuple(item.generated_action_root_count for item in result.cells) == (428, 459)
    assert result.evaluation_accessed is False
    assert "nested_exclusive" in result.timing_semantics
    assert experiment.M8PortableFactGate3Result.model_validate_json(
        result.model_dump_json(),
        strict=True,
    ) == result

    forged = result.model_copy(
        update={
            "cells": (
                result.cells[0].model_copy(
                    update={"decision_id": "yfm8d-" + "f" * 24}
                ),
                result.cells[1],
            )
        }
    )
    with pytest.raises(ValidationError, match="content identity"):
        experiment.M8PortableFactGate3Result.model_validate_json(
            forged.model_dump_json(),
            strict=True,
        )

    impossible = result.model_dump(mode="python")
    impossible["cells"][0]["timing"]["checker_worker_wall_seconds"] = 0.01
    semantic = {
        key: value
        for key, value in impossible.items()
        if key not in {"gate3_id", "content_sha256"}
    }
    digest = experiment.semantic_sha256(semantic)
    impossible["gate3_id"] = f"yfm8gate3-{digest[:24]}"
    impossible["content_sha256"] = f"sha256:{digest}"
    with pytest.raises(ValidationError, match="checker phases exceed worker wall time"):
        experiment.M8PortableFactGate3Result.model_validate(impossible, strict=True)

    with pytest.raises(ValidationError, match="metadata does not reconcile"):
        experiment.M8PortableFactGate3Cell.model_validate(
            {
                **result.cells[0].model_dump(mode="python"),
                "counted_translation_audit_call_count": 3,
            },
            strict=True,
        )


def test_publish_portable_gate3_is_atomic_idempotent_and_immutable(
    tmp_path: Path,
) -> None:
    from yieldforge.oracle import experiment

    result = _portable_gate3_result()
    path = experiment.publish_portable_fact_gate3(tmp_path, result)

    assert path.name == f"m8-portable-fact-gate3-{result.gate3_id}.json"
    assert experiment.M8PortableFactGate3Result.model_validate_json(
        path.read_bytes(),
        strict=True,
    ) == result
    assert experiment.publish_portable_fact_gate3(tmp_path, result) == path
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="immutable and differs"):
        experiment.publish_portable_fact_gate3(tmp_path, result)


def test_publish_portable_gate3_removes_temporary_file_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment

    monkeypatch.setattr(
        experiment.os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("forced link failure")),
    )
    with pytest.raises(OSError, match="forced link failure"):
        experiment.publish_portable_fact_gate3(tmp_path, _portable_gate3_result())
    assert tuple(tmp_path.iterdir()) == ()


def test_portable_gate3_selector_opens_only_two_frozen_calibration_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.baseline.contracts import M7CalibrationProblemView
    from yieldforge.oracle import experiment

    bindings = []
    for digit, regime in (("1", TemporalRegime.NO_SIGNAL), ("2", TemporalRegime.REGIME_SHIFT)):
        bindings.extend(
            SimpleNamespace(
                regime=regime,
                temporal_seed=2026082300,
                stream_id="yfts-" + digit * 24,
                sequence=position,
                binding_id=f"yfm7b-{digit * 20}{position:04x}",
            )
            for position in range(24)
        )
        bindings.append(
            SimpleNamespace(
                regime=regime,
                temporal_seed=2026082301,
                stream_id="yfts-" + "f" * 24,
                sequence=0,
                binding_id="yfm7b-" + "f" * 24,
            )
        )
    bindings.append(
        SimpleNamespace(
            regime=TemporalRegime.HIGH_MIX,
            temporal_seed=2026082300,
            stream_id="yfts-" + "e" * 24,
            sequence=0,
            binding_id="yfm7b-" + "e" * 24,
        )
    )
    monkeypatch.setattr(
        experiment,
        "select_calibration_instances",
        lambda _index: tuple(reversed(bindings)),
    )

    selected = experiment._select_portable_gate3_probe_streams(  # noqa: SLF001
        M7CalibrationProblemView.model_construct(evaluation_partition_opened=False)
    )

    assert tuple(stream[0].regime for stream in selected) == (
        TemporalRegime.NO_SIGNAL,
        TemporalRegime.REGIME_SHIFT,
    )
    assert all(len(stream) == 2 for stream in selected)
    assert all(item.temporal_seed == 2026082300 for stream in selected for item in stream)
    assert all(
        item.sequence in {0, 1}
        for stream in selected
        for item in stream
    )
    assert all(item.regime is not TemporalRegime.HIGH_MIX for stream in selected for item in stream)
    with pytest.raises(TypeError, match="exact calibration view"):
        experiment._select_portable_gate3_probe_streams(  # noqa: SLF001
            SimpleNamespace(evaluation_partition_opened=False)
        )


def test_portable_gate3_callable_has_no_probe_or_worker_override() -> None:
    from yieldforge.oracle.experiment import execute_portable_fact_gate3

    parameters = inspect.signature(execute_portable_fact_gate3).parameters
    assert {
        "index",
        "m0",
        "frozen",
        "archive_roots",
        "jagua_executable",
        "progress",
    } == set(parameters)
    assert {
        "regime",
        "seed",
        "event_count",
        "split",
        "worker_count",
        "allow_exact_replay",
    }.isdisjoint(parameters)


def test_portable_gate3_strictly_reloads_each_public_input_contract() -> None:
    from tests.baseline.test_replay import _m0
    from yieldforge.baseline.experiment import M7FrozenBaseline
    from yieldforge.baseline.problems import (
        build_registered_calibration_problem_view,
        build_registered_problem_index,
    )
    from yieldforge.oracle.experiment import execute_portable_fact_gate3

    full = build_registered_problem_index()
    index = build_registered_calibration_problem_view(
        full_problem_index_id=full.index_id,
        full_problem_index_sha256=full.content_sha256,
    )
    m0 = _m0()
    frozen = M7FrozenBaseline.model_validate_json(
        (
            Path(__file__).parents[2]
            / "experiments"
            / "results"
            / "m7-frozen-baseline-v1.json"
        ).read_bytes(),
        strict=True,
    )
    common = {
        "archive_roots": (),
        "jagua_executable": Path("/unused/jagua"),
    }

    with pytest.raises(ValidationError):
        execute_portable_fact_gate3(
            index=index.model_copy(update={"evaluation_partition_opened": True}),
            m0=m0,
            frozen=frozen,
            **common,
        )
    with pytest.raises(ValidationError):
        execute_portable_fact_gate3(
            index=index,
            m0=m0.model_copy(update={"contract_id": "yfm0-" + "0" * 24}),
            frozen=frozen,
            **common,
        )
    with pytest.raises(ValidationError):
        execute_portable_fact_gate3(
            index=index,
            m0=m0,
            frozen=frozen.model_copy(
                update={"freeze_id": "yfm7freeze-" + "0" * 24}
            ),
            **common,
        )


def test_distributed_checker_and_audit_round_trip_in_fresh_processes() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )
    generated = experiment._generate_cell_worker(  # noqa: SLF001
        cell,
        runtime.rules,
        runtime.jagua_executable,
        M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
    )
    audit_bindings = experiment._freeze_audit_bindings(  # noqa: SLF001
        experiment._audit_candidates_for_cell(  # noqa: SLF001
            cell.stream,
            generated.sparse,
        )
    )

    (checked,) = experiment._run_process_phase(  # noqa: SLF001
        experiment._check_cell_worker,  # noqa: SLF001
        (
            (
                cell,
                runtime.rules,
                runtime.jagua_executable,
                generated.sparse.proofs,
                M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
            ),
        ),
        process_count=1,
    )
    (sampled,) = experiment._run_process_phase(  # noqa: SLF001
        experiment._sample_audit_generator_worker,  # noqa: SLF001
        (
            (
                cell,
                runtime.rules,
                runtime.jagua_executable,
                audit_bindings,
                M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
            ),
        ),
        process_count=1,
    )
    (sampled_checked,) = experiment._run_process_phase(  # noqa: SLF001
        experiment._sample_audit_checker_worker,  # noqa: SLF001
        (
            (
                cell,
                runtime.rules,
                runtime.jagua_executable,
                tuple(item.proof for item in sampled.sampled),
                M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
            ),
        ),
        process_count=1,
    )
    reference_actions = experiment._run_process_phase(  # noqa: SLF001
        experiment._reference_audit_action_worker,  # noqa: SLF001
        tuple(
            (
                cell,
                runtime.rules,
                runtime.jagua_executable,
                binding.catalog_action_id,
            )
            for binding in audit_bindings
        ),
        process_count=1,
    )
    references = experiment._assemble_reference_audit_actions(  # noqa: SLF001
        reference_actions,
        audit_by_cell={generated.regime: audit_bindings},
    )
    audited = experiment._assemble_audit_results(  # noqa: SLF001
        sampled=(sampled,),
        checked=(sampled_checked,),
        references=references,
        audit_by_cell={generated.regime: audit_bindings},
    )[0]
    result = experiment._assemble_timed_cell(  # noqa: SLF001
        generated,
        checked=checked,
        audited=audited,
        audit_bindings=audit_bindings,
    )

    assert result.current_action_count == len(generated.sparse.proofs)
    assert result.checked_action_count == result.current_action_count
    assert result.valid_proof_count == result.current_action_count
    assert result.checker_failure_count == 0
    assert result.sampled_checker_failure_count == 0
    assert result.audit_mismatch_count == 0


def test_distributed_cell_assembly_rejects_cross_regime_worker_result() -> None:
    from tests.oracle.fixtures import two_problem_runtime
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    cell = experiment._ExecutionCell(  # noqa: SLF001
        stream=runtime.replay_input.instances,
        problem_ids=tuple(sorted(runtime.runtime_candidates)),
        replay_input=runtime.replay_input,
        verified=runtime.runtime_candidates,
    )
    generated = experiment._generate_cell_worker(  # noqa: SLF001
        cell,
        runtime.rules,
        runtime.jagua_executable,
        M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell,
    )
    wrong_regime = next(
        regime for regime in TemporalRegime if regime is not generated.regime
    )
    checked = SimpleNamespace(
        regime=wrong_regime,
        checks=(),
        elapsed_seconds=0.1,
    )
    audited = SimpleNamespace(regime=generated.regime)

    with pytest.raises(ValueError, match="regime identities"):
        experiment._assemble_timed_cell(  # noqa: SLF001
            generated,
            checked=checked,
            audited=audited,
            audit_bindings=(),
        )


def test_distributed_cell_phases_use_separate_pools_and_measure_wall_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import M8_GATE3_CONCURRENCY_BUDGET

    cells = tuple(
        SimpleNamespace(stream=(SimpleNamespace(regime=regime),))
        for regime in TemporalRegime
    )
    generated = tuple(
        SimpleNamespace(
            regime=regime,
            cell=cell,
            sparse=SimpleNamespace(proofs=(regime.value,)),
            elapsed_seconds=float(offset + 1),
        )
        for offset, (regime, cell) in enumerate(
            zip(TemporalRegime, cells, strict=True)
        )
    )
    checked = tuple(
        SimpleNamespace(regime=regime) for regime in TemporalRegime
    )
    audit_action_ids = tuple(
        (regime, f"{regime.value}-{offset}")
        for regime in TemporalRegime
        for offset in range(2)
    )
    audit_schedule = tuple(reversed(tuple(TemporalRegime)))
    sampled = tuple(
        SimpleNamespace(
            regime=regime,
            sampled=tuple(
                SimpleNamespace(proof=action_id)
                for bound_regime, action_id in audit_action_ids
                if bound_regime is regime
            ),
        )
        for regime in audit_schedule
    )
    sampled_checked = tuple(
        SimpleNamespace(regime=regime)
        for regime in audit_schedule
    )
    reference_actions = tuple(
        SimpleNamespace(
            regime=regime,
            score=SimpleNamespace(action_id=action_id),
            elapsed_seconds=0.1,
        )
        for regime in audit_schedule
        for bound_regime, action_id in audit_action_ids
        if bound_regime is regime
    )
    audited = tuple(
        SimpleNamespace(regime=regime) for regime in TemporalRegime
    )
    phase_results = iter(
        (generated, checked, sampled, sampled_checked, reference_actions)
    )
    operations = []
    phase_regimes = []

    def run_phase(operation, tasks, *, process_count):  # type: ignore[no-untyped-def]
        operations.append((operation.__name__, len(tasks), process_count))
        if operation.__name__ in {
            "_sample_audit_generator_worker",
            "_sample_audit_checker_worker",
            "_reference_audit_action_worker",
        }:
            phase_regimes.append(
                tuple(task[0].stream[0].regime for task in tasks)
            )
        return next(phase_results)

    audit = tuple(
        SimpleNamespace(regime=regime, catalog_action_id=action_id)
        for regime, action_id in audit_action_ids
    )
    monkeypatch.setattr(experiment, "_run_process_phase", run_phase)
    monkeypatch.setattr(experiment, "_freeze_preflight_audit", lambda values: audit)
    monkeypatch.setattr(
        experiment,
        "_audit_by_cell",
        lambda values: {
            regime: tuple(item for item in values if item.regime is regime)
            for regime in TemporalRegime
        },
    )
    monkeypatch.setattr(
        experiment,
        "_assemble_audit_results",
        lambda **kwargs: audited,
    )
    monkeypatch.setattr(
        experiment,
        "_assemble_timed_cell",
        lambda value, **kwargs: SimpleNamespace(regime=value.regime),
    )
    times = iter(
        (0.0, 0.0, 2.0, 2.0, 5.0, 5.0, 7.0, 7.0, 9.0, 9.0, 12.0, 13.0)
    )
    monkeypatch.setattr(experiment, "perf_counter", lambda: next(times))

    result = experiment._execute_distributed_cells(  # noqa: SLF001
        cells,
        rules=object(),
        jagua_executable=Path("jagua"),
        budget=M8_GATE3_CONCURRENCY_BUDGET,
    )

    assert operations == [
        ("_generate_cell_worker", 6, 4),
        ("_check_cell_worker", 6, 4),
        ("_sample_audit_generator_worker", 6, 4),
        ("_sample_audit_checker_worker", 6, 4),
        ("_reference_audit_action_worker", 12, 6),
    ]
    assert phase_regimes == [
        audit_schedule,
        audit_schedule,
        tuple(regime for regime in audit_schedule for _offset in range(2)),
    ]
    assert tuple(item.regime for item in result.cells) == tuple(TemporalRegime)
    assert result.generator_wall_seconds == 2.0
    assert result.checker_wall_seconds == 3.0
    assert result.audit_wall_seconds == 7.0
    assert result.total_wall_seconds == 13.0
    assert result.measured_process_count == 6
    assert result.cell_phase_process_count == 4
    assert result.translation_audit_processes_per_cell == 2
    assert result.reference_phase_process_count == 6
    assert result.peak_compute_count == 8


def test_nested_worker_entrypoints_activate_requested_audit_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import current_m8_translation_audit_processes

    cell = SimpleNamespace(stream=(SimpleNamespace(regime=tuple(TemporalRegime)[0]),))
    request = object()
    sparse = SimpleNamespace(proofs=(object(),))
    binding = SimpleNamespace(catalog_action_id="m7-standard:audit")
    seen: list[tuple[str, int | None]] = []

    monkeypatch.setattr(experiment, "_request_for_cell", lambda *args, **kwargs: request)
    monkeypatch.setattr(
        experiment,
        "_measure_proof_phase",
        lambda operation: (operation(), 0.1),
    )

    def sparse_score(_request):  # type: ignore[no-untyped-def]
        seen.append(("generator", current_m8_translation_audit_processes()))
        return sparse

    def check(_request, proofs):  # type: ignore[no-untyped-def]
        seen.append(("checker", current_m8_translation_audit_processes()))
        return tuple(SimpleNamespace(valid=True) for _proof in proofs)

    def sampled_score(_request, *, action_ids):  # type: ignore[no-untyped-def]
        seen.append(("audit_generator", current_m8_translation_audit_processes()))
        return tuple(
            SimpleNamespace(score=SimpleNamespace(action_id=action_id), proof=object())
            for action_id in action_ids
        )

    monkeypatch.setattr(experiment, "score_sparse_event", sparse_score)
    monkeypatch.setattr(experiment, "check_action_proofs", check)
    monkeypatch.setattr(experiment, "score_certificate_actions", sampled_score)

    experiment._generate_cell_worker(cell, object(), Path("jagua"), 2)  # noqa: SLF001
    experiment._check_cell_worker(  # noqa: SLF001
        cell,
        object(),
        Path("jagua"),
        sparse.proofs,
        2,
    )
    sampled = experiment._sample_audit_generator_worker(  # noqa: SLF001
        cell,
        object(),
        Path("jagua"),
        (binding,),
        2,
    )
    experiment._sample_audit_checker_worker(  # noqa: SLF001
        cell,
        object(),
        Path("jagua"),
        tuple(item.proof for item in sampled.sampled),
        2,
    )

    assert seen == [
        ("generator", 2),
        ("checker", 2),
        ("audit_generator", 2),
        ("checker", 2),
    ]
    assert current_m8_translation_audit_processes() is None


def test_gate3_v1_generator_worker_accepts_exact_action_id_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import current_m8_translation_audit_processes

    regime = tuple(TemporalRegime)[0]
    cell = SimpleNamespace(stream=(SimpleNamespace(regime=regime),))
    requested = []
    action_ids = ("m7-standard:first", "m7-remnant:second")
    monkeypatch.setattr(
        experiment,
        "_request_for_cell",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        experiment,
        "_measure_proof_phase",
        lambda operation: (operation(), 0.25),
    )

    def sampled_score(_request, *, action_ids):  # type: ignore[no-untyped-def]
        requested.append((action_ids, current_m8_translation_audit_processes()))
        return tuple(
            SimpleNamespace(score=SimpleNamespace(action_id=action_id), proof=object())
            for action_id in action_ids
        )

    monkeypatch.setattr(experiment, "score_certificate_actions", sampled_score)

    result = experiment._gate3_v1_generator_worker(  # noqa: SLF001
        cell,
        object(),
        Path("jagua"),
        action_ids,
        2,
    )

    assert requested == [(action_ids, 2)]
    assert result.regime is regime
    assert tuple(item.score.action_id for item in result.sampled) == action_ids
    assert result.elapsed_seconds == 0.25
    assert current_m8_translation_audit_processes() is None

    with pytest.raises(TypeError, match="exact tuple"):
        experiment._gate3_v1_generator_worker(  # noqa: SLF001
            cell,
            object(),
            Path("jagua"),
            list(action_ids),  # type: ignore[arg-type]
            2,
        )
    with pytest.raises(ValueError, match="unique nonempty"):
        experiment._gate3_v1_generator_worker(  # noqa: SLF001
            cell,
            object(),
            Path("jagua"),
            (action_ids[0], action_ids[0]),
            2,
        )


def test_reference_worker_has_no_nested_audit_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yieldforge.oracle import experiment
    from yieldforge.oracle.concurrency import current_m8_translation_audit_processes

    regime = tuple(TemporalRegime)[0]
    cell = SimpleNamespace(stream=(SimpleNamespace(regime=regime),))
    monkeypatch.setattr(
        experiment,
        "_request_for_cell",
        lambda *args, **kwargs: object(),
    )

    def reference_score(_request, *, action_id):  # type: ignore[no-untyped-def]
        assert current_m8_translation_audit_processes() is None
        return SimpleNamespace(action_id=action_id)

    monkeypatch.setattr(experiment, "score_reference_action", reference_score)
    monkeypatch.setattr(
        experiment,
        "_measure_proof_phase",
        lambda operation: (operation(), 0.1),
    )

    result = experiment._reference_audit_action_worker(  # noqa: SLF001
        cell,
        object(),
        Path("jagua"),
        "m7-standard:audit",
    )

    assert result.score.action_id == "m7-standard:audit"


def test_sparse_prefix_execution_has_no_public_worker_override() -> None:
    from yieldforge.oracle.experiment import execute_sparse_prefix_proof

    assert not {
        "process_count",
        "worker_count",
        "translation_audit_processes",
    } & set(inspect.signature(execute_sparse_prefix_proof).parameters)


@pytest.mark.parametrize("failure", ["missing", "duplicate"])
def test_distributed_audit_assembly_rejects_incomplete_reference_actions(
    failure: str,
) -> None:
    from yieldforge.oracle import experiment

    audit_by_cell = {
        regime: (
            SimpleNamespace(
                regime=regime,
                catalog_action_id=f"m7-standard:{regime.value}",
            ),
        )
        for regime in TemporalRegime
    }
    sampled = tuple(
        SimpleNamespace(
            regime=regime,
            sampled=(
                SimpleNamespace(
                    score=SimpleNamespace(
                        action_id=audit_by_cell[regime][0].catalog_action_id
                    )
                ),
            ),
            elapsed_seconds=0.1,
        )
        for regime in TemporalRegime
    )
    checked = tuple(
        SimpleNamespace(
            regime=regime,
            checks=(object(),),
            elapsed_seconds=0.1,
        )
        for regime in TemporalRegime
    )
    references = [
        SimpleNamespace(
            regime=regime,
            scores=(
                SimpleNamespace(
                    action_id=audit_by_cell[regime][0].catalog_action_id
                ),
            ),
            elapsed_seconds=0.1,
        )
        for regime in TemporalRegime
    ]
    if failure == "missing":
        references.pop()
    else:
        references[-1] = references[0]

    with pytest.raises(ValueError, match="reference actions"):
        experiment._assemble_audit_results(  # noqa: SLF001
            sampled=sampled,
            checked=checked,
            references=tuple(references),
            audit_by_cell=audit_by_cell,
        )


@pytest.mark.parametrize("failure", [None, "missing", "duplicate"])
def test_reference_action_assembly_reconciles_frozen_regime_batches(
    failure: str | None,
) -> None:
    from yieldforge.oracle import experiment

    regime = TemporalRegime.NO_SIGNAL
    action_ids = ("m7-standard:first", "m7-standard:second")
    audit_by_cell = {
        regime: tuple(
            SimpleNamespace(catalog_action_id=action_id)
            for action_id in action_ids
        )
    }
    first = SimpleNamespace(
        regime=regime,
        score=SimpleNamespace(action_id=action_ids[0]),
        elapsed_seconds=0.2,
    )
    second = SimpleNamespace(
        regime=regime,
        score=SimpleNamespace(action_id=action_ids[1]),
        elapsed_seconds=0.3,
    )
    results = [second, first]
    if failure == "missing":
        results.pop()
    elif failure == "duplicate":
        results[-1] = second

    if failure is not None:
        with pytest.raises(ValueError, match="reference actions"):
            experiment._assemble_reference_audit_actions(  # noqa: SLF001
                tuple(results),
                audit_by_cell=audit_by_cell,
            )
        return

    (assembled,) = experiment._assemble_reference_audit_actions(  # noqa: SLF001
        tuple(results),
        audit_by_cell=audit_by_cell,
    )
    assert assembled.regime is regime
    assert tuple(item.action_id for item in assembled.scores) == action_ids
    assert assembled.elapsed_seconds == 0.5


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
