"""Authenticated execution of the frozen M11 Gate 3 validity controls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from yieldforge.baseline.replay import M7ReplayCursor, M7ReplayRuntime, initial_m7_cursor
from yieldforge.oracle.search_validation import M9ExactSearchRequest, solve_exact_search
from yieldforge.oracle.visibility import FullRealizedVisibility
from yieldforge.realistic_falsification.adapter import (
    M11M7AdapterContext,
    M11MaterialRuntimeProjection,
    project_exact_audit,
    project_hard_null,
    project_stream,
)
from yieldforge.realistic_falsification.confirmation import (
    Gate3Arm,
    Gate3BaselineCalibrationFreeze,
    Gate3BaselinePolicyId,
    Gate3DecisionRuntimeReceipt,
    Gate3ExactAuditTrace,
    Gate3HardNullControl,
    Gate3ProjectionShardEvidence,
    Gate3RootBinding,
    Gate3TwinControl,
    Gate3ValidityReceipt,
    build_gate3_decision_runtime_receipt,
    build_gate3_exact_audit_trace,
    build_gate3_exact_material_audit,
    build_gate3_hard_null_arm_trace,
    build_gate3_hard_null_control,
    build_gate3_projection_shard_evidence,
    build_gate3_stream_cell,
    build_gate3_twin_control,
    evaluate_gate3_validity_controls,
)
from yieldforge.realistic_falsification.gate3_backend import (
    Gate3MaterialExecution,
    build_gate3_known_only_runtime,
    execute_gate3_material_shard,
    gate3_policy_identity,
    merge_gate3_shard_traces,
)
from yieldforge.realistic_falsification.pack import (
    M11ExactAuditEpisode,
    M11HardNull,
    M11Stream,
)

_CORPUS_ORDER = ("lectra-m3-m4", "loco-2dics")
_HARD_NULL_KIND_ORDER = (
    "single_action",
    "unique_materials_single_action",
    "all_work_known_single_action",
)
_EXACT_ARM_ORDER = ("central", "central", "adverse", "adverse", "null", "null")


class Gate3ControlsError(ValueError):
    """Authenticated Gate 3 validity-control inputs are not canonical."""


@dataclass(frozen=True, slots=True)
class _ExactSearchRequest:
    runtime: M7ReplayRuntime
    cursor: M7ReplayCursor
    visibility: FullRealizedVisibility


def _require_authenticated_inputs(
    *,
    context: M11M7AdapterContext,
    roots: Gate3RootBinding,
    baseline_freezes: tuple[
        Gate3BaselineCalibrationFreeze,
        Gate3BaselineCalibrationFreeze,
    ],
) -> tuple[
    tuple[M11HardNull, ...],
    tuple[M11Stream, ...],
    tuple[M11ExactAuditEpisode, ...],
]:
    try:
        population = context.population
        root_values = (
            population.contract_id,
            population.contract_content_sha256,
            population.population_id,
            population.content_sha256,
            context.gate1_result.result_id,
            context.gate1_result.content_sha256,
            context.gate2_result.result_id,
            context.gate2_result.content_sha256,
            context.gate3_config.config_id,
            context.gate3_config.content_sha256,
        )
        expected_roots = (
            roots.contract_id,
            roots.contract_content_sha256,
            roots.population_id,
            roots.population_content_sha256,
            roots.gate1_evaluation_result_id,
            roots.gate1_evaluation_result_content_sha256,
            roots.gate2_evaluation_result_id,
            roots.gate2_evaluation_result_content_sha256,
            roots.gate3_config_id,
            roots.gate3_config_content_sha256,
        )
        freezes = tuple(baseline_freezes)
        if (
            root_values != expected_roots
            or len(freezes) != 2
            or tuple(item.corpus_id for item in freezes) != _CORPUS_ORDER
            or any(item.roots != roots for item in freezes)
        ):
            raise Gate3ControlsError("Gate 3 validity roots or calibration-freeze order differ")

        hard_nulls = tuple(population.hard_nulls)
        expected_hard_order = tuple(
            (corpus_id, kind) for corpus_id in _CORPUS_ORDER for kind in _HARD_NULL_KIND_ORDER
        )
        if (
            len(hard_nulls) != 6
            or tuple((item.corpus_id, item.null_kind) for item in hard_nulls) != expected_hard_order
            or len({item.null_id for item in hard_nulls}) != 6
        ):
            raise Gate3ControlsError("Gate 3 hard-null registry is not canonical")

        twins = tuple(item for item in population.streams if item.stream_kind == "shuffled_twin")
        if (
            len(twins) != 40
            or tuple(item.corpus_id for item in twins)
            != (("lectra-m3-m4",) * 20 + ("loco-2dics",) * 20)
            or len({item.stream_id for item in twins}) != 40
            or len({item.source_stream_id for item in twins}) != 40
            or any(
                item.partition != "confirmation"
                or item.source_stream_id is None
                or not item.no_signal_control
                for item in twins
            )
        ):
            raise Gate3ControlsError("Gate 3 shuffled-twin registry is not canonical")

        exact_audits = tuple(population.exact_audits)
        expected_exact_order = tuple(
            (corpus_id, ordinal, arm)
            for corpus_id in _CORPUS_ORDER
            for ordinal, arm in enumerate(_EXACT_ARM_ORDER)
        )
        if (
            len(exact_audits) != 12
            or tuple(
                (item.corpus_id, item.audit_ordinal, item.economic_arm) for item in exact_audits
            )
            != expected_exact_order
            or len({item.audit_id for item in exact_audits}) != 12
        ):
            raise Gate3ControlsError("Gate 3 exact-audit registry is not canonical")
    except Gate3ControlsError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise Gate3ControlsError("Gate 3 validity inputs are incomplete") from error
    return hard_nulls, twins, exact_audits


def _known_retained_positions(
    projection: M11MaterialRuntimeProjection,
    *,
    local_event_position: int,
) -> tuple[int, ...]:
    visibility = projection.attestation.known_visible_local_prefixes[local_event_position]
    return tuple(
        sorted(set(range(local_event_position + 1)) | set(visibility.visible_local_event_positions))
    )


def _build_projection_evidence(
    *,
    projection: M11MaterialRuntimeProjection,
    execution: Gate3MaterialExecution,
    roots: Gate3RootBinding,
) -> Gate3ProjectionShardEvidence:
    base_runtime = projection.runtime
    cursor = initial_m7_cursor(base_runtime.replay_input)
    decisions = execution.shard_trace.decisions
    if len(decisions) != len(execution.steps):
        raise Gate3ControlsError(
            "Gate 3 validity execution decisions differ from their applied cursors"
        )
    receipts: list[Gate3DecisionRuntimeReceipt] = []
    for local_position, (decision, step) in enumerate(zip(decisions, execution.steps, strict=True)):
        if decision.algorithm == "m9_two_ply":
            if decision.visibility == "full_future":
                runtime = base_runtime
                runtime_role: Literal["base_full_future", "known_only_physical_mask"] = (
                    "base_full_future"
                )
                retained = tuple(range(len(base_runtime.replay_input.instances)))
            elif decision.visibility == "known_only":
                runtime = build_gate3_known_only_runtime(
                    projection=projection,
                    cursor=cursor,
                    local_event_position=local_position,
                )
                runtime_role = "known_only_physical_mask"
                retained = _known_retained_positions(
                    projection,
                    local_event_position=local_position,
                )
            else:
                raise Gate3ControlsError(
                    "Gate 3 validity M9 decision has an unsupported visibility"
                )
            receipts.append(
                build_gate3_decision_runtime_receipt(
                    decision=decision,
                    runtime_role=runtime_role,
                    retained_local_event_positions=retained,
                    runtime=runtime,
                )
            )
        cursor = step.cursor
    return build_gate3_projection_shard_evidence(
        roots=roots,
        projection_attestation=projection.attestation,
        replay_input=base_runtime.replay_input,
        shard_trace=execution.shard_trace,
        decision_runtime_receipts=tuple(receipts),
    )


def _hard_null_control(
    *,
    context: M11M7AdapterContext,
    roots: Gate3RootBinding,
    freeze: Gate3BaselineCalibrationFreeze,
    registration: M11HardNull,
) -> Gate3HardNullControl:
    policy_id = cast(Gate3BaselinePolicyId, freeze.selected_policy_id)
    projections = project_hard_null(
        context,
        registration.null_id,
        "central",
        policy=gate3_policy_identity(policy_id),
    )
    arm_traces = {}
    for arm in cast(tuple[Gate3Arm, Gate3Arm, Gate3Arm], ("B", "F", "K")):
        evidence = []
        for projection in projections:
            execution = execute_gate3_material_shard(
                projection=projection,
                roots=roots,
                arm=arm,
                policy_id=policy_id,
            )
            evidence.append(
                _build_projection_evidence(
                    projection=projection,
                    execution=execution,
                    roots=roots,
                )
            )
        arm_traces[arm] = build_gate3_hard_null_arm_trace(
            roots=roots,
            registration=registration,
            arm=arm,
            policy_id=policy_id,
            material_evidence=tuple(evidence),
        )
    return build_gate3_hard_null_control(
        roots=roots,
        baseline_freeze=freeze,
        registration=registration,
        baseline=arm_traces["B"],
        full_future=arm_traces["F"],
        known_only=arm_traces["K"],
    )


def _twin_control(
    *,
    context: M11M7AdapterContext,
    roots: Gate3RootBinding,
    freeze: Gate3BaselineCalibrationFreeze,
    twin: M11Stream,
) -> Gate3TwinControl:
    policy_id = cast(Gate3BaselinePolicyId, freeze.selected_policy_id)
    projections = project_stream(
        context,
        twin.stream_id,
        "central",
        policy=gate3_policy_identity(policy_id),
    )
    traces = {}
    for arm in cast(tuple[Gate3Arm, Gate3Arm, Gate3Arm], ("B", "F", "K")):
        executions = tuple(
            execute_gate3_material_shard(
                projection=projection,
                roots=roots,
                arm=arm,
                policy_id=policy_id,
            )
            for projection in projections
        )
        traces[arm] = merge_gate3_shard_traces(
            roots=roots,
            stream_id=twin.stream_id,
            corpus_id=twin.corpus_id,
            regime=twin.regime,
            arm=arm,
            policy_id=policy_id,
            shards=tuple(item.shard_trace for item in executions),
        )
    cell = build_gate3_stream_cell(
        roots=roots,
        baseline_freeze=freeze,
        baseline=traces["B"],
        full_future=traces["F"],
        known_only=traces["K"],
    )
    if twin.source_stream_id is None:  # closed by the authenticated registry check.
        raise Gate3ControlsError("Gate 3 shuffled twin lacks its source stream")
    return build_gate3_twin_control(
        roots=roots,
        source_stream_id=twin.source_stream_id,
        twin_cell=cell,
    )


def _exact_audit(
    *,
    context: M11M7AdapterContext,
    roots: Gate3RootBinding,
    freeze: Gate3BaselineCalibrationFreeze,
    registration: M11ExactAuditEpisode,
) -> Gate3ExactAuditTrace:
    policy_id = cast(Gate3BaselinePolicyId, freeze.selected_policy_id)
    projections = project_exact_audit(
        context,
        registration.audit_id,
        policy=gate3_policy_identity(policy_id),
    )
    material_audits = []
    for projection in projections:
        execution = execute_gate3_material_shard(
            projection=projection,
            roots=roots,
            arm="F",
            policy_id=policy_id,
        )
        evidence = _build_projection_evidence(
            projection=projection,
            execution=execution,
            roots=roots,
        )
        runtime = projection.runtime
        request = _ExactSearchRequest(
            runtime=runtime,
            cursor=initial_m7_cursor(runtime.replay_input),
            visibility=FullRealizedVisibility(stream=runtime.replay_input.instances),
        )
        exact_result = solve_exact_search(
            cast(M9ExactSearchRequest, request),
            include_terminal_credit=True,
        )
        material_audits.append(
            build_gate3_exact_material_audit(
                evidence=evidence,
                exact_result=exact_result,
            )
        )
    return build_gate3_exact_audit_trace(
        roots=roots,
        baseline_freeze=freeze,
        registration=registration,
        material_audits=tuple(material_audits),
    )


def execute_gate3_validity_controls(
    *,
    context: M11M7AdapterContext,
    roots: Gate3RootBinding,
    baseline_freezes: tuple[
        Gate3BaselineCalibrationFreeze,
        Gate3BaselineCalibrationFreeze,
    ],
) -> Gate3ValidityReceipt:
    """Execute the exact authenticated 6/40/12 Gate 3 validity population."""

    hard_registrations, twins, exact_registrations = _require_authenticated_inputs(
        context=context,
        roots=roots,
        baseline_freezes=baseline_freezes,
    )
    freeze_by_corpus = {item.corpus_id: item for item in baseline_freezes}
    hard_nulls = tuple(
        _hard_null_control(
            context=context,
            roots=roots,
            freeze=freeze_by_corpus[item.corpus_id],
            registration=item,
        )
        for item in hard_registrations
    )
    twin_controls = tuple(
        _twin_control(
            context=context,
            roots=roots,
            freeze=freeze_by_corpus[item.corpus_id],
            twin=item,
        )
        for item in twins
    )
    exact_audits = tuple(
        _exact_audit(
            context=context,
            roots=roots,
            freeze=freeze_by_corpus[item.corpus_id],
            registration=item,
        )
        for item in exact_registrations
    )
    return evaluate_gate3_validity_controls(
        roots=roots,
        hard_nulls=hard_nulls,
        twin_controls=twin_controls,
        exact_audits=exact_audits,
    )


__all__ = ["Gate3ControlsError", "execute_gate3_validity_controls"]
