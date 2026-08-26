from __future__ import annotations

import pytest

from yieldforge.oracle.concurrency import (
    M8_GATE3_CONCURRENCY_BUDGET,
    M8ConcurrencyBudget,
    activate_m8_local_trusted_audit,
    activate_m8_translation_audit_processes,
    current_m8_translation_audit_processes,
    require_m8_translation_audit_processes,
)


def test_budget_freezes_eight_active_compute_slots() -> None:
    budget = M8ConcurrencyBudget(
        total_compute_slots=8,
        cell_phase_processes=4,
        translation_audit_processes_per_cell=2,
        reference_phase_processes=6,
    )

    assert budget.peak_nested_compute == 8
    assert budget.peak_compute == 8


def test_gate3_budget_uses_four_by_two_and_six_reference_workers() -> None:
    assert M8_GATE3_CONCURRENCY_BUDGET == M8ConcurrencyBudget()
    assert M8_GATE3_CONCURRENCY_BUDGET.cell_phase_processes == 4
    assert M8_GATE3_CONCURRENCY_BUDGET.translation_audit_processes_per_cell == 2
    assert M8_GATE3_CONCURRENCY_BUDGET.reference_phase_processes == 6


@pytest.mark.parametrize(
    "field",
    (
        "total_compute_slots",
        "cell_phase_processes",
        "translation_audit_processes_per_cell",
        "reference_phase_processes",
    ),
)
@pytest.mark.parametrize("value", (False, True, 0))
def test_budget_rejects_boolean_and_zero_widths(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        M8ConcurrencyBudget(**{field: value})


def test_budget_rejects_nested_product_above_total_slots() -> None:
    with pytest.raises(ValueError, match="nested compute"):
        M8ConcurrencyBudget(
            total_compute_slots=8,
            cell_phase_processes=5,
            translation_audit_processes_per_cell=2,
            reference_phase_processes=6,
        )


def test_budget_rejects_reference_width_above_total_slots() -> None:
    with pytest.raises(ValueError, match="reference width"):
        M8ConcurrencyBudget(
            total_compute_slots=8,
            cell_phase_processes=4,
            translation_audit_processes_per_cell=2,
            reference_phase_processes=9,
        )


def test_translation_audit_context_restores_prior_width_after_success() -> None:
    assert current_m8_translation_audit_processes() is None
    with activate_m8_translation_audit_processes(4):
        assert current_m8_translation_audit_processes() == 4
        with activate_m8_translation_audit_processes(2):
            assert current_m8_translation_audit_processes() == 2
        assert current_m8_translation_audit_processes() == 4
    assert current_m8_translation_audit_processes() is None


def test_translation_audit_context_restores_prior_width_after_failure() -> None:
    assert current_m8_translation_audit_processes() is None
    with pytest.raises(RuntimeError, match="synthetic nested failure"):
        with activate_m8_translation_audit_processes(2):
            assert current_m8_translation_audit_processes() == 2
            raise RuntimeError("synthetic nested failure")
    assert current_m8_translation_audit_processes() is None


def test_local_trusted_audit_activates_four_without_overriding_outer_width() -> None:
    with activate_m8_local_trusted_audit():
        assert require_m8_translation_audit_processes() == 4
        with activate_m8_translation_audit_processes(2):
            with activate_m8_local_trusted_audit():
                assert require_m8_translation_audit_processes() == 2
        assert require_m8_translation_audit_processes() == 4
    assert current_m8_translation_audit_processes() is None


def test_translation_audit_width_must_be_configured_before_use() -> None:
    with pytest.raises(RuntimeError, match="not configured"):
        require_m8_translation_audit_processes()


@pytest.mark.parametrize("value", (False, True, 0))
def test_translation_audit_context_rejects_nonpositive_or_boolean_width(
    value: object,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        with activate_m8_translation_audit_processes(value):  # type: ignore[arg-type]
            pass
