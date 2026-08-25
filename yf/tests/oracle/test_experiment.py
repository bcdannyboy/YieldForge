from __future__ import annotations

from yieldforge.temporal_benchmark.contracts import TemporalRegime


def _cells(*, mismatch: int = 0):  # type: ignore[no-untyped-def]
    from yieldforge.oracle.experiment import M8SparseProofCell

    return tuple(
        M8SparseProofCell(
            regime=regime,
            temporal_seed=2026082300,
            stream_id="yfts-" + f"{offset + 1:x}" * 24,
            prefix_event_count=2,
            current_action_count=100,
            reference_continuation_event_count=100,
            sparse_common_continuation_event_count=1,
            sparse_exact_branch_event_count=0,
            sparse_skipped_passive_event_count=99,
            rejection_certificate_count=100,
            survivor_pair_count=0,
            state_rejoin_count=0,
            reference_elapsed_seconds=2.0,
            sparse_elapsed_seconds=0.05,
            semantic_mismatch_count=mismatch if offset == 0 else 0,
        )
        for offset, regime in enumerate(TemporalRegime)
    )


def test_sparse_proof_passes_only_all_cells_zero_mismatch_20x_and_seven_days() -> None:
    from yieldforge.oracle.experiment import finalize_sparse_proof

    result = finalize_sparse_proof(
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
        cells=_cells(),
    )

    assert result.semantic_mismatch_count == 0
    assert result.end_to_end_speedup == 40.0
    assert result.projected_held_out_calendar_days <= 7.0
    assert result.technical_decision == "pass_sparse_exact"


def test_sparse_proof_mismatch_fails_closed() -> None:
    from yieldforge.oracle.experiment import finalize_sparse_proof

    result = finalize_sparse_proof(
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
        cells=_cells(mismatch=1),
    )
    assert result.technical_decision == "redesign_sparse_exact"
