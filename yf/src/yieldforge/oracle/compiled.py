"""Compiled inventory-independent M7 winners and future relevance helpers."""

from __future__ import annotations

import gc
import math
import os
import stat
import weakref
from collections import OrderedDict
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType, UnionType
from typing import Annotated, Any, Literal, TypeAliasType, Union, get_args, get_origin

from pydantic import BaseModel
from shapely import MultiPolygon, Polygon

from yieldforge.baseline.archives import (
    VerifiedCandidateRejectionLayout,
    VerifiedProblemCandidates,
)
from yieldforge.baseline.contracts import (
    M7CandidateArchiveEvidence,
    M7CandidateSetEvidence,
    ReusableGeometryProblem,
    TemporalInstanceBinding,
)
from yieldforge.baseline.geometry import (
    PreparedLayoutFootprint,
    PreparedTranslationRejectionLayout,
    PreparedTranslationRejectionRemnant,
    TranslationRejectionCertificate,
    certify_prepared_translation_impossible,
    certify_translation_impossible,
    prepare_layout_footprint,
    prepare_translation_rejection_layout,
    prepare_translation_rejection_remnant,
)
from yieldforge.baseline.replay import (
    M7ReplayCursor,
    M7ReplayInput,
    M7ReplayRuntime,
    M7SemanticRuntimeSnapshot,
    M7StandardActionProfile,
    _M7SnapshotReplayRuntime,
    enumerate_m7_action_catalog,
    m7_semantic_runtime_sha256,
    select_m7_fallback,
    snapshot_m7_replay_runtime,
)
from yieldforge.domain import (
    Candidate,
    CandidateReportType,
    Part,
    Placement,
    ProjectionMode,
    SolverProjectionBinding,
    StripPackingProblem,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle.frontier import (
    DominanceEdge,
    ParetoFrontier,
    RejectionScalar,
    build_pareto_frontier,
)
from yieldforge.oracle.profiling import increment_profile_count, profile_phase
from yieldforge.replay.contracts import InventoryItem, ReplayCostLedger
from yieldforge.residuals.contracts import ResidualRuleSet
from yieldforge.reuse.contracts import (
    CanonicalPolygon,
    MaterialIdentity,
    MaterialProvenance,
    RemnantFitConfig,
    RemnantLineage,
    RemnantStock,
    ReuseAccounting,
    derive_remnant_id,
    polygon_from_record,
)
from yieldforge.reuse.geometry import material_key
from yieldforge.temporal_benchmark.contracts import CandidateArchiveRequirement

_MAX_PREPARED_LAYOUT_CACHE_PROBLEMS = 2


class M8PreparedFrontierIntegrityError(ValueError):
    """Malformed prepared scalar authority that must never become fallback."""


@dataclass(frozen=True, slots=True)
class _VerifiedRejectionLayoutProjection:
    """Exact primitive snapshot of one caller-owned retained scalar."""

    problem_id: str
    problem_sha256: str
    candidate_set_id: str
    candidate_set_sha256: str
    candidate_id: str
    source_transform_sha256: str
    fit_config_sha256: str
    layout_area: float
    layout_width: float
    layout_height: float
    layout_bounds: tuple[float, float, float, float]
    material_binding_scope: str


def _exact_finite_float(value: object, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise TypeError("M8 prepared scalar numeric type differs")
    normalized = float(value)
    if positive and normalized <= 0:
        raise ValueError("M8 prepared scalar numeric value differs")
    return normalized


def _exact_instance_state(
    value: object,
    expected_type: type[object],
    field_names: tuple[str, ...],
) -> dict[str, object]:
    """Read exact native model storage without dispatching instance serializers."""

    if type(value) is not expected_type:
        raise TypeError("M8 prepared evidence node type differs")
    state = object.__getattribute__(value, "__dict__")
    if (
        type(state) is not dict
        or any(type(name) is not str for name in state)
        or set(state) != set(field_names)
    ):
        raise TypeError("M8 prepared evidence node state differs")
    return state


_M7_REPLAY_RUNTIME_STATE_FIELDS = (
    "replay_input",
    "runtime_candidates",
    "rules",
    "runtime_metrics",
    "standard_profile_cache",
    "fit_search_cache",
    "shared_fit_search_cache",
    "prepared_layout_cache",
    "standard_profile_executor",
    "jagua_executable",
    "jagua_differential_audit",
)


def _exact_prepared_runtime_state(runtime: object) -> dict[str, object]:
    """Read fixed runtime roots without invoking caller-owned attribute protocols."""

    runtime_type = type(runtime)
    if runtime_type is M7ReplayRuntime:
        expected_fields = _M7_REPLAY_RUNTIME_STATE_FIELDS
    elif runtime_type is _M7SnapshotReplayRuntime:
        expected_fields = (*_M7_REPLAY_RUNTIME_STATE_FIELDS, "_snapshot_sealed")
    else:
        raise TypeError("M8 prepared source runtime type differs")
    state = object.__getattribute__(runtime, "__dict__")
    if (
        type(state) is not dict
        or any(type(name) is not str for name in state)
        or set(state) != set(expected_fields)
        or (
            runtime_type is _M7SnapshotReplayRuntime
            and state["_snapshot_sealed"] is not True
        )
    ):
        raise TypeError("M8 prepared source runtime state differs")
    return state


def _exact_prepared_replay_input_state(runtime_state: dict[str, object]) -> dict[str, object]:
    replay_input = runtime_state["replay_input"]
    return _exact_instance_state(
        replay_input,
        M7ReplayInput,
        tuple(M7ReplayInput.model_fields),
    )


def _require_exact_prepared_source_key(key: object) -> tuple[str, str]:
    if (
        type(key) is not tuple
        or len(key) != 2
        or any(type(value) is not str or not value for value in tuple.__iter__(key))
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: source key type"
        )
    return key


def _validate_exact_annotation(value: object, annotation: object) -> None:
    """Attest a Pydantic field graph without invoking caller-owned protocols."""

    if isinstance(annotation, TypeAliasType):
        _validate_exact_annotation(value, annotation.__value__)
        return
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Annotated:
        _validate_exact_annotation(value, arguments[0])
        return
    if origin is Literal:
        if not any(type(value) is type(choice) and value == choice for choice in arguments):
            raise TypeError("M8 prepared source literal differs")
        return
    if origin in (Union, UnionType):
        for candidate in arguments:
            try:
                _validate_exact_annotation(value, candidate)
            except (TypeError, ValueError):
                continue
            return
        raise TypeError("M8 prepared source union differs")
    if origin is tuple:
        if type(value) is not tuple:
            raise TypeError("M8 prepared source tuple differs")
        values = tuple(tuple.__iter__(value))
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            for item in values:
                _validate_exact_annotation(item, arguments[0])
            return
        if len(values) != len(arguments):
            raise TypeError("M8 prepared source tuple arity differs")
        for item, item_annotation in zip(values, arguments, strict=True):
            _validate_exact_annotation(item, item_annotation)
        return
    if origin is list:
        if type(value) is _PreparedMutationTrackedList:
            guard = _prepared_tracked_list_guard(value)
            if not guard.active:
                raise TypeError("M8 prepared source list guard differs")
        elif type(value) is not list:
            raise TypeError("M8 prepared source list differs")
        for item in list.__iter__(value):
            _validate_exact_annotation(item, arguments[0])
        return
    if origin is dict:
        if type(value) is not dict:
            raise TypeError("M8 prepared source mapping differs")
        for key, item in dict.items(value):
            _validate_exact_annotation(key, arguments[0])
            _validate_exact_annotation(item, arguments[1])
        return
    if annotation is Any:
        raise TypeError("M8 prepared source untyped field differs")
    if annotation is datetime:
        if type(value) is not datetime or value.tzinfo is not UTC:
            raise TypeError("M8 prepared source datetime differs")
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        _validate_exact_model_graph(value, annotation)
        return
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if type(value) is not annotation:
            raise TypeError("M8 prepared source enum differs")
        return
    if isinstance(annotation, type):
        if type(value) is not annotation:
            raise TypeError("M8 prepared source scalar differs")
        return
    raise TypeError("M8 prepared source annotation differs")


def _validate_exact_model_graph(value: object, expected_type: type[BaseModel]) -> None:
    field_names = tuple(expected_type.model_fields)
    state = _exact_instance_state(value, expected_type, field_names)
    for name, field_info in expected_type.model_fields.items():
        _validate_exact_annotation(state[name], field_info.annotation)


def _verified_rejection_layout_projection(
    retained: VerifiedCandidateRejectionLayout,
) -> _VerifiedRejectionLayoutProjection:
    if type(retained) is not VerifiedCandidateRejectionLayout:
        raise TypeError("M8 prepared rejection scalar type differs")
    identities = (
        retained.problem_id,
        retained.problem_sha256,
        retained.candidate_set_id,
        retained.candidate_set_sha256,
        retained.candidate_id,
        retained.source_transform_sha256,
        retained.fit_config_sha256,
        retained.material_binding_scope,
    )
    if any(type(value) is not str or not value for value in identities):
        raise TypeError("M8 prepared rejection scalar identity differs")
    bounds = retained.layout_bounds
    if type(bounds) is not tuple or len(bounds) != 4:
        raise TypeError("M8 prepared rejection scalar bounds differ")
    normalized_bounds = tuple(_exact_finite_float(value) for value in bounds)
    area = _exact_finite_float(retained.layout_area, positive=True)
    width = _exact_finite_float(retained.layout_width, positive=True)
    height = _exact_finite_float(retained.layout_height, positive=True)
    min_x, min_y, max_x, max_y = normalized_bounds
    if (
        max_x < min_x
        or max_y < min_y
        or width != float(max_x - min_x)
        or height != float(max_y - min_y)
        or retained.material_binding_scope != "temporal_event"
    ):
        raise ValueError("M8 prepared rejection scalar measurement differs")
    return _VerifiedRejectionLayoutProjection(
        problem_id=retained.problem_id,
        problem_sha256=retained.problem_sha256,
        candidate_set_id=retained.candidate_set_id,
        candidate_set_sha256=retained.candidate_set_sha256,
        candidate_id=retained.candidate_id,
        source_transform_sha256=retained.source_transform_sha256,
        fit_config_sha256=retained.fit_config_sha256,
        layout_area=area,
        layout_width=width,
        layout_height=height,
        layout_bounds=normalized_bounds,
        material_binding_scope=retained.material_binding_scope,
    )


def _verified_rejection_layout_projections(
    verified: VerifiedProblemCandidates,
) -> tuple[_VerifiedRejectionLayoutProjection, ...]:
    if type(verified) is not VerifiedProblemCandidates:
        raise TypeError("M8 prepared candidate archive type differs")
    if type(verified.rejection_layouts) is not tuple:
        raise TypeError("M8 prepared rejection scalar collection differs")
    return tuple(
        _verified_rejection_layout_projection(retained)
        for retained in verified.rejection_layouts
    )


def _verified_rejection_layout_projection_payload(
    retained: _VerifiedRejectionLayoutProjection,
) -> dict[str, object]:
    if type(retained) is not _VerifiedRejectionLayoutProjection:
        raise TypeError("M8 prepared rejection projection type differs")
    return {
        "problem_id": retained.problem_id,
        "problem_sha256": retained.problem_sha256,
        "candidate_set_id": retained.candidate_set_id,
        "candidate_set_sha256": retained.candidate_set_sha256,
        "candidate_id": retained.candidate_id,
        "source_transform_sha256": retained.source_transform_sha256,
        "fit_config_sha256": retained.fit_config_sha256,
        "layout_area": retained.layout_area,
        "layout_width": retained.layout_width,
        "layout_height": retained.layout_height,
        "layout_bounds": retained.layout_bounds,
        "material_binding_scope": retained.material_binding_scope,
    }


def _candidate_transform_projection(candidate: Candidate) -> tuple[str, str]:
    placements_source = candidate.placements if type(candidate) is Candidate else None
    if (
        type(candidate) is not Candidate
        or type(candidate.candidate_id) is not str
        or not candidate.candidate_id
        or type(placements_source) not in (list, _PreparedMutationTrackedList)
    ):
        raise TypeError("M8 prepared candidate transform type differs")
    if type(placements_source) is _PreparedMutationTrackedList:
        guard = _prepared_tracked_list_guard(placements_source)
        if not guard.active:
            raise TypeError("M8 prepared candidate transform guard differs")
    placements: list[dict[str, object]] = []
    for placement in list.__iter__(placements_source):
        if (
            type(placement) is not Placement
            or type(placement.part_id) is not str
            or not placement.part_id
            or type(placement.translation) is not tuple
            or len(placement.translation) != 2
        ):
            raise TypeError("M8 prepared candidate placement type differs")
        placements.append(
            {
                "part_id": placement.part_id,
                "rotation": _exact_finite_float(placement.rotation),
                "translation": tuple(
                    _exact_finite_float(value) for value in placement.translation
                ),
            }
        )
    transform_sha256 = "sha256:" + semantic_sha256(
        {
            "schema_version": "yieldforge.m7-candidate-transform.v1",
            "candidate_id": candidate.candidate_id,
            "placements": placements,
        }
    )
    return candidate.candidate_id, transform_sha256


def _exact_source_list_values(value: object) -> tuple[object, ...]:
    """Read only ordinary or actively guarded source lists via the builtin iterator."""

    if type(value) is _PreparedMutationTrackedList:
        guard = _prepared_tracked_list_guard(value)
        if not guard.active:
            raise TypeError("M8 prepared source list guard differs")
    elif type(value) is not list:
        raise TypeError("M8 prepared source list type differs")
    return tuple(list.__iter__(value))


def _solver_projection_payload(projection: SolverProjectionBinding) -> dict[str, object]:
    fields = (
        "schema_version",
        "mode",
        "transform_convention",
        "projection_sha256",
        "assumption_codes",
        "intervention_codes",
        "source_flip_part_count",
    )
    state = _exact_instance_state(projection, SolverProjectionBinding, fields)
    strings = (
        state["schema_version"],
        state["transform_convention"],
        state["projection_sha256"],
    )
    code_tuples = (state["assumption_codes"], state["intervention_codes"])
    if (
        any(type(value) is not str or not value for value in strings)
        or type(state["mode"]) is not ProjectionMode
        or any(
            type(values) is not tuple
            or any(type(value) is not str or not value for value in values)
            for values in code_tuples
        )
        or type(state["source_flip_part_count"]) is not int
        or state["source_flip_part_count"] < 0
    ):
        raise TypeError("M8 prepared solver projection differs")
    return {
        "schema_version": state["schema_version"],
        "mode": state["mode"].value,
        "transform_convention": state["transform_convention"],
        "projection_sha256": state["projection_sha256"],
        "assumption_codes": state["assumption_codes"],
        "intervention_codes": state["intervention_codes"],
        "source_flip_part_count": state["source_flip_part_count"],
    }


def _part_payload(part: Part) -> dict[str, object]:
    state = _exact_instance_state(
        part,
        Part,
        ("id", "shape", "demand", "allowed_orientations"),
    )
    shape_values = _exact_source_list_values(state["shape"])
    shape: list[tuple[float, float]] = []
    for point in shape_values:
        if type(point) is not tuple or len(point) != 2:
            raise TypeError("M8 prepared part shape differs")
        shape.append(tuple(_exact_finite_float(value) for value in point))
    orientations_source = state["allowed_orientations"]
    orientations = (
        None
        if orientations_source is None
        else tuple(
            _exact_finite_float(value)
            for value in _exact_source_list_values(orientations_source)
        )
    )
    if (
        type(state["id"]) is not str
        or not state["id"]
        or type(state["demand"]) is not int
        or state["demand"] <= 0
        or len(shape) < 3
    ):
        raise TypeError("M8 prepared part identity differs")
    return {
        "id": state["id"],
        "shape": shape,
        "demand": state["demand"],
        "allowed_orientations": orientations,
    }


def _strip_problem_payload(problem: StripPackingProblem) -> dict[str, object]:
    state = _exact_instance_state(
        problem,
        StripPackingProblem,
        ("name", "strip_height", "sheet_length", "parts"),
    )
    if type(state["name"]) is not str or not state["name"]:
        raise TypeError("M8 prepared strip problem identity differs")
    return {
        "name": state["name"],
        "strip_height": _exact_finite_float(state["strip_height"], positive=True),
        "sheet_length": _exact_finite_float(state["sheet_length"], positive=True),
        "parts": tuple(_part_payload(item) for item in _exact_source_list_values(state["parts"])),
    }


def _candidate_archive_requirement_payload(
    requirement: CandidateArchiveRequirement,
) -> dict[str, object]:
    fields = (
        "solver_name",
        "solver_version",
        "seeds",
        "seconds_per_seed",
        "num_workers",
        "early_termination",
        "min_items_separation",
        "archive_requirement",
    )
    state = _exact_instance_state(requirement, CandidateArchiveRequirement, fields)
    if (
        any(
            type(state[name]) is not str or not state[name]
            for name in ("solver_name", "solver_version", "archive_requirement")
        )
        or type(state["seeds"]) is not tuple
        or any(type(value) is not int for value in state["seeds"])
        or type(state["seconds_per_seed"]) is not int
        or state["seconds_per_seed"] <= 0
        or type(state["num_workers"]) is not int
        or state["num_workers"] <= 0
        or type(state["early_termination"]) is not bool
        or state["min_items_separation"] is not None
    ):
        raise TypeError("M8 prepared candidate archive requirement differs")
    return {name: state[name] for name in fields}


def _reusable_geometry_problem_payload(
    problem: ReusableGeometryProblem,
) -> dict[str, object]:
    fields = (
        "schema_version",
        "problem_id",
        "content_sha256",
        "source_catalog_sha256",
        "tasks_index",
        "sheet_type",
        "projection",
        "problem",
        "candidate_requirement",
        "claim_ceiling",
    )
    state = _exact_instance_state(problem, ReusableGeometryProblem, fields)
    string_fields = (
        "schema_version",
        "problem_id",
        "content_sha256",
        "source_catalog_sha256",
        "claim_ceiling",
    )
    if (
        any(type(state[name]) is not str or not state[name] for name in string_fields)
        or type(state["tasks_index"]) is not int
        or state["tasks_index"] < 0
        or type(state["sheet_type"]) is not int
    ):
        raise TypeError("M8 prepared reusable problem identity differs")
    return {
        "schema_version": state["schema_version"],
        "problem_id": state["problem_id"],
        "content_sha256": state["content_sha256"],
        "source_catalog_sha256": state["source_catalog_sha256"],
        "tasks_index": state["tasks_index"],
        "sheet_type": state["sheet_type"],
        "projection": _solver_projection_payload(state["projection"]),
        "problem": _strip_problem_payload(state["problem"]),
        "candidate_requirement": _candidate_archive_requirement_payload(
            state["candidate_requirement"]
        ),
        "claim_ceiling": state["claim_ceiling"],
    }


def _candidate_archive_payload(archive: M7CandidateArchiveEvidence) -> dict[str, object]:
    fields = (
        "seed",
        "job_id",
        "batch_sha256",
        "candidate_count",
        "source_result_id",
        "source_result_sha256",
    )
    state = _exact_instance_state(archive, M7CandidateArchiveEvidence, fields)
    if (
        type(state["seed"]) is not int
        or type(state["candidate_count"]) is not int
        or state["candidate_count"] <= 0
        or any(
            type(state[name]) is not str or not state[name]
            for name in (
                "job_id",
                "batch_sha256",
                "source_result_id",
                "source_result_sha256",
            )
        )
    ):
        raise TypeError("M8 prepared candidate archive differs")
    return {name: state[name] for name in fields}


def _candidate_evidence_payload(evidence: M7CandidateSetEvidence) -> dict[str, object]:
    fields = (
        "schema_version",
        "candidate_set_id",
        "content_sha256",
        "problem_id",
        "problem_sha256",
        "archives",
        "raw_candidate_count",
        "distinct_candidate_count",
        "candidate_ids",
        "rejected_candidate_ids",
        "claim_ceiling",
    )
    state = _exact_instance_state(evidence, M7CandidateSetEvidence, fields)
    string_fields = (
        "schema_version",
        "candidate_set_id",
        "content_sha256",
        "problem_id",
        "problem_sha256",
        "claim_ceiling",
    )
    id_fields = (state["candidate_ids"], state["rejected_candidate_ids"])
    if (
        any(type(state[name]) is not str or not state[name] for name in string_fields)
        or type(state["archives"]) is not tuple
        or any(
            type(values) is not tuple
            or any(type(value) is not str or not value for value in values)
            for values in id_fields
        )
        or type(state["raw_candidate_count"]) is not int
        or state["raw_candidate_count"] <= 0
        or type(state["distinct_candidate_count"]) is not int
        or state["distinct_candidate_count"] <= 0
    ):
        raise TypeError("M8 prepared candidate evidence differs")
    return {
        "schema_version": state["schema_version"],
        "candidate_set_id": state["candidate_set_id"],
        "content_sha256": state["content_sha256"],
        "problem_id": state["problem_id"],
        "problem_sha256": state["problem_sha256"],
        "archives": tuple(_candidate_archive_payload(item) for item in state["archives"]),
        "raw_candidate_count": state["raw_candidate_count"],
        "distinct_candidate_count": state["distinct_candidate_count"],
        "candidate_ids": state["candidate_ids"],
        "rejected_candidate_ids": state["rejected_candidate_ids"],
        "claim_ceiling": state["claim_ceiling"],
    }


def _placement_payload(placement: Placement) -> dict[str, object]:
    state = _exact_instance_state(
        placement,
        Placement,
        ("part_id", "rotation", "translation"),
    )
    if (
        type(state["part_id"]) is not str
        or not state["part_id"]
        or type(state["translation"]) is not tuple
        or len(state["translation"]) != 2
    ):
        raise TypeError("M8 prepared placement differs")
    return {
        "part_id": state["part_id"],
        "rotation": _exact_finite_float(state["rotation"]),
        "translation": tuple(
            _exact_finite_float(value) for value in state["translation"]
        ),
    }


def _candidate_payload(candidate: Candidate) -> dict[str, object]:
    fields = ("candidate_id", "report_type", "seed", "width", "density", "placements")
    state = _exact_instance_state(candidate, Candidate, fields)
    if (
        type(state["candidate_id"]) is not str
        or not state["candidate_id"]
        or type(state["report_type"]) is not CandidateReportType
        or type(state["seed"]) is not int
    ):
        raise TypeError("M8 prepared candidate differs")
    density = _exact_finite_float(state["density"])
    if density < 0 or density > 1:
        raise ValueError("M8 prepared candidate density differs")
    return {
        "candidate_id": state["candidate_id"],
        "report_type": state["report_type"].value,
        "seed": state["seed"],
        "width": _exact_finite_float(state["width"], positive=True),
        "density": density,
        "placements": tuple(
            _placement_payload(item)
            for item in _exact_source_list_values(state["placements"])
        ),
    }


def _verified_evidence_partition(
    evidence: M7CandidateSetEvidence,
) -> tuple[tuple[str, str, str, str], tuple[str, ...]]:
    if type(evidence) is not M7CandidateSetEvidence:
        raise TypeError("M8 prepared candidate evidence type differs")
    partition = (
        evidence.problem_id,
        evidence.problem_sha256,
        evidence.candidate_set_id,
        evidence.content_sha256,
    )
    if any(type(value) is not str or not value for value in partition):
        raise TypeError("M8 prepared candidate evidence identity differs")
    candidate_ids = evidence.candidate_ids
    if (
        type(candidate_ids) is not tuple
        or any(type(value) is not str or not value for value in candidate_ids)
    ):
        raise TypeError("M8 prepared candidate evidence membership differs")
    return partition, candidate_ids


def _prepared_translation_layout_projection(
    layout: PreparedTranslationRejectionLayout,
) -> tuple[str, float, tuple[float, float, float, float]]:
    if type(layout) is not PreparedTranslationRejectionLayout:
        raise TypeError("M8 prepared translation layout type differs")
    if type(layout.candidate_id) is not str or not layout.candidate_id:
        raise TypeError("M8 prepared translation layout identity differs")
    bounds = layout.bounds
    if type(bounds) is not tuple or len(bounds) != 4:
        raise TypeError("M8 prepared translation layout bounds differ")
    return (
        layout.candidate_id,
        _exact_finite_float(layout.area, positive=True),
        tuple(_exact_finite_float(value) for value in bounds),
    )


def _prepared_footprint_measurement_projection(
    footprint: PreparedLayoutFootprint,
) -> tuple[str, float, tuple[float, float, float, float]]:
    if type(footprint) is not PreparedLayoutFootprint:
        raise TypeError("M8 prepared layout footprint type differs")
    if type(footprint.candidate_id) is not str or not footprint.candidate_id:
        raise TypeError("M8 prepared layout footprint identity differs")
    bounds = footprint.bounds
    if type(bounds) is not tuple or len(bounds) != 4:
        raise TypeError("M8 prepared layout footprint bounds differ")
    return (
        footprint.candidate_id,
        _exact_finite_float(footprint.geometry.area, positive=True),
        tuple(_exact_finite_float(value) for value in bounds),
    )


def _compiled_rejection_identity(
    problem: ReusableGeometryProblem,
    verified: VerifiedProblemCandidates,
) -> tuple[str, str, str, str]:
    if type(problem) is not ReusableGeometryProblem:
        raise TypeError("M8 prepared rejection problem source type differs")
    problem_identity = (problem.problem_id, problem.content_sha256)
    if any(type(value) is not str or not value for value in problem_identity):
        raise TypeError("M8 prepared rejection problem source identity differs")
    evidence_partition, _candidate_ids = _verified_evidence_partition(verified.evidence)
    identity = (*problem_identity, evidence_partition[2], evidence_partition[3])
    if problem_identity != evidence_partition[:2]:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: rejection problem binding"
        )
    return identity


def _rejection_scalar_from_projection(
    retained: _VerifiedRejectionLayoutProjection,
) -> RejectionScalar:
    if type(retained) is not _VerifiedRejectionLayoutProjection:
        raise TypeError("M8 prepared rejection projection type differs")
    return RejectionScalar(
        problem_id=retained.problem_id,
        problem_sha256=retained.problem_sha256,
        candidate_set_id=retained.candidate_set_id,
        candidate_set_sha256=retained.candidate_set_sha256,
        candidate_id=retained.candidate_id,
        source_transform_sha256=retained.source_transform_sha256,
        material_partition=retained.material_binding_scope,
        fit_config_sha256=retained.fit_config_sha256,
        area=retained.layout_area,
        width=retained.layout_width,
        height=retained.layout_height,
    )


def _verified_rejection_layouts_cover_candidates(
    verified: VerifiedProblemCandidates,
    *,
    expected_fit_config_sha256: str | None = None,
    prepared_footprints: tuple[PreparedLayoutFootprint, ...] | None = None,
    prepared_layouts: tuple[PreparedTranslationRejectionLayout, ...] | None = None,
    rejection_projections: tuple[_VerifiedRejectionLayoutProjection, ...] | None = None,
    strict_rejection_membership: bool = True,
) -> bool:
    """Separate legitimate absence from malformed or drifted scalar evidence."""

    try:
        if type(verified) is not VerifiedProblemCandidates:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: candidate archive type"
            )
        if type(verified.candidates) is not tuple:
            raise TypeError("M8 prepared candidate collection differs")
        candidate_transforms = tuple(
            _candidate_transform_projection(candidate) for candidate in verified.candidates
        )
        candidate_ids = tuple(candidate_id for candidate_id, _digest in candidate_transforms)
        expected_partition, evidence_candidate_ids = _verified_evidence_partition(
            verified.evidence
        )
        if (
            not candidate_ids
            or candidate_ids != evidence_candidate_ids
            or len(candidate_ids) != len(set(candidate_ids))
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: candidate membership"
            )
        prepared_layout_values: tuple[
            tuple[str, float, tuple[float, float, float, float]], ...
        ] | None = None
        if prepared_layouts is not None:
            if type(prepared_layouts) is not tuple:
                raise TypeError("M8 prepared translation layout collection differs")
            prepared_layout_values = tuple(
                _prepared_translation_layout_projection(item) for item in prepared_layouts
            )
            if tuple(item[0] for item in prepared_layout_values) != candidate_ids:
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: prepared layout membership"
                )
        if prepared_footprints is not None:
            if type(prepared_footprints) is not tuple:
                raise TypeError("M8 prepared layout footprint collection differs")
            footprint_values = tuple(
                _prepared_footprint_measurement_projection(item)
                for item in prepared_footprints
            )
            if (
                tuple(item[0] for item in footprint_values) != candidate_ids
                or prepared_layout_values is None
                or footprint_values != prepared_layout_values
            ):
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: prepared layout measurement"
                )
        retained_values = (
            _verified_rejection_layout_projections(verified)
            if rejection_projections is None
            else rejection_projections
        )
        if type(retained_values) is not tuple or any(
            type(value) is not _VerifiedRejectionLayoutProjection
            for value in retained_values
        ):
            raise TypeError("M8 prepared rejection projection collection differs")
        if not retained_values:
            return False
        retained_ids = tuple(item.candidate_id for item in retained_values)
        candidate_position = {
            candidate_id: position for position, candidate_id in enumerate(candidate_ids)
        }
        retained_positions = tuple(
            candidate_position.get(candidate_id, -1) for candidate_id in retained_ids
        )
        if (
            len(retained_ids) != len(set(retained_ids))
            or any(position < 0 for position in retained_positions)
            or retained_positions != tuple(sorted(retained_positions))
        ):
            if not strict_rejection_membership:
                return False
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: rejection membership"
            )
        candidate_transform_by_id = dict(candidate_transforms)
        prepared_by_id = (
            {item[0]: item for item in prepared_layout_values}
            if prepared_layout_values is not None
            else {}
        )
        if expected_fit_config_sha256 is not None and type(expected_fit_config_sha256) is not str:
            raise TypeError("M8 prepared fit configuration identity differs")
        for scalar in retained_values:
            if (
                (
                    scalar.problem_id,
                    scalar.problem_sha256,
                    scalar.candidate_set_id,
                    scalar.candidate_set_sha256,
                )
                != expected_partition
                or scalar.source_transform_sha256
                != candidate_transform_by_id[scalar.candidate_id]
                or scalar.material_binding_scope != "temporal_event"
                or (
                    expected_fit_config_sha256 is not None
                    and scalar.fit_config_sha256 != expected_fit_config_sha256
                )
            ):
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: scalar partition"
                )
            if prepared_layout_values is not None:
                prepared = prepared_by_id[scalar.candidate_id]
                _candidate_id, prepared_area, prepared_bounds = prepared
                min_x, min_y, max_x, max_y = prepared_bounds
                if (
                    scalar.layout_area != prepared_area
                    or scalar.layout_bounds != prepared_bounds
                    or scalar.layout_width != float(max_x - min_x)
                    or scalar.layout_height != float(max_y - min_y)
                ):
                    raise M8PreparedFrontierIntegrityError(
                        "M8 prepared frontier integrity differs: scalar measurement"
                    )
        return retained_ids == candidate_ids
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: malformed archive"
        ) from error


