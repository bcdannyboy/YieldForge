"""Sparse exact delta evaluator for M8 current-action rollouts."""

from __future__ import annotations

from dataclasses import dataclass

from yieldforge.baseline.geometry import (
    PreparedLayoutFootprint,
    certify_translation_impossible,
    prepare_layout_footprint,
)
from yieldforge.baseline.replay import (
    M7ReplayRuntime,
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    run_m7_continuation,
    select_m7_fallback,
)
from yieldforge.oracle.contracts import M8OracleDecision, build_oracle_decision
from yieldforge.oracle.reference import M8OracleRequest
from yieldforge.replay.contracts import InventoryItem, rounded_cost
from yieldforge.reuse.contracts import polygon_from_record


@dataclass(frozen=True)
class M8SparseMetrics:
    common_continuation_event_count: int
    exact_branch_event_count: int
    skipped_passive_event_count: int
    rejection_certificate_count: int
    survivor_pair_count: int
    state_rejoin_count: int


@dataclass(frozen=True)
class M8SparseResult:
    decision: M8OracleDecision
    metrics: M8SparseMetrics


def _isolated_runtime(source: M7ReplayRuntime) -> M7ReplayRuntime:
    return M7ReplayRuntime(
        replay_input=source.replay_input,
        runtime_candidates=source.runtime_candidates,
        rules=source.rules,
        runtime_metrics=source.runtime_metrics,
        standard_profile_executor=source.standard_profile_executor,
        jagua_executable=source.jagua_executable,
        jagua_differential_audit=source.jagua_differential_audit,
    )


def _area(items: tuple[InventoryItem, ...]) -> float:
    return sum(polygon_from_record(item.remnant.geometry).area for item in items)


def _apply_inventory_delta(
    common: tuple[InventoryItem, ...],
    *,
    removed: tuple[InventoryItem, ...],
    added: tuple[InventoryItem, ...],
) -> tuple[InventoryItem, ...]:
    by_id = {item.remnant.remnant_id: item for item in common}
    for item in removed:
        if item.remnant.remnant_id not in by_id:
            raise ValueError("M8 passive delta lost a removed common remnant")
        del by_id[item.remnant.remnant_id]
    for item in added:
        existing = by_id.get(item.remnant.remnant_id)
        if existing is not None and existing != item:
            raise ValueError("M8 passive delta created a remnant identity conflict")
        by_id[item.remnant.remnant_id] = item
    return tuple(sorted(by_id.values(), key=lambda item: item.remnant.remnant_id))


def _passive_score(
    request: M8OracleRequest,
    *,
    branch_cursor,  # type: ignore[no-untyped-def]
    fallback_cursor,  # type: ignore[no-untyped-def]
    common_continuation,  # type: ignore[no-untyped-def]
    visible,  # type: ignore[no-untyped-def]
    layout_cache: dict[tuple[str, str], PreparedLayoutFootprint],
) -> tuple[float | None, int, int]:
    common_by_id = {item.remnant.remnant_id: item for item in fallback_cursor.inventory}
    branch_by_id = {item.remnant.remnant_id: item for item in branch_cursor.inventory}
    removed = tuple(common_by_id[key] for key in sorted(common_by_id.keys() - branch_by_id))
    added = tuple(branch_by_id[key] for key in sorted(branch_by_id.keys() - common_by_id))
    shared_ids = common_by_id.keys() & branch_by_id
    if any(common_by_id[key] != branch_by_id[key] for key in shared_ids):
        return None, 0, 0
    selected_future = {
        event.action.selected_remnant_id
        for event in common_continuation.events
        if event.action.selected_remnant_id is not None
    }
    if selected_future & {item.remnant.remnant_id for item in removed}:
        return None, 0, 0

    certificate_count = 0
    survivor_count = 0
    replay_input = request.runtime.replay_input
    problem_by_id = {item.problem_id: item for item in replay_input.problems}
    for item in added:
        for binding in visible:
            problem = problem_by_id[binding.problem_id]
            verified = request.runtime.runtime_candidates[binding.problem_id]
            for candidate in verified.candidates:
                key = (problem.problem_id, candidate.candidate_id)
                layout = layout_cache.get(key)
                if layout is None:
                    layout = prepare_layout_footprint(
                        problem.problem,
                        candidate,
                        replay_input.fit_config,
                    )
                    layout_cache[key] = layout
                certificate = certify_translation_impossible(
                    layout,
                    item.remnant,
                    material=binding.material,
                    fit_config=replay_input.fit_config,
                )
                certificate_count += 1
                if not certificate.impossible:
                    survivor_count += 1
                    return None, certificate_count, survivor_count

    fields = (
        "purchase_cost",
        "storage_cost",
        "return_handling_cost",
        "retrieval_handling_cost",
        "scrap_proceeds",
        "terminal_scrap_credit",
    )
    totals = {
        field: rounded_cost(
            getattr(common_continuation.final_costs, field)
            + getattr(branch_cursor.cumulative_costs, field)
            - getattr(fallback_cursor.cumulative_costs, field)
        )
        for field in fields
    }
    rate = replay_input.rates.storage_cost_per_area_hour
    for event in common_continuation.events:
        branch_inventory = _apply_inventory_delta(
            event.inventory_before,
            removed=removed,
            added=added,
        )
        hours = (
            event.storage_interval_end - event.storage_interval_start
        ).total_seconds() / 3600.0
        branch_storage = rounded_cost(_area(branch_inventory) * hours * rate)
        totals["storage_cost"] = rounded_cost(
            totals["storage_cost"]
            + branch_storage
            - event.delta_costs.storage_cost
        )
    common_terminal = common_continuation.terminal
    branch_terminal_inventory = _apply_inventory_delta(
        common_terminal.inventory_before_liquidation,
        removed=removed,
        added=added,
    )
    terminal_hours = (
        common_terminal.horizon_end - common_terminal.storage_interval_start
    ).total_seconds() / 3600.0
    branch_terminal_storage = rounded_cost(
        _area(branch_terminal_inventory) * terminal_hours * rate
    )
    totals["storage_cost"] = rounded_cost(
        totals["storage_cost"]
        + branch_terminal_storage
        - common_terminal.delta_costs.storage_cost
    )
    branch_terminal_credit = rounded_cost(
        _area(branch_terminal_inventory) * replay_input.rates.scrap_credit_per_area
    )
    totals["terminal_scrap_credit"] = rounded_cost(
        totals["terminal_scrap_credit"]
        + branch_terminal_credit
        - common_terminal.delta_costs.terminal_scrap_credit
    )
    net_cost = rounded_cost(
        totals["purchase_cost"]
        + totals["storage_cost"]
        + totals["return_handling_cost"]
        + totals["retrieval_handling_cost"]
        - totals["scrap_proceeds"]
        - totals["terminal_scrap_credit"]
    )
    return net_cost, certificate_count, survivor_count


