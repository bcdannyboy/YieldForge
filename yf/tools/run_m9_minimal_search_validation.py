#!/usr/bin/env python3
"""Execute and immutably publish the bounded minimal M9 search validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from pathlib import Path

from tests.oracle.fixtures import exhaustive_certificate_cases
from yieldforge.oracle.search_validation import (
    M9ObjectiveEvaluation,
    M9SearchValidationResult,
    M9TwoPlyCaseComparison,
    M9TwoPlyObjectiveEvaluation,
    M9TwoPlyRepairValidationResult,
    evaluate_search_validation,
    evaluate_two_ply_repair_validation,
)

_SCHEMA_VERSION = "yieldforge.m9-minimal-search-validation.v1"
_FIXTURE_SOURCE = "tests.oracle.fixtures.exhaustive_certificate_cases"
_CLAIM_CEILING = (
    "finite_registered_45_case_decision_validation_only_not_global_policy_optimality_"
    "physical_or_commercial_evidence"
)
_OBJECTIVE_LABELS = ("scrap_only", "zero_total_terminal_credit")
_OBJECTIVE_DEFINITIONS = {
    "scrap_only": "m7_final_net_cost_including_terminal_scrap_credit",
    "zero_total_terminal_credit": (
        "counterfactual_m7_final_net_cost_with_terminal_scrap_credit_added_back_only"
    ),
}
_REPAIR_SCHEMA_VERSION = "yieldforge.m9-two-ply-repair-validation.v1"
_REPAIR_SEMANTICS: dict[str, object] = {
    "action_catalogs": "complete",
    "continuation_policy": "frozen_m7",
    "search_depth": 2,
    "tie_break": "bounded_cost_then_baseline_then_action_id",
}
_ORIGINAL_FAILURE_ARTIFACT_NAME = (
    "m9-minimal-search-validation-yfm9-97e032de7a09247cc83e6c5a.json"
)
_ORIGINAL_FAILURE_SCHEMA_VERSION = "yieldforge.m9-minimal-search-validation.v1"
_ORIGINAL_FAILURE_RESULT_ID = "yfm9-97e032de7a09247cc83e6c5a"
_ORIGINAL_FAILURE_CONTENT_SHA256 = (
    "sha256:97e032de7a09247cc83e6c5a7140c67"
    "ea988712a1b493ca92b6513d95ea98dca"
)
_ORIGINAL_FAILURE_RAW_SHA256 = (
    "sha256:9ae6d7fdf2252023a96de8773877bb50"
    "f3786f2f7c8b1c6c4bcb5a7de1ca82e3"
)
_ORIGINAL_FAILURE_ARTIFACT_PATH = (
    Path(__file__).parents[1] / "experiments" / "results" / _ORIGINAL_FAILURE_ARTIFACT_NAME
)


class M9RunnerError(RuntimeError):
    """The repeated M9 evaluation or immutable publication failed closed."""


@dataclass(frozen=True)
class M9RunOutcome:
    """Published identity plus volatile operational timings kept outside the artifact."""

    artifact_path: Path
    result_id: str
    content_sha256: str
    decision: str
    pass_wall_seconds: tuple[float, float]


@dataclass(frozen=True)
class _EvaluationPass:
    ordered_case_ids: tuple[str, ...]
    evaluator_payload: dict[str, object]
    evaluator_semantic_bytes: bytes
    decision: str
    wall_seconds: float


def _json_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Mapping):
        converted: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise M9RunnerError("M9 semantic JSON requires exact string mapping keys")
            converted[key] = _json_value(item)
        return converted
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or type(value) in {str, int, bool}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise M9RunnerError("M9 semantic JSON refuses non-finite floats")
        return value
    raise M9RunnerError(
        f"M9 semantic JSON does not support {type(value).__name__}"
    )


def _canonical_bytes(payload: Mapping[str, object]) -> bytes:
    converted = _json_value(payload)
    if not isinstance(converted, dict):
        raise M9RunnerError("M9 canonical semantic payload must be a mapping")
    return json.dumps(
        converted,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _pretty_bytes(payload: Mapping[str, object]) -> bytes:
    converted = _json_value(payload)
    if not isinstance(converted, dict):
        raise M9RunnerError("M9 published payload must be a mapping")
    return (
        json.dumps(
            converted,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _validate_objective(
    objective: M9ObjectiveEvaluation,
    *,
    expected_label: str,
    ordered_case_ids: tuple[str, ...],
) -> None:
    if objective.objective_label != expected_label:
        raise M9RunnerError("M9 evaluator objective labels do not reconcile")
    record_ids = tuple(record.case_id for record in objective.cases)
    if record_ids != ordered_case_ids:
        raise M9RunnerError("M9 evaluator case IDs do not reconcile with fresh fixtures")
    if any(record.objective_label != expected_label for record in objective.cases):
        raise M9RunnerError("M9 case objective labels do not reconcile")
    if objective.complete is not (
        len(objective.cases) == 45 and all(record.complete for record in objective.cases)
    ):
        raise M9RunnerError("M9 objective completeness does not reconcile")
    every_optimal = all(
        record.selected_action_is_globally_optimal for record in objective.cases
    )
    if objective.every_selected_action_is_globally_optimal is not every_optimal:
        raise M9RunnerError("M9 objective global-optimality aggregate does not reconcile")
    regrets = tuple(record.absolute_first_action_regret for record in objective.cases)
    if any(not math.isfinite(value) or value < 0.0 for value in regrets):
        raise M9RunnerError("M9 objective contains an invalid absolute regret")
    if objective.max_absolute_first_action_regret != max(regrets, default=0.0):
        raise M9RunnerError("M9 objective maximum regret does not reconcile")
    counterexample_ids = tuple(record.case_id for record in objective.counterexamples)
    expected_counterexamples = tuple(
        record.case_id
        for record in objective.cases
        if not record.selected_action_is_globally_optimal
    )
    if counterexample_ids != expected_counterexamples:
        raise M9RunnerError("M9 objective counterexamples do not reconcile")
    expected_conclusion = (
        "pass_decision_feasibility"
        if objective.complete
        and every_optimal
        and objective.max_absolute_first_action_regret == 0.0
        and objective.information_null_controls_pass
        else "fail_search_gap"
    )
    if objective.conclusion != expected_conclusion:
        raise M9RunnerError("M9 objective conclusion does not reconcile")


def _validate_evaluator_result(
    result: M9SearchValidationResult,
    *,
    ordered_case_ids: tuple[str, ...],
) -> None:
    if len(ordered_case_ids) != len(set(ordered_case_ids)) or any(
        not case_id for case_id in ordered_case_ids
    ):
        raise M9RunnerError("M9 fresh fixture case IDs must be nonempty and unique")
    if len(ordered_case_ids) != 45 or result.case_count != len(ordered_case_ids):
        raise M9RunnerError("M9 result must contain exactly 45 registered cases")
    if tuple(result.objective_labels) != _OBJECTIVE_LABELS:
        raise M9RunnerError("M9 evaluator objective census differs")
    _validate_objective(
        result.primary,
        expected_label=_OBJECTIVE_LABELS[0],
        ordered_case_ids=ordered_case_ids,
    )
    _validate_objective(
        result.terminal_sensitivity,
        expected_label=_OBJECTIVE_LABELS[1],
        ordered_case_ids=ordered_case_ids,
    )
    expected_controls = tuple(
        case_id
        for case_id in ordered_case_ids
        if case_id.endswith("zero-no-fit-equal-separated-two")
    )
    if result.information_null_control_case_ids != expected_controls or len(
        expected_controls
    ) != 5:
        raise M9RunnerError("M9 information-null control census does not reconcile")
    no_reversal = result.primary.conclusion == result.terminal_sensitivity.conclusion
    if result.terminal_conclusion_does_not_reverse is not no_reversal:
        raise M9RunnerError("M9 terminal-sensitivity conclusion does not reconcile")
    expected_decision = (
        "pass_decision_feasibility"
        if result.primary.complete
        and result.primary.every_selected_action_is_globally_optimal
        and result.primary.max_absolute_first_action_regret == 0.0
        and result.primary.information_null_controls_pass
        and no_reversal
        else "fail_search_gap"
    )
    if result.decision != expected_decision:
        raise M9RunnerError("M9 aggregate decision does not reconcile")


def _rounded_cost(value: float) -> float:
    if not math.isfinite(value):
        raise M9RunnerError("M9 repair result contains a non-finite cost")
    quantum = Decimal("0.000001")
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _validate_two_ply_case(
    record: M9TwoPlyCaseComparison,
    *,
    expected_case_id: str,
    expected_label: str,
) -> None:
    if record.case_id != expected_case_id or not record.policy_name:
        raise M9RunnerError("M9 repair case identity does not reconcile")
    if (
        record.objective_label != expected_label
        or record.objective_definition != _OBJECTIVE_DEFINITIONS[expected_label]
    ):
        raise M9RunnerError("M9 repair case objective does not reconcile")
    bounded_ids = tuple(score.action_id for score in record.two_ply_root_scores)
    exact_ids = tuple(score.action_id for score in record.exact_root_scores)
    if (
        not bounded_ids
        or len(bounded_ids) != len(set(bounded_ids))
        or bounded_ids != exact_ids
    ):
        raise M9RunnerError("M9 repair root action catalogs do not reconcile")
    for bounded_score, exact_score in zip(
        record.two_ply_root_scores,
        record.exact_root_scores,
        strict=True,
    ):
        if bounded_score.kind != exact_score.kind:
            raise M9RunnerError("M9 repair root action kinds do not reconcile")
        if _rounded_cost(
            bounded_score.bounded_objective_cost - exact_score.final_net_cost
        ) < 0.0:
            raise M9RunnerError("M9 repair bounded value is below exact reachability")

    if record.baseline_action_id not in exact_ids:
        raise M9RunnerError("M9 repair baseline action is absent from the root catalog")
    if record.repaired_selected_action_id not in exact_ids:
        raise M9RunnerError("M9 repair selected action is absent from the root catalog")
    exact_by_action = {
        score.action_id: score.final_net_cost for score in record.exact_root_scores
    }
    bounded_by_action = {
        score.action_id: score.bounded_objective_cost
        for score in record.two_ply_root_scores
    }
    exact_optimum = min(exact_by_action.values())
    exact_optimal_ids = tuple(
        action_id
        for action_id, cost in exact_by_action.items()
        if cost == exact_optimum
    )
    selected_exact_cost = exact_by_action[record.repaired_selected_action_id]
    selected_bounded_cost = bounded_by_action[record.repaired_selected_action_id]
    absolute_regret = _rounded_cost(selected_exact_cost - exact_optimum)
    relative_regret = (
        0.0
        if absolute_regret == 0.0
        else None
        if exact_optimum == 0.0
        else _rounded_cost(absolute_regret / abs(exact_optimum))
    )
    signed_error = _rounded_cost(selected_bounded_cost - selected_exact_cost)
    if (
        record.exact_optimal_first_action_ids != exact_optimal_ids
        or record.exact_optimal_cost != exact_optimum
        or record.exact_cost_after_selected_first_action != selected_exact_cost
        or record.absolute_first_action_regret != absolute_regret
        or record.relative_first_action_regret != relative_regret
        or record.selected_action_is_globally_optimal
        is not (record.repaired_selected_action_id in exact_optimal_ids)
        or record.bounded_selected_action_score != selected_bounded_cost
        or record.bounded_selected_action_signed_error != signed_error
        or record.bounded_selected_action_absolute_error != abs(signed_error)
    ):
        raise M9RunnerError("M9 repair case regret or value error does not reconcile")

    expected_control = (
        "tiny_information_null"
        if expected_case_id.endswith("zero-no-fit-equal-separated-two")
        else None
    )
    if record.control_label != expected_control:
        raise M9RunnerError("M9 repair control label does not reconcile")
    telemetry = record.two_ply_search_telemetry
    telemetry_values = (
        telemetry.catalog_count,
        telemetry.explicit_transition_count,
        telemetry.continuation_event_count,
        telemetry.continuation_call_count,
        telemetry.direct_terminalization_count,
        telemetry.peak_branching_factor,
        telemetry.truncated_catalog_count,
        telemetry.total_event_transition_count,
    )
    if any(type(value) is not int or value < 0 for value in telemetry_values):
        raise M9RunnerError("M9 repair telemetry contains an invalid count")
    if telemetry.total_event_transition_count != (
        telemetry.explicit_transition_count + telemetry.continuation_event_count
    ):
        raise M9RunnerError("M9 repair case transition totals do not reconcile")
    exact_telemetry = record.exact_search_telemetry
    exact_values = (
        exact_telemetry.catalog_count,
        exact_telemetry.explored_transition_count,
        exact_telemetry.terminal_leaf_count,
        exact_telemetry.peak_branching_factor,
        exact_telemetry.truncated_catalog_count,
    )
    if any(type(value) is not int or value < 0 for value in exact_values):
        raise M9RunnerError("M9 repair exact telemetry contains an invalid count")
    if (
        record.action_catalog_complete is not (telemetry.truncated_catalog_count == 0)
        or record.complete
        is not (
            record.action_catalog_complete
            and exact_telemetry.truncated_catalog_count == 0
        )
    ):
        raise M9RunnerError("M9 repair case completeness does not reconcile")


def _validate_two_ply_objective(
    objective: M9TwoPlyObjectiveEvaluation,
    *,
    expected_label: str,
    ordered_case_ids: tuple[str, ...],
) -> None:
    if (
        objective.objective_label != expected_label
        or objective.objective_definition != _OBJECTIVE_DEFINITIONS[expected_label]
    ):
        raise M9RunnerError("M9 repair evaluator objective identity does not reconcile")
    case_ids = tuple(record.case_id for record in objective.cases)
    if case_ids != ordered_case_ids:
        raise M9RunnerError("M9 repair evaluator case IDs do not reconcile")
    for record, case_id in zip(objective.cases, ordered_case_ids, strict=True):
        _validate_two_ply_case(
            record,
            expected_case_id=case_id,
            expected_label=expected_label,
        )

    complete = len(objective.cases) == 45 and all(
        record.complete for record in objective.cases
    )
    every_optimal = all(
        record.selected_action_is_globally_optimal for record in objective.cases
    )
    max_regret = max(
        (record.absolute_first_action_regret for record in objective.cases),
        default=0.0,
    )
    controls = tuple(
        record for record in objective.cases if record.control_label is not None
    )
    controls_pass = len(controls) == 5 and all(
        record.control_label == "tiny_information_null"
        and record.absolute_first_action_regret == 0.0
        and record.relative_first_action_regret == 0.0
        and record.repaired_selected_action_id == record.baseline_action_id
        and len(
            {score.bounded_objective_cost for score in record.two_ply_root_scores}
        )
        == 1
        and len({score.final_net_cost for score in record.exact_root_scores}) == 1
        for record in controls
    )
    counterexamples = tuple(
        record
        for record in objective.cases
        if not record.selected_action_is_globally_optimal
    )
    if (
        objective.complete is not complete
        or objective.every_selected_action_is_globally_optimal is not every_optimal
        or objective.max_absolute_first_action_regret != max_regret
        or objective.information_null_controls_pass is not controls_pass
        or objective.counterexamples != counterexamples
    ):
        raise M9RunnerError("M9 repair objective aggregates do not reconcile")

    catalog_count = sum(
        record.two_ply_search_telemetry.catalog_count for record in objective.cases
    )
    explicit_count = sum(
        record.two_ply_search_telemetry.explicit_transition_count
        for record in objective.cases
    )
    continuation_event_count = sum(
        record.two_ply_search_telemetry.continuation_event_count
        for record in objective.cases
    )
    continuation_call_count = sum(
        record.two_ply_search_telemetry.continuation_call_count
        for record in objective.cases
    )
    direct_terminalization_count = sum(
        record.two_ply_search_telemetry.direct_terminalization_count
        for record in objective.cases
    )
    total_count = sum(
        record.two_ply_search_telemetry.total_event_transition_count
        for record in objective.cases
    )
    truncated_count = sum(
        record.two_ply_search_telemetry.truncated_catalog_count
        for record in objective.cases
    )
    peak_branching = max(
        (
            record.two_ply_search_telemetry.peak_branching_factor
            for record in objective.cases
        ),
        default=0,
    )
    budget = objective.compute_budget
    totals_reconcile = total_count == explicit_count + continuation_event_count
    pass_budget = (
        totals_reconcile
        and truncated_count == 0
        and catalog_count <= 200
        and explicit_count <= 700
        and total_count <= 1200
    )
    if (
        budget.max_catalog_count != 200
        or budget.max_explicit_transition_count != 700
        or budget.max_total_event_transition_count != 1200
        or budget.observed_catalog_count != catalog_count
        or budget.observed_explicit_transition_count != explicit_count
        or budget.observed_continuation_event_count != continuation_event_count
        or budget.observed_continuation_call_count != continuation_call_count
        or budget.observed_direct_terminalization_count != direct_terminalization_count
        or budget.observed_total_event_transition_count != total_count
        or budget.peak_branching_factor != peak_branching
        or budget.observed_truncated_catalog_count != truncated_count
        or budget.totals_reconcile is not totals_reconcile
        or budget.pass_budget is not pass_budget
    ):
        raise M9RunnerError("M9 repair compute budget does not reconcile")

    signed_errors = tuple(
        record.bounded_selected_action_signed_error for record in objective.cases
    )
    exact_match_count = sum(error == 0.0 for error in signed_errors)
    value_error = objective.value_error_summary
    if (
        value_error.case_count != len(objective.cases)
        or value_error.exact_value_match_count != exact_match_count
        or value_error.nonexact_value_count != len(objective.cases) - exact_match_count
        or value_error.min_signed_error != min(signed_errors, default=0.0)
        or value_error.max_signed_error != max(signed_errors, default=0.0)
        or value_error.max_absolute_error
        != max((abs(error) for error in signed_errors), default=0.0)
    ):
        raise M9RunnerError("M9 repair value-error summary does not reconcile")
    conclusion = (
        "pass_decision_feasibility"
        if complete
        and every_optimal
        and max_regret == 0.0
        and controls_pass
        and pass_budget
        else "fail_search_gap"
    )
    if objective.conclusion != conclusion:
        raise M9RunnerError("M9 repair objective conclusion does not reconcile")


def _validate_two_ply_evaluator_result(
    result: M9TwoPlyRepairValidationResult,
    *,
    ordered_case_ids: tuple[str, ...],
) -> None:
    if len(ordered_case_ids) != 45 or len(set(ordered_case_ids)) != 45 or any(
        not case_id for case_id in ordered_case_ids
    ):
        raise M9RunnerError("M9 repair requires exactly 45 unique fresh fixtures")
    if result.case_count != 45 or tuple(result.objective_labels) != _OBJECTIVE_LABELS:
        raise M9RunnerError("M9 repair evaluator census does not reconcile")
    _validate_two_ply_objective(
        result.primary,
        expected_label=_OBJECTIVE_LABELS[0],
        ordered_case_ids=ordered_case_ids,
    )
    _validate_two_ply_objective(
        result.terminal_sensitivity,
        expected_label=_OBJECTIVE_LABELS[1],
        ordered_case_ids=ordered_case_ids,
    )
    expected_controls = tuple(
        case_id
        for case_id in ordered_case_ids
        if case_id.endswith("zero-no-fit-equal-separated-two")
    )
    if result.information_null_control_case_ids != expected_controls or len(
        expected_controls
    ) != 5:
        raise M9RunnerError("M9 repair control census does not reconcile")
    no_reversal = result.primary.conclusion == result.terminal_sensitivity.conclusion
    decision = (
        "pass_decision_feasibility"
        if result.primary.conclusion == "pass_decision_feasibility"
        and result.terminal_sensitivity.conclusion == "pass_decision_feasibility"
        and no_reversal
        else "fail_search_gap"
    )
    if (
        result.terminal_conclusion_does_not_reverse is not no_reversal
        or result.decision != decision
    ):
        raise M9RunnerError("M9 repair aggregate decision does not reconcile")


def _live_original_failure_binding() -> dict[str, object]:
    path = _ORIGINAL_FAILURE_ARTIFACT_PATH
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise M9RunnerError("original M9 failure artifact must be a regular file")
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise M9RunnerError("original M9 failure artifact could not be verified") from error
    if path.name != _ORIGINAL_FAILURE_ARTIFACT_NAME or not isinstance(payload, dict):
        raise M9RunnerError("original M9 failure artifact identity differs")
    raw_sha256 = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    semantic_core = dict(payload)
    result_id = semantic_core.pop("result_id", None)
    content_sha256 = semantic_core.pop("content_sha256", None)
    recomputed_digest = hashlib.sha256(_canonical_bytes(semantic_core)).hexdigest()
    recomputed_content_sha256 = f"sha256:{recomputed_digest}"
    evaluator_result = payload.get("evaluator_result")
    decision = (
        evaluator_result.get("decision")
        if isinstance(evaluator_result, dict)
        else None
    )
    if (
        payload.get("schema_version") != _ORIGINAL_FAILURE_SCHEMA_VERSION
        or result_id != _ORIGINAL_FAILURE_RESULT_ID
        or content_sha256 != _ORIGINAL_FAILURE_CONTENT_SHA256
        or recomputed_content_sha256 != _ORIGINAL_FAILURE_CONTENT_SHA256
        or raw_sha256 != _ORIGINAL_FAILURE_RAW_SHA256
        or decision != "fail_search_gap"
    ):
        raise M9RunnerError("original M9 failure artifact binding does not reconcile")
    return {
        "artifact_name": _ORIGINAL_FAILURE_ARTIFACT_NAME,
        "schema_version": _ORIGINAL_FAILURE_SCHEMA_VERSION,
        "result_id": _ORIGINAL_FAILURE_RESULT_ID,
        "content_sha256": _ORIGINAL_FAILURE_CONTENT_SHA256,
        "raw_file_sha256": _ORIGINAL_FAILURE_RAW_SHA256,
        "decision": "fail_search_gap",
    }


def _run_evaluation_pass() -> _EvaluationPass:
    started = time.perf_counter()
    cases = exhaustive_certificate_cases()
    ordered_case_ids = tuple(case.case_id for case in cases)
    result = evaluate_search_validation(cases)
    _validate_evaluator_result(result, ordered_case_ids=ordered_case_ids)
    payload = _json_value(result)
    if not isinstance(payload, dict):
        raise M9RunnerError("M9 evaluator result did not serialize to a mapping")
    semantic_bytes = _canonical_bytes(payload)
    elapsed = time.perf_counter() - started
    if not math.isfinite(elapsed) or elapsed < 0.0:
        raise M9RunnerError("M9 evaluation produced an invalid wall time")
    return _EvaluationPass(
        ordered_case_ids=ordered_case_ids,
        evaluator_payload=payload,
        evaluator_semantic_bytes=semantic_bytes,
        decision=result.decision,
        wall_seconds=elapsed,
    )


def _run_two_ply_repair_evaluation_pass() -> _EvaluationPass:
    started = time.perf_counter()
    cases = exhaustive_certificate_cases()
    ordered_case_ids = tuple(case.case_id for case in cases)
    result = evaluate_two_ply_repair_validation(cases)
    _validate_two_ply_evaluator_result(result, ordered_case_ids=ordered_case_ids)
    payload = _json_value(result)
    if not isinstance(payload, dict):
        raise M9RunnerError("M9 repair evaluator result did not serialize to a mapping")
    semantic_bytes = _canonical_bytes(payload)
    elapsed = time.perf_counter() - started
    if not math.isfinite(elapsed) or elapsed < 0.0:
        raise M9RunnerError("M9 repair evaluation produced an invalid wall time")
    return _EvaluationPass(
        ordered_case_ids=ordered_case_ids,
        evaluator_payload=payload,
        evaluator_semantic_bytes=semantic_bytes,
        decision=result.decision,
        wall_seconds=elapsed,
    )


def _read_regular_destination(directory_fd: int, name: str) -> bytes | None:
    try:
        named_metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(named_metadata.st_mode):
        raise M9RunnerError("M9 destination must be a regular file")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise M9RunnerError("M9 destination must remain a regular file") from error
    try:
        opened_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(opened_metadata.st_mode):
            raise M9RunnerError("M9 destination must be a regular file")
        if (opened_metadata.st_dev, opened_metadata.st_ino) != (
            named_metadata.st_dev,
            named_metadata.st_ino,
        ):
            raise M9RunnerError("M9 destination identity changed during readback")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _publish_immutable(
    *,
    output_directory: Path,
    name: str,
    payload: bytes,
) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    directory_metadata = output_directory.lstat()
    if not stat.S_ISDIR(directory_metadata.st_mode):
        raise M9RunnerError("M9 output directory must be a real directory")
    directory_flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(output_directory, directory_flags)
    except OSError as error:
        raise M9RunnerError("M9 output directory must be a real directory") from error
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    temporary_created = False
    try:
        existing = _read_regular_destination(directory_fd, name)
        if existing is not None:
            if existing != payload:
                raise M9RunnerError("refusing to replace a different existing artifact")
            return output_directory / name

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o644, dir_fd=directory_fd)
        temporary_created = True
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise M9RunnerError("M9 artifact staging made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        try:
            os.link(
                temporary,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            raced = _read_regular_destination(directory_fd, name)
            if raced != payload:
                if raced is None:
                    raise M9RunnerError("M9 destination vanished during publication") from None
                raise M9RunnerError(
                    "refusing to replace a different existing artifact"
                ) from None
        os.unlink(temporary, dir_fd=directory_fd)
        temporary_created = False
        os.fsync(directory_fd)
        published = _read_regular_destination(directory_fd, name)
        if published != payload:
            raise M9RunnerError("M9 published artifact failed immutable readback")
        return output_directory / name
    finally:
        if temporary_created:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def run_minimal_search_validation(*, output_directory: Path) -> M9RunOutcome:
    """Run two fresh exhaustive passes and publish only byte-identical semantics."""

    first = _run_evaluation_pass()
    second = _run_evaluation_pass()
    if first.ordered_case_ids != second.ordered_case_ids:
        raise M9RunnerError("M9 fresh fixture case IDs differ between repeats")
    if first.evaluator_semantic_bytes != second.evaluator_semantic_bytes:
        raise M9RunnerError("M9 repeated semantic results differ")
    if first.decision != second.decision:
        raise M9RunnerError("M9 repeated decisions differ")

    reproducibility_digest = hashlib.sha256(first.evaluator_semantic_bytes).hexdigest()
    semantic_core: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "fixture_source": _FIXTURE_SOURCE,
        "ordered_case_ids": first.ordered_case_ids,
        "evaluator_result": first.evaluator_payload,
        "fixture_build_count": 2,
        "repeat_count": 2,
        "reproducibility_sha256": f"sha256:{reproducibility_digest}",
        "repeat_semantic_identity_match": True,
        "evaluation_partition_opened": False,
        "claim_ceiling": _CLAIM_CEILING,
    }
    digest = hashlib.sha256(_canonical_bytes(semantic_core)).hexdigest()
    result_id = f"yfm9-{digest[:24]}"
    content_sha256 = f"sha256:{digest}"
    artifact_payload = {
        **semantic_core,
        "result_id": result_id,
        "content_sha256": content_sha256,
    }
    artifact_bytes = _pretty_bytes(artifact_payload)
    artifact_name = f"m9-minimal-search-validation-{result_id}.json"
    artifact_path = _publish_immutable(
        output_directory=Path(output_directory),
        name=artifact_name,
        payload=artifact_bytes,
    )
    return M9RunOutcome(
        artifact_path=artifact_path,
        result_id=result_id,
        content_sha256=content_sha256,
        decision=first.decision,
        pass_wall_seconds=(first.wall_seconds, second.wall_seconds),
    )


def run_two_ply_repair_validation(*, output_directory: Path) -> M9RunOutcome:
    """Run and immutably publish the additive fixed-depth-two M9 repair."""

    original_failure_binding = _live_original_failure_binding()
    first = _run_two_ply_repair_evaluation_pass()
    second = _run_two_ply_repair_evaluation_pass()
    if first.ordered_case_ids != second.ordered_case_ids:
        raise M9RunnerError("M9 repair fresh fixture case IDs differ between repeats")
    if first.evaluator_semantic_bytes != second.evaluator_semantic_bytes:
        raise M9RunnerError("M9 repair repeated semantic results differ")
    if first.decision != second.decision:
        raise M9RunnerError("M9 repair repeated decisions differ")
    if first.decision != "pass_decision_feasibility":
        raise M9RunnerError("M9 repair publication requires a reconciled pass decision")

    reproducibility_digest = hashlib.sha256(first.evaluator_semantic_bytes).hexdigest()
    semantic_core: dict[str, object] = {
        "schema_version": _REPAIR_SCHEMA_VERSION,
        "fixture_source": _FIXTURE_SOURCE,
        "ordered_case_ids": first.ordered_case_ids,
        "evaluator_result": first.evaluator_payload,
        "fixture_build_count": 2,
        "repeat_count": 2,
        "reproducibility_sha256": f"sha256:{reproducibility_digest}",
        "repeat_semantic_identity_match": True,
        "evaluation_partition_opened": False,
        "claim_ceiling": _CLAIM_CEILING,
        "repair_semantics": _REPAIR_SEMANTICS,
        "original_failure_binding": original_failure_binding,
    }
    digest = hashlib.sha256(_canonical_bytes(semantic_core)).hexdigest()
    result_id = f"yfm9r-{digest[:24]}"
    content_sha256 = f"sha256:{digest}"
    artifact_payload = {
        **semantic_core,
        "result_id": result_id,
        "content_sha256": content_sha256,
    }
    artifact_bytes = _pretty_bytes(artifact_payload)
    artifact_name = f"m9-two-ply-repair-validation-{result_id}.json"
    artifact_path = _publish_immutable(
        output_directory=Path(output_directory),
        name=artifact_name,
        payload=artifact_bytes,
    )
    return M9RunOutcome(
        artifact_path=artifact_path,
        result_id=result_id,
        content_sha256=content_sha256,
        decision=first.decision,
        pass_wall_seconds=(first.wall_seconds, second.wall_seconds),
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-directory",
        type=Path,
        required=True,
        help="Existing or creatable directory for the immutable M9 result",
    )
    parser.add_argument(
        "--two-ply-repair",
        action="store_true",
        help="Run the additive fixed-depth-two repair validation",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.two_ply_repair:
            outcome = run_two_ply_repair_validation(
                output_directory=args.output_directory,
            )
        else:
            outcome = run_minimal_search_validation(
                output_directory=args.output_directory,
            )
    except M9RunnerError as error:
        label = (
            "M9 two-ply repair validation"
            if args.two_ply_repair
            else "M9 minimal search validation"
        )
        print(f"{label} failed closed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "artifact_path": str(outcome.artifact_path),
                "content_sha256": outcome.content_sha256,
                "decision": outcome.decision,
                "pass_wall_seconds": list(outcome.pass_wall_seconds),
                "result_id": outcome.result_id,
            },
            allow_nan=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