@dataclass(frozen=True)
class CompiledStandardWinner:
    problem_id: str
    problem_sha256: str
    candidate_set_id: str
    candidate_set_sha256: str
    action_id: str
    candidate_id: str
    decision_key: tuple[str, ...]
    standard_profiles: tuple[M7StandardActionProfile, ...]


@dataclass(frozen=True)
class CompiledTranslationRejection:
    candidate_id: str
    certificate: TranslationRejectionCertificate


@dataclass(frozen=True)
class CompiledRejectionProblem:
    """One verified problem bound to its complete minimal rejection frontier."""

    problem_id: str
    problem_sha256: str
    candidate_set_id: str
    candidate_set_sha256: str
    frontier: ParetoFrontier


_PREPARED_FRONTIER_INPUT_ISSUER = object()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _PreparedFrontierBatchInputs:
    """Proof-owned scalar inputs for one prepared event partition."""

    problem_id: str
    problem_sha256: str
    candidate_set_id: str
    candidate_set_sha256: str
    candidate_ids: tuple[str, ...]
    rejection_layout_candidate_ids: tuple[str, ...]
    rejection_layout_sha256s: tuple[str, ...]
    fit_config_sha256: str
    problem: CompiledRejectionProblem | None
    event_material_key: tuple[str, str, str, str, str]
    fit_config: RemnantFitConfig
    measurements: tuple[PreparedTranslationRejectionRemnant, ...]
    content_sha256: str
    _owner_token: object = field(repr=False, compare=False)
    _token: object = field(repr=False, compare=False)


def _prepared_frontier_batch_inputs_sha256(inputs: _PreparedFrontierBatchInputs) -> str:
    """Commit every proof-owned scalar DTO field before crossing module boundaries."""

    return "sha256:" + semantic_sha256(_prepared_frontier_batch_inputs_payload(inputs))


def _remnant_fit_config_payload(config: RemnantFitConfig) -> dict[str, object]:
    fields = (
        "schema_version",
        "clearance_distance",
        "buffer_join_style",
        "coordinate_tolerance",
        "relative_area_tolerance",
    )
    state = _exact_instance_state(config, RemnantFitConfig, fields)
    if (
        type(state["schema_version"]) is not str
        or state["schema_version"] != "yieldforge.remnant-fit-config.v1"
        or type(state["buffer_join_style"]) is not str
        or state["buffer_join_style"] != "mitre"
    ):
        raise TypeError("M8 prepared fit configuration identity differs")
    clearance = _exact_finite_float(state["clearance_distance"])
    coordinate_tolerance = _exact_finite_float(state["coordinate_tolerance"], positive=True)
    relative_area_tolerance = _exact_finite_float(
        state["relative_area_tolerance"], positive=True
    )
    if clearance < 0:
        raise ValueError("M8 prepared fit configuration clearance differs")
    return {
        "schema_version": state["schema_version"],
        "clearance_distance": clearance,
        "buffer_join_style": state["buffer_join_style"],
        "coordinate_tolerance": coordinate_tolerance,
        "relative_area_tolerance": relative_area_tolerance,
    }


def _prepared_remnant_measurement_payload(
    measurement: PreparedTranslationRejectionRemnant,
) -> dict[str, object]:
    if (
        type(measurement) is not PreparedTranslationRejectionRemnant
        or type(measurement.remnant_id) is not str
        or not measurement.remnant_id
        or type(measurement.material_key) is not tuple
        or len(measurement.material_key) != 5
        or any(
            type(value) is not str or not value for value in measurement.material_key
        )
        or type(measurement.bounds) is not tuple
        or len(measurement.bounds) != 4
    ):
        raise TypeError("M8 prepared remnant measurement graph differs")
    bounds = tuple(_exact_finite_float(value) for value in measurement.bounds)
    min_x, min_y, max_x, max_y = bounds
    if max_x < min_x or max_y < min_y:
        raise ValueError("M8 prepared remnant measurement bounds differ")
    return {
        "remnant_id": measurement.remnant_id,
        "material_key": measurement.material_key,
        "area": _exact_finite_float(measurement.area, positive=True),
        "bounds": bounds,
    }


def _prepared_frontier_batch_inputs_payload(
    inputs: _PreparedFrontierBatchInputs,
) -> dict[str, object]:
    """Project one issued DTO into fresh exact builtins without serializer dispatch."""

    if type(inputs) is not _PreparedFrontierBatchInputs:
        raise TypeError("M8 prepared frontier inputs require an exact DTO")
    identities = (
        inputs.problem_id,
        inputs.problem_sha256,
        inputs.candidate_set_id,
        inputs.candidate_set_sha256,
        inputs.fit_config_sha256,
    )
    string_tuples = (
        inputs.candidate_ids,
        inputs.rejection_layout_candidate_ids,
        inputs.rejection_layout_sha256s,
    )
    if (
        any(type(value) is not str or not value for value in identities)
        or any(
            type(values) is not tuple
            or any(type(value) is not str or not value for value in values)
            for values in string_tuples
        )
        or type(inputs.event_material_key) is not tuple
        or len(inputs.event_material_key) != 5
        or any(
            type(value) is not str or not value for value in inputs.event_material_key
        )
        or type(inputs.measurements) is not tuple
    ):
        raise TypeError("M8 prepared frontier input graph differs")
    return {
        "schema_version": "yieldforge.m8-prepared-frontier-inputs.v1",
        "problem_id": inputs.problem_id,
        "problem_sha256": inputs.problem_sha256,
        "candidate_set_id": inputs.candidate_set_id,
        "candidate_set_sha256": inputs.candidate_set_sha256,
        "candidate_ids": inputs.candidate_ids,
        "rejection_layout_candidate_ids": inputs.rejection_layout_candidate_ids,
        "rejection_layout_sha256s": inputs.rejection_layout_sha256s,
        "fit_config_sha256": inputs.fit_config_sha256,
        "problem": (
            _compiled_rejection_problem_payload(inputs.problem)
            if inputs.problem is not None
            else None
        ),
        "event_material_key": inputs.event_material_key,
        "fit_config": _remnant_fit_config_payload(inputs.fit_config),
        "measurements": tuple(
            _prepared_remnant_measurement_payload(item) for item in inputs.measurements
        ),
    }


def _prepared_frontier_batch_inputs_have_exact_shape(
    inputs: _PreparedFrontierBatchInputs,
) -> bool:
    """Reject class-drifted DTO nodes before projecting untrusted input."""

    try:
        if type(inputs.content_sha256) is not str:
            return False
        _prepared_frontier_batch_inputs_payload(inputs)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False
    return True


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _PreparedTranslationLayoutBatch:
    """Process-local capability for one registry-owned proof batch."""

    _runtime_id: int
    _owner_token: object = field(repr=False, compare=False)
    _issuance_fingerprint: str = ""
    _remnant_authorities: dict[
        _PreparedRemnantSemanticKey,
        _PreparedRemnantMeasurementAuthority,
    ] = field(default_factory=dict, repr=False, compare=False)

    def require_active(self, runtime: M7ReplayRuntime, *, deep: bool = False) -> None:
        _require_prepared_translation_layout_record(self, runtime, deep=deep)

    def require_owned(self, runtime: M7ReplayRuntime) -> None:
        _require_prepared_translation_layout_record(self, runtime, owned=True)

    def __reduce__(self) -> object:
        raise TypeError("M8 prepared translation layout batches cannot be serialized")


_PreparedTranslationLayouts = tuple[
    tuple[tuple[str, str], tuple[PreparedTranslationRejectionLayout, ...]], ...
]
_PreparedLayoutFootprints = tuple[tuple[tuple[str, str], tuple[PreparedLayoutFootprint, ...]], ...]
_CompiledRejectionProblems = tuple[tuple[tuple[str, str], CompiledRejectionProblem], ...]
_CompiledStandardWinners = tuple[tuple[tuple[str, str], CompiledStandardWinner], ...]


@dataclass(frozen=True, slots=True)
class _PreparedLayoutSourceBinding:
    problem_id: str
    problem_sha256: str
    candidate_set_id: str
    candidate_set_sha256: str
    candidate_ids: tuple[str, ...]
    candidate_sha256s: tuple[str, ...]
    rejection_layout_candidate_ids: tuple[str, ...]
    rejection_layout_sha256s: tuple[str, ...]
    fit_config_sha256: str


_PreparedLayoutSourceBindings = tuple[tuple[tuple[str, str], _PreparedLayoutSourceBinding], ...]
_PreparedLayoutSourceEventPositions = tuple[tuple[tuple[str, str], int], ...]
_PreparedLayoutKeyFingerprints = tuple[tuple[tuple[str, str], str], ...]
_PreparedEventMaterials = tuple[tuple[tuple[str, str], MaterialIdentity] | None, ...]


@dataclass(slots=True)
class _PreparedSourceMutationGuard:
    """Monotonic ordinary-list mutation version for one key's live source."""

    owner_pid: int
    version: int = 0
    active: bool = True
    snapshots: tuple[tuple[list[object], list[object]], ...] = ()


_PREPARED_TRACKED_LIST_GUARDS: dict[
    int,
    tuple[weakref.ReferenceType[object], _PreparedSourceMutationGuard],
] = {}


def _prepared_tracked_list_guard(
    tracked: object,
) -> _PreparedSourceMutationGuard:
    entry = _PREPARED_TRACKED_LIST_GUARDS.get(id(tracked))
    if entry is None or entry[0]() is not tracked:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: tracked source guard authority"
        )
    return entry[1]


class _PreparedMutationTrackedList(list):  # type: ignore[type-arg]
    """List-compatible source storage that records all ordinary mutations."""

    __slots__ = ("__weakref__",)

    def __init__(self, values, *, guard: _PreparedSourceMutationGuard) -> None:  # type: ignore[no-untyped-def]
        super().__init__(values)
        tracked_id = id(self)

        def discard(reference: weakref.ReferenceType[object]) -> None:
            current = _PREPARED_TRACKED_LIST_GUARDS.get(tracked_id)
            if current is not None and current[0] is reference:
                _PREPARED_TRACKED_LIST_GUARDS.pop(tracked_id, None)

        reference = weakref.ref(self, discard)
        _PREPARED_TRACKED_LIST_GUARDS[tracked_id] = (reference, guard)

    def _bump(self) -> None:
        _prepared_tracked_list_guard(self).version += 1

    def __getstate__(self) -> object:
        raise TypeError("M8 prepared tracked source lists cannot expose state")

    def __reduce__(self) -> object:
        raise TypeError("M8 prepared tracked source lists cannot be serialized")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("M8 prepared tracked source lists cannot be serialized")

    def __setitem__(self, key, value) -> None:  # type: ignore[no-untyped-def,override]
        super().__setitem__(key, value)
        self._bump()

    def __delitem__(self, key) -> None:  # type: ignore[no-untyped-def,override]
        super().__delitem__(key)
        self._bump()

    def __iadd__(self, values):  # type: ignore[no-untyped-def,override]
        result = super().__iadd__(values)
        self._bump()
        return result

    def __imul__(self, count):  # type: ignore[no-untyped-def,override]
        result = super().__imul__(count)
        self._bump()
        return result

    def append(self, value) -> None:  # type: ignore[no-untyped-def,override]
        super().append(value)
        self._bump()

    def clear(self) -> None:
        super().clear()
        self._bump()

    def extend(self, values) -> None:  # type: ignore[no-untyped-def,override]
        super().extend(values)
        self._bump()

    def insert(self, index, value) -> None:  # type: ignore[no-untyped-def,override]
        super().insert(index, value)
        self._bump()

    def pop(self, index=-1):  # type: ignore[no-untyped-def,override]
        result = super().pop(index)
        self._bump()
        return result

    def remove(self, value) -> None:  # type: ignore[no-untyped-def,override]
        super().remove(value)
        self._bump()

    def reverse(self) -> None:
        super().reverse()
        self._bump()

    def sort(self, *, key=None, reverse=False) -> None:  # type: ignore[no-untyped-def,override]
        super().sort(key=key, reverse=reverse)
        self._bump()


@dataclass(frozen=True, slots=True)
class _PreparedTrackedListBinding:
    owner: object
    field_name: str
    original: list[object]
    original_values: tuple[object, ...]
    tracked: _PreparedMutationTrackedList
    canonical: list[object]
    guard: _PreparedSourceMutationGuard


_PreparedSourceMutationGuards = tuple[tuple[tuple[str, str], _PreparedSourceMutationGuard], ...]


@dataclass(frozen=True, slots=True)
class _PreparedLayoutLiveSourceIdentity:
    """Strong references used for constant-time source-identity checks."""

    runtime: object
    replay_input: object
    instances: object
    problems: object
    problem: object
    geometry_problem: object
    problem_parts: object
    runtime_candidates: object
    verified: object
    evidence: object
    candidates: object
    rejection_layouts: object
    fit_config: object


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _PreparedLayoutSourceLease:
    """Private per-key lease for one deep-checked prepared source."""

    _token: object
    _owner_token: object = field(repr=False, compare=False)

    def __reduce__(self) -> object:
        raise TypeError("M8 prepared layout source leases cannot be serialized")


@dataclass(frozen=True, slots=True)
class _RegisteredPreparedLayoutSourceLease:
    reference: weakref.ReferenceType[_PreparedLayoutSourceLease]
    child_id: int
    owner_pid: int
    token: object
    owner_token: object
    prepared: _PreparedTranslationLayoutBatch
    runtime: M7ReplayRuntime
    key: tuple[str, str]
    source_binding: _PreparedLayoutSourceBinding
    source_identity: _PreparedLayoutLiveSourceIdentity
    mutation_guard: _PreparedSourceMutationGuard
    mutation_version: int
    fit_config: RemnantFitConfig
    event_materials: tuple[MaterialIdentity | None, ...]
    event_bindings: tuple[object | None, ...]
    layout_footprints: tuple[PreparedLayoutFootprint, ...]
    layouts: tuple[PreparedTranslationRejectionLayout, ...]
    rejection_problem: CompiledRejectionProblem | None
    standard_winner: CompiledStandardWinner


@dataclass(frozen=True, slots=True)
class _PreparedRemnantSemanticKey:
    """All immutable remnant evidence relevant to a rejection certificate."""

    remnant_id: str
    geometry_schema_version: str
    geometry_wkb_hex: str
    geometry_sha256: str
    geometry_area: float
    lineage_schema_version: str
    lineage_root_stock_id: str
    lineage_parent_remnant_id: str | None
    lineage_ancestor_remnant_ids: tuple[str, ...]
    lineage_generation: int
    lineage_source_candidate_id: str
    lineage_source_component_sha256: str
    material_schema_version: str
    material_key: tuple[str, str, str, str, str]
    material_provenance: str


_PREPARED_REMNANT_AUTHORITY_ISSUER = object()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _PreparedRemnantMeasurementAuthority:
    """Opaque immutable authority for one independently checked remnant measurement."""

    semantic_key: _PreparedRemnantSemanticKey
    measurement: PreparedTranslationRejectionRemnant
    key_values: tuple[object, ...]
    measurement_values: tuple[object, ...]
    commitment: str
    _owner_token: object = field(repr=False, compare=False)
    _token: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class _RegisteredPreparedRemnantMeasurementAuthority:
    reference: weakref.ReferenceType[_PreparedRemnantMeasurementAuthority]
    child_id: int
    owner_pid: int
    token: object
    owner_token: object
    prepared: _PreparedTranslationLayoutBatch
    semantic_key: _PreparedRemnantSemanticKey
    measurement: PreparedTranslationRejectionRemnant


@dataclass(frozen=True, slots=True)
class _RegisteredPreparedFrontierBatchInputs:
    reference: weakref.ReferenceType[_PreparedFrontierBatchInputs]
    child_id: int
    owner_pid: int
    token: object
    owner_token: object
    prepared: _PreparedTranslationLayoutBatch
    runtime: M7ReplayRuntime
    event_position: int
    source_lease: _PreparedLayoutSourceLease
    remnant_authorities: tuple[_PreparedRemnantMeasurementAuthority, ...]
    content_sha256: str
    canonical_inputs: _PreparedFrontierBatchInputs = field(repr=False)


@dataclass(frozen=True, slots=True)
class _PreparedTranslationLayoutRecord:
    """Private canonical storage that is never exposed through the capability."""

    reference: weakref.ReferenceType[_PreparedTranslationLayoutBatch]
    owner_pid: int
    owner_token: object
    runtime_id: int
    source_runtime_snapshot: M7SemanticRuntimeSnapshot
    source_runtime_integrity_sha256: str
    source_bindings: _PreparedLayoutSourceBindings
    source_event_positions: _PreparedLayoutSourceEventPositions
    event_materials: _PreparedEventMaterials
    source_key_fingerprints: _PreparedLayoutKeyFingerprints
    source_mutation_guards: _PreparedSourceMutationGuards
    source_leases: dict[tuple[str, str], _PreparedLayoutSourceLease]
    frontier_inputs: dict[int, weakref.ReferenceType[_PreparedFrontierBatchInputs]]
    remnant_authorities: dict[
        _PreparedRemnantSemanticKey,
        _PreparedRemnantMeasurementAuthority,
    ]
    layout_footprints: _PreparedLayoutFootprints
    layouts: _PreparedTranslationLayouts
    rejection_problems: _CompiledRejectionProblems
    standard_winners: _CompiledStandardWinners
    remnant_measurements: dict[
        _PreparedRemnantSemanticKey,
        PreparedTranslationRejectionRemnant,
    ]
    remnant_commitments: dict[_PreparedRemnantSemanticKey, str]
    remnant_snapshots: dict[str, tuple[tuple[object, ...], tuple[object, ...]]]
    layout_fingerprint: str


@dataclass(frozen=True, slots=True)
class _PreparedChildOwnerBinding:
    """Immutable ownership independent of mutable parent and child records."""

    child_reference: weakref.ReferenceType[object]
    child_id: int
    prepared_reference: weakref.ReferenceType[_PreparedTranslationLayoutBatch]
    prepared_id: int
    owner_pid: int
    owner_token: object


_PREPARED_REGISTRY_MISSING = object()

_REGISTRY_ISSUANCE_FIELDS: dict[type[object], tuple[str, ...]] = {
    _PreparedTranslationLayoutRecord: (
        "reference",
        "owner_pid",
        "owner_token",
        "runtime_id",
        "source_runtime_snapshot",
        "source_runtime_integrity_sha256",
        "source_bindings",
        "source_event_positions",
        "event_materials",
        "source_key_fingerprints",
        "source_mutation_guards",
        "source_leases",
        "frontier_inputs",
        "remnant_authorities",
        "layout_footprints",
        "layouts",
        "rejection_problems",
        "standard_winners",
        "remnant_measurements",
        "remnant_commitments",
        "remnant_snapshots",
        "layout_fingerprint",
    ),
    _RegisteredPreparedLayoutSourceLease: (
        "reference",
        "child_id",
        "owner_pid",
        "token",
        "owner_token",
        "prepared",
        "runtime",
        "key",
        "source_binding",
        "source_identity",
        "mutation_guard",
        "mutation_version",
        "fit_config",
        "event_materials",
        "event_bindings",
        "layout_footprints",
        "layouts",
        "rejection_problem",
        "standard_winner",
    ),
    _RegisteredPreparedRemnantMeasurementAuthority: (
        "reference",
        "child_id",
        "owner_pid",
        "token",
        "owner_token",
        "prepared",
        "semantic_key",
        "measurement",
    ),
    _RegisteredPreparedFrontierBatchInputs: (
        "reference",
        "child_id",
        "owner_pid",
        "token",
        "owner_token",
        "prepared",
        "runtime",
        "event_position",
        "source_lease",
        "remnant_authorities",
        "content_sha256",
        "canonical_inputs",
    ),
    _PreparedChildOwnerBinding: (
        "child_reference",
        "child_id",
        "prepared_reference",
        "prepared_id",
        "owner_pid",
        "owner_token",
    ),
}


@dataclass(frozen=True, slots=True)
class _RegistryNestedIssuanceProvenance:
    value: object
    value_type: type[object]
    fields: tuple[tuple[str, object], ...]


@dataclass(frozen=True, slots=True)
class _PreparedSnapshotCleanupAuthority:
    owner_pid: int
    executable_path: str | None
    executable_device: int | None
    executable_inode: int | None
    directory_path: str | None
    directory_device: int | None
    directory_inode: int | None


@dataclass(frozen=True, slots=True)
class _RegistryIssuanceProvenance:
    value_type: type[object]
    fields: tuple[tuple[str, object], ...]
    nested: tuple[_RegistryNestedIssuanceProvenance, ...] = ()
    trusted_runtime: M7ReplayRuntime | None = field(default=None, repr=False)
    trusted_runtime_integrity_sha256: str | None = field(default=None, repr=False)
    trusted_runtime_type: type[object] | None = field(default=None, repr=False)
    trusted_runtime_fields: tuple[tuple[str, object], ...] = field(
        default=(),
        repr=False,
    )
    snapshot_cleanup: _PreparedSnapshotCleanupAuthority | None = field(
        default=None,
        repr=False,
    )


def _detached_prepared_operational_runtime(
    runtime: M7ReplayRuntime,
    record: _PreparedTranslationLayoutRecord,
) -> M7ReplayRuntime:
    """Create a private semantic clone with isolated proof-owned cache inputs."""

    standard_profiles: dict[tuple[str, str], M7StandardActionProfile] = {}
    for _key, winner in record.standard_winners:
        for profile in winner.standard_profiles:
            profile_key = (winner.problem_id, profile.candidate_id)
            prior = standard_profiles.get(profile_key)
            if prior is not None and prior != profile:
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: trusted profile cache"
                )
            standard_profiles[profile_key] = deepcopy(profile)
    prepared_layout_cache = deepcopy(runtime.prepared_layout_cache)
    prepared_layout_cache.clear()
    source_jagua_executable = runtime.jagua_executable
    detached_jagua_executable = (
        None if source_jagua_executable is None else Path(str(source_jagua_executable))
    )
    return M7ReplayRuntime(
        replay_input=deepcopy(runtime.replay_input),
        runtime_candidates=MappingProxyType(deepcopy(dict(runtime.runtime_candidates))),
        rules=deepcopy(runtime.rules),
        standard_profile_cache=standard_profiles,
        fit_search_cache={},
        shared_fit_search_cache=(None if runtime.shared_fit_search_cache is None else {}),
        prepared_layout_cache=prepared_layout_cache,
        jagua_executable=detached_jagua_executable,
        jagua_differential_audit=runtime.jagua_differential_audit,
    )


def _registry_issuance_provenance(value: object) -> _RegistryIssuanceProvenance:
    field_names = _REGISTRY_ISSUANCE_FIELDS.get(type(value))
    if field_names is None:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: registry issuance type"
        )
    nested: tuple[_RegistryNestedIssuanceProvenance, ...] = ()
    trusted_runtime: M7ReplayRuntime | None = None
    trusted_runtime_integrity_sha256: str | None = None
    trusted_runtime_type: type[object] | None = None
    trusted_runtime_fields: tuple[tuple[str, object], ...] = ()
    snapshot_cleanup: _PreparedSnapshotCleanupAuthority | None = None
    if type(value) is _PreparedTranslationLayoutRecord:
        snapshot = value.source_runtime_snapshot
        if type(snapshot) is not M7SemanticRuntimeSnapshot:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: snapshot issuance type"
            )
        runtime = snapshot.runtime
        runtime_fields = (
            "replay_input",
            "runtime_candidates",
            "rules",
            "runtime_metrics",
            "standard_profile_cache",
            "fit_search_cache",
            "shared_fit_search_cache",
            "prepared_layout_cache",
            "standard_profile_executor",
            "jagua_executable",
            "jagua_differential_audit",
        )
        nested_items = [
            _RegistryNestedIssuanceProvenance(
                value=snapshot,
                value_type=type(snapshot),
                fields=(
                    ("runtime", snapshot.runtime),
                    ("semantic_sha256", snapshot.semantic_sha256),
                    ("_owner_pid", snapshot._owner_pid),  # noqa: SLF001
                    ("_jagua_private", snapshot._jagua_private),  # noqa: SLF001
                ),
            ),
            _RegistryNestedIssuanceProvenance(
                value=runtime,
                value_type=type(runtime),
                fields=tuple(
                    (field_name, getattr(runtime, field_name)) for field_name in runtime_fields
                )
                + (
                    (("_snapshot_sealed", runtime._snapshot_sealed),)  # noqa: SLF001
                    if hasattr(runtime, "_snapshot_sealed")
                    else ()
                ),
            ),
        ]
        private = snapshot._jagua_private  # noqa: SLF001
        if private is not None:
            executable = private.executable
            snapshot_cleanup = _PreparedSnapshotCleanupAuthority(
                owner_pid=snapshot._owner_pid,  # noqa: SLF001
                executable_path=str(executable.path),
                executable_device=executable.device,
                executable_inode=executable.inode,
                directory_path=str(private.directory),
                directory_device=private.directory_device,
                directory_inode=private.directory_inode,
            )
            nested_items.extend(
                (
                    _RegistryNestedIssuanceProvenance(
                        value=private,
                        value_type=type(private),
                        fields=(
                            ("directory", private.directory),
                            ("directory_device", private.directory_device),
                            ("directory_inode", private.directory_inode),
                            ("directory_mode", private.directory_mode),
                            ("executable", executable),
                            ("content", private.content),
                        ),
                    ),
                    _RegistryNestedIssuanceProvenance(
                        value=executable,
                        value_type=type(executable),
                        fields=(
                            ("path", executable.path),
                            ("device", executable.device),
                            ("inode", executable.inode),
                            ("mode", executable.mode),
                            ("size_bytes", executable.size_bytes),
                            ("content_sha256", executable.content_sha256),
                        ),
                    ),
                    _RegistryNestedIssuanceProvenance(
                        value=private.directory,
                        value_type=type(private.directory),
                        fields=(),
                    ),
                    _RegistryNestedIssuanceProvenance(
                        value=executable.path,
                        value_type=type(executable.path),
                        fields=(),
                    ),
                )
            )
        else:
            snapshot_cleanup = _PreparedSnapshotCleanupAuthority(
                owner_pid=snapshot._owner_pid,  # noqa: SLF001
                executable_path=None,
                executable_device=None,
                executable_inode=None,
                directory_path=None,
                directory_device=None,
                directory_inode=None,
            )
        nested = tuple(nested_items)
        trusted_runtime = _detached_prepared_operational_runtime(runtime, value)
        trusted_runtime_type = type(trusted_runtime)
        trusted_runtime_fields = tuple(
            (field_name, getattr(trusted_runtime, field_name)) for field_name in runtime_fields
        )
        try:
            trusted_runtime_integrity_sha256 = m7_semantic_runtime_sha256(trusted_runtime)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: trusted runtime issuance"
            ) from error
        if trusted_runtime_integrity_sha256 != value.source_runtime_integrity_sha256:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: trusted runtime issuance"
            )
    elif type(value) is _RegisteredPreparedRemnantMeasurementAuthority:
        authority = value.reference()
        measurement = value.measurement
        if (
            type(authority) is not _PreparedRemnantMeasurementAuthority
            or authority.measurement is not measurement
            or type(measurement) is not PreparedTranslationRejectionRemnant
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: remnant authority issuance"
            )
        nested = (
            _RegistryNestedIssuanceProvenance(
                value=authority,
                value_type=type(authority),
                fields=(
                    ("semantic_key", authority.semantic_key),
                    ("measurement", measurement),
                    ("key_values", authority.key_values),
                    ("measurement_values", authority.measurement_values),
                    ("commitment", authority.commitment),
                    ("_owner_token", authority._owner_token),  # noqa: SLF001
                    ("_token", authority._token),  # noqa: SLF001
                ),
            ),
            _RegistryNestedIssuanceProvenance(
                value=measurement,
                value_type=type(measurement),
                fields=(
                    ("remnant_id", measurement.remnant_id),
                    ("material_key", measurement.material_key),
                    ("area", measurement.area),
                    ("bounds", measurement.bounds),
                ),
            ),
        )
    return _RegistryIssuanceProvenance(
        value_type=type(value),
        fields=tuple((field_name, getattr(value, field_name)) for field_name in field_names),
        nested=nested,
        trusted_runtime=trusted_runtime,
        trusted_runtime_integrity_sha256=trusted_runtime_integrity_sha256,
        trusted_runtime_type=trusted_runtime_type,
        trusted_runtime_fields=trusted_runtime_fields,
        snapshot_cleanup=snapshot_cleanup,
    )