def score_sparse_event(request: M8OracleRequest) -> M8SparseResult:
    """Score exactly, skipping only future intervals proven branch-passive."""

    catalog = enumerate_m7_action_catalog(request.runtime, cursor=request.cursor)
    fallback = select_m7_fallback(catalog, policy=request.runtime.replay_input.policy)
    fallback_descriptor = next(
        item for item in catalog.actions if item.action_id == fallback.action_id
    )
    fallback_step = apply_m7_action_descriptor(
        request.runtime,
        cursor=request.cursor,
        catalog=catalog,
        descriptor=fallback_descriptor,
        decision_key=fallback.decision_key,
    )
    visible = request.visibility.visible_suffix(current_position=catalog.event_position)
    registered = request.runtime.replay_input.instances
    expected = registered[
        catalog.event_position + 1 : catalog.event_position + 1 + len(visible)
    ]
    if visible != expected:
        raise ValueError("M8 visibility provider returned a non-prefix or mutated suffix")
    stop = catalog.event_position + 1 + len(visible)
    common = run_m7_continuation(
        _isolated_runtime(request.runtime),
        cursor=fallback_step.cursor,
        stop_event_position=stop,
    )
    scores = []
    exact_branch_events = 0
    skipped_events = 0
    certificate_count = 0
    survivor_count = 0
    rejoin_count = 0
    layout_cache: dict[tuple[str, str], PreparedLayoutFootprint] = {}
    for descriptor in catalog.actions:
        if descriptor.action_id == fallback.action_id:
            score = common.final_costs.net_cost
        else:
            step = apply_m7_action_descriptor(
                request.runtime,
                cursor=request.cursor,
                catalog=catalog,
                descriptor=descriptor,
                decision_key=(f"m8_hypothetical_action_id={descriptor.action_id}",),
            )
            if step.cursor == fallback_step.cursor:
                score = common.final_costs.net_cost
                rejoin_count += 1
            else:
                passive, certificates, survivors = _passive_score(
                    request,
                    branch_cursor=step.cursor,
                    fallback_cursor=fallback_step.cursor,
                    common_continuation=common,
                    visible=visible,
                    layout_cache=layout_cache,
                )
                certificate_count += certificates
                survivor_count += survivors
                if passive is not None:
                    score = passive
                    skipped_events += len(visible)
                else:
                    continuation = run_m7_continuation(
                        _isolated_runtime(request.runtime),
                        cursor=step.cursor,
                        stop_event_position=stop,
                    )
                    score = continuation.final_costs.net_cost
                    exact_branch_events += len(continuation.events)
        scores.append((descriptor.action_id, score))
    decision = build_oracle_decision(
        baseline_action_id=fallback.action_id,
        expected_action_ids=tuple(item.action_id for item in catalog.actions),
        scores=tuple(scores),
    )
    return M8SparseResult(
        decision=decision,
        metrics=M8SparseMetrics(
            common_continuation_event_count=len(common.events),
            exact_branch_event_count=exact_branch_events,
            skipped_passive_event_count=skipped_events,
            rejection_certificate_count=certificate_count,
            survivor_pair_count=survivor_count,
            state_rejoin_count=rejoin_count,
        ),
    )


__all__ = ["M8SparseMetrics", "M8SparseResult", "score_sparse_event"]
