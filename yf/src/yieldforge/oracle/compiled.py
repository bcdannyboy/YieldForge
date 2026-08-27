"""Compiled inventory-independent M7 winners and future relevance helpers."""

from __future__ import annotations

import os
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
from types import MappingProxyType

from yieldforge.baseline.archives import VerifiedProblemCandidates
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
    M7ReplayRuntime,
    M7SemanticRuntimeSnapshot,
    M7StandardActionProfile,
    enumerate_m7_action_catalog,
    m7_semantic_runtime_sha256,
    select_m7_fallback,
    snapshot_m7_replay_runtime,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle.frontier import ParetoFrontier, RejectionScalar, build_pareto_frontier
from yieldforge.oracle.profiling import increment_profile_count, profile_phase
from yieldforge.replay.contracts import InventoryItem, ReplayCostLedger
from yieldforge.reuse.contracts import MaterialIdentity, RemnantFitConfig, RemnantStock
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


class _PreparedMutationTrackedList(list):  # type: ignore[type-arg]
    """List-compatible source storage that records all ordinary mutations."""

    __slots__ = ("_guard",)

    def __init__(self, values, *, guard: _PreparedSourceMutationGuard) -> None:  # type: ignore[no-untyped-def]
        super().__init__(values)
        self._guard = guard

    def _bump(self) -> None:
        self._guard.version += 1

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

    def __reduce__(self) -> object:
        raise TypeError("M8 prepared layout source leases cannot be serialized")


@dataclass(frozen=True, slots=True)
class _RegisteredPreparedLayoutSourceLease:
    reference: weakref.ReferenceType[_PreparedLayoutSourceLease]
    owner_pid: int
    token: object
    prepared: _PreparedTranslationLayoutBatch
    runtime: M7ReplayRuntime
    operational_runtime: M7ReplayRuntime
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


@dataclass(frozen=True, slots=True)
class _PreparedTranslationLayoutRecord:
    """Private canonical storage that is never exposed through the capability."""

    reference: weakref.ReferenceType[_PreparedTranslationLayoutBatch]
    owner_pid: int
    runtime_id: int
    source_runtime_snapshot: M7SemanticRuntimeSnapshot
    source_bindings: _PreparedLayoutSourceBindings
    source_event_positions: _PreparedLayoutSourceEventPositions
    event_materials: _PreparedEventMaterials
    source_key_fingerprints: _PreparedLayoutKeyFingerprints
    source_mutation_guards: _PreparedSourceMutationGuards
    source_leases: dict[tuple[str, str], _PreparedLayoutSourceLease]
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


_PREPARED_TRANSLATION_LAYOUT_REGISTRY: dict[
    int,
    _PreparedTranslationLayoutRecord,
] = {}
_PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY: dict[
    int,
    _RegisteredPreparedLayoutSourceLease,
] = {}


def _prepared_standard_winner_payload(
    compiled: CompiledStandardWinner,
) -> dict[str, object]:
    return {
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
    }


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
        _validate_all_prepared_layout_source_bindings(registered, runtime)
        _validate_all_prepared_event_materials(registered, runtime)
        _validate_all_prepared_source_mutation_guards(registered)
        _require_prepared_semantic_runtime_source(
            runtime,
            expected_sha256=registered.source_runtime_snapshot.semantic_sha256,
        )
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

    leased, _material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    if leased.rejection_problem is None:
        raise ValueError("M8 prepared rejection problem is absent from the batch")
    return leased.rejection_problem