def _nested_registry_issuance_matches(
    provenance: _RegistryNestedIssuanceProvenance,
) -> bool:
    value = provenance.value
    if type(value) is not provenance.value_type:
        return False
    try:
        return all(
            getattr(value, field_name, _PREPARED_REGISTRY_MISSING) is expected
            for field_name, expected in provenance.fields
        )
    except Exception:  # noqa: BLE001 - hostile nested issued-record diagnostics
        return False


def _registry_issuance_matches(
    value: object,
    provenance: _RegistryIssuanceProvenance,
) -> bool:
    if type(value) is not provenance.value_type:
        return False
    try:
        return all(
            getattr(value, field_name, _PREPARED_REGISTRY_MISSING) is expected
            for field_name, expected in provenance.fields
        ) and all(_nested_registry_issuance_matches(nested) for nested in provenance.nested)
    except Exception:  # noqa: BLE001 - hostile issued-record diagnostics
        return False


def _restore_registry_issuance(
    value: object,
    provenance: _RegistryIssuanceProvenance,
) -> None:
    try:
        if type(value) is not provenance.value_type:
            object.__setattr__(value, "__class__", provenance.value_type)
        if type(value) is not provenance.value_type:
            raise TypeError("issued registry record class restoration failed")
        for field_name, expected in provenance.fields:
            object.__setattr__(value, field_name, expected)
        for nested in provenance.nested:
            if type(nested.value) is not nested.value_type:
                object.__setattr__(nested.value, "__class__", nested.value_type)
            if type(nested.value) is not nested.value_type:
                raise TypeError("nested registry class restoration failed")
            for field_name, expected in nested.fields:
                object.__setattr__(nested.value, field_name, expected)
    except Exception as error:  # noqa: BLE001 - normalize hostile record classes
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: registry issuance restoration"
        ) from error


@dataclass(slots=True)
class _ExactIntegerKeyRegistryState:
    """Authority storage kept outside the publicly reachable registry object."""

    exact_key_integrity_differs: bool = False
    issued_values: dict[int, tuple[object, _RegistryIssuanceProvenance]] = field(
        default_factory=dict
    )
    tainted_issued_keys: set[int] = field(default_factory=set)
    values: dict[int, object] = field(default_factory=dict)


_EXACT_INTEGER_KEY_REGISTRY_STATES: dict[
    int,
    tuple[object, _ExactIntegerKeyRegistryState],
] = {}


def _exact_integer_key_registry_state(
    registry: object,
) -> _ExactIntegerKeyRegistryState:
    entry = _EXACT_INTEGER_KEY_REGISTRY_STATES.get(id(registry))
    if entry is None or entry[0] is not registry:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: registry state authority"
        )
    return entry[1]


class _ExactIntegerKeyRegistry(MutableMapping[object, object]):
    """Composed authority mapping with private canonical exact-key storage.

    The untrusted surface is this mapping API and the issued records it returns.
    Its name-mangled stores never escape; reflective replacement of those stores
    is equivalent to replacing the checker implementation itself and is outside
    the in-process integrity boundary.
    """

    __slots__ = ()

    def __init__(self) -> None:
        key = id(self)
        existing = _EXACT_INTEGER_KEY_REGISTRY_STATES.get(key)
        if existing is not None and existing[0] is not self:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: registry state collision"
            )
        _EXACT_INTEGER_KEY_REGISTRY_STATES[key] = (
            self,
            _ExactIntegerKeyRegistryState(),
        )

    def __getitem__(self, key: object) -> object:
        if type(key) is not int:
            raise KeyError(key)
        value = _exact_integer_key_registry_state(self).values[key]
        # Registry values are authority-bearing implementation records, not a
        # supported observation surface.  Once one escapes through the public
        # mapping protocol, arbitrary same-process code can retain and mutate
        # any object reachable from it between validation and use.  Revoke the
        # exact issuance before returning it so every subsequent production
        # lookup fails closed while unrelated issuances remain usable.
        if key in _exact_integer_key_registry_state(self).issued_values:
            _exact_integer_key_registry_state(self).tainted_issued_keys.add(key)
        return value

    def __iter__(self) -> Iterator[object]:
        return iter(_exact_integer_key_registry_state(self).values)

    def __len__(self) -> int:
        return len(_exact_integer_key_registry_state(self).values)

    def __getstate__(self) -> object:
        raise TypeError("M8 prepared registries cannot expose state")

    def __reduce__(self) -> object:
        raise TypeError("M8 prepared registries cannot be serialized")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("M8 prepared registries cannot be serialized")

    def __contains__(self, key: object) -> bool:
        return type(key) is int and key in _exact_integer_key_registry_state(self).values

    @property
    def exact_key_integrity_differs(self) -> bool:
        return _exact_integer_key_registry_state(self).exact_key_integrity_differs

    def _debug_integrity_state(self) -> tuple[bool, frozenset[int], bool]:
        """Return immutable test diagnostics without exposing either backing store."""

        issued_keys = frozenset(_exact_integer_key_registry_state(self).issued_values)
        projection_matches = (
            not _exact_integer_key_registry_state(self).tainted_issued_keys
            and all(type(key) is int for key in _exact_integer_key_registry_state(self).values)
            and all(
                type(key) is int for key in _exact_integer_key_registry_state(self).issued_values
            )
            and all(
                _exact_integer_key_registry_state(self).values.get(key, _PREPARED_REGISTRY_MISSING)
                is issued
                and _registry_issuance_matches(issued, provenance)
                for key, (issued, provenance) in _exact_integer_key_registry_state(
                    self
                ).issued_values.items()
            )
        )
        return (
            _exact_integer_key_registry_state(self).exact_key_integrity_differs,
            issued_keys,
            projection_matches,
        )

    def __setitem__(self, key: object, value: object) -> None:
        if type(key) is not int:
            _exact_integer_key_registry_state(self).exact_key_integrity_differs = True
            return
        if key in _exact_integer_key_registry_state(self).issued_values:
            issued, provenance = _exact_integer_key_registry_state(self).issued_values[key]
            current = _exact_integer_key_registry_state(self).values.get(
                key, _PREPARED_REGISTRY_MISSING
            )
            if (
                current is not issued
                or value is not issued
                or not _registry_issuance_matches(issued, provenance)
            ):
                _exact_integer_key_registry_state(self).exact_key_integrity_differs = True
            if current is not issued:
                _exact_integer_key_registry_state(self).values[key] = issued
            return
        _exact_integer_key_registry_state(self).exact_key_integrity_differs = True

    def setdefault(self, key: object, default: object = None) -> object:
        if type(key) is not int:
            _exact_integer_key_registry_state(self).exact_key_integrity_differs = True
            return default
        current = _exact_integer_key_registry_state(self).values.get(
            key, _PREPARED_REGISTRY_MISSING
        )
        if current is not _PREPARED_REGISTRY_MISSING:
            if key in _exact_integer_key_registry_state(self).issued_values:
                _exact_integer_key_registry_state(self).tainted_issued_keys.add(key)
            return current
        self[key] = default
        return _exact_integer_key_registry_state(self).values.get(key, default)

    def pop(self, key: object, default: object = _PREPARED_REGISTRY_MISSING) -> object:
        if type(key) is not int:
            _exact_integer_key_registry_state(self).exact_key_integrity_differs = True
            if default is _PREPARED_REGISTRY_MISSING:
                raise KeyError(key)
            return default
        current = _exact_integer_key_registry_state(self).values.get(
            key, _PREPARED_REGISTRY_MISSING
        )
        if current is _PREPARED_REGISTRY_MISSING:
            if default is _PREPARED_REGISTRY_MISSING:
                raise KeyError(key)
            return default
        _exact_integer_key_registry_state(self).exact_key_integrity_differs = True
        return _exact_integer_key_registry_state(self).values.pop(key)

    def __delitem__(self, key: object) -> None:
        self.pop(key)

    def popitem(self) -> tuple[object, object]:
        try:
            key = next(reversed(_exact_integer_key_registry_state(self).values))
        except StopIteration as error:
            raise KeyError("popitem(): dictionary is empty") from error
        return key, self.pop(key)

    def update(self, other: object = (), /, **values: object) -> None:
        entries = other.items() if hasattr(other, "items") else other
        for key, value in entries:  # type: ignore[union-attr]
            self[key] = value
        for key, value in values.items():
            self[key] = value

    def __ior__(self, other: object) -> _ExactIntegerKeyRegistry:
        self.update(other)
        return self

    def clear(self) -> None:
        _exact_integer_key_registry_state(self).values.clear()
        _exact_integer_key_registry_state(self).exact_key_integrity_differs = bool(
            _exact_integer_key_registry_state(self).issued_values
        )

    def _repair_untrusted_mutations(self) -> bool:
        malformed = _exact_integer_key_registry_state(self).exact_key_integrity_differs
        unsafe_visible_key = any(
            type(key) is not int or key not in _exact_integer_key_registry_state(self).issued_values
            for key in _exact_integer_key_registry_state(self).values
        )
        if unsafe_visible_key:
            for issued, provenance in _exact_integer_key_registry_state(
                self
            ).issued_values.values():
                _restore_registry_issuance(issued, provenance)
            _exact_integer_key_registry_state(self).values.clear()
            for key, (issued, _provenance) in _exact_integer_key_registry_state(
                self
            ).issued_values.items():
                _exact_integer_key_registry_state(self).values[key] = issued
            _exact_integer_key_registry_state(self).exact_key_integrity_differs = True
            return True
        for key, (issued, provenance) in _exact_integer_key_registry_state(
            self
        ).issued_values.items():
            try:
                current = _exact_integer_key_registry_state(self).values.get(
                    key, _PREPARED_REGISTRY_MISSING
                )
            except Exception:  # noqa: BLE001 - rebuild below from issuance shadow
                malformed = True
                break
            if current is not issued or not _registry_issuance_matches(
                issued,
                provenance,
            ):
                malformed = True
                _restore_registry_issuance(issued, provenance)
        if malformed:
            _exact_integer_key_registry_state(self).values.clear()
            for key, (issued, _provenance) in _exact_integer_key_registry_state(
                self
            ).issued_values.items():
                _exact_integer_key_registry_state(self).values[key] = issued
        _exact_integer_key_registry_state(self).exact_key_integrity_differs = malformed
        return malformed

    def _trusted_rebuild(self, entries: tuple[tuple[int, object], ...]) -> None:
        old_issuance = _exact_integer_key_registry_state(self).issued_values
        old_tainted_keys = _exact_integer_key_registry_state(self).tainted_issued_keys
        retained: list[tuple[int, object, _RegistryIssuanceProvenance]] = []
        malformed = False
        for key, value in entries:
            issuance = old_issuance.get(key)
            if issuance is None:
                malformed = True
                continue
            issued, provenance = issuance
            if value is not issued or not _registry_issuance_matches(
                issued,
                provenance,
            ):
                malformed = True
                _restore_registry_issuance(issued, provenance)
            retained.append((key, issued, provenance))
        _exact_integer_key_registry_state(self).values.clear()
        _exact_integer_key_registry_state(self).issued_values = {}
        _exact_integer_key_registry_state(self).tainted_issued_keys = set()
        for key, issued, provenance in retained:
            _exact_integer_key_registry_state(self).values[key] = issued
            _exact_integer_key_registry_state(self).issued_values[key] = (issued, provenance)
            if key in old_tainted_keys:
                _exact_integer_key_registry_state(self).tainted_issued_keys.add(key)
        _exact_integer_key_registry_state(self).exact_key_integrity_differs = malformed

    def _trusted_issue(self, key: int, value: object) -> None:
        if type(key) is not int:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: registry issuance key"
            )
        if (
            key in _exact_integer_key_registry_state(self).issued_values
            or _exact_integer_key_registry_state(self).values.get(
                key,
                _PREPARED_REGISTRY_MISSING,
            )
            is not _PREPARED_REGISTRY_MISSING
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: duplicate registry issuance"
            )
        provenance = _registry_issuance_provenance(value)
        _exact_integer_key_registry_state(self).values[key] = value
        _exact_integer_key_registry_state(self).issued_values[key] = (value, provenance)
        _exact_integer_key_registry_state(self).tainted_issued_keys.discard(key)

    def _trusted_revoke(self, key: int) -> object:
        _exact_integer_key_registry_state(self).issued_values.pop(key, None)
        _exact_integer_key_registry_state(self).tainted_issued_keys.discard(key)
        return _exact_integer_key_registry_state(self).values.pop(key, None)

    def _mark_issued_key_tainted(self, key: int) -> None:
        if type(key) is int and key in _exact_integer_key_registry_state(self).issued_values:
            _exact_integer_key_registry_state(self).tainted_issued_keys.add(key)

    def _issued_key_is_tainted(self, key: int) -> bool:
        return (
            type(key) is int and key in _exact_integer_key_registry_state(self).tainted_issued_keys
        )

    def _trusted_items(self) -> tuple[tuple[int, object], ...]:
        """Return the private projection without exporting it through Mapping."""

        return tuple(_exact_integer_key_registry_state(self).values.items())

    def _trusted_get(
        self,
        key: int,
        default: object = None,
    ) -> object:
        """Read raw cleanup state without creating a new public exposure."""

        if type(key) is not int:
            return default
        return _exact_integer_key_registry_state(self).values.get(key, default)

    def _trusted_runtime_for_issued_value(
        self,
        key: int,
        value: object,
    ) -> tuple[M7ReplayRuntime, str]:
        issuance = _exact_integer_key_registry_state(self).issued_values.get(key)
        if (
            type(key) is not int
            or key in _exact_integer_key_registry_state(self).tainted_issued_keys
            or issuance is None
            or issuance[0] is not value
            or _exact_integer_key_registry_state(self).values.get(key, _PREPARED_REGISTRY_MISSING)
            is not value
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: trusted runtime lookup"
            )
        provenance = issuance[1]
        trusted_runtime = provenance.trusted_runtime
        trusted_sha256 = provenance.trusted_runtime_integrity_sha256
        trusted_type = provenance.trusted_runtime_type
        trusted_fields = provenance.trusted_runtime_fields
        if trusted_runtime is None or trusted_sha256 is None or trusted_type is None:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: trusted runtime lookup"
            )
        try:
            trusted_matches = type(trusted_runtime) is trusted_type and all(
                getattr(trusted_runtime, field_name, _PREPARED_REGISTRY_MISSING) is expected
                for field_name, expected in trusted_fields
            )
        except Exception:  # noqa: BLE001 - restore the private runtime below
            trusted_matches = False
        if not trusted_matches:
            try:
                if type(trusted_runtime) is not trusted_type:
                    object.__setattr__(trusted_runtime, "__class__", trusted_type)
                for field_name, expected in trusted_fields:
                    object.__setattr__(trusted_runtime, field_name, expected)
            except Exception as error:  # noqa: BLE001 - normalize hostile class drift
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: trusted runtime restoration"
                ) from error
            _exact_integer_key_registry_state(self).tainted_issued_keys.add(key)
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: trusted runtime provenance"
            )
        return trusted_runtime, trusted_sha256

    def _snapshot_cleanup_for_issued_value(
        self,
        key: int,
        value: object,
    ) -> _PreparedSnapshotCleanupAuthority:
        issuance = _exact_integer_key_registry_state(self).issued_values.get(key)
        if (
            type(key) is not int
            or issuance is None
            or issuance[0] is not value
            or _exact_integer_key_registry_state(self).values.get(key, _PREPARED_REGISTRY_MISSING)
            is not value
            or issuance[1].snapshot_cleanup is None
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: snapshot cleanup authority"
            )
        return issuance[1].snapshot_cleanup

    def _restore_issued_key(self, key: int) -> object:
        issuance = _exact_integer_key_registry_state(self).issued_values.get(key)
        if issuance is None:
            return _PREPARED_REGISTRY_MISSING
        issued, provenance = issuance
        _restore_registry_issuance(issued, provenance)
        _exact_integer_key_registry_state(self).values[key] = issued
        _exact_integer_key_registry_state(self).exact_key_integrity_differs = True
        return issued

    def _seal_repaired_state(self) -> bool:
        if len(self) != len(_exact_integer_key_registry_state(self).issued_values):
            return False
        for key, (issued, provenance) in _exact_integer_key_registry_state(
            self
        ).issued_values.items():
            if (
                _exact_integer_key_registry_state(self).values.get(key, _PREPARED_REGISTRY_MISSING)
                is not issued
            ):
                return False
            if not _registry_issuance_matches(issued, provenance):
                return False
        _exact_integer_key_registry_state(self).exact_key_integrity_differs = False
        return True

    def _value_at_issued_key(self, key: int) -> object:
        if (
            type(key) is not int
            or _exact_integer_key_registry_state(self).exact_key_integrity_differs
            or key in _exact_integer_key_registry_state(self).tainted_issued_keys
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: registry lookup state"
            )
        try:
            current = _exact_integer_key_registry_state(self).values.get(
                key, _PREPARED_REGISTRY_MISSING
            )
        except Exception as error:  # noqa: BLE001 - hostile key equality
            _exact_integer_key_registry_state(self).exact_key_integrity_differs = True
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: registry key lookup"
            ) from error
        issuance = _exact_integer_key_registry_state(self).issued_values.get(key)
        if issuance is None:
            if current is not _PREPARED_REGISTRY_MISSING:
                _exact_integer_key_registry_state(self).exact_key_integrity_differs = True
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: unissued registry key"
                )
            return _PREPARED_REGISTRY_MISSING
        issued, provenance = issuance
        if current is not issued or not _registry_issuance_matches(issued, provenance):
            _exact_integer_key_registry_state(self).exact_key_integrity_differs = True
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: registry issuance provenance"
            )
        return current


_PREPARED_TRANSLATION_LAYOUT_REGISTRY: _ExactIntegerKeyRegistry = _ExactIntegerKeyRegistry()
_PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY: _ExactIntegerKeyRegistry = _ExactIntegerKeyRegistry()
_PREPARED_REMNANT_AUTHORITY_REGISTRY: _ExactIntegerKeyRegistry = _ExactIntegerKeyRegistry()
_PREPARED_FRONTIER_INPUT_REGISTRY: _ExactIntegerKeyRegistry = _ExactIntegerKeyRegistry()
_PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY: _ExactIntegerKeyRegistry = _ExactIntegerKeyRegistry()
_PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY: _ExactIntegerKeyRegistry = _ExactIntegerKeyRegistry()
_PREPARED_FRONTIER_INPUT_OWNER_REGISTRY: _ExactIntegerKeyRegistry = _ExactIntegerKeyRegistry()


def _trusted_registry_rebuild(
    registry: _ExactIntegerKeyRegistry,
    entries: tuple[tuple[int, object], ...],
) -> None:
    if type(registry) is not _ExactIntegerKeyRegistry:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: registry implementation"
        )
    registry._trusted_rebuild(entries)


def _trusted_registry_issue(
    registry: _ExactIntegerKeyRegistry,
    *,
    key: int,
    value: object,
) -> None:
    if type(registry) is not _ExactIntegerKeyRegistry:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: registry implementation"
        )
    registry._trusted_issue(key, value)


def _trusted_registry_revoke(
    registry: _ExactIntegerKeyRegistry,
    key: int,
) -> object:
    if type(registry) is not _ExactIntegerKeyRegistry:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: registry implementation"
        )
    return registry._trusted_revoke(key)


def _restore_exact_integer_key_registry_class(
    registry: object,
) -> tuple[_ExactIntegerKeyRegistry, bool]:
    """Restore a drifted registry object before mandatory cleanup continues."""

    class_drifted = type(registry) is not _ExactIntegerKeyRegistry
    if class_drifted:
        try:
            object.__setattr__(
                registry,
                "__class__",
                _ExactIntegerKeyRegistry,
            )
        except Exception as error:  # noqa: BLE001 - normalize hostile class drift
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: registry implementation"
            ) from error
    if type(registry) is not _ExactIntegerKeyRegistry:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: registry implementation"
        )
    return registry, class_drifted


def _sanitize_exact_integer_registry_keys(
    registry: _ExactIntegerKeyRegistry,
) -> bool:
    """Drop untrusted keys without hashing, comparing, or rendering them."""

    registry, class_drifted = _restore_exact_integer_key_registry_class(registry)
    key_integrity_differs = registry._repair_untrusted_mutations()
    entries = registry._trusted_items()
    exact_entries = tuple((key, value) for key, value in entries if type(key) is int)
    malformed = class_drifted or key_integrity_differs or len(exact_entries) != len(entries)
    if len(exact_entries) != len(entries):
        _trusted_registry_rebuild(registry, exact_entries)
    return malformed


def _prepared_registry_value_at_key(
    registry: _ExactIntegerKeyRegistry,
    *,
    key: int,
    detail: str,
) -> object:
    """Hot O(1) child lookup with hostile equality normalized to typed M8."""

    if type(registry) is not _ExactIntegerKeyRegistry or registry.exact_key_integrity_differs:
        raise M8PreparedFrontierIntegrityError(f"M8 prepared frontier integrity differs: {detail}")
    try:
        return registry._value_at_issued_key(key)
    except Exception as error:  # noqa: BLE001 - attacker-controlled key equality
        raise M8PreparedFrontierIntegrityError(
            f"M8 prepared frontier integrity differs: {detail}"
        ) from error


def _prepared_translation_layout_registry_state(
    prepared: _PreparedTranslationLayoutBatch,
    *,
    expected_record: _PreparedTranslationLayoutRecord | None = None,
    release: bool = False,
) -> tuple[_PreparedTranslationLayoutRecord | None, bool]:
    """Inspect one parent by native identity and optionally remove every local alias."""

    registry, registry_class_drifted = _restore_exact_integer_key_registry_class(
        _PREPARED_TRANSLATION_LAYOUT_REGISTRY
    )
    registry_key_integrity_differs = (
        registry._repair_untrusted_mutations()
        if type(registry) is _ExactIntegerKeyRegistry
        else True
    )
    entries = registry._trusted_items()
    retained: list[tuple[int, _PreparedTranslationLayoutRecord]] = []
    canonical: _PreparedTranslationLayoutRecord | None = None
    local_entries = 0
    prepared_id = id(prepared)
    malformed = (
        registry_class_drifted
        or registry_key_integrity_differs
        or (
            type(registry) is _ExactIntegerKeyRegistry
            and registry._issued_key_is_tainted(prepared_id)
        )
    )
    for observed_key, observed_record in entries:
        if type(observed_key) is not int:
            malformed = True
            continue
        exact_record = type(observed_record) is _PreparedTranslationLayoutRecord
        exact_reference = exact_record and type(observed_record.reference) is weakref.ReferenceType
        referenced_parent = observed_record.reference() if exact_reference else None
        referenced = exact_reference and referenced_parent is prepared
        local = observed_record is expected_record or referenced
        attributable = (
            exact_record
            and exact_reference
            and referenced_parent is not None
            and observed_key == id(referenced_parent)
            and type(observed_record.owner_pid) is int
            and observed_record.owner_pid == os.getpid()
        )
        structurally_valid = (
            attributable and type(referenced_parent) is _PreparedTranslationLayoutBatch
        )
        if not local and not attributable:
            malformed = True
            continue
        if attributable and not structurally_valid:
            malformed = True
        if local:
            local_entries += 1
            if observed_key != prepared_id:
                malformed = True
            if expected_record is not None and observed_record is not expected_record:
                malformed = True
            if release:
                continue
        retained.append((observed_key, observed_record))
        if observed_key == prepared_id:
            canonical = observed_record
    if local_entries > 1:
        malformed = True
    if release or registry_key_integrity_differs or len(retained) != len(entries):
        _trusted_registry_rebuild(registry, tuple(retained))
    return canonical, malformed


def _prepared_translation_layout_record_at_key(
    prepared: _PreparedTranslationLayoutBatch,
) -> object:
    """Hot O(1) lookup with hostile-key failures normalized to typed M8."""

    return _prepared_registry_value_at_key(  # type: ignore[arg-type]
        _PREPARED_TRANSLATION_LAYOUT_REGISTRY,
        key=id(prepared),
        detail="prepared registry lookup",
    )


def _discard_prepared_translation_layout_reference(
    reference: weakref.ReferenceType[_PreparedTranslationLayoutBatch],
    *,
    prepared_id: int,
) -> None:
    """Revoke one issued parent in O(1) without touching unrelated parents."""

    registry = _PREPARED_TRANSLATION_LAYOUT_REGISTRY
    if type(registry) is not _ExactIntegerKeyRegistry:
        return
    try:
        record = registry._value_at_issued_key(prepared_id)
    except M8PreparedFrontierIntegrityError:
        record = registry._restore_issued_key(prepared_id)
    if (
        type(record) is _PreparedTranslationLayoutRecord
        and type(record.reference) is weakref.ReferenceType
        and record.reference is reference
    ):
        registry._trusted_revoke(prepared_id)


def _prepared_parent_issuance_tokens(
    *,
    local_prepared: _PreparedTranslationLayoutBatch,
    local_owner_token: object,
) -> dict[int, tuple[object, object]]:
    """Index every canonical parent once for linear-time child revocation."""

    indexed = {id(local_prepared): (local_prepared, local_owner_token)}
    for observed_key, observed_record in tuple(
        _PREPARED_TRANSLATION_LAYOUT_REGISTRY._trusted_items()
    ):
        if not (
            type(observed_key) is int
            and type(observed_record) is _PreparedTranslationLayoutRecord
            and type(observed_record.reference) is weakref.ReferenceType
            and type(observed_record.owner_pid) is int
            and observed_record.owner_pid == os.getpid()
        ):
            continue
        owner = observed_record.reference()
        if owner is not None and observed_key == id(owner) and owner is not local_prepared:
            indexed[observed_key] = (owner, observed_record.owner_token)
    return indexed


