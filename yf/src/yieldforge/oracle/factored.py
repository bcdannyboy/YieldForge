"""Explicitly unchecked M8 portable fact-bundle generation.

The objects assembled here are producer claims, never accepted proof authority.  Only the
independent fresh-process checker may validate them.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from time import perf_counter
from typing import ClassVar, Literal

from pydantic import BaseModel

from yieldforge.baseline.archives import VerifiedCandidateRejectionLayout
from yieldforge.baseline.contracts import M7ActionKind
from yieldforge.baseline.replay import (
    M7ReplayCursor,
    M7ReplayRuntime,
    m7_cursor_sha256,
    m7_semantic_runtime_sha256,
    run_m7_continuation,
)
from yieldforge.oracle import facts
from yieldforge.oracle.certificates import (
    BranchInventoryDelta,
    M8UncheckedCommonInventoryCapture,
    M8UncheckedInfluenceCapture,
    M8UncheckedProducerTransition,
    M8UncheckedStandardCandidateCapture,
    M8UncheckedTranslationBatchCapture,
    _capture_replay_cursor_commitment_source,
    _capture_replay_cursor_source,
    _capture_visible_suffix_source,
    _portable_policy_rank_components,
    _portable_search_config,
)
from yieldforge.oracle.compiled import (
    M8PreparedFrontierIntegrityError,
    _preflight_prepared_source_runtime,
)
from yieldforge.oracle.frontier import ParetoFrontier
from yieldforge.oracle.profiling import profile_phase
from yieldforge.oracle.proofs import m8_suffix_sha256
from yieldforge.oracle.reference import M8OracleRequest
from yieldforge.oracle.sparse import (
    M8UncheckedBranchEventCapture,
    M8UncheckedBranchTraversalCapture,
    _capture_prepared_unchecked_traversal,
    _prepare_m8_generator_context,
)
from yieldforge.replay.contracts import rounded_cost
from yieldforge.temporal_benchmark.contracts import TemporalPartition

_FREEZE_ID = re.compile(r"^yfm7freeze-([0-9a-f]{24})$")
_SHA256 = re.compile(r"^sha256:([0-9a-f]{64})$")


@dataclass(frozen=True)
class M8UncheckedBundleRequest:
    """Calibration-only request with explicit, mutually bound freeze provenance."""

    oracle_request: M8OracleRequest
    freeze_id: str
    freeze_sha256: str
    _runtime_object_id: int = field(init=False, repr=False, compare=False)
    _replay_input_object_id: int = field(init=False, repr=False, compare=False)
    _semantic_runtime_sha256: str = field(init=False, repr=False, compare=False)
    _replay_input_id: str = field(init=False, repr=False, compare=False)
    _replay_input_sha256: str = field(init=False, repr=False, compare=False)
    _stream_id: str = field(init=False, repr=False, compare=False)
    _stream_sha256: str = field(init=False, repr=False, compare=False)
    _cursor_sha256: str = field(init=False, repr=False, compare=False)
    authority_mode: ClassVar[Literal["unchecked_portable"]] = "unchecked_portable"

    def __post_init__(self) -> None:
        self._require_claim_shape()
        runtime = self.oracle_request.runtime
        replay_input = runtime.replay_input
        object.__setattr__(self, "_runtime_object_id", id(runtime))
        object.__setattr__(self, "_replay_input_object_id", id(replay_input))
        object.__setattr__(self, "_semantic_runtime_sha256", m7_semantic_runtime_sha256(runtime))
        object.__setattr__(self, "_replay_input_id", replay_input.input_id)
        object.__setattr__(self, "_replay_input_sha256", replay_input.content_sha256)
        object.__setattr__(self, "_stream_id", replay_input.stream_id)
        object.__setattr__(self, "_stream_sha256", replay_input.stream_sha256)
        object.__setattr__(self, "_cursor_sha256", m7_cursor_sha256(self.oracle_request.cursor))

    def _require_claim_shape(self) -> None:
        if type(self.oracle_request) is not M8OracleRequest:
            raise TypeError("M8 unchecked bundle requires an exact oracle request")
        if type(self.freeze_id) is not str or type(self.freeze_sha256) is not str:
            raise TypeError("M8 unchecked bundle freeze identity requires exact strings")
        freeze_id = _FREEZE_ID.fullmatch(self.freeze_id)
        freeze_sha = _SHA256.fullmatch(self.freeze_sha256)
        if freeze_id is None or freeze_sha is None:
            raise ValueError("M8 unchecked bundle freeze identity is not canonical")
        if freeze_id.group(1) != freeze_sha.group(1)[:24]:
            raise ValueError("M8 unchecked bundle freeze ID and SHA-256 differ")
        bindings = self.oracle_request.runtime.replay_input.instances
        if any(item.partition is not TemporalPartition.CALIBRATION for item in bindings):
            raise ValueError("M8 unchecked fact generation is calibration-only")

    def require_valid(self) -> None:
        """Reject post-construction drift before producer traversal begins."""

        self._require_claim_shape()
        runtime = self.oracle_request.runtime
        replay_input = runtime.replay_input
        if id(runtime) != self._runtime_object_id:
            raise ValueError("M8 unchecked bundle runtime identity drifted")
        if id(replay_input) != self._replay_input_object_id:
            raise ValueError("M8 unchecked bundle replay-input identity drifted")
        if (
            replay_input.input_id != self._replay_input_id
            or replay_input.content_sha256 != self._replay_input_sha256
            or replay_input.stream_id != self._stream_id
            or replay_input.stream_sha256 != self._stream_sha256
        ):
            raise ValueError("M8 unchecked bundle replay-input provenance drifted")
        if m7_cursor_sha256(self.oracle_request.cursor) != self._cursor_sha256:
            raise ValueError("M8 unchecked bundle cursor identity drifted")
        if m7_semantic_runtime_sha256(runtime) != self._semantic_runtime_sha256:
            raise ValueError("M8 unchecked bundle semantic runtime identity drifted")

    def require_prepared_bindings(
        self,
        *,
        oracle_request: M8OracleRequest,
        semantic_runtime_sha256: str,
    ) -> None:
        """Bind the captured authoritative snapshot back to this unchecked request."""

        replay_input = oracle_request.runtime.replay_input
        if any(
            item.partition is not TemporalPartition.CALIBRATION for item in replay_input.instances
        ):
            raise ValueError("M8 unchecked fact generation is calibration-only")
        if (
            replay_input.input_id != self._replay_input_id
            or replay_input.content_sha256 != self._replay_input_sha256
            or replay_input.stream_id != self._stream_id
            or replay_input.stream_sha256 != self._stream_sha256
            or m7_cursor_sha256(oracle_request.cursor) != self._cursor_sha256
            or semantic_runtime_sha256 != self._semantic_runtime_sha256
        ):
            raise ValueError("M8 prepared generator bindings differ from bundle request")


@dataclass(frozen=True)
class _CapturedUncheckedBundleRequest:
    oracle_request: M8OracleRequest
    freeze_id: str
    freeze_sha256: str
    runtime_object_id: int
    replay_input_object_id: int
    semantic_runtime_sha256: str
    replay_input_id: str
    replay_input_sha256: str
    stream_id: str
    stream_sha256: str
    cursor_sha256: str
    cursor_position: int
    suffix_sha256: str
    source_request: M8UncheckedBundleRequest = field(repr=False, compare=False)
    source_request_state: dict[str, object] = field(repr=False, compare=False)
    source_oracle_request: M8OracleRequest = field(repr=False, compare=False)
    source_oracle_state: dict[str, object] = field(repr=False, compare=False)
    source_runtime: M7ReplayRuntime = field(repr=False, compare=False)
    source_replay_input: BaseModel = field(repr=False, compare=False)
    source_cursor: M7ReplayCursor = field(repr=False, compare=False)
    source_visibility: object = field(repr=False, compare=False)


def _capture_unchecked_bundle_request_source(
    request: M8UncheckedBundleRequest,
) -> _CapturedUncheckedBundleRequest:
    """Capture wrapper claims without dispatching instance-shadowed methods."""

    try:
        if type(request) is not M8UncheckedBundleRequest:
            raise TypeError("M8 unchecked bundle request type differs")
        state = object.__getattribute__(request, "__dict__")
        expected_fields = {item.name for item in dataclass_fields(M8UncheckedBundleRequest)}
        if (
            type(state) is not dict
            or any(type(name) is not str for name in state)
            or set(state) != expected_fields
        ):
            raise TypeError("M8 unchecked bundle request state differs")
        scalar_names = (
            "freeze_id",
            "freeze_sha256",
            "_semantic_runtime_sha256",
            "_replay_input_id",
            "_replay_input_sha256",
            "_stream_id",
            "_stream_sha256",
            "_cursor_sha256",
        )
        identity_names = ("_runtime_object_id", "_replay_input_object_id")
        if any(type(state[name]) is not str for name in scalar_names) or any(
            type(state[name]) is not int for name in identity_names
        ):
            raise TypeError("M8 unchecked bundle claim type differs")
        freeze_id = _FREEZE_ID.fullmatch(state["freeze_id"])
        freeze_sha256 = _SHA256.fullmatch(state["freeze_sha256"])
        if (
            freeze_id is None
            or freeze_sha256 is None
            or freeze_id.group(1) != freeze_sha256.group(1)[:24]
        ):
            raise ValueError("M8 unchecked bundle freeze claim differs")
        original_claims = tuple(state[name] for name in (*scalar_names, *identity_names))
        oracle_request = state["oracle_request"]
        if type(oracle_request) is not M8OracleRequest:
            raise TypeError("M8 unchecked oracle request type differs")
        oracle_state = object.__getattribute__(oracle_request, "__dict__")
        if type(oracle_state) is not dict or set(oracle_state) != {
            "runtime",
            "cursor",
            "visibility",
        }:
            raise TypeError("M8 unchecked oracle request state differs")
        source_runtime = oracle_state["runtime"]
        source_cursor = oracle_state["cursor"]
        source_visibility = oracle_state["visibility"]
        if type(source_runtime) is not M7ReplayRuntime:
            raise TypeError("M8 unchecked source runtime type differs")
        _preflight_prepared_source_runtime(source_runtime)
        source_replay_input = source_runtime.replay_input
        if any(
            item.partition is not TemporalPartition.CALIBRATION
            for item in source_replay_input.instances
        ):
            raise M8PreparedFrontierIntegrityError(
                "M8 unchecked fact generation is calibration-only"
            )
        cursor_position, cursor_sha256 = _capture_replay_cursor_commitment_source(
            source_cursor
        )
        captured_visibility = _capture_visible_suffix_source(
            source_runtime,
            source_visibility,
            current_position=cursor_position,
        )
        current_state = object.__getattribute__(request, "__dict__")
        current_oracle_state = object.__getattribute__(oracle_request, "__dict__")
        if (
            type(current_state) is not dict
            or current_state is not state
            or set(current_state) != expected_fields
            or tuple(current_state[name] for name in (*scalar_names, *identity_names))
            != original_claims
            or current_state["oracle_request"] is not oracle_request
            or type(current_oracle_state) is not dict
            or current_oracle_state is not oracle_state
            or set(current_oracle_state) != {"runtime", "cursor", "visibility"}
            or current_oracle_state["runtime"] is not source_runtime
            or current_oracle_state["cursor"] is not source_cursor
            or current_oracle_state["visibility"] is not source_visibility
        ):
            raise TypeError("M8 unchecked bundle request drifted during source capture")
        captured_cursor = _capture_replay_cursor_source(source_cursor)
        if (
            captured_cursor.next_event_position != cursor_position
            or m7_cursor_sha256(captured_cursor) != cursor_sha256
        ):
            raise TypeError("M8 unchecked bundle cursor drifted during source capture")
        if (
            id(source_runtime) != state["_runtime_object_id"]
            or id(source_replay_input) != state["_replay_input_object_id"]
            or source_runtime.replay_input is not source_replay_input
            or source_replay_input.input_id != state["_replay_input_id"]
            or source_replay_input.content_sha256 != state["_replay_input_sha256"]
            or source_replay_input.stream_id != state["_stream_id"]
            or source_replay_input.stream_sha256 != state["_stream_sha256"]
            or m7_cursor_sha256(captured_cursor) != state["_cursor_sha256"]
            or captured_visibility.semantic_runtime_sha256
            != state["_semantic_runtime_sha256"]
            or any(
                item.partition is not TemporalPartition.CALIBRATION
                for item in source_replay_input.instances
            )
        ):
            raise ValueError("M8 unchecked bundle source bindings drifted")
        captured_oracle_request = M8OracleRequest(
            runtime=source_runtime,
            cursor=captured_cursor,
            visibility=captured_visibility,
        )
        suffix_sha256 = m8_suffix_sha256(
            semantic_runtime_sha256=state["_semantic_runtime_sha256"],
            start_event_position=cursor_position,
            stop_event_position=cursor_position + 1 + len(captured_visibility.bindings),
            bindings=captured_visibility.bindings,
        )
        return _CapturedUncheckedBundleRequest(
            oracle_request=captured_oracle_request,
            freeze_id=state["freeze_id"],
            freeze_sha256=state["freeze_sha256"],
            runtime_object_id=state["_runtime_object_id"],
            replay_input_object_id=state["_replay_input_object_id"],
            semantic_runtime_sha256=state["_semantic_runtime_sha256"],
            replay_input_id=state["_replay_input_id"],
            replay_input_sha256=state["_replay_input_sha256"],
            stream_id=state["_stream_id"],
            stream_sha256=state["_stream_sha256"],
            cursor_sha256=state["_cursor_sha256"],
            cursor_position=cursor_position,
            suffix_sha256=suffix_sha256,
            source_request=request,
            source_request_state=state,
            source_oracle_request=oracle_request,
            source_oracle_state=oracle_state,
            source_runtime=source_runtime,
            source_replay_input=source_replay_input,
            source_cursor=source_cursor,
            source_visibility=source_visibility,
        )
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: unchecked bundle request source capture"
        ) from error


def _require_captured_unchecked_bundle_bindings(
    captured: _CapturedUncheckedBundleRequest,
    *,
    oracle_request: M8OracleRequest,
    semantic_runtime_sha256: str,
) -> None:
    replay_input = oracle_request.runtime.replay_input
    if any(item.partition is not TemporalPartition.CALIBRATION for item in replay_input.instances):
        raise ValueError("M8 unchecked fact generation is calibration-only")
    if (
        replay_input.input_id != captured.replay_input_id
        or replay_input.content_sha256 != captured.replay_input_sha256
        or replay_input.stream_id != captured.stream_id
        or replay_input.stream_sha256 != captured.stream_sha256
        or m7_cursor_sha256(oracle_request.cursor) != captured.cursor_sha256
        or semantic_runtime_sha256 != captured.semantic_runtime_sha256
    ):
        raise ValueError("M8 prepared generator bindings differ from bundle request")


def _require_unchecked_bundle_request_source_stable(
    captured: _CapturedUncheckedBundleRequest,
    *,
    request: M8UncheckedBundleRequest,
    bundle: facts.M8UncheckedFactBundleV2,
) -> None:
    """Recheck public source commitments without invoking visibility again."""

    try:
        if (
            type(captured) is not _CapturedUncheckedBundleRequest
            or type(request) is not M8UncheckedBundleRequest
            or request is not captured.source_request
            or type(bundle) is not facts.M8UncheckedFactBundleV2
        ):
            raise TypeError("M8 unchecked bundle final source type differs")
        expected_request_fields = {
            item.name for item in dataclass_fields(M8UncheckedBundleRequest)
        }
        state = object.__getattribute__(request, "__dict__")
        oracle_request = captured.source_oracle_request
        oracle_state = object.__getattribute__(oracle_request, "__dict__")
        if (
            type(state) is not dict
            or state is not captured.source_request_state
            or set(state) != expected_request_fields
            or state["oracle_request"] is not oracle_request
            or type(oracle_state) is not dict
            or oracle_state is not captured.source_oracle_state
            or set(oracle_state) != {"runtime", "cursor", "visibility"}
            or oracle_state["runtime"] is not captured.source_runtime
            or oracle_state["cursor"] is not captured.source_cursor
            or oracle_state["visibility"] is not captured.source_visibility
        ):
            raise TypeError("M8 unchecked bundle source storage drifted")
        scalar_claims = (
            state["freeze_id"],
            state["freeze_sha256"],
            state["_semantic_runtime_sha256"],
            state["_replay_input_id"],
            state["_replay_input_sha256"],
            state["_stream_id"],
            state["_stream_sha256"],
            state["_cursor_sha256"],
        )
        identity_claims = (
            state["_runtime_object_id"],
            state["_replay_input_object_id"],
        )
        if any(type(value) is not str for value in scalar_claims) or any(
            type(value) is not int for value in identity_claims
        ):
            raise TypeError("M8 unchecked bundle final claim type differs")
        if scalar_claims != (
            captured.freeze_id,
            captured.freeze_sha256,
            captured.semantic_runtime_sha256,
            captured.replay_input_id,
            captured.replay_input_sha256,
            captured.stream_id,
            captured.stream_sha256,
            captured.cursor_sha256,
        ) or identity_claims != (
            captured.runtime_object_id,
            captured.replay_input_object_id,
        ):
            raise ValueError("M8 unchecked bundle final source claims drifted")
        _preflight_prepared_source_runtime(captured.source_runtime)
        replay_input = captured.source_runtime.replay_input
        cursor_position, cursor_sha256 = _capture_replay_cursor_commitment_source(
            captured.source_cursor
        )
        if (
            replay_input is not captured.source_replay_input
            or id(captured.source_runtime) != captured.runtime_object_id
            or id(replay_input) != captured.replay_input_object_id
            or replay_input.input_id != captured.replay_input_id
            or replay_input.content_sha256 != captured.replay_input_sha256
            or replay_input.stream_id != captured.stream_id
            or replay_input.stream_sha256 != captured.stream_sha256
            or cursor_position != captured.cursor_position
            or cursor_sha256 != captured.cursor_sha256
            or m7_semantic_runtime_sha256(captured.source_runtime)
            != captured.semantic_runtime_sha256
        ):
            raise ValueError("M8 unchecked bundle final source bindings drifted")
        provenance = bundle.provenance
        if (
            provenance.freeze_id != captured.freeze_id
            or provenance.freeze_sha256 != captured.freeze_sha256
            or provenance.replay_input_id != captured.replay_input_id
            or provenance.replay_input_sha256 != captured.replay_input_sha256
            or provenance.semantic_runtime_sha256 != captured.semantic_runtime_sha256
            or provenance.stream_id != captured.stream_id
            or provenance.stream_sha256 != captured.stream_sha256
            or provenance.suffix_sha256 != captured.suffix_sha256
            or provenance.evaluation_partition_opened is not False
            or any(
                root.semantic_runtime_sha256 != captured.semantic_runtime_sha256
                or root.stream_id != captured.stream_id
                or root.suffix_sha256 != captured.suffix_sha256
                or root.start_state_sha256 != captured.cursor_sha256
                for root in bundle.action_roots
            )
        ):
            raise ValueError("M8 unchecked bundle output bindings differ from source capture")
    except M8PreparedFrontierIntegrityError:
        raise
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise M8PreparedFrontierIntegrityError(
            "M8 prepared frontier integrity differs: unchecked bundle request drift"
        ) from error


@dataclass(frozen=True)
class M8BundleGenerationTelemetry:
    """Non-semantic operational measurements excluded from bundle hashing."""

    semantic_serialized_bytes: int
    serialization_seconds: float
    portable_transition_serialized_bytes: tuple[int, ...]
    common_event_count: int
    action_root_count: int
    counted_inventory_evidence_count: int
    translation_batch_count: int
    exact_transition_count: int

    def __post_init__(self) -> None:
        counts = (
            self.semantic_serialized_bytes,
            self.common_event_count,
            self.action_root_count,
            self.counted_inventory_evidence_count,
            self.translation_batch_count,
            self.exact_transition_count,
            *self.portable_transition_serialized_bytes,
        )
        if any(type(item) is not int or item < 0 for item in counts):
            raise ValueError("M8 bundle telemetry counts must be nonnegative exact integers")
        if (
            type(self.serialization_seconds) is not float
            or not math.isfinite(self.serialization_seconds)
            or self.serialization_seconds < 0.0
        ):
            raise ValueError("M8 bundle serialization timing must be finite and nonnegative")


@dataclass(frozen=True)
class M8UncheckedBundleGenerationResult:
    """Unchecked portable bundle and non-semantic telemetry."""

    bundle: facts.M8UncheckedFactBundleV2
    telemetry: M8BundleGenerationTelemetry
    authority_mode: ClassVar[Literal["unchecked_portable"]] = "unchecked_portable"

    @property
    def semantic_bytes(self) -> bytes:
        """Return deterministic semantic JSON bytes suitable for fresh strict loading."""

        return unchecked_fact_bundle_semantic_bytes(self.bundle)


def _content_addressed[FactModel: BaseModel](
    model: type[FactModel],
    payload: dict[str, object],
) -> FactModel:
    fact_kind = payload.get("fact_kind")
    if type(fact_kind) is not str:
        raise TypeError("M8 portable fact payload lacks an exact fact kind")
    fact_sha256 = facts.m8_fact_sha256(fact_kind, payload)
    return model.model_validate(
        {**payload, "fact_sha256": fact_sha256},
        strict=True,
    )


def unchecked_fact_bundle_semantic_bytes(
    bundle: facts.M8UncheckedFactBundleV2,
) -> bytes:
    """Serialize one unchecked bundle without timing or other operational metadata."""

    if type(bundle) is not facts.M8UncheckedFactBundleV2:
        raise TypeError("M8 semantic serialization requires an unchecked v2 bundle")
    return facts.canonical_semantic_json(bundle.model_dump(mode="json"))


def _translation_point(point: tuple[float, float]) -> facts.M8TranslationPointV2:
    return facts.M8TranslationPointV2(
        x_bits=facts.encode_canonical_f64(float(point[0])),
        y_bits=facts.encode_canonical_f64(float(point[1])),
    )


def _portable_search_config_sha256(source: M8UncheckedProducerTransition) -> str:
    portable = _portable_search_config(source.source.search_config)
    digest = hashlib.sha256(
        facts.canonical_semantic_json(portable.model_dump(mode="json"))
    ).hexdigest()
    return f"sha256:{digest}"


class _FactStore:
    """Identity-indexed fixed layers with collision-safe deterministic deduplication."""

    def __init__(self) -> None:
        self.translations: dict[
            tuple[str, str, int, str, str], facts.M8PortableTranslationBatch
        ] = {}
        self.scalars: dict[tuple[str, str, str, str, str], facts.M8CandidateScalarFactV2] = {}
        self.frontiers: dict[tuple[str, str, str, str], facts.M8FrontierFactV2] = {}
        self.standards: dict[tuple[str, str, int, int], facts.M8StandardCandidateFactV2] = {}

    @staticmethod
    def _deduplicate[FactModel: BaseModel](
        layer: dict[object, FactModel],
        identity: object,
        candidate: FactModel,
        *,
        label: str,
    ) -> FactModel:
        existing = layer.get(identity)
        if existing is None:
            layer[identity] = candidate
            return candidate
        if (
            existing.fact_sha256 != candidate.fact_sha256  # type: ignore[attr-defined]
            or existing.model_dump(mode="json") != candidate.model_dump(mode="json")
        ):
            raise ValueError(f"M8 {label} semantic identity has conflicting content")
        return existing

    def scalar(
        self,
        *,
        semantic_runtime_sha256: str,
        stream_id: str,
        layout: VerifiedCandidateRejectionLayout,
    ) -> facts.M8CandidateScalarFactV2:
        identity = (
            semantic_runtime_sha256,
            stream_id,
            layout.problem_id,
            layout.candidate_set_id,
            layout.candidate_id,
        )
        layout_area_bits = facts.encode_canonical_f64(float(layout.layout_area))
        layout_width_bits = facts.encode_canonical_f64(float(layout.layout_width))
        layout_height_bits = facts.encode_canonical_f64(float(layout.layout_height))
        existing = self.scalars.get(identity)
        if existing is not None:
            expected_source = (
                layout.problem_sha256,
                layout.candidate_set_sha256,
                layout.source_transform_sha256,
                layout.material_binding_scope,
                layout.fit_config_sha256,
                layout_area_bits,
                layout_width_bits,
                layout_height_bits,
            )
            observed_source = (
                existing.problem_sha256,
                existing.candidate_set_sha256,
                existing.source_transform_sha256,
                existing.material_partition,
                existing.fit_config_sha256,
                existing.layout_area_bits,
                existing.layout_width_bits,
                existing.layout_height_bits,
            )
            if observed_source != expected_source:
                raise ValueError(
                    "M8 candidate scalar semantic identity has conflicting content"
                )
            return existing
        candidate = _content_addressed(
            facts.M8CandidateScalarFactV2,
            {
                "schema_version": "yieldforge.m8-candidate-scalar-fact.v2",
                "fact_kind": "candidate_scalar",
                "semantic_runtime_sha256": semantic_runtime_sha256,
                "stream_id": stream_id,
                "problem_id": layout.problem_id,
                "problem_sha256": layout.problem_sha256,
                "candidate_set_id": layout.candidate_set_id,
                "candidate_set_sha256": layout.candidate_set_sha256,
                "candidate_id": layout.candidate_id,
                "source_transform_sha256": layout.source_transform_sha256,
                "material_partition": layout.material_binding_scope,
                "fit_config_sha256": layout.fit_config_sha256,
                "layout_area_bits": layout_area_bits,
                "layout_width_bits": layout_width_bits,
                "layout_height_bits": layout_height_bits,
            },
        )
        return self._deduplicate(  # type: ignore[return-value]
            self.scalars,
            identity,
            candidate,
            label="candidate scalar",
        )

    def translation(
        self,
        *,
        common: M8UncheckedProducerTransition,
        capture: M8UncheckedTranslationBatchCapture,
    ) -> facts.M8PortableTranslationBatch:
        source = common.source
        candidate = _content_addressed(
            facts.M8PortableTranslationBatch,
            {
                "schema_version": "yieldforge.m8-portable-translation-batch.v2",
                "fact_kind": "translation_batch",
                "semantic_runtime_sha256": source.semantic_runtime_sha256,
                "stream_id": source.stream_id,
                "event_position": common.common_fact.event_position,
                "remnant_id": capture.remnant_id,
                "candidate_id": capture.candidate_id,
                "fit_config_sha256": source.fit_config_sha256,
                "search_config_sha256": _portable_search_config_sha256(common),
                "source_order": source.search_config.candidate_source_order,
                "translations": tuple(_translation_point(item) for item in capture.translations),
                "generated_candidate_count": capture.generated_candidate_count,
                "duplicate_candidate_count": capture.duplicate_candidate_count,
                "evaluated_candidate_count": capture.evaluated_candidate_count,
                "budget_truncated": capture.budget_truncated,
            },
        )
        identity = (
            source.semantic_runtime_sha256,
            source.stream_id,
            common.common_fact.event_position,
            capture.remnant_id,
            capture.candidate_id,
        )
        return self._deduplicate(  # type: ignore[return-value]
            self.translations,
            identity,
            candidate,
            label="translation batch",
        )

    def frontier(
        self,
        *,
        common: M8UncheckedProducerTransition,
        frontier: ParetoFrontier,
        scalar_by_candidate: dict[str, facts.M8CandidateScalarFactV2],
    ) -> facts.M8FrontierFactV2:
        if not frontier.members:
            raise ValueError("M8 portable frontier cannot be empty")
        first = frontier.members[0]
        candidate_refs = tuple(
            sorted(scalar_by_candidate[item.candidate_id].fact_sha256 for item in frontier.members)
        )
        retained_refs = tuple(
            sorted(scalar_by_candidate[item.candidate_id].fact_sha256 for item in frontier.retained)
        )
        dominance = tuple(
            sorted(
                (
                    facts.M8DominanceEvidenceV2(
                        dominated_candidate_scalar_ref=scalar_by_candidate[
                            item.dominated_candidate_id
                        ].fact_sha256,
                        retained_candidate_scalar_ref=scalar_by_candidate[
                            item.retained_candidate_id
                        ].fact_sha256,
                    )
                    for item in frontier.dominated_by
                ),
                key=lambda item: (
                    item.dominated_candidate_scalar_ref,
                    item.retained_candidate_scalar_ref,
                    item.relation,
                ),
            )
        )
        candidate = _content_addressed(
            facts.M8FrontierFactV2,
            {
                "schema_version": "yieldforge.m8-frontier-fact.v2",
                "fact_kind": "frontier",
                "semantic_runtime_sha256": common.source.semantic_runtime_sha256,
                "stream_id": common.source.stream_id,
                "problem_id": first.problem_id,
                "problem_sha256": first.problem_sha256,
                "candidate_set_id": first.candidate_set_id,
                "candidate_set_sha256": first.candidate_set_sha256,
                "material_partition": first.material_partition,
                "fit_config_sha256": first.fit_config_sha256,
                "candidate_scalar_refs": candidate_refs,
                "retained_candidate_scalar_refs": retained_refs,
                "dominance_evidence": dominance,
            },
        )
        identity = (
            common.source.semantic_runtime_sha256,
            common.source.stream_id,
            first.problem_id,
            first.candidate_set_id,
        )
        return self._deduplicate(  # type: ignore[return-value]
            self.frontiers,
            identity,
            candidate,
            label="frontier",
        )

    def standard(
        self,
        *,
        common: M8UncheckedProducerTransition,
        capture: M8UncheckedStandardCandidateCapture,
    ) -> facts.M8StandardCandidateFactV2:
        profile = capture.profile
        accounting = profile.accounting
        rates = common.source.replay_input.rates
        purchase = rounded_cost(accounting.parent_remnant_area * rates.purchase_cost_per_area)
        storage = rounded_cost(accounting.retained_child_area * rates.storage_cost_per_area_hour)
        returns = rounded_cost(
            profile.returned_remnant_count * rates.return_handling_cost_per_remnant
        )
        retrieval = 0.0
        scrap = rounded_cost(accounting.scrap_area * rates.scrap_credit_per_area)
        terminal_credit = 0.0
        selected = capture.descriptor.action_id == common.common_fact.step.descriptor.action_id
        materialized_action_id = (
            common.common_fact.step.event.action.action_id
            if selected and capture.descriptor.kind is M7ActionKind.OPEN_STANDARD_SHEET
            else None
        )
        candidate = _content_addressed(
            facts.M8StandardCandidateFactV2,
            {
                "schema_version": "yieldforge.m8-standard-candidate-fact.v2",
                "fact_kind": "standard_candidate",
                "semantic_runtime_sha256": common.source.semantic_runtime_sha256,
                "stream_id": common.source.stream_id,
                "event_position": common.common_fact.event_position,
                "profile_position": capture.profile_position,
                "candidate_id": profile.candidate_id,
                "catalog_action_id": capture.descriptor.action_id,
                "materialized_action_id": materialized_action_id,
                "action_kind": capture.descriptor.kind.value,
                "selected_stock_id": capture.context.selected_stock_id,
                "policy_name": capture.rank.policy.value,
                "candidate_width_bits": facts.encode_canonical_f64(float(profile.candidate_width)),
                "parent_remnant_area_bits": facts.encode_canonical_f64(
                    float(accounting.parent_remnant_area)
                ),
                "placed_area_bits": facts.encode_canonical_f64(float(accounting.placed_area)),
                "process_loss_area_bits": facts.encode_canonical_f64(
                    float(accounting.process_loss_area)
                ),
                "retained_child_area_bits": facts.encode_canonical_f64(
                    float(accounting.retained_child_area)
                ),
                "scrap_area_bits": facts.encode_canonical_f64(float(accounting.scrap_area)),
                "reconciliation_delta_bits": facts.encode_canonical_f64(
                    float(accounting.reconciliation_delta)
                ),
                "accounting_area_tolerance_bits": facts.encode_canonical_f64(
                    float(accounting.area_tolerance)
                ),
                "purchase_cost_bits": facts.encode_canonical_f64(purchase),
                "storage_cost_bits": facts.encode_canonical_f64(storage),
                "return_handling_cost_bits": facts.encode_canonical_f64(returns),
                "retrieval_handling_cost_bits": facts.encode_canonical_f64(retrieval),
                "scrap_proceeds_bits": facts.encode_canonical_f64(scrap),
                "terminal_scrap_credit_bits": facts.encode_canonical_f64(terminal_credit),
                "immediate_net_cost_bits": facts.encode_canonical_f64(
                    float(capture.policy_immediate_net_cost)
                ),
                "returned_remnant_count": profile.returned_remnant_count,
                "returned_regularity_bits": facts.encode_canonical_f64(
                    float(profile.returned_regularity)
                ),
                "selected_remnant_age_hours_bits": facts.encode_canonical_f64(
                    float(capture.context.selected_remnant_age_hours)
                ),
                "known_order_lookahead_term_bits": facts.encode_canonical_f64(
                    float(capture.context.known_order_lookahead_term)
                ),
                "comparison_key": _portable_policy_rank_components(capture.rank.comparison_key),
                "decision_key": capture.rank.decision_key,
            },
        )
        identity = (
            common.source.semantic_runtime_sha256,
            common.source.stream_id,
            common.common_fact.event_position,
            capture.profile_position,
        )
        return self._deduplicate(  # type: ignore[return-value]
            self.standards,
            identity,
            candidate,
            label="standard candidate",
        )


def _layout_by_candidate(
    common: M8UncheckedProducerTransition,
) -> dict[str, VerifiedCandidateRejectionLayout]:
    layouts = common.source.verified_candidates.rejection_layouts
    by_candidate = {item.candidate_id: item for item in layouts}
    if len(by_candidate) != len(layouts):
        raise ValueError("M8 source rejection layouts contain duplicate candidates")
    return by_candidate


def _inventory_classification(
    store: _FactStore,
    *,
    common: M8UncheckedProducerTransition,
    capture: M8UncheckedCommonInventoryCapture,
) -> facts.M8CommonInventoryClassificationV2:
    scalar_by_candidate = {
        layout.candidate_id: store.scalar(
            semantic_runtime_sha256=common.source.semantic_runtime_sha256,
            stream_id=common.source.stream_id,
            layout=layout,
        )
        for layout in capture.candidate_rejection_layouts
    }
    frontier = (
        store.frontier(
            common=common,
            frontier=capture.frontier,
            scalar_by_candidate=scalar_by_candidate,
        )
        if capture.frontier is not None
        else None
    )
    translations = tuple(
        store.translation(common=common, capture=item) for item in capture.translation_batches
    )
    return facts.M8CommonInventoryClassificationV2(
        remnant_id=capture.remnant_id,
        classification=capture.classification,
        material_matches=capture.material_matches,
        remnant_area_bits=facts.encode_canonical_f64(float(capture.remnant_area)),
        remnant_width_bits=facts.encode_canonical_f64(float(capture.remnant_width)),
        remnant_height_bits=facts.encode_canonical_f64(float(capture.remnant_height)),
        area_tolerance_bits=facts.encode_canonical_f64(float(capture.area_tolerance)),
        coordinate_tolerance_bits=facts.encode_canonical_f64(float(capture.coordinate_tolerance)),
        frontier_ref=frontier.fact_sha256 if frontier is not None else None,
        candidate_scalar_refs=tuple(
            sorted(item.fact_sha256 for item in scalar_by_candidate.values())
        ),
        translation_batch_refs=tuple(sorted(item.fact_sha256 for item in translations)),
        exact_replay_reason=capture.exact_replay_reason,
    )


def _exact_reason_summary(
    classifications: tuple[facts.M8CommonInventoryClassificationV2, ...],
) -> facts.M8CommonExactReplayReasonV2 | None:
    reasons = {
        item.exact_replay_reason
        for item in classifications
        if item.classification == "exact_survivor"
    }
    reasons.discard(None)
    if not reasons:
        return None
    if len(reasons) > 1:
        return "exact_survivor_mixed"
    reason = next(iter(reasons))
    return {
        "frontier_survivor": "exact_survivor_frontier",
        "counted_search_survivor": "exact_survivor_counted_search",
        "unsupported_representation": "exact_survivor_unsupported_representation",
    }[reason]


def _common_lemma(
    store: _FactStore,
    *,
    common: M8UncheckedProducerTransition,
    previous_common_lemma_ref: str | None,
) -> facts.M8CommonTransitionLemmaV2:
    standards = tuple(
        store.standard(common=common, capture=item) for item in common.standard_candidates
    )
    minimum = min(
        zip(common.standard_candidates, standards, strict=True),
        key=lambda pair: pair[0].rank.comparison_key,
    )[1]
    classifications = tuple(
        sorted(
            (
                _inventory_classification(store, common=common, capture=item)
                for item in common.inventory_classifications
            ),
            key=lambda item: item.remnant_id,
        )
    )
    scalar_refs = tuple(
        sorted({reference for item in classifications for reference in item.candidate_scalar_refs})
    )
    frontier_refs = tuple(
        sorted({item.frontier_ref for item in classifications if item.frontier_ref is not None})
    )
    translation_refs = tuple(
        sorted({reference for item in classifications for reference in item.translation_batch_refs})
    )
    classifications_present = {item.classification for item in classifications}
    exact_reason = _exact_reason_summary(classifications)
    if "exact_survivor" in classifications_present:
        evidence_mode = "exact_replay"
    elif "counted_no_fit" in classifications_present:
        evidence_mode = "counted_no_fit"
    else:
        evidence_mode = "frontier_no_fit"
    fact = common.common_fact
    transition = common.portable_transition
    payload: dict[str, object] = {
        "schema_version": "yieldforge.m8-common-transition-lemma.v2",
        "fact_kind": "common_transition_lemma",
        "replay_input_id": common.source.replay_input_id,
        "replay_input_sha256": common.source.replay_input_sha256,
        "semantic_runtime_sha256": common.source.semantic_runtime_sha256,
        "stream_id": common.source.stream_id,
        "event_position": fact.event_position,
        "event_id": fact.event_id,
        "legacy_common_fact_sha256": fact.content_sha256,
        "portable_transition": transition,
        "problem_id": common.source.problem.problem_id,
        "problem_sha256": common.source.problem.content_sha256,
        "candidate_set_id": common.source.candidate_set.candidate_set_id,
        "candidate_set_sha256": common.source.candidate_set.content_sha256,
        "fit_config_sha256": common.source.fit_config_sha256,
        "search_config_sha256": _portable_search_config_sha256(common),
        "collision_backend": common.source.collision_backend,
        "jagua_executable_sha256": common.source.jagua_executable_sha256,
        "cursor_before_sha256": fact.cursor_before_sha256,
        "cursor_after_sha256": fact.cursor_after_sha256,
        "cursor_before_inventory_remnant_ids": tuple(
            item.remnant.remnant_id for item in fact.cursor_before.inventory
        ),
        "cursor_after_inventory_remnant_ids": tuple(
            item.remnant.remnant_id for item in fact.step.cursor.inventory
        ),
        "event_occurred_at": transition.event.occurred_at,
        "storage_interval_start": transition.event.storage_interval_start,
        "storage_interval_end": transition.event.storage_interval_end,
        "cursor_current_time": transition.cursor_after.current_time,
        "cursor_previous_release": transition.cursor_after.previous_release,
        "previous_common_lemma_ref": previous_common_lemma_ref,
        "baseline_fallback_cursor_sha256": (
            fact.cursor_before_sha256 if previous_common_lemma_ref is None else None
        ),
        "minimum_standard_candidate_ref": minimum.fact_sha256,
        "selected_catalog_action_id": fact.step.descriptor.action_id,
        "selected_materialized_action_id": fact.step.event.action.action_id,
        "selected_candidate_id": fact.step.event.action.candidate_id,
        "policy_name": fact.policy_rank.policy.value,
        "selected_comparison_key": _portable_policy_rank_components(
            fact.policy_rank.comparison_key
        ),
        "selected_decision_key": fact.policy_rank.decision_key,
        "selected_immediate_net_cost_bits": transition.selected_context.immediate_net_cost_bits,
        "event_net_cost_bits": transition.event.delta_costs.net_cost_bits,
        "candidate_scalar_refs": scalar_refs,
        "frontier_refs": frontier_refs,
        "standard_candidate_refs": tuple(item.fact_sha256 for item in standards),
        "inventory_classifications": classifications,
        "evidence_mode": evidence_mode,
        "translation_batch_refs": translation_refs,
        "exact_replay_reason": exact_reason,
    }
    return _content_addressed(facts.M8CommonTransitionLemmaV2, payload)


def _branch_delta(
    common: M7ReplayCursor,
    branch: M7ReplayCursor,
) -> BranchInventoryDelta:
    if (
        branch.next_event_position != common.next_event_position
        or branch.current_time != common.current_time
        or branch.timestamp_group_sequence != common.timestamp_group_sequence
        or branch.timestamp_subsequence != common.timestamp_subsequence
        or branch.previous_release != common.previous_release
    ):
        raise ValueError("M8 unchecked branch cursor metadata differs from common cursor")
    common_by_id = {item.remnant.remnant_id: item for item in common.inventory}
    branch_by_id = {item.remnant.remnant_id: item for item in branch.inventory}
    for remnant_id in set(common_by_id) & set(branch_by_id):
        if common_by_id[remnant_id] != branch_by_id[remnant_id]:
            raise ValueError("M8 shared branch/common remnant content differs")
    return BranchInventoryDelta(
        added=tuple(branch_by_id[key] for key in sorted(set(branch_by_id) - set(common_by_id))),
        removed=tuple(common_by_id[key] for key in sorted(set(common_by_id) - set(branch_by_id))),
    )


def _rejection_evidence(
    store: _FactStore,
    *,
    common: M8UncheckedProducerTransition,
    influence: M8UncheckedInfluenceCapture,
) -> tuple[facts.M8CandidateScalarGroupEvidenceV2, ...]:
    layout_by_candidate = _layout_by_candidate(common)
    scalar_refs = []
    impossible = []
    for rejection in influence.rejections:
        layout = layout_by_candidate.get(rejection.candidate_id)
        if layout is None:
            raise ValueError("M8 influence rejection candidate is absent from frozen source")
        scalar = store.scalar(
            semantic_runtime_sha256=common.source.semantic_runtime_sha256,
            stream_id=common.source.stream_id,
            layout=layout,
        )
        scalar_refs.append(scalar.fact_sha256)
        impossible.append(rejection.certificate.impossible)
    if not scalar_refs:
        raise ValueError("M8 influence rejection group lacks candidate scalar references")
    return (
        facts.M8CandidateScalarGroupEvidenceV2(
            evidence_kind="candidate_scalar_group",
            direction=influence.direction,
            remnant_id=influence.remnant_id,
            candidate_scalar_refs=tuple(sorted(set(scalar_refs))),
            all_candidates_impossible=all(impossible),
        ),
    )


def _search_evidence(
    store: _FactStore,
    *,
    common: M8UncheckedProducerTransition,
    influence: M8UncheckedInfluenceCapture,
) -> tuple[facts.M8SearchEvidenceV2, ...]:
    rows = []
    if len(influence.searches) != len(influence.translation_batches):
        raise ValueError("M8 influence searches and translation sources differ")
    for search, source in zip(
        influence.searches,
        influence.translation_batches,
        strict=True,
    ):
        translation = store.translation(common=common, capture=source)
        config = _portable_search_config(search.config)
        config_sha256 = (
            "sha256:"
            + hashlib.sha256(
                facts.canonical_semantic_json(config.model_dump(mode="json"))
            ).hexdigest()
        )
        rows.append(
            facts.M8SearchEvidenceV2(
                direction=influence.direction,
                remnant_id=influence.remnant_id,
                candidate_id=search.candidate_id,
                search_config=config,
                search_config_sha256=config_sha256,
                translation_batch_ref=translation.fact_sha256,
                generated_candidate_count=search.generated_candidate_count,
                duplicate_candidate_count=search.duplicate_candidate_count,
                evaluated_candidate_count=search.evaluated_candidate_count,
                budget_truncated=search.budget_truncated,
                result=search.status.value,
                selected_translation=(
                    _translation_point(search.translation)
                    if search.translation is not None
                    else None
                ),
            )
        )
    return tuple(
        sorted(
            rows,
            key=lambda item: (
                item.direction,
                item.remnant_id,
                item.candidate_id,
                item.search_config_sha256,
            ),
        )
    )


def _competitor_evidence(
    influence: M8UncheckedInfluenceCapture,
) -> facts.M8CompetitorEvidenceV2 | None:
    descriptor = influence.competitor
    context = influence.competitor_context
    rank = influence.competitor_rank
    if descriptor is None or context is None or rank is None:
        if descriptor is not None or context is not None or rank is not None:
            raise ValueError("M8 influence competitor source is incomplete")
        return None
    evidence = descriptor.evidence
    if evidence is None or descriptor.selected_remnant_id is None:
        raise ValueError("M8 influence competitor lacks exact remnant evidence")
    return facts.M8CompetitorEvidenceV2(
        direction=influence.direction,
        candidate_id=descriptor.candidate_id,
        catalog_action_id=descriptor.action_id,
        materialized_action_id=evidence.action_id,
        materialized_content_sha256=evidence.content_sha256,
        selected_remnant_id=descriptor.selected_remnant_id,
        action_kind=descriptor.kind.value,
        selected_stock_id=context.selected_stock_id,
        candidate_width_bits=facts.encode_canonical_f64(float(context.candidate_width)),
        immediate_net_cost_bits=facts.encode_canonical_f64(float(context.immediate_net_cost)),
        selected_remnant_age_hours_bits=facts.encode_canonical_f64(
            float(context.selected_remnant_age_hours)
        ),
        returned_regularity_bits=facts.encode_canonical_f64(float(context.returned_regularity)),
        known_order_lookahead_term_bits=facts.encode_canonical_f64(
            float(context.known_order_lookahead_term)
        ),
        policy_name=rank.policy.value,
        comparison_key=_portable_policy_rank_components(rank.comparison_key),
        decision_key=rank.decision_key,
    )


def _influence_fact(
    store: _FactStore,
    *,
    common: M8UncheckedProducerTransition,
    common_lemma: facts.M8CommonTransitionLemmaV2,
    branch: M8UncheckedBranchTraversalCapture,
    event: M8UncheckedBranchEventCapture,
    branch_cursor_before: M7ReplayCursor,
) -> facts.M8InfluenceFactV2:
    delta = _branch_delta(common.common_fact.cursor_before, branch_cursor_before)
    sources = event.influences
    if sources and any(item.delta != delta for item in sources):
        raise ValueError("M8 passive influence delta differs from branch/common state")
    if event.classification == "exact_transition":
        rejection_rows: tuple[facts.M8RejectionEvidenceClaimV2, ...] = ()
        search_rows: tuple[facts.M8SearchEvidenceV2, ...] = ()
        competitor_rows: tuple[facts.M8CompetitorEvidenceV2, ...] = ()
        evidence_mode = "exact_transition"
        if event.exact_step is None:
            raise ValueError("M8 exact transition lacks its exact source step")
        branch_catalog_action_id = event.exact_step.descriptor.action_id
    else:
        rejection_rows = tuple(
            sorted(
                (
                    row
                    for source in sources
                    for row in _rejection_evidence(store, common=common, influence=source)
                ),
                key=lambda item: (item.direction, item.remnant_id),
            )
        )
        search_rows = tuple(
            sorted(
                (
                    row
                    for source in sources
                    for row in _search_evidence(store, common=common, influence=source)
                ),
                key=lambda item: (
                    item.direction,
                    item.remnant_id,
                    item.candidate_id,
                    item.search_config_sha256,
                ),
            )
        )
        competitor_rows = tuple(
            sorted(
                (
                    competitor
                    for source in sources
                    if (competitor := _competitor_evidence(source)) is not None
                ),
                key=lambda item: (
                    item.direction,
                    item.selected_remnant_id,
                    item.catalog_action_id,
                    item.materialized_action_id,
                ),
            )
        )
        if event.classification == "state_rejoin":
            evidence_mode = "state_rejoin"
        elif event.classification == "policy_dominated":
            evidence_mode = "policy_dominated_exact_check"
        elif search_rows:
            evidence_mode = "exact_transition"
        else:
            evidence_mode = "scalar_no_fit"
        branch_catalog_action_id = common.common_fact.step.descriptor.action_id
    payload: dict[str, object] = {
        "schema_version": "yieldforge.m8-influence-fact.v2",
        "fact_kind": "influence",
        "semantic_runtime_sha256": common.source.semantic_runtime_sha256,
        "stream_id": common.source.stream_id,
        "event_position": event.event_position,
        "common_lemma_ref": common_lemma.fact_sha256,
        "root_action_id": branch.initial_step.event.action.action_id,
        "common_catalog_action_id": common.common_fact.step.descriptor.action_id,
        "common_materialized_action_id": common.common_fact.step.event.action.action_id,
        "branch_catalog_action_id": branch_catalog_action_id,
        "branch_materialized_action_id": event.branch_action_id,
        "state_before_sha256": event.state_before_sha256,
        "state_after_sha256": event.state_after_sha256,
        "inventory_delta": facts.M8InventoryDeltaV2(
            removed_remnant_ids=tuple(item.remnant.remnant_id for item in delta.removed),
            added_remnant_ids=tuple(item.remnant.remnant_id for item in delta.added),
        ),
        "classification": event.classification,
        "evidence_mode": evidence_mode,
        "rejection_evidence": rejection_rows,
        "search_evidence": search_rows,
        "competitor_evidence": competitor_rows,
    }
    return _content_addressed(facts.M8InfluenceFactV2, payload)


def _bundle_payload(
    *,
    provenance: facts.M8BundleProvenanceV2,
    store: _FactStore,
    common_lemmas: tuple[facts.M8CommonTransitionLemmaV2, ...],
    influence_facts: tuple[facts.M8InfluenceFactV2, ...],
    action_roots: tuple[facts.M8ActionRootV2, ...],
) -> dict[str, object]:
    return {
        "schema_version": "yieldforge.m8-unchecked-fact-bundle.v2",
        "bundle_kind": "unchecked_fact_bundle",
        "provenance": provenance,
        "translation_batches": tuple(
            sorted(store.translations.values(), key=lambda item: item.fact_sha256)
        ),
        "candidate_scalar_facts": tuple(
            sorted(store.scalars.values(), key=lambda item: item.fact_sha256)
        ),
        "frontier_facts": tuple(
            sorted(store.frontiers.values(), key=lambda item: item.fact_sha256)
        ),
        "standard_candidate_facts": tuple(
            sorted(
                store.standards.values(),
                key=lambda item: (
                    item.stream_id,
                    item.event_position,
                    item.profile_position,
                    item.fact_sha256,
                ),
            )
        ),
        "common_lemmas": common_lemmas,
        "influence_facts": tuple(
            sorted(
                influence_facts,
                key=lambda item: (
                    item.stream_id,
                    item.event_position,
                    item.root_action_id,
                    item.fact_sha256,
                ),
            )
        ),
        "action_roots": tuple(
            sorted(
                action_roots,
                key=lambda item: (item.stream_id, item.action_id, item.fact_sha256),
            )
        ),
    }


def score_unchecked_fact_bundle(
    request: M8UncheckedBundleRequest,
) -> M8UncheckedBundleGenerationResult:
    """Generate one calibration bundle without creating or validating proof authority."""

    if type(request) is not M8UncheckedBundleRequest:
        raise TypeError("M8 unchecked fact generation requires an exact bundle request")
    captured_request = _capture_unchecked_bundle_request_source(request)
    oracle_request = captured_request.oracle_request
    store = _FactStore()
    with (
        profile_phase("fact_bundle_prepared_context_session"),
        _prepare_m8_generator_context(oracle_request) as context,
    ):
        _require_captured_unchecked_bundle_bindings(
            captured_request,
            oracle_request=context._request,  # noqa: SLF001
            semantic_runtime_sha256=context._authority.semantic_sha256,  # noqa: SLF001
        )
        with profile_phase("fact_bundle_unchecked_traversal"):
            capture = _capture_prepared_unchecked_traversal(context)
        with profile_phase("fact_bundle_layer_assembly"):
            common_lemmas_list: list[facts.M8CommonTransitionLemmaV2] = []
            previous_common_lemma_ref: str | None = None
            for common in capture.common_transitions:
                lemma = _common_lemma(
                    store,
                    common=common,
                    previous_common_lemma_ref=previous_common_lemma_ref,
                )
                common_lemmas_list.append(lemma)
                previous_common_lemma_ref = lemma.fact_sha256
            common_lemmas = tuple(common_lemmas_list)

            if len(common_lemmas) != len(capture.common_transitions):
                raise ValueError("M8 unchecked common lemma coverage differs from source traversal")
            start_state_sha256 = m7_cursor_sha256(context._request.cursor)  # noqa: SLF001
            influence_rows: list[facts.M8InfluenceFactV2] = []
            roots: list[facts.M8ActionRootV2] = []
            for branch in capture.branches:
                if len(branch.events) != len(common_lemmas):
                    raise ValueError("M8 unchecked branch/common event coverage differs")
                branch_cursor_before = branch.initial_step.cursor
                branch_influences = []
                for common, lemma, event in zip(
                    capture.common_transitions,
                    common_lemmas,
                    branch.events,
                    strict=True,
                ):
                    influence = _influence_fact(
                        store,
                        common=common,
                        common_lemma=lemma,
                        branch=branch,
                        event=event,
                        branch_cursor_before=branch_cursor_before,
                    )
                    branch_influences.append(influence)
                    influence_rows.append(influence)
                    branch_cursor_before = event.branch_after
                if branch_cursor_before != branch.cursor:
                    raise ValueError("M8 unchecked terminal branch cursor differs from event chain")
                terminal = run_m7_continuation(
                    context._request.runtime,  # noqa: SLF001
                    cursor=branch.cursor,
                    stop_event_position=context._stop_event_position,  # noqa: SLF001
                )
                if terminal.events:
                    raise ValueError("M8 terminal reconciliation replayed missing branch events")
                root = _content_addressed(
                    facts.M8ActionRootV2,
                    {
                        "schema_version": "yieldforge.m8-action-root.v2",
                        "fact_kind": "action_root",
                        "semantic_runtime_sha256": context._authority.semantic_sha256,  # noqa: SLF001
                        "stream_id": context._request.runtime.replay_input.stream_id,  # noqa: SLF001
                        "action_id": branch.initial_step.event.action.action_id,
                        "catalog_action_id": branch.descriptor.action_id,
                        "baseline_action_id": context._fallback_step.event.action.action_id,  # noqa: SLF001
                        "baseline_catalog_action_id": context._fallback_step.descriptor.action_id,  # noqa: SLF001
                        "start_event_position": context._catalog.event_position,  # noqa: SLF001
                        "stop_event_position": context._stop_event_position,  # noqa: SLF001
                        "suffix_sha256": context._suffix_sha256,  # noqa: SLF001
                        "start_state_sha256": start_state_sha256,
                        "initial_state_after_sha256": m7_cursor_sha256(branch.initial_step.cursor),
                        "final_state_sha256": m7_cursor_sha256(branch.cursor),
                        "common_lemma_refs": tuple(item.fact_sha256 for item in common_lemmas),
                        "influence_fact_refs": tuple(
                            item.fact_sha256 for item in branch_influences
                        ),
                        "final_net_cost_bits": facts.encode_canonical_f64(
                            float(terminal.final_costs.net_cost)
                        ),
                    },
                )
                roots.append(root)

            replay_input = context._request.runtime.replay_input  # noqa: SLF001
            dimension = replay_input.instances[0]
            provenance = facts.M8BundleProvenanceV2(
                replay_input_id=replay_input.input_id,
                replay_input_sha256=replay_input.content_sha256,
                semantic_runtime_sha256=context._authority.semantic_sha256,  # noqa: SLF001
                stream_id=replay_input.stream_id,
                stream_sha256=replay_input.stream_sha256,
                regime=dimension.regime.value,
                temporal_seed=dimension.temporal_seed,
                suffix_sha256=context._suffix_sha256,  # noqa: SLF001
                freeze_id=captured_request.freeze_id,
                freeze_sha256=captured_request.freeze_sha256,
                evaluation_partition_opened=False,
            )
            payload = _bundle_payload(
                provenance=provenance,
                store=store,
                common_lemmas=common_lemmas,
                influence_facts=tuple(influence_rows),
                action_roots=tuple(roots),
            )
        with profile_phase("fact_bundle_hash_validation"):
            bundle = facts.M8UncheckedFactBundleV2.model_validate(
                {**payload, "bundle_sha256": facts.m8_bundle_sha256(payload)},
                strict=True,
            )
    with profile_phase("fact_bundle_semantic_serialization"):
        serialization_started = perf_counter()
        semantic_bytes = unchecked_fact_bundle_semantic_bytes(bundle)
        serialization_seconds = perf_counter() - serialization_started
    with profile_phase("fact_bundle_strict_roundtrip"):
        strict_loaded = facts.M8UncheckedFactBundleV2.model_validate_json(
            semantic_bytes,
            strict=True,
        )
        if strict_loaded != bundle:
            raise ValueError("M8 unchecked bundle semantic serialization does not round trip")
        _require_unchecked_bundle_request_source_stable(
            captured_request,
            request=request,
            bundle=strict_loaded,
        )
        bundle = strict_loaded
    with profile_phase("fact_bundle_telemetry"):
        telemetry = M8BundleGenerationTelemetry(
            semantic_serialized_bytes=len(semantic_bytes),
            serialization_seconds=float(serialization_seconds),
            portable_transition_serialized_bytes=tuple(
                len(facts.canonical_semantic_json(item.portable_transition.model_dump(mode="json")))
                for item in bundle.common_lemmas
            ),
            common_event_count=len(bundle.common_lemmas),
            action_root_count=len(bundle.action_roots),
            counted_inventory_evidence_count=sum(
                item.classification == "counted_no_fit"
                for lemma in bundle.common_lemmas
                for item in lemma.inventory_classifications
            ),
            translation_batch_count=len(bundle.translation_batches),
            exact_transition_count=sum(
                item.classification == "exact_transition" for item in bundle.influence_facts
            ),
        )
    return M8UncheckedBundleGenerationResult(
        bundle=bundle,
        telemetry=telemetry,
    )


__all__ = [
    "M8BundleGenerationTelemetry",
    "M8UncheckedBundleGenerationResult",
    "M8UncheckedBundleRequest",
    "score_unchecked_fact_bundle",
    "unchecked_fact_bundle_semantic_bytes",
]
