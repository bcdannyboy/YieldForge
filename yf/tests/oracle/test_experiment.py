from __future__ import annotations

from pathlib import Path

import pytest

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
    assert result.sampled_speedup == 25.0
    assert result.projected_held_out_calendar_days <= 7.0
    assert result.evaluation_partition_opened is False
    assert result.technical_decision == "pass_certificate_exact"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("checked_action_count", 99),
        ("valid_proof_count", 99),
        ("checker_failure_count", 1),
        ("audit_mismatch_count", 1),
    ],
)
def test_certificate_gate_redesigns_for_each_exactness_failure(
    field: str,
    value: int,
) -> None:
    cells = _cells()
    cells = (cells[0].model_copy(update={field: value}), *cells[1:])

    assert _finalize(cells=cells).technical_decision == "redesign_certificate_proof"


def test_certificate_gate_redesigns_for_slow_matched_sample() -> None:
    cells = tuple(
        item.model_copy(update={"certificate_elapsed_seconds": 2.0})
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


def test_certificate_gate_requires_distributed_only_for_projection() -> None:
    cells = tuple(
        item.model_copy(
            update={
                "certificate_elapsed_seconds": 50.0,
                "sampled_reference_elapsed_seconds": 5000.0,
            }
        )
        for item in _cells()
    )

    result = _finalize(cells=cells)

    assert result.sampled_speedup >= 20.0
    assert result.projected_held_out_calendar_days > 7.0
    assert result.technical_decision == "require_distributed_exact"


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