def _discard_prepared_child_reference(
    child_registry: _ExactIntegerKeyRegistry,
    owner_registry: _ExactIntegerKeyRegistry,
    *,
    reference: weakref.ReferenceType[object],
    child_record_type: type[object],
    child_id: int,
) -> bool:
    """Revoke one issued child pair in O(1), leaving siblings untouched."""

    if (
        type(child_registry) is not _ExactIntegerKeyRegistry
        or type(owner_registry) is not _ExactIntegerKeyRegistry
    ):
        return False
    try:
        record = child_registry._value_at_issued_key(child_id)
    except M8PreparedFrontierIntegrityError:
        record = child_registry._restore_issued_key(child_id)
    try:
        binding = owner_registry._value_at_issued_key(child_id)
    except M8PreparedFrontierIntegrityError:
        binding = owner_registry._restore_issued_key(child_id)
    if not (
        type(record) is child_record_type
        and type(record.reference) is weakref.ReferenceType
        and record.reference is reference
        and type(record.child_id) is int
        and record.child_id == child_id
        and type(binding) is _PreparedChildOwnerBinding
        and type(binding.child_reference) is weakref.ReferenceType
        and binding.child_reference is reference
        and type(binding.child_id) is int
        and binding.child_id == child_id
    ):
        return False
    if (
        child_registry is _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY
        or child_registry is _PREPARED_REMNANT_AUTHORITY_REGISTRY
    ):
        _PREPARED_TRANSLATION_LAYOUT_REGISTRY._mark_issued_key_tainted(binding.prepared_id)
    child_registry._trusted_revoke(child_id)
    owner_registry._trusted_revoke(child_id)
    return True


def _bind_prepared_child_owner(
    registry: _ExactIntegerKeyRegistry,
    *,
    child_id: int,
    child_reference: weakref.ReferenceType[object],
    prepared: _PreparedTranslationLayoutBatch,
) -> None:
    existing = _prepared_registry_value_at_key(  # type: ignore[arg-type]
        registry,
        key=child_id,
        detail="immutable child owner registry",
    )
    if existing is not _PREPARED_REGISTRY_MISSING:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: duplicate child owner binding"
        )
    _trusted_registry_issue(
        registry,  # type: ignore[arg-type]
        key=child_id,
        value=_PreparedChildOwnerBinding(
            child_reference=child_reference,
            child_id=child_id,
            prepared_reference=weakref.ref(prepared),
            prepared_id=id(prepared),
            owner_pid=os.getpid(),
            owner_token=prepared._owner_token,  # noqa: SLF001
        ),
    )


def _prepared_child_owner_ids(
    registry: _ExactIntegerKeyRegistry,
    prepared: _PreparedTranslationLayoutBatch,
) -> tuple[set[int], set[int], bool]:
    """Return exact local and foreign IDs without consulting either parent's ledgers."""

    local: set[int] = set()
    foreign: set[int] = set()
    malformed = _sanitize_exact_integer_registry_keys(  # type: ignore[arg-type]
        registry
    )
    entries = registry._trusted_items()
    valid_entries = tuple(
        (child_id, binding) for child_id, binding in entries if type(child_id) is int
    )
    malformed = len(valid_entries) != len(entries) or malformed
    if len(valid_entries) != len(entries):
        _trusted_registry_rebuild(  # type: ignore[arg-type]
            registry,
            valid_entries,
        )
    if registry is _PREPARED_FRONTIER_INPUT_OWNER_REGISTRY:
        child_registry = _PREPARED_FRONTIER_INPUT_REGISTRY
        child_record_type = _RegisteredPreparedFrontierBatchInputs
        child_type = _PreparedFrontierBatchInputs
    elif registry is _PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY:
        child_registry = _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY
        child_record_type = _RegisteredPreparedLayoutSourceLease
        child_type = _PreparedLayoutSourceLease
    elif registry is _PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY:
        child_registry = _PREPARED_REMNANT_AUTHORITY_REGISTRY
        child_record_type = _RegisteredPreparedRemnantMeasurementAuthority
        child_type = _PreparedRemnantMeasurementAuthority
    else:
        return local, foreign, True
    malformed = (
        _sanitize_exact_integer_registry_keys(child_registry)  # type: ignore[arg-type]
        or malformed
    )
    child_entries = child_registry._trusted_items()
    valid_child_entries = tuple(
        (child_id, record) for child_id, record in child_entries if type(child_id) is int
    )
    if len(valid_child_entries) != len(child_entries):
        malformed = True
        _trusted_registry_rebuild(  # type: ignore[arg-type]
            child_registry,
            valid_child_entries,
        )
    for child_id, binding in valid_entries:
        child_record = child_registry._trusted_get(child_id)
        registered_child = (
            child_record.reference()
            if type(child_record) is child_record_type
            and type(child_record.reference) is weakref.ReferenceType
            else None
        )
        exact_registered_child = (
            type(child_record) is child_record_type
            and type(child_record.child_id) is int
            and child_record.child_id == child_id
            and type(registered_child) is child_type
            and id(registered_child) == child_id
            and child_record.reference() is registered_child
        )
        child = (
            binding.child_reference()
            if type(binding) is _PreparedChildOwnerBinding
            and type(binding.child_reference) is weakref.ReferenceType
            else None
        )
        owner = (
            binding.prepared_reference()
            if type(binding) is _PreparedChildOwnerBinding
            and type(binding.prepared_reference) is weakref.ReferenceType
            else None
        )
        valid_binding = (
            type(binding) is _PreparedChildOwnerBinding
            and type(binding.child_reference) is weakref.ReferenceType
            and type(binding.prepared_reference) is weakref.ReferenceType
            and type(binding.child_id) is int
            and binding.child_id == child_id
            and type(binding.owner_pid) is int
            and binding.owner_pid == os.getpid()
            and type(binding.prepared_id) is int
            and binding.prepared_id > 0
            and type(owner) is _PreparedTranslationLayoutBatch
            and binding.prepared_id == id(owner)
            and binding.owner_token is owner._owner_token  # noqa: SLF001
            and (
                (child is None and not exact_registered_child)
                or (
                    type(child) is child_type
                    and id(child) == child_id
                    and (not exact_registered_child or registered_child is child)
                    and binding.owner_token is child._owner_token  # type: ignore[attr-defined]  # noqa: SLF001
                    and (
                        not exact_registered_child
                        or (
                            type(child_record.owner_pid) is int
                            and child_record.owner_pid == os.getpid()
                            and child_record.prepared is owner
                            and child_record.owner_token is binding.owner_token
                            and child_record.token is getattr(child, "_token", None)
                        )
                    )
                )
            )
        )
        if not valid_binding:
            malformed = True
            continue
        if owner is prepared:
            local.add(child_id)
            if registry._issued_key_is_tainted(child_id) or child_registry._issued_key_is_tainted(
                child_id
            ):
                malformed = True
        else:
            foreign.add(child_id)
    return local, foreign, malformed


def _require_prepared_child_owner(
    registry: _ExactIntegerKeyRegistry,
    *,
    child: object,
    prepared: _PreparedTranslationLayoutBatch,
) -> _PreparedChildOwnerBinding:
    binding = _prepared_registry_value_at_key(  # type: ignore[arg-type]
        registry,
        key=id(child),
        detail="immutable child owner binding",
    )
    if (
        type(binding) is not _PreparedChildOwnerBinding
        or type(binding.child_reference) is not weakref.ReferenceType
        or binding.child_reference() is not child
        or type(binding.child_id) is not int
        or binding.child_id != id(child)
        or type(binding.prepared_reference) is not weakref.ReferenceType
        or binding.prepared_reference() is not prepared
        or type(binding.prepared_id) is not int
        or binding.prepared_id != id(prepared)
        or type(binding.owner_pid) is not int
        or binding.owner_pid != os.getpid()
        or binding.owner_token is not prepared._owner_token  # noqa: SLF001
        or binding.owner_token is not getattr(child, "_owner_token", None)
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: immutable child owner binding"
        )
    return binding


def _other_prepared_child_owner_ids(
    prepared: _PreparedTranslationLayoutBatch,
) -> tuple[set[int], set[int], set[int]]:
    """Index children immutably owned by every other batch."""

    _local_leases, foreign_leases, _malformed_leases = _prepared_child_owner_ids(
        _PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,
        prepared,
    )
    _local_inputs, foreign_inputs, _malformed_inputs = _prepared_child_owner_ids(
        _PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,
        prepared,
    )
    _local_authorities, foreign_authorities, _malformed_authorities = _prepared_child_owner_ids(
        _PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,
        prepared,
    )
    return foreign_leases, foreign_inputs, foreign_authorities


@dataclass(frozen=True, slots=True)
class _PreparedEventValidationScope:
    prepared: _PreparedTranslationLayoutBatch
    runtime: M7ReplayRuntime
    event_position: int
    source_key: tuple[str, str]
    source_lease: _PreparedLayoutSourceLease
    source_lease_token: object
    owner_pid: int


_PREPARED_EVENT_VALIDATION_SCOPES: ContextVar[_PreparedEventValidationScope | None] = (
    ContextVar("yieldforge_m8_prepared_event_validation_scope", default=None)
)


def _require_exact_event_position(event_position: int) -> int:
    if type(event_position) is not int:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event position type"
        )
    return event_position


def _prepared_standard_winner_payload(
    compiled: CompiledStandardWinner,
) -> dict[str, object]:
    fields = (
        "problem_id",
        "problem_sha256",
        "candidate_set_id",
        "candidate_set_sha256",
        "action_id",
        "candidate_id",
        "decision_key",
        "standard_profiles",
    )
    state = _exact_instance_state(compiled, CompiledStandardWinner, fields)
    identities = (
        state["problem_id"],
        state["problem_sha256"],
        state["candidate_set_id"],
        state["candidate_set_sha256"],
        state["action_id"],
        state["candidate_id"],
    )
    if any(type(value) is not str or not value for value in identities):
        raise TypeError("M8 prepared standard winner identity differs")
    if type(state["decision_key"]) is not tuple or any(
        type(value) is not str for value in state["decision_key"]
    ):
        raise TypeError("M8 prepared standard winner decision key differs")
    if type(state["standard_profiles"]) is not tuple:
        raise TypeError("M8 prepared standard profile collection differs")
    return {
        "problem_id": state["problem_id"],
        "problem_sha256": state["problem_sha256"],
        "candidate_set_id": state["candidate_set_id"],
        "candidate_set_sha256": state["candidate_set_sha256"],
        "action_id": state["action_id"],
        "candidate_id": state["candidate_id"],
        "decision_key": state["decision_key"],
        "standard_profiles": tuple(
            _prepared_standard_profile_payload(profile)
            for profile in state["standard_profiles"]
        ),
    }


def _reuse_accounting_payload(accounting: ReuseAccounting) -> dict[str, float]:
    fields = (
        "parent_remnant_area",
        "placed_area",
        "process_loss_area",
        "retained_child_area",
        "scrap_area",
        "reconciliation_delta",
        "area_tolerance",
    )
    state = _exact_instance_state(accounting, ReuseAccounting, fields)
    payload = {name: _exact_finite_float(state[name]) for name in fields}
    if (
        payload["parent_remnant_area"] <= 0
        or payload["placed_area"] < 0
        or payload["process_loss_area"] < 0
        or payload["retained_child_area"] < 0
        or payload["scrap_area"] < 0
        or payload["reconciliation_delta"] < 0
        or payload["area_tolerance"] <= 0
    ):
        raise ValueError("M8 prepared reuse accounting value differs")
    accounted = (
        payload["placed_area"]
        + payload["process_loss_area"]
        + payload["retained_child_area"]
        + payload["scrap_area"]
    )
    expected_delta = abs(payload["parent_remnant_area"] - accounted)
    if (
        payload["reconciliation_delta"] != expected_delta
        or payload["reconciliation_delta"] > payload["area_tolerance"]
    ):
        raise ValueError("M8 prepared reuse accounting reconciliation differs")
    return payload


def _prepared_standard_profile_payload(
    profile: M7StandardActionProfile,
) -> dict[str, object]:
    fields = (
        "candidate_id",
        "candidate_width",
        "accounting",
        "returned_remnant_count",
        "returned_regularity",
    )
    state = _exact_instance_state(profile, M7StandardActionProfile, fields)
    if (
        type(state["candidate_id"]) is not str
        or not state["candidate_id"]
        or type(state["returned_remnant_count"]) is not int
        or state["returned_remnant_count"] < 0
    ):
        raise TypeError("M8 prepared standard profile type differs")
    return {
        "candidate_id": state["candidate_id"],
        "candidate_width": _exact_finite_float(state["candidate_width"], positive=True),
        "accounting": _reuse_accounting_payload(state["accounting"]),
        "returned_remnant_count": state["returned_remnant_count"],
        "returned_regularity": _exact_finite_float(state["returned_regularity"]),
    }


def _rejection_scalar_payload(scalar: RejectionScalar) -> dict[str, object]:
    if type(scalar) is not RejectionScalar:
        raise TypeError("M8 prepared rejection frontier scalar type differs")
    identities = (
        scalar.problem_id,
        scalar.problem_sha256,
        scalar.candidate_set_id,
        scalar.candidate_set_sha256,
        scalar.candidate_id,
        scalar.source_transform_sha256,
        scalar.material_partition,
        scalar.fit_config_sha256,
    )
    if any(type(value) is not str or not value for value in identities):
        raise TypeError("M8 prepared rejection frontier scalar identity differs")
    return {
        "problem_id": scalar.problem_id,
        "problem_sha256": scalar.problem_sha256,
        "candidate_set_id": scalar.candidate_set_id,
        "candidate_set_sha256": scalar.candidate_set_sha256,
        "candidate_id": scalar.candidate_id,
        "source_transform_sha256": scalar.source_transform_sha256,
        "material_partition": scalar.material_partition,
        "fit_config_sha256": scalar.fit_config_sha256,
        "area": _exact_finite_float(scalar.area, positive=True),
        "width": _exact_finite_float(scalar.width, positive=True),
        "height": _exact_finite_float(scalar.height, positive=True),
    }


def _dominance_edge_payload(edge: DominanceEdge) -> dict[str, object]:
    if (
        type(edge) is not DominanceEdge
        or type(edge.partition_key) is not tuple
        or any(type(value) is not str or not value for value in edge.partition_key)
        or type(edge.dominated_candidate_id) is not str
        or type(edge.retained_candidate_id) is not str
    ):
        raise TypeError("M8 prepared rejection frontier edge type differs")
    return {
        "partition_key": edge.partition_key,
        "dominated_candidate_id": edge.dominated_candidate_id,
        "retained_candidate_id": edge.retained_candidate_id,
    }


def _compiled_rejection_problem_payload(
    compiled: CompiledRejectionProblem,
) -> dict[str, object]:
    fields = (
        "problem_id",
        "problem_sha256",
        "candidate_set_id",
        "candidate_set_sha256",
        "frontier",
    )
    state = _exact_instance_state(compiled, CompiledRejectionProblem, fields)
    identities = (
        state["problem_id"],
        state["problem_sha256"],
        state["candidate_set_id"],
        state["candidate_set_sha256"],
    )
    frontier = state["frontier"]
    if (
        any(type(value) is not str or not value for value in identities)
        or type(frontier) is not ParetoFrontier
        or type(frontier.members) is not tuple
        or type(frontier.retained) is not tuple
        or type(frontier.dominated_by) is not tuple
    ):
        raise TypeError("M8 prepared rejection problem graph differs")
    return {
        "problem_id": state["problem_id"],
        "problem_sha256": state["problem_sha256"],
        "candidate_set_id": state["candidate_set_id"],
        "candidate_set_sha256": state["candidate_set_sha256"],
        "frontier": {
            "members": tuple(_rejection_scalar_payload(item) for item in frontier.members),
            "retained": tuple(_rejection_scalar_payload(item) for item in frontier.retained),
            "dominated_by": tuple(
                _dominance_edge_payload(item) for item in frontier.dominated_by
            ),
        },
    }


def _prepared_layout_footprints_payload(
    layouts: tuple[PreparedLayoutFootprint, ...],
) -> tuple[dict[str, object], ...]:
    """Project one layout tuple without comparing it to another object graph."""

    if type(layouts) is not tuple:
        raise TypeError("M8 prepared layout footprint collection differs")
    payloads: list[dict[str, object]] = []
    for layout in layouts:
        state = _exact_instance_state(
            layout,
            PreparedLayoutFootprint,
            ("candidate_id", "geometry", "part_polygons", "vertices", "bounds"),
        )
        if (
            type(state["candidate_id"]) is not str
            or not state["candidate_id"]
            or type(state["geometry"]) not in (Polygon, MultiPolygon)
            or type(state["part_polygons"]) is not tuple
            or any(type(polygon) is not Polygon for polygon in state["part_polygons"])
            or type(state["vertices"]) is not tuple
            or type(state["bounds"]) is not tuple
            or len(state["bounds"]) != 4
        ):
            raise TypeError("M8 prepared layout footprint graph differs")
        vertices = tuple(
            tuple(_exact_finite_float(value) for value in vertex)
            if type(vertex) is tuple and len(vertex) == 2
            else (_ for _ in ()).throw(
                TypeError("M8 prepared layout footprint vertex differs")
            )
            for vertex in state["vertices"]
        )
        geometry_wkb = state["geometry"].wkb
        part_wkbs = tuple(polygon.wkb for polygon in state["part_polygons"])
        if type(geometry_wkb) is not bytes or any(type(value) is not bytes for value in part_wkbs):
            raise TypeError("M8 prepared layout footprint geometry differs")
        payloads.append(
            {
                "candidate_id": state["candidate_id"],
                "geometry_wkb_hex": geometry_wkb.hex(),
                "part_polygon_wkb_hex": tuple(value.hex() for value in part_wkbs),
                "vertices": vertices,
                "bounds": tuple(_exact_finite_float(value) for value in state["bounds"]),
            }
        )
    return tuple(payloads)


def _independent_semantic_commitment(payload: dict[str, object]) -> str:
    """Hash one object graph before comparing only the resulting exact string."""

    return f"sha256:{semantic_sha256(payload)}"


def _prepared_translation_layout_key_fingerprint(
    prepared: _PreparedTranslationLayoutBatch,
    *,
    key: tuple[str, str],
    source_binding: _PreparedLayoutSourceBinding,
    layout_footprints: tuple[PreparedLayoutFootprint, ...],
    layouts: tuple[PreparedTranslationRejectionLayout, ...],
    rejection_problem: CompiledRejectionProblem | None,
    standard_winner: CompiledStandardWinner,
) -> str:
    """Commit every registry-owned value exposed by one per-key lease."""

    payload = {
        "schema_version": "yieldforge.m8-prepared-translation-layout-key.v1",
        "batch_id": id(prepared),
        "runtime_id": prepared._runtime_id,  # noqa: SLF001
        "key": key,
        "source_binding": asdict(source_binding),
        "layout_footprints": tuple(
            {
                "candidate_id": layout.candidate_id,
                "geometry_wkb_hex": layout.geometry.wkb.hex(),
                "part_polygon_wkb_hex": tuple(
                    polygon.wkb.hex() for polygon in layout.part_polygons
                ),
                "vertices": layout.vertices,
                "bounds": layout.bounds,
            }
            for layout in layout_footprints
        ),
        "layouts": tuple(
            {
                "candidate_id": layout.candidate_id,
                "area": layout.area,
                "bounds": layout.bounds,
            }
            for layout in layouts
        ),
        "rejection_problem": (asdict(rejection_problem) if rejection_problem is not None else None),
        "standard_winner": _prepared_standard_winner_payload(standard_winner),
    }
    return f"sha256:{semantic_sha256(payload)}"


def _prepared_translation_layout_fingerprint(
    prepared: _PreparedTranslationLayoutBatch,
    source_runtime_sha256: str,
    source_bindings: _PreparedLayoutSourceBindings,
    source_event_positions: _PreparedLayoutSourceEventPositions,
    event_materials: _PreparedEventMaterials,
    source_key_fingerprints: _PreparedLayoutKeyFingerprints,
    layout_footprints: _PreparedLayoutFootprints,
    layouts: _PreparedTranslationLayouts,
    rejection_problems: _CompiledRejectionProblems,
    standard_winners: _CompiledStandardWinners,
) -> str:
    payload = {
        "schema_version": "yieldforge.m8-prepared-translation-layout-batch.v1",
        "batch_id": id(prepared),
        "runtime_id": prepared._runtime_id,  # noqa: SLF001
        "source_runtime_sha256": source_runtime_sha256,
        "source_bindings": tuple(
            {"key": key, "binding": asdict(binding)} for key, binding in source_bindings
        ),
        "source_event_positions": tuple(
            {"key": key, "event_position": event_position}
            for key, event_position in source_event_positions
        ),
        "event_materials": tuple(
            {
                "event_position": event_position,
                "key": key,
                "material": material.model_dump(mode="json"),
            }
            for event_position, event_source in enumerate(event_materials)
            if event_source is not None
            for key, material in (event_source,)
        ),
        "source_key_fingerprints": source_key_fingerprints,
        "layout_footprints": tuple(
            {
                "problem_id": key[0],
                "candidate_set_id": key[1],
                "values": tuple(
                    {
                        "candidate_id": layout.candidate_id,
                        "geometry_wkb_hex": layout.geometry.wkb.hex(),
                        "part_polygon_wkb_hex": tuple(
                            polygon.wkb.hex() for polygon in layout.part_polygons
                        ),
                        "vertices": layout.vertices,
                        "bounds": layout.bounds,
                    }
                    for layout in candidate_layouts
                ),
            }
            for key, candidate_layouts in layout_footprints
        ),
        "layouts": tuple(
            {
                "problem_id": key[0],
                "candidate_set_id": key[1],
                "values": tuple(
                    {
                        "candidate_id": layout.candidate_id,
                        "area": layout.area,
                        "bounds": layout.bounds,
                    }
                    for layout in candidate_layouts
                ),
            }
            for key, candidate_layouts in layouts
        ),
        "rejection_problems": tuple(
            {
                "key": key,
                "compiled": asdict(compiled),
            }
            for key, compiled in rejection_problems
        ),
        "standard_winners": tuple(
            {
                "key": key,
                "compiled": _prepared_standard_winner_payload(compiled),
            }
            for key, compiled in standard_winners
        ),
    }
    return f"sha256:{semantic_sha256(payload)}"


def _prepared_remnant_key_values(
    key: _PreparedRemnantSemanticKey,
) -> tuple[object, ...]:
    return (
        key.remnant_id,
        key.geometry_schema_version,
        key.geometry_wkb_hex,
        key.geometry_sha256,
        key.geometry_area,
        key.lineage_schema_version,
        key.lineage_root_stock_id,
        key.lineage_parent_remnant_id,
        key.lineage_ancestor_remnant_ids,
        key.lineage_generation,
        key.lineage_source_candidate_id,
        key.lineage_source_component_sha256,
        key.material_schema_version,
        key.material_key,
        key.material_provenance,
    )


def _prepared_remnant_measurement_values(
    measurement: PreparedTranslationRejectionRemnant,
) -> tuple[object, ...]:
    return (
        measurement.remnant_id,
        measurement.material_key,
        measurement.area,
        measurement.bounds,
    )


def _prepared_remnant_measurement_commitment(
    key: _PreparedRemnantSemanticKey,
    measurement: PreparedTranslationRejectionRemnant,
) -> str:
    payload = {
        "schema_version": "yieldforge.m8-prepared-remnant-rejection.v1",
        "semantic_key": _prepared_remnant_key_values(key),
        "measurement": _prepared_remnant_measurement_values(measurement),
    }
    return f"sha256:{semantic_sha256(payload)}"


def _validate_prepared_remnant_measurements(
    registered: _PreparedTranslationLayoutRecord,
    *,
    deep: bool,
) -> None:
    if registered.remnant_measurements.keys() != registered.remnant_commitments.keys() or len(
        registered.remnant_measurements
    ) != len(registered.remnant_snapshots):
        raise ValueError("M8 prepared translation layout batch integrity differs")
    for key, measurement in registered.remnant_measurements.items():
        commitment = registered.remnant_commitments[key]
        expected_snapshot = (
            _prepared_remnant_key_values(key),
            _prepared_remnant_measurement_values(measurement),
        )
        if registered.remnant_snapshots.get(commitment) != expected_snapshot:
            raise ValueError("M8 prepared translation layout batch integrity differs")
        if deep and commitment != _prepared_remnant_measurement_commitment(
            key,
            measurement,
        ):
            raise ValueError("M8 prepared translation layout batch integrity differs")


def _validate_prepared_remnant_authorities(
    prepared: _PreparedTranslationLayoutBatch,
    registered: _PreparedTranslationLayoutRecord,
) -> None:
    authorities = registered.remnant_authorities
    audit_authorities = prepared._remnant_authorities  # noqa: SLF001
    if (
        type(authorities) is not dict
        or type(audit_authorities) is not dict
        or len(authorities) != len(registered.remnant_measurements)
        or audit_authorities
    ):
        raise ValueError("M8 prepared remnant authority coverage differs")
    for key, authority in authorities.items():
        _require_prepared_remnant_measurement_authority(
            prepared,
            registered,
            key=key,
            authority=authority,
        )


def _issue_prepared_remnant_measurement_authority(
    prepared: _PreparedTranslationLayoutBatch,
    *,
    key: _PreparedRemnantSemanticKey,
    measurement: PreparedTranslationRejectionRemnant,
    commitment: str,
) -> _PreparedRemnantMeasurementAuthority:
    authority = _PreparedRemnantMeasurementAuthority(
        semantic_key=key,
        measurement=measurement,
        key_values=_prepared_remnant_key_values(key),
        measurement_values=_prepared_remnant_measurement_values(measurement),
        commitment=commitment,
        _owner_token=prepared._owner_token,  # noqa: SLF001
        _token=_PREPARED_REMNANT_AUTHORITY_ISSUER,
    )
    authority_id = id(authority)

    def discard(
        reference: weakref.ReferenceType[_PreparedRemnantMeasurementAuthority],
    ) -> None:
        _discard_prepared_child_reference(
            _PREPARED_REMNANT_AUTHORITY_REGISTRY,
            _PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,
            reference=reference,
            child_record_type=_RegisteredPreparedRemnantMeasurementAuthority,
            child_id=authority_id,
        )

    reference = weakref.ref(authority, discard)
    existing_authority = _prepared_registry_value_at_key(  # type: ignore[arg-type]
        _PREPARED_REMNANT_AUTHORITY_REGISTRY,
        key=authority_id,
        detail="remnant authority registry issuance",
    )
    if existing_authority is not _PREPARED_REGISTRY_MISSING:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: duplicate remnant authority"
        )
    try:
        _bind_prepared_child_owner(
            _PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,
            child_id=authority_id,
            child_reference=reference,
            prepared=prepared,
        )
        _trusted_registry_issue(
            _PREPARED_REMNANT_AUTHORITY_REGISTRY,  # type: ignore[arg-type]
            key=authority_id,
            value=_RegisteredPreparedRemnantMeasurementAuthority(
                reference=reference,
                child_id=authority_id,
                owner_pid=os.getpid(),
                token=_PREPARED_REMNANT_AUTHORITY_ISSUER,
                owner_token=prepared._owner_token,  # noqa: SLF001
                prepared=prepared,
                semantic_key=key,
                measurement=measurement,
            ),
        )
    except BaseException:
        _trusted_registry_revoke(  # type: ignore[arg-type]
            _PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,
            authority_id,
        )
        _trusted_registry_revoke(  # type: ignore[arg-type]
            _PREPARED_REMNANT_AUTHORITY_REGISTRY,
            authority_id,
        )
        raise
    return authority


