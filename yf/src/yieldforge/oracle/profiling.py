"""Low-overhead scoped phase profiling for calibration-only M8 experiments."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter_ns as _perf_counter_ns
from time import process_time_ns as _process_time_ns
from typing import Literal

M8ProfileCounter = Literal[
    "events",
    "candidates",
    "frontier_entries",
    "actions",
    "facts",
    "fallbacks",
]

_COUNTER_NAMES: tuple[M8ProfileCounter, ...] = (
    "events",
    "candidates",
    "frontier_entries",
    "actions",
    "facts",
    "fallbacks",
)
_PHASE_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class M8ProfilePhase:
    """One inclusive process/wall timing node in a nested phase tree."""

    name: str
    process_ns: int
    wall_ns: int
    children: tuple[M8ProfilePhase, ...] = ()

    def __post_init__(self) -> None:
        if not _PHASE_NAME.fullmatch(self.name):
            raise ValueError("M8 profile phase name must be non-empty snake-case")
        if self.process_ns < 0 or self.wall_ns < 0:
            raise ValueError("M8 profile duration cannot be negative")

    def timed_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "process_ns": self.process_ns,
            "wall_ns": self.wall_ns,
            "children": [child.timed_payload() for child in self.children],
        }

    def normalized_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "children": [child.normalized_payload() for child in self.children],
        }


@dataclass(frozen=True, slots=True)
class M8ProfileReport:
    """Serializable profile evidence with timing-free semantic normalization."""

    total_process_ns: int
    total_wall_ns: int
    phases: tuple[M8ProfilePhase, ...]
    _counts: tuple[tuple[M8ProfileCounter, int], ...]
    schema_version: str = "yieldforge.m8-phase-profile.v1"

    def __post_init__(self) -> None:
        if self.total_process_ns < 0 or self.total_wall_ns < 0:
            raise ValueError("M8 profile total duration cannot be negative")
        if tuple(name for name, _value in self._counts) != _COUNTER_NAMES:
            raise ValueError("M8 profile counts do not use the frozen counter set")
        if any(type(value) is not int or value < 0 for _name, value in self._counts):
            raise ValueError("M8 profile counts must be non-negative exact integers")

    @property
    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    @property
    def accounted_process_ns(self) -> int:
        return sum(phase.process_ns for phase in self.phases)

    @property
    def accounted_process_fraction(self) -> float:
        if self.total_process_ns == 0:
            return 1.0 if self.accounted_process_ns == 0 else 0.0
        return min(1.0, self.accounted_process_ns / self.total_process_ns)

    def model_dump(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "total_process_ns": self.total_process_ns,
            "total_wall_ns": self.total_wall_ns,
            "accounted_process_ns": self.accounted_process_ns,
            "accounted_process_fraction": self.accounted_process_fraction,
            "counts": self.counts,
            "phases": [phase.timed_payload() for phase in self.phases],
        }

    def model_dump_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.model_dump(),
            indent=indent,
            sort_keys=True,
            separators=None if indent is not None else (",", ":"),
        )

    def normalized_json(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "counts": self.counts,
                "phase_tree": [phase.normalized_payload() for phase in self.phases],
            },
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(slots=True)
class _OpenPhase:
    name: str
    process_started_ns: int
    wall_started_ns: int
    children: list[M8ProfilePhase] = field(default_factory=list)


class M8PhaseProfiler:
    """One process-local profiler activated only around an explicit M8 operation."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._counts: dict[M8ProfileCounter, int] = dict.fromkeys(_COUNTER_NAMES, 0)
        self._phases: list[M8ProfilePhase] = []
        self._stack: list[_OpenPhase] = []
        self._started = False
        self._finished = False
        self._process_started_ns = 0
        self._wall_started_ns = 0
        self._total_process_ns = 0
        self._total_wall_ns = 0

    def _start(self) -> None:
        if self._started:
            raise RuntimeError("M8 profiler cannot be activated twice")
        self._started = True
        if self.enabled:
            self._process_started_ns = _process_time_ns()
            self._wall_started_ns = _perf_counter_ns()

    def _finish(self) -> None:
        if not self._started or self._finished:
            raise RuntimeError("M8 profiler activation lifecycle differs")
        if self._stack:
            raise RuntimeError("M8 profiler finished with an open phase")
        if self.enabled:
            self._total_process_ns = max(
                0,
                _process_time_ns() - self._process_started_ns,
            )
            self._total_wall_ns = max(0, _perf_counter_ns() - self._wall_started_ns)
        self._finished = True

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        if not isinstance(name, str) or not _PHASE_NAME.fullmatch(name):
            raise ValueError("M8 profile phase name must be non-empty snake-case")
        if any(open_phase.name == name for open_phase in self._stack):
            raise ValueError("M8 profile phase cannot contain itself")
        opened = _OpenPhase(
            name=name,
            process_started_ns=_process_time_ns(),
            wall_started_ns=_perf_counter_ns(),
        )
        self._stack.append(opened)
        try:
            yield
        finally:
            if not self._stack or self._stack[-1] is not opened:
                raise RuntimeError("M8 profile phases closed out of order")
            process_ns = max(0, _process_time_ns() - opened.process_started_ns)
            wall_ns = max(0, _perf_counter_ns() - opened.wall_started_ns)
            self._stack.pop()
            phase = M8ProfilePhase(
                name=opened.name,
                process_ns=process_ns,
                wall_ns=wall_ns,
                children=tuple(opened.children),
            )
            if self._stack:
                self._stack[-1].children.append(phase)
            else:
                self._phases.append(phase)

    def increment(self, name: M8ProfileCounter, amount: int = 1) -> None:
        if not self.enabled:
            return
        if name not in self._counts:
            raise ValueError(f"unknown M8 profile counter: {name}")
        if type(amount) is not int:
            raise TypeError("M8 profile count amount must be an exact integer")
        if amount < 0:
            raise ValueError("M8 profile count amount must be a non-negative integer")
        self._counts[name] += amount

    def report(self) -> M8ProfileReport:
        if not self._finished:
            raise RuntimeError("M8 profile report is unavailable before activation ends")
        return M8ProfileReport(
            total_process_ns=self._total_process_ns,
            total_wall_ns=self._total_wall_ns,
            phases=tuple(self._phases),
            _counts=tuple((name, self._counts[name]) for name in _COUNTER_NAMES),
        )


