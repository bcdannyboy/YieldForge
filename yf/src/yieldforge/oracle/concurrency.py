"""Internal active-compute budget for M8 certificate execution."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

_M8_FROZEN_MAX_COMPUTE_SLOTS = 8


def _require_positive_integer(value: object, *, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"M8 {field} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class M8ConcurrencyBudget:
    """Bound active compute while outer processes wait on nested audit work."""

    total_compute_slots: int = _M8_FROZEN_MAX_COMPUTE_SLOTS
    cell_phase_processes: int = 4
    translation_audit_processes_per_cell: int = 2
    reference_phase_processes: int = 6

    def __post_init__(self) -> None:
        for field in (
            "total_compute_slots",
            "cell_phase_processes",
            "translation_audit_processes_per_cell",
            "reference_phase_processes",
        ):
            _require_positive_integer(getattr(self, field), field=field)
        if self.total_compute_slots > _M8_FROZEN_MAX_COMPUTE_SLOTS:
            raise ValueError(
                "M8 total compute slots exceed the frozen maximum of "
                f"{_M8_FROZEN_MAX_COMPUTE_SLOTS}"
            )
        if self.peak_nested_compute > self.total_compute_slots:
            raise ValueError("M8 nested compute exceeds the total compute-slot budget")
        if self.reference_phase_processes > self.total_compute_slots:
            raise ValueError("M8 reference width exceeds the total compute-slot budget")

    @property
    def peak_nested_compute(self) -> int:
        return self.cell_phase_processes * self.translation_audit_processes_per_cell

    @property
    def peak_compute(self) -> int:
        return max(self.peak_nested_compute, self.reference_phase_processes)


M8_GATE3_CONCURRENCY_BUDGET = M8ConcurrencyBudget()
_M8_LOCAL_TRUSTED_AUDIT_PROCESSES = 4


_TRANSLATION_AUDIT_PROCESSES: ContextVar[int | None] = ContextVar(
    "m8_translation_audit_processes",
    default=None,
)


def current_m8_translation_audit_processes() -> int | None:
    """Return the audit width active in this process context, if configured."""

    return _TRANSLATION_AUDIT_PROCESSES.get()


def require_m8_translation_audit_processes() -> int:
    """Return the explicit audit width or fail closed when none is active."""

    process_count = current_m8_translation_audit_processes()
    if process_count is None:
        raise RuntimeError("M8 translation audit process count is not configured")
    return process_count


@contextmanager
def activate_m8_translation_audit_processes(process_count: int) -> Iterator[None]:
    """Activate one explicit audit width and restore the prior value on exit."""

    width = _require_positive_integer(process_count, field="translation audit width")
    token = _TRANSLATION_AUDIT_PROCESSES.set(width)
    try:
        yield
    finally:
        _TRANSLATION_AUDIT_PROCESSES.reset(token)


@contextmanager
def activate_m8_local_trusted_audit() -> Iterator[None]:
    """Give isolated trusted calls four workers unless an outer budget is active."""

    if current_m8_translation_audit_processes() is not None:
        yield
        return
    with activate_m8_translation_audit_processes(
        _M8_LOCAL_TRUSTED_AUDIT_PROCESSES
    ):
        yield


__all__ = [
    "M8_GATE3_CONCURRENCY_BUDGET",
    "M8ConcurrencyBudget",
    "activate_m8_local_trusted_audit",
    "activate_m8_translation_audit_processes",
    "current_m8_translation_audit_processes",
    "require_m8_translation_audit_processes",
]