def _require_prepared_remnant_measurement_authority(
    prepared: _PreparedTranslationLayoutBatch,
    batch: _PreparedTranslationLayoutRecord,
    *,
    key: _PreparedRemnantSemanticKey,
    authority: _PreparedRemnantMeasurementAuthority,
) -> PreparedTranslationRejectionRemnant:
    if type(key) is not _PreparedRemnantSemanticKey:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: remnant semantic key"
        )
    _require_prepared_child_owner(
        _PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,
        child=authority,
        prepared=prepared,
    )
    registered = _prepared_registry_value_at_key(  # type: ignore[arg-type]
        _PREPARED_REMNANT_AUTHORITY_REGISTRY,
        key=id(authority),
        detail="remnant measurement authority",
    )
    measurement = batch.remnant_measurements.get(key)
    if (
        type(authority) is not _PreparedRemnantMeasurementAuthority
        or registered is None
        or type(registered) is not _RegisteredPreparedRemnantMeasurementAuthority
        or type(registered.reference) is not weakref.ReferenceType
        or registered.reference() is not authority
        or type(registered.child_id) is not int
        or registered.child_id != id(authority)
        or registered.owner_pid != os.getpid()
        or registered.token is not authority._token  # noqa: SLF001
        or registered.owner_token is not prepared._owner_token  # noqa: SLF001
        or registered.owner_token is not authority._owner_token  # noqa: SLF001
        or registered.prepared is not prepared
        or type(registered.semantic_key) is not _PreparedRemnantSemanticKey
        or registered.semantic_key != key
        or type(authority.semantic_key) is not _PreparedRemnantSemanticKey
        or authority.semantic_key != key
        or authority.key_values != _prepared_remnant_key_values(key)
        or authority.measurement.remnant_id != key.remnant_id
        or authority.measurement.material_key != key.material_key
        or authority.measurement.area != key.geometry_area
        or registered.measurement is not authority.measurement
        or measurement is not authority.measurement
        or authority.measurement_values
        != _prepared_remnant_measurement_values(authority.measurement)
        or batch.remnant_commitments.get(key) != authority.commitment
        or batch.remnant_snapshots.get(authority.commitment)
        != (authority.key_values, authority.measurement_values)
        or batch.remnant_authorities.get(key) is not authority
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: remnant measurement authority"
        )
    return deepcopy(authority.measurement)


def _release_prepared_remnant_measurement_authorities(
    prepared: _PreparedTranslationLayoutBatch,
    batch: _PreparedTranslationLayoutRecord,
) -> None:
    authorities = batch.remnant_authorities
    try:
        live_authorities = object.__getattribute__(
            prepared,
            "_remnant_authorities",
        )
    except Exception:  # noqa: BLE001 - hostile native capability class
        live_authorities = _PREPARED_REGISTRY_MISSING
    expected = (
        {id(authority): (key, authority) for key, authority in authorities.items()}
        if type(authorities) is dict
        else {}
    )
    local_ids, _foreign_ids, malformed_owners = _prepared_child_owner_ids(
        _PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY, prepared
    )
    integrity_differs = (
        type(authorities) is not dict
        or type(live_authorities) is not dict
        or live_authorities is authorities
        or bool(live_authorities)
        or malformed_owners
        or set(expected) != local_ids
    )
    for authority_id in local_ids:
        key_and_authority = expected.get(authority_id)
        registered = _PREPARED_REMNANT_AUTHORITY_REGISTRY._trusted_get(authority_id)
        current = _PREPARED_REMNANT_AUTHORITY_REGISTRY._trusted_get(authority_id)
        if (
            key_and_authority is None
            or type(registered) is not _RegisteredPreparedRemnantMeasurementAuthority
        ):
            integrity_differs = True
        else:
            key, authority = key_and_authority
            if current is not registered:
                integrity_differs = True
            try:
                _require_prepared_remnant_measurement_authority(
                    prepared,
                    batch,
                    key=key,
                    authority=authority,
                )
            except (AttributeError, M8PreparedFrontierIntegrityError, TypeError, ValueError):
                integrity_differs = True
    for authority_id in local_ids:
        _trusted_registry_revoke(
            _PREPARED_REMNANT_AUTHORITY_REGISTRY,
            authority_id,
        )
        _trusted_registry_revoke(
            _PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,
            authority_id,
        )
    authorities.clear()
    if type(live_authorities) is dict:
        live_authorities.clear()
    object.__setattr__(prepared, "_remnant_authorities", {})
    if integrity_differs:
        raise ValueError("M8 prepared remnant authority cleanup differs")


def _require_prepared_semantic_runtime_source(
    runtime: M7ReplayRuntime,
    *,
    expected_sha256: str,
) -> None:
    """Normalize malformed or drifting live semantic input at prepared boundaries."""

    try:
        observed_sha256 = m7_semantic_runtime_sha256(runtime)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise ValueError("M8 prepared semantic runtime source differs") from error
    if observed_sha256 != expected_sha256:
        raise ValueError("M8 prepared semantic runtime source differs")


def _require_prepared_snapshot_runtime_integrity(
    registered: _PreparedTranslationLayoutRecord,
) -> None:
    """Reject drift in the issued audit snapshot; it is never consumed."""

    runtime = registered.source_runtime_snapshot.runtime
    try:
        observed_sha256 = m7_semantic_runtime_sha256(runtime)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: snapshot runtime semantics"
        ) from error
    if observed_sha256 != registered.source_runtime_integrity_sha256:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: snapshot runtime semantics"
        )
    try:
        caches_are_empty = (
            not runtime.standard_profile_cache
            and not runtime.fit_search_cache
            and not runtime.prepared_layout_cache
            and (runtime.shared_fit_search_cache is None or not runtime.shared_fit_search_cache)
        )
    except (AttributeError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: snapshot runtime caches"
        ) from error
    if not caches_are_empty:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: snapshot runtime caches"
        )


def _require_prepared_trusted_runtime_integrity(
    prepared: _PreparedTranslationLayoutBatch,
    registered: _PreparedTranslationLayoutRecord,
) -> M7ReplayRuntime:
    trusted_runtime, expected_sha256 = (
        _PREPARED_TRANSLATION_LAYOUT_REGISTRY._trusted_runtime_for_issued_value(
            id(prepared),
            registered,
        )
    )
    try:
        observed_sha256 = m7_semantic_runtime_sha256(trusted_runtime)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: trusted runtime semantics"
        ) from error
    if observed_sha256 != expected_sha256:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: trusted runtime semantics"
        )
    return trusted_runtime


def _require_prepared_translation_layout_record(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    deep: bool = False,
    owned: bool = False,
) -> _PreparedTranslationLayoutRecord:
    if (
        type(prepared) is not _PreparedTranslationLayoutBatch
        or type(runtime) not in (M7ReplayRuntime, _M7SnapshotReplayRuntime)
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: prepared runtime boundary"
        )
    registered = _prepared_translation_layout_record_at_key(prepared)
    if (
        type(prepared) is not _PreparedTranslationLayoutBatch
        or registered is None
        or type(registered) is not _PreparedTranslationLayoutRecord
        or type(registered.reference) is not weakref.ReferenceType
        or registered.reference() is not prepared
        or type(registered.owner_pid) is not int
        or registered.owner_pid != os.getpid()
        or registered.owner_token is not prepared._owner_token  # noqa: SLF001
        or type(registered.runtime_id) is not int
        or registered.runtime_id != id(runtime)
        or type(registered.source_runtime_snapshot) is not M7SemanticRuntimeSnapshot
        or type(registered.source_runtime_integrity_sha256) is not str
        or not registered.source_runtime_integrity_sha256.startswith("sha256:")
        or type(registered.source_leases) is not dict
        or type(registered.frontier_inputs) is not dict
        or type(registered.remnant_authorities) is not dict
        or type(prepared._remnant_authorities) is not dict  # noqa: SLF001
        or type(prepared._runtime_id) is not int  # noqa: SLF001
        or prepared._runtime_id != id(runtime)  # noqa: SLF001
        or type(prepared._issuance_fingerprint) is not str  # noqa: SLF001
        or not prepared._issuance_fingerprint  # noqa: SLF001
        or type(registered.layout_fingerprint) is not str
        or registered.layout_fingerprint != prepared._issuance_fingerprint  # noqa: SLF001
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: "
            "prepared translation layout batch is invalid or inactive"
        )
    if owned or deep:
        if registered.layout_fingerprint != _prepared_translation_layout_fingerprint(
            prepared,
            registered.source_runtime_snapshot.semantic_sha256,
            registered.source_bindings,
            registered.source_event_positions,
            registered.event_materials,
            registered.source_key_fingerprints,
            registered.layout_footprints,
            registered.layouts,
            registered.rejection_problems,
            registered.standard_winners,
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: "
                "prepared translation layout batch integrity differs"
            )
        _validate_prepared_snapshot_source_bindings(registered)
        _validate_prepared_remnant_measurements(registered, deep=deep)
        _validate_prepared_remnant_authorities(prepared, registered)
    if deep:
        try:
            _preflight_prepared_source_runtime(runtime)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: live source boundary"
            ) from error
        _require_prepared_snapshot_runtime_integrity(registered)
        _require_prepared_trusted_runtime_integrity(prepared, registered)
        _validate_all_prepared_layout_source_bindings(registered, runtime)
        _validate_all_prepared_event_materials(registered, runtime)
        _validate_all_prepared_source_mutation_guards(registered)
        _require_prepared_semantic_runtime_source(
            runtime,
            expected_sha256=registered.source_runtime_snapshot.semantic_sha256,
        )
    return registered


def _registered_prepared_translation_layout_record(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
) -> _PreparedTranslationLayoutRecord:
    """Return only canonical registry-owned state for an active capability."""

    return _require_prepared_translation_layout_record(prepared, runtime)


def _prepared_event_validation_is_active(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    source_key: tuple[str, str],
    source_lease: _PreparedLayoutSourceLease,
) -> bool:
    _require_exact_event_position(event_position)
    scope = _PREPARED_EVENT_VALIDATION_SCOPES.get()
    return (
        type(scope) is _PreparedEventValidationScope
        and scope.prepared is prepared
        and scope.runtime is runtime
        and scope.event_position == event_position
        and scope.source_key == source_key
        and scope.source_lease is source_lease
        and type(source_lease) is _PreparedLayoutSourceLease
        and object.__getattribute__(source_lease, "_token") is scope.source_lease_token
        and scope.owner_pid == os.getpid()
    )


def _prepared_event_position_validation_is_active(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> bool:
    _require_exact_event_position(event_position)
    scope = _PREPARED_EVENT_VALIDATION_SCOPES.get()
    return (
        type(scope) is _PreparedEventValidationScope
        and scope.prepared is prepared
        and scope.runtime is runtime
        and scope.event_position == event_position
        and scope.owner_pid == os.getpid()
    )


@contextmanager
def _activate_prepared_event_validation(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> Iterator[None]:
    """Full-check one event once, then authorize only O(1) lease hits in its scope."""

    _require_exact_event_position(event_position)
    current = _PREPARED_EVENT_VALIDATION_SCOPES.get()
    if (
        type(current) is _PreparedEventValidationScope
        and current.prepared is prepared
        and current.runtime is runtime
        and current.event_position == event_position
        and current.owner_pid == os.getpid()
    ):
        yield
        return
    leased, event_material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    batch = _require_prepared_translation_layout_record(prepared, runtime, owned=True)
    _require_prepared_snapshot_runtime_integrity(batch)
    _require_prepared_trusted_runtime_integrity(prepared, batch)
    _require_prepared_semantic_runtime_source(
        runtime,
        expected_sha256=batch.source_runtime_snapshot.semantic_sha256,
    )
    if event_position < 0 or event_position >= len(batch.event_materials):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event validation scope"
        )
    event_source = batch.event_materials[event_position]
    if event_source is None:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event validation source"
        )
    if (
        leased.key != event_source[0]
        or _prepared_material_identity_commitment(event_material)
        != _prepared_material_identity_commitment(event_source[1])
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event validation source"
        )
    source_lease = leased.reference()
    if type(source_lease) is not _PreparedLayoutSourceLease:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event validation lease"
        )
    scope = _PreparedEventValidationScope(
        prepared=prepared,
        runtime=runtime,
        event_position=event_position,
        source_key=event_source[0],
        source_lease=source_lease,
        source_lease_token=object.__getattribute__(source_lease, "_token"),
        owner_pid=os.getpid(),
    )
    token = _PREPARED_EVENT_VALIDATION_SCOPES.set(scope)
    try:
        yield
    finally:
        _PREPARED_EVENT_VALIDATION_SCOPES.reset(token)


def _prepared_rejection_problem(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> CompiledRejectionProblem:
    """Return an audit copy of one frontier compiled for the prepared batch."""

    leased, _material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    if leased.rejection_problem is None:
        raise ValueError("M8 prepared rejection problem is absent from the batch")
    return deepcopy(leased.rejection_problem)


def _consume_prepared_rejection_problem(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    observed: CompiledRejectionProblem,
) -> CompiledRejectionProblem:
    """Rebind an audit copy to private canonical frontier authority."""

    leased, _material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    canonical = leased.rejection_problem
    try:
        if type(observed) is not CompiledRejectionProblem:
            raise TypeError("observed rejection problem type differs")
        observed_commitment = _independent_semantic_commitment(
            {"rejection_problem": _compiled_rejection_problem_payload(observed)}
        )
        if canonical is None or type(canonical) is not CompiledRejectionProblem:
            raise TypeError("canonical rejection problem type differs")
        canonical_commitment = _independent_semantic_commitment(
            {"rejection_problem": _compiled_rejection_problem_payload(canonical)}
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: consumed rejection problem"
        ) from error
    if observed_commitment != canonical_commitment:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: consumed rejection problem"
        )
    return deepcopy(canonical)


def _prepared_layout_footprints(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> tuple[PreparedLayoutFootprint, ...]:
    """Return audit copies of prepared geometry in candidate order."""

    leased, _material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    layouts = leased.layout_footprints
    if tuple(layout.candidate_id for layout in layouts) != leased.source_binding.candidate_ids:
        raise ValueError("M8 prepared layout footprint candidate identities differ")
    return deepcopy(layouts)


def _consume_prepared_layout_footprints(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    observed: tuple[PreparedLayoutFootprint, ...],
) -> tuple[PreparedLayoutFootprint, ...]:
    """Rebind escaped layout copies to private canonical geometry."""

    leased, _material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    canonical = leased.layout_footprints
    try:
        if type(observed) is not tuple or any(
            type(item) is not PreparedLayoutFootprint for item in observed
        ):
            raise TypeError("observed layout footprint type differs")
        observed_commitment = _independent_semantic_commitment(
            {"layout_footprints": _prepared_layout_footprints_payload(observed)}
        )
        if type(canonical) is not tuple or any(
            type(item) is not PreparedLayoutFootprint for item in canonical
        ):
            raise TypeError("canonical layout footprint type differs")
        canonical_commitment = _independent_semantic_commitment(
            {"layout_footprints": _prepared_layout_footprints_payload(canonical)}
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: consumed layout footprints"
        ) from error
    if observed_commitment != canonical_commitment:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: consumed layout footprints"
        )
    return deepcopy(canonical)


def _prepared_standard_winner(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> CompiledStandardWinner:
    """Return an audit copy of one standard winner compiled for the batch."""

    leased, _material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    return deepcopy(leased.standard_winner)


def _consume_prepared_standard_winner(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    observed: CompiledStandardWinner,
) -> CompiledStandardWinner:
    """Rebind an escaped winner copy to private canonical winner authority."""

    leased, _material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    canonical = leased.standard_winner
    try:
        if type(observed) is not CompiledStandardWinner:
            raise TypeError("observed standard winner type differs")
        observed_commitment = _independent_semantic_commitment(
            {"standard_winner": _prepared_standard_winner_payload(observed)}
        )
        if type(canonical) is not CompiledStandardWinner:
            raise TypeError("canonical standard winner type differs")
        canonical_commitment = _independent_semantic_commitment(
            {"standard_winner": _prepared_standard_winner_payload(canonical)}
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: consumed standard winner"
        ) from error
    if observed_commitment != canonical_commitment:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: consumed standard winner"
        )
    return deepcopy(canonical)


def _prepared_remnant_semantic_key(
    remnant: RemnantStock,
) -> _PreparedRemnantSemanticKey:
    geometry = remnant.geometry
    lineage = remnant.lineage
    return _PreparedRemnantSemanticKey(
        remnant_id=remnant.remnant_id,
        geometry_schema_version=geometry.schema_version,
        geometry_wkb_hex=geometry.wkb_hex,
        geometry_sha256=geometry.polygon_sha256,
        geometry_area=geometry.area,
        lineage_schema_version=lineage.schema_version,
        lineage_root_stock_id=lineage.root_stock_id,
        lineage_parent_remnant_id=lineage.parent_remnant_id,
        lineage_ancestor_remnant_ids=lineage.ancestor_remnant_ids,
        lineage_generation=lineage.generation,
        lineage_source_candidate_id=lineage.source_candidate_id,
        lineage_source_component_sha256=lineage.source_component_sha256,
        material_schema_version=remnant.material.schema_version,
        material_key=material_key(remnant.material),
        material_provenance=str(remnant.material.provenance),
    )


def _capture_prepared_remnant_source(remnant: RemnantStock) -> RemnantStock:
    """Normalize one caller remnant into an exact detached validated preimage."""

    try:
        remnant_state = _exact_instance_state(
            remnant,
            RemnantStock,
            (
                "schema_version",
                "remnant_id",
                "geometry",
                "material",
                "root_sheet_area",
                "root_sheet_short_side",
                "lineage",
            ),
        )
        geometry_state = _exact_instance_state(
            remnant_state["geometry"],
            CanonicalPolygon,
            ("schema_version", "wkb_hex", "polygon_sha256", "area"),
        )
        material_state = _exact_instance_state(
            remnant_state["material"],
            MaterialIdentity,
            (
                "schema_version",
                "material_code",
                "grade",
                "thickness",
                "surface",
                "grain",
                "provenance",
            ),
        )
        lineage_state = _exact_instance_state(
            remnant_state["lineage"],
            RemnantLineage,
            (
                "schema_version",
                "root_stock_id",
                "parent_remnant_id",
                "ancestor_remnant_ids",
                "generation",
                "source_candidate_id",
                "source_component_sha256",
            ),
        )
        string_values = (
            remnant_state["schema_version"],
            remnant_state["remnant_id"],
            geometry_state["schema_version"],
            geometry_state["wkb_hex"],
            geometry_state["polygon_sha256"],
            material_state["schema_version"],
            material_state["material_code"],
            material_state["grade"],
            material_state["thickness"],
            material_state["surface"],
            material_state["grain"],
            lineage_state["schema_version"],
            lineage_state["root_stock_id"],
            lineage_state["source_candidate_id"],
            lineage_state["source_component_sha256"],
        )
        ancestors = lineage_state["ancestor_remnant_ids"]
        parent_id = lineage_state["parent_remnant_id"]
        if (
            any(type(value) is not str for value in string_values)
            or (parent_id is not None and type(parent_id) is not str)
            or type(ancestors) is not tuple
            or any(type(value) is not str for value in ancestors)
            or type(lineage_state["generation"]) is not int
            or type(material_state["provenance"]) is not MaterialProvenance
        ):
            raise TypeError("remnant source scalar type differs")
        geometry = CanonicalPolygon(
            schema_version=geometry_state["schema_version"],
            wkb_hex=geometry_state["wkb_hex"],
            polygon_sha256=geometry_state["polygon_sha256"],
            area=_exact_finite_float(geometry_state["area"], positive=True),
        )
        material = MaterialIdentity(
            schema_version=material_state["schema_version"],
            material_code=material_state["material_code"],
            grade=material_state["grade"],
            thickness=material_state["thickness"],
            surface=material_state["surface"],
            grain=material_state["grain"],
            provenance=material_state["provenance"],
        )
        lineage = RemnantLineage(
            schema_version=lineage_state["schema_version"],
            root_stock_id=lineage_state["root_stock_id"],
            parent_remnant_id=parent_id,
            ancestor_remnant_ids=ancestors,
            generation=lineage_state["generation"],
            source_candidate_id=lineage_state["source_candidate_id"],
            source_component_sha256=lineage_state["source_component_sha256"],
        )
        return RemnantStock(
            schema_version=remnant_state["schema_version"],
            remnant_id=remnant_state["remnant_id"],
            geometry=geometry,
            material=material,
            root_sheet_area=_exact_finite_float(
                remnant_state["root_sheet_area"], positive=True
            ),
            root_sheet_short_side=_exact_finite_float(
                remnant_state["root_sheet_short_side"], positive=True
            ),
            lineage=lineage,
        )
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: remnant source capture"
        ) from error


def _independent_prepared_remnant_measurement(
    remnant: RemnantStock,
) -> PreparedTranslationRejectionRemnant:
    """Rebind optimized scalar output to strict source geometry and lineage."""

    parent = polygon_from_record(remnant.geometry)
    if remnant.lineage.source_component_sha256 != remnant.geometry.polygon_sha256:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: remnant lineage geometry"
        )
    if (
        remnant.remnant_id in remnant.lineage.ancestor_remnant_ids
        or remnant.remnant_id == remnant.lineage.parent_remnant_id
        or derive_remnant_id(remnant.lineage, remnant.geometry, remnant.material)
        != remnant.remnant_id
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: remnant identity"
        )
    return PreparedTranslationRejectionRemnant(
        remnant_id=remnant.remnant_id,
        material_key=material_key(remnant.material),
        area=float(parent.area),
        bounds=tuple(float(value) for value in parent.bounds),
    )


def _registered_prepared_remnant_evidence(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    remnant: RemnantStock,
) -> tuple[
    _PreparedRemnantSemanticKey,
    _PreparedRemnantMeasurementAuthority,
    PreparedTranslationRejectionRemnant,
]:
    """Use O(1) hits; validate prior snapshots only before one scalar-only miss."""

    registered = _require_prepared_translation_layout_record(prepared, runtime)
    captured_remnant = _capture_prepared_remnant_source(remnant)
    semantic_key = _prepared_remnant_semantic_key(captured_remnant)
    cached = registered.remnant_measurements.get(semantic_key)
    if cached is not None:
        authority = registered.remnant_authorities.get(semantic_key)
        if authority is None:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: remnant authority coverage"
            )
        measurement = _require_prepared_remnant_measurement_authority(
            prepared,
            registered,
            key=semantic_key,
            authority=authority,
        )
        return semantic_key, authority, measurement
    _validate_prepared_remnant_measurements(registered, deep=False)
    try:
        measured = prepare_translation_rejection_remnant(captured_remnant)
        authoritative = _independent_prepared_remnant_measurement(captured_remnant)
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: remnant source measurement"
        ) from error
    if (
        type(measured) is not PreparedTranslationRejectionRemnant
        or type(authoritative) is not PreparedTranslationRejectionRemnant
        or _independent_semantic_commitment(
            {"measurement": _prepared_remnant_measurement_values(measured)}
        )
        != _independent_semantic_commitment(
            {"measurement": _prepared_remnant_measurement_values(authoritative)}
        )
        or _independent_semantic_commitment(
            {"semantic_key": _prepared_remnant_key_values(semantic_key)}
        )
        != _independent_semantic_commitment(
            {
                "semantic_key": _prepared_remnant_key_values(
                    _prepared_remnant_semantic_key(captured_remnant)
                )
            }
        )
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: remnant source measurement"
        )
    commitment = _prepared_remnant_measurement_commitment(
        semantic_key,
        measured,
    )
    current_record = _prepared_translation_layout_record_at_key(prepared)
    if current_record is not registered:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: "
            "prepared translation layout batch is invalid or inactive"
        )
    authority = _issue_prepared_remnant_measurement_authority(
        prepared,
        key=semantic_key,
        measurement=measured,
        commitment=commitment,
    )
    try:
        registered.remnant_measurements[semantic_key] = measured
        registered.remnant_commitments[semantic_key] = commitment
        registered.remnant_snapshots[commitment] = (
            authority.key_values,
            authority.measurement_values,
        )
        registered.remnant_authorities[semantic_key] = authority
        _require_prepared_remnant_measurement_authority(
            prepared,
            registered,
            key=semantic_key,
            authority=authority,
        )
    except BaseException:
        registered.remnant_measurements.pop(semantic_key, None)
        registered.remnant_commitments.pop(semantic_key, None)
        registered.remnant_snapshots.pop(commitment, None)
        registered.remnant_authorities.pop(semantic_key, None)
        _trusted_registry_revoke(  # type: ignore[arg-type]
            _PREPARED_REMNANT_AUTHORITY_REGISTRY,
            id(authority),
        )
        _trusted_registry_revoke(  # type: ignore[arg-type]
            _PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,
            id(authority),
        )
        raise
    return semantic_key, authority, deepcopy(measured)


def _registered_prepared_remnant_measurement(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    remnant: RemnantStock,
) -> PreparedTranslationRejectionRemnant:
    """Return only the detached scalar from one canonical remnant registration."""

    _key, _authority, measurement = _registered_prepared_remnant_evidence(
        prepared,
        runtime,
        remnant,
    )
    return measurement


def _issue_prepared_frontier_batch_inputs(
    inputs: _PreparedFrontierBatchInputs,
    *,
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    event_position: int,
    source_lease: _PreparedLayoutSourceLease,
    remnant_authorities: tuple[_PreparedRemnantMeasurementAuthority, ...],
) -> None:
    canonical_inputs = _require_prepared_frontier_batch_inputs_source(
        inputs,
        prepared=prepared,
        runtime=runtime,
        event_position=event_position,
        source_lease=source_lease,
        remnant_authorities=remnant_authorities,
    )
    inputs_id = id(inputs)
    batch = _require_prepared_translation_layout_record(prepared, runtime)
    registered_input = _prepared_registry_value_at_key(  # type: ignore[arg-type]
        _PREPARED_FRONTIER_INPUT_REGISTRY,
        key=inputs_id,
        detail="input registry issuance",
    )
    if inputs_id in batch.frontier_inputs or registered_input is not _PREPARED_REGISTRY_MISSING:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: duplicate input authority"
        )

    def discard(reference: weakref.ReferenceType[_PreparedFrontierBatchInputs]) -> None:
        released = _discard_prepared_child_reference(
            _PREPARED_FRONTIER_INPUT_REGISTRY,
            _PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,
            reference=reference,
            child_record_type=_RegisteredPreparedFrontierBatchInputs,
            child_id=inputs_id,
        )
        if (
            released
            and type(batch.frontier_inputs) is dict
            and batch.frontier_inputs.get(inputs_id) is reference
        ):
            batch.frontier_inputs.pop(inputs_id, None)

    reference = weakref.ref(inputs, discard)
    try:
        _bind_prepared_child_owner(
            _PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,
            child_id=inputs_id,
            child_reference=reference,
            prepared=prepared,
        )
        if (
            not _prepared_frontier_batch_inputs_have_exact_shape(canonical_inputs)
            or canonical_inputs is inputs
            or canonical_inputs.content_sha256
            != _prepared_frontier_batch_inputs_sha256(canonical_inputs)
            or canonical_inputs.content_sha256 != inputs.content_sha256
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: canonical input issuance"
            )
        _trusted_registry_issue(
            _PREPARED_FRONTIER_INPUT_REGISTRY,  # type: ignore[arg-type]
            key=inputs_id,
            value=_RegisteredPreparedFrontierBatchInputs(
                reference=reference,
                child_id=inputs_id,
                owner_pid=os.getpid(),
                token=_PREPARED_FRONTIER_INPUT_ISSUER,
                owner_token=prepared._owner_token,  # noqa: SLF001
                prepared=prepared,
                runtime=runtime,
                event_position=event_position,
                source_lease=source_lease,
                remnant_authorities=remnant_authorities,
                content_sha256=inputs.content_sha256,
                canonical_inputs=canonical_inputs,
            ),
        )
        batch.frontier_inputs[inputs_id] = reference
    except BaseException:
        _trusted_registry_revoke(  # type: ignore[arg-type]
            _PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,
            inputs_id,
        )
        _trusted_registry_revoke(  # type: ignore[arg-type]
            _PREPARED_FRONTIER_INPUT_REGISTRY,
            inputs_id,
        )
        if type(batch.frontier_inputs) is dict:
            batch.frontier_inputs.pop(inputs_id, None)
        raise


