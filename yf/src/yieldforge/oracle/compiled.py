"""Compiled inventory-independent M7 winners and future relevance helpers."""

from __future__ import annotations

from dataclasses import dataclass

from yieldforge.baseline.replay import (
    M7ReplayCursor,
    M7ReplayRuntime,
    enumerate_m7_action_catalog,
    select_m7_fallback,
)
from yieldforge.replay.contracts import ReplayCostLedger


@dataclass(frozen=True)
class CompiledStandardWinner:
    problem_id: str
    problem_sha256: str
    candidate_set_id: str
    candidate_set_sha256: str
    action_id: str
    candidate_id: str
    decision_key: tuple[str, ...]


def compile_standard_winner(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> CompiledStandardWinner:
    """Compile the exact frozen-policy standard winner for one problem occurrence."""

    if event_position < 0 or event_position >= len(runtime.replay_input.instances):
        raise ValueError("M8 standard-winner event position is outside the stream")
    binding = runtime.replay_input.instances[event_position]
    cursor = M7ReplayCursor(
        next_event_position=event_position,
        current_time=binding.released_at,
        inventory=(),
        cumulative_costs=ReplayCostLedger.zero(),
        timestamp_group_sequence=-1,
        timestamp_subsequence=0,
        previous_release=None,
    )
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor)
    if catalog.remnant_action_count != 0:
        raise ValueError("M8 standard compilation unexpectedly observed remnant actions")
    selection = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
    descriptor = next(item for item in catalog.actions if item.action_id == selection.action_id)
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    evidence = runtime.runtime_candidates[binding.problem_id].evidence
    return CompiledStandardWinner(
        problem_id=problem.problem_id,
        problem_sha256=problem.content_sha256,
        candidate_set_id=evidence.candidate_set_id,
        candidate_set_sha256=evidence.content_sha256,
        action_id=descriptor.action_id,
        candidate_id=descriptor.candidate_id,
        decision_key=selection.decision_key,
    )


__all__ = ["CompiledStandardWinner", "compile_standard_winner"]
