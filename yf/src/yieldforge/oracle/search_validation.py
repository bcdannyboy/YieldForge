"""Calibration-only exact finite search for minimal M9 validation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

from yieldforge.baseline.contracts import M7ActionKind
from yieldforge.baseline.replay import (
    M7ActionCatalog,
    M7ReplayCursor,
    M7ReplayRuntime,
    apply_m7_action_descriptor,
    enumerate_m7_action_catalog,
    run_m7_continuation,
    select_m7_fallback,
)
from yieldforge.replay.contracts import rounded_cost

M9ObjectiveLabel = Literal["scrap_only", "zero_total_terminal_credit"]
M9Decision = Literal["pass_decision_feasibility", "fail_search_gap"]
M9ControlLabel = Literal["tiny_information_null"]

_PRIMARY_OBJECTIVE: M9ObjectiveLabel = "scrap_only"
_TERMINAL_SENSITIVITY_OBJECTIVE: M9ObjectiveLabel = "zero_total_terminal_credit"
_OBJECTIVE_LABELS = (_PRIMARY_OBJECTIVE, _TERMINAL_SENSITIVITY_OBJECTIVE)
_OBJECTIVE_DEFINITIONS: dict[M9ObjectiveLabel, str] = {
    _PRIMARY_OBJECTIVE: "m7_final_net_cost_including_terminal_scrap_credit",
    _TERMINAL_SENSITIVITY_OBJECTIVE: (
        "counterfactual_m7_final_net_cost_with_terminal_scrap_credit_added_back_only"
    ),
}
_INFORMATION_NULL_SUFFIX = "zero-no-fit-equal-separated-two"


class _FutureVisibility(Protocol):
    def visible_suffix(self, *, current_position: int) -> tuple[object, ...]: ...


class M9ExactSearchRequest(Protocol):
    runtime: M7ReplayRuntime
    cursor: M7ReplayCursor
    visibility: _FutureVisibility


class M9RegisteredSearchCase(Protocol):
    case_id: str
    request: M9ExactSearchRequest


@dataclass(frozen=True)
class M9ExactRootScore:
    """Exact terminal cost reachable from one root action."""

    action_id: str
    kind: M7ActionKind
    final_net_cost: float


@dataclass(frozen=True)
class M9ExactSearchTelemetry:
    """Bounded work counts for one complete finite search."""

    catalog_count: int
    explored_transition_count: int
    terminal_leaf_count: int
    peak_branching_factor: int
    truncated_catalog_count: int


@dataclass(frozen=True)
class M9ExactSearchResult:
    """Complete root score vector and globally optimal first-action set."""

    start_event_position: int
    stop_event_position: int
    include_terminal_credit: bool
    optimal_final_net_cost: float
    optimal_first_action_ids: tuple[str, ...]
    root_scores: tuple[M9ExactRootScore, ...]
    complete: bool
    telemetry: M9ExactSearchTelemetry


@dataclass(frozen=True)
class M9OneStepScore:
    """One frozen-M7 rollout score under the explicitly named objective."""

    action_id: str
    objective_cost: float


@dataclass(frozen=True)
class M9OneStepResult:
    """Complete one-step rollout vector with the M8 exact tie rule."""

    objective_label: M9ObjectiveLabel
    objective_definition: str
    start_event_position: int
    stop_event_position: int
    baseline_action_id: str
    selected_action_id: str
    scores: tuple[M9OneStepScore, ...]
    action_catalog_complete: bool
    truncated_catalog_count: int


@dataclass(frozen=True)
class M9CaseComparison:
    """One registered case compared against globally reoptimized continuation."""

    case_id: str
    policy_name: str
    objective_label: M9ObjectiveLabel
    objective_definition: str
    control_label: M9ControlLabel | None
    baseline_action_id: str
    rollout_selected_action_id: str
    one_step_scores: tuple[M9OneStepScore, ...]
    exact_root_scores: tuple[M9ExactRootScore, ...]
    exact_optimal_first_action_ids: tuple[str, ...]
    exact_optimal_cost: float
    exact_cost_after_selected_first_action: float
    absolute_first_action_regret: float
    relative_first_action_regret: float | None
    selected_action_is_globally_optimal: bool
    action_catalog_complete: bool
    complete: bool
    exact_search_telemetry: M9ExactSearchTelemetry


@dataclass(frozen=True)
class M9ObjectiveEvaluation:
    """Ordered 45-case result for one terminal-value objective."""

    objective_label: M9ObjectiveLabel
    objective_definition: str
    cases: tuple[M9CaseComparison, ...]
    complete: bool
    every_selected_action_is_globally_optimal: bool
    max_absolute_first_action_regret: float
    information_null_controls_pass: bool
    counterexamples: tuple[M9CaseComparison, ...]
    conclusion: M9Decision


@dataclass(frozen=True)
class M9SearchValidationResult:
    """Primary and terminal-sensitivity M9 decision evidence."""

    case_count: int
    objective_labels: tuple[M9ObjectiveLabel, M9ObjectiveLabel]
    information_null_control_case_ids: tuple[str, ...]
    primary: M9ObjectiveEvaluation
    terminal_sensitivity: M9ObjectiveEvaluation
    terminal_conclusion_does_not_reverse: bool
    decision: M9Decision


@dataclass
class _MutableTelemetry:
    catalog_count: int = 0
    explored_transition_count: int = 0
    terminal_leaf_count: int = 0
    peak_branching_factor: int = 0
    truncated_catalog_count: int = 0


def _objective_parameters(objective_label: M9ObjectiveLabel) -> tuple[bool, str]:
    try:
        definition = _OBJECTIVE_DEFINITIONS[objective_label]
    except KeyError as error:
        raise ValueError("M9 terminal objective is not registered") from error
    return objective_label == _PRIMARY_OBJECTIVE, definition


def _visible_stop(request: M9ExactSearchRequest) -> tuple[int, int]:
    start = request.cursor.next_event_position
    visible_suffix = request.visibility.visible_suffix(current_position=start)
    stop = start + 1 + len(visible_suffix)
    if stop > len(request.runtime.replay_input.instances):
        raise ValueError("M9 search visibility extends beyond the replay stream")
    return start, stop


def _terminal_cost(
    runtime: M7ReplayRuntime,
    cursor: M7ReplayCursor,
    stop_event_position: int,
    *,
    include_terminal_credit: bool,
) -> float:
    terminal = run_m7_continuation(
        runtime,
        cursor=cursor,
        stop_event_position=stop_event_position,
    )
    if terminal.events:
        raise RuntimeError("M9 exact terminalization replayed an event")
    cost = terminal.final_costs.net_cost
    if not include_terminal_credit:
        cost = rounded_cost(cost + terminal.final_costs.terminal_scrap_credit)
    return cost


def _complete_catalog(
    runtime: M7ReplayRuntime,
    cursor: M7ReplayCursor,
    telemetry: _MutableTelemetry,
) -> M7ActionCatalog:
    catalog = enumerate_m7_action_catalog(runtime, cursor=cursor, complete=True)
    telemetry.catalog_count += 1
    telemetry.peak_branching_factor = max(
        telemetry.peak_branching_factor,
        len(catalog.actions),
    )
    truncated = catalog.generated.fit_search_budget_truncated_count
    telemetry.truncated_catalog_count += truncated
    if truncated:
        raise ValueError("M9 exact search encountered a truncated action catalog")
    if not catalog.actions:
        raise ValueError("M9 exact search encountered an empty action catalog")
    return catalog


def solve_exact_search(
    request: M9ExactSearchRequest,
    *,
    include_terminal_credit: bool = True,
) -> M9ExactSearchResult:
    """Exhaustively reoptimize every visible future M7 decision."""

    runtime = request.runtime
    root_cursor = request.cursor
    start, stop = _visible_stop(request)

    telemetry = _MutableTelemetry()

    def recurse(cursor: M7ReplayCursor) -> float:
        if cursor.next_event_position == stop:
            telemetry.terminal_leaf_count += 1
            return _terminal_cost(
                runtime,
                cursor,
                stop,
                include_terminal_credit=include_terminal_credit,
            )
        if cursor.next_event_position > stop:
            raise RuntimeError("M9 exact search advanced beyond the visible suffix")
        catalog = _complete_catalog(runtime, cursor, telemetry)
        branch_costs: list[float] = []
        for descriptor in catalog.actions:
            telemetry.explored_transition_count += 1
            step = apply_m7_action_descriptor(
                runtime,
                cursor=cursor,
                catalog=catalog,
                descriptor=descriptor,
                decision_key=(f"m9_exact_action_id={descriptor.action_id}",),
            )
            branch_costs.append(recurse(step.cursor))
        return min(branch_costs)

    root_catalog = _complete_catalog(runtime, root_cursor, telemetry)
    root_scores = []
    for descriptor in root_catalog.actions:
        telemetry.explored_transition_count += 1
        step = apply_m7_action_descriptor(
            runtime,
            cursor=root_cursor,
            catalog=root_catalog,
            descriptor=descriptor,
            decision_key=(f"m9_exact_action_id={descriptor.action_id}",),
        )
        root_scores.append(
            M9ExactRootScore(
                action_id=descriptor.action_id,
                kind=descriptor.kind,
                final_net_cost=recurse(step.cursor),
            )
        )

    score_tuple = tuple(root_scores)
    optimum = min(item.final_net_cost for item in score_tuple)
    immutable_telemetry = M9ExactSearchTelemetry(
        catalog_count=telemetry.catalog_count,
        explored_transition_count=telemetry.explored_transition_count,
        terminal_leaf_count=telemetry.terminal_leaf_count,
        peak_branching_factor=telemetry.peak_branching_factor,
        truncated_catalog_count=telemetry.truncated_catalog_count,
    )
    return M9ExactSearchResult(
        start_event_position=start,
        stop_event_position=stop,
        include_terminal_credit=include_terminal_credit,
        optimal_final_net_cost=optimum,
        optimal_first_action_ids=tuple(
            item.action_id for item in score_tuple if item.final_net_cost == optimum
        ),
        root_scores=score_tuple,
        complete=immutable_telemetry.truncated_catalog_count == 0,
        telemetry=immutable_telemetry,
    )


def score_one_step_rollout(
    request: M9ExactSearchRequest,
    *,
    objective_label: M9ObjectiveLabel,
) -> M9OneStepResult:
    """Score every first action followed by the unchanged frozen M7 policy."""

    include_terminal_credit, objective_definition = _objective_parameters(objective_label)
    start, stop = _visible_stop(request)
    catalog = enumerate_m7_action_catalog(
        request.runtime,
        cursor=request.cursor,
        complete=True,
    )
    truncated = catalog.generated.fit_search_budget_truncated_count
    if truncated:
        raise ValueError("M9 one-step scoring encountered a truncated action catalog")
    if not catalog.actions:
        raise ValueError("M9 one-step scoring encountered an empty action catalog")
    fallback = select_m7_fallback(
        catalog,
        policy=request.runtime.replay_input.policy,
    )
    scores: list[M9OneStepScore] = []
    for descriptor in catalog.actions:
        step = apply_m7_action_descriptor(
            request.runtime,
            cursor=request.cursor,
            catalog=catalog,
            descriptor=descriptor,
            decision_key=(f"m9_one_step_action_id={descriptor.action_id}",),
        )
        terminal = run_m7_continuation(
            request.runtime,
            cursor=step.cursor,
            stop_event_position=stop,
        )
        objective_cost = terminal.final_costs.net_cost
        if not include_terminal_credit:
            objective_cost = rounded_cost(
                objective_cost + terminal.final_costs.terminal_scrap_credit
            )
        scores.append(
            M9OneStepScore(
                action_id=descriptor.action_id,
                objective_cost=objective_cost,
            )
        )
    score_tuple = tuple(scores)
    selected = min(
        score_tuple,
        key=lambda item: (
            item.objective_cost,
            item.action_id != fallback.action_id,
            item.action_id,
        ),
    )
    return M9OneStepResult(
        objective_label=objective_label,
        objective_definition=objective_definition,
        start_event_position=start,
        stop_event_position=stop,
        baseline_action_id=fallback.action_id,
        selected_action_id=selected.action_id,
        scores=score_tuple,
        action_catalog_complete=truncated == 0,
        truncated_catalog_count=truncated,
    )


def _relative_regret(*, absolute_regret: float, optimum: float) -> float | None:
    if absolute_regret == 0.0:
        return 0.0
    if optimum == 0.0:
        return None
    return rounded_cost(absolute_regret / abs(optimum))


def _compare_case(
    case: M9RegisteredSearchCase,
    *,
    objective_label: M9ObjectiveLabel,
) -> M9CaseComparison:
    include_terminal_credit, objective_definition = _objective_parameters(objective_label)
    one_step = score_one_step_rollout(case.request, objective_label=objective_label)
    exact = solve_exact_search(
        case.request,
        include_terminal_credit=include_terminal_credit,
    )
    one_step_action_ids = tuple(item.action_id for item in one_step.scores)
    exact_action_ids = tuple(item.action_id for item in exact.root_scores)
    if one_step_action_ids != exact_action_ids:
        raise RuntimeError("M9 one-step and exact root action catalogs differ")
    exact_by_action = {item.action_id: item.final_net_cost for item in exact.root_scores}
    selected_exact_cost = exact_by_action[one_step.selected_action_id]
    absolute_regret = rounded_cost(selected_exact_cost - exact.optimal_final_net_cost)
    if absolute_regret < 0.0:
        raise RuntimeError("M9 selected-action exact cost is below the exact optimum")
    is_control = case.case_id.endswith(_INFORMATION_NULL_SUFFIX)
    return M9CaseComparison(
        case_id=case.case_id,
        policy_name=case.request.runtime.replay_input.policy.name.value,
        objective_label=objective_label,
        objective_definition=objective_definition,
        control_label="tiny_information_null" if is_control else None,
        baseline_action_id=one_step.baseline_action_id,
        rollout_selected_action_id=one_step.selected_action_id,
        one_step_scores=one_step.scores,
        exact_root_scores=exact.root_scores,
        exact_optimal_first_action_ids=exact.optimal_first_action_ids,
        exact_optimal_cost=exact.optimal_final_net_cost,
        exact_cost_after_selected_first_action=selected_exact_cost,
        absolute_first_action_regret=absolute_regret,
        relative_first_action_regret=_relative_regret(
            absolute_regret=absolute_regret,
            optimum=exact.optimal_final_net_cost,
        ),
        selected_action_is_globally_optimal=(
            one_step.selected_action_id in exact.optimal_first_action_ids
        ),
        action_catalog_complete=one_step.action_catalog_complete,
        complete=(one_step.action_catalog_complete and exact.complete),
        exact_search_telemetry=exact.telemetry,
    )


def _controls_pass(records: tuple[M9CaseComparison, ...]) -> bool:
    controls = tuple(record for record in records if record.control_label is not None)
    return len(controls) == 5 and all(
        record.control_label == "tiny_information_null"
        and record.absolute_first_action_regret == 0.0
        and record.relative_first_action_regret == 0.0
        and record.rollout_selected_action_id == record.baseline_action_id
        and len({score.objective_cost for score in record.one_step_scores}) == 1
        and len({score.final_net_cost for score in record.exact_root_scores}) == 1
        for record in controls
    )


def _evaluate_objective(
    cases: tuple[M9RegisteredSearchCase, ...],
    *,
    objective_label: M9ObjectiveLabel,
) -> M9ObjectiveEvaluation:
    _, objective_definition = _objective_parameters(objective_label)
    records = tuple(
        _compare_case(case, objective_label=objective_label) for case in cases
    )
    complete = len(records) == 45 and all(record.complete for record in records)
    every_optimal = all(record.selected_action_is_globally_optimal for record in records)
    max_regret = max(
        (record.absolute_first_action_regret for record in records),
        default=0.0,
    )
    controls_pass = _controls_pass(records)
    counterexamples = tuple(
        record for record in records if not record.selected_action_is_globally_optimal
    )
    conclusion: M9Decision = (
        "pass_decision_feasibility"
        if complete and every_optimal and max_regret == 0.0 and controls_pass
        else "fail_search_gap"
    )
    return M9ObjectiveEvaluation(
        objective_label=objective_label,
        objective_definition=objective_definition,
        cases=records,
        complete=complete,
        every_selected_action_is_globally_optimal=every_optimal,
        max_absolute_first_action_regret=max_regret,
        information_null_controls_pass=controls_pass,
        counterexamples=counterexamples,
        conclusion=conclusion,
    )


def evaluate_search_validation(
    registered_cases: Iterable[M9RegisteredSearchCase],
) -> M9SearchValidationResult:
    """Evaluate the ordered registered matrix under both terminal objectives."""

    cases = tuple(registered_cases)
    case_ids = tuple(case.case_id for case in cases)
    if not cases:
        raise ValueError("M9 search validation requires at least one case")
    if any(not case_id for case_id in case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("M9 registered case IDs must be nonempty and unique")
    controls = tuple(
        case_id for case_id in case_ids if case_id.endswith(_INFORMATION_NULL_SUFFIX)
    )
    primary = _evaluate_objective(cases, objective_label=_PRIMARY_OBJECTIVE)
    sensitivity = _evaluate_objective(
        cases,
        objective_label=_TERMINAL_SENSITIVITY_OBJECTIVE,
    )
    no_reversal = primary.conclusion == sensitivity.conclusion
    decision: M9Decision = (
        "pass_decision_feasibility"
        if primary.complete
        and primary.every_selected_action_is_globally_optimal
        and primary.max_absolute_first_action_regret == 0.0
        and primary.information_null_controls_pass
        and no_reversal
        else "fail_search_gap"
    )
    return M9SearchValidationResult(
        case_count=len(cases),
        objective_labels=_OBJECTIVE_LABELS,
        information_null_control_case_ids=controls,
        primary=primary,
        terminal_sensitivity=sensitivity,
        terminal_conclusion_does_not_reverse=no_reversal,
        decision=decision,
    )


__all__ = [
    "M9CaseComparison",
    "M9Decision",
    "M9ExactRootScore",
    "M9ExactSearchRequest",
    "M9ExactSearchResult",
    "M9ExactSearchTelemetry",
    "M9ObjectiveEvaluation",
    "M9ObjectiveLabel",
    "M9OneStepResult",
    "M9OneStepScore",
    "M9RegisteredSearchCase",
    "M9SearchValidationResult",
    "evaluate_search_validation",
    "score_one_step_rollout",
    "solve_exact_search",
]