def _require_prepared_frontier_batch_inputs_source(
    inputs: _PreparedFrontierBatchInputs,
    *,
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    event_position: int,
    source_lease: _PreparedLayoutSourceLease,
    remnant_authorities: tuple[_PreparedRemnantMeasurementAuthority, ...],
) -> _PreparedFrontierBatchInputs:
    """Bind every DTO field to independent registered lease and remnant evidence."""

    try:
        batch = _require_prepared_translation_layout_record(prepared, runtime)
        lease_record = _prepared_registry_value_at_key(  # type: ignore[arg-type]
            _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,
            key=id(source_lease),
            detail="input source lease registry",
        )
        if type(lease_record) is not _RegisteredPreparedLayoutSourceLease:
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: input source lease"
            )
        leased = _require_registered_prepared_layout_source_lease(
            source_lease,
            prepared=prepared,
            runtime=runtime,
            key=lease_record.key,
            event_position=event_position,
        )
        source = leased.source_binding
        event_material = _prepared_event_material(
            leased,
            event_position=event_position,
        )
        if not _prepared_event_validation_is_active(
            prepared,
            runtime,
            event_position=event_position,
            source_key=leased.key,
            source_lease=source_lease,
        ):
            current_key, current_binding, current_problem, current_verified = (
                _prepared_key_and_inputs(runtime, event_position=event_position)
            )
            current_rejection_projections = _verified_rejection_layout_projections(
                current_verified
            )
            current_source = _prepared_layout_source_binding_from_inputs(
                runtime,
                problem=current_problem,
                verified=current_verified,
                rejection_projections=current_rejection_projections,
            )
            if (
                current_key != leased.key
                or current_source != source
                or _prepared_material_identity_commitment(current_binding.material)
                != _prepared_material_identity_commitment(event_material)
            ):
                raise M8PreparedFrontierIntegrityError(
                    "M8 prepared frontier integrity differs: live input source"
                )
        if (
            not _prepared_frontier_batch_inputs_have_exact_shape(inputs)
            or inputs._token is not _PREPARED_FRONTIER_INPUT_ISSUER  # noqa: SLF001
            or type(remnant_authorities) is not tuple
            or len(remnant_authorities) != len(inputs.measurements)
            or type(source) is not _PreparedLayoutSourceBinding
            or type(event_material) is not MaterialIdentity
            or type(leased.fit_config) is not RemnantFitConfig
            or (
                leased.rejection_problem is not None
                and type(leased.rejection_problem) is not CompiledRejectionProblem
            )
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: input source authority"
            )
        canonical_measurements = tuple(
            _require_prepared_remnant_measurement_authority(
                prepared,
                batch,
                key=authority.semantic_key,
                authority=authority,
            )
            for authority in remnant_authorities
        )
        expected = _PreparedFrontierBatchInputs(
            problem_id=source.problem_id,
            problem_sha256=source.problem_sha256,
            candidate_set_id=source.candidate_set_id,
            candidate_set_sha256=source.candidate_set_sha256,
            candidate_ids=source.candidate_ids,
            rejection_layout_candidate_ids=source.rejection_layout_candidate_ids,
            rejection_layout_sha256s=source.rejection_layout_sha256s,
            fit_config_sha256=source.fit_config_sha256,
            problem=deepcopy(leased.rejection_problem),
            event_material_key=_prepared_material_identity_key(event_material),
            fit_config=deepcopy(leased.fit_config),
            measurements=canonical_measurements,
            content_sha256="",
            _owner_token=prepared._owner_token,  # noqa: SLF001
            _token=_PREPARED_FRONTIER_INPUT_ISSUER,
        )
        observed_commitment = _prepared_frontier_batch_inputs_sha256(inputs)
        expected_commitment = _prepared_frontier_batch_inputs_sha256(expected)
        if (
            inputs.content_sha256 != observed_commitment
            or observed_commitment != expected_commitment
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: input source authority"
            )
        return replace(expected, content_sha256=expected_commitment)
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: input source authority"
        ) from error


def _require_prepared_frontier_batch_inputs(
    inputs: _PreparedFrontierBatchInputs,
    *,
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    event_position: int,
) -> _PreparedFrontierBatchInputs:
    if not _prepared_frontier_batch_inputs_have_exact_shape(inputs):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: input authority"
        )
    _require_prepared_child_owner(
        _PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,
        child=inputs,
        prepared=prepared,
    )
    registered = _prepared_registry_value_at_key(  # type: ignore[arg-type]
        _PREPARED_FRONTIER_INPUT_REGISTRY,
        key=id(inputs),
        detail="input authority",
    )
    if (
        registered is None
        or type(registered) is not _RegisteredPreparedFrontierBatchInputs
        or type(registered.reference) is not weakref.ReferenceType
        or registered.reference() is not inputs
        or type(registered.child_id) is not int
        or registered.child_id != id(inputs)
        or registered.owner_pid != os.getpid()
        or registered.token is not inputs._token  # noqa: SLF001
        or registered.owner_token is not prepared._owner_token  # noqa: SLF001
        or registered.owner_token is not inputs._owner_token  # noqa: SLF001
        or registered.prepared is not prepared
        or registered.runtime is not runtime
        or registered.event_position != event_position
        or registered.content_sha256 != inputs.content_sha256
        or registered.content_sha256 != _prepared_frontier_batch_inputs_sha256(inputs)
        or len(registered.remnant_authorities) != len(inputs.measurements)
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: input authority"
        )
    _require_prepared_frontier_batch_inputs_source(
        inputs,
        prepared=prepared,
        runtime=runtime,
        event_position=event_position,
        source_lease=registered.source_lease,
        remnant_authorities=registered.remnant_authorities,
    )
    canonical = registered.canonical_inputs
    if (
        not _prepared_frontier_batch_inputs_have_exact_shape(canonical)
        or canonical is inputs
        or canonical.content_sha256 != registered.content_sha256
        or canonical.content_sha256 != _prepared_frontier_batch_inputs_sha256(canonical)
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: canonical input authority"
        )
    detached = deepcopy(canonical)
    if (
        not _prepared_frontier_batch_inputs_have_exact_shape(detached)
        or detached is canonical
        or detached.content_sha256 != registered.content_sha256
        or detached.content_sha256 != _prepared_frontier_batch_inputs_sha256(detached)
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: detached input authority"
        )
    return detached


def _prepared_frontier_batch_inputs(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    remnants: tuple[RemnantStock, ...],
) -> _PreparedFrontierBatchInputs:
    """Bind one scalar batch exclusively to registry-owned prepared evidence."""

    if type(remnants) is not tuple or any(
        type(remnant) is not RemnantStock for remnant in remnants
    ):
        raise TypeError("M8 prepared frontier batch requires an exact remnant tuple")
    captured_remnants = tuple(
        _capture_prepared_remnant_source(remnant) for remnant in remnants
    )
    leased, _event_material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    remnant_evidence = tuple(
        _registered_prepared_remnant_evidence(
            prepared,
            runtime,
            remnant,
        )
        for remnant in captured_remnants
    )
    measurements = tuple(evidence[2] for evidence in remnant_evidence)
    source_lease = leased.reference()
    if source_lease is None:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: source lease expired"
        )
    leased = _require_registered_prepared_layout_source_lease(
        source_lease,
        prepared=prepared,
        runtime=runtime,
        key=leased.key,
        event_position=event_position,
    )
    event_scope_active = _prepared_event_validation_is_active(
        prepared,
        runtime,
        event_position=event_position,
        source_key=leased.key,
        source_lease=source_lease,
    )
    batch = _require_prepared_translation_layout_record(
        prepared,
        runtime,
        owned=not event_scope_active,
    )
    _validate_prepared_remnant_measurements(batch, deep=False)
    _validate_prepared_remnant_authorities(prepared, batch)
    event_material = deepcopy(
        _prepared_event_material(
            leased,
            event_position=event_position,
        )
    )
    fit_config = deepcopy(leased.fit_config)
    rejection_problem = deepcopy(leased.rejection_problem)
    fit_config_sha256 = "sha256:" + semantic_sha256(
        _remnant_fit_config_payload(fit_config)
    )
    if fit_config_sha256 != leased.source_binding.fit_config_sha256:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: fit configuration"
        )
    remnant_authorities = tuple(evidence[1] for evidence in remnant_evidence)
    provisional = _PreparedFrontierBatchInputs(
        problem_id=leased.source_binding.problem_id,
        problem_sha256=leased.source_binding.problem_sha256,
        candidate_set_id=leased.source_binding.candidate_set_id,
        candidate_set_sha256=leased.source_binding.candidate_set_sha256,
        candidate_ids=leased.source_binding.candidate_ids,
        rejection_layout_candidate_ids=leased.source_binding.rejection_layout_candidate_ids,
        rejection_layout_sha256s=leased.source_binding.rejection_layout_sha256s,
        fit_config_sha256=fit_config_sha256,
        problem=rejection_problem,
        event_material_key=_prepared_material_identity_key(event_material),
        fit_config=fit_config,
        measurements=measurements,
        content_sha256="",
        _owner_token=prepared._owner_token,  # noqa: SLF001
        _token=_PREPARED_FRONTIER_INPUT_ISSUER,
    )
    inputs = replace(
        provisional,
        content_sha256=_prepared_frontier_batch_inputs_sha256(provisional),
    )
    _issue_prepared_frontier_batch_inputs(
        inputs,
        prepared=prepared,
        runtime=runtime,
        event_position=event_position,
        source_lease=source_lease,
        remnant_authorities=remnant_authorities,
    )
    return inputs


def _exact_runtime_candidate_entries(
    runtime: M7ReplayRuntime,
    *,
    runtime_state: dict[str, object] | None = None,
) -> tuple[tuple[str, VerifiedProblemCandidates], ...]:
    """Read physical candidate-map entries without hashing attacker-owned keys."""

    state = _exact_prepared_runtime_state(runtime) if runtime_state is None else runtime_state
    mapping = state["runtime_candidates"]
    if type(mapping) is dict:
        backing = mapping
    elif type(mapping) is MappingProxyType:
        referents = gc.get_referents(mapping)
        if len(referents) != 1 or type(referents[0]) is not dict:
            raise TypeError("M8 prepared candidate mapping backing differs")
        backing = referents[0]
    else:
        raise TypeError("M8 prepared candidate mapping type differs")
    entries = tuple(dict.items(backing))
    if (
        len(entries) != dict.__len__(backing)
        or any(
            type(key) is not str
            or not key
            or type(value) is not VerifiedProblemCandidates
            for key, value in entries
        )
        or len({key for key, _value in entries}) != len(entries)
    ):
        raise TypeError("M8 prepared candidate mapping entry differs")
    return entries


def _preflight_prepared_source_runtime(runtime: M7ReplayRuntime) -> None:
    """Reject nonexact public source nodes before deepcopy, hashing, or lookup."""

    runtime_state = _exact_prepared_runtime_state(runtime)
    replay_input = runtime_state["replay_input"]
    _validate_exact_model_graph(replay_input, M7ReplayInput)
    _validate_exact_model_graph(runtime_state["rules"], ResidualRuleSet)
    jagua_executable = runtime_state["jagua_executable"]
    if jagua_executable is not None and type(jagua_executable) is not type(Path(".")):
        raise TypeError("M8 prepared Jagua path type differs")
    if type(runtime_state["jagua_differential_audit"]) is not bool:
        raise TypeError("M8 prepared Jagua audit flag differs")
    for _problem_id, verified in _exact_runtime_candidate_entries(
        runtime,
        runtime_state=runtime_state,
    ):
        state = _exact_instance_state(
            verified,
            VerifiedProblemCandidates,
            ("evidence", "candidates", "rejection_layouts"),
        )
        _validate_exact_model_graph(state["evidence"], M7CandidateSetEvidence)
        candidates = state["candidates"]
        rejection_layouts = state["rejection_layouts"]
        if type(candidates) is not tuple or type(rejection_layouts) is not tuple:
            raise TypeError("M8 prepared candidate collection differs")
        for candidate in tuple.__iter__(candidates):
            _validate_exact_model_graph(candidate, Candidate)
        for retained in tuple.__iter__(rejection_layouts):
            _verified_rejection_layout_projection(retained)


def _prepared_key_and_inputs(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
):  # type: ignore[no-untyped-def]
    if type(runtime) not in (M7ReplayRuntime, _M7SnapshotReplayRuntime):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: source runtime type"
        )
    if type(event_position) is not int:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event position type"
        )
    replay_input = runtime.replay_input
    if type(replay_input) is not M7ReplayInput:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: replay input type"
        )
    instances = replay_input.instances
    problems = replay_input.problems
    candidate_entries = _exact_runtime_candidate_entries(runtime)
    if (
        type(instances) is not tuple
        or type(problems) is not tuple
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: source collection type"
        )
    if event_position < 0 or event_position >= len(instances):
        raise ValueError("M8 rejection event position is outside the stream")
    binding = instances[event_position]
    if type(binding) is not TemporalInstanceBinding:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event binding type"
        )
    problem_id = binding.problem_id
    if type(problem_id) is not str or not problem_id:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event binding identity"
        )
    try:
        problem = next(
            item
            for item in problems
            if type(item) is ReusableGeometryProblem and item.problem_id == problem_id
        )
        verified = dict(candidate_entries)[problem_id]
    except (KeyError, StopIteration) as error:
        raise ValueError("M8 prepared layout footprint source geometry differs") from error
    if (
        type(problem) is not ReusableGeometryProblem
        or type(verified) is not VerifiedProblemCandidates
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: prepared source root type"
        )
    evidence = verified.evidence
    if (
        type(evidence) is not M7CandidateSetEvidence
        or type(evidence.candidate_set_id) is not str
        or not evidence.candidate_set_id
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: candidate evidence type"
        )
    return (
        (problem.problem_id, evidence.candidate_set_id),
        binding,
        problem,
        verified,
    )


def _prepared_layout_source_binding_from_inputs(
    runtime: M7ReplayRuntime,
    *,
    problem,  # type: ignore[no-untyped-def]
    verified: VerifiedProblemCandidates,
    rejection_projections: tuple[_VerifiedRejectionLayoutProjection, ...] | None = None,
) -> _PreparedLayoutSourceBinding:
    projections = (
        _verified_rejection_layout_projections(verified)
        if rejection_projections is None
        else rejection_projections
    )
    if type(projections) is not tuple or any(
        type(item) is not _VerifiedRejectionLayoutProjection for item in projections
    ):
        raise TypeError("M8 prepared rejection projection collection differs")
    problem_payload = _reusable_geometry_problem_payload(problem)
    evidence_payload = _candidate_evidence_payload(verified.evidence)
    if type(verified.candidates) is not tuple:
        raise TypeError("M8 prepared candidate collection differs")
    candidate_payloads = tuple(_candidate_payload(candidate) for candidate in verified.candidates)
    fit_config_payload = _remnant_fit_config_payload(runtime.replay_input.fit_config)
    problem_digest = semantic_sha256(
        problem_payload,
        excluded_fields={"problem_id", "content_sha256"},
    )
    candidate_set_digest = semantic_sha256(
        evidence_payload,
        excluded_fields={"candidate_set_id", "content_sha256"},
    )
    problem_sha256 = f"sha256:{problem_digest}"
    candidate_set_sha256 = f"sha256:{candidate_set_digest}"
    if (
        problem_sha256 != problem.content_sha256
        or candidate_set_sha256 != verified.evidence.content_sha256
    ):
        raise ValueError("M8 prepared layout footprint source geometry differs")
    return _PreparedLayoutSourceBinding(
        problem_id=problem.problem_id,
        problem_sha256=problem_sha256,
        candidate_set_id=verified.evidence.candidate_set_id,
        candidate_set_sha256=candidate_set_sha256,
        candidate_ids=tuple(payload["candidate_id"] for payload in candidate_payloads),
        candidate_sha256s=tuple(
            f"sha256:{semantic_sha256(payload)}" for payload in candidate_payloads
        ),
        rejection_layout_candidate_ids=tuple(
            retained.candidate_id for retained in projections
        ),
        rejection_layout_sha256s=tuple(
            "sha256:" + semantic_sha256(_verified_rejection_layout_projection_payload(retained))
            for retained in projections
        ),
        fit_config_sha256=(
            "sha256:"
            + semantic_sha256(fit_config_payload)
        ),
    )


def _prepared_layout_source_binding(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> _PreparedLayoutSourceBinding:
    try:
        _key, _binding, problem, verified = _prepared_key_and_inputs(
            runtime,
            event_position=event_position,
        )
        return _prepared_layout_source_binding_from_inputs(
            runtime,
            problem=problem,
            verified=verified,
        )
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared layout footprint source geometry differs"
        ) from error


def _validate_prepared_snapshot_source_bindings(
    registered: _PreparedTranslationLayoutRecord,
) -> None:
    """Recheck proof-owned snapshot semantics without consulting mutable caller roots."""

    snapshot = registered.source_runtime_snapshot.runtime
    sources = dict(registered.source_bindings)
    if len(sources) != len(registered.source_bindings):
        raise ValueError("M8 prepared translation layout batch integrity differs")
    for key, event_position in registered.source_event_positions:
        try:
            binding = snapshot.replay_input.instances[event_position]
            problem = next(
                item
                for item in snapshot.replay_input.problems
                if item.problem_id == binding.problem_id
            )
            verified = snapshot.runtime_candidates[binding.problem_id]
        except (IndexError, KeyError, StopIteration) as error:
            raise ValueError("M8 prepared semantic runtime snapshot differs") from error
        if (problem.problem_id, verified.evidence.candidate_set_id) != key or sources.get(
            key
        ) != _prepared_layout_source_binding_from_inputs(
            snapshot,
            problem=problem,
            verified=verified,
        ):
            raise ValueError("M8 prepared semantic runtime snapshot differs")


def _prepared_layout_live_source_identity(
    runtime: M7ReplayRuntime,
    *,
    problem,  # type: ignore[no-untyped-def]
    verified: VerifiedProblemCandidates,
) -> _PreparedLayoutLiveSourceIdentity:
    """Capture strong source roots without hashing their nested content."""

    return _PreparedLayoutLiveSourceIdentity(
        runtime=runtime,
        replay_input=runtime.replay_input,
        instances=runtime.replay_input.instances,
        problems=runtime.replay_input.problems,
        problem=problem,
        geometry_problem=problem.problem,
        problem_parts=problem.problem.parts,
        runtime_candidates=runtime.runtime_candidates,
        verified=verified,
        evidence=verified.evidence,
        candidates=verified.candidates,
        rejection_layouts=verified.rejection_layouts,
        fit_config=runtime.replay_input.fit_config,
    )


def _prepared_layout_live_source_identity_is_current(
    identity: _PreparedLayoutLiveSourceIdentity,
    runtime: M7ReplayRuntime,
    *,
    key: tuple[str, str],
    event_position: int,
    event_binding: object,
    event_material: MaterialIdentity,
    source_binding: _PreparedLayoutSourceBinding,
) -> bool:
    """Check fixed live roots and event scalars without scanning source collections."""

    try:
        _require_exact_prepared_source_key(key)
        _require_exact_event_position(event_position)
        runtime_state = _exact_prepared_runtime_state(runtime)
        replay_state = _exact_prepared_replay_input_state(runtime_state)
        instances = replay_state["instances"]
        problems = replay_state["problems"]
        runtime_candidates = runtime_state["runtime_candidates"]
        if (
            type(identity) is not _PreparedLayoutLiveSourceIdentity
            or type(instances) is not tuple
            or type(problems) is not tuple
            or type(runtime_candidates) not in (dict, MappingProxyType)
            or event_position < 0
            or event_position >= tuple.__len__(instances)
        ):
            return False
        current_event_binding = tuple.__getitem__(instances, event_position)
        binding_state = _exact_instance_state(
            current_event_binding,
            TemporalInstanceBinding,
            tuple(TemporalInstanceBinding.model_fields),
        )
        problem_state = _exact_instance_state(
            identity.problem,
            ReusableGeometryProblem,
            tuple(ReusableGeometryProblem.model_fields),
        )
        geometry_state = _exact_instance_state(
            identity.geometry_problem,
            StripPackingProblem,
            tuple(StripPackingProblem.model_fields),
        )
        verified_state = _exact_instance_state(
            identity.verified,
            VerifiedProblemCandidates,
            ("evidence", "candidates", "rejection_layouts"),
        )
        evidence_state = _exact_instance_state(
            identity.evidence,
            M7CandidateSetEvidence,
            tuple(M7CandidateSetEvidence.model_fields),
        )
        current_material = binding_state["material"]
        current_fit_config = replay_state["fit_config"]
        return (
            identity.runtime is runtime
            and identity.replay_input is runtime_state["replay_input"]
            and identity.instances is instances
            and identity.problems is problems
            and problem_state["problem"] is identity.geometry_problem
            and geometry_state["parts"] is identity.problem_parts
            and identity.runtime_candidates is runtime_candidates
            and verified_state["evidence"] is identity.evidence
            and verified_state["candidates"] is identity.candidates
            and type(identity.candidates) is tuple
            and verified_state["rejection_layouts"] is identity.rejection_layouts
            and type(identity.rejection_layouts) is tuple
            and identity.fit_config is current_fit_config
            and current_event_binding is event_binding
            and binding_state["problem_id"] == key[0]
            and problem_state["problem_id"] == key[0]
            and evidence_state["candidate_set_id"] == key[1]
            and _prepared_material_identity_commitment(current_material)
            == _prepared_material_identity_commitment(event_material)
            and "sha256:"
            + semantic_sha256(_remnant_fit_config_payload(current_fit_config))
            == source_binding.fit_config_sha256
        )
    except (AttributeError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        return False


def _install_prepared_tracked_list(
    owner: object,
    *,
    field_name: str,
    guard: _PreparedSourceMutationGuard,
) -> _PreparedTrackedListBinding | None:
    value = getattr(owner, field_name)
    if value is None:
        return None
    if type(value) is not list:
        raise ValueError("M8 prepared source nested-list guard cannot be installed")
    tracked = _PreparedMutationTrackedList(value, guard=guard)
    binding = _PreparedTrackedListBinding(
        owner=owner,
        field_name=field_name,
        original=value,
        original_values=tuple(value),
        tracked=tracked,
        canonical=list(value),
        guard=guard,
    )
    try:
        object.__setattr__(owner, field_name, tracked)
        if getattr(owner, field_name) is not tracked:
            raise ValueError("M8 prepared source nested-list guard cannot be installed")
    except Exception as error:  # noqa: BLE001 - issuance must roll back locally
        entry = _PREPARED_TRACKED_LIST_GUARDS.get(id(tracked))
        if entry is not None and entry[0]() is tracked:
            _PREPARED_TRACKED_LIST_GUARDS.pop(id(tracked), None)
        try:
            object.__setattr__(owner, field_name, value)
        except Exception as cleanup_error:  # noqa: BLE001 - normalize atomicity failure
            raise M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: source guard rollback"
            ) from cleanup_error
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: source guard issuance"
        ) from error
    return binding


def _install_prepared_source_mutation_guard(
    *,
    problem,  # type: ignore[no-untyped-def]
    verified: VerifiedProblemCandidates,
) -> tuple[_PreparedSourceMutationGuard, tuple[_PreparedTrackedListBinding, ...]]:
    """Guard every nested-list mutation path used by prepared geometry."""

    guard = _PreparedSourceMutationGuard(owner_pid=os.getpid())
    bindings: list[_PreparedTrackedListBinding] = []
    try:
        parts = tuple(problem.problem.parts)
        for part in parts:
            for field_name in ("shape", "allowed_orientations"):
                binding = _install_prepared_tracked_list(
                    part,
                    field_name=field_name,
                    guard=guard,
                )
                if binding is not None:
                    bindings.append(binding)
        parts_binding = _install_prepared_tracked_list(
            problem.problem,
            field_name="parts",
            guard=guard,
        )
        if parts_binding is not None:
            bindings.append(parts_binding)
        for candidate in verified.candidates:
            placement_binding = _install_prepared_tracked_list(
                candidate,
                field_name="placements",
                guard=guard,
            )
            if placement_binding is not None:
                bindings.append(placement_binding)
        guard.snapshots = tuple((binding.tracked, binding.canonical) for binding in bindings)
    except Exception as error:  # noqa: BLE001 - multi-binding issuance is transactional
        try:
            _restore_prepared_source_mutation_guards(
                guards=(guard,),
                bindings=tuple(bindings),
            )
        except Exception:  # noqa: BLE001 - force every owner before propagating
            _force_restore_prepared_source_mutation_guards(
                guards=(guard,),
                bindings=tuple(bindings),
            )
        if isinstance(error, M8PreparedFrontierIntegrityError):
            raise
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: source guard issuance"
        ) from error
    return guard, tuple(bindings)


def _restore_prepared_source_mutation_guards(
    *,
    guards: tuple[_PreparedSourceMutationGuard, ...],
    bindings: tuple[_PreparedTrackedListBinding, ...],
) -> None:
    """Restore exact caller-owned lists after one proof batch."""

    integrity_differs = False
    try:
        for binding in reversed(bindings):
            try:
                current = getattr(binding.owner, binding.field_name, None)
                guard_is_registered = any(binding.guard is guard for guard in guards)
                if (
                    current is not binding.tracked
                    or not guard_is_registered
                    or type(binding.tracked) is not _PreparedMutationTrackedList
                    or type(binding.original) is not list
                    or type(binding.canonical) is not list
                ):
                    integrity_differs = True
                if list.__eq__(binding.tracked, binding.canonical) is not True:
                    integrity_differs = True
                if list.__eq__(binding.original, list(binding.original_values)) is not True:
                    integrity_differs = True
            except Exception:  # noqa: BLE001 - every owner still must be restored
                integrity_differs = True
            try:
                list.__setitem__(binding.original, slice(None), binding.original_values)
            except Exception:  # noqa: BLE001 - owner rebinding remains mandatory
                integrity_differs = True
            try:
                object.__setattr__(binding.owner, binding.field_name, binding.original)
                if getattr(binding.owner, binding.field_name, None) is not binding.original:
                    integrity_differs = True
            except Exception:  # noqa: BLE001 - continue restoring sibling owners
                integrity_differs = True
            entry = _PREPARED_TRACKED_LIST_GUARDS.get(id(binding.tracked))
            if entry is not None and entry[0]() is binding.tracked:
                _PREPARED_TRACKED_LIST_GUARDS.pop(id(binding.tracked), None)
    finally:
        for guard in guards:
            guard.active = False
    if integrity_differs:
        raise ValueError("M8 prepared source nested-list guard cleanup differs")


def _prepared_source_mutation_guard(
    registered: _PreparedTranslationLayoutRecord,
    *,
    key: tuple[str, str],
) -> _PreparedSourceMutationGuard:
    matching = tuple(
        guard for candidate_key, guard in registered.source_mutation_guards if candidate_key == key
    )
    if len(matching) != 1:
        raise ValueError("M8 prepared translation layout batch integrity differs")
    return matching[0]


def _require_prepared_source_mutation_guard_unchanged(
    guard: _PreparedSourceMutationGuard,
    *,
    expected_version: int,
    check_base_list_bypass: bool = False,
) -> None:
    if guard.owner_pid != os.getpid() or not guard.active or guard.version != expected_version:
        raise ValueError("M8 prepared source mutated during prepared use")
    if check_base_list_bypass:
        for tracked, canonical in guard.snapshots:
            if (
                type(tracked) is not _PreparedMutationTrackedList
                or type(canonical) is not list
                or list.__eq__(tracked, canonical) is not True
            ):
                raise ValueError("M8 prepared source mutated during prepared use")


def _validate_all_prepared_source_mutation_guards(
    registered: _PreparedTranslationLayoutRecord,
) -> None:
    source_keys = tuple(key for key, _source in registered.source_bindings)
    guard_keys = tuple(key for key, _guard in registered.source_mutation_guards)
    if source_keys != guard_keys:
        raise ValueError("M8 prepared translation layout batch integrity differs")
    for _key, guard in registered.source_mutation_guards:
        _require_prepared_source_mutation_guard_unchanged(
            guard,
            expected_version=0,
            check_base_list_bypass=True,
        )


def _prepared_layout_record_key_values(
    registered: _PreparedTranslationLayoutRecord,
    *,
    key: tuple[str, str],
) -> tuple[
    _PreparedLayoutSourceBinding,
    tuple[PreparedLayoutFootprint, ...],
    tuple[PreparedTranslationRejectionLayout, ...],
    CompiledRejectionProblem | None,
    CompiledStandardWinner,
    str,
]:
    sources = tuple(
        value for candidate_key, value in registered.source_bindings if candidate_key == key
    )
    footprints = tuple(
        value for candidate_key, value in registered.layout_footprints if candidate_key == key
    )
    layouts = tuple(value for candidate_key, value in registered.layouts if candidate_key == key)
    rejections = tuple(
        value for candidate_key, value in registered.rejection_problems if candidate_key == key
    )
    standards = tuple(
        value for candidate_key, value in registered.standard_winners if candidate_key == key
    )
    fingerprints = tuple(
        value for candidate_key, value in registered.source_key_fingerprints if candidate_key == key
    )
    positions = tuple(
        value for candidate_key, value in registered.source_event_positions if candidate_key == key
    )
    if (
        len(sources) != 1
        or len(footprints) != 1
        or len(layouts) != 1
        or len(rejections) > 1
        or len(standards) != 1
        or len(fingerprints) != 1
        or len(positions) != 1
    ):
        raise ValueError("M8 prepared translation layout batch integrity differs")
    return (
        sources[0],
        footprints[0],
        layouts[0],
        rejections[0] if rejections else None,
        standards[0],
        fingerprints[0],
    )