_ACTIVE_PROFILER: ContextVar[M8PhaseProfiler | None] = ContextVar(
    "yieldforge_m8_active_profiler",
    default=None,
)


@contextmanager
def activate_m8_profile(*, enabled: bool = True) -> Iterator[M8PhaseProfiler]:
    """Activate one profiler for instrumented calls in the current context."""

    if _ACTIVE_PROFILER.get() is not None:
        raise RuntimeError("M8 profiling sessions cannot be nested")
    profiler = M8PhaseProfiler(enabled=enabled)
    profiler._start()  # noqa: SLF001 - lifecycle owned by this context manager.
    token = _ACTIVE_PROFILER.set(profiler)
    try:
        yield profiler
    finally:
        _ACTIVE_PROFILER.reset(token)
        profiler._finish()  # noqa: SLF001


@contextmanager
def profile_phase(name: str) -> Iterator[None]:
    """Record a named phase only when a profile is currently active."""

    profiler = _ACTIVE_PROFILER.get()
    if profiler is None:
        yield
        return
    with profiler.phase(name):
        yield


def increment_profile_count(name: M8ProfileCounter, amount: int = 1) -> None:
    """Increment one frozen exact count when a profile is active."""

    profiler = _ACTIVE_PROFILER.get()
    if profiler is not None:
        profiler.increment(name, amount)


__all__ = [
    "M8PhaseProfiler",
    "M8ProfileCounter",
    "M8ProfilePhase",
    "M8ProfileReport",
    "activate_m8_profile",
    "increment_profile_count",
    "profile_phase",
]