def _prepared_layout_footprints(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> tuple[PreparedLayoutFootprint, ...]:
    """Return exact prepared geometry in the live candidate order."""

    leased, _material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    layouts = leased.layout_footprints
    if tuple(layout.candidate_id for layout in layouts) != leased.source_binding.candidate_ids:
        raise ValueError("M8 prepared layout footprint candidate identities differ")
    return layouts


def _prepared_standard_winner(
    prepared: _PreparedTranslationLayoutBatch,
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> CompiledStandardWinner:
    """Return one registry-owned standard winner compiled once for the batch."""

    leased, _material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    return leased.standard_winner


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
    try:
        problem = next(
            problem
            for problem in runtime.replay_input.problems
            if problem.problem_id == binding.problem_id
        )
        verified = runtime.runtime_candidates[binding.problem_id]
    except (KeyError, StopIteration) as error:
        raise ValueError("M8 prepared layout footprint source geometry differs") from error
    return (
        (problem.problem_id, verified.evidence.candidate_set_id),
        binding,
        problem,
        verified,
    )


def _prepared_layout_source_binding(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
) -> _PreparedLayoutSourceBinding:
    _key, _binding, problem, verified = _prepared_key_and_inputs(
        runtime,
        event_position=event_position,
    )
    problem_digest = semantic_sha256(
        problem.model_dump(mode="json", warnings=False),
        excluded_fields={"problem_id", "content_sha256"},
    )
    candidate_set_digest = semantic_sha256(
        verified.evidence.model_dump(mode="json", warnings=False),
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
        candidate_ids=tuple(candidate.candidate_id for candidate in verified.candidates),
        candidate_sha256s=tuple(
            f"sha256:{semantic_sha256(candidate.model_dump(mode='json', warnings=False))}"
            for candidate in verified.candidates
        ),
        rejection_layout_candidate_ids=tuple(
            retained.candidate_id for retained in verified.rejection_layouts
        ),
        rejection_layout_sha256s=tuple(
            f"sha256:{semantic_sha256(asdict(retained))}" for retained in verified.rejection_layouts
        ),
        fit_config_sha256=(
            "sha256:"
            + semantic_sha256(
                runtime.replay_input.fit_config.model_dump(mode="json", warnings=False)
            )
        ),
    )


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
) -> bool:
    return (
        identity.runtime is runtime
        and identity.replay_input is runtime.replay_input
        and identity.instances is runtime.replay_input.instances
        and identity.problems is runtime.replay_input.problems
        and identity.problem.problem is identity.geometry_problem
        and identity.geometry_problem.parts is identity.problem_parts
        and identity.runtime_candidates is runtime.runtime_candidates
        and runtime.runtime_candidates.get(key[0]) is identity.verified
        and identity.verified.evidence is identity.evidence
        and identity.verified.candidates is identity.candidates
        and identity.verified.rejection_layouts is identity.rejection_layouts
        and identity.fit_config is runtime.replay_input.fit_config
    )


def _install_prepared_tracked_list(
    owner: object,
    *,
    field_name: str,
    guard: _PreparedSourceMutationGuard,
) -> _PreparedTrackedListBinding | None:
    value = getattr(owner, field_name)
    if value is None:
        return None
    if isinstance(value, _PreparedMutationTrackedList) or not isinstance(value, list):
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
    object.__setattr__(owner, field_name, tracked)
    if getattr(owner, field_name) is not tracked:
        raise ValueError("M8 prepared source nested-list guard cannot be installed")
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
    except (AttributeError, TypeError, ValueError):
        _restore_prepared_source_mutation_guards(
            guards=(guard,),
            bindings=tuple(bindings),
        )
        raise
    return guard, tuple(bindings)


def _restore_prepared_source_mutation_guards(
    *,
    guards: tuple[_PreparedSourceMutationGuard, ...],
    bindings: tuple[_PreparedTrackedListBinding, ...],
) -> None:
    """Restore exact caller-owned lists after one proof batch."""

    integrity_differs = False
    for binding in reversed(bindings):
        current = getattr(binding.owner, binding.field_name, None)
        guard_is_registered = any(binding.guard is guard for guard in guards)
        current_is_tracked = current is binding.tracked
        if not current_is_tracked or not guard_is_registered:
            integrity_differs = True
        if list.__eq__(binding.tracked, binding.canonical) is not True:
            integrity_differs = True
        if binding.original != list(binding.original_values):
            integrity_differs = True
        binding.original[:] = binding.original_values
        object.__setattr__(binding.owner, binding.field_name, binding.original)
        if getattr(binding.owner, binding.field_name, None) is not binding.original:
            integrity_differs = True
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
            if list.__eq__(tracked, canonical) is not True:
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
        if current_key != key or binding.material != material:
            raise ValueError("M8 prepared event source snapshot differs")


def _prepared_event_material(
    registered: _RegisteredPreparedLayoutSourceLease,
    *,
    event_position: int,
) -> MaterialIdentity:
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
    registered = _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY.get(id(lease))
    if (
        type(lease) is not _PreparedLayoutSourceLease
        or registered is None
        or registered.reference() is not lease
        or registered.owner_pid != os.getpid()
        or registered.token is not lease._token  # noqa: SLF001
        or registered.prepared is not prepared
        or registered.runtime is not runtime
        or registered.key != key
        or event_position < 0
        or event_position >= len(registered.event_bindings)
        or event_position >= len(runtime.replay_input.instances)
        or registered.event_bindings[event_position]
        is not runtime.replay_input.instances[event_position]
    ):
        raise ValueError("M8 prepared layout source lease is invalid or inactive")
    _require_prepared_source_mutation_guard_unchanged(
        registered.mutation_guard,
        expected_version=registered.mutation_version,
    )
    if not _prepared_layout_live_source_identity_is_current(
        registered.source_identity,
        runtime,
        key=key,
    ):
        raise ValueError("M8 prepared layout source lease is invalid or inactive")
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

    batch = _require_prepared_translation_layout_record(prepared, runtime)
    if event_position < 0 or event_position >= len(batch.event_materials):
        raise ValueError("M8 prepared event source snapshot differs")
    event_source = batch.event_materials[event_position]
    if event_source is None:
        raise ValueError("M8 prepared event source snapshot differs")
    key, _proof_material = event_source
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
                or candidate_binding.material != candidate_material
            ):
                raise ValueError("M8 prepared event source snapshot differs")
            event_material_slots[candidate_position] = candidate_material
            event_binding_slots[candidate_position] = candidate_binding
    event_materials = tuple(event_material_slots)
    event_bindings = tuple(event_binding_slots)
    material = event_materials[event_position]
    if material is None or binding.material != material:
        raise ValueError("M8 prepared event source snapshot differs")

    token = object()
    lease = _PreparedLayoutSourceLease(token)
    lease_key = id(lease)

    def discard(reference: weakref.ReferenceType[_PreparedLayoutSourceLease]) -> None:
        leased = _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY.get(lease_key)
        if leased is not None and leased.reference is reference:
            _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY.pop(lease_key, None)

    reference = weakref.ref(lease, discard)
    registered = _RegisteredPreparedLayoutSourceLease(
        reference=reference,
        owner_pid=os.getpid(),
        token=token,
        prepared=prepared,
        runtime=runtime,
        operational_runtime=batch.source_runtime_snapshot.runtime,
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
    _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY[lease_key] = registered
    batch.source_leases[key] = lease
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
    return registered.operational_runtime


def _release_prepared_layout_source_leases(
    prepared: _PreparedTranslationLayoutBatch,
    registered: _PreparedTranslationLayoutRecord,
) -> None:
    expected = {id(lease): (key, lease) for key, lease in registered.source_leases.items()}
    actual = {
        lease_id: leased
        for lease_id, leased in _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY.items()
        if leased.prepared is prepared
    }
    integrity_differs = set(expected) != set(actual)
    for lease_id, leased in actual.items():
        key_and_lease = expected.get(lease_id)
        if key_and_lease is None:
            integrity_differs = True
        else:
            key, lease = key_and_lease
            if (
                leased.reference() is not lease
                or leased.owner_pid != os.getpid()
                or leased.token is not lease._token  # noqa: SLF001
                or leased.prepared is not prepared
                or leased.key != key
            ):
                integrity_differs = True
        _PREPARED_LAYOUT_SOURCE_LEASE_REGISTRY.pop(lease_id, None)
    registered.source_leases.clear()
    if integrity_differs:
        raise ValueError("M8 prepared layout source lease cleanup differs")


def _prepared_cleanup_integrity_error(
    error: Exception,
    *,
    fallback: str,
) -> ValueError:
    """Normalize malformed live-source failures without skipping cleanup."""

    if isinstance(error, ValueError):
        return error
    return ValueError(fallback)


def _snapshot_prepared_source_runtime(
    runtime: M7ReplayRuntime,
) -> M7SemanticRuntimeSnapshot:
    """Deep-capture a source without duplicating an outer proof's Jagua lease."""

    if not isinstance(runtime.runtime_candidates, MappingProxyType):
        return snapshot_m7_replay_runtime(runtime)

    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            before_sha256 = m7_semantic_runtime_sha256(runtime)
            snapshot_runtime = M7ReplayRuntime(
                replay_input=deepcopy(runtime.replay_input),
                runtime_candidates=MappingProxyType(deepcopy(dict(runtime.runtime_candidates))),
                rules=deepcopy(runtime.rules),
                standard_profile_cache=deepcopy(runtime.standard_profile_cache),
                fit_search_cache=deepcopy(runtime.fit_search_cache),
                shared_fit_search_cache=deepcopy(runtime.shared_fit_search_cache),
                prepared_layout_cache=deepcopy(runtime.prepared_layout_cache),
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

    if event_positions != tuple(sorted(set(event_positions))):
        raise ValueError("M8 prepared translation event positions must be sorted unique")
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
        runtime.replay_input.instances
    )
    rejection_by_key: dict[tuple[str, str], CompiledRejectionProblem] = {}
    standard_by_key: dict[tuple[str, str], CompiledStandardWinner] = {}
    with profile_phase("standard_layout_materialization"):
        for event_position in event_positions:
            key, binding, problem, verified = _prepared_key_and_inputs(
                runtime,
                event_position=event_position,
            )
            event_material_slots[event_position] = (key, deepcopy(binding.material))
            if key not in layouts_by_key:
                source_event_positions_by_key[key] = event_position
                sources_by_key[key] = _prepared_layout_source_binding(
                    runtime,
                    event_position=event_position,
                )
                standard_by_key[key] = deepcopy(
                    compile_standard_winner(
                        runtime,
                        event_position=event_position,
                    )
                )
                footprints = tuple(
                    prepare_layout_footprint(
                        problem.problem,
                        candidate,
                        runtime.replay_input.fit_config,
                    )
                    for candidate in verified.candidates
                )
                footprints_by_key[key] = footprints
                layouts_by_key[key] = tuple(
                    prepare_translation_rejection_layout(layout) for layout in footprints
                )
                if _verified_rejection_layouts_cover_candidates(verified):
                    rejection_by_key[key] = compile_rejection_problem(
                        runtime,
                        event_position=event_position,
                    )
    source_bindings = tuple(sorted(sources_by_key.items()))
    source_event_positions = tuple(sorted(source_event_positions_by_key.items()))
    event_materials = tuple(event_material_slots)
    layout_footprints = tuple(sorted(footprints_by_key.items()))
    layouts = tuple(sorted(layouts_by_key.items()))
    rejection_problems = tuple(sorted(rejection_by_key.items()))
    standard_winners = tuple(sorted(standard_by_key.items()))
    prepared = _PreparedTranslationLayoutBatch(_runtime_id=id(runtime))
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
    source_runtime_snapshot = _snapshot_prepared_source_runtime(runtime)
    try:
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
        registered = _PREPARED_TRANSLATION_LAYOUT_REGISTRY.get(key)
        if registered is not None and registered.reference is reference:
            _PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(key, None)

    reference = weakref.ref(prepared, discard)
    _PREPARED_TRANSLATION_LAYOUT_REGISTRY[key] = _PreparedTranslationLayoutRecord(
        reference=reference,
        owner_pid=os.getpid(),
        runtime_id=id(runtime),
        source_runtime_snapshot=source_runtime_snapshot,
        source_bindings=source_bindings,
        source_event_positions=source_event_positions,
        event_materials=event_materials,
        source_key_fingerprints=source_key_fingerprints,
        source_mutation_guards=source_mutation_guards,
        source_leases={},
        layout_footprints=layout_footprints,
        layouts=layouts,
        rejection_problems=rejection_problems,
        standard_winners=standard_winners,
        remnant_measurements={},
        remnant_commitments={},
        remnant_snapshots={},
        layout_fingerprint=layout_fingerprint,
    )
    try:
        yield prepared
    finally:
        integrity_error = None
        try:
            prepared.require_active(runtime, deep=True)
        except Exception as error:  # noqa: BLE001 - cleanup must survive malformed live roots
            integrity_error = _prepared_cleanup_integrity_error(
                error,
                fallback="M8 prepared layout footprint source geometry differs",
            )
        registered = _PREPARED_TRANSLATION_LAYOUT_REGISTRY.get(key)
        if registered is not None and registered.reference() is prepared:
            try:
                _release_prepared_layout_source_leases(prepared, registered)
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
        if registered is not None and registered.reference() is prepared:
            _PREPARED_TRANSLATION_LAYOUT_REGISTRY.pop(key, None)
        try:
            source_runtime_snapshot.close()
        except Exception as error:  # noqa: BLE001 - preserve the prior integrity failure
            if integrity_error is None:
                integrity_error = _prepared_cleanup_integrity_error(
                    error,
                    fallback="M8 prepared semantic runtime snapshot cleanup differs",
                )
        if integrity_error is not None:
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

    leased, material = _prepared_layout_source_lease_and_inputs(
        prepared,
        runtime,
        event_position=event_position,
    )
    remnant = _registered_prepared_remnant_measurement(
        prepared,
        runtime,
        item.remnant,
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
                RejectionScalar.from_verified(retained) for retained in verified.rejection_layouts
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