def _validate_all_prepared_layout_source_bindings(
    registered: _PreparedTranslationLayoutRecord,
    runtime: M7ReplayRuntime,
) -> None:
    source_by_key = dict(registered.source_bindings)
    if len(source_by_key) != len(registered.source_bindings) or tuple(source_by_key) != tuple(
        key for key, _event_position in registered.source_event_positions
    ):
        raise ValueError("M8 prepared translation layout batch integrity differs")
    for key, event_position in registered.source_event_positions:
        current_key, _binding, _problem, _verified = _prepared_key_and_inputs(
            runtime,
            event_position=event_position,
        )
        if current_key != key or source_by_key[key] != _prepared_layout_source_binding(
            runtime,
            event_position=event_position,
        ):
            raise ValueError("M8 prepared layout footprint source geometry differs")


def _prepared_material_identity_payload(material: MaterialIdentity) -> dict[str, str]:
    fields = (
        "schema_version",
        "material_code",
        "grade",
        "thickness",
        "surface",
        "grain",
        "provenance",
    )
    state = _exact_instance_state(material, MaterialIdentity, fields)
    if (
        any(
            type(state[name]) is not str or not state[name]
            for name in fields[:-1]
        )
        or type(state["provenance"]) is not MaterialProvenance
    ):
        raise TypeError("M8 prepared event material payload differs")
    return {
        "schema_version": state["schema_version"],
        "material_code": state["material_code"],
        "grade": state["grade"],
        "thickness": state["thickness"],
        "surface": state["surface"],
        "grain": state["grain"],
        "provenance": state["provenance"].value,
    }


def _prepared_material_identity_key(
    material: MaterialIdentity,
) -> tuple[str, str, str, str, str]:
    payload = _prepared_material_identity_payload(material)
    return (
        payload["material_code"],
        payload["grade"],
        payload["thickness"],
        payload["surface"],
        payload["grain"],
    )


def _prepared_material_identity_commitment(material: MaterialIdentity) -> str:
    """Project one material independently so equality never receives its peer."""

    return _independent_semantic_commitment(
        {"material": _prepared_material_identity_payload(material)}
    )


def _validate_all_prepared_event_materials(
    registered: _PreparedTranslationLayoutRecord,
    runtime: M7ReplayRuntime,
) -> None:
    if len(registered.event_materials) != len(runtime.replay_input.instances):
        raise ValueError("M8 prepared event source snapshot differs")
    for event_position, event_source in enumerate(registered.event_materials):
        if event_source is None:
            continue
        key, material = event_source
        current_key, binding, _problem, _verified = _prepared_key_and_inputs(
            runtime,
            event_position=event_position,
        )
        if (
            current_key != key
            or _prepared_material_identity_commitment(binding.material)
            != _prepared_material_identity_commitment(material)
        ):
            raise ValueError("M8 prepared event source snapshot differs")


def _prepared_event_material(
    registered: _RegisteredPreparedLayoutSourceLease,
    *,
    event_position: int,
) -> MaterialIdentity:
    _require_exact_event_position(event_position)
    if event_position < 0 or event_position >= len(registered.event_materials):
        raise ValueError("M8 prepared event source snapshot differs")
    material = registered.event_materials[event_position]
    if material is None:
        raise ValueError("M8 prepared event source snapshot differs")
    return material


def _require_registered_prepared_layout_source_lease(
    lease: _PreparedLayoutSourceLease,
    *,
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    key: tuple[str, str],
    event_position: int,
) -> _RegisteredPreparedLayoutSourceLease:
    _require_exact_event_position(event_position)
    key = _require_exact_prepared_source_key(key)
    if (
        type(lease) is not _PreparedLayoutSourceLease
        or type(prepared) is not _PreparedTranslationLayoutBatch
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: source lease identity"
        )
    event_scope_active = _prepared_event_validation_is_active(
        prepared,
        runtime,
        event_position=event_position,
        source_key=key,
        source_lease=lease,
    )
    try:
        runtime_state = _exact_prepared_runtime_state(runtime)
        replay_state = _exact_prepared_replay_input_state(runtime_state)
        instances = replay_state["instances"]
        if type(instances) is not tuple:
            raise TypeError("M8 prepared source instance collection differs")
        if not event_scope_active:
            _preflight_prepared_source_runtime(runtime)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: live source boundary"
        ) from error
    _require_prepared_child_owner(
        _PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,
        child=lease,
        prepared=prepared,
    )
    registered = _prepared_registry_value_at_key(  # type: ignore[arg-type]
        _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,
        key=id(lease),
        detail="source lease identity",
    )
    if (
        registered is None
        or type(registered) is not _RegisteredPreparedLayoutSourceLease
        or type(registered.reference) is not weakref.ReferenceType
        or registered.reference() is not lease
        or type(registered.child_id) is not int
        or registered.child_id != id(lease)
        or registered.owner_pid != os.getpid()
        or registered.token is not lease._token  # noqa: SLF001
        or registered.owner_token is not prepared._owner_token  # noqa: SLF001
        or registered.owner_token is not lease._owner_token  # noqa: SLF001
        or registered.prepared is not prepared
        or registered.runtime is not runtime
        or type(registered.key) is not tuple
        or len(registered.key) != 2
        or any(type(value) is not str for value in tuple.__iter__(registered.key))
        or registered.key != key
        or type(registered.source_binding) is not _PreparedLayoutSourceBinding
        or type(registered.source_identity) is not _PreparedLayoutLiveSourceIdentity
        or type(registered.mutation_guard) is not _PreparedSourceMutationGuard
        or type(registered.mutation_version) is not int
        or type(registered.event_materials) is not tuple
        or type(registered.event_bindings) is not tuple
        or event_position < 0
        or event_position >= len(registered.event_bindings)
        or event_position >= tuple.__len__(instances)
        or registered.event_bindings[event_position]
        is not tuple.__getitem__(instances, event_position)
        or event_position >= len(registered.event_materials)
        or type(registered.event_materials[event_position]) is not MaterialIdentity
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: source lease identity"
        )
    try:
        batch = _require_prepared_translation_layout_record(
            prepared,
            runtime,
            owned=not event_scope_active,
        )
        _require_prepared_source_mutation_guard_unchanged(
            registered.mutation_guard,
            expected_version=registered.mutation_version,
        )
        if not _prepared_layout_live_source_identity_is_current(
            registered.source_identity,
            runtime,
            key=key,
            event_position=event_position,
            event_binding=registered.event_bindings[event_position],
            event_material=registered.event_materials[event_position],
            source_binding=registered.source_binding,
        ):
            raise ValueError("M8 prepared layout source lease is invalid or inactive")
        if event_scope_active:
            return registered
        source, footprints, layouts, rejection, standard, source_fingerprint = (
            _prepared_layout_record_key_values(batch, key=key)
        )
        expected_materials = tuple(
            material if event_source is not None and event_source[0] == key else None
            for event_source in batch.event_materials
            for material in ((event_source[1] if event_source is not None else None),)
        )
        expected_bindings = tuple(
            tuple.__getitem__(instances, position)
            if event_source is not None and event_source[0] == key
            else None
            for position, event_source in enumerate(batch.event_materials)
        )
        expected_guard = _prepared_source_mutation_guard(batch, key=key)
        if (
            registered.source_binding != source
            or registered.layout_footprints != footprints
            or registered.layouts != layouts
            or registered.rejection_problem != rejection
            or registered.standard_winner != standard
            or registered.event_materials != expected_materials
            or registered.event_bindings != expected_bindings
            or registered.mutation_guard is not expected_guard
            or registered.mutation_version != expected_guard.version
            or source_fingerprint
            != _prepared_translation_layout_key_fingerprint(
                prepared,
                key=key,
                source_binding=registered.source_binding,
                layout_footprints=registered.layout_footprints,
                layouts=registered.layouts,
                rejection_problem=registered.rejection_problem,
                standard_winner=registered.standard_winner,
            )
            or "sha256:"
            + semantic_sha256(_remnant_fit_config_payload(registered.fit_config))
            != source.fit_config_sha256
        ):
            raise ValueError("M8 prepared layout source lease semantic payload differs")
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: source lease payload"
        ) from error
    return registered


def _prepared_layout_source_lease_and_inputs(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
):  # type: ignore[no-untyped-def]
    """Deep-check once, then evaluate only proof-owned source snapshots.

    Ordinary guarded-list mutation remains a constant-time monotonic alarm.
    Base-list bypass snapshots are compared at lease entry and batch exit, not
    on every scalar access.  Repeated accesses therefore retain constant-time
    capability, source-root, and event checks.  Arbitrary same-process
    reflection can evade a live mutation alarm; it cannot change a prepared
    result because consumers never read mutable caller-owned
    candidate/config/material values after lease issuance.  The batch exit
    deep-validates the complete live source and rejects persistent drift before
    the proof can be published.
    """

    _require_exact_event_position(event_position)
    try:
        _exact_prepared_replay_input_state(_exact_prepared_runtime_state(runtime))
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: live source boundary"
        ) from error
    batch = _require_prepared_translation_layout_record(prepared, runtime)
    if event_position < 0 or event_position >= len(batch.event_materials):
        raise ValueError("M8 prepared event source snapshot differs")
    event_source = batch.event_materials[event_position]
    if event_source is None:
        raise ValueError("M8 prepared event source snapshot differs")
    key, _proof_material = event_source
    key = _require_exact_prepared_source_key(key)
    lease = batch.source_leases.get(key)
    if lease is not None:
        registered = _require_registered_prepared_layout_source_lease(
            lease,
            prepared=prepared,
            runtime=runtime,
            key=key,
            event_position=event_position,
        )
        return registered, _prepared_event_material(
            registered,
            event_position=event_position,
        )

    if _prepared_event_position_validation_is_active(
        prepared,
        runtime,
        event_position=event_position,
    ):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event validation lease"
        )
    try:
        _preflight_prepared_source_runtime(runtime)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: live source boundary"
        ) from error
    current_key, binding, problem, verified = _prepared_key_and_inputs(
        runtime,
        event_position=event_position,
    )
    if current_key != key:
        raise ValueError("M8 prepared layout footprint source geometry differs")
    mutation_guard = _prepared_source_mutation_guard(batch, key=key)
    source, footprints, layouts, rejection, standard, key_fingerprint = (
        _prepared_layout_record_key_values(batch, key=key)
    )
    if key_fingerprint != _prepared_translation_layout_key_fingerprint(
        prepared,
        key=key,
        source_binding=source,
        layout_footprints=footprints,
        layouts=layouts,
        rejection_problem=rejection,
        standard_winner=standard,
    ):
        raise ValueError("M8 prepared translation layout batch integrity differs")
    if source != _prepared_layout_source_binding(
        runtime,
        event_position=event_position,
    ):
        raise ValueError("M8 prepared layout footprint source geometry differs")
    _require_prepared_source_mutation_guard_unchanged(
        mutation_guard,
        expected_version=0,
        check_base_list_bypass=True,
    )
    _require_prepared_semantic_runtime_source(
        runtime,
        expected_sha256=batch.source_runtime_snapshot.semantic_sha256,
    )
    event_material_slots: list[MaterialIdentity | None] = [None] * len(
        runtime.replay_input.instances
    )
    event_binding_slots: list[object | None] = [None] * len(runtime.replay_input.instances)
    for candidate_position, candidate_source in enumerate(batch.event_materials):
        if candidate_source is None:
            continue
        candidate_key, candidate_material = candidate_source
        if candidate_key == key:
            candidate_binding = runtime.replay_input.instances[candidate_position]
            if (
                candidate_binding.problem_id != key[0]
                or _prepared_material_identity_commitment(candidate_binding.material)
                != _prepared_material_identity_commitment(candidate_material)
            ):
                raise ValueError("M8 prepared event source snapshot differs")
            event_material_slots[candidate_position] = candidate_material
            event_binding_slots[candidate_position] = candidate_binding
    event_materials = tuple(event_material_slots)
    event_bindings = tuple(event_binding_slots)
    material = event_materials[event_position]
    if (
        material is None
        or _prepared_material_identity_commitment(binding.material)
        != _prepared_material_identity_commitment(material)
    ):
        raise ValueError("M8 prepared event source snapshot differs")

    token = object()
    lease = _PreparedLayoutSourceLease(
        _token=token,
        _owner_token=prepared._owner_token,  # noqa: SLF001
    )
    lease_key = id(lease)

    def discard(reference: weakref.ReferenceType[_PreparedLayoutSourceLease]) -> None:
        _discard_prepared_child_reference(
            _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,
            _PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,
            reference=reference,
            child_record_type=_RegisteredPreparedLayoutSourceLease,
            child_id=lease_key,
        )

    reference = weakref.ref(lease, discard)
    registered = _RegisteredPreparedLayoutSourceLease(
        reference=reference,
        child_id=lease_key,
        owner_pid=os.getpid(),
        token=token,
        owner_token=prepared._owner_token,  # noqa: SLF001
        prepared=prepared,
        runtime=runtime,
        key=key,
        source_binding=source,
        source_identity=_prepared_layout_live_source_identity(
            runtime,
            problem=problem,
            verified=verified,
        ),
        mutation_guard=mutation_guard,
        mutation_version=mutation_guard.version,
        fit_config=deepcopy(runtime.replay_input.fit_config),
        event_materials=event_materials,
        event_bindings=event_bindings,
        layout_footprints=footprints,
        layouts=layouts,
        rejection_problem=rejection,
        standard_winner=standard,
    )
    existing_lease = _prepared_registry_value_at_key(  # type: ignore[arg-type]
        _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,
        key=lease_key,
        detail="source lease registry issuance",
    )
    if existing_lease is not _PREPARED_REGISTRY_MISSING:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: duplicate source lease"
        )
    try:
        _bind_prepared_child_owner(
            _PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,
            child_id=lease_key,
            child_reference=reference,
            prepared=prepared,
        )
        _trusted_registry_issue(
            _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,  # type: ignore[arg-type]
            key=lease_key,
            value=registered,
        )
        batch.source_leases[key] = lease
    except BaseException:
        _trusted_registry_revoke(  # type: ignore[arg-type]
            _PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,
            lease_key,
        )
        _trusted_registry_revoke(  # type: ignore[arg-type]
            _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,
            lease_key,
        )
        if type(batch.source_leases) is dict:
            batch.source_leases.pop(key, None)
        raise
    return registered, _prepared_event_material(
        registered,
        event_position=event_position,
    )


def _prepared_source_runtime(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> M7ReplayRuntime:
    """Return the proof-owned semantic runtime for surrounding prepared logic."""

    registered, _material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    batch = _require_prepared_translation_layout_record(prepared, runtime)
    source_lease = registered.reference()
    if type(source_lease) is not _PreparedLayoutSourceLease:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: source lease expired"
        )
    if _prepared_event_validation_is_active(
        prepared,
        runtime,
        event_position=event_position,
        source_key=registered.key,
        source_lease=source_lease,
    ):
        trusted_runtime, _expected_sha256 = (
            _PREPARED_TRANSLATION_LAYOUT_REGISTRY._trusted_runtime_for_issued_value(
                id(prepared),
                batch,
            )
        )
        _exact_prepared_runtime_state(trusted_runtime)
        return trusted_runtime
    _require_prepared_snapshot_runtime_integrity(batch)
    return _require_prepared_trusted_runtime_integrity(prepared, batch)


def _release_prepared_layout_source_leases(
    prepared: _PreparedTranslationLayoutBatch,
    registered: _PreparedTranslationLayoutRecord,
    runtime: M7ReplayRuntime,
) -> None:
    source_leases = registered.source_leases
    expected = (
        {id(lease): (key, lease) for key, lease in source_leases.items()}
        if type(source_leases) is dict
        else {}
    )
    local_ids, _foreign_ids, malformed_owners = _prepared_child_owner_ids(
        _PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY, prepared
    )
    integrity_differs = (
        type(source_leases) is not dict or malformed_owners or set(expected) != local_ids
    )
    for lease_id in local_ids:
        key_and_lease = expected.get(lease_id)
        leased = _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY._trusted_get(lease_id)
        current = _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY._trusted_get(lease_id)
        if (
            key_and_lease is None
            or type(leased) is not _RegisteredPreparedLayoutSourceLease
            or current is not leased
        ):
            integrity_differs = True
        else:
            key, lease = key_and_lease
            event_positions = tuple(
                event_position
                for source_key, event_position in registered.source_event_positions
                if source_key == key
            )
            if len(event_positions) != 1:
                integrity_differs = True
                continue
            try:
                _require_registered_prepared_layout_source_lease(
                    lease,
                    prepared=prepared,
                    runtime=runtime,
                    key=key,
                    event_position=event_positions[0],
                )
            except (
                AttributeError,
                M8PreparedFrontierIntegrityError,
                TypeError,
                ValueError,
            ):
                integrity_differs = True
    for lease_id in local_ids:
        _trusted_registry_revoke(
            _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,
            lease_id,
        )
        _trusted_registry_revoke(
            _PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,
            lease_id,
        )
    source_leases.clear()
    if integrity_differs:
        raise ValueError("M8 prepared layout source lease cleanup differs")


def _release_prepared_frontier_batch_inputs(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    batch: _PreparedTranslationLayoutRecord,
) -> None:
    """Invalidate every still-live DTO owned by one closing prepared batch."""

    frontier_inputs = batch.frontier_inputs
    expected = dict(frontier_inputs) if type(frontier_inputs) is dict else {}
    local_ids, _foreign_ids, malformed_owners = _prepared_child_owner_ids(
        _PREPARED_FRONTIER_INPUT_OWNER_REGISTRY, prepared
    )
    integrity_differs = (
        type(frontier_inputs) is not dict or malformed_owners or set(expected) != local_ids
    )
    for inputs_id in local_ids:
        reference = expected.get(inputs_id)
        registered = _PREPARED_FRONTIER_INPUT_REGISTRY._trusted_get(inputs_id)
        current = _PREPARED_FRONTIER_INPUT_REGISTRY._trusted_get(inputs_id)
        inputs = reference() if type(reference) is weakref.ReferenceType else None
        if (
            reference is None
            or type(registered) is not _RegisteredPreparedFrontierBatchInputs
            or current is not registered
            or registered.reference is not reference
            or inputs is None
        ):
            integrity_differs = True
        elif inputs is not None:
            try:
                _require_prepared_frontier_batch_inputs(
                    inputs,
                    prepared=prepared,
                    runtime=runtime,
                    event_position=registered.event_position,
                )
            except (AttributeError, M8PreparedFrontierIntegrityError, TypeError, ValueError):
                integrity_differs = True
    frontier_inputs.clear()
    if integrity_differs:
        raise ValueError("M8 prepared frontier input cleanup differs")


def _close_prepared_source_snapshot(
    snapshot: M7SemanticRuntimeSnapshot,
    cleanup: _PreparedSnapshotCleanupAuthority,
) -> None:
    """Drain captured local Jagua paths before consulting any issued object."""

    if os.getpid() == cleanup.owner_pid:
        if (
            cleanup.executable_path is not None
            and cleanup.executable_device is not None
            and cleanup.executable_inode is not None
        ):
            try:
                executable = os.lstat(cleanup.executable_path)
            except OSError:
                executable = None
            if (
                executable is not None
                and stat.S_ISREG(executable.st_mode)
                and executable.st_dev == cleanup.executable_device
                and executable.st_ino == cleanup.executable_inode
            ):
                try:
                    os.unlink(cleanup.executable_path)
                except OSError:
                    pass
        if (
            cleanup.directory_path is not None
            and cleanup.directory_device is not None
            and cleanup.directory_inode is not None
        ):
            try:
                directory = os.lstat(cleanup.directory_path)
            except OSError:
                directory = None
            if (
                directory is not None
                and stat.S_ISDIR(directory.st_mode)
                and directory.st_dev == cleanup.directory_device
                and directory.st_ino == cleanup.directory_inode
            ):
                try:
                    os.rmdir(cleanup.directory_path)
                except OSError:
                    pass
    M7SemanticRuntimeSnapshot.close(snapshot)


def _prepared_cleanup_integrity_error(
    error: Exception,
    *,
    fallback: str,
) -> M8PreparedFrontierIntegrityError:
    """Normalize malformed live-source failures without skipping cleanup."""

    if isinstance(error, M8PreparedFrontierIntegrityError):
        return error
    detail = fallback
    if type(error) is ValueError:
        arguments = error.args
        if (
            type(arguments) is tuple
            and len(arguments) == 1
            and type(arguments[0]) is str
            and arguments[0]
        ):
            detail = arguments[0]
    normalized = M8PreparedFrontierIntegrityError(
        f"M8 prepared frontier integrity differs: {detail}"
    )
    normalized.__cause__ = error
    return normalized


def _native_attribute_is(value: object, name: str, expected: object) -> bool:
    """Read a diagnostic native field without granting it cleanup authority."""

    try:
        return getattr(value, name, _PREPARED_REGISTRY_MISSING) is expected
    except Exception:  # noqa: BLE001 - hostile native capability class
        return False


def _force_revoke_prepared_children(
    prepared: _PreparedTranslationLayoutBatch,
    batch: _PreparedTranslationLayoutRecord,
    *,
    owner_token: object,
) -> None:
    """Revoke every entry provably owned locally, under every observed exact key."""

    malformed = False
    parent_issuance_tokens = _prepared_parent_issuance_tokens(
        local_prepared=prepared,
        local_owner_token=owner_token,
    )
    registries = (
        (
            _PREPARED_FRONTIER_INPUT_OWNER_REGISTRY,
            _PREPARED_FRONTIER_INPUT_REGISTRY,
            _RegisteredPreparedFrontierBatchInputs,
            _PreparedFrontierBatchInputs,
        ),
        (
            _PREPARED_LAYOUT_SOURCE_LEASE_OWNER_REGISTRY,
            _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY,
            _RegisteredPreparedLayoutSourceLease,
            _PreparedLayoutSourceLease,
        ),
        (
            _PREPARED_REMNANT_AUTHORITY_OWNER_REGISTRY,
            _PREPARED_REMNANT_AUTHORITY_REGISTRY,
            _RegisteredPreparedRemnantMeasurementAuthority,
            _PreparedRemnantMeasurementAuthority,
        ),
    )
    for owner_registry, child_registry, child_record_type, child_type in registries:
        malformed = (
            _sanitize_exact_integer_registry_keys(child_registry)  # type: ignore[arg-type]
            or malformed
        )
        malformed = (
            _sanitize_exact_integer_registry_keys(owner_registry)  # type: ignore[arg-type]
            or malformed
        )
        main_by_child: dict[
            int,
            list[tuple[int, object, object, object, object, bool]],
        ] = {}
        owner_by_child: dict[
            int,
            list[tuple[int, object, object, object, object, bool]],
        ] = {}

        for observed_key, child_record in child_registry._trusted_items():
            exact_record = type(child_record) is child_record_type
            exact_reference = exact_record and type(child_record.reference) is weakref.ReferenceType
            child = child_record.reference() if exact_reference else None
            exact_child = child is None or type(child) is child_type
            stored_child_id = (
                child_record.child_id
                if exact_record and type(child_record.child_id) is int
                else observed_key
            )
            child_key = stored_child_id
            main_owner = child_record.prepared if exact_record else None
            attributable = (
                exact_record
                and exact_reference
                and type(child_record.child_id) is int
                and (child is None or child_record.child_id == id(child))
                and type(child_record.owner_pid) is int
                and child_record.owner_pid == os.getpid()
                and main_owner is not None
            )
            canonical = observed_key == stored_child_id
            if not attributable or not canonical:
                malformed = True
                _trusted_registry_revoke(child_registry, observed_key)  # type: ignore[arg-type]
                continue
            parent_token_entry = parent_issuance_tokens.get(id(main_owner))
            parent_issuance_token = (
                parent_token_entry[1]
                if parent_token_entry is not None and parent_token_entry[0] is main_owner
                else None
            )
            native_token = child is None or _native_attribute_is(
                child,
                "_token",
                child_record.token,
            )
            classified_owner = None
            if parent_token_entry is not None and child_record.owner_token is parent_issuance_token:
                classified_owner = main_owner
            elif main_owner is prepared and child_record.owner_token is owner_token:
                classified_owner = prepared
            authoritative = (
                attributable
                and canonical
                and classified_owner is main_owner
                and child_record.owner_token is parent_issuance_token
            )
            credible_main_claim = classified_owner is not None
            live_owner_token = child is None or _native_attribute_is(
                child,
                "_owner_token",
                child_record.owner_token,
            )
            if not (
                attributable
                and exact_child
                and type(main_owner) is _PreparedTranslationLayoutBatch
                and native_token
                and canonical
                and authoritative
                and live_owner_token
            ):
                malformed = True
            main_by_child.setdefault(child_key, []).append(
                (
                    observed_key,
                    child_record,
                    child,
                    main_owner,
                    classified_owner,
                    credible_main_claim,
                )
            )

        for observed_key, owner_binding in owner_registry._trusted_items():
            exact_binding = type(owner_binding) is _PreparedChildOwnerBinding
            exact_child_reference = (
                exact_binding and type(owner_binding.child_reference) is weakref.ReferenceType
            )
            exact_parent_reference = (
                exact_binding and type(owner_binding.prepared_reference) is weakref.ReferenceType
            )
            child = owner_binding.child_reference() if exact_child_reference else None
            owner = owner_binding.prepared_reference() if exact_parent_reference else None
            exact_child = child is None or type(child) is child_type
            stored_child_id = (
                owner_binding.child_id
                if exact_binding and type(owner_binding.child_id) is int
                else observed_key
            )
            child_key = stored_child_id
            attributable = (
                exact_binding
                and exact_child_reference
                and exact_parent_reference
                and type(owner_binding.child_id) is int
                and (child is None or owner_binding.child_id == id(child))
                and owner is not None
                and type(owner_binding.prepared_id) is int
                and owner_binding.prepared_id == id(owner)
                and type(owner_binding.owner_pid) is int
                and owner_binding.owner_pid == os.getpid()
            )
            canonical = observed_key == stored_child_id
            if not attributable or not canonical:
                malformed = True
                _trusted_registry_revoke(owner_registry, observed_key)  # type: ignore[arg-type]
                continue
            parent_token_entry = parent_issuance_tokens.get(id(owner))
            parent_issuance_token = (
                parent_token_entry[1]
                if parent_token_entry is not None and parent_token_entry[0] is owner
                else None
            )
            classified_owner = None
            if (
                parent_token_entry is not None
                and owner_binding.owner_token is parent_issuance_token
            ):
                classified_owner = owner
            elif owner is prepared and owner_binding.owner_token is owner_token:
                classified_owner = prepared
            authoritative = (
                attributable
                and canonical
                and classified_owner is owner
                and owner_binding.owner_token is parent_issuance_token
            )
            live_owner_token = _native_attribute_is(
                owner,
                "_owner_token",
                owner_binding.owner_token,
            ) and (
                child is None
                or _native_attribute_is(
                    child,
                    "_owner_token",
                    owner_binding.owner_token,
                )
            )
            credible_owner_claim = classified_owner is not None
            if not (
                attributable
                and exact_child
                and type(owner) is _PreparedTranslationLayoutBatch
                and canonical
                and authoritative
                and live_owner_token
            ):
                malformed = True
            owner_by_child.setdefault(child_key, []).append(
                (
                    observed_key,
                    owner_binding,
                    child,
                    owner,
                    classified_owner,
                    credible_owner_claim,
                )
            )

        for child_key in set(main_by_child) | set(owner_by_child):
            main_entries = main_by_child.get(child_key, [])
            owner_entries = owner_by_child.get(child_key, [])
            if len(main_entries) > 1 or len(owner_entries) > 1:
                malformed = True
            authoritative_main_owners = [entry[4] for entry in main_entries if entry[4] is not None]
            chosen_owner = authoritative_main_owners[0] if authoritative_main_owners else None
            if chosen_owner is not None and any(
                owner is not chosen_owner for owner in authoritative_main_owners[1:]
            ):
                chosen_owner = None
                malformed = True

            if chosen_owner is None:
                authoritative_sidecar_owners = [
                    entry[4] for entry in owner_entries if entry[4] is not None
                ]
                chosen_owner = (
                    authoritative_sidecar_owners[0] if authoritative_sidecar_owners else None
                )
                if chosen_owner is not None and any(
                    owner is not chosen_owner for owner in authoritative_sidecar_owners[1:]
                ):
                    chosen_owner = None
                    malformed = True

            if chosen_owner is None:
                native_local_child = any(
                    type(entry[2]) is child_type
                    and _native_attribute_is(
                        entry[2],
                        "_owner_token",
                        owner_token,
                    )
                    for entry in (*main_entries, *owner_entries)
                )
                if native_local_child:
                    chosen_owner = prepared
                    malformed = True

            main_claims = {id(entry[3]) for entry in main_entries if entry[3] is not None}
            owner_claims = {id(entry[3]) for entry in owner_entries if entry[3] is not None}
            if main_claims and owner_claims and main_claims != owner_claims:
                malformed = True
            if chosen_owner is prepared:
                for observed_key, *_rest in main_entries:
                    _trusted_registry_revoke(  # type: ignore[arg-type]
                        child_registry,
                        observed_key,
                    )
                for observed_key, *_rest in owner_entries:
                    _trusted_registry_revoke(  # type: ignore[arg-type]
                        owner_registry,
                        observed_key,
                    )
        if (
            type(child_registry) is not _ExactIntegerKeyRegistry
            or not child_registry._seal_repaired_state()
            or type(owner_registry) is not _ExactIntegerKeyRegistry
            or not owner_registry._seal_repaired_state()
        ):
            malformed = True
    batch.frontier_inputs.clear()
    batch.source_leases.clear()
    batch.remnant_authorities.clear()
    object.__setattr__(prepared, "_remnant_authorities", {})
    if malformed:
        raise ValueError("M8 prepared immutable child owner cleanup differs")


