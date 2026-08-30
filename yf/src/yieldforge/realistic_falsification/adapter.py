"""Authenticated, runtime-only projection from M11 chronology into the M7 engine.

The native M7 contracts predate M11 and therefore cannot truthfully be persisted as
new M7 evidence.  This module builds private, content-addressed compatibility DTOs
solely to reuse the exact M7 action, geometry, ledger, and replay implementation.
Every public projection retains an explicit M11 attestation and forbids native-M7
evidence persistence.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, StrictFloat, StrictInt, StrictStr, model_validator

from yieldforge.baseline.archives import (
    VerifiedProblemCandidates,
    build_verified_candidate_rejection_layout,
)
from yieldforge.baseline.contracts import (
    M7CandidateArchiveEvidence,
    M7CandidateSetEvidence,
    ReusableGeometryProblem,
    TemporalInstanceBinding,
)
from yieldforge.baseline.geometry import prepare_layout_footprint
from yieldforge.baseline.policies import M7PolicyName, policy_identity
from yieldforge.baseline.replay import (
    M7ReplayRuntime,
    build_m7_replay_input,
    m7_semantic_runtime_sha256,
)
from yieldforge.domain import (
    Candidate,
    ProjectionMode,
    SolverProjectionBinding,
    StripPackingProblem,
)
from yieldforge.experiments.contracts import FrozenExperimentModel, semantic_sha256
from yieldforge.realistic_falsification.evaluate import (
    Gate1EvaluationResult,
    authenticate_official_gate1_evaluation,
)
from yieldforge.realistic_falsification.geometry_gate import (
    Gate2EvaluationResult,
    _load_official_gate2_context,
    _OfficialGate2Context,
    _pinned_jagua_executable_is_usable,
    _reconstruct_official_payload,
    authenticate_official_gate2_evaluation,
)
from yieldforge.realistic_falsification.pack import (
    M11EconomicProfile,
    M11Event,
    M11Payload,
    M11Population,
    M11Stream,
    visible_event_positions,
)
from yieldforge.reuse.contracts import MaterialIdentity, MaterialProvenance
from yieldforge.temporal_benchmark.contracts import (
    CandidateArchiveRequirement,
    FeasibilityRateManifest,
    TemporalPartition,
    TemporalRegime,
)

EconomicArm = Literal["central", "adverse"]
RegistrationKind = Literal[
    "calibration",
    "confirmation",
    "shuffled_twin",
    "hard_null",
    "exact_audit",
]
ControlKind = Literal[
    "single_action",
    "unique_materials_single_action",
    "all_work_known_single_action",
]


class AdapterEvidenceError(ValueError):
    """M11 evidence could not be projected without weakening its source binding."""


def _timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise AdapterEvidenceError("M11 adapter received a noncanonical timestamp") from error


def _identity(prefix: str, semantic: dict[str, object]) -> tuple[str, str]:
    digest = semantic_sha256(semantic)
    return f"{prefix}{digest[:24]}", f"sha256:{digest}"


def _material(key: str) -> MaterialIdentity:
    if not key or key.strip() != key:
        raise AdapterEvidenceError("M11 adapter material key is not canonical")
    return MaterialIdentity(
        material_code=key,
        grade=key,
        thickness=key,
        surface=key,
        grain=key,
        provenance=MaterialProvenance.ASSUMED,
    )


class M11SourceEventMap(FrozenExperimentModel):
    """One local M7 compatibility event mapped back to immutable M11 chronology."""

    local_event_position: StrictInt = Field(ge=0)
    compatibility_event_id: StrictStr = Field(pattern=r"^yfte-[0-9a-f]{20}$")
    source_event_position: StrictInt = Field(ge=0, le=23)
    source_event_id: StrictStr = Field(pattern=r"^yfm11e-[0-9a-f]{24}$")
    source_event_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_material_key: StrictStr = Field(min_length=1)
    projected_material_key: StrictStr = Field(min_length=1)
    payload_id: StrictStr = Field(pattern=r"^yfm11pl-[0-9a-f]{24}$")
    released_at: StrictStr = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class M11KnownVisibleLocalPrefix(FrozenExperimentModel):
    """M11 firm-known work filtered to one independent material runtime."""

    local_event_position: StrictInt = Field(ge=0)
    source_as_of_event_position: StrictInt = Field(ge=0, le=23)
    source_as_of_event_id: StrictStr = Field(pattern=r"^yfm11e-[0-9a-f]{24}$")
    visible_source_event_positions: tuple[StrictInt, ...]
    visible_local_event_positions: tuple[StrictInt, ...]
    visibility_rule: Literal[
        "m11_known_at_filtered_to_registered_slice_and_material",
        "hard_null_all_registered_work_known",
    ]

    @model_validator(mode="after")
    def require_canonical_prefixes(self) -> Self:
        for values in (
            self.visible_source_event_positions,
            self.visible_local_event_positions,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("M11 adapter visibility positions must be sorted and unique")
        if self.local_event_position not in self.visible_local_event_positions:
            raise ValueError("M11 adapter visibility must include the current local event")
        return self


class M11CandidateActionParity(FrozenExperimentModel):
    """Exact candidate and standard-action census for one projected event."""

    local_event_position: StrictInt = Field(ge=0)
    source_event_id: StrictStr = Field(pattern=r"^yfm11e-[0-9a-f]{24}$")
    payload_id: StrictStr = Field(pattern=r"^yfm11pl-[0-9a-f]{24}$")
    runtime_problem_id: StrictStr = Field(pattern=r"^yfm7p-[0-9a-f]{24}$")
    source_candidate_ids: tuple[StrictStr, ...] = Field(min_length=1)
    projected_candidate_ids: tuple[StrictStr, ...] = Field(min_length=1)
    runtime_candidate_ids: tuple[StrictStr, ...] = Field(min_length=1)
    source_binding_sha256s: tuple[StrictStr, ...] = Field(min_length=1)
    standard_action_ids: tuple[StrictStr, ...] = Field(min_length=1)
    projection_rule: Literal["all_registered_candidates", "hard_null_single_action"]

    @model_validator(mode="after")
    def require_exact_parity(self) -> Self:
        if len(self.source_candidate_ids) != len(set(self.source_candidate_ids)):
            raise ValueError("M11 adapter source candidates repeat")
        if self.runtime_candidate_ids != tuple(sorted(set(self.runtime_candidate_ids))):
            raise ValueError("M11 adapter runtime candidates must use canonical M7 order")
        if set(self.projected_candidate_ids) != set(self.runtime_candidate_ids):
            raise ValueError("M11 adapter projected and runtime candidates differ")
        if not set(self.projected_candidate_ids).issubset(self.source_candidate_ids):
            raise ValueError("M11 adapter projected a candidate absent from the source payload")
        if len(self.source_binding_sha256s) != len(self.projected_candidate_ids):
            raise ValueError("M11 adapter candidate source bindings do not reconcile")
        if any(
            len(value) != 71
            or not value.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in value[7:])
            for value in self.source_binding_sha256s
        ):
            raise ValueError("M11 adapter candidate source binding is not a SHA-256 identity")
        if self.standard_action_ids != tuple(
            f"m7-standard:{candidate_id}" for candidate_id in self.runtime_candidate_ids
        ):
            raise ValueError("M11 adapter standard actions differ from runtime candidates")
        if self.projection_rule == "all_registered_candidates":
            if self.projected_candidate_ids != self.source_candidate_ids:
                raise ValueError("M11 adapter all-candidate projection omitted a source option")
        elif len(self.projected_candidate_ids) != 1:
            raise ValueError("M11 hard-null compatibility projection requires one action")
        return self


class M11M7ProjectionAttestation(FrozenExperimentModel):
    """Content-addressed boundary around one private M11-to-M7 runtime DTO."""

    schema_version: Literal["yieldforge.m11-m7-runtime-attestation.v1"] = (
        "yieldforge.m11-m7-runtime-attestation.v1"
    )
    attestation_id: StrictStr = Field(pattern=r"^yfm11m7a-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate1_result_id: StrictStr = Field(pattern=r"^yfm11g1r-[0-9a-f]{24}$")
    gate1_result_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate2_result_id: StrictStr = Field(pattern=r"^yfm11g2r-[0-9a-f]{24}$")
    gate2_result_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    population_id: StrictStr = Field(pattern=r"^yfm11pop-[0-9a-f]{24}$")
    population_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    registration_kind: RegistrationKind
    control_kind: ControlKind | None
    registered_exact_audit_arm: Literal["central", "adverse", "null"] | None
    source_registration_id: StrictStr = Field(min_length=1)
    source_registration_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_stream_id: StrictStr = Field(pattern=r"^yfm11st-[0-9a-f]{24}$")
    source_stream_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: Literal["lectra-m3-m4", "loco-2dics"]
    economic_arm: EconomicArm
    material_key: StrictStr = Field(min_length=1)
    reference_area_key: StrictStr = Field(min_length=1)
    reference_area: StrictFloat = Field(gt=0)
    rates: FeasibilityRateManifest
    source_event_map: tuple[M11SourceEventMap, ...] = Field(min_length=1)
    known_visible_local_prefixes: tuple[M11KnownVisibleLocalPrefix, ...] = Field(min_length=1)
    candidate_action_parity: tuple[M11CandidateActionParity, ...] = Field(min_length=1)
    m7_replay_input_id: StrictStr = Field(pattern=r"^yfm7ri-[0-9a-f]{24}$")
    m7_replay_input_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    m7_runtime_semantic_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    collision_backend: Literal[
        "shapely_authoritative",
        "jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
    ]
    ledger_scope: Literal["single_material_independent_substream"] = (
        "single_material_independent_substream"
    )
    compatibility_dto_only: Literal[True] = True
    native_m7_evidence_persistence_authorized: Literal[False] = False

    @model_validator(mode="after")
    def require_identity_rates_and_alignment(self) -> Self:
        count = len(self.source_event_map)
        if tuple(item.local_event_position for item in self.source_event_map) != tuple(
            range(count)
        ):
            raise ValueError("M11 adapter source-event map must be locally contiguous")
        if tuple(item.local_event_position for item in self.known_visible_local_prefixes) != tuple(
            range(count)
        ):
            raise ValueError("M11 adapter visibility evidence must be event-aligned")
        if tuple(item.local_event_position for item in self.candidate_action_parity) != tuple(
            range(count)
        ):
            raise ValueError("M11 adapter candidate parity must be event-aligned")
        if any(item.projected_material_key != self.material_key for item in self.source_event_map):
            raise ValueError("M11 adapter material substream crosses material identities")
        expected = {
            "central": (100.0, 10.0, 0.25, 0.25, 0.5),
            "adverse": (100.0, 25.0, 1.0, 1.0, 2.0),
        }[self.economic_arm]
        purchase, scrap, returned, retrieved, storage_30d = expected
        expected_rates = FeasibilityRateManifest(
            purchase_cost_per_area=purchase / self.reference_area,
            storage_cost_per_area_hour=storage_30d / (self.reference_area * 30.0 * 24.0),
            return_handling_cost_per_remnant=returned,
            retrieval_handling_cost_per_remnant=retrieved,
            scrap_credit_per_area=scrap / self.reference_area,
        )
        if self.rates != expected_rates:
            raise ValueError("M11 adapter rates differ from the frozen M11 profile conversion")
        if self.registration_kind == "hard_null":
            if self.control_kind is None or self.registered_exact_audit_arm is not None:
                raise ValueError("M11 hard-null adapter metadata is incomplete")
        elif self.registration_kind == "exact_audit":
            if self.control_kind is not None or self.registered_exact_audit_arm is None:
                raise ValueError("M11 exact-audit adapter metadata is incomplete")
        elif self.control_kind is not None or self.registered_exact_audit_arm is not None:
            raise ValueError("ordinary M11 adapter projection carries control metadata")
        digest = semantic_sha256(self, excluded_fields={"attestation_id", "content_sha256"})
        if self.attestation_id != f"yfm11m7a-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("M11 adapter attestation identity differs from semantic content")
        return self


@dataclass(frozen=True, slots=True)
class M11MaterialRuntimeProjection:
    """One material-isolated runtime plus its non-native compatibility attestation."""

    attestation: M11M7ProjectionAttestation
    runtime: M7ReplayRuntime

    def __post_init__(self) -> None:
        replay = self.runtime.replay_input
        if (
            replay.input_id != self.attestation.m7_replay_input_id
            or replay.content_sha256 != self.attestation.m7_replay_input_content_sha256
            or replay.rates != self.attestation.rates
            or replay.collision_backend != self.attestation.collision_backend
            or m7_semantic_runtime_sha256(self.runtime)
            != self.attestation.m7_runtime_semantic_sha256
        ):
            raise AdapterEvidenceError("M11 adapter runtime differs from its attestation")

    @property
    def material_key(self) -> str:
        return self.attestation.material_key

    @property
    def reference_area(self) -> float:
        return self.attestation.reference_area

    @property
    def rates(self) -> FeasibilityRateManifest:
        return self.attestation.rates

    @property
    def source_event_map(self) -> tuple[M11SourceEventMap, ...]:
        return self.attestation.source_event_map

    @property
    def known_visible_local_prefixes(self) -> tuple[M11KnownVisibleLocalPrefix, ...]:
        return self.attestation.known_visible_local_prefixes

    @property
    def candidate_action_parity(self) -> tuple[M11CandidateActionParity, ...]:
        return self.attestation.candidate_action_parity


@dataclass(frozen=True, slots=True, weakref_slot=True)
class M11M7AdapterContext:
    """Authenticated roots and private geometry authority for runtime projection."""

    repository_root: Path
    gate1_result: Gate1EvaluationResult
    gate2_result: Gate2EvaluationResult
    geometry_context: _OfficialGate2Context

    @property
    def population(self) -> M11Population:
        return self.geometry_context.gate1.bundle.population


_AUTHENTICATED_CONTEXTS: dict[
    int,
    tuple[weakref.ReferenceType[M11M7AdapterContext], str],
] = {}


def _context_fingerprint(context: M11M7AdapterContext) -> str:
    """Bind the process-local capability to the exact authenticated object graph."""

    geometry = context.geometry_context
    return f"sha256:{
        semantic_sha256(
            {
                'repository_root': str(context.repository_root),
                'gate1_object_id': id(context.gate1_result),
                'gate1_result_id': context.gate1_result.result_id,
                'gate1_result_content_sha256': context.gate1_result.content_sha256,
                'gate2_object_id': id(context.gate2_result),
                'gate2_result_id': context.gate2_result.result_id,
                'gate2_result_content_sha256': context.gate2_result.content_sha256,
                'geometry_context_object_id': id(geometry),
                'population_object_id': id(geometry.gate1.bundle.population),
                'population_id': geometry.gate1.bundle.population.population_id,
                'population_content_sha256': geometry.gate1.bundle.population.content_sha256,
                'm0_contract_id': geometry.m0.contract_id,
                'm0_contract_content_sha256': geometry.m0.content_sha256,
                'm3_input_id': geometry.gate1.m3_input.input_id,
                'm3_input_content_sha256': geometry.gate1.m3_input.content_sha256,
                'm4_input_id': geometry.m4.input_id,
                'm4_input_content_sha256': geometry.m4.content_sha256,
                'loco_catalog_id': geometry.gate1.loco_catalog.catalog_id,
                'loco_catalog_content_sha256': geometry.gate1.loco_catalog.content_sha256,
            }
        )
    }"


def _register_authenticated_context(context: M11M7AdapterContext) -> None:
    key = id(context)

    def discard(reference: weakref.ReferenceType[M11M7AdapterContext]) -> None:
        registered = _AUTHENTICATED_CONTEXTS.get(key)
        if registered is not None and registered[0] is reference:
            _AUTHENTICATED_CONTEXTS.pop(key, None)

    _AUTHENTICATED_CONTEXTS[key] = (
        weakref.ref(context, discard),
        _context_fingerprint(context),
    )


@dataclass(frozen=True, slots=True)
class _SelectedEvent:
    event: M11Event
    projected_material_key: str
    reference_area_key: str
    selected_candidate_ids: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class _Registration:
    registration_kind: RegistrationKind
    control_kind: ControlKind | None
    registered_exact_audit_arm: Literal["central", "adverse", "null"] | None
    registration_id: str
    registration_content_sha256: str
    source_stream: M11Stream
    selected_events: tuple[_SelectedEvent, ...]
    all_work_known: bool = False


def _require_context_roots(
    *,
    repository_root: Path,
    geometry_context: _OfficialGate2Context,
    gate1_result: Gate1EvaluationResult,
    gate2_result: Gate2EvaluationResult,
) -> None:
    population = geometry_context.gate1.bundle.population
    source_manifest = geometry_context.gate1.source_manifest
    gate1_roots = (
        gate1_result.population_id,
        gate1_result.population_content_sha256,
        gate1_result.source_manifest_id,
        gate1_result.source_manifest_content_sha256,
    )
    source_roots = (
        population.population_id,
        population.content_sha256,
        source_manifest.source_manifest_id,
        source_manifest.content_sha256,
    )
    gate2_roots = (
        gate2_result.population_id,
        gate2_result.population_content_sha256,
        gate2_result.source_manifest_id,
        gate2_result.source_manifest_content_sha256,
    )
    geometry_leaves = (
        geometry_context.m0.contract_id,
        geometry_context.m0.content_sha256,
        geometry_context.gate1.m3_input.input_id,
        geometry_context.gate1.m3_input.content_sha256,
        geometry_context.m4.input_id,
        geometry_context.m4.content_sha256,
        geometry_context.gate1.loco_catalog.catalog_id,
        geometry_context.gate1.loco_catalog.content_sha256,
    )
    gate2_leaves = (
        gate2_result.m0_contract_id,
        gate2_result.m0_contract_content_sha256,
        gate2_result.m3_input_id,
        gate2_result.m3_input_content_sha256,
        gate2_result.m4_input_id,
        gate2_result.m4_input_content_sha256,
        gate2_result.loco_catalog_id,
        gate2_result.loco_catalog_content_sha256,
    )
    if (
        Path(repository_root).resolve() != geometry_context.gate1.repository_root.resolve()
        or gate1_roots != source_roots
        or gate2_roots != source_roots
        or gate2_leaves != geometry_leaves
        or gate2_result.gate1_result_id != gate1_result.result_id
        or gate2_result.gate1_result_content_sha256 != gate1_result.content_sha256
    ):
        raise AdapterEvidenceError("M11 adapter authenticated root bindings do not reconcile")
    if (
        gate1_result.status != "gate_1_survived"
        or not gate1_result.opens_gate_2
        or gate2_result.status != "gate_2_survived"
        or gate2_result.evaluation_stage != "stage_b_exact_attempted"
        or not gate2_result.opens_gate_3
    ):
        raise AdapterEvidenceError("M11 adapter requires the valid Gate 2-to-Gate 3 branch")


def _adapter_context_from_authenticated(
    *,
    repository_root: Path,
    geometry_context: _OfficialGate2Context,
    gate1_result: Gate1EvaluationResult,
    gate2_result: Gate2EvaluationResult,
) -> M11M7AdapterContext:
    """Bind already-authenticated roots; private seam used by focused reconstruction tests."""

    _require_context_roots(
        repository_root=repository_root,
        geometry_context=geometry_context,
        gate1_result=gate1_result,
        gate2_result=gate2_result,
    )
    context = M11M7AdapterContext(
        repository_root=Path(repository_root).resolve(),
        gate1_result=gate1_result,
        gate2_result=gate2_result,
        geometry_context=geometry_context,
    )
    _register_authenticated_context(context)
    return context


def load_authenticated_adapter_context(
    root: Path,
    gate1_result: Gate1EvaluationResult,
    gate2_result: Gate2EvaluationResult,
) -> M11M7AdapterContext:
    """Authenticate both official gate roots before issuing any compatibility runtime."""

    repository_root = Path(root).resolve()
    try:
        canonical_gate1 = authenticate_official_gate1_evaluation(
            gate1_result,
            repository_root=repository_root,
        )
    except (TypeError, ValueError) as error:
        raise AdapterEvidenceError("M11 adapter Gate 1 authentication failed") from error
    try:
        canonical_gate2 = authenticate_official_gate2_evaluation(
            gate2_result,
            repository_root=repository_root,
            gate1_result=canonical_gate1,
        )
    except (TypeError, ValueError) as error:
        raise AdapterEvidenceError("M11 adapter Gate 2 authentication failed") from error
    try:
        geometry_context = _load_official_gate2_context(repository_root)
    except (OSError, TypeError, ValueError) as error:
        raise AdapterEvidenceError("M11 adapter source geometry authentication failed") from error
    return _adapter_context_from_authenticated(
        repository_root=repository_root,
        geometry_context=geometry_context,
        gate1_result=canonical_gate1,
        gate2_result=canonical_gate2,
    )


def _require_live_context(context: M11M7AdapterContext) -> None:
    registered = _AUTHENTICATED_CONTEXTS.get(id(context))
    if (
        registered is None
        or registered[0]() is not context
        or registered[1] != _context_fingerprint(context)
    ):
        raise AdapterEvidenceError("M11 adapter context lacks authenticated authority")
    try:
        _require_context_roots(
            repository_root=context.repository_root,
            geometry_context=context.geometry_context,
            gate1_result=context.gate1_result,
            gate2_result=context.gate2_result,
        )
    except (AttributeError, TypeError, ValueError) as error:
        if isinstance(error, AdapterEvidenceError):
            raise
        raise AdapterEvidenceError("M11 adapter context is invalid") from error


def _population_maps(
    population: M11Population,
) -> tuple[dict[str, M11Stream], dict[str, M11Payload]]:
    return (
        {item.stream_id: item for item in population.streams},
        {item.payload_id: item for item in population.payloads},
    )


def _reference_area_key_for_event(
    population: M11Population,
    stream: M11Stream,
    event: M11Event,
) -> str:
    registry = next(
        item for item in population.reference_areas if item.corpus_id == stream.corpus_id
    )
    direct = dict(registry.by_material)
    if event.material_key in direct:
        return event.material_key
    if stream.stream_kind != "shuffled_twin" or not event.payload_source_event_id:
        raise AdapterEvidenceError("M11 adapter material has no authenticated reference area")
    streams, _payloads = _population_maps(population)
    source = streams.get(stream.source_stream_id or "")
    if source is None:
        raise AdapterEvidenceError("M11 twin source stream is absent")
    source_event = next(
        (item for item in source.events if item.event_id == event.payload_source_event_id),
        None,
    )
    if source_event is None or source_event.material_key not in direct:
        raise AdapterEvidenceError("M11 twin reference-area lineage is incomplete")
    return source_event.material_key


def _reference_area(
    population: M11Population,
    *,
    corpus_id: str,
    reference_area_key: str,
) -> float:
    try:
        registry = next(item for item in population.reference_areas if item.corpus_id == corpus_id)
        value = dict(registry.by_material)[reference_area_key]
    except (KeyError, StopIteration) as error:
        raise AdapterEvidenceError("M11 adapter reference area is absent") from error
    if value <= 0:
        raise AdapterEvidenceError("M11 adapter reference area is not positive")
    return float(value)


def _registration_for_stream(context: M11M7AdapterContext, stream_id: str) -> _Registration:
    streams, _payloads = _population_maps(context.population)
    stream = streams.get(stream_id)
    if stream is None:
        raise AdapterEvidenceError("M11 adapter stream is not registered")
    if stream.stream_kind == "shuffled_twin":
        kind: RegistrationKind = "shuffled_twin"
    else:
        kind = stream.partition
    selected = tuple(
        _SelectedEvent(
            event=item,
            projected_material_key=item.material_key,
            reference_area_key=_reference_area_key_for_event(context.population, stream, item),
        )
        for item in stream.events
    )
    return _Registration(
        registration_kind=kind,
        control_kind=None,
        registered_exact_audit_arm=None,
        registration_id=stream.stream_id,
        registration_content_sha256=stream.content_sha256,
        source_stream=stream,
        selected_events=selected,
    )


def _gate1_selected_candidate(
    context: M11M7AdapterContext,
    *,
    stream_id: str,
    event: M11Event,
    payload: M11Payload,
) -> str:
    receipt = context.gate1_result.audit_receipt
    if receipt is None:
        raise AdapterEvidenceError("M11 hard-null selection requires Gate 1 receipt evidence")
    cell = next((item for item in receipt.confirmation_cells if item.stream_id == stream_id), None)
    if cell is None:
        raise AdapterEvidenceError("M11 hard-null source stream is absent from Gate 1")
    opening = next(
        (item for item in cell.baseline.openings if item.event_id == event.event_id),
        None,
    )
    source_ids = tuple(item.candidate_id for item in payload.candidate_references)
    if (
        opening is None
        or opening.payload_id != payload.payload_id
        or opening.selected_candidate_id not in source_ids
    ):
        raise AdapterEvidenceError("M11 hard-null Gate 1 action binding does not reconcile")
    return opening.selected_candidate_id


def _registration_for_hard_null(
    context: M11M7AdapterContext,
    null_id: str,
) -> _Registration:
    population = context.population
    control = next((item for item in population.hard_nulls if item.null_id == null_id), None)
    if control is None:
        raise AdapterEvidenceError("M11 hard-null control is not registered")
    streams, payloads = _population_maps(population)
    source = streams.get(control.source_stream_id)
    if source is None:
        raise AdapterEvidenceError("M11 hard-null source stream is absent")
    by_id = {item.event_id: item for item in source.events}
    selected: list[_SelectedEvent] = []
    for ordinal, event_id in enumerate(control.event_ids):
        event = by_id.get(event_id)
        if event is None:
            raise AdapterEvidenceError("M11 hard-null source event is absent")
        payload = payloads[event.payload_id]
        candidate_id = _gate1_selected_candidate(
            context,
            stream_id=source.stream_id,
            event=event,
            payload=payload,
        )
        material_key = (
            f"hard-null:{control.null_id}:{ordinal:02d}"
            if control.unique_material_per_event
            else event.material_key
        )
        selected.append(
            _SelectedEvent(
                event=event,
                projected_material_key=material_key,
                reference_area_key=_reference_area_key_for_event(population, source, event),
                selected_candidate_ids=(candidate_id,),
            )
        )
    return _Registration(
        registration_kind="hard_null",
        control_kind=control.null_kind,
        registered_exact_audit_arm=None,
        registration_id=control.null_id,
        registration_content_sha256=control.content_sha256,
        source_stream=source,
        selected_events=tuple(selected),
        all_work_known=control.all_work_known,
    )


def _registration_for_exact_audit(
    context: M11M7AdapterContext,
    audit_id: str,
) -> _Registration:
    population = context.population
    audit = next((item for item in population.exact_audits if item.audit_id == audit_id), None)
    if audit is None:
        raise AdapterEvidenceError("M11 exact-audit control is not registered")
    streams, _payloads = _population_maps(population)
    source = streams.get(audit.source_stream_id)
    if source is None:
        raise AdapterEvidenceError("M11 exact-audit source stream is absent")
    events = tuple(source.events[position] for position in audit.event_positions)
    if tuple(item.event_id for item in events) != audit.event_ids:
        raise AdapterEvidenceError("M11 exact-audit event slice differs from its registry")
    return _Registration(
        registration_kind="exact_audit",
        control_kind=None,
        registered_exact_audit_arm=audit.economic_arm,
        registration_id=audit.audit_id,
        registration_content_sha256=audit.content_sha256,
        source_stream=source,
        selected_events=tuple(
            _SelectedEvent(
                event=item,
                projected_material_key=item.material_key,
                reference_area_key=_reference_area_key_for_event(population, source, item),
            )
            for item in events
        ),
    )


def _rates(population: M11Population, arm: EconomicArm, reference_area: float):
    profile = next((item for item in population.economic_profiles if item.arm == arm), None)
    if profile is None:
        raise AdapterEvidenceError("M11 adapter economic arm is absent")
    return _rates_for_profile(profile, reference_area)


def _rates_for_profile(
    profile: M11EconomicProfile,
    reference_area: float,
) -> FeasibilityRateManifest:
    if reference_area <= 0:
        raise AdapterEvidenceError("M11 adapter reference area must be positive")
    return FeasibilityRateManifest(
        purchase_cost_per_area=profile.virgin_cost_per_reference_area / reference_area,
        storage_cost_per_area_hour=(
            profile.storage_per_reference_area_30_days / (reference_area * 30.0 * 24.0)
        ),
        return_handling_cost_per_remnant=profile.return_handling,
        retrieval_handling_cost_per_remnant=profile.retrieval_handling,
        scrap_credit_per_area=profile.scrap_and_terminal_credit / reference_area,
    )


def _regime(registration: _Registration) -> TemporalRegime:
    if registration.registration_kind in ("shuffled_twin", "hard_null"):
        return TemporalRegime.NO_SIGNAL
    return {
        "recurrent": TemporalRegime.EXACT_RECURRENCE,
        "mixed": TemporalRegime.FAMILY_SIMILARITY,
        "high_mix": TemporalRegime.HIGH_MIX,
        "regime_shift": TemporalRegime.REGIME_SHIFT,
    }[registration.source_stream.regime]


def _partition(registration: _Registration) -> TemporalPartition:
    if registration.registration_kind == "calibration":
        return TemporalPartition.CALIBRATION
    return TemporalPartition.EVALUATION


def _compatibility_problem(
    context: M11M7AdapterContext,
    *,
    payload: M11Payload,
    problem: StripPackingProblem,
) -> ReusableGeometryProblem:
    if payload.source_kind == "lectra":
        try:
            tasks_index = int(payload.source_case_id.removeprefix("lectra-task:"))
        except ValueError as error:
            raise AdapterEvidenceError("M11 Lectra task identity is invalid") from error
        pair = next(
            (
                item
                for item in context.geometry_context.gate1.m3_input.task_pairs
                if item.tasks_index == tasks_index
            ),
            None,
        )
        projection = pair.source_task_binding.solver_projection if pair is not None else None
        if pair is None or projection is None or pair.problem != problem:
            raise AdapterEvidenceError("M11 Lectra projection does not rejoin M3")
        source_catalog_sha256 = (
            context.geometry_context.gate1.source_manifest.lectra.lectra_catalog_raw_sha256
        )
        compatibility_problem = problem
    else:
        task_digest = semantic_sha256(
            {
                "domain": "m11-loco-private-m7-compatibility-task",
                "payload_id": payload.payload_id,
                "payload_content_sha256": payload.content_sha256,
            }
        )
        tasks_index = int(task_digest[:12], 16)
        projection_digest = semantic_sha256(
            {
                "domain": "m11-loco-private-m7-compatibility-projection",
                "payload_id": payload.payload_id,
                "payload_content_sha256": payload.content_sha256,
                "geometry_references": [
                    item.model_dump(mode="json") for item in payload.geometry_references
                ],
                "fallback_stock": payload.fallback_stock.model_dump(mode="json")
                if payload.fallback_stock is not None
                else None,
            }
        )
        projection = SolverProjectionBinding(
            mode=ProjectionMode.SOURCE_AS_RECORDED,
            projection_sha256=projection_digest,
            assumption_codes=("m11_loco_bbox_shelf_compatibility_dto",),
            source_flip_part_count=0,
        )
        source_catalog_sha256 = (
            context.geometry_context.gate1.source_manifest.loco.catalog_raw_sha256
        )
        compatibility_problem = StripPackingProblem(
            name=f"lectra-task-{tasks_index}",
            strip_height=problem.strip_height,
            sheet_length=problem.sheet_length,
            parts=problem.parts,
        )
    semantic = {
        "schema_version": "yieldforge.m7-reusable-geometry-problem.v1",
        "source_catalog_sha256": source_catalog_sha256,
        "tasks_index": tasks_index,
        "sheet_type": 0,
        "projection": projection.model_dump(mode="json"),
        "problem": compatibility_problem.model_dump(mode="json"),
        "candidate_requirement": CandidateArchiveRequirement().model_dump(mode="json"),
        "claim_ceiling": (
            "reusable_source_geometry_and_solver_requirement_only_not_temporal_material_or_"
            "policy_evidence"
        ),
    }
    identifier, content = _identity("yfm7p-", semantic)
    return ReusableGeometryProblem(
        problem_id=identifier,
        content_sha256=content,
        source_catalog_sha256=source_catalog_sha256,
        tasks_index=tasks_index,
        sheet_type=0,
        projection=projection,
        problem=compatibility_problem,
        candidate_requirement=CandidateArchiveRequirement(),
    )


def _candidate_archives(
    context: M11M7AdapterContext,
    *,
    payload: M11Payload,
    candidate_count: int,
) -> tuple[
    M7CandidateArchiveEvidence,
    M7CandidateArchiveEvidence,
    M7CandidateArchiveEvidence,
    M7CandidateArchiveEvidence,
]:
    if payload.source_kind == "lectra":
        tasks_index = int(payload.source_case_id.removeprefix("lectra-task:"))
        pair = next(
            item
            for item in context.geometry_context.gate1.m3_input.task_pairs
            if item.tasks_index == tasks_index
        )
        values = tuple(
            M7CandidateArchiveEvidence(
                seed=item.seed,
                job_id=item.job_id,
                batch_sha256=item.batch_sha256,
                candidate_count=item.candidate_count,
                source_result_id=context.geometry_context.gate1.m3_input.m2_result_id,
                source_result_sha256=context.geometry_context.gate1.m3_input.m2_result_sha256,
            )
            for item in pair.archives
        )
        if len(values) != 4:
            raise AdapterEvidenceError("M11 Lectra candidate archives do not cover four seeds")
        return values  # type: ignore[return-value]
    values = []
    for seed in range(4):
        digest = semantic_sha256(
            {
                "domain": "m11-loco-private-m7-compatibility-candidate-archive",
                "payload_id": payload.payload_id,
                "payload_content_sha256": payload.content_sha256,
                "seed": seed,
                "candidate_count": candidate_count,
            }
        )
        values.append(
            M7CandidateArchiveEvidence(
                seed=seed,  # type: ignore[arg-type]
                job_id=f"m11-loco-compat-{payload.payload_id}-seed-{seed}",
                batch_sha256=digest,
                candidate_count=candidate_count,
                source_result_id=f"yfgcr-{digest[:24]}",
                source_result_sha256=f"sha256:{digest}",
            )
        )
    return tuple(values)  # type: ignore[return-value]


def _verified_candidates(
    context: M11M7AdapterContext,
    *,
    payload: M11Payload,
    problem: ReusableGeometryProblem,
    candidates_by_id: dict[str, Candidate],
) -> VerifiedProblemCandidates:
    candidates = tuple(candidates_by_id[key] for key in sorted(candidates_by_id))
    archives = _candidate_archives(context, payload=payload, candidate_count=len(candidates))
    raw_count = sum(item.candidate_count for item in archives)
    candidate_ids = tuple(item.candidate_id for item in candidates)
    semantic = {
        "schema_version": "yieldforge.m7-candidate-set.v1",
        "problem_id": problem.problem_id,
        "problem_sha256": problem.content_sha256,
        "archives": [item.model_dump(mode="json") for item in archives],
        "raw_candidate_count": raw_count,
        "distinct_candidate_count": len(candidate_ids),
        "candidate_ids": list(candidate_ids),
        "rejected_candidate_ids": [],
        "claim_ceiling": (
            "verified_shared_geometry_candidates_only_not_actions_policy_value_or_savings_evidence"
        ),
    }
    identifier, content = _identity("yfm7c-", semantic)
    evidence = M7CandidateSetEvidence(
        candidate_set_id=identifier,
        content_sha256=content,
        problem_id=problem.problem_id,
        problem_sha256=problem.content_sha256,
        archives=archives,
        raw_candidate_count=raw_count,
        distinct_candidate_count=len(candidate_ids),
        candidate_ids=candidate_ids,
    )
    rejection_layouts = tuple(
        build_verified_candidate_rejection_layout(
            problem=problem,
            evidence=evidence,
            candidate=candidate,
            prepared=prepare_layout_footprint(
                problem.problem,
                candidate,
                context.geometry_context.fit_config,
            ),
            fit_config=context.geometry_context.fit_config,
        )
        for candidate in candidates
    )
    return VerifiedProblemCandidates(
        evidence=evidence,
        candidates=candidates,
        rejection_layouts=rejection_layouts,
    )


def _compatible_stream_identity(
    context: M11M7AdapterContext,
    registration: _Registration,
    *,
    material_key: str,
    selected: tuple[_SelectedEvent, ...],
) -> tuple[str, str]:
    semantic = {
        "schema_version": "yieldforge.m11-private-m7-stream-compatibility.v1",
        "gate1_result_id": context.gate1_result.result_id,
        "gate1_result_content_sha256": context.gate1_result.content_sha256,
        "gate2_result_id": context.gate2_result.result_id,
        "gate2_result_content_sha256": context.gate2_result.content_sha256,
        "registration_kind": registration.registration_kind,
        "control_kind": registration.control_kind,
        "registered_exact_audit_arm": registration.registered_exact_audit_arm,
        "source_registration_id": registration.registration_id,
        "source_registration_content_sha256": registration.registration_content_sha256,
        "source_stream_id": registration.source_stream.stream_id,
        "source_stream_content_sha256": registration.source_stream.content_sha256,
        "material_key": material_key,
        "events": [
            {
                "source_event_id": item.event.event_id,
                "source_event_content_sha256": item.event.content_sha256,
                "source_event_position": item.event.position,
                "projected_material_key": item.projected_material_key,
                "reference_area_key": item.reference_area_key,
                "selected_candidate_ids": item.selected_candidate_ids,
            }
            for item in selected
        ],
        "native_m7_evidence": False,
    }
    digest = semantic_sha256(semantic)
    return f"yfts-{digest[:24]}", f"sha256:{digest}"


def _event_maps_and_instances(
    registration: _Registration,
    *,
    selected: tuple[_SelectedEvent, ...],
    problem_by_payload: dict[str, ReusableGeometryProblem],
    stream_id: str,
    stream_sha256: str,
) -> tuple[tuple[M11SourceEventMap, ...], tuple[TemporalInstanceBinding, ...]]:
    maps = []
    instances = []
    previous_release: datetime | None = None
    batch_sequence = -1
    batch_subsequence = 0
    for sequence, item in enumerate(selected):
        event = item.event
        released = _timestamp(event.released_at)
        if released != previous_release:
            batch_sequence += 1
            batch_subsequence = 0
        else:
            batch_subsequence += 1
        event_digest = semantic_sha256(
            {
                "domain": "m11-private-m7-compatibility-event",
                "stream_id": stream_id,
                "source_event_id": event.event_id,
                "source_event_content_sha256": event.content_sha256,
                "sequence": sequence,
            }
        )
        event_id = f"yfte-{event_digest[:20]}"
        batch_digest = semantic_sha256(
            {
                "domain": "m11-private-m7-compatibility-batch",
                "stream_id": stream_id,
                "released_at": event.released_at,
                "batch_sequence": batch_sequence,
            }
        )
        batch_id = f"yftb-{batch_digest[:20]}"
        problem = problem_by_payload[event.payload_id]
        semantic = {
            "schema_version": "yieldforge.m7-temporal-instance-binding.v1",
            "problem_id": problem.problem_id,
            "problem_sha256": problem.content_sha256,
            "stream_id": stream_id,
            "stream_sha256": stream_sha256,
            "event_id": event_id,
            "m6_batch_id": batch_id,
            "m6_batch_sequence": batch_sequence,
            "m6_subsequence": batch_subsequence,
            "sequence": sequence,
            "tasks_index": problem.tasks_index,
            "released_at": event.released_at,
            "material": _material(item.projected_material_key).model_dump(mode="json"),
            "regime": _regime(registration),
            "temporal_seed": registration.source_stream.root_seed,
            "partition": _partition(registration),
            "decomposition_rule": "source_event_boundary_before_policy",
            "chronology_provenance": "generated",
            "material_provenance": "assumed",
        }
        identifier, content = _identity("yfm7b-", semantic)
        instances.append(
            TemporalInstanceBinding(
                binding_id=identifier,
                content_sha256=content,
                problem_id=problem.problem_id,
                problem_sha256=problem.content_sha256,
                stream_id=stream_id,
                stream_sha256=stream_sha256,
                event_id=event_id,
                m6_batch_id=batch_id,
                m6_batch_sequence=batch_sequence,
                m6_subsequence=batch_subsequence,
                sequence=sequence,
                tasks_index=problem.tasks_index,
                released_at=released,
                material=_material(item.projected_material_key),
                regime=_regime(registration),
                temporal_seed=registration.source_stream.root_seed,
                partition=_partition(registration),
            )
        )
        maps.append(
            M11SourceEventMap(
                local_event_position=sequence,
                compatibility_event_id=event_id,
                source_event_position=event.position,
                source_event_id=event.event_id,
                source_event_content_sha256=event.content_sha256,
                source_material_key=event.material_key,
                projected_material_key=item.projected_material_key,
                payload_id=event.payload_id,
                released_at=event.released_at,
            )
        )
        previous_release = released
    return tuple(maps), tuple(instances)


def _visibility(
    registration: _Registration,
    *,
    selected: tuple[_SelectedEvent, ...],
) -> tuple[M11KnownVisibleLocalPrefix, ...]:
    local_by_source = {item.event.position: index for index, item in enumerate(selected)}
    selected_source_positions = tuple(sorted(local_by_source))
    result = []
    for local_position, item in enumerate(selected):
        if registration.all_work_known:
            visible_source = selected_source_positions
            rule = "hard_null_all_registered_work_known"
        else:
            visible_full = visible_event_positions(
                registration.source_stream,
                item.event.released_at,
            )
            visible_source = tuple(
                position for position in visible_full if position in local_by_source
            )
            rule = "m11_known_at_filtered_to_registered_slice_and_material"
        visible_local = tuple(local_by_source[position] for position in visible_source)
        result.append(
            M11KnownVisibleLocalPrefix(
                local_event_position=local_position,
                source_as_of_event_position=item.event.position,
                source_as_of_event_id=item.event.event_id,
                visible_source_event_positions=visible_source,
                visible_local_event_positions=visible_local,
                visibility_rule=rule,
            )
        )
    return tuple(result)


def _jagua_executable(context: M11M7AdapterContext) -> Path | None:
    candidate = context.geometry_context.jagua_executable
    if candidate is None or not _pinned_jagua_executable_is_usable(candidate):
        return None
    return Path(candidate).resolve()


def _compatibility_root(prefix: str, domain: str, values: dict[str, object]) -> tuple[str, str]:
    return _identity(prefix, {"domain": domain, **values, "native_m7_evidence": False})


def _build_material_projection(
    context: M11M7AdapterContext,
    registration: _Registration,
    *,
    material_key: str,
    selected: tuple[_SelectedEvent, ...],
    arm: EconomicArm,
    horizon_end: datetime,
) -> M11MaterialRuntimeProjection:
    population = context.population
    _streams, payloads = _population_maps(population)
    if not selected or any(item.projected_material_key != material_key for item in selected):
        raise AdapterEvidenceError("M11 adapter material projection is empty or crossed")
    reference_keys = {item.reference_area_key for item in selected}
    reference_values = {
        _reference_area(
            population,
            corpus_id=registration.source_stream.corpus_id,
            reference_area_key=key,
        )
        for key in reference_keys
    }
    if len(reference_values) != 1:
        raise AdapterEvidenceError("M11 adapter reference area changes within one material")
    reference_area = next(iter(reference_values))
    reference_area_key = (
        next(iter(reference_keys)) if len(reference_keys) == 1 else "derived-equal-reference-area"
    )
    rates = _rates(population, arm, reference_area)

    problem_by_payload: dict[str, ReusableGeometryProblem] = {}
    verified_by_problem: dict[str, VerifiedProblemCandidates] = {}
    source_bindings_by_payload: dict[str, tuple[str, ...]] = {}
    projected_ids_by_payload: dict[str, tuple[str, ...]] = {}
    for item in selected:
        event = item.event
        payload = payloads.get(event.payload_id)
        if payload is None:
            raise AdapterEvidenceError("M11 adapter event payload is absent")
        source_ids = tuple(value.candidate_id for value in payload.candidate_references)
        projected_ids = item.selected_candidate_ids or source_ids
        if not projected_ids or not set(projected_ids).issubset(source_ids):
            raise AdapterEvidenceError("M11 adapter candidate selection differs from payload")
        existing_ids = projected_ids_by_payload.get(payload.payload_id)
        if existing_ids is not None:
            if existing_ids != projected_ids:
                raise AdapterEvidenceError("one M11 payload has inconsistent projected actions")
            continue
        reconstructed = tuple(
            _reconstruct_official_payload(
                context.geometry_context,
                payload=payload,
                selected_candidate_id=candidate_id,
                material_key=material_key,
            )
            for candidate_id in projected_ids
        )
        problems = {value.problem.model_dump_json() for value in reconstructed}
        if len(problems) != 1 or tuple(value.candidate.candidate_id for value in reconstructed) != (
            projected_ids
        ):
            raise AdapterEvidenceError("M11 adapter reconstructed candidate geometry differs")
        problem = _compatibility_problem(
            context,
            payload=payload,
            problem=reconstructed[0].problem,
        )
        candidates_by_id = {
            value.candidate.candidate_id: value.candidate for value in reconstructed
        }
        verified = _verified_candidates(
            context,
            payload=payload,
            problem=problem,
            candidates_by_id=candidates_by_id,
        )
        if problem.problem_id in verified_by_problem:
            raise AdapterEvidenceError("M11 adapter compatibility problem identity collides")
        problem_by_payload[payload.payload_id] = problem
        verified_by_problem[problem.problem_id] = verified
        source_bindings_by_payload[payload.payload_id] = tuple(
            value.source_binding_sha256 for value in reconstructed
        )
        projected_ids_by_payload[payload.payload_id] = projected_ids

    stream_id, stream_sha256 = _compatible_stream_identity(
        context,
        registration,
        material_key=material_key,
        selected=selected,
    )
    event_map, instances = _event_maps_and_instances(
        registration,
        selected=selected,
        problem_by_payload=problem_by_payload,
        stream_id=stream_id,
        stream_sha256=stream_sha256,
    )
    roots = {
        "gate1_result_id": context.gate1_result.result_id,
        "gate1_result_content_sha256": context.gate1_result.content_sha256,
        "gate2_result_id": context.gate2_result.result_id,
        "gate2_result_content_sha256": context.gate2_result.content_sha256,
        "population_id": population.population_id,
        "population_content_sha256": population.content_sha256,
        "source_registration_id": registration.registration_id,
        "source_registration_content_sha256": registration.registration_content_sha256,
        "material_key": material_key,
        "stream_id": stream_id,
        "stream_sha256": stream_sha256,
    }
    problem_index_id, problem_index_sha = _compatibility_root(
        "yfm7i-", "m11-private-m7-problem-index", roots
    )
    m6_contract_id, m6_contract_sha = _compatibility_root("yfm6-", "m11-private-m6-contract", roots)
    m6_population_id, m6_population_sha = _compatibility_root(
        "yftp-", "m11-private-m6-population", roots
    )
    jagua = _jagua_executable(context)
    replay_input = build_m7_replay_input(
        m0_contract_id=context.geometry_context.m0.contract_id,
        m0_contract_sha256=context.geometry_context.m0.content_sha256,
        problem_index_id=problem_index_id,
        problem_index_sha256=problem_index_sha,
        m6_contract_id=m6_contract_id,
        m6_contract_sha256=m6_contract_sha,
        m6_population_id=m6_population_id,
        m6_population_sha256=m6_population_sha,
        policy=policy_identity(M7PolicyName.REMNANT_FIRST),
        rates=rates,
        fit_config=context.geometry_context.fit_config,
        search_config=context.geometry_context.search_config,
        problems=tuple(problem_by_payload.values()),
        candidate_sets=tuple(item.evidence for item in verified_by_problem.values()),
        instances=instances,
        horizon_end=horizon_end,
        collision_backend=(
            "jagua_rs_0_7_0_guarded_prefilter_shapely_witness"
            if jagua is not None
            else "shapely_authoritative"
        ),
        jagua_container_guard=1.0 if jagua is not None else None,
    )
    runtime = M7ReplayRuntime(
        replay_input=replay_input,
        runtime_candidates=verified_by_problem,
        rules=context.geometry_context.rules,
        jagua_executable=jagua,
    )
    visibility = _visibility(registration, selected=selected)
    parity = tuple(
        M11CandidateActionParity(
            local_event_position=local_position,
            source_event_id=item.event.event_id,
            payload_id=item.event.payload_id,
            runtime_problem_id=problem_by_payload[item.event.payload_id].problem_id,
            source_candidate_ids=tuple(
                value.candidate_id for value in payloads[item.event.payload_id].candidate_references
            ),
            projected_candidate_ids=projected_ids_by_payload[item.event.payload_id],
            runtime_candidate_ids=verified_by_problem[
                problem_by_payload[item.event.payload_id].problem_id
            ].evidence.candidate_ids,
            source_binding_sha256s=source_bindings_by_payload[item.event.payload_id],
            standard_action_ids=tuple(
                f"m7-standard:{candidate_id}"
                for candidate_id in verified_by_problem[
                    problem_by_payload[item.event.payload_id].problem_id
                ].evidence.candidate_ids
            ),
            projection_rule=(
                "hard_null_single_action"
                if registration.registration_kind == "hard_null"
                else "all_registered_candidates"
            ),
        )
        for local_position, item in enumerate(selected)
    )
    runtime_sha = m7_semantic_runtime_sha256(runtime)
    semantic = {
        "schema_version": "yieldforge.m11-m7-runtime-attestation.v1",
        "gate1_result_id": context.gate1_result.result_id,
        "gate1_result_content_sha256": context.gate1_result.content_sha256,
        "gate2_result_id": context.gate2_result.result_id,
        "gate2_result_content_sha256": context.gate2_result.content_sha256,
        "population_id": population.population_id,
        "population_content_sha256": population.content_sha256,
        "registration_kind": registration.registration_kind,
        "control_kind": registration.control_kind,
        "registered_exact_audit_arm": registration.registered_exact_audit_arm,
        "source_registration_id": registration.registration_id,
        "source_registration_content_sha256": registration.registration_content_sha256,
        "source_stream_id": registration.source_stream.stream_id,
        "source_stream_content_sha256": registration.source_stream.content_sha256,
        "corpus_id": registration.source_stream.corpus_id,
        "economic_arm": arm,
        "material_key": material_key,
        "reference_area_key": reference_area_key,
        "reference_area": reference_area,
        "rates": rates.model_dump(mode="json"),
        "source_event_map": tuple(item.model_dump(mode="json") for item in event_map),
        "known_visible_local_prefixes": tuple(item.model_dump(mode="json") for item in visibility),
        "candidate_action_parity": tuple(item.model_dump(mode="json") for item in parity),
        "m7_replay_input_id": replay_input.input_id,
        "m7_replay_input_content_sha256": replay_input.content_sha256,
        "m7_runtime_semantic_sha256": runtime_sha,
        "collision_backend": replay_input.collision_backend,
        "ledger_scope": "single_material_independent_substream",
        "compatibility_dto_only": True,
        "native_m7_evidence_persistence_authorized": False,
    }
    attestation_id, attestation_sha = _identity("yfm11m7a-", semantic)
    constructor_values = {
        **semantic,
        "rates": rates,
        "source_event_map": event_map,
        "known_visible_local_prefixes": visibility,
        "candidate_action_parity": parity,
    }
    attestation = M11M7ProjectionAttestation(
        attestation_id=attestation_id,
        content_sha256=attestation_sha,
        **constructor_values,
    )
    return M11MaterialRuntimeProjection(attestation=attestation, runtime=runtime)


def _project_registration(
    context: M11M7AdapterContext,
    registration: _Registration,
    arm: EconomicArm,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    _require_live_context(context)
    if arm not in ("central", "adverse"):
        raise AdapterEvidenceError("M11 adapter economic arm must be central or adverse")
    grouped: dict[str, list[_SelectedEvent]] = {}
    for item in registration.selected_events:
        grouped.setdefault(item.projected_material_key, []).append(item)
    horizon_end = max(_timestamp(item.event.due_at) for item in registration.selected_events)
    return tuple(
        _build_material_projection(
            context,
            registration,
            material_key=material_key,
            selected=tuple(grouped[material_key]),
            arm=arm,
            horizon_end=horizon_end,
        )
        for material_key in sorted(grouped)
    )


def project_stream(
    context: M11M7AdapterContext,
    stream_id: str,
    arm: EconomicArm,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    """Project one registered primary or shuffled-twin stream by exact material."""

    return _project_registration(context, _registration_for_stream(context, stream_id), arm)


def project_hard_null(
    context: M11M7AdapterContext,
    null_id: str,
    arm: EconomicArm,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    """Project one registered three-event hard null with its frozen control semantics."""

    return _project_registration(context, _registration_for_hard_null(context, null_id), arm)


def project_exact_audit(
    context: M11M7AdapterContext,
    audit_id: str,
    arm: EconomicArm,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    """Project one registered three-event exact-audit slice without widening it."""

    return _project_registration(context, _registration_for_exact_audit(context, audit_id), arm)


def _stream_cohort(
    context: M11M7AdapterContext,
    *,
    arm: EconomicArm,
    kind: Literal["calibration", "confirmation", "shuffled_twin"],
) -> tuple[M11MaterialRuntimeProjection, ...]:
    streams = tuple(
        item
        for item in context.population.streams
        if (
            (kind == "shuffled_twin" and item.stream_kind == "shuffled_twin")
            or (
                kind != "shuffled_twin" and item.stream_kind == "primary" and item.partition == kind
            )
        )
    )
    return tuple(
        projection
        for stream in streams
        for projection in project_stream(context, stream.stream_id, arm)
    )


def _control_cohort(
    context: M11M7AdapterContext,
    *,
    arm: EconomicArm,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    return tuple(
        projection
        for control in context.population.hard_nulls
        for projection in project_hard_null(context, control.null_id, arm)
    ) + tuple(
        projection
        for audit in context.population.exact_audits
        for projection in project_exact_audit(context, audit.audit_id, arm)
    )


def central_calibration_runtimes(
    context: M11M7AdapterContext,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    return _stream_cohort(context, arm="central", kind="calibration")


def adverse_calibration_runtimes(
    context: M11M7AdapterContext,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    return _stream_cohort(context, arm="adverse", kind="calibration")


def central_confirmation_runtimes(
    context: M11M7AdapterContext,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    return _stream_cohort(context, arm="central", kind="confirmation")


def adverse_confirmation_runtimes(
    context: M11M7AdapterContext,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    return _stream_cohort(context, arm="adverse", kind="confirmation")


def central_twin_runtimes(
    context: M11M7AdapterContext,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    return _stream_cohort(context, arm="central", kind="shuffled_twin")


def adverse_twin_runtimes(
    context: M11M7AdapterContext,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    return _stream_cohort(context, arm="adverse", kind="shuffled_twin")


def central_control_runtimes(
    context: M11M7AdapterContext,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    return _control_cohort(context, arm="central")


def adverse_control_runtimes(
    context: M11M7AdapterContext,
) -> tuple[M11MaterialRuntimeProjection, ...]:
    return _control_cohort(context, arm="adverse")


__all__ = [
    "AdapterEvidenceError",
    "M11CandidateActionParity",
    "M11KnownVisibleLocalPrefix",
    "M11M7AdapterContext",
    "M11M7ProjectionAttestation",
    "M11MaterialRuntimeProjection",
    "M11SourceEventMap",
    "adverse_calibration_runtimes",
    "adverse_confirmation_runtimes",
    "adverse_control_runtimes",
    "adverse_twin_runtimes",
    "central_calibration_runtimes",
    "central_confirmation_runtimes",
    "central_control_runtimes",
    "central_twin_runtimes",
    "load_authenticated_adapter_context",
    "project_exact_audit",
    "project_hard_null",
    "project_stream",
]
