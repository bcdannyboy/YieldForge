"""Explicit future-information providers for M8."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from yieldforge.baseline.contracts import TemporalInstanceBinding


class FutureVisibility(Protocol):
    mode: str

    def visible_suffix(
        self, *, current_position: int
    ) -> tuple[TemporalInstanceBinding, ...]: ...


@dataclass(frozen=True)
class FullRealizedVisibility:
    """Expose the exact registered post-current M6 suffix."""

    stream: tuple[TemporalInstanceBinding, ...]
    mode: str = "full_realized_future"

    def visible_suffix(
        self, *, current_position: int
    ) -> tuple[TemporalInstanceBinding, ...]:
        if current_position < 0 or current_position >= len(self.stream):
            raise ValueError("M8 visibility position is outside the stream")
        return self.stream[current_position + 1 :]


@dataclass(frozen=True)
class KnownOnlyVisibility:
    """Expose only pre-release known work; M6 provides no such field."""

    stream: tuple[TemporalInstanceBinding, ...]
    mode: str = "known_only"

    def visible_suffix(
        self, *, current_position: int
    ) -> tuple[TemporalInstanceBinding, ...]:
        if current_position < 0 or current_position >= len(self.stream):
            raise ValueError("M8 visibility position is outside the stream")
        return ()


__all__ = ["FullRealizedVisibility", "FutureVisibility", "KnownOnlyVisibility"]