def _force_restore_prepared_source_mutation_guards(
    *,
    guards: tuple[_PreparedSourceMutationGuard, ...],
    bindings: tuple[_PreparedTrackedListBinding, ...],
) -> None:
    """Best-effort idempotent restoration used after the validating release fails."""

    integrity_differs = False
    try:
        for binding in reversed(bindings):
            try:
                current = getattr(binding.owner, binding.field_name, None)
                if current is not binding.tracked and current is not binding.original:
                    integrity_differs = True
            except Exception:  # noqa: BLE001 - every owner still must be restored
                integrity_differs = True
            try:
                list.__setitem__(binding.original, slice(None), binding.original_values)
            except Exception:  # noqa: BLE001 - owner rebinding remains mandatory
                integrity_differs = True
            try:
                object.__setattr__(binding.owner, binding.field_name, binding.original)
                if getattr(binding.owner, binding.field_name, None) is not binding.original:
                    integrity_differs = True
            except Exception:  # noqa: BLE001 - continue restoring sibling owners
                integrity_differs = True
            entry = _PREPARED_TRACKED_LIST_GUARDS.get(id(binding.tracked))
            if entry is not None and entry[0]() is binding.tracked:
                _PREPARED_TRACKED_LIST_GUARDS.pop(id(binding.tracked), None)
    finally:
        for guard in guards:
            guard.active = False
    if integrity_differs:
        raise ValueError("M8 prepared source emergency guard cleanup differs")


def _snapshot_prepared_source_runtime(
    runtime: M7ReplayRuntime,
) -> M7SemanticRuntimeSnapshot:
    """Deep-capture a source without duplicating an outer proof's Jagua lease."""

    _preflight_prepared_source_runtime(runtime)
    candidate_entries = _exact_runtime_candidate_entries(runtime)
    if not isinstance(runtime.runtime_candidates, MappingProxyType):
        return snapshot_m7_replay_runtime(
            runtime,
            copy_operational_caches=False,
        )

    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            before_sha256 = m7_semantic_runtime_sha256(runtime)
            snapshot_runtime = M7ReplayRuntime(
                replay_input=deepcopy(runtime.replay_input),
                runtime_candidates=MappingProxyType(deepcopy(dict(candidate_entries))),
                rules=deepcopy(runtime.rules),
                standard_profile_cache={},
                fit_search_cache={},
                shared_fit_search_cache=(None if runtime.shared_fit_search_cache is None else {}),
                prepared_layout_cache=OrderedDict(),
                jagua_executable=runtime.jagua_executable,
                jagua_differential_audit=runtime.jagua_differential_audit,
            )
            captured_sha256 = m7_semantic_runtime_sha256(snapshot_runtime)
            after_sha256 = m7_semantic_runtime_sha256(runtime)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            last_error = error
            continue
        if before_sha256 == captured_sha256 == after_sha256:
            # The outer authoritative proof scope owns and validates its private Jagua
            # lease through this prepared batch. This nested snapshot owns only the
            # deep-copied semantic objects and therefore must not materialize or unlink
            # another executable copy.
            return M7SemanticRuntimeSnapshot(
                runtime=snapshot_runtime,
                semantic_sha256=captured_sha256,
                _owner_pid=os.getpid(),
            )
        last_error = ValueError("M8 prepared semantic runtime changed during capture")
    raise ValueError("M8 prepared semantic runtime could not be captured") from last_error


@contextmanager
def _prepare_translation_layout_batch(
    runtime: M7ReplayRuntime,
    *,
    event_positions: tuple[int, ...],
) -> Iterator[_PreparedTranslationLayoutBatch]:
    """Build each future problem's layouts once for one private proof batch."""

    if type(event_positions) is not tuple:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event position collection type"
        )
    exact_event_positions = tuple(tuple.__iter__(event_positions))
    if any(type(position) is not int for position in exact_event_positions):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: event position scalar type"
        )
    if exact_event_positions != tuple(sorted(set(exact_event_positions))):
        raise ValueError("M8 prepared translation event positions must be sorted unique")
    event_positions = exact_event_positions
    if type(runtime) not in (M7ReplayRuntime, _M7SnapshotReplayRuntime):
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: source runtime type"
        )
    try:
        source_runtime_snapshot = _snapshot_prepared_source_runtime(runtime)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: source runtime capture"
        ) from error
    proof_runtime = source_runtime_snapshot.runtime
    layouts_by_key: dict[
        tuple[str, str],
        tuple[PreparedTranslationRejectionLayout, ...],
    ] = {}
    footprints_by_key: dict[
        tuple[str, str],
        tuple[PreparedLayoutFootprint, ...],
    ] = {}
    sources_by_key: dict[tuple[str, str], _PreparedLayoutSourceBinding] = {}
    source_event_positions_by_key: dict[tuple[str, str], int] = {}
    event_material_slots: list[tuple[tuple[str, str], MaterialIdentity] | None] = [None] * len(
        proof_runtime.replay_input.instances
    )
    rejection_by_key: dict[tuple[str, str], CompiledRejectionProblem] = {}
    standard_by_key: dict[tuple[str, str], CompiledStandardWinner] = {}
    try:
        with profile_phase("standard_layout_materialization"):
            for event_position in event_positions:
                key, binding, problem, verified = _prepared_key_and_inputs(
                    proof_runtime,
                    event_position=event_position,
                )
                rejection_projections = _verified_rejection_layout_projections(verified)
                rejection_identity = _compiled_rejection_identity(problem, verified)
                _verified_rejection_layouts_cover_candidates(
                    verified,
                    rejection_projections=rejection_projections,
                )
                source_material_commitment = _prepared_material_identity_commitment(
                    binding.material
                )
                captured_material = deepcopy(binding.material)
                if (
                    _prepared_material_identity_commitment(captured_material)
                    != source_material_commitment
                ):
                    raise ValueError("M8 prepared event source snapshot differs")
                event_material_slots[event_position] = (key, captured_material)
                if key not in layouts_by_key:
                    source_event_positions_by_key[key] = event_position
                    sources_by_key[key] = _prepared_layout_source_binding_from_inputs(
                        proof_runtime,
                        problem=problem,
                        verified=verified,
                        rejection_projections=rejection_projections,
                    )
                    standard_by_key[key] = deepcopy(
                        compile_standard_winner(
                            proof_runtime,
                            event_position=event_position,
                        )
                    )
                    footprints = tuple(
                        prepare_layout_footprint(
                            problem.problem,
                            candidate,
                            proof_runtime.replay_input.fit_config,
                        )
                        for candidate in verified.candidates
                    )
                    footprints_by_key[key] = footprints
                    layouts_by_key[key] = tuple(
                        prepare_translation_rejection_layout(layout) for layout in footprints
                    )
                    if _verified_rejection_layouts_cover_candidates(
                        verified,
                        expected_fit_config_sha256=sources_by_key[key].fit_config_sha256,
                        prepared_footprints=footprints_by_key[key],
                        prepared_layouts=layouts_by_key[key],
                        rejection_projections=rejection_projections,
                    ):
                        with profile_phase("scalar_frontier_construction"):
                            compiled_rejection = _build_compiled_rejection_problem(
                                problem,
                                verified,
                                projections=rejection_projections,
                                identity=rejection_identity,
                            )
                        increment_profile_count(
                            "frontier_entries", len(compiled_rejection.frontier.retained)
                        )
                        expected_rejection = _independent_compiled_rejection_problem(
                            problem,
                            verified,
                            projections=rejection_projections,
                            identity=rejection_identity,
                        )
                        if _independent_semantic_commitment(
                            {"compiled_rejection": asdict(compiled_rejection)}
                        ) != _independent_semantic_commitment(
                            {"compiled_rejection": asdict(expected_rejection)}
                        ):
                            raise M8PreparedFrontierIntegrityError(
                                "M8 prepared frontier integrity differs: compiled rejection scalars"
                            )
                        rejection_by_key[key] = compiled_rejection
    except M8PreparedFrontierIntegrityError:
        source_runtime_snapshot.close()
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        source_runtime_snapshot.close()
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: canonical source build"
        ) from error
    except BaseException:
        source_runtime_snapshot.close()
        raise
    proof_runtime.standard_profile_cache.clear()
    proof_runtime.fit_search_cache.clear()
    proof_runtime.prepared_layout_cache.clear()
    if proof_runtime.shared_fit_search_cache is not None:
        proof_runtime.shared_fit_search_cache.clear()
    source_bindings = tuple(sorted(sources_by_key.items()))
    source_event_positions = tuple(sorted(source_event_positions_by_key.items()))
    event_materials = tuple(event_material_slots)
    layout_footprints = tuple(sorted(footprints_by_key.items()))
    layouts = tuple(sorted(layouts_by_key.items()))
    rejection_problems = tuple(sorted(rejection_by_key.items()))
    standard_winners = tuple(sorted(standard_by_key.items()))
    issuance_owner_token = object()
    prepared = _PreparedTranslationLayoutBatch(
        _runtime_id=id(runtime),
        _owner_token=issuance_owner_token,
    )
    key = id(prepared)
    source_key_fingerprints = tuple(
        (
            source_key,
            _prepared_translation_layout_key_fingerprint(
                prepared,
                key=source_key,
                source_binding=sources_by_key[source_key],
                layout_footprints=footprints_by_key[source_key],
                layouts=layouts_by_key[source_key],
                rejection_problem=rejection_by_key.get(source_key),
                standard_winner=standard_by_key[source_key],
            ),
        )
        for source_key in sorted(sources_by_key)
    )
    try:
        source_runtime_integrity_sha256 = m7_semantic_runtime_sha256(
            source_runtime_snapshot.runtime
        )
        layout_fingerprint = _prepared_translation_layout_fingerprint(
            prepared,
            source_runtime_snapshot.semantic_sha256,
            source_bindings,
            source_event_positions,
            event_materials,
            source_key_fingerprints,
            layout_footprints,
            layouts,
            rejection_problems,
            standard_winners,
        )
        object.__setattr__(prepared, "_issuance_fingerprint", layout_fingerprint)
    except Exception:
        source_runtime_snapshot.close()
        raise
    mutation_guards: list[tuple[tuple[str, str], _PreparedSourceMutationGuard]] = []
    mutation_bindings: list[_PreparedTrackedListBinding] = []
    try:
        for source_key, event_position in source_event_positions:
            current_key, _binding, problem, verified = _prepared_key_and_inputs(
                runtime,
                event_position=event_position,
            )
            if current_key != source_key:
                raise ValueError("M8 prepared layout footprint source geometry differs")
            guard, bindings = _install_prepared_source_mutation_guard(
                problem=problem,
                verified=verified,
            )
            mutation_guards.append((source_key, guard))
            mutation_bindings.extend(bindings)
    except (AttributeError, TypeError, ValueError):
        _restore_prepared_source_mutation_guards(
            guards=tuple(guard for _source_key, guard in mutation_guards),
            bindings=tuple(mutation_bindings),
        )
        source_runtime_snapshot.close()
        raise
    source_mutation_guards = tuple(mutation_guards)
    source_mutation_bindings = tuple(mutation_bindings)

    def discard(reference: weakref.ReferenceType[_PreparedTranslationLayoutBatch]) -> None:
        _discard_prepared_translation_layout_reference(
            reference,
            prepared_id=key,
        )

    reference = weakref.ref(prepared, discard)
    prepared_record = _PreparedTranslationLayoutRecord(
        reference=reference,
        owner_pid=os.getpid(),
        owner_token=prepared._owner_token,  # noqa: SLF001
        runtime_id=id(runtime),
        source_runtime_snapshot=source_runtime_snapshot,
        source_runtime_integrity_sha256=source_runtime_integrity_sha256,
        source_bindings=source_bindings,
        source_event_positions=source_event_positions,
        event_materials=event_materials,
        source_key_fingerprints=source_key_fingerprints,
        source_mutation_guards=source_mutation_guards,
        source_leases={},
        frontier_inputs={},
        remnant_authorities={},
        layout_footprints=layout_footprints,
        layouts=layouts,
        rejection_problems=rejection_problems,
        standard_winners=standard_winners,
        remnant_measurements={},
        remnant_commitments={},
        remnant_snapshots={},
        layout_fingerprint=layout_fingerprint,
    )
    if _sanitize_exact_integer_registry_keys(  # type: ignore[arg-type]
        _PREPARED_TRANSLATION_LAYOUT_REGISTRY
    ):
        _restore_prepared_source_mutation_guards(
            guards=tuple(guard for _source_key, guard in source_mutation_guards),
            bindings=source_mutation_bindings,
        )
        source_runtime_snapshot.close()
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: prepared registry issuance"
        )
    _trusted_registry_issue(
        _PREPARED_TRANSLATION_LAYOUT_REGISTRY,  # type: ignore[arg-type]
        key=key,
        value=prepared_record,
    )
    snapshot_cleanup_authority = (
        _PREPARED_TRANSLATION_LAYOUT_REGISTRY._snapshot_cleanup_for_issued_value(
            key,
            prepared_record,
        )
    )
    body_error: BaseException | None = None
    try:
        yield prepared
    except BaseException as error:
        body_error = error
        raise
    finally:
        current_record, malformed_parent_registry = _prepared_translation_layout_registry_state(
            prepared,
            expected_record=prepared_record,
        )
        integrity_error = (
            M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: prepared registry cleanup"
            )
            if malformed_parent_registry
            else None
        )
        try:
            prepared.require_active(runtime, deep=True)
        except Exception as error:  # noqa: BLE001 - cleanup must survive malformed live roots
            if integrity_error is None:
                integrity_error = _prepared_cleanup_integrity_error(
                    error,
                    fallback="M8 prepared layout footprint source geometry differs",
                )
        if current_record is not prepared_record and integrity_error is None:
            integrity_error = M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: prepared registry cleanup"
            )
        try:
            _release_prepared_frontier_batch_inputs(
                prepared,
                runtime,
                prepared_record,
            )
        except Exception as error:  # noqa: BLE001 - continue parent teardown
            if integrity_error is None:
                integrity_error = _prepared_cleanup_integrity_error(
                    error,
                    fallback="M8 prepared frontier input cleanup differs",
                )
        try:
            _release_prepared_layout_source_leases(
                prepared,
                prepared_record,
                runtime,
            )
        except Exception as error:  # noqa: BLE001 - continue exact list restoration
            if integrity_error is None:
                integrity_error = _prepared_cleanup_integrity_error(
                    error,
                    fallback="M8 prepared layout source lease cleanup differs",
                )
        try:
            _restore_prepared_source_mutation_guards(
                guards=tuple(guard for _source_key, guard in source_mutation_guards),
                bindings=source_mutation_bindings,
            )
        except Exception as error:  # noqa: BLE001 - registry teardown still must run
            if integrity_error is None:
                integrity_error = _prepared_cleanup_integrity_error(
                    error,
                    fallback="M8 prepared source nested-list guard cleanup differs",
                )
            try:
                _force_restore_prepared_source_mutation_guards(
                    guards=tuple(guard for _source_key, guard in source_mutation_guards),
                    bindings=source_mutation_bindings,
                )
            except Exception as fallback_error:  # noqa: BLE001 - parent pop still must run
                if integrity_error is None:
                    integrity_error = _prepared_cleanup_integrity_error(
                        fallback_error,
                        fallback="M8 prepared source emergency guard cleanup differs",
                    )
        try:
            _release_prepared_remnant_measurement_authorities(
                prepared,
                prepared_record,
            )
        except Exception as error:  # noqa: BLE001 - registry teardown still must run
            if integrity_error is None:
                integrity_error = _prepared_cleanup_integrity_error(
                    error,
                    fallback="M8 prepared remnant authority cleanup differs",
                )
        try:
            _force_revoke_prepared_children(
                prepared,
                prepared_record,
                owner_token=issuance_owner_token,
            )
        except Exception as error:  # noqa: BLE001 - parent pop and snapshot close are mandatory
            if integrity_error is None:
                integrity_error = _prepared_cleanup_integrity_error(
                    error,
                    fallback="M8 prepared immutable child owner cleanup differs",
                )
        _released_record, malformed_parent_release = _prepared_translation_layout_registry_state(
            prepared,
            expected_record=prepared_record,
            release=True,
        )
        if malformed_parent_release and integrity_error is None:
            integrity_error = M8PreparedFrontierIntegrityError(
                "M8 prepared frontier integrity differs: prepared registry cleanup"
            )
        try:
            if type(source_runtime_snapshot) is not M7SemanticRuntimeSnapshot:
                object.__setattr__(
                    source_runtime_snapshot,
                    "__class__",
                    M7SemanticRuntimeSnapshot,
                )
            if type(source_runtime_snapshot) is not M7SemanticRuntimeSnapshot:
                raise TypeError("prepared runtime snapshot class restoration failed")
            _close_prepared_source_snapshot(
                source_runtime_snapshot,
                snapshot_cleanup_authority,
            )
        except Exception as error:  # noqa: BLE001 - preserve the prior integrity failure
            if integrity_error is None:
                integrity_error = _prepared_cleanup_integrity_error(
                    error,
                    fallback="M8 prepared semantic runtime snapshot cleanup differs",
                )
        if integrity_error is not None and not isinstance(
            body_error,
            M8PreparedFrontierIntegrityError,
        ):
            raise integrity_error


def _compile_rejections_from_layouts(
    *,
    candidate_ids: tuple[str, ...],
    layouts: tuple[PreparedTranslationRejectionLayout, ...],
    remnant: PreparedTranslationRejectionRemnant,
    material: MaterialIdentity,
    fit_config: RemnantFitConfig,
) -> tuple[CompiledTranslationRejection, ...]:
    if tuple(layout.candidate_id for layout in layouts) != candidate_ids:
        raise ValueError("M8 compiled layout candidate identities differ")
    return tuple(
        CompiledTranslationRejection(
            candidate_id=candidate_id,
            certificate=certify_prepared_translation_impossible(
                layout,
                remnant,
                material=material,
                fit_config=fit_config,
            ),
        )
        for candidate_id, layout in zip(candidate_ids, layouts, strict=True)
    )


def _compile_prepared_translation_rejections(
    runtime: M7ReplayRuntime,
    *,
    prepared: _PreparedTranslationLayoutBatch,
    event_position: int,
    item: InventoryItem,
) -> tuple[CompiledTranslationRejection, ...]:
    """Use one already-validated layout set without reconstructing geometry."""

    _require_exact_event_position(event_position)
    try:
        _validate_exact_model_graph(item, InventoryItem)
        item_state = _exact_instance_state(item, InventoryItem, ("remnant", "entered_at"))
        captured_remnant = _capture_prepared_remnant_source(item_state["remnant"])
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: inventory source capture"
        ) from error
    leased, material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    remnant = _registered_prepared_remnant_measurement(
        prepared,
        runtime,
        captured_remnant,
    )
    return _compile_rejections_from_layouts(
        candidate_ids=leased.source_binding.candidate_ids,
        layouts=leased.layouts,
        remnant=remnant,
        material=material,
        fit_config=leased.fit_config,
    )


def _same_prepared_layout(left, right) -> bool:  # type: ignore[no-untyped-def]
    return (
        left.candidate_id == right.candidate_id
        and left.geometry.wkb == right.geometry.wkb
        and tuple(item.wkb for item in left.part_polygons)
        == tuple(item.wkb for item in right.part_polygons)
        and left.vertices == right.vertices
        and left.bounds == right.bounds
    )


def compile_translation_rejections(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
) -> tuple[CompiledTranslationRejection, ...]:
    """Evaluate every safe constant-time rejection for one remnant/event pair."""

    if event_position < 0 or event_position >= len(runtime.replay_input.instances):
        raise ValueError("M8 rejection event position is outside the stream")
    binding = runtime.replay_input.instances[event_position]
    problem = next(
        problem
        for problem in runtime.replay_input.problems
        if problem.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    prepared_key = (problem.problem_id, verified.evidence.candidate_set_id)
    cached_layouts = runtime.prepared_layout_cache.get(prepared_key)
    expected_layouts = tuple(
        prepare_layout_footprint(
            problem.problem,
            candidate,
            runtime.replay_input.fit_config,
        )
        for candidate in verified.candidates
    )
    if cached_layouts is None:
        runtime.prepared_layout_cache[prepared_key] = expected_layouts
        while len(runtime.prepared_layout_cache) > _MAX_PREPARED_LAYOUT_CACHE_PROBLEMS:
            runtime.prepared_layout_cache.popitem(last=False)
    else:
        if tuple(layout.candidate_id for layout in cached_layouts) != tuple(
            candidate.candidate_id for candidate in verified.candidates
        ):
            raise ValueError("M8 compiled layout candidate identities differ")
        if any(
            not _same_prepared_layout(cached, expected)
            for cached, expected in zip(
                cached_layouts,
                expected_layouts,
                strict=True,
            )
        ):
            raise ValueError("M8 prepared layout cache value differs from frozen geometry")
        runtime.prepared_layout_cache.move_to_end(prepared_key)
    return tuple(
        CompiledTranslationRejection(
            candidate_id=candidate.candidate_id,
            certificate=certify_translation_impossible(
                layout,
                item.remnant,
                material=binding.material,
                fit_config=runtime.replay_input.fit_config,
            ),
        )
        for candidate, layout in zip(verified.candidates, expected_layouts, strict=True)
    )


def _build_compiled_rejection_problem(
    problem,  # type: ignore[no-untyped-def]
    verified: VerifiedProblemCandidates,
    *,
    projections: tuple[_VerifiedRejectionLayoutProjection, ...] | None = None,
    identity: tuple[str, str, str, str] | None = None,
) -> CompiledRejectionProblem:
    retained_values = (
        _verified_rejection_layout_projections(verified)
        if projections is None
        else projections
    )
    bound_identity = (
        _compiled_rejection_identity(problem, verified) if identity is None else identity
    )
    frontier = build_pareto_frontier(
        tuple(_rejection_scalar_from_projection(retained) for retained in retained_values)
    )
    return CompiledRejectionProblem(
        problem_id=bound_identity[0],
        problem_sha256=bound_identity[1],
        candidate_set_id=bound_identity[2],
        candidate_set_sha256=bound_identity[3],
        frontier=frontier,
    )


def _independent_compiled_rejection_problem(
    problem,  # type: ignore[no-untyped-def]
    verified: VerifiedProblemCandidates,
    *,
    projections: tuple[_VerifiedRejectionLayoutProjection, ...] | None = None,
    identity: tuple[str, str, str, str] | None = None,
) -> CompiledRejectionProblem:
    """Reconstruct the canonical frontier without the production builder."""

    retained_values = (
        _verified_rejection_layout_projections(verified)
        if projections is None
        else projections
    )
    bound_identity = (
        _compiled_rejection_identity(problem, verified) if identity is None else identity
    )
    members = tuple(
        sorted(
            (
                _rejection_scalar_from_projection(retained)
                for retained in retained_values
            ),
            key=lambda item: item.identity_key,
        )
    )
    member_keys = tuple(item.identity_key for item in members)
    if len(member_keys) != len(set(member_keys)):
        raise ValueError("M8 independent frontier candidate identities differ")

    def independently_dominates(left: RejectionScalar, right: RejectionScalar) -> bool:
        if left.partition_key != right.partition_key or left.candidate_id == right.candidate_id:
            return False
        componentwise = (
            left.area <= right.area and left.width <= right.width and left.height <= right.height
        )
        return componentwise and (
            left.vector != right.vector or left.candidate_id < right.candidate_id
        )

    retained_members = tuple(
        candidate
        for candidate in members
        if not any(independently_dominates(other, candidate) for other in members)
    )
    retained_keys = {item.identity_key for item in retained_members}
    edges = []
    for candidate in members:
        if candidate.identity_key in retained_keys:
            continue
        dominators = tuple(
            item for item in retained_members if independently_dominates(item, candidate)
        )
        if not dominators:
            raise ValueError("M8 independent frontier lacks a retained dominator")
        selected = min(
            dominators,
            key=lambda item: (*item.vector, item.candidate_id),
        )
        edges.append(
            DominanceEdge(
                partition_key=candidate.partition_key,
                dominated_candidate_id=candidate.candidate_id,
                retained_candidate_id=selected.candidate_id,
            )
        )
    frontier = ParetoFrontier(
        members=members,
        retained=retained_members,
        dominated_by=tuple(
            sorted(
                edges,
                key=lambda item: (
                    item.partition_key,
                    item.dominated_candidate_id,
                    item.retained_candidate_id,
                ),
            )
        ),
    )
    return CompiledRejectionProblem(
        problem_id=bound_identity[0],
        problem_sha256=bound_identity[1],
        candidate_set_id=bound_identity[2],
        candidate_set_sha256=bound_identity[3],
        frontier=frontier,
    )


def compile_rejection_problem(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> CompiledRejectionProblem:
    """Compile retained verified scalars once for one future problem occurrence."""

    if event_position < 0 or event_position >= len(runtime.replay_input.instances):
        raise ValueError("M8 rejection event position is outside the stream")
    binding = runtime.replay_input.instances[event_position]
    problem = next(
        item for item in runtime.replay_input.problems if item.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    projections = _verified_rejection_layout_projections(verified)
    if not _verified_rejection_layouts_cover_candidates(
        verified,
        rejection_projections=projections,
    ):
        raise ValueError("M8 retained rejection layouts do not cover verified candidates")
    identity = _compiled_rejection_identity(problem, verified)
    for retained in projections:
        if (
            (
                retained.problem_id,
                retained.problem_sha256,
                retained.candidate_set_id,
                retained.candidate_set_sha256,
            )
            != identity
        ):
            raise ValueError("M8 retained rejection layout binding differs")
    with profile_phase("scalar_frontier_construction"):
        compiled = _build_compiled_rejection_problem(
            problem,
            verified,
            projections=projections,
            identity=identity,
        )
    increment_profile_count("frontier_entries", len(compiled.frontier.retained))
    return compiled


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
        standard_profiles=catalog.generated.standard_profiles,
    )


__all__ = [
    "CompiledStandardWinner",
    "CompiledRejectionProblem",
    "CompiledTranslationRejection",
    "compile_rejection_problem",
    "compile_standard_winner",
    "compile_translation_rejections",
]
