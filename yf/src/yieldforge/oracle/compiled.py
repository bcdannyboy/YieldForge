"""Compiled inventory-independent M7 winners and future relevance helpers."""

from __future__ import annotations

import os
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass

from yieldforge.baseline.archives import VerifiedProblemCandidates
from yieldforge.baseline.geometry import (
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
    M7ReplayRuntime,
    M7StandardActionProfile,
    enumerate_m7_action_catalog,
    select_m7_fallback,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle.frontier import ParetoFrontier, RejectionScalar, build_pareto_frontier
from yieldforge.oracle.profiling import increment_profile_count, profile_phase
from yieldforge.replay.contracts import InventoryItem, ReplayCostLedger
from yieldforge.reuse.contracts import RemnantStock
from yieldforge.reuse.geometry import material_key

_MAX_PREPARED_LAYOUT_CACHE_PROBLEMS = 2


def _verified_rejection_layouts_cover_candidates(
    verified: VerifiedProblemCandidates,
) -> bool:
    """Require one unique, ordered retained scalar for every unique candidate."""

    candidate_ids = tuple(item.candidate_id for item in verified.candidates)
    retained_ids = tuple(item.candidate_id for item in verified.rejection_layouts)
    return (
        bool(candidate_ids)
        and bool(retained_ids)
        and len(candidate_ids) == len(set(candidate_ids))
        and len(retained_ids) == len(set(retained_ids))
        and retained_ids == candidate_ids
    )


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


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _PreparedTranslationLayoutBatch:
    """Process-local capability for one registry-owned proof batch."""

    _runtime_id: int

    def require_active(self, runtime: M7ReplayRuntime, *, deep: bool = False) -> None:
        _require_prepared_translation_layout_record(self, runtime, deep=deep)

    def __reduce__(self) -> object:
        raise TypeError("M8 prepared translation layout batches cannot be serialized")


_PreparedTranslationLayouts = tuple[
    tuple[tuple[str, str], tuple[PreparedTranslationRejectionLayout, ...]], ...
]
_CompiledRejectionProblems = tuple[
    tuple[tuple[str, str], CompiledRejectionProblem], ...
]
_CompiledStandardWinners = tuple[
    tuple[tuple[str, str], CompiledStandardWinner], ...
]


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


@dataclass(frozen=True, slots=True)
class _PreparedTranslationLayoutRecord:
    """Private canonical storage that is never exposed through the capability."""

    reference: weakref.ReferenceType[_PreparedTranslationLayoutBatch]
    owner_pid: int
    runtime_id: int
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


_PREPARED_TRANSLATION_LAYOUT_REGISTRY: dict[
    int,
    _PreparedTranslationLayoutRecord,
] = {}


def _prepared_translation_layout_fingerprint(
    prepared: _PreparedTranslationLayoutBatch,
    layouts: _PreparedTranslationLayouts,
    rejection_problems: _CompiledRejectionProblems,
    standard_winners: _CompiledStandardWinners,
) -> str:
    payload = {
        "schema_version": "yieldforge.m8-prepared-translation-layout-batch.v1",
        "batch_id": id(prepared),
        "runtime_id": prepared._runtime_id,  # noqa: SLF001
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
                "compiled": {
                    "problem_id": compiled.problem_id,
                    "problem_sha256": compiled.problem_sha256,
                    "candidate_set_id": compiled.candidate_set_id,
                    "candidate_set_sha256": compiled.candidate_set_sha256,
                    "action_id": compiled.action_id,
                    "candidate_id": compiled.candidate_id,
                    "decision_key": compiled.decision_key,
                    "standard_profiles": tuple(
                        {
                            "candidate_id": profile.candidate_id,
                            "candidate_width": profile.candidate_width,
                            "accounting": profile.accounting.model_dump(mode="json"),
                            "returned_remnant_count": profile.returned_remnant_count,
                            "returned_regularity": profile.returned_regularity,
                        }
                        for profile in compiled.standard_profiles
                    ),
                },
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
    if (
        registered.remnant_measurements.keys()
        != registered.remnant_commitments.keys()
        or len(registered.remnant_measurements) != len(registered.remnant_snapshots)
    ):
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


def _require_prepared_translation_layout_record(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    deep: bool = False,
) -> _PreparedTranslationLayoutRecord:
    registered = _PREPARED_TRANSLATION_LAYOUT_REGISTRY.get(id(prepared))
    if (
        type(prepared) is not _PreparedTranslationLayoutBatch
        or registered is None
        or registered.reference() is not prepared
        or registered.owner_pid != os.getpid()
        or registered.runtime_id != id(runtime)
        or prepared._runtime_id != id(runtime)  # noqa: SLF001
    ):
        raise ValueError("M8 prepared translation layout batch is invalid or inactive")
    if deep:
        if registered.layout_fingerprint != _prepared_translation_layout_fingerprint(
            prepared,
            registered.layouts,
            registered.rejection_problems,
            registered.standard_winners,
        ):
            raise ValueError("M8 prepared translation layout batch integrity differs")
        _validate_prepared_remnant_measurements(registered, deep=True)
    return registered


def _registered_prepared_translation_layout_record(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
) -> _PreparedTranslationLayoutRecord:
    """Return only canonical registry-owned state for an active capability."""

    return _require_prepared_translation_layout_record(prepared, runtime)


def _prepared_rejection_problem(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> CompiledRejectionProblem:
    """Return one registry-owned frontier compiled once for the prepared batch."""

    registered = _require_prepared_translation_layout_record(prepared, runtime)
    key, _binding, _problem, _verified = _prepared_key_and_inputs(
        runtime,
        event_position=event_position,
    )
    try:
        return dict(registered.rejection_problems)[key]
    except KeyError as error:
        raise ValueError("M8 prepared rejection problem is absent from the batch") from error


def _prepared_standard_winner(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> CompiledStandardWinner:
    """Return one registry-owned standard winner compiled once for the batch."""

    registered = _require_prepared_translation_layout_record(
        prepared,
        runtime,
        deep=True,
    )
    key, _binding, _problem, _verified = _prepared_key_and_inputs(
        runtime,
        event_position=event_position,
    )
    try:
        return dict(registered.standard_winners)[key]
    except KeyError as error:
        raise ValueError("M8 prepared standard winner is absent from the batch") from error


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


def _registered_prepared_remnant_measurement(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    remnant: RemnantStock,
) -> PreparedTranslationRejectionRemnant:
    """Use O(1) hits; validate prior snapshots only before one scalar-only miss."""

    registered = _require_prepared_translation_layout_record(prepared, runtime)
    semantic_key = _prepared_remnant_semantic_key(remnant)
    cached = registered.remnant_measurements.get(semantic_key)
    if cached is not None:
        return cached
    _validate_prepared_remnant_measurements(registered, deep=False)
    measured = prepare_translation_rejection_remnant(remnant)
    if (
        measured.remnant_id != semantic_key.remnant_id
        or measured.material_key != semantic_key.material_key
        or measured.area != semantic_key.geometry_area
    ):
        raise ValueError("M8 prepared remnant measurements differ from semantic evidence")
    commitment = _prepared_remnant_measurement_commitment(
        semantic_key,
        measured,
    )
    if _PREPARED_TRANSLATION_LAYOUT_REGISTRY.get(id(prepared)) is not registered:
        raise ValueError("M8 prepared translation layout batch is invalid or inactive")
    registered.remnant_measurements[semantic_key] = measured
    registered.remnant_commitments[semantic_key] = commitment
    registered.remnant_snapshots[commitment] = (
        _prepared_remnant_key_values(semantic_key),
        _prepared_remnant_measurement_values(measured),
    )
    return measured


def _prepared_key_and_inputs(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
):  # type: ignore[no-untyped-def]
    if event_position < 0 or event_position >= len(runtime.replay_input.instances):
        raise ValueError("M8 rejection event position is outside the stream")
    binding = runtime.replay_input.instances[event_position]
    problem = next(
        problem
        for problem in runtime.replay_input.problems
        if problem.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    return (
        (problem.problem_id, verified.evidence.candidate_set_id),
        binding,
        problem,
        verified,
    )


@contextmanager
def _prepare_translation_layout_batch(
    runtime: M7ReplayRuntime,
    *,
    event_positions: tuple[int, ...],
) -> Iterator[_PreparedTranslationLayoutBatch]:
    """Build each future problem's layouts once for one private proof batch."""

    if event_positions != tuple(sorted(set(event_positions))):
        raise ValueError("M8 prepared translation event positions must be sorted unique")
    layouts_by_key: dict[
        tuple[str, str],
        tuple[PreparedTranslationRejectionLayout, ...],
    ] = {}
    rejection_by_key: dict[tuple[str, str], CompiledRejectionProblem] = {}
    standard_by_key: dict[tuple[str, str], CompiledStandardWinner] = {}
    with profile_phase("standard_layout_materialization"):
        for event_position in event_positions:
            key, _binding, problem, verified = _prepared_key_and_inputs(
                runtime,
                event_position=event_position,
            )
            if key not in layouts_by_key:
                standard_by_key[key] = deepcopy(
                    compile_standard_winner(
                        runtime,
                        event_position=event_position,
                    )
                )
                layouts_by_key[key] = tuple(
                    prepare_translation_rejection_layout(
                        prepare_layout_footprint(
                            problem.problem,
                            candidate,
                            runtime.replay_input.fit_config,
                        )
                    )
                    for candidate in verified.candidates
                )
                if _verified_rejection_layouts_cover_candidates(verified):
                    rejection_by_key[key] = compile_rejection_problem(
                        runtime,
                        event_position=event_position,
                    )
    layouts = tuple(sorted(layouts_by_key.items()))
    rejection_problems = tuple(sorted(rejection_by_key.items()))
    standard_winners = tuple(sorted(standard_by_key.items()))
    prepared = _PreparedTranslationLayoutBatch(_runtime_id=id(runtime))
    key = id(prepared)

    def discard(reference: weakref.ReferenceType[_PreparedTranslationLayoutBatch]) -> None:
        registered = _PREPARED_TRANSLATION_LAYOUT_REGISTRY.get(key)
        if registered is not None and registered.reference is reference:
            _PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(key, None)

    reference = weakref.ref(prepared, discard)
    _PREPARED_TRANSLATION_LAYOUT_REGISTRY[key] = _PreparedTranslationLayoutRecord(
        reference=reference,
        owner_pid=os.getpid(),
        runtime_id=id(runtime),
        layouts=layouts,
        rejection_problems=rejection_problems,
        standard_winners=standard_winners,
        remnant_measurements={},
        remnant_commitments={},
        remnant_snapshots={},
        layout_fingerprint=_prepared_translation_layout_fingerprint(
            prepared,
            layouts,
            rejection_problems,
            standard_winners,
        ),
    )
    try:
        yield prepared
    finally:
        integrity_error = None
        try:
            prepared.require_active(runtime, deep=True)
        except ValueError as error:
            integrity_error = error
        registered = _PREPARED_TRANSLATION_LAYOUT_REGISTRY.get(key)
        if registered is not None and registered.reference() is prepared:
            _PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(key, None)
        if integrity_error is not None:
            raise integrity_error


def _compile_rejections_from_layouts(
    runtime: M7ReplayRuntime,
    *,
    binding,  # type: ignore[no-untyped-def]
    verified,  # type: ignore[no-untyped-def]
    layouts: tuple[PreparedTranslationRejectionLayout, ...],
    remnant: PreparedTranslationRejectionRemnant,
) -> tuple[CompiledTranslationRejection, ...]:
    if tuple(layout.candidate_id for layout in layouts) != tuple(
        candidate.candidate_id for candidate in verified.candidates
    ):
        raise ValueError("M8 compiled layout candidate identities differ")
    return tuple(
        CompiledTranslationRejection(
            candidate_id=candidate.candidate_id,
            certificate=certify_prepared_translation_impossible(
                layout,
                remnant,
                material=binding.material,
                fit_config=runtime.replay_input.fit_config,
            ),
        )
        for candidate, layout in zip(verified.candidates, layouts, strict=True)
    )


def _compile_prepared_translation_rejections(
    runtime: M7ReplayRuntime,
    *,
    prepared: _PreparedTranslationLayoutBatch,
    event_position: int,
    item: InventoryItem,
) -> tuple[CompiledTranslationRejection, ...]:
    """Use one already-validated layout set without reconstructing geometry."""

    registered = _registered_prepared_translation_layout_record(prepared, runtime)
    key, binding, _problem, verified = _prepared_key_and_inputs(
        runtime,
        event_position=event_position,
    )
    matching = tuple(
        layouts for candidate_key, layouts in registered.layouts if candidate_key == key
    )
    if len(matching) != 1:
        raise ValueError("M8 prepared translation layouts do not cover the event problem")
    remnant = _registered_prepared_remnant_measurement(
        prepared,
        runtime,
        item.remnant,
    )
    return _compile_rejections_from_layouts(
        runtime,
        binding=binding,
        verified=verified,
        layouts=matching[0],
        remnant=remnant,
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
        item
        for item in runtime.replay_input.problems
        if item.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    if not _verified_rejection_layouts_cover_candidates(verified):
        raise ValueError("M8 retained rejection layouts do not cover verified candidates")
    for retained in verified.rejection_layouts:
        if (
            retained.problem_id != problem.problem_id
            or retained.problem_sha256 != problem.content_sha256
            or retained.candidate_set_id != verified.evidence.candidate_set_id
            or retained.candidate_set_sha256 != verified.evidence.content_sha256
        ):
            raise ValueError("M8 retained rejection layout binding differs")
    with profile_phase("scalar_frontier_construction"):
        frontier = build_pareto_frontier(
            tuple(
                RejectionScalar.from_verified(retained)
                for retained in verified.rejection_layouts
            )
        )
    increment_profile_count("frontier_entries", len(frontier.retained))
    return CompiledRejectionProblem(
        problem_id=problem.problem_id,
        problem_sha256=problem.content_sha256,
        candidate_set_id=verified.evidence.candidate_set_id,
        candidate_set_sha256=verified.evidence.content_sha256,
        frontier=frontier,
    )


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
