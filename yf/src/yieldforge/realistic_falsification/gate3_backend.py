"""Authenticated low-level runtime execution for M11 Gate 3.

This module deliberately stops below the confirmation-backend Protocol.  It
turns authenticated adapter projections into exact M7/M9 decision evidence;
the runner-facing orchestration layer owns population selection and artifact
publication.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import product
from typing import Any, Literal

from yieldforge.baseline.contracts import M7ActionKind, TemporalInstanceBinding
from yieldforge.baseline.policies import M7PolicyIdentity, M7PolicyName, policy_identity
from yieldforge.baseline.replay import (
    M7ActionCatalog,
    M7ContinuationResult,
    M7ReplayCursor,
    M7ReplayResult,
    M7ReplayRuntime,
    M7ReplaySummary,
    M7StepResult,
    apply_m7_action_descriptor,
    build_m7_replay_input,
    enumerate_m7_action_catalog,
    initial_m7_cursor,
    m7_semantic_runtime_sha256,
    run_m7_continuation,
    select_m7_fallback,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle.search_validation import M9TwoPlyResult, score_two_ply_reoptimization
from yieldforge.oracle.visibility import FullRealizedVisibility
from yieldforge.realistic_falsification.adapter import (
    M11MaterialRuntimeProjection,
)
from yieldforge.realistic_falsification.confirmation import (
    Gate3Arm,
    Gate3ArmTrace,
    Gate3BaselinePolicyId,
    Gate3CorpusId,
    Gate3CostLedger,
    Gate3DecisionTrace,
    Gate3RootBinding,
    Gate3ShardTrace,
    build_gate3_cost_ledger,
    build_gate3_decision_trace,
    build_gate3_shard_trace,
    merge_gate3_material_shards,
)
from yieldforge.realistic_falsification.confirmation import (
    gate3_inventory_sha256 as _confirmation_inventory_sha256,
)
from yieldforge.replay.contracts import InventoryItem, ReplayCostLedger

_POLICY_MAP: dict[str, M7PolicyName] = {
    "myopic_geometry": M7PolicyName.MYOPIC_GEOMETRY,
    "remnant_first": M7PolicyName.REMNANT_FIRST,
    "net_cost": M7PolicyName.NET_COST,
    "age_regularity": M7PolicyName.AGE_REGULARITY,
    "known_order_lookahead": M7PolicyName.KNOWN_ORDER_LOOKAHEAD,
    # M9 repair froze continuation to the winning M7 baseline.
    "known_only_m9_two_ply_scrap": M7PolicyName.AGE_REGULARITY,
}
_COST_QUANTUM = Decimal("0.000001")


class Gate3BackendEvidenceError(ValueError):
    """Authenticated Gate 3 runtime evidence failed closed."""


@dataclass(frozen=True, slots=True)
class Gate3MaterialExecution:
    """One material shard plus raw transitions needed by confirmation packaging."""

    shard_trace: Gate3ShardTrace
    steps: tuple[M7StepResult, ...]
    terminal: M7ContinuationResult
    m7_replay_result: M7ReplayResult | None

    def __post_init__(self) -> None:
        if self.shard_trace.final_costs != gate3_ledger_from_replay(self.terminal.final_costs):
            raise Gate3BackendEvidenceError(
                "Gate 3 material execution ledger differs from its terminal"
            )
        if len(self.steps) != len(self.shard_trace.decisions):
            raise Gate3BackendEvidenceError(
                "Gate 3 material execution transitions differ from its decisions"
            )


@dataclass(frozen=True, slots=True)
class _Gate3SearchRequest:
    runtime: M7ReplayRuntime
    cursor: M7ReplayCursor
    visibility: FullRealizedVisibility


def _sha256(payload: Any) -> str:
    return f"sha256:{semantic_sha256(payload)}"


def _six_place(value: float) -> str:
    return format(
        Decimal(str(value)).quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP),
        ".6f",
    )


def gate3_policy_identity(policy_id: str) -> M7PolicyIdentity:
    """Resolve one frozen Gate 3 policy ID to its concrete M7 continuation."""

    try:
        name = _POLICY_MAP[policy_id]
    except KeyError as error:
        raise ValueError("unregistered Gate 3 baseline policy") from error
    return policy_identity(name)


def gate3_ledger_from_replay(ledger: ReplayCostLedger) -> Gate3CostLedger:
    """Convert one exact M0 replay ledger without changing any cost term."""

    return build_gate3_cost_ledger(
        purchase_cost=_six_place(ledger.purchase_cost),
        storage_cost=_six_place(ledger.storage_cost),
        return_handling_cost=_six_place(ledger.return_handling_cost),
        retrieval_handling_cost=_six_place(ledger.retrieval_handling_cost),
        scrap_proceeds=_six_place(ledger.scrap_proceeds),
        terminal_credit=_six_place(ledger.terminal_scrap_credit),
    )


def gate3_inventory_sha256(inventory: tuple[InventoryItem, ...]) -> str:
    """Hash a canonical inventory independently of tuple construction order."""

    ordered = tuple(sorted(inventory, key=lambda item: item.remnant.remnant_id))
    if len({item.remnant.remnant_id for item in ordered}) != len(ordered):
        raise ValueError("Gate 3 inventory contains duplicate remnant identities")
    return _confirmation_inventory_sha256(ordered)


def gate3_search_config_sha256(runtime: M7ReplayRuntime) -> str:
    """Bind the exact fit/search/collision configuration used by a decision."""

    replay = runtime.replay_input
    return _sha256(
        {
            "fit_config": replay.fit_config.model_dump(mode="json"),
            "search_config": replay.search_config.model_dump(mode="json"),
            "collision_backend": replay.collision_backend,
            "jagua_container_guard": replay.jagua_container_guard,
        }
    )


def gate3_compute_budget_sha256(runtime: M7ReplayRuntime) -> str:
    """Bind the common complete-catalog fixed-depth-two compute contract."""

    replay = runtime.replay_input
    return _sha256(
        {
            "catalog_requirement": "complete_no_truncation",
            "depth": 2,
            "objective": "scrap_only",
            "tie_rule": "bounded_cost_then_baseline_then_action_id",
            "fit_config": replay.fit_config.model_dump(mode="json"),
            "search_config": replay.search_config.model_dump(mode="json"),
            "collision_backend": replay.collision_backend,
            "jagua_container_guard": replay.jagua_container_guard,
        }
    )


def gate3_action_catalog_sha256(
    *,
    runtime: M7ReplayRuntime,
    cursor: M7ReplayCursor,
    catalog: M7ActionCatalog,
) -> str:
    """Hash one complete current-state catalog and all policy-visible terms."""

    if catalog.event_position != cursor.next_event_position:
        raise ValueError("Gate 3 action catalog position differs from its cursor")
    if len(catalog.actions) != catalog.standard_action_count + catalog.remnant_action_count:
        raise ValueError("Gate 3 action catalog count does not reconcile")
    if catalog.generated.fit_search_budget_truncated_count:
        raise ValueError("Gate 3 requires a complete untruncated action catalog")
    action_ids = tuple(item.action_id for item in catalog.actions)
    context_ids = tuple(item.action_id for item in catalog.contexts)
    if len(set(action_ids)) != len(action_ids) or context_ids != action_ids:
        raise ValueError("Gate 3 action catalog and policy contexts differ")
    binding = runtime.replay_input.instances[catalog.event_position]
    candidate_set = next(
        item
        for item in runtime.replay_input.candidate_sets
        if item.problem_id == binding.problem_id
    )
    return _sha256(
        {
            "event_position": catalog.event_position,
            "inventory_before_sha256": gate3_inventory_sha256(cursor.inventory),
            "candidate_set_id": candidate_set.candidate_set_id,
            "candidate_set_content_sha256": candidate_set.content_sha256,
            "actions": tuple(
                {
                    "action_id": item.action_id,
                    "kind": item.kind.value,
                    "candidate_id": item.candidate_id,
                    "selected_remnant_id": item.selected_remnant_id,
                    "evidence_action_id": (
                        item.evidence.action_id if item.evidence is not None else None
                    ),
                    "evidence_content_sha256": (
                        item.evidence.content_sha256 if item.evidence is not None else None
                    ),
                }
                for item in catalog.actions
            ),
            "policy_contexts": tuple(
                {
                    "action_id": item.action_id,
                    "kind": item.kind.value,
                    "candidate_id": item.candidate_id,
                    "candidate_width": item.candidate_width,
                    "selected_stock_id": item.selected_stock_id,
                    "immediate_net_cost": item.immediate_net_cost,
                    "selected_remnant_age_hours": item.selected_remnant_age_hours,
                    "returned_regularity": item.returned_regularity,
                    "known_order_lookahead_term": item.known_order_lookahead_term,
                }
                for item in catalog.contexts
            ),
            "standard_profiles": tuple(
                {
                    "candidate_id": item.candidate_id,
                    "candidate_width": item.candidate_width,
                    "accounting": item.accounting.model_dump(mode="json"),
                    "returned_remnant_count": item.returned_remnant_count,
                    "returned_regularity": item.returned_regularity,
                }
                for item in catalog.generated.standard_profiles
            ),
            "standard_action_count": catalog.standard_action_count,
            "remnant_action_count": catalog.remnant_action_count,
            "storage_cost": catalog.storage_cost,
            "timestamp_group_sequence": catalog.timestamp_group_sequence,
            "timestamp_subsequence": catalog.timestamp_subsequence,
            "fit_search_query_count": catalog.generated.fit_search_query_count,
            "fit_search_generated_candidate_count": (
                catalog.generated.fit_search_generated_candidate_count
            ),
            "fit_search_evaluated_candidate_count": (
                catalog.generated.fit_search_evaluated_candidate_count
            ),
            "fit_search_budget_truncated_count": (
                catalog.generated.fit_search_budget_truncated_count
            ),
        }
    )


def _require_projection_authority(
    projection: M11MaterialRuntimeProjection,
    *,
    roots: Gate3RootBinding | None = None,
    policy_id: str | None = None,
) -> None:
    # Re-run the adapter boundary because runtime dictionaries and caches are
    # mutable even though the projection dataclass itself is frozen.
    try:
        attestation = type(projection.attestation).model_validate(
            projection.attestation.model_dump(mode="python", round_trip=True),
            strict=True,
        )
        if attestation != projection.attestation:
            raise ValueError("detached adapter attestation differs")
        M11MaterialRuntimeProjection(
            attestation=projection.attestation,
            runtime=projection.runtime,
        )
    except (TypeError, ValueError) as error:
        raise Gate3BackendEvidenceError(
            "Gate 3 projection differs from its authenticated adapter attestation"
        ) from error
    replay = projection.runtime.replay_input
    source_map = projection.source_event_map
    parity = projection.candidate_action_parity
    if (
        len(source_map) != len(replay.instances)
        or len(parity) != len(replay.instances)
        or tuple(item.local_event_position for item in source_map) != tuple(range(len(source_map)))
        or tuple(item.compatibility_event_id for item in source_map)
        != tuple(item.event_id for item in replay.instances)
        or tuple(item.source_event_position for item in source_map)
        != tuple(sorted({item.source_event_position for item in source_map}))
    ):
        raise Gate3BackendEvidenceError("Gate 3 projection chronology differs from its runtime")
    for local_position, (mapping, binding, action_parity) in enumerate(
        zip(source_map, replay.instances, parity, strict=True)
    ):
        candidates = projection.runtime.runtime_candidates.get(binding.problem_id)
        candidate_ids = candidates.evidence.candidate_ids if candidates is not None else ()
        if (
            action_parity.local_event_position != local_position
            or action_parity.source_event_id != mapping.source_event_id
            or action_parity.runtime_problem_id != binding.problem_id
            or action_parity.runtime_candidate_ids != candidate_ids
            or action_parity.standard_action_ids
            != tuple(f"m7-standard:{candidate_id}" for candidate_id in candidate_ids)
        ):
            raise Gate3BackendEvidenceError(
                "Gate 3 projection candidate/action parity differs from its runtime"
            )
    if policy_id is not None and replay.policy != gate3_policy_identity(policy_id):
        raise Gate3BackendEvidenceError(
            "Gate 3 projection policy differs from the requested baseline"
        )
    if roots is not None:
        try:
            strict_roots = Gate3RootBinding.model_validate(
                roots.model_dump(mode="python", round_trip=True),
                strict=True,
            )
        except (TypeError, ValueError) as error:
            raise Gate3BackendEvidenceError(
                "Gate 3 root binding failed strict content authentication"
            ) from error
        if strict_roots != roots:
            raise Gate3BackendEvidenceError(
                "Gate 3 root binding differs after strict authentication"
            )
        attestation = projection.attestation
        observed = (
            attestation.gate1_result_id,
            attestation.gate1_result_content_sha256,
            attestation.gate2_result_id,
            attestation.gate2_result_content_sha256,
            attestation.gate3_config_id,
            attestation.gate3_config_content_sha256,
            attestation.population_id,
            attestation.population_content_sha256,
        )
        expected = (
            roots.gate1_evaluation_result_id,
            roots.gate1_evaluation_result_content_sha256,
            roots.gate2_evaluation_result_id,
            roots.gate2_evaluation_result_content_sha256,
            roots.gate3_config_id,
            roots.gate3_config_content_sha256,
            roots.population_id,
            roots.population_content_sha256,
        )
        if observed != expected:
            raise Gate3BackendEvidenceError(
                "Gate 3 projection differs from the authenticated root binding"
            )


def _resequence_binding(
    binding: TemporalInstanceBinding,
    *,
    sequence: int,
) -> TemporalInstanceBinding:
    if binding.sequence == sequence:
        return binding
    values = binding.model_dump(
        mode="python",
        round_trip=True,
        exclude={"binding_id", "content_sha256"},
    )
    values["sequence"] = sequence
    provisional = binding.model_copy(
        update={
            "binding_id": "yfm7b-" + "0" * 24,
            "content_sha256": "sha256:" + "0" * 64,
            "sequence": sequence,
        }
    )
    digest = semantic_sha256(
        provisional,
        excluded_fields={"binding_id", "content_sha256"},
    )
    return TemporalInstanceBinding(
        binding_id=f"yfm7b-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **values,
    )


def build_gate3_known_only_runtime(
    *,
    projection: M11MaterialRuntimeProjection,
    cursor: M7ReplayCursor,
    local_event_position: int,
) -> M7ReplayRuntime:
    """Build a content-addressed physical K mask with shared runtime authority.

    Executed/current events remain a contiguous prefix.  Only future events
    registered as known at the current release survive, and every problem,
    candidate set, and runtime candidate unreachable from that prefix is
    removed.  The global terminal horizon and all authoritative caches/Jagua
    dependencies remain unchanged.
    """

    _require_projection_authority(projection)
    base = projection.runtime
    replay = base.replay_input
    if (
        local_event_position != cursor.next_event_position
        or local_event_position < 0
        or local_event_position >= len(replay.instances)
    ):
        raise Gate3BackendEvidenceError("Gate 3 known-only mask position differs from its cursor")
    visibility = projection.known_visible_local_prefixes[local_event_position]
    expected_source_positions = tuple(
        projection.source_event_map[position].source_event_position
        for position in visibility.visible_local_event_positions
    )
    current_source = projection.source_event_map[local_event_position]
    if (
        visibility.local_event_position != local_event_position
        or visibility.source_as_of_event_position != current_source.source_event_position
        or visibility.source_as_of_event_id != current_source.source_event_id
        or visibility.visible_source_event_positions != expected_source_positions
    ):
        raise Gate3BackendEvidenceError("Gate 3 known-only visibility is not event-aligned")
    visible = set(visibility.visible_local_event_positions)
    if any(position < 0 or position >= len(replay.instances) for position in visible):
        raise Gate3BackendEvidenceError("Gate 3 known-only visibility escapes its material runtime")
    retained_positions = tuple(sorted(set(range(local_event_position + 1)) | visible))
    if retained_positions[: local_event_position + 1] != tuple(range(local_event_position + 1)):
        raise Gate3BackendEvidenceError("Gate 3 known-only mask omitted an executed event")
    instances = tuple(
        _resequence_binding(replay.instances[position], sequence=sequence)
        for sequence, position in enumerate(retained_positions)
    )
    problem_ids = {item.problem_id for item in instances}
    problems = tuple(item for item in replay.problems if item.problem_id in problem_ids)
    candidate_sets = tuple(item for item in replay.candidate_sets if item.problem_id in problem_ids)
    runtime_candidates = {
        problem_id: base.runtime_candidates[problem_id] for problem_id in sorted(problem_ids)
    }
    masked_input = build_m7_replay_input(
        m0_contract_id=replay.m0_contract_id,
        m0_contract_sha256=replay.m0_contract_sha256,
        problem_index_id=replay.problem_index_id,
        problem_index_sha256=replay.problem_index_sha256,
        m6_contract_id=replay.m6_contract_id,
        m6_contract_sha256=replay.m6_contract_sha256,
        m6_population_id=replay.m6_population_id,
        m6_population_sha256=replay.m6_population_sha256,
        policy=replay.policy,
        rates=replay.rates,
        fit_config=replay.fit_config,
        search_config=replay.search_config,
        problems=problems,
        candidate_sets=candidate_sets,
        instances=instances,
        horizon_end=replay.horizon_end,
        collision_backend=replay.collision_backend,
        jagua_container_guard=replay.jagua_container_guard,
    )
    masked = M7ReplayRuntime(
        replay_input=masked_input,
        runtime_candidates=runtime_candidates,
        rules=base.rules,
        runtime_metrics=base.runtime_metrics,
        standard_profile_cache=base.standard_profile_cache,
        fit_search_cache=base.fit_search_cache,
        shared_fit_search_cache=base.shared_fit_search_cache,
        prepared_layout_cache=base.prepared_layout_cache,
        standard_profile_executor=base.standard_profile_executor,
        jagua_executable=base.jagua_executable,
        jagua_differential_audit=base.jagua_differential_audit,
    )
    if gate3_search_config_sha256(masked) != gate3_search_config_sha256(
        base
    ) or gate3_compute_budget_sha256(masked) != gate3_compute_budget_sha256(base):
        raise Gate3BackendEvidenceError(
            "Gate 3 known-only mask changed the paired search or compute contract"
        )
    return masked


def _catalog_candidate_set_sha256(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> str:
    problem_id = runtime.replay_input.instances[event_position].problem_id
    matches = tuple(
        item for item in runtime.replay_input.candidate_sets if item.problem_id == problem_id
    )
    if len(matches) != 1:
        raise Gate3BackendEvidenceError(
            "Gate 3 event does not have one authoritative candidate set"
        )
    return matches[0].content_sha256


def _catalog_context_cost(catalog: M7ActionCatalog, action_id: str) -> str:
    matches = tuple(item for item in catalog.contexts if item.action_id == action_id)
    if len(matches) != 1:
        raise Gate3BackendEvidenceError("Gate 3 selected action does not have one policy context")
    return _six_place(matches[0].immediate_net_cost)


def _decision_trace(
    *,
    projection: M11MaterialRuntimeProjection,
    runtime: M7ReplayRuntime,
    cursor: M7ReplayCursor,
    catalog: M7ActionCatalog,
    step: M7StepResult,
    arm: Gate3Arm,
    policy_id: str,
    visibility: Literal["released_only", "full_future", "known_only"],
    m9: M9TwoPlyResult | None,
) -> Gate3DecisionTrace:
    local_position = cursor.next_event_position
    source = projection.source_event_map[local_position]
    if source.local_event_position != local_position:
        raise Gate3BackendEvidenceError("Gate 3 decision source map is not event-aligned")
    fallback_id = (
        m9.baseline_action_id
        if m9 is not None
        else select_m7_fallback(
            catalog,
            policy=runtime.replay_input.policy,
        ).action_id
    )
    selected_id = m9.selected_action_id if m9 is not None else step.descriptor.action_id
    if selected_id != step.descriptor.action_id:
        raise Gate3BackendEvidenceError(
            "Gate 3 executed action differs from its recorded selection"
        )
    action = step.event.action
    returned_roots = tuple(
        sorted({item.lineage.root_stock_id for item in action.returned_remnants})
    )
    selected_root = (
        action.selected_stock.lineage.root_stock_id
        if action.kind is M7ActionKind.CONSUME_REMNANT
        else None
    )
    return build_gate3_decision_trace(
        event_position=source.source_event_position,
        event_id=source.source_event_id,
        arm=arm,
        algorithm="m9_two_ply" if m9 is not None else "m7_policy",
        visibility=visibility,
        policy_id=policy_id,
        standard_candidate_set_sha256=_catalog_candidate_set_sha256(
            runtime,
            event_position=local_position,
        ),
        search_config_sha256=gate3_search_config_sha256(runtime),
        compute_budget_sha256=gate3_compute_budget_sha256(runtime),
        search_runtime_sha256=(m7_semantic_runtime_sha256(runtime) if m9 is not None else None),
        action_catalog_sha256=gate3_action_catalog_sha256(
            runtime=runtime,
            cursor=cursor,
            catalog=catalog,
        ),
        action_ids=tuple(item.action_id for item in catalog.actions),
        baseline_action_id=fallback_id,
        selected_action_id=selected_id,
        selected_immediate_cost=_catalog_context_cost(catalog, selected_id),
        baseline_immediate_cost=_catalog_context_cost(catalog, fallback_id),
        m9_root_scores=(
            tuple(
                (item.action_id, _six_place(item.bounded_objective_cost)) for item in m9.root_scores
            )
            if m9 is not None
            else ()
        ),
        inventory_before_sha256=gate3_inventory_sha256(cursor.inventory),
        inventory_after_sha256=gate3_inventory_sha256(step.cursor.inventory),
        returned_lineage_root_ids=returned_roots,
        selected_lineage_root_id=selected_root,
        m9_catalog_count=m9.telemetry.catalog_count if m9 is not None else 0,
        m9_explicit_transition_count=(
            m9.telemetry.explicit_transition_count if m9 is not None else 0
        ),
        m9_continuation_event_count=(
            m9.telemetry.continuation_event_count if m9 is not None else 0
        ),
        m9_start_event_position=(m9.start_event_position if m9 is not None else None),
        m9_stop_event_position=(m9.stop_event_position if m9 is not None else None),
    )


def _m7_replay_result(
    runtime: M7ReplayRuntime,
    *,
    steps: tuple[M7StepResult, ...],
    terminal: M7ContinuationResult,
) -> M7ReplayResult:
    replay = runtime.replay_input
    events = tuple(item.event for item in steps)
    if len(events) != len(replay.instances) or terminal.events:
        raise Gate3BackendEvidenceError(
            "Gate 3 direct replay did not execute exactly one complete stream"
        )
    summary = M7ReplaySummary(
        instance_count=len(replay.instances),
        fulfilled_instance_count=len(events),
        timestamp_group_count=events[-1].timestamp_group_sequence + 1,
        full_sheet_opening_count=sum(
            item.action.kind is M7ActionKind.OPEN_STANDARD_SHEET for item in events
        ),
        remnant_retrieval_count=sum(
            item.action.kind is M7ActionKind.CONSUME_REMNANT for item in events
        ),
        returned_remnant_count=sum(len(item.action.returned_remnants) for item in events),
        terminal_remnant_count=len(terminal.terminal.inventory_before_liquidation),
        total_action_count=sum(item.action_set_size for item in events),
        total_fit_search_query_count=sum(item.fit_search_query_count for item in events),
        total_fit_search_evaluated_candidate_count=sum(
            item.fit_search_evaluated_candidate_count for item in events
        ),
        final_net_cost=terminal.final_costs.net_cost,
        technical_decision="pass",
    )
    semantic = {
        "schema_version": "yieldforge.m7-replay-result.v1",
        "input_id": replay.input_id,
        "input_sha256": replay.content_sha256,
        "policy": replay.policy.model_dump(mode="json"),
        "events": tuple(item.model_dump(mode="json") for item in events),
        "terminal": terminal.terminal.model_dump(mode="json"),
        "summary": summary.model_dump(mode="json"),
        "claim_ceiling": replay.claim_ceiling,
    }
    digest = semantic_sha256(semantic)
    return M7ReplayResult(
        result_id=f"yfm7r-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        input_id=replay.input_id,
        input_sha256=replay.content_sha256,
        policy=replay.policy,
        events=events,
        terminal=terminal.terminal,
        summary=summary,
    )


def _run_direct_m7(
    projection: M11MaterialRuntimeProjection,
    *,
    arm: Gate3Arm,
    policy_id: str,
) -> tuple[
    tuple[Gate3DecisionTrace, ...],
    tuple[M7StepResult, ...],
    M7ContinuationResult,
    M7ReplayResult,
]:
    runtime = projection.runtime
    cursor = initial_m7_cursor(runtime.replay_input)
    decisions: list[Gate3DecisionTrace] = []
    steps: list[M7StepResult] = []
    while cursor.next_event_position < len(runtime.replay_input.instances):
        catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=True)
        if catalog.generated.fit_search_budget_truncated_count:
            raise Gate3BackendEvidenceError("Gate 3 direct replay encountered a truncated catalog")
        selection = select_m7_fallback(catalog, policy=runtime.replay_input.policy)
        descriptor = next(item for item in catalog.actions if item.action_id == selection.action_id)
        step = apply_m7_action_descriptor(
            runtime,
            cursor=cursor,
            catalog=catalog,
            descriptor=descriptor,
            decision_key=selection.decision_key,
        )
        decisions.append(
            _decision_trace(
                projection=projection,
                runtime=runtime,
                cursor=cursor,
                catalog=catalog,
                step=step,
                arm=arm,
                policy_id=policy_id,
                visibility="released_only",
                m9=None,
            )
        )
        steps.append(step)
        cursor = step.cursor
    terminal = run_m7_continuation(
        runtime,
        cursor=cursor,
        stop_event_position=len(runtime.replay_input.instances),
    )
    step_tuple = tuple(steps)
    return (
        tuple(decisions),
        step_tuple,
        terminal,
        _m7_replay_result(runtime, steps=step_tuple, terminal=terminal),
    )


def _run_receding_m9(
    projection: M11MaterialRuntimeProjection,
    *,
    arm: Gate3Arm,
    policy_id: str,
    visibility: Literal["full_future", "known_only"],
) -> tuple[
    tuple[Gate3DecisionTrace, ...],
    tuple[M7StepResult, ...],
    M7ContinuationResult,
]:
    base = projection.runtime
    cursor = initial_m7_cursor(base.replay_input)
    decisions: list[Gate3DecisionTrace] = []
    steps: list[M7StepResult] = []
    while cursor.next_event_position < len(base.replay_input.instances):
        local_position = cursor.next_event_position
        runtime = (
            base
            if visibility == "full_future"
            else build_gate3_known_only_runtime(
                projection=projection,
                cursor=cursor,
                local_event_position=local_position,
            )
        )
        catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=True)
        if catalog.generated.fit_search_budget_truncated_count:
            raise Gate3BackendEvidenceError(
                "Gate 3 two-ply replay encountered a truncated root catalog"
            )
        result = score_two_ply_reoptimization(
            _Gate3SearchRequest(
                runtime=runtime,
                cursor=cursor,
                visibility=FullRealizedVisibility(stream=runtime.replay_input.instances),
            ),
            objective_label="scrap_only",
        )
        action_ids = tuple(item.action_id for item in catalog.actions)
        if (
            not result.complete
            or not result.action_catalog_complete
            or result.telemetry.truncated_catalog_count
            or tuple(item.action_id for item in result.root_scores) != action_ids
        ):
            raise Gate3BackendEvidenceError(
                "Gate 3 two-ply result differs from its complete root catalog"
            )
        descriptor = next(
            item for item in catalog.actions if item.action_id == result.selected_action_id
        )
        step = apply_m7_action_descriptor(
            runtime,
            cursor=cursor,
            catalog=catalog,
            descriptor=descriptor,
            decision_key=(f"gate3_m9_selected_action_id={result.selected_action_id}",),
        )
        decisions.append(
            _decision_trace(
                projection=projection,
                runtime=runtime,
                cursor=cursor,
                catalog=catalog,
                step=step,
                arm=arm,
                policy_id=policy_id,
                visibility=visibility,
                m9=result,
            )
        )
        steps.append(step)
        cursor = step.cursor
    terminal = run_m7_continuation(
        base,
        cursor=cursor,
        stop_event_position=len(base.replay_input.instances),
    )
    return tuple(decisions), tuple(steps), terminal


def execute_gate3_material_shard(
    *,
    projection: M11MaterialRuntimeProjection,
    roots: Gate3RootBinding,
    arm: Gate3Arm,
    policy_id: Gate3BaselinePolicyId,
) -> Gate3MaterialExecution:
    """Execute one authenticated independent material shard for B, F, or K."""

    _require_projection_authority(projection, roots=roots, policy_id=policy_id)
    is_additional_m9_baseline = arm == "B" and policy_id == "known_only_m9_two_ply_scrap"
    if arm == "B" and not is_additional_m9_baseline:
        decisions, steps, terminal, replay_result = _run_direct_m7(
            projection,
            arm=arm,
            policy_id=policy_id,
        )
        trace_visibility: Literal["released_only", "full_future", "known_only"] = "released_only"
    else:
        search_visibility: Literal["full_future", "known_only"] = (
            "full_future" if arm == "F" else "known_only"
        )
        decisions, steps, terminal = _run_receding_m9(
            projection,
            arm=arm,
            policy_id=policy_id,
            visibility=search_visibility,
        )
        replay_result = None
        trace_visibility = search_visibility
    attestation = projection.attestation
    shard = build_gate3_shard_trace(
        roots=roots,
        stream_id=attestation.source_stream_id,
        corpus_id=attestation.corpus_id,
        shard_id=f"{attestation.attestation_id}:{arm}",
        material_key=attestation.material_key,
        arm=arm,
        policy_id=policy_id,
        visibility=trace_visibility,
        projection_binding_sha256=attestation.content_sha256,
        decisions=decisions,
        final_costs=gate3_ledger_from_replay(terminal.final_costs),
    )
    return Gate3MaterialExecution(
        shard_trace=shard,
        steps=steps,
        terminal=terminal,
        m7_replay_result=replay_result,
    )


def merge_gate3_shard_traces(
    *,
    roots: Gate3RootBinding,
    stream_id: str,
    corpus_id: Gate3CorpusId,
    regime: Literal["recurrent", "mixed", "high_mix", "regime_shift"],
    arm: Gate3Arm,
    policy_id: str,
    shards: tuple[Gate3ShardTrace, ...],
) -> Gate3ArmTrace:
    """Reconstruct one stream arm from its disjoint material executions."""

    return merge_gate3_material_shards(
        roots=roots,
        stream_id=stream_id,
        corpus_id=corpus_id,
        regime=regime,
        arm=arm,
        policy_id=policy_id,
        shards=shards,
    )


def _canonical_signed_cost(value: str) -> Decimal:
    try:
        cost = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Gate 3 exact-audit cost is not decimal") from error
    if not cost.is_finite() or format(cost, ".6f") != value:
        raise ValueError("Gate 3 exact-audit cost is not canonical six-place")
    return cost


def verify_gate3_exact_audit_separability(
    *,
    material_root_costs: tuple[
        tuple[str, tuple[tuple[str, str], ...]],
        ...,
    ],
    cartesian_costs: tuple[
        tuple[tuple[tuple[str, str], ...], str],
        ...,
    ],
) -> bool:
    """Verify exhaustive whole-slice costs equal the material Cartesian sum.

    This is the executable separability check used before an exact-audit shard
    result is substituted for a monolithic exhaustive search.
    """

    canonical = tuple(sorted(material_root_costs, key=lambda item: item[0]))
    if not canonical or len({item[0] for item in canonical}) != len(canonical):
        raise ValueError("Gate 3 exact audit requires unique material shards")
    action_sets: list[tuple[tuple[str, str], ...]] = []
    for _material_key, actions in canonical:
        if not actions or len({item[0] for item in actions}) != len(actions):
            raise ValueError("Gate 3 exact audit requires unique nonempty material actions")
        ordered_actions = tuple(sorted(actions, key=lambda item: item[0]))
        for _action_id, cost in ordered_actions:
            _canonical_signed_cost(cost)
        action_sets.append(ordered_actions)
    expected = tuple(
        sorted(
            (
                tuple(
                    (material_key, action[0])
                    for (material_key, _), action in zip(
                        canonical,
                        combination,
                        strict=True,
                    )
                ),
                format(
                    sum(
                        (_canonical_signed_cost(action[1]) for action in combination),
                        Decimal(0),
                    ),
                    ".6f",
                ),
            )
            for combination in product(*action_sets)
        )
    )
    observed = tuple(sorted(cartesian_costs))
    if expected != observed:
        raise ValueError("Gate 3 exact-audit Cartesian costs differ from material addition")
    return True


__all__ = [
    "Gate3BackendEvidenceError",
    "Gate3MaterialExecution",
    "build_gate3_known_only_runtime",
    "execute_gate3_material_shard",
    "gate3_action_catalog_sha256",
    "gate3_compute_budget_sha256",
    "gate3_inventory_sha256",
    "gate3_ledger_from_replay",
    "gate3_policy_identity",
    "gate3_search_config_sha256",
    "merge_gate3_shard_traces",
    "verify_gate3_exact_audit_separability",
]
