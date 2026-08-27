"""Strict portable contracts for explicitly unchecked M8 fact bundles.

These contracts are transport and structural-validation types only.  They do
not generate facts, validate mathematical truth, or confer proof authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, NoReturn, Self

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)
from pydantic_core import PydanticCustomError

from yieldforge.baseline.contracts import BaselineContractModel

_FACT_DOMAIN = b"yieldforge.m8.fact.v2\0"
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_REMNANT_ID_PATTERN = r"^yfrm-[0-9a-f]{24}$"
_ACTION_ID_PATTERN = r"^yfm7a-[0-9a-f]{24}$"
_STREAM_ID_PATTERN = r"^yfts-[0-9a-f]{24}$"
_UTC_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$")
_F64_PATTERN = re.compile(r"^f64:[0-9a-f]{16}$")


class _M8StructuralErrorCode(StrEnum):
    """Stable machine-readable codes for unchecked bundle structure failures."""

    BUNDLE_HASH_MISMATCH = "m8_bundle_hash_mismatch"
    FIXED_LAYER_ORDER = "m8_fixed_layer_order"
    DUPLICATE_FACT = "m8_duplicate_fact"
    DUPLICATE_IDENTITY = "m8_duplicate_identity"
    CONTEXT_MISMATCH = "m8_context_mismatch"
    DANGLING_REFERENCE = "m8_dangling_reference"
    PARTITION_MISMATCH = "m8_partition_mismatch"
    EVENT_ORDER_MISMATCH = "m8_event_order_mismatch"
    CURSOR_CHAIN_MISMATCH = "m8_cursor_chain_mismatch"
    INCOMPLETE_EVIDENCE = "m8_incomplete_evidence"
    CONFIGURATION_MISMATCH = "m8_configuration_mismatch"
    STANDARD_PROFILE_MISMATCH = "m8_standard_profile_mismatch"
    POLICY_MINIMUM_MISMATCH = "m8_policy_minimum_mismatch"
    REPLAY_CONTEXT_MISMATCH = "m8_replay_context_mismatch"
    ACTION_BINDING_MISMATCH = "m8_action_binding_mismatch"
    TRANSLATION_MISMATCH = "m8_translation_mismatch"
    STATE_CHAIN_MISMATCH = "m8_state_chain_mismatch"
    UNUSED_FACT = "m8_unused_fact"
    ROOT_CONTEXT_MISMATCH = "m8_root_context_mismatch"


def _raise_structural_error(
    code: _M8StructuralErrorCode,
    message: str,
    *,
    fact_sha256: str | None = None,
    bundle_sha256: str | None = None,
    dependency_sha256: str | None = None,
) -> NoReturn:
    """Raise one stable first-failure error with an attributable semantic owner."""

    if (fact_sha256 is None) == (bundle_sha256 is None):
        raise RuntimeError("structural error requires exactly one fact or bundle identity")
    context = (
        {"fact_sha256": fact_sha256}
        if fact_sha256 is not None
        else {"bundle_sha256": bundle_sha256}
    )
    if dependency_sha256 is not None:
        context["dependency_sha256"] = dependency_sha256
    raise PydanticCustomError(code.value, message, context)


def encode_canonical_f64(value: float) -> str:
    """Encode one finite Python float as canonical big-endian IEEE-754 bits."""

    if type(value) is not float:
        raise TypeError("canonical f64 encoding requires an exact float")
    if not math.isfinite(value):
        raise ValueError("canonical f64 encoding requires a finite value")
    normalized = 0.0 if value == 0.0 else value
    return f"f64:{struct.pack('>d', normalized).hex()}"


def decode_canonical_f64(value: str) -> float:
    """Decode one canonical finite f64 bit string, rejecting aliases."""

    if type(value) is not str:
        raise TypeError("canonical f64 decoding requires an exact string")
    if _F64_PATTERN.fullmatch(value) is None:
        raise ValueError("noncanonical f64 bit string")
    bits = value.removeprefix("f64:")
    if bits == "8000000000000000":
        raise ValueError("negative zero is not a canonical f64 encoding")
    decoded = struct.unpack(">d", bytes.fromhex(bits))[0]
    if not math.isfinite(decoded):
        raise ValueError("canonical f64 values must be finite")
    if encode_canonical_f64(decoded) != value:
        raise ValueError("noncanonical f64 bit string")
    return decoded


def encode_canonical_utc(value: datetime) -> str:
    """Encode an aware datetime as one exact UTC microsecond string."""

    if type(value) is not datetime:
        raise TypeError("canonical UTC encoding requires an exact datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("canonical UTC datetime must be timezone-aware")
    canonical = value.astimezone(UTC)
    return canonical.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def decode_canonical_utc(value: str) -> datetime:
    """Decode one canonical UTC microsecond string, rejecting aliases."""

    if type(value) is not str:
        raise TypeError("canonical UTC decoding requires an exact string")
    if _UTC_PATTERN.fullmatch(value) is None:
        raise ValueError("noncanonical UTC datetime")
    try:
        decoded = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("invalid canonical UTC datetime") from error
    if encode_canonical_utc(decoded) != value:
        raise ValueError("noncanonical UTC datetime")
    return decoded


def _validate_f64(value: str) -> str:
    decode_canonical_f64(value)
    return value


def _validate_utc(value: str) -> str:
    decode_canonical_utc(value)
    return value


type M8CanonicalF64 = Annotated[StrictStr, AfterValidator(_validate_f64)]
type M8CanonicalUtc = Annotated[StrictStr, AfterValidator(_validate_utc)]
type M8Sha256 = Annotated[StrictStr, Field(pattern=_SHA256_PATTERN)]
type M8RawSha256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


def _json_value(value: object) -> object:
    if isinstance(value, BaselineContractModel):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        converted: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("canonical semantic JSON requires string mapping keys")
            converted[key] = _json_value(item)
        return converted
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or type(value) in {str, int, bool}:
        return value
    if type(value) is float:
        raise TypeError("semantic floats must use canonical f64 bit strings")
    raise TypeError(f"unsupported canonical semantic JSON value: {type(value).__name__}")


def canonical_semantic_json(payload: Mapping[str, object]) -> bytes:
    """Return deterministic UTF-8 JSON for a typed semantic payload."""

    if not isinstance(payload, Mapping):
        raise TypeError("canonical semantic payload must be a mapping")
    return json.dumps(
        _json_value(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def m8_fact_sha256(fact_kind: str, payload: Mapping[str, object]) -> str:
    """Hash one fact with the frozen M8 v2 domain separator."""

    if type(fact_kind) is not str or not fact_kind:
        raise TypeError("M8 fact kind must be a nonempty exact string")
    semantic = dict(payload)
    semantic.pop("fact_sha256", None)
    semantic.pop("fact_kind", None)
    digest = hashlib.sha256(
        _FACT_DOMAIN + fact_kind.encode("utf-8") + b"\0" + canonical_semantic_json(semantic)
    ).hexdigest()
    return f"sha256:{digest}"


def _bundle_hash_payload(payload: Mapping[str, object]) -> dict[str, object]:
    semantic = dict(payload)
    semantic.pop("bundle_sha256", None)
    semantic.pop("bundle_kind", None)
    for field in (
        "translation_batches",
        "candidate_scalar_facts",
        "frontier_facts",
        "standard_candidate_facts",
        "common_lemmas",
        "influence_facts",
        "action_roots",
    ):
        entries = semantic.pop(field, ())
        if not isinstance(entries, Sequence):
            raise TypeError(f"M8 bundle {field} must be a sequence")
        references: list[str] = []
        for entry in entries:
            if isinstance(entry, BaselineContractModel):
                reference = entry.model_dump(mode="python").get("fact_sha256")
            elif isinstance(entry, Mapping):
                reference = entry.get("fact_sha256")
            else:
                raise TypeError(f"M8 bundle {field} entry must be a typed fact")
            if type(reference) is not str:
                raise TypeError(f"M8 bundle {field} entry lacks a fact SHA-256")
            references.append(reference)
        semantic[f"{field}_sha256"] = references
    return semantic


def m8_bundle_sha256(payload: Mapping[str, object]) -> str:
    """Hash one fixed-layer bundle root over provenance and ordered fact hashes."""

    return m8_fact_sha256("unchecked_fact_bundle", _bundle_hash_payload(payload))


def _sorted_unique(values: tuple[str, ...], *, label: str) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")


class _M8FactV2(BaselineContractModel):
    """Content-addressed base shared only by portable v2 fact contracts."""

    model_config = ConfigDict(revalidate_instances="always")

    fact_kind: StrictStr
    fact_sha256: M8Sha256
    _EXPECTED_KIND: ClassVar[str]

    @model_validator(mode="after")
    def require_content_address(self) -> Self:
        if self.fact_kind != self._EXPECTED_KIND:
            raise ValueError("M8 fact kind differs from its typed fixed layer")
        payload = self.model_dump(mode="json", exclude={"fact_sha256"})
        expected = m8_fact_sha256(self.fact_kind, payload)
        if self.fact_sha256 != expected:
            raise ValueError("M8 fact SHA-256 does not match semantic content")
        return self


class M8TranslationPointV2(BaselineContractModel):
    """One ordered translation coordinate with exact f64 identities."""

    x_bits: M8CanonicalF64
    y_bits: M8CanonicalF64


class M8PortableTranslationBatch(_M8FactV2):
    """Unchecked ordered translation/count evidence without collision claims."""

    _EXPECTED_KIND = "translation_batch"

    schema_version: Literal["yieldforge.m8-portable-translation-batch.v2"] = (
        "yieldforge.m8-portable-translation-batch.v2"
    )
    fact_kind: Literal["translation_batch"] = "translation_batch"
    semantic_runtime_sha256: M8Sha256
    stream_id: StrictStr = Field(pattern=_STREAM_ID_PATTERN)
    event_position: StrictInt = Field(ge=0)
    remnant_id: StrictStr = Field(pattern=_REMNANT_ID_PATTERN)
    candidate_id: StrictStr = Field(min_length=1)
    fit_config_sha256: M8Sha256
    search_config_sha256: M8Sha256
    source_order: tuple[
        Literal["bbox_alignments"],
        Literal["vertex_alignments"],
        Literal["uniform_grid"],
    ] = ("bbox_alignments", "vertex_alignments", "uniform_grid")
    translations: tuple[M8TranslationPointV2, ...]
    generated_candidate_count: StrictInt = Field(ge=0)
    duplicate_candidate_count: StrictInt = Field(ge=0)
    evaluated_candidate_count: StrictInt = Field(ge=0)
    budget_truncated: StrictBool

    @model_validator(mode="after")
    def require_complete_ordered_counts(self) -> Self:
        if self.evaluated_candidate_count != len(self.translations):
            raise ValueError("translation evaluated count differs from ordered evidence")
        expected_generated = self.evaluated_candidate_count + int(self.budget_truncated)
        if self.generated_candidate_count != expected_generated:
            raise ValueError("translation generated/evaluated count shape is invalid")
        points = tuple((item.x_bits, item.y_bits) for item in self.translations)
        if len(points) != len(set(points)):
            raise ValueError("translation evidence contains duplicate coordinates")
        return self


class M8CandidateScalarFactV2(_M8FactV2):
    """One portable frozen candidate necessary-fit vector."""

    _EXPECTED_KIND = "candidate_scalar"

    schema_version: Literal["yieldforge.m8-candidate-scalar-fact.v2"] = (
        "yieldforge.m8-candidate-scalar-fact.v2"
    )
    fact_kind: Literal["candidate_scalar"] = "candidate_scalar"
    semantic_runtime_sha256: M8Sha256
    stream_id: StrictStr = Field(pattern=_STREAM_ID_PATTERN)
    problem_id: StrictStr = Field(pattern=r"^yfm7p-[0-9a-f]{24}$")
    problem_sha256: M8Sha256
    candidate_set_id: StrictStr = Field(pattern=r"^yfm7c-[0-9a-f]{24}$")
    candidate_set_sha256: M8Sha256
    candidate_id: StrictStr = Field(min_length=1)
    source_transform_sha256: M8Sha256
    material_partition: Literal["temporal_event"] = "temporal_event"
    fit_config_sha256: M8Sha256
    layout_area_bits: M8CanonicalF64
    layout_width_bits: M8CanonicalF64
    layout_height_bits: M8CanonicalF64

    @model_validator(mode="after")
    def require_scalar_ranges(self) -> Self:
        dimensions = (
            self.layout_area_bits,
            self.layout_width_bits,
            self.layout_height_bits,
        )
        if any(decode_canonical_f64(item) <= 0.0 for item in dimensions):
            raise ValueError("candidate scalar dimensions must be positive")
        return self


class M8DominanceEvidenceV2(BaselineContractModel):
    """Typed frontier dominance relationship between scalar leaves."""

    dominated_candidate_scalar_ref: M8Sha256
    retained_candidate_scalar_ref: M8Sha256
    relation: Literal["componentwise_necessary_fit"] = "componentwise_necessary_fit"

    @model_validator(mode="after")
    def require_distinct_facts(self) -> Self:
        if self.dominated_candidate_scalar_ref == self.retained_candidate_scalar_ref:
            raise ValueError("frontier dominance requires distinct scalar facts")
        return self


class M8FrontierFactV2(_M8FactV2):
    """Complete sorted scalar set plus frontier membership/dominance evidence."""

    _EXPECTED_KIND = "frontier"

    schema_version: Literal["yieldforge.m8-frontier-fact.v2"] = "yieldforge.m8-frontier-fact.v2"
    fact_kind: Literal["frontier"] = "frontier"
    semantic_runtime_sha256: M8Sha256
    stream_id: StrictStr = Field(pattern=_STREAM_ID_PATTERN)
    problem_id: StrictStr = Field(pattern=r"^yfm7p-[0-9a-f]{24}$")
    problem_sha256: M8Sha256
    candidate_set_id: StrictStr = Field(pattern=r"^yfm7c-[0-9a-f]{24}$")
    candidate_set_sha256: M8Sha256
    material_partition: Literal["temporal_event"] = "temporal_event"
    fit_config_sha256: M8Sha256
    candidate_scalar_refs: tuple[M8Sha256, ...] = Field(min_length=1)
    retained_candidate_scalar_refs: tuple[M8Sha256, ...] = Field(min_length=1)
    dominance_evidence: tuple[M8DominanceEvidenceV2, ...]

    @model_validator(mode="after")
    def require_deterministic_frontier(self) -> Self:
        _sorted_unique(self.candidate_scalar_refs, label="frontier candidate-scalar references")
        _sorted_unique(
            self.retained_candidate_scalar_refs,
            label="frontier retained candidate-scalar references",
        )
        dominance_keys = tuple(
            (
                item.dominated_candidate_scalar_ref,
                item.retained_candidate_scalar_ref,
                item.relation,
            )
            for item in self.dominance_evidence
        )
        if dominance_keys != tuple(sorted(set(dominance_keys))):
            raise ValueError("frontier dominance evidence must be sorted and unique")
        allowed = set(self.candidate_scalar_refs)
        if any(
            item.dominated_candidate_scalar_ref not in allowed
            or item.retained_candidate_scalar_ref not in set(self.retained_candidate_scalar_refs)
            for item in self.dominance_evidence
        ):
            raise ValueError("frontier dominance evidence references a foreign scalar fact")
        classified = set(self.retained_candidate_scalar_refs) | {
            item.dominated_candidate_scalar_ref for item in self.dominance_evidence
        }
        dominated = tuple(item.dominated_candidate_scalar_ref for item in self.dominance_evidence)
        if len(dominated) != len(set(dominated)):
            raise ValueError("frontier assigns one dominated scalar more than once")
        if set(self.retained_candidate_scalar_refs) & set(dominated):
            raise ValueError("frontier retained and dominated scalar sets must be disjoint")
        if classified != allowed:
            raise ValueError("frontier does not classify every scalar fact exactly once")
        return self


type M8PolicyNameV2 = Literal[
    "myopic_geometry",
    "remnant_first",
    "net_cost",
    "age_regularity",
    "known_order_lookahead",
]


class M8PolicyRankComponentV2(BaselineContractModel):
    """One strictly typed member of a heterogeneous frozen policy rank."""

    component_kind: Literal["f64", "int", "string"]
    f64_bits: M8CanonicalF64 | None = None
    int_value: StrictInt | None = None
    string_value: StrictStr | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_exact_component_value(self) -> Self:
        present = (
            self.f64_bits is not None,
            self.int_value is not None,
            self.string_value is not None,
        )
        expected = {
            "f64": (True, False, False),
            "int": (False, True, False),
            "string": (False, False, True),
        }[self.component_kind]
        if present != expected:
            raise ValueError("policy rank component kind and typed value differ")
        return self

    def orderable_value(self) -> float | int | str:
        """Return the exact homogeneous value after the key shape is validated."""

        if self.component_kind == "f64":
            if self.f64_bits is None:  # pragma: no cover - validator closes this branch.
                raise AssertionError("validated f64 rank component lacks its value")
            return decode_canonical_f64(self.f64_bits)
        if self.component_kind == "int":
            if self.int_value is None:  # pragma: no cover - validator closes this branch.
                raise AssertionError("validated integer rank component lacks its value")
            return self.int_value
        if self.string_value is None:  # pragma: no cover - validator closes this branch.
            raise AssertionError("validated string rank component lacks its value")
        return self.string_value


_POLICY_RANK_SHAPES: dict[M8PolicyNameV2, tuple[str, ...]] = {
    "myopic_geometry": ("f64", "string", "string", "string"),
    "remnant_first": ("int", "f64", "string", "string", "string"),
    "net_cost": ("f64", "string", "string", "string"),
    "age_regularity": (
        "f64",
        "f64",
        "f64",
        "string",
        "string",
        "string",
    ),
    "known_order_lookahead": ("f64", "f64", "string", "string", "string"),
}


def _policy_rank_value(
    policy_name: M8PolicyNameV2,
    components: tuple[M8PolicyRankComponentV2, ...],
) -> tuple[float | int | str, ...]:
    expected = _POLICY_RANK_SHAPES[policy_name]
    observed = tuple(item.component_kind for item in components)
    if observed != expected:
        raise ValueError("M8 policy comparison key has the wrong registered shape")
    return tuple(item.orderable_value() for item in components)


class M8StandardCandidateFactV2(_M8FactV2):
    """One complete ordered standard action profile, context, and policy rank."""

    _EXPECTED_KIND = "standard_candidate"

    schema_version: Literal["yieldforge.m8-standard-candidate-fact.v2"] = (
        "yieldforge.m8-standard-candidate-fact.v2"
    )
    fact_kind: Literal["standard_candidate"] = "standard_candidate"
    semantic_runtime_sha256: M8Sha256
    stream_id: StrictStr = Field(pattern=_STREAM_ID_PATTERN)
    event_position: StrictInt = Field(ge=0)
    profile_position: StrictInt = Field(ge=0)
    candidate_id: StrictStr = Field(min_length=1)
    catalog_action_id: StrictStr = Field(min_length=1)
    materialized_action_id: StrictStr | None = Field(default=None, pattern=_ACTION_ID_PATTERN)
    action_kind: Literal["open_standard_sheet"] = "open_standard_sheet"
    selected_stock_id: Literal["current_standard_sheet"] = "current_standard_sheet"
    policy_name: M8PolicyNameV2
    candidate_width_bits: M8CanonicalF64
    parent_remnant_area_bits: M8CanonicalF64
    placed_area_bits: M8CanonicalF64
    process_loss_area_bits: M8CanonicalF64
    retained_child_area_bits: M8CanonicalF64
    scrap_area_bits: M8CanonicalF64
    reconciliation_delta_bits: M8CanonicalF64
    accounting_area_tolerance_bits: M8CanonicalF64
    purchase_cost_bits: M8CanonicalF64
    storage_cost_bits: M8CanonicalF64
    return_handling_cost_bits: M8CanonicalF64
    retrieval_handling_cost_bits: M8CanonicalF64
    scrap_proceeds_bits: M8CanonicalF64
    terminal_scrap_credit_bits: M8CanonicalF64
    immediate_net_cost_bits: M8CanonicalF64
    returned_remnant_count: StrictInt = Field(ge=0)
    returned_regularity_bits: M8CanonicalF64
    selected_remnant_age_hours_bits: M8CanonicalF64
    known_order_lookahead_term_bits: M8CanonicalF64
    comparison_key: tuple[M8PolicyRankComponentV2, ...] = Field(min_length=1)
    decision_key: tuple[StrictStr, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_complete_profile_context(self) -> Self:
        if decode_canonical_f64(self.candidate_width_bits) <= 0.0:
            raise ValueError("standard candidate width must be positive")
        nonnegative = (
            self.parent_remnant_area_bits,
            self.placed_area_bits,
            self.process_loss_area_bits,
            self.retained_child_area_bits,
            self.scrap_area_bits,
            self.reconciliation_delta_bits,
            self.purchase_cost_bits,
            self.storage_cost_bits,
            self.return_handling_cost_bits,
            self.retrieval_handling_cost_bits,
            self.scrap_proceeds_bits,
            self.terminal_scrap_credit_bits,
            self.selected_remnant_age_hours_bits,
        )
        if any(decode_canonical_f64(item) < 0.0 for item in nonnegative):
            raise ValueError("standard candidate profile terms must be nonnegative")
        if decode_canonical_f64(self.parent_remnant_area_bits) <= 0.0 or (
            decode_canonical_f64(self.accounting_area_tolerance_bits) <= 0.0
        ):
            raise ValueError("standard candidate accounting bounds must be positive")
        parent = decode_canonical_f64(self.parent_remnant_area_bits)
        accounted = sum(
            decode_canonical_f64(item)
            for item in (
                self.placed_area_bits,
                self.process_loss_area_bits,
                self.retained_child_area_bits,
                self.scrap_area_bits,
            )
        )
        delta = decode_canonical_f64(self.reconciliation_delta_bits)
        tolerance = decode_canonical_f64(self.accounting_area_tolerance_bits)
        if delta != abs(parent - accounted) or delta > tolerance:
            raise ValueError("standard candidate material accounting does not reconcile")
        regularity = decode_canonical_f64(self.returned_regularity_bits)
        if not 0.0 <= regularity <= 1.0:
            raise ValueError("standard candidate regularity must be in [0, 1]")
        if decode_canonical_f64(self.known_order_lookahead_term_bits) != 0.0:
            raise ValueError("M8 standard candidate lookahead term must remain zero")
        if f"action_id={self.catalog_action_id}" not in self.decision_key:
            raise ValueError("standard candidate decision key must bind its catalog action")
        _policy_rank_value(self.policy_name, self.comparison_key)
        return self


class M8PortableCostLedgerV2(BaselineContractModel):
    """Every exact M7 cost-ledger term encoded as canonical f64 bits."""

    purchase_cost_bits: M8CanonicalF64
    storage_cost_bits: M8CanonicalF64
    return_handling_cost_bits: M8CanonicalF64
    retrieval_handling_cost_bits: M8CanonicalF64
    scrap_proceeds_bits: M8CanonicalF64
    terminal_scrap_credit_bits: M8CanonicalF64
    net_cost_bits: M8CanonicalF64

    @model_validator(mode="after")
    def require_nonnegative_terms(self) -> Self:
        terms = (
            self.purchase_cost_bits,
            self.storage_cost_bits,
            self.return_handling_cost_bits,
            self.retrieval_handling_cost_bits,
            self.scrap_proceeds_bits,
            self.terminal_scrap_credit_bits,
        )
        if any(decode_canonical_f64(item) < 0.0 for item in terms):
            raise ValueError("portable cost-ledger terms must be nonnegative")
        return self


class M8PortablePolygonV2(BaselineContractModel):
    """Exact canonical polygon bytes plus their frozen identity and area."""

    schema_version: Literal["yieldforge.m8-portable-polygon.v2"] = (
        "yieldforge.m8-portable-polygon.v2"
    )
    source_schema_version: Literal["yieldforge.canonical-polygon.v1"] = (
        "yieldforge.canonical-polygon.v1"
    )
    wkb_hex: StrictStr = Field(min_length=2, pattern=r"^[0-9a-f]+$")
    polygon_sha256: M8RawSha256
    area_bits: M8CanonicalF64

    @model_validator(mode="after")
    def require_polygon_shape(self) -> Self:
        if len(self.wkb_hex) % 2:
            raise ValueError("portable polygon WKB must have even length")
        if decode_canonical_f64(self.area_bits) <= 0.0:
            raise ValueError("portable polygon area must be positive")
        return self


class M8PortableMaterialIdentityV2(BaselineContractModel):
    """Exact M0 material-compatibility identity carried by a portable remnant."""

    schema_version: Literal["yieldforge.m8-portable-material-identity.v2"] = (
        "yieldforge.m8-portable-material-identity.v2"
    )
    source_schema_version: Literal["yieldforge.material-identity.v1"] = (
        "yieldforge.material-identity.v1"
    )
    material_code: StrictStr = Field(min_length=1)
    grade: StrictStr = Field(min_length=1)
    thickness: StrictStr = Field(min_length=1)
    surface: StrictStr = Field(min_length=1)
    grain: StrictStr = Field(min_length=1)
    provenance: Literal["observed", "generated", "assumed"]


class M8PortableRemnantLineageV2(BaselineContractModel):
    """Complete immutable lineage fields from one M7 remnant."""

    schema_version: Literal["yieldforge.m8-portable-remnant-lineage.v2"] = (
        "yieldforge.m8-portable-remnant-lineage.v2"
    )
    source_schema_version: Literal["yieldforge.remnant-lineage.v1"] = (
        "yieldforge.remnant-lineage.v1"
    )
    root_stock_id: StrictStr = Field(min_length=1)
    parent_remnant_id: StrictStr | None = Field(default=None, pattern=_REMNANT_ID_PATTERN)
    ancestor_remnant_ids: tuple[StrictStr, ...]
    generation: StrictInt = Field(ge=1)
    source_candidate_id: StrictStr = Field(min_length=1)
    source_component_sha256: M8RawSha256

    @model_validator(mode="after")
    def require_lineage_chain(self) -> Self:
        if len(self.ancestor_remnant_ids) != len(set(self.ancestor_remnant_ids)) or any(
            re.fullmatch(_REMNANT_ID_PATTERN, item) is None for item in self.ancestor_remnant_ids
        ):
            raise ValueError("portable remnant ancestors must be unique remnant IDs")
        if self.generation != len(self.ancestor_remnant_ids) + 1:
            raise ValueError("portable remnant generation differs from ancestor count")
        if self.generation == 1:
            if self.parent_remnant_id is not None or self.ancestor_remnant_ids:
                raise ValueError("portable root lineage cannot carry ancestry")
        elif (
            self.parent_remnant_id is None
            or not self.ancestor_remnant_ids
            or self.ancestor_remnant_ids[-1] != self.parent_remnant_id
        ):
            raise ValueError("portable child lineage does not end at its parent")
        return self


class M8PortableRemnantStockV2(BaselineContractModel):
    """Complete portable form of one exact M7 remnant stock object."""

    schema_version: Literal["yieldforge.m8-portable-remnant-stock.v2"] = (
        "yieldforge.m8-portable-remnant-stock.v2"
    )
    source_schema_version: Literal["yieldforge.remnant-stock.v1"] = "yieldforge.remnant-stock.v1"
    remnant_id: StrictStr = Field(pattern=_REMNANT_ID_PATTERN)
    geometry: M8PortablePolygonV2
    material: M8PortableMaterialIdentityV2
    root_sheet_area_bits: M8CanonicalF64
    root_sheet_short_side_bits: M8CanonicalF64
    lineage: M8PortableRemnantLineageV2

    @model_validator(mode="after")
    def require_remnant_commitments(self) -> Self:
        if decode_canonical_f64(self.root_sheet_area_bits) <= 0.0 or (
            decode_canonical_f64(self.root_sheet_short_side_bits) <= 0.0
        ):
            raise ValueError("portable remnant root-sheet dimensions must be positive")
        if self.lineage.source_component_sha256 != self.geometry.polygon_sha256:
            raise ValueError("portable remnant lineage differs from its geometry")
        if self.remnant_id in self.lineage.ancestor_remnant_ids or (
            self.remnant_id == self.lineage.parent_remnant_id
        ):
            raise ValueError("portable remnant lineage is cyclic")
        return self


class M8PortableInventoryItemV2(BaselineContractModel):
    """One complete portable inventory item and semantic entry time."""

    remnant: M8PortableRemnantStockV2
    entered_at: M8CanonicalUtc


class M8PortablePlacementV2(BaselineContractModel):
    """One exact M7 part placement."""

    part_id: StrictStr = Field(min_length=1)
    rotation_bits: M8CanonicalF64
    translation: M8TranslationPointV2


class M8PortablePlacedPartV2(BaselineContractModel):
    """One exact placed-part geometry commitment."""

    part_id: StrictStr = Field(min_length=1)
    geometry: M8PortablePolygonV2


class M8PortableLayoutSearchConfigV2(BaselineContractModel):
    """Frozen registered complete-layout translation-search settings."""

    schema_version: Literal["yieldforge.m8-portable-layout-search-config.v2"] = (
        "yieldforge.m8-portable-layout-search-config.v2"
    )
    source_schema_version: Literal["yieldforge.m7-layout-fit-search-config.v1"] = (
        "yieldforge.m7-layout-fit-search-config.v1"
    )
    grid_columns: StrictInt = Field(ge=2)
    grid_rows: StrictInt = Field(ge=2)
    maximum_candidates: StrictInt = Field(ge=1)
    candidate_source_order: tuple[
        Literal["bbox_alignments"],
        Literal["vertex_alignments"],
        Literal["uniform_grid"],
    ] = ("bbox_alignments", "vertex_alignments", "uniform_grid")


class M8PortableLayoutSearchResultV2(BaselineContractModel):
    """Exact M7 layout-search result embedded in action evidence."""

    schema_version: Literal["yieldforge.m8-portable-layout-search-result.v2"] = (
        "yieldforge.m8-portable-layout-search-result.v2"
    )
    source_schema_version: Literal["yieldforge.m7-layout-fit-search-result.v1"] = (
        "yieldforge.m7-layout-fit-search-result.v1"
    )
    status: Literal["fit", "no_witness_within_registered_search"]
    candidate_id: StrictStr = Field(min_length=1)
    remnant_id: StrictStr = Field(pattern=_REMNANT_ID_PATTERN)
    config: M8PortableLayoutSearchConfigV2
    generated_candidate_count: StrictInt = Field(ge=0)
    duplicate_candidate_count: StrictInt = Field(ge=0)
    evaluated_candidate_count: StrictInt = Field(ge=0)
    budget_truncated: StrictBool
    translation: M8TranslationPointV2 | None = None

    @model_validator(mode="after")
    def require_search_shape(self) -> Self:
        if self.evaluated_candidate_count > self.generated_candidate_count:
            raise ValueError("portable action search evaluated count exceeds generated count")
        if (self.status == "fit") != (self.translation is not None):
            raise ValueError("portable action search status and translation differ")
        return self


class M8PortableAccountingV2(BaselineContractModel):
    """Every exact M7 material-accounting term as canonical f64 bits."""

    parent_remnant_area_bits: M8CanonicalF64
    placed_area_bits: M8CanonicalF64
    process_loss_area_bits: M8CanonicalF64
    retained_child_area_bits: M8CanonicalF64
    scrap_area_bits: M8CanonicalF64
    reconciliation_delta_bits: M8CanonicalF64
    area_tolerance_bits: M8CanonicalF64

    @model_validator(mode="after")
    def require_accounting_shape(self) -> Self:
        values = (
            self.parent_remnant_area_bits,
            self.placed_area_bits,
            self.process_loss_area_bits,
            self.retained_child_area_bits,
            self.scrap_area_bits,
            self.reconciliation_delta_bits,
        )
        if any(decode_canonical_f64(item) < 0.0 for item in values) or (
            decode_canonical_f64(self.parent_remnant_area_bits) <= 0.0
        ):
            raise ValueError("portable action accounting terms are outside bounds")
        if decode_canonical_f64(self.area_tolerance_bits) <= 0.0:
            raise ValueError("portable action accounting tolerance must be positive")
        parent = decode_canonical_f64(self.parent_remnant_area_bits)
        accounted = sum(
            decode_canonical_f64(item)
            for item in (
                self.placed_area_bits,
                self.process_loss_area_bits,
                self.retained_child_area_bits,
                self.scrap_area_bits,
            )
        )
        delta = decode_canonical_f64(self.reconciliation_delta_bits)
        if delta != abs(parent - accounted) or delta > decode_canonical_f64(
            self.area_tolerance_bits
        ):
            raise ValueError("portable action accounting does not reconcile")
        return self


class M8PortableLayoutActionV2(BaselineContractModel):
    """Complete portable semantic preimage of one M7 materialized action."""

    schema_version: Literal["yieldforge.m8-portable-layout-action.v2"] = (
        "yieldforge.m8-portable-layout-action.v2"
    )
    source_schema_version: Literal["yieldforge.m7-layout-action.v1"] = (
        "yieldforge.m7-layout-action.v1"
    )
    action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    content_sha256: M8Sha256
    problem_id: StrictStr = Field(pattern=r"^yfm7p-[0-9a-f]{24}$")
    problem_sha256: M8Sha256
    candidate_set_id: StrictStr = Field(pattern=r"^yfm7c-[0-9a-f]{24}$")
    candidate_set_sha256: M8Sha256
    candidate_id: StrictStr = Field(min_length=1)
    kind: Literal["open_standard_sheet", "consume_remnant"]
    selected_stock: M8PortableRemnantStockV2
    selected_remnant_id: StrictStr | None = Field(default=None, pattern=_REMNANT_ID_PATTERN)
    translation: M8TranslationPointV2
    placements: tuple[M8PortablePlacementV2, ...] = Field(min_length=1)
    placed_parts: tuple[M8PortablePlacedPartV2, ...] = Field(min_length=1)
    search_result: M8PortableLayoutSearchResultV2 | None = None
    accounting: M8PortableAccountingV2
    returned_remnants: tuple[M8PortableRemnantStockV2, ...]

    @model_validator(mode="after")
    def require_action_shape(self) -> Self:
        placement_ids = tuple(item.part_id for item in self.placements)
        part_ids = tuple(item.part_id for item in self.placed_parts)
        if placement_ids != part_ids or len(placement_ids) != len(set(placement_ids)):
            raise ValueError("portable action placement and part identities differ")
        if self.kind == "open_standard_sheet":
            if self.selected_remnant_id is not None or self.search_result is not None:
                raise ValueError("portable standard action cannot carry remnant search")
        elif (
            self.selected_remnant_id != self.selected_stock.remnant_id
            or self.search_result is None
            or self.search_result.status != "fit"
            or self.search_result.translation != self.translation
        ):
            raise ValueError("portable remnant action lacks matching exact search")
        returned_ids = tuple(item.remnant_id for item in self.returned_remnants)
        _sorted_unique(returned_ids, label="portable action returned-remnant identities")
        return self


class M8PortablePolicyContextV2(BaselineContractModel):
    """Complete policy-visible action context from the legacy common fact."""

    action_id: StrictStr = Field(min_length=1)
    kind: Literal["open_standard_sheet", "consume_remnant"]
    candidate_id: StrictStr = Field(min_length=1)
    candidate_width_bits: M8CanonicalF64
    selected_stock_id: StrictStr = Field(min_length=1)
    immediate_net_cost_bits: M8CanonicalF64
    selected_remnant_age_hours_bits: M8CanonicalF64
    returned_regularity_bits: M8CanonicalF64
    known_order_lookahead_term_bits: M8CanonicalF64

    @model_validator(mode="after")
    def require_policy_context_bounds(self) -> Self:
        if decode_canonical_f64(self.candidate_width_bits) <= 0.0 or (
            decode_canonical_f64(self.selected_remnant_age_hours_bits) < 0.0
        ):
            raise ValueError("portable policy width or age is outside bounds")
        regularity = decode_canonical_f64(self.returned_regularity_bits)
        if not 0.0 <= regularity <= 1.0:
            raise ValueError("portable policy regularity must be in [0, 1]")
        if decode_canonical_f64(self.known_order_lookahead_term_bits) != 0.0:
            raise ValueError("portable M8 lookahead term must remain zero")
        return self


class M8PortableActionDescriptorV2(BaselineContractModel):
    """Complete lazy M7 catalog descriptor used by the legacy common fact."""

    action_id: StrictStr = Field(min_length=1)
    kind: Literal["open_standard_sheet", "consume_remnant"]
    candidate_id: StrictStr = Field(min_length=1)
    selected_remnant_id: StrictStr | None = Field(default=None, pattern=_REMNANT_ID_PATTERN)
    evidence: M8PortableLayoutActionV2 | None = None


class M8PortableActionBindingV2(BaselineContractModel):
    """Catalog-to-materialized action binding from the exact M7 step."""

    catalog_action_id: StrictStr = Field(min_length=1)
    materialized_action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    context: M8PortablePolicyContextV2

    @model_validator(mode="after")
    def require_context_binding(self) -> Self:
        if self.context.action_id != self.catalog_action_id:
            raise ValueError("portable action binding differs from its policy context")
        return self


class M8PortableReplayEventV2(BaselineContractModel):
    """Complete exact M7 event carried by the portable common-transition payload."""

    sequence: StrictInt = Field(ge=0)
    event_id: StrictStr = Field(pattern=r"^yfm7e-[0-9a-f]{24}$")
    binding_id: StrictStr = Field(pattern=r"^yfm7b-[0-9a-f]{24}$")
    occurred_at: M8CanonicalUtc
    timestamp_group_sequence: StrictInt = Field(ge=0)
    timestamp_subsequence: StrictInt = Field(ge=0)
    storage_interval_start: M8CanonicalUtc
    storage_interval_end: M8CanonicalUtc
    inventory_before: tuple[M8PortableInventoryItemV2, ...]
    action_set_size: StrictInt = Field(ge=1)
    standard_action_count: StrictInt = Field(ge=1)
    remnant_action_count: StrictInt = Field(ge=0)
    fit_search_query_count: StrictInt = Field(ge=0)
    fit_search_generated_candidate_count: StrictInt = Field(ge=0)
    fit_search_evaluated_candidate_count: StrictInt = Field(ge=0)
    fit_search_budget_truncated_count: StrictInt = Field(ge=0)
    policy_decision_key: tuple[StrictStr, ...] = Field(min_length=1)
    action: M8PortableLayoutActionV2
    inventory_after: tuple[M8PortableInventoryItemV2, ...]
    delta_costs: M8PortableCostLedgerV2
    cumulative_costs: M8PortableCostLedgerV2

    @model_validator(mode="after")
    def require_event_shape(self) -> Self:
        if self.storage_interval_end != self.occurred_at or (
            decode_canonical_utc(self.storage_interval_start)
            > decode_canonical_utc(self.storage_interval_end)
        ):
            raise ValueError("portable event storage interval is invalid")
        if self.action_set_size != self.standard_action_count + self.remnant_action_count:
            raise ValueError("portable event action-set counts do not reconcile")
        for inventory, label in (
            (self.inventory_before, "before"),
            (self.inventory_after, "after"),
        ):
            ids = tuple(item.remnant.remnant_id for item in inventory)
            _sorted_unique(ids, label=f"portable event inventory-{label} identities")
        return self


class M8PortableReplayCursorV2(BaselineContractModel):
    """Complete portable M7 cursor at one common-transition boundary."""

    next_event_position: StrictInt = Field(ge=0)
    current_time: M8CanonicalUtc
    inventory: tuple[M8PortableInventoryItemV2, ...]
    cumulative_costs: M8PortableCostLedgerV2
    timestamp_group_sequence: StrictInt = Field(ge=0)
    timestamp_subsequence: StrictInt = Field(ge=0)
    previous_release: M8CanonicalUtc | None

    @model_validator(mode="after")
    def require_cursor_inventory_order(self) -> Self:
        ids = tuple(item.remnant.remnant_id for item in self.inventory)
        _sorted_unique(ids, label="portable cursor inventory identities")
        return self


class M8PortablePolicyRankV2(BaselineContractModel):
    """Exact policy rank retained by the existing M8 common fact."""

    policy_name: M8PolicyNameV2
    comparison_key: tuple[M8PolicyRankComponentV2, ...] = Field(min_length=1)
    decision_key: tuple[StrictStr, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_registered_rank_shape(self) -> Self:
        _policy_rank_value(self.policy_name, self.comparison_key)
        return self


class M8PortableCommonTransitionV2(BaselineContractModel):
    """Typed portable semantic preimage of the exact legacy common fact."""

    schema_version: Literal["yieldforge.m8-portable-common-transition.v2"] = (
        "yieldforge.m8-portable-common-transition.v2"
    )
    source_schema_version: Literal["yieldforge.m8-common-transition-fact.v1"] = (
        "yieldforge.m8-common-transition-fact.v1"
    )
    replay_input_id: StrictStr = Field(min_length=1)
    replay_input_sha256: M8Sha256
    semantic_runtime_sha256: M8Sha256
    event_position: StrictInt = Field(ge=0)
    cursor_before_sha256: M8Sha256
    cursor_before: M8PortableReplayCursorV2
    descriptor: M8PortableActionDescriptorV2
    selected_context: M8PortablePolicyContextV2
    action_binding: M8PortableActionBindingV2
    event: M8PortableReplayEventV2
    cursor_after_sha256: M8Sha256
    cursor_after: M8PortableReplayCursorV2
    event_id: StrictStr = Field(pattern=r"^yfm7e-[0-9a-f]{24}$")
    policy_rank: M8PortablePolicyRankV2

    @model_validator(mode="after")
    def require_exact_step_bindings(self) -> Self:
        if (
            self.cursor_before.next_event_position != self.event_position
            or self.cursor_after.next_event_position != self.event_position + 1
            or self.event.sequence != self.event_position
            or self.event.event_id != self.event_id
        ):
            raise ValueError("portable common event/cursor positions differ")
        if (
            self.event.inventory_before != self.cursor_before.inventory
            or self.event.inventory_after != self.cursor_after.inventory
            or self.event.cumulative_costs != self.cursor_after.cumulative_costs
            or self.event.storage_interval_start != self.cursor_before.current_time
            or self.event.occurred_at != self.cursor_after.current_time
            or self.cursor_after.previous_release != self.event.occurred_at
            or self.event.timestamp_group_sequence != self.cursor_after.timestamp_group_sequence
            or self.event.timestamp_subsequence != self.cursor_after.timestamp_subsequence
        ):
            raise ValueError("portable common cursor/event commitments differ")
        if (
            self.action_binding.catalog_action_id != self.descriptor.action_id
            or self.action_binding.materialized_action_id != self.event.action.action_id
            or self.action_binding.context != self.selected_context
            or self.descriptor.kind != self.event.action.kind
            or self.descriptor.candidate_id != self.event.action.candidate_id
            or self.descriptor.selected_remnant_id != self.event.action.selected_remnant_id
            or self.selected_context.kind != self.event.action.kind
            or self.selected_context.candidate_id != self.event.action.candidate_id
        ):
            raise ValueError("portable common descriptor/action bindings differ")
        if (
            self.descriptor.kind == "open_standard_sheet" and self.descriptor.evidence is not None
        ) or (
            self.descriptor.kind == "consume_remnant"
            and self.descriptor.evidence != self.event.action
        ):
            raise ValueError("portable common descriptor evidence differs from its action")
        expected_stock = (
            "current_standard_sheet"
            if self.event.action.selected_remnant_id is None
            else self.event.action.selected_remnant_id
        )
        if self.selected_context.selected_stock_id != expected_stock:
            raise ValueError("portable common policy stock differs from its action")
        if (
            self.policy_rank.decision_key != self.event.policy_decision_key
            or self.policy_rank.policy_name not in _POLICY_RANK_SHAPES
        ):
            raise ValueError("portable common event differs from its policy rank")
        if f"action_id={self.descriptor.action_id}" not in self.policy_rank.decision_key:
            raise ValueError("portable common policy rank does not bind its selected action")
        return self


type M8InventoryExactReplayReasonV2 = Literal[
    "frontier_survivor",
    "counted_search_survivor",
    "unsupported_representation",
]
type M8CommonExactReplayReasonV2 = Literal[
    "exact_survivor_frontier",
    "exact_survivor_counted_search",
    "exact_survivor_unsupported_representation",
    "exact_survivor_mixed",
]


def _exact_replay_reason_summary(
    reasons: tuple[M8InventoryExactReplayReasonV2, ...],
) -> M8CommonExactReplayReasonV2:
    unique = set(reasons)
    if len(unique) > 1:
        return "exact_survivor_mixed"
    reason = next(iter(unique))
    if reason == "frontier_survivor":
        return "exact_survivor_frontier"
    if reason == "counted_search_survivor":
        return "exact_survivor_counted_search"
    return "exact_survivor_unsupported_representation"


class M8CommonInventoryClassificationV2(BaselineContractModel):
    """Complete portable classification of one common-cursor inventory item."""

    remnant_id: StrictStr = Field(pattern=_REMNANT_ID_PATTERN)
    classification: Literal["scalar_no_fit", "counted_no_fit", "exact_survivor"]
    material_matches: StrictBool
    remnant_area_bits: M8CanonicalF64
    remnant_width_bits: M8CanonicalF64
    remnant_height_bits: M8CanonicalF64
    area_tolerance_bits: M8CanonicalF64
    coordinate_tolerance_bits: M8CanonicalF64
    frontier_ref: M8Sha256 | None
    candidate_scalar_refs: tuple[M8Sha256, ...]
    translation_batch_refs: tuple[M8Sha256, ...]
    exact_replay_reason: M8InventoryExactReplayReasonV2 | None = None

    @model_validator(mode="after")
    def require_classification_shape(self) -> Self:
        _sorted_unique(
            self.candidate_scalar_refs,
            label="inventory candidate-scalar references",
        )
        _sorted_unique(
            self.translation_batch_refs,
            label="inventory translation-batch references",
        )
        measurements = (
            self.remnant_area_bits,
            self.remnant_width_bits,
            self.remnant_height_bits,
        )
        tolerances = (self.area_tolerance_bits, self.coordinate_tolerance_bits)
        if any(decode_canonical_f64(item) <= 0.0 for item in measurements) or any(
            decode_canonical_f64(item) < 0.0 for item in tolerances
        ):
            raise ValueError("inventory classification measurements are outside bounds")
        if self.classification == "scalar_no_fit":
            valid = (
                self.frontier_ref is not None
                and bool(self.candidate_scalar_refs)
                and not self.translation_batch_refs
                and self.exact_replay_reason is None
            )
        elif self.classification == "counted_no_fit":
            valid = (
                self.frontier_ref is not None
                and bool(self.candidate_scalar_refs)
                and bool(self.translation_batch_refs)
                and self.exact_replay_reason is None
            )
        else:
            evidence_shape = (
                self.frontier_ref is not None,
                bool(self.candidate_scalar_refs),
                bool(self.translation_batch_refs),
            )
            expected_reason: M8InventoryExactReplayReasonV2 | None = {
                (True, True, False): "frontier_survivor",
                (True, True, True): "counted_search_survivor",
                (False, False, False): "unsupported_representation",
            }.get(evidence_shape)
            valid = expected_reason is not None and self.exact_replay_reason == expected_reason
        if not valid:
            raise ValueError(
                "inventory classification reason differs from its evidence path or is incomplete"
            )
        return self


class M8CommonTransitionLemmaV2(_M8FactV2):
    """Portable common-transition commitments plus one explicit evidence mode."""

    _EXPECTED_KIND = "common_transition_lemma"

    schema_version: Literal["yieldforge.m8-common-transition-lemma.v2"] = (
        "yieldforge.m8-common-transition-lemma.v2"
    )
    fact_kind: Literal["common_transition_lemma"] = "common_transition_lemma"
    replay_input_id: StrictStr = Field(min_length=1)
    replay_input_sha256: M8Sha256
    semantic_runtime_sha256: M8Sha256
    stream_id: StrictStr = Field(pattern=_STREAM_ID_PATTERN)
    event_position: StrictInt = Field(ge=0)
    event_id: StrictStr = Field(pattern=r"^yfm7e-[0-9a-f]{24}$")
    legacy_common_fact_sha256: M8Sha256
    portable_transition: M8PortableCommonTransitionV2
    problem_id: StrictStr = Field(pattern=r"^yfm7p-[0-9a-f]{24}$")
    problem_sha256: M8Sha256
    candidate_set_id: StrictStr = Field(pattern=r"^yfm7c-[0-9a-f]{24}$")
    candidate_set_sha256: M8Sha256
    fit_config_sha256: M8Sha256
    search_config_sha256: M8Sha256
    collision_backend: Literal[
        "shapely_authoritative",
        "jagua_rs_0_7_0_guarded_prefilter_shapely_witness",
    ]
    jagua_executable_sha256: M8Sha256 | None
    cursor_before_sha256: M8Sha256
    cursor_after_sha256: M8Sha256
    cursor_before_inventory_remnant_ids: tuple[StrictStr, ...]
    cursor_after_inventory_remnant_ids: tuple[StrictStr, ...]
    event_occurred_at: M8CanonicalUtc
    storage_interval_start: M8CanonicalUtc
    storage_interval_end: M8CanonicalUtc
    cursor_current_time: M8CanonicalUtc
    cursor_previous_release: M8CanonicalUtc | None
    previous_common_lemma_ref: M8Sha256 | None
    baseline_fallback_cursor_sha256: M8Sha256 | None
    minimum_standard_candidate_ref: M8Sha256
    selected_catalog_action_id: StrictStr = Field(min_length=1)
    selected_materialized_action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    selected_candidate_id: StrictStr = Field(min_length=1)
    policy_name: M8PolicyNameV2
    selected_comparison_key: tuple[M8PolicyRankComponentV2, ...] = Field(min_length=1)
    selected_decision_key: tuple[StrictStr, ...] = Field(min_length=1)
    selected_immediate_net_cost_bits: M8CanonicalF64
    event_net_cost_bits: M8CanonicalF64
    candidate_scalar_refs: tuple[M8Sha256, ...]
    frontier_refs: tuple[M8Sha256, ...]
    standard_candidate_refs: tuple[M8Sha256, ...] = Field(min_length=1)
    inventory_classifications: tuple[M8CommonInventoryClassificationV2, ...]
    evidence_mode: Literal["frontier_no_fit", "counted_no_fit", "exact_replay"]
    translation_batch_refs: tuple[M8Sha256, ...]
    exact_replay_reason: M8CommonExactReplayReasonV2 | None = None

    @model_validator(mode="after")
    def require_common_evidence_mode_and_time(self) -> Self:
        transition = self.portable_transition
        before_ids = tuple(item.remnant.remnant_id for item in transition.cursor_before.inventory)
        after_ids = tuple(item.remnant.remnant_id for item in transition.cursor_after.inventory)
        if (
            transition.replay_input_id != self.replay_input_id
            or transition.replay_input_sha256 != self.replay_input_sha256
            or transition.semantic_runtime_sha256 != self.semantic_runtime_sha256
            or transition.event_position != self.event_position
            or transition.event_id != self.event_id
            or transition.cursor_before_sha256 != self.cursor_before_sha256
            or transition.cursor_after_sha256 != self.cursor_after_sha256
            or before_ids != self.cursor_before_inventory_remnant_ids
            or after_ids != self.cursor_after_inventory_remnant_ids
            or transition.event.occurred_at != self.event_occurred_at
            or transition.event.storage_interval_start != self.storage_interval_start
            or transition.event.storage_interval_end != self.storage_interval_end
            or transition.cursor_after.current_time != self.cursor_current_time
            or transition.cursor_after.previous_release != self.cursor_previous_release
        ):
            raise ValueError("portable legacy common transition differs from lemma context")
        if (
            transition.descriptor.action_id != self.selected_catalog_action_id
            or transition.action_binding.catalog_action_id != self.selected_catalog_action_id
            or transition.action_binding.materialized_action_id
            != self.selected_materialized_action_id
            or transition.event.action.candidate_id != self.selected_candidate_id
            or transition.policy_rank.policy_name != self.policy_name
            or transition.policy_rank.comparison_key != self.selected_comparison_key
            or transition.policy_rank.decision_key != self.selected_decision_key
            or transition.selected_context.immediate_net_cost_bits
            != self.selected_immediate_net_cost_bits
            or transition.event.delta_costs.net_cost_bits != self.event_net_cost_bits
        ):
            raise ValueError("portable legacy common action/rank differs from lemma")
        action = transition.event.action
        if (
            action.problem_id != self.problem_id
            or action.problem_sha256 != self.problem_sha256
            or action.candidate_set_id != self.candidate_set_id
            or action.candidate_set_sha256 != self.candidate_set_sha256
        ):
            raise ValueError("portable legacy common problem context differs from lemma")
        if (self.previous_common_lemma_ref is None) == (
            self.baseline_fallback_cursor_sha256 is None
        ):
            raise ValueError(
                "common lemma requires exactly one previous lemma or baseline fallback"
            )
        for refs, label in (
            (self.candidate_scalar_refs, "common candidate-scalar references"),
            (self.frontier_refs, "common frontier references"),
            (self.translation_batch_refs, "common translation-batch references"),
        ):
            _sorted_unique(refs, label=label)
        if len(self.standard_candidate_refs) != len(set(self.standard_candidate_refs)):
            raise ValueError("common standard-candidate references must be unique")
        inventory_ids = tuple(item.remnant_id for item in self.inventory_classifications)
        _sorted_unique(inventory_ids, label="common inventory classifications")
        for values, label in (
            (
                self.cursor_before_inventory_remnant_ids,
                "common cursor-before inventory IDs",
            ),
            (
                self.cursor_after_inventory_remnant_ids,
                "common cursor-after inventory IDs",
            ),
        ):
            _sorted_unique(values, label=label)
            if any(re.fullmatch(_REMNANT_ID_PATTERN, item) is None for item in values):
                raise ValueError(f"{label} contain an invalid remnant identity")
        if inventory_ids != self.cursor_before_inventory_remnant_ids:
            raise ValueError(
                "common inventory classifications do not cover cursor-before inventory"
            )
        if self.minimum_standard_candidate_ref not in self.standard_candidate_refs:
            raise ValueError("minimum standard candidate is absent from complete references")
        classifications = {item.classification for item in self.inventory_classifications}
        if self.evidence_mode == "counted_no_fit":
            if (
                "counted_no_fit" not in classifications
                or "exact_survivor" in classifications
                or not self.translation_batch_refs
                or self.exact_replay_reason is not None
            ):
                raise ValueError("counted-no-fit common evidence requires translation batches only")
        elif self.evidence_mode == "exact_replay":
            survivor_reasons = tuple(
                item.exact_replay_reason
                for item in self.inventory_classifications
                if item.classification == "exact_survivor" and item.exact_replay_reason is not None
            )
            if not survivor_reasons:
                raise ValueError(
                    "exact-replay common evidence requires one or more exact survivors"
                )
            expected_reason = _exact_replay_reason_summary(survivor_reasons)
            if self.exact_replay_reason != expected_reason:
                raise ValueError(
                    "common exact-replay reason summary differs from exact-survivor reasons"
                )
        elif (
            classifications - {"scalar_no_fit"}
            or self.translation_batch_refs
            or self.exact_replay_reason is not None
        ):
            raise ValueError("frontier-no-fit common evidence cannot carry fallback evidence")
        scalar_union = {
            reference
            for item in self.inventory_classifications
            for reference in item.candidate_scalar_refs
        }
        frontier_union = {
            item.frontier_ref
            for item in self.inventory_classifications
            if item.frontier_ref is not None
        }
        translation_union = {
            reference
            for item in self.inventory_classifications
            for reference in item.translation_batch_refs
        }
        if (
            scalar_union != set(self.candidate_scalar_refs)
            or frontier_union != set(self.frontier_refs)
            or translation_union != set(self.translation_batch_refs)
        ):
            raise ValueError("common fact references differ from inventory evidence union")
        selected_remnant_id = transition.event.action.selected_remnant_id
        if selected_remnant_id is not None:
            selected_classification = next(
                (
                    item
                    for item in self.inventory_classifications
                    if item.remnant_id == selected_remnant_id
                ),
                None,
            )
            if (
                self.evidence_mode != "exact_replay"
                or selected_classification is None
                or selected_classification.classification != "exact_survivor"
                or not selected_classification.material_matches
            ):
                raise ValueError(
                    "M8 selected remnant must have a matching exact survivor classification"
                )
        jagua_active = self.collision_backend == "jagua_rs_0_7_0_guarded_prefilter_shapely_witness"
        if jagua_active != (self.jagua_executable_sha256 is not None):
            raise ValueError("common collision backend and Jagua executable binding differ")
        occurred = decode_canonical_utc(self.event_occurred_at)
        interval_start = decode_canonical_utc(self.storage_interval_start)
        interval_end = decode_canonical_utc(self.storage_interval_end)
        current = decode_canonical_utc(self.cursor_current_time)
        previous = (
            decode_canonical_utc(self.cursor_previous_release)
            if self.cursor_previous_release is not None
            else None
        )
        if interval_end != occurred or interval_start > interval_end:
            raise ValueError("common lemma storage interval differs from event occurrence")
        if current != occurred or previous != occurred:
            raise ValueError("common lemma resulting cursor time differs from event occurrence")
        return self


class M8InventoryDeltaV2(BaselineContractModel):
    """Exact branch inventory identity delta."""

    removed_remnant_ids: tuple[StrictStr, ...]
    added_remnant_ids: tuple[StrictStr, ...]

    @model_validator(mode="after")
    def require_deterministic_delta(self) -> Self:
        _sorted_unique(self.removed_remnant_ids, label="removed remnant IDs")
        _sorted_unique(self.added_remnant_ids, label="added remnant IDs")
        if set(self.removed_remnant_ids) & set(self.added_remnant_ids):
            raise ValueError("inventory delta cannot both remove and add one remnant")
        for item in (*self.removed_remnant_ids, *self.added_remnant_ids):
            if re.fullmatch(_REMNANT_ID_PATTERN, item) is None:
                raise ValueError("inventory delta contains an invalid remnant ID")
        return self


class M8RejectionEvidenceV2(BaselineContractModel):
    """Complete typed translation-rejection certificate preimage."""

    direction: Literal["added", "removed"]
    remnant_id: StrictStr = Field(pattern=_REMNANT_ID_PATTERN)
    candidate_id: StrictStr = Field(min_length=1)
    candidate_scalar_ref: M8Sha256
    impossible: StrictBool
    reason: (
        Literal[
            "material_mismatch",
            "footprint_area_exceeds_remnant",
            "footprint_width_exceeds_remnant",
            "footprint_height_exceeds_remnant",
        ]
        | None
    )
    layout_area_bits: M8CanonicalF64
    remnant_area_bits: M8CanonicalF64
    layout_width_bits: M8CanonicalF64
    remnant_width_bits: M8CanonicalF64
    layout_height_bits: M8CanonicalF64
    remnant_height_bits: M8CanonicalF64
    area_tolerance_bits: M8CanonicalF64

    @model_validator(mode="after")
    def require_complete_certificate(self) -> Self:
        if self.impossible != (self.reason is not None):
            raise ValueError("rejection impossible flag and reason differ")
        dimensions = (
            self.layout_area_bits,
            self.remnant_area_bits,
            self.layout_width_bits,
            self.remnant_width_bits,
            self.layout_height_bits,
            self.remnant_height_bits,
        )
        if any(decode_canonical_f64(item) <= 0.0 for item in dimensions) or (
            decode_canonical_f64(self.area_tolerance_bits) < 0.0
        ):
            raise ValueError("rejection certificate measurements are outside bounds")
        return self


class M8CandidateScalarGroupEvidenceV2(BaselineContractModel):
    """Compact complete candidate-scalar set for one changed remnant."""

    evidence_kind: Literal["candidate_scalar_group"] = "candidate_scalar_group"
    direction: Literal["added", "removed"]
    remnant_id: StrictStr = Field(pattern=_REMNANT_ID_PATTERN)
    candidate_scalar_refs: tuple[M8Sha256, ...] = Field(min_length=1)
    all_candidates_impossible: StrictBool

    @model_validator(mode="after")
    def require_canonical_candidate_refs(self) -> Self:
        _sorted_unique(
            self.candidate_scalar_refs,
            label="compact rejection candidate-scalar references",
        )
        return self


M8RejectionEvidenceClaimV2 = M8RejectionEvidenceV2 | M8CandidateScalarGroupEvidenceV2


class M8SearchEvidenceV2(BaselineContractModel):
    """Typed exact-search preimage; collision classifications remain absent."""

    direction: Literal["added", "removed"]
    remnant_id: StrictStr = Field(pattern=_REMNANT_ID_PATTERN)
    candidate_id: StrictStr = Field(min_length=1)
    search_config: M8PortableLayoutSearchConfigV2
    search_config_sha256: M8Sha256
    translation_batch_ref: M8Sha256
    generated_candidate_count: StrictInt = Field(ge=0)
    duplicate_candidate_count: StrictInt = Field(ge=0)
    evaluated_candidate_count: StrictInt = Field(ge=0)
    budget_truncated: StrictBool
    result: Literal["fit", "no_witness_within_registered_search"]
    selected_translation: M8TranslationPointV2 | None = None

    @model_validator(mode="after")
    def require_search_result_evidence(self) -> Self:
        if self.evaluated_candidate_count > self.generated_candidate_count:
            raise ValueError("search evaluated count exceeds generated count")
        if (self.result == "fit") != (self.selected_translation is not None):
            raise ValueError("search fit status and translation witness differ")
        expected_config_sha256 = (
            "sha256:"
            + hashlib.sha256(
                canonical_semantic_json(self.search_config.model_dump(mode="json"))
            ).hexdigest()
        )
        if self.search_config_sha256 != expected_config_sha256:
            raise ValueError("search configuration SHA-256 differs from its semantic content")
        return self


class M8CompetitorEvidenceV2(BaselineContractModel):
    """Complete competing action/rank preimage for policy dominance."""

    direction: Literal["added", "removed"]
    candidate_id: StrictStr = Field(min_length=1)
    catalog_action_id: StrictStr = Field(min_length=1)
    materialized_action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    materialized_content_sha256: M8Sha256
    selected_remnant_id: StrictStr = Field(pattern=_REMNANT_ID_PATTERN)
    action_kind: Literal["consume_remnant"] = "consume_remnant"
    selected_stock_id: StrictStr = Field(pattern=_REMNANT_ID_PATTERN)
    candidate_width_bits: M8CanonicalF64
    immediate_net_cost_bits: M8CanonicalF64
    selected_remnant_age_hours_bits: M8CanonicalF64
    returned_regularity_bits: M8CanonicalF64
    known_order_lookahead_term_bits: M8CanonicalF64
    policy_name: M8PolicyNameV2
    comparison_key: tuple[M8PolicyRankComponentV2, ...] = Field(min_length=1)
    decision_key: tuple[StrictStr, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_catalog_binding(self) -> Self:
        if f"action_id={self.catalog_action_id}" not in self.decision_key:
            raise ValueError("competitor decision key must bind its catalog action")
        if self.selected_stock_id != self.selected_remnant_id:
            raise ValueError("competitor selected stock differs from selected remnant")
        if decode_canonical_f64(self.candidate_width_bits) <= 0.0 or (
            decode_canonical_f64(self.selected_remnant_age_hours_bits) < 0.0
        ):
            raise ValueError("competitor policy context is outside bounds")
        regularity = decode_canonical_f64(self.returned_regularity_bits)
        if not 0.0 <= regularity <= 1.0:
            raise ValueError("competitor returned regularity must be in [0, 1]")
        if decode_canonical_f64(self.known_order_lookahead_term_bits) != 0.0:
            raise ValueError("competitor lookahead term must remain zero")
        _policy_rank_value(self.policy_name, self.comparison_key)
        return self


class M8InfluenceFactV2(_M8FactV2):
    """One branch transition with complete typed rejection/search preimages."""

    _EXPECTED_KIND = "influence"

    schema_version: Literal["yieldforge.m8-influence-fact.v2"] = "yieldforge.m8-influence-fact.v2"
    fact_kind: Literal["influence"] = "influence"
    semantic_runtime_sha256: M8Sha256
    stream_id: StrictStr = Field(pattern=_STREAM_ID_PATTERN)
    event_position: StrictInt = Field(ge=0)
    common_lemma_ref: M8Sha256
    root_action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    common_catalog_action_id: StrictStr = Field(min_length=1)
    common_materialized_action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    branch_catalog_action_id: StrictStr = Field(min_length=1)
    branch_materialized_action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    state_before_sha256: M8Sha256
    state_after_sha256: M8Sha256
    inventory_delta: M8InventoryDeltaV2
    classification: Literal["state_rejoin", "no_fit", "policy_dominated", "exact_transition"]
    evidence_mode: Literal[
        "scalar_no_fit",
        "policy_dominated_exact_check",
        "exact_transition",
        "state_rejoin",
    ]
    rejection_evidence: tuple[M8RejectionEvidenceClaimV2, ...]
    search_evidence: tuple[M8SearchEvidenceV2, ...]
    competitor_evidence: tuple[M8CompetitorEvidenceV2, ...]

    @model_validator(mode="after")
    def require_complete_influence_evidence(self) -> Self:
        expanded_rejections = tuple(
            item for item in self.rejection_evidence if isinstance(item, M8RejectionEvidenceV2)
        )
        compact_rejections = tuple(
            item
            for item in self.rejection_evidence
            if isinstance(item, M8CandidateScalarGroupEvidenceV2)
        )
        if expanded_rejections and compact_rejections:
            raise ValueError("influence cannot mix expanded and compact rejection encodings")
        rejection_keys = (
            tuple(
                (item.direction, item.remnant_id, item.candidate_id) for item in expanded_rejections
            )
            if expanded_rejections
            else tuple((item.direction, item.remnant_id) for item in compact_rejections)
        )
        if rejection_keys != tuple(sorted(set(rejection_keys))):
            raise ValueError("influence rejection evidence must be sorted and unique")
        search_keys = tuple(
            (
                item.direction,
                item.remnant_id,
                item.candidate_id,
                item.search_config_sha256,
            )
            for item in self.search_evidence
        )
        if search_keys != tuple(sorted(set(search_keys))):
            raise ValueError("influence search evidence must be sorted and unique")
        search_remnants = {(item.direction, item.remnant_id) for item in self.search_evidence}
        for group in compact_rejections:
            group_key = (group.direction, group.remnant_id)
            if group.all_candidates_impossible and group_key in search_remnants:
                raise ValueError(
                    "all-impossible compact rejection group cannot carry search evidence"
                )
            if not group.all_candidates_impossible and group_key not in search_remnants:
                raise ValueError("non-impossible compact rejection group requires search evidence")
        competitor_keys = tuple(
            (
                item.direction,
                item.selected_remnant_id,
                item.catalog_action_id,
                item.materialized_action_id,
            )
            for item in self.competitor_evidence
        )
        if competitor_keys != tuple(sorted(set(competitor_keys))):
            raise ValueError("influence competitor evidence must be sorted and unique")
        if self.evidence_mode == "state_rejoin" and (
            self.inventory_delta.removed_remnant_ids or self.inventory_delta.added_remnant_ids
        ):
            raise ValueError("state rejoin requires an empty inventory delta")
        expected_delta = {
            *(("removed", item) for item in self.inventory_delta.removed_remnant_ids),
            *(("added", item) for item in self.inventory_delta.added_remnant_ids),
        }
        evidence_delta = {
            *((item.direction, item.remnant_id) for item in self.rejection_evidence),
            *((item.direction, item.remnant_id) for item in self.search_evidence),
            *((item.direction, item.selected_remnant_id) for item in self.competitor_evidence),
        }
        if not evidence_delta <= expected_delta or (
            self.classification != "exact_transition" and evidence_delta != expected_delta
        ):
            raise ValueError("influence evidence and inventory delta coverage differ")
        if (
            self.evidence_mode in {"policy_dominated_exact_check", "exact_transition"}
            and self.classification in {"no_fit", "policy_dominated"}
            and not self.rejection_evidence
        ):
            raise ValueError("M8 influence lacks the complete rejection candidate set")

        if self.evidence_mode == "scalar_no_fit":
            valid = (
                self.classification == "no_fit"
                and bool(self.rejection_evidence)
                and all(
                    item.impossible
                    if isinstance(item, M8RejectionEvidenceV2)
                    else item.all_candidates_impossible
                    for item in self.rejection_evidence
                )
                and not self.search_evidence
                and not self.competitor_evidence
            )
        elif self.evidence_mode == "policy_dominated_exact_check":
            valid = (
                self.classification == "policy_dominated"
                and bool(self.rejection_evidence)
                and bool(self.search_evidence)
                and bool(self.competitor_evidence)
            )
        elif self.evidence_mode == "exact_transition":
            valid = (
                self.classification == "exact_transition" and not self.competitor_evidence
            ) or (
                self.classification == "no_fit"
                and bool(self.rejection_evidence)
                and bool(self.search_evidence)
                and not self.competitor_evidence
            )
        else:
            valid = (
                self.classification == "state_rejoin"
                and not self.inventory_delta.removed_remnant_ids
                and not self.inventory_delta.added_remnant_ids
                and not self.rejection_evidence
                and not self.search_evidence
                and not self.competitor_evidence
            )
        if not valid:
            raise ValueError("influence classification and typed evidence mode differ")
        if self.classification == "no_fit" and any(
            item.result == "fit" for item in self.search_evidence
        ):
            raise ValueError("no-fit influence cannot carry positive fit search evidence")
        if self.classification == "policy_dominated":
            fit_searches = {
                (item.direction, item.remnant_id, item.candidate_id)
                for item in self.search_evidence
                if item.result == "fit"
            }
            fit_remnants = {(direction, remnant_id) for direction, remnant_id, _ in fit_searches}
            competitor_remnant_keys = tuple(
                (item.direction, item.selected_remnant_id) for item in self.competitor_evidence
            )
            if len(competitor_remnant_keys) != len(set(competitor_remnant_keys)):
                raise ValueError(
                    "policy-dominated fit remnant requires exactly one selected competitor"
                )
            competitor_remnants = set(competitor_remnant_keys)
            if fit_remnants != competitor_remnants or any(
                (item.direction, item.selected_remnant_id, item.candidate_id) not in fit_searches
                for item in self.competitor_evidence
            ):
                raise ValueError(
                    "policy-dominated competitor evidence differs from exact fit searches"
                )
        if self.classification != "exact_transition" and (
            self.branch_catalog_action_id != self.common_catalog_action_id
            or self.branch_materialized_action_id != self.common_materialized_action_id
        ):
            raise ValueError("unchecked branch action must equal the common selected action")
        return self


class M8ActionRootV2(_M8FactV2):
    """One v1-compatible terminal action commitment over ordered shared facts."""

    _EXPECTED_KIND = "action_root"

    schema_version: Literal["yieldforge.m8-action-root.v2"] = "yieldforge.m8-action-root.v2"
    fact_kind: Literal["action_root"] = "action_root"
    semantic_runtime_sha256: M8Sha256
    stream_id: StrictStr = Field(pattern=_STREAM_ID_PATTERN)
    action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    catalog_action_id: StrictStr = Field(min_length=1)
    baseline_action_id: StrictStr = Field(pattern=_ACTION_ID_PATTERN)
    baseline_catalog_action_id: StrictStr = Field(min_length=1)
    start_event_position: StrictInt = Field(ge=0)
    stop_event_position: StrictInt = Field(ge=1)
    suffix_sha256: M8Sha256
    start_state_sha256: M8Sha256
    initial_state_after_sha256: M8Sha256
    final_state_sha256: M8Sha256
    common_lemma_refs: tuple[M8Sha256, ...]
    influence_fact_refs: tuple[M8Sha256, ...]
    final_net_cost_bits: M8CanonicalF64

    @model_validator(mode="after")
    def require_reference_counts(self) -> Self:
        if self.stop_event_position < self.start_event_position + 1:
            raise ValueError("M8 action root stop position must follow its start")
        if len(self.common_lemma_refs) != len(self.influence_fact_refs):
            raise ValueError("M8 action root common/influence coverage differs")
        if len(set(self.common_lemma_refs)) != len(self.common_lemma_refs):
            raise ValueError("M8 action root contains duplicate common references")
        if len(set(self.influence_fact_refs)) != len(self.influence_fact_refs):
            raise ValueError("M8 action root contains duplicate influence references")
        return self


class M8BundleProvenanceV2(BaselineContractModel):
    """Calibration-only identities shared by every fact in one bundle."""

    partition: Literal["calibration"] = "calibration"
    replay_input_id: StrictStr = Field(min_length=1)
    replay_input_sha256: M8Sha256
    semantic_runtime_sha256: M8Sha256
    stream_id: StrictStr = Field(pattern=_STREAM_ID_PATTERN)
    stream_sha256: M8Sha256
    regime: Literal[
        "no_signal",
        "exact_recurrence",
        "family_similarity",
        "compatible_bundle",
        "high_mix",
        "regime_shift",
    ]
    temporal_seed: StrictInt
    suffix_sha256: M8Sha256
    freeze_id: StrictStr = Field(pattern=r"^yfm7freeze-[0-9a-f]{24}$")
    freeze_sha256: M8Sha256
    evaluation_partition_opened: Literal[False] = False


class M8UncheckedFactBundleV2(BaselineContractModel):
    """Explicitly unchecked fixed-layer portable M8 fact bundle."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m8-unchecked-fact-bundle.v2"] = (
        "yieldforge.m8-unchecked-fact-bundle.v2"
    )
    bundle_kind: Literal["unchecked_fact_bundle"] = "unchecked_fact_bundle"
    bundle_sha256: M8Sha256
    provenance: M8BundleProvenanceV2
    translation_batches: tuple[M8PortableTranslationBatch, ...]
    candidate_scalar_facts: tuple[M8CandidateScalarFactV2, ...]
    frontier_facts: tuple[M8FrontierFactV2, ...]
    standard_candidate_facts: tuple[M8StandardCandidateFactV2, ...]
    common_lemmas: tuple[M8CommonTransitionLemmaV2, ...]
    influence_facts: tuple[M8InfluenceFactV2, ...]
    action_roots: tuple[M8ActionRootV2, ...] = Field(min_length=1)

    @staticmethod
    def _fact_order(item: _M8FactV2) -> str:
        return item.fact_sha256

    @staticmethod
    def _event_order(item: M8CommonTransitionLemmaV2) -> tuple[str, int, str]:
        return (item.stream_id, item.event_position, item.fact_sha256)

    @staticmethod
    def _influence_order(item: M8InfluenceFactV2) -> tuple[str, int, str, str]:
        return (
            item.stream_id,
            item.event_position,
            item.root_action_id,
            item.fact_sha256,
        )

    @staticmethod
    def _standard_order(item: M8StandardCandidateFactV2) -> tuple[str, int, int, str]:
        return (
            item.stream_id,
            item.event_position,
            item.profile_position,
            item.fact_sha256,
        )

    @staticmethod
    def _root_order(item: M8ActionRootV2) -> tuple[str, str, str]:
        return (item.stream_id, item.action_id, item.fact_sha256)

    def _shallow_hash_input(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "bundle_kind": self.bundle_kind,
            "provenance": self.provenance,
            "translation_batches": tuple(
                {"fact_sha256": item.fact_sha256} for item in self.translation_batches
            ),
            "candidate_scalar_facts": tuple(
                {"fact_sha256": item.fact_sha256} for item in self.candidate_scalar_facts
            ),
            "frontier_facts": tuple(
                {"fact_sha256": item.fact_sha256} for item in self.frontier_facts
            ),
            "standard_candidate_facts": tuple(
                {"fact_sha256": item.fact_sha256} for item in self.standard_candidate_facts
            ),
            "common_lemmas": tuple(
                {"fact_sha256": item.fact_sha256} for item in self.common_lemmas
            ),
            "influence_facts": tuple(
                {"fact_sha256": item.fact_sha256} for item in self.influence_facts
            ),
            "action_roots": tuple({"fact_sha256": item.fact_sha256} for item in self.action_roots),
        }

    @model_validator(mode="after")
    def require_fixed_layers_and_reachability(self) -> Self:
        expected_hash = m8_bundle_sha256(self._shallow_hash_input())
        if self.bundle_sha256 != expected_hash:
            _raise_structural_error(
                _M8StructuralErrorCode.BUNDLE_HASH_MISMATCH,
                "M8 bundle SHA-256 does not match ordered fact roots",
                bundle_sha256=self.bundle_sha256,
            )

        ordered_layers: tuple[tuple[tuple[_M8FactV2, ...], object], ...] = (
            (self.translation_batches, self._fact_order),
            (self.candidate_scalar_facts, self._fact_order),
            (self.frontier_facts, self._fact_order),
            (self.standard_candidate_facts, self._standard_order),
            (self.common_lemmas, self._event_order),
            (self.influence_facts, self._influence_order),
            (self.action_roots, self._root_order),
        )
        for entries, order_key in ordered_layers:
            expected_entries = tuple(sorted(entries, key=order_key))  # type: ignore[arg-type]
            if entries != expected_entries:
                first_misordered = next(
                    item
                    for item, expected in zip(entries, expected_entries, strict=True)
                    if item != expected
                )
                _raise_structural_error(
                    _M8StructuralErrorCode.FIXED_LAYER_ORDER,
                    "M8 fixed-layer facts differ from deterministic order",
                    fact_sha256=first_misordered.fact_sha256,
                )

        layers = (
            self.translation_batches,
            self.candidate_scalar_facts,
            self.frontier_facts,
            self.standard_candidate_facts,
            self.common_lemmas,
            self.influence_facts,
            self.action_roots,
        )
        all_entries = tuple(item for layer in layers for item in layer)
        all_hashes = tuple(item.fact_sha256 for item in all_entries)
        if len(all_hashes) != len(set(all_hashes)):
            seen_hashes: set[str] = set()
            duplicate_hash = ""
            for item in all_entries:
                if item.fact_sha256 in seen_hashes:
                    duplicate_hash = item.fact_sha256
                    break
                seen_hashes.add(item.fact_sha256)
            _raise_structural_error(
                _M8StructuralErrorCode.DUPLICATE_FACT,
                "M8 bundle contains duplicate serialized fact entries",
                fact_sha256=duplicate_hash,
            )

        identity_layers: tuple[tuple[tuple[_M8FactV2, object], ...], ...] = (
            tuple(
                (
                    item,
                    (
                        item.semantic_runtime_sha256,
                        item.stream_id,
                        item.event_position,
                        item.remnant_id,
                        item.candidate_id,
                    ),
                )
                for item in self.translation_batches
            ),
            tuple(
                (
                    item,
                    (
                        item.semantic_runtime_sha256,
                        item.stream_id,
                        item.problem_id,
                        item.candidate_set_id,
                        item.candidate_id,
                    ),
                )
                for item in self.candidate_scalar_facts
            ),
            tuple(
                (
                    item,
                    (
                        item.semantic_runtime_sha256,
                        item.stream_id,
                        item.problem_id,
                        item.candidate_set_id,
                    ),
                )
                for item in self.frontier_facts
            ),
            tuple(
                (
                    item,
                    (
                        item.semantic_runtime_sha256,
                        item.stream_id,
                        item.event_position,
                        item.profile_position,
                    ),
                )
                for item in self.standard_candidate_facts
            ),
            tuple(
                (item, (item.semantic_runtime_sha256, item.stream_id, item.event_position))
                for item in self.common_lemmas
            ),
            tuple(
                (
                    item,
                    (
                        item.semantic_runtime_sha256,
                        item.stream_id,
                        item.event_position,
                        item.root_action_id,
                    ),
                )
                for item in self.influence_facts
            ),
            tuple(
                (item, (item.semantic_runtime_sha256, item.stream_id, item.action_id))
                for item in self.action_roots
            ),
        )
        for identity_layer in identity_layers:
            seen_identities: dict[object, _M8FactV2] = {}
            for item, identity in identity_layer:
                if identity in seen_identities:
                    _raise_structural_error(
                        _M8StructuralErrorCode.DUPLICATE_IDENTITY,
                        "M8 bundle contains duplicate semantic fact identities",
                        fact_sha256=item.fact_sha256,
                        dependency_sha256=seen_identities[identity].fact_sha256,
                    )
                seen_identities[identity] = item

        context = (
            self.provenance.semantic_runtime_sha256,
            self.provenance.stream_id,
        )
        for item in all_entries:
            if (item.semantic_runtime_sha256, item.stream_id) != context:
                _raise_structural_error(
                    _M8StructuralErrorCode.CONTEXT_MISMATCH,
                    "M8 bundle fact has cross-runtime or cross-stream context",
                    fact_sha256=item.fact_sha256,
                )

        translations = {item.fact_sha256: item for item in self.translation_batches}
        scalars = {item.fact_sha256: item for item in self.candidate_scalar_facts}
        frontiers = {item.fact_sha256: item for item in self.frontier_facts}
        standards = {item.fact_sha256: item for item in self.standard_candidate_facts}
        commons = {item.fact_sha256: item for item in self.common_lemmas}
        influences = {item.fact_sha256: item for item in self.influence_facts}

        for frontier in self.frontier_facts:
            missing_scalar_ref = next(
                (ref for ref in frontier.candidate_scalar_refs if ref not in scalars),
                None,
            )
            if missing_scalar_ref is not None:
                _raise_structural_error(
                    _M8StructuralErrorCode.DANGLING_REFERENCE,
                    "M8 frontier has a dangling candidate-scalar reference",
                    fact_sha256=frontier.fact_sha256,
                    dependency_sha256=missing_scalar_ref,
                )
            referenced = tuple(scalars[ref] for ref in frontier.candidate_scalar_refs)
            self._require_dependency_context(frontier, referenced)
            partition_mismatch = next(
                (
                    item
                    for item in referenced
                    if (
                        item.problem_id,
                        item.problem_sha256,
                        item.candidate_set_id,
                        item.candidate_set_sha256,
                        item.material_partition,
                        item.fit_config_sha256,
                    )
                    != (
                        frontier.problem_id,
                        frontier.problem_sha256,
                        frontier.candidate_set_id,
                        frontier.candidate_set_sha256,
                        frontier.material_partition,
                        frontier.fit_config_sha256,
                    )
                ),
                None,
            )
            if partition_mismatch is not None:
                _raise_structural_error(
                    _M8StructuralErrorCode.PARTITION_MISMATCH,
                    "M8 frontier scalar partition bindings differ",
                    fact_sha256=frontier.fact_sha256,
                    dependency_sha256=partition_mismatch.fact_sha256,
                )

        previous_by_stream: dict[str, M8CommonTransitionLemmaV2] = {}
        for common in self.common_lemmas:
            previous = previous_by_stream.get(common.stream_id)
            if previous is None:
                if common.previous_common_lemma_ref is not None:
                    _raise_structural_error(
                        _M8StructuralErrorCode.EVENT_ORDER_MISMATCH,
                        "M8 first common lemma must use baseline fallback cursor",
                        fact_sha256=common.fact_sha256,
                        dependency_sha256=common.previous_common_lemma_ref,
                    )
                if common.baseline_fallback_cursor_sha256 != common.cursor_before_sha256:
                    _raise_structural_error(
                        _M8StructuralErrorCode.CURSOR_CHAIN_MISMATCH,
                        "M8 first common lemma baseline fallback differs from cursor before",
                        fact_sha256=common.fact_sha256,
                    )
            else:
                if (
                    common.event_position != previous.event_position + 1
                    or common.previous_common_lemma_ref != previous.fact_sha256
                    or common.baseline_fallback_cursor_sha256 is not None
                ):
                    _raise_structural_error(
                        _M8StructuralErrorCode.EVENT_ORDER_MISMATCH,
                        "M8 common lemma previous reference skips or reverses event order",
                        fact_sha256=common.fact_sha256,
                        dependency_sha256=previous.fact_sha256,
                    )
                if common.cursor_before_sha256 != previous.cursor_after_sha256:
                    _raise_structural_error(
                        _M8StructuralErrorCode.CURSOR_CHAIN_MISMATCH,
                        "M8 common lemma cursor chain is discontinuous",
                        fact_sha256=common.fact_sha256,
                        dependency_sha256=previous.fact_sha256,
                    )
                if (
                    common.cursor_before_inventory_remnant_ids
                    != previous.cursor_after_inventory_remnant_ids
                ):
                    _raise_structural_error(
                        _M8StructuralErrorCode.CURSOR_CHAIN_MISMATCH,
                        "M8 common lemma inventory chain is discontinuous",
                        fact_sha256=common.fact_sha256,
                        dependency_sha256=previous.fact_sha256,
                    )
                if (
                    common.portable_transition.cursor_before
                    != previous.portable_transition.cursor_after
                ):
                    _raise_structural_error(
                        _M8StructuralErrorCode.CURSOR_CHAIN_MISMATCH,
                        "M8 common lemma full portable cursor chain is discontinuous",
                        fact_sha256=common.fact_sha256,
                        dependency_sha256=previous.fact_sha256,
                    )
            previous_by_stream[common.stream_id] = common

            common_dependencies: list[_M8FactV2] = []
            event_dependencies: list[_M8FactV2] = []
            for refs, index, label in (
                (common.candidate_scalar_refs, scalars, "candidate-scalar"),
                (common.frontier_refs, frontiers, "frontier"),
                (common.standard_candidate_refs, standards, "standard-candidate"),
                (common.translation_batch_refs, translations, "translation-batch"),
            ):
                missing_ref = next((ref for ref in refs if ref not in index), None)
                if missing_ref is not None:
                    _raise_structural_error(
                        _M8StructuralErrorCode.DANGLING_REFERENCE,
                        f"M8 common lemma has a dangling {label} reference",
                        fact_sha256=common.fact_sha256,
                        dependency_sha256=missing_ref,
                    )
                resolved = [index[ref] for ref in refs]
                common_dependencies.extend(resolved)
                if label in {"standard-candidate", "translation-batch"}:
                    event_dependencies.extend(resolved)
            self._require_dependency_context(common, tuple(common_dependencies))
            out_of_order_dependency = next(
                (
                    item
                    for item in event_dependencies
                    if item.event_position != common.event_position
                ),
                None,
            )
            if out_of_order_dependency is not None:
                _raise_structural_error(
                    _M8StructuralErrorCode.EVENT_ORDER_MISMATCH,
                    "M8 common lemma dependency has out-of-order event context",
                    fact_sha256=common.fact_sha256,
                    dependency_sha256=out_of_order_dependency.fact_sha256,
                )

            scalar_items = tuple(scalars[ref] for ref in common.candidate_scalar_refs)
            frontier_items = tuple(frontiers[ref] for ref in common.frontier_refs)
            common_partition = (
                common.problem_id,
                common.problem_sha256,
                common.candidate_set_id,
                common.candidate_set_sha256,
                common.fit_config_sha256,
            )
            partition_dependency = next(
                (
                    item
                    for item in (*scalar_items, *frontier_items)
                    if (
                        item.problem_id,
                        item.problem_sha256,
                        item.candidate_set_id,
                        item.candidate_set_sha256,
                        item.fit_config_sha256,
                    )
                    != common_partition
                ),
                None,
            )
            if partition_dependency is not None:
                _raise_structural_error(
                    _M8StructuralErrorCode.PARTITION_MISMATCH,
                    "M8 common candidate partition bindings differ",
                    fact_sha256=common.fact_sha256,
                    dependency_sha256=partition_dependency.fact_sha256,
                )
            translation_items = tuple(translations[ref] for ref in common.translation_batch_refs)
            configuration_dependency = next(
                (
                    item
                    for item in translation_items
                    if item.fit_config_sha256 != common.fit_config_sha256
                    or item.search_config_sha256 != common.search_config_sha256
                ),
                None,
            )
            if configuration_dependency is not None:
                _raise_structural_error(
                    _M8StructuralErrorCode.CONFIGURATION_MISMATCH,
                    "M8 common translation configuration bindings differ",
                    fact_sha256=common.fact_sha256,
                    dependency_sha256=configuration_dependency.fact_sha256,
                )
            for classification in common.inventory_classifications:
                frontier = frontiers.get(classification.frontier_ref or "")
                if frontier is None:
                    if classification.frontier_ref is not None:
                        _raise_structural_error(
                            _M8StructuralErrorCode.DANGLING_REFERENCE,
                            "M8 common inventory has a dangling frontier reference",
                            fact_sha256=common.fact_sha256,
                            dependency_sha256=classification.frontier_ref,
                        )
                    if (
                        classification.classification != "exact_survivor"
                        or classification.candidate_scalar_refs
                    ):
                        _raise_structural_error(
                            _M8StructuralErrorCode.INCOMPLETE_EVIDENCE,
                            "M8 common inventory lacks its required frontier reference",
                            fact_sha256=common.fact_sha256,
                        )
                    scalar_candidate_ids: set[str] = set()
                else:
                    if classification.candidate_scalar_refs != frontier.candidate_scalar_refs:
                        _raise_structural_error(
                            _M8StructuralErrorCode.INCOMPLETE_EVIDENCE,
                            "M8 common inventory lacks the complete frontier scalar set",
                            fact_sha256=common.fact_sha256,
                            dependency_sha256=frontier.fact_sha256,
                        )
                    scalar_candidate_ids = {
                        scalars[reference].candidate_id
                        for reference in classification.candidate_scalar_refs
                    }
                translation_candidate_ids: set[str] = set()
                for reference in classification.translation_batch_refs:
                    translation = translations.get(reference)
                    if translation is None:
                        _raise_structural_error(
                            _M8StructuralErrorCode.DANGLING_REFERENCE,
                            "M8 common inventory has a dangling translation reference",
                            fact_sha256=common.fact_sha256,
                            dependency_sha256=reference,
                        )
                    if (
                        translation.remnant_id != classification.remnant_id
                        or translation.event_position != common.event_position
                    ):
                        _raise_structural_error(
                            _M8StructuralErrorCode.TRANSLATION_MISMATCH,
                            "M8 common inventory translation identity differs",
                            fact_sha256=common.fact_sha256,
                            dependency_sha256=translation.fact_sha256,
                        )
                    if (
                        scalar_candidate_ids
                        and translation.candidate_id not in scalar_candidate_ids
                    ):
                        _raise_structural_error(
                            _M8StructuralErrorCode.PARTITION_MISMATCH,
                            "M8 common translation candidate is absent from frontier scalars",
                            fact_sha256=common.fact_sha256,
                            dependency_sha256=translation.fact_sha256,
                        )
                    translation_candidate_ids.add(translation.candidate_id)
                counted_evidence = classification.classification == "counted_no_fit" or (
                    classification.exact_replay_reason == "counted_search_survivor"
                )
                if counted_evidence and translation_candidate_ids != scalar_candidate_ids:
                    _raise_structural_error(
                        _M8StructuralErrorCode.INCOMPLETE_EVIDENCE,
                        "M8 counted common lacks the complete candidate translation set",
                        fact_sha256=common.fact_sha256,
                        dependency_sha256=classification.frontier_ref,
                    )

            standard_items = tuple(standards[ref] for ref in common.standard_candidate_refs)
            if len(standard_items) != common.portable_transition.event.standard_action_count:
                _raise_structural_error(
                    _M8StructuralErrorCode.INCOMPLETE_EVIDENCE,
                    "M8 common standard action count differs from its complete profile set",
                    fact_sha256=common.fact_sha256,
                )
            expected_profiles = tuple(range(len(standard_items)))
            if tuple(item.profile_position for item in standard_items) != expected_profiles:
                misplaced_standard = next(
                    item
                    for item, position in zip(standard_items, expected_profiles, strict=True)
                    if item.profile_position != position
                )
                _raise_structural_error(
                    _M8StructuralErrorCode.INCOMPLETE_EVIDENCE,
                    "M8 common lemma lacks complete ordered standard candidates",
                    fact_sha256=common.fact_sha256,
                    dependency_sha256=misplaced_standard.fact_sha256,
                )
            wrong_policy_standard = next(
                (item for item in standard_items if item.policy_name != common.policy_name),
                None,
            )
            if wrong_policy_standard is not None:
                _raise_structural_error(
                    _M8StructuralErrorCode.STANDARD_PROFILE_MISMATCH,
                    "M8 common standard candidates use a different policy",
                    fact_sha256=common.fact_sha256,
                    dependency_sha256=wrong_policy_standard.fact_sha256,
                )
            standard_candidate_ids = tuple(item.candidate_id for item in standard_items)
            standard_catalog_ids = tuple(item.catalog_action_id for item in standard_items)
            if len(standard_candidate_ids) != len(set(standard_candidate_ids)) or len(
                standard_catalog_ids
            ) != len(set(standard_catalog_ids)):
                seen_candidates: dict[str, M8StandardCandidateFactV2] = {}
                seen_catalogs: dict[str, M8StandardCandidateFactV2] = {}
                duplicate_standard: M8StandardCandidateFactV2 | None = None
                conflicting_standard: M8StandardCandidateFactV2 | None = None
                for item in standard_items:
                    conflicting_standard = seen_candidates.get(item.candidate_id) or (
                        seen_catalogs.get(item.catalog_action_id)
                    )
                    if conflicting_standard is not None:
                        duplicate_standard = item
                        break
                    seen_candidates[item.candidate_id] = item
                    seen_catalogs[item.catalog_action_id] = item
                if duplicate_standard is None or conflicting_standard is None:  # pragma: no cover
                    _raise_structural_error(
                        _M8StructuralErrorCode.DUPLICATE_IDENTITY,
                        "M8 common standard candidates contain duplicate identities",
                        fact_sha256=common.fact_sha256,
                    )
                _raise_structural_error(
                    _M8StructuralErrorCode.DUPLICATE_IDENTITY,
                    "M8 common standard candidates contain duplicate candidate or catalog "
                    "identities",
                    fact_sha256=duplicate_standard.fact_sha256,
                    dependency_sha256=conflicting_standard.fact_sha256,
                )
            standard_by_candidate = {item.candidate_id: item for item in standard_items}
            scalar_by_candidate = {item.candidate_id: item for item in scalar_items}
            if scalar_items and set(standard_by_candidate) != set(scalar_by_candidate):
                asymmetric_candidate = next(
                    candidate_id
                    for candidate_id in (*standard_by_candidate, *scalar_by_candidate)
                    if (candidate_id in standard_by_candidate)
                    != (candidate_id in scalar_by_candidate)
                )
                asymmetric_fact = (
                    standard_by_candidate.get(asymmetric_candidate)
                    or scalar_by_candidate[asymmetric_candidate]
                )
                _raise_structural_error(
                    _M8StructuralErrorCode.PARTITION_MISMATCH,
                    "M8 common standard and scalar candidate sets differ",
                    fact_sha256=common.fact_sha256,
                    dependency_sha256=asymmetric_fact.fact_sha256,
                )
            ranked_standards = tuple(
                (
                    _policy_rank_value(item.policy_name, item.comparison_key),
                    item.fact_sha256,
                )
                for item in standard_items
            )
            minimum_standard_ref = min(ranked_standards, key=lambda item: item[0])[1]
            if common.minimum_standard_candidate_ref != minimum_standard_ref:
                _raise_structural_error(
                    _M8StructuralErrorCode.POLICY_MINIMUM_MISMATCH,
                    "M8 minimum standard reference is not the policy minimum standard profile",
                    fact_sha256=common.fact_sha256,
                    dependency_sha256=common.minimum_standard_candidate_ref,
                )
            minimum_standard = standards[common.minimum_standard_candidate_ref]
            transition = common.portable_transition
            if transition.event.action.kind == "open_standard_sheet":
                if (
                    minimum_standard.materialized_action_id is None
                    or minimum_standard.catalog_action_id != common.selected_catalog_action_id
                    or minimum_standard.materialized_action_id
                    != common.selected_materialized_action_id
                    or minimum_standard.candidate_id != common.selected_candidate_id
                    or minimum_standard.policy_name != common.policy_name
                    or minimum_standard.comparison_key != common.selected_comparison_key
                    or minimum_standard.decision_key != common.selected_decision_key
                ):
                    _raise_structural_error(
                        _M8StructuralErrorCode.STANDARD_PROFILE_MISMATCH,
                        "M8 common selected action differs from its standard candidate",
                        fact_sha256=common.fact_sha256,
                        dependency_sha256=minimum_standard.fact_sha256,
                    )
                if (
                    minimum_standard.immediate_net_cost_bits
                    != common.selected_immediate_net_cost_bits
                ):
                    _raise_structural_error(
                        _M8StructuralErrorCode.STANDARD_PROFILE_MISMATCH,
                        "M8 common selected immediate cost differs from its standard candidate",
                        fact_sha256=common.fact_sha256,
                        dependency_sha256=minimum_standard.fact_sha256,
                    )
                selected_context = transition.selected_context
                if (
                    minimum_standard.catalog_action_id != selected_context.action_id
                    or minimum_standard.action_kind != selected_context.kind
                    or minimum_standard.candidate_id != selected_context.candidate_id
                    or minimum_standard.candidate_width_bits
                    != selected_context.candidate_width_bits
                    or minimum_standard.selected_stock_id != selected_context.selected_stock_id
                    or minimum_standard.immediate_net_cost_bits
                    != selected_context.immediate_net_cost_bits
                    or minimum_standard.selected_remnant_age_hours_bits
                    != selected_context.selected_remnant_age_hours_bits
                    or minimum_standard.returned_regularity_bits
                    != selected_context.returned_regularity_bits
                    or minimum_standard.known_order_lookahead_term_bits
                    != selected_context.known_order_lookahead_term_bits
                ):
                    _raise_structural_error(
                        _M8StructuralErrorCode.STANDARD_PROFILE_MISMATCH,
                        "M8 selected standard profile differs from its portable policy context",
                        fact_sha256=common.fact_sha256,
                        dependency_sha256=minimum_standard.fact_sha256,
                    )
                action = transition.event.action
                accounting = action.accounting
                if (
                    minimum_standard.materialized_action_id != action.action_id
                    or minimum_standard.candidate_id != action.candidate_id
                    or minimum_standard.action_kind != action.kind
                    or minimum_standard.parent_remnant_area_bits
                    != accounting.parent_remnant_area_bits
                    or minimum_standard.placed_area_bits != accounting.placed_area_bits
                    or minimum_standard.process_loss_area_bits != accounting.process_loss_area_bits
                    or minimum_standard.retained_child_area_bits
                    != accounting.retained_child_area_bits
                    or minimum_standard.scrap_area_bits != accounting.scrap_area_bits
                    or minimum_standard.reconciliation_delta_bits
                    != accounting.reconciliation_delta_bits
                    or minimum_standard.accounting_area_tolerance_bits
                    != accounting.area_tolerance_bits
                    or minimum_standard.returned_remnant_count != len(action.returned_remnants)
                ):
                    _raise_structural_error(
                        _M8StructuralErrorCode.STANDARD_PROFILE_MISMATCH,
                        "M8 selected standard profile differs from its portable action accounting",
                        fact_sha256=common.fact_sha256,
                        dependency_sha256=minimum_standard.fact_sha256,
                    )
                event_costs = transition.event.delta_costs
                if (
                    minimum_standard.purchase_cost_bits != event_costs.purchase_cost_bits
                    or minimum_standard.return_handling_cost_bits
                    != event_costs.return_handling_cost_bits
                    or minimum_standard.retrieval_handling_cost_bits
                    != event_costs.retrieval_handling_cost_bits
                    or minimum_standard.scrap_proceeds_bits != event_costs.scrap_proceeds_bits
                    or minimum_standard.terminal_scrap_credit_bits
                    != event_costs.terminal_scrap_credit_bits
                ):
                    _raise_structural_error(
                        _M8StructuralErrorCode.STANDARD_PROFILE_MISMATCH,
                        "M8 selected standard profile differs from shared portable event cost "
                        "components",
                        fact_sha256=common.fact_sha256,
                        dependency_sha256=minimum_standard.fact_sha256,
                    )
                # Policy storage prices retained children; event storage prices elapsed
                # pre-event inventory. The policy immediate cost is therefore bound to
                # selected_context above, not to the replay event's net/storage costs.
            if common.replay_input_id != self.provenance.replay_input_id or (
                common.replay_input_sha256 != self.provenance.replay_input_sha256
            ):
                _raise_structural_error(
                    _M8StructuralErrorCode.REPLAY_CONTEXT_MISMATCH,
                    "M8 common lemma replay context differs from bundle provenance",
                    fact_sha256=common.fact_sha256,
                )

        for influence in self.influence_facts:
            common = commons.get(influence.common_lemma_ref)
            if common is None:
                _raise_structural_error(
                    _M8StructuralErrorCode.DANGLING_REFERENCE,
                    "M8 influence has a dangling common-lemma reference",
                    fact_sha256=influence.fact_sha256,
                    dependency_sha256=influence.common_lemma_ref,
                )
            self._require_dependency_context(influence, (common,))
            if influence.event_position != common.event_position:
                _raise_structural_error(
                    _M8StructuralErrorCode.EVENT_ORDER_MISMATCH,
                    "M8 influence common reference is out of event order",
                    fact_sha256=influence.fact_sha256,
                    dependency_sha256=common.fact_sha256,
                )
            expected_candidate_ids = {
                standards[reference].candidate_id for reference in common.standard_candidate_refs
            }
            expected_delta = {
                *(("removed", item) for item in influence.inventory_delta.removed_remnant_ids),
                *(("added", item) for item in influence.inventory_delta.added_remnant_ids),
            }
            expanded_rejections = tuple(
                evidence
                for evidence in influence.rejection_evidence
                if isinstance(evidence, M8RejectionEvidenceV2)
            )
            compact_rejections = tuple(
                evidence
                for evidence in influence.rejection_evidence
                if isinstance(evidence, M8CandidateScalarGroupEvidenceV2)
            )
            expected_rejection_pairs = {
                (direction, remnant_id, candidate_id)
                for direction, remnant_id in expected_delta
                for candidate_id in expected_candidate_ids
            }
            expanded_rejection_pairs = tuple(
                (evidence.direction, evidence.remnant_id, evidence.candidate_id)
                for evidence in expanded_rejections
            )
            compact_rejection_pairs = tuple(
                (evidence.direction, evidence.remnant_id) for evidence in compact_rejections
            )
            incomplete_expanded = expanded_rejection_pairs and (
                len(expanded_rejection_pairs) != len(set(expanded_rejection_pairs))
                or set(expanded_rejection_pairs) != expected_rejection_pairs
            )
            incomplete_compact = compact_rejection_pairs and (
                len(compact_rejection_pairs) != len(set(compact_rejection_pairs))
                or set(compact_rejection_pairs) != expected_delta
            )
            if incomplete_expanded or incomplete_compact:
                _raise_structural_error(
                    _M8StructuralErrorCode.INCOMPLETE_EVIDENCE,
                    "M8 influence lacks the complete rejection candidate set",
                    fact_sha256=influence.fact_sha256,
                    dependency_sha256=common.fact_sha256,
                )
            search_candidates: dict[tuple[str, str], set[str]] = {}
            for evidence in influence.search_evidence:
                search_candidates.setdefault((evidence.direction, evidence.remnant_id), set()).add(
                    evidence.candidate_id
                )
            if search_candidates and (
                not set(search_candidates) <= expected_delta
                or any(
                    candidates != expected_candidate_ids
                    for candidates in search_candidates.values()
                )
            ):
                _raise_structural_error(
                    _M8StructuralErrorCode.INCOMPLETE_EVIDENCE,
                    "M8 influence lacks the complete search candidate set",
                    fact_sha256=influence.fact_sha256,
                    dependency_sha256=common.fact_sha256,
                )
            if any(
                item.candidate_id not in expected_candidate_ids
                for item in influence.competitor_evidence
            ):
                _raise_structural_error(
                    _M8StructuralErrorCode.PARTITION_MISMATCH,
                    "M8 influence competitor candidate is outside the event set",
                    fact_sha256=influence.fact_sha256,
                    dependency_sha256=common.fact_sha256,
                )
            scalar_refs = tuple(
                reference
                for evidence in influence.rejection_evidence
                for reference in (
                    (evidence.candidate_scalar_ref,)
                    if isinstance(evidence, M8RejectionEvidenceV2)
                    else evidence.candidate_scalar_refs
                )
            )
            missing_scalar_ref = next((ref for ref in scalar_refs if ref not in scalars), None)
            if missing_scalar_ref is not None:
                _raise_structural_error(
                    _M8StructuralErrorCode.DANGLING_REFERENCE,
                    "M8 influence has a dangling candidate-scalar reference",
                    fact_sha256=influence.fact_sha256,
                    dependency_sha256=missing_scalar_ref,
                )
            scalar_items = tuple(scalars[ref] for ref in scalar_refs)
            self._require_dependency_context(influence, scalar_items)
            for evidence in expanded_rejections:
                scalar = scalars[evidence.candidate_scalar_ref]
                if (
                    evidence.candidate_id != scalar.candidate_id
                    or scalar.problem_id != common.problem_id
                    or scalar.problem_sha256 != common.problem_sha256
                    or scalar.candidate_set_id != common.candidate_set_id
                    or scalar.candidate_set_sha256 != common.candidate_set_sha256
                    or scalar.fit_config_sha256 != common.fit_config_sha256
                    or evidence.layout_area_bits != scalar.layout_area_bits
                    or evidence.layout_width_bits != scalar.layout_width_bits
                    or evidence.layout_height_bits != scalar.layout_height_bits
                ):
                    _raise_structural_error(
                        _M8StructuralErrorCode.PARTITION_MISMATCH,
                        "M8 influence rejection scalar partition differs from "
                        "referenced scalar identity or scalar measurements",
                        fact_sha256=influence.fact_sha256,
                        dependency_sha256=scalar.fact_sha256,
                    )
            for group in compact_rejections:
                group_scalars = tuple(
                    scalars[reference] for reference in group.candidate_scalar_refs
                )
                scalar_candidate_ids = tuple(item.candidate_id for item in group_scalars)
                if (
                    len(scalar_candidate_ids) != len(set(scalar_candidate_ids))
                    or set(scalar_candidate_ids) != expected_candidate_ids
                ):
                    _raise_structural_error(
                        _M8StructuralErrorCode.INCOMPLETE_EVIDENCE,
                        "M8 influence lacks the complete rejection candidate set",
                        fact_sha256=influence.fact_sha256,
                        dependency_sha256=common.fact_sha256,
                    )
                mismatched_scalar = next(
                    (
                        scalar
                        for scalar in group_scalars
                        if scalar.problem_id != common.problem_id
                        or scalar.problem_sha256 != common.problem_sha256
                        or scalar.candidate_set_id != common.candidate_set_id
                        or scalar.candidate_set_sha256 != common.candidate_set_sha256
                        or scalar.fit_config_sha256 != common.fit_config_sha256
                    ),
                    None,
                )
                if mismatched_scalar is not None:
                    _raise_structural_error(
                        _M8StructuralErrorCode.PARTITION_MISMATCH,
                        "M8 influence rejection scalar partition differs from referenced "
                        "scalar identity",
                        fact_sha256=influence.fact_sha256,
                        dependency_sha256=mismatched_scalar.fact_sha256,
                    )
            translation_refs = tuple(
                search.translation_batch_ref for search in influence.search_evidence
            )
            missing_translation_ref = next(
                (ref for ref in translation_refs if ref not in translations),
                None,
            )
            if missing_translation_ref is not None:
                _raise_structural_error(
                    _M8StructuralErrorCode.DANGLING_REFERENCE,
                    "M8 influence has a dangling translation-batch reference",
                    fact_sha256=influence.fact_sha256,
                    dependency_sha256=missing_translation_ref,
                )
            translation_items = tuple(translations[ref] for ref in translation_refs)
            self._require_dependency_context(influence, translation_items)
            translation_by_ref = {item.fact_sha256: item for item in translation_items}
            for search in influence.search_evidence:
                translation = translation_by_ref[search.translation_batch_ref]
                if (
                    search.remnant_id != translation.remnant_id
                    or search.candidate_id != translation.candidate_id
                    or search.search_config_sha256 != translation.search_config_sha256
                    or translation.fit_config_sha256 != common.fit_config_sha256
                    or translation.search_config_sha256 != common.search_config_sha256
                    or translation.source_order != search.search_config.candidate_source_order
                    or search.generated_candidate_count != translation.generated_candidate_count
                    or search.duplicate_candidate_count != translation.duplicate_candidate_count
                    or search.budget_truncated != translation.budget_truncated
                    or translation.event_position != influence.event_position
                ):
                    _raise_structural_error(
                        _M8StructuralErrorCode.CONFIGURATION_MISMATCH,
                        "M8 influence translation configuration bindings differ",
                        fact_sha256=influence.fact_sha256,
                        dependency_sha256=translation.fact_sha256,
                    )
                maximum_candidates = search.search_config.maximum_candidates
                if (
                    translation.budget_truncated
                    and (
                        len(translation.translations) != maximum_candidates
                        or translation.generated_candidate_count != maximum_candidates + 1
                    )
                ) or (
                    not translation.budget_truncated
                    and translation.generated_candidate_count > maximum_candidates
                ):
                    _raise_structural_error(
                        _M8StructuralErrorCode.TRANSLATION_MISMATCH,
                        "M8 influence translation exceeds its registered budget",
                        fact_sha256=influence.fact_sha256,
                        dependency_sha256=translation.fact_sha256,
                    )
                if search.result == "no_witness_within_registered_search":
                    sequence_matches = search.evaluated_candidate_count == len(
                        translation.translations
                    )
                else:
                    sequence_matches = (
                        1 <= search.evaluated_candidate_count <= len(translation.translations)
                        and search.selected_translation
                        == translation.translations[search.evaluated_candidate_count - 1]
                    )
                if not sequence_matches:
                    _raise_structural_error(
                        _M8StructuralErrorCode.TRANSLATION_MISMATCH,
                        "M8 influence search differs from translation sequence",
                        fact_sha256=influence.fact_sha256,
                        dependency_sha256=translation.fact_sha256,
                    )
            fit_searches = {
                (search.direction, search.remnant_id, search.candidate_id)
                for search in influence.search_evidence
                if search.result == "fit"
            }
            if any(
                (
                    competitor.direction,
                    competitor.selected_remnant_id,
                    competitor.candidate_id,
                )
                not in fit_searches
                for competitor in influence.competitor_evidence
            ):
                _raise_structural_error(
                    _M8StructuralErrorCode.INCOMPLETE_EVIDENCE,
                    "M8 influence competitor lacks a matching fit search",
                    fact_sha256=influence.fact_sha256,
                )
            if any(
                competitor.policy_name != common.policy_name
                for competitor in influence.competitor_evidence
            ):
                _raise_structural_error(
                    _M8StructuralErrorCode.POLICY_MINIMUM_MISMATCH,
                    "M8 influence competitor uses a different policy",
                    fact_sha256=influence.fact_sha256,
                    dependency_sha256=common.fact_sha256,
                )
            if influence.common_catalog_action_id != common.selected_catalog_action_id or (
                influence.common_materialized_action_id != common.selected_materialized_action_id
            ):
                _raise_structural_error(
                    _M8StructuralErrorCode.ACTION_BINDING_MISMATCH,
                    "M8 influence common action binding differs from its lemma",
                    fact_sha256=influence.fact_sha256,
                    dependency_sha256=common.fact_sha256,
                )

        reachable: set[str] = set()

        def visit_common(reference: str) -> None:
            pending: list[tuple[str, bool]] = [(reference, False)]
            while pending:
                current_reference, dependencies_pending = pending.pop()
                if not dependencies_pending and current_reference in reachable:
                    continue
                common = commons[current_reference]
                if dependencies_pending:
                    reachable.update(common.candidate_scalar_refs)
                    reachable.update(common.frontier_refs)
                    reachable.update(common.standard_candidate_refs)
                    reachable.update(common.translation_batch_refs)
                    for frontier_ref in common.frontier_refs:
                        reachable.update(frontiers[frontier_ref].candidate_scalar_refs)
                    continue
                reachable.add(current_reference)
                pending.append((current_reference, True))
                if common.previous_common_lemma_ref is not None:
                    pending.append((common.previous_common_lemma_ref, False))

        for root in self.action_roots:
            expected_positions = tuple(
                range(root.start_event_position + 1, root.stop_event_position)
            )
            if len(root.common_lemma_refs) != len(expected_positions):
                _raise_structural_error(
                    _M8StructuralErrorCode.INCOMPLETE_EVIDENCE,
                    "M8 action root lacks ordered complete event coverage",
                    fact_sha256=root.fact_sha256,
                )
            dangling_event_ref = next(
                (ref for ref in root.common_lemma_refs if ref not in commons),
                next((ref for ref in root.influence_fact_refs if ref not in influences), None),
            )
            if dangling_event_ref is not None:
                _raise_structural_error(
                    _M8StructuralErrorCode.DANGLING_REFERENCE,
                    "M8 action root contains a dangling event reference",
                    fact_sha256=root.fact_sha256,
                    dependency_sha256=dangling_event_ref,
                )
            root_commons = tuple(commons[ref] for ref in root.common_lemma_refs)
            root_influences = tuple(influences[ref] for ref in root.influence_fact_refs)
            if (
                tuple(item.event_position for item in root_commons) != expected_positions
                or tuple(item.event_position for item in root_influences) != expected_positions
            ):
                misplaced_dependency = next(
                    (
                        item
                        for item, position in zip(root_commons, expected_positions, strict=True)
                        if item.event_position != position
                    ),
                    next(
                        (
                            item
                            for item, position in zip(
                                root_influences,
                                expected_positions,
                                strict=True,
                            )
                            if item.event_position != position
                        ),
                        None,
                    ),
                )
                _raise_structural_error(
                    _M8StructuralErrorCode.EVENT_ORDER_MISMATCH,
                    "M8 action root lacks ordered complete event coverage",
                    fact_sha256=root.fact_sha256,
                    dependency_sha256=(
                        misplaced_dependency.fact_sha256
                        if misplaced_dependency is not None
                        else None
                    ),
                )
            mismatched_influence = next(
                (
                    influence
                    for common, influence in zip(root_commons, root_influences, strict=True)
                    if influence.common_lemma_ref != common.fact_sha256
                ),
                None,
            )
            if mismatched_influence is not None:
                _raise_structural_error(
                    _M8StructuralErrorCode.ACTION_BINDING_MISMATCH,
                    "M8 action root common/influence event bindings differ",
                    fact_sha256=root.fact_sha256,
                    dependency_sha256=mismatched_influence.fact_sha256,
                )
            foreign_branch = next(
                (item for item in root_influences if item.root_action_id != root.action_id),
                None,
            )
            if foreign_branch is not None:
                _raise_structural_error(
                    _M8StructuralErrorCode.ACTION_BINDING_MISMATCH,
                    "M8 influence branch differs from its root action",
                    fact_sha256=root.fact_sha256,
                    dependency_sha256=foreign_branch.fact_sha256,
                )
            if root_influences:
                if root_influences[0].state_before_sha256 != root.initial_state_after_sha256:
                    _raise_structural_error(
                        _M8StructuralErrorCode.STATE_CHAIN_MISMATCH,
                        "M8 action root post-initial state differs from influence chain",
                        fact_sha256=root.fact_sha256,
                        dependency_sha256=root_influences[0].fact_sha256,
                    )
                if root_influences[-1].state_after_sha256 != root.final_state_sha256:
                    _raise_structural_error(
                        _M8StructuralErrorCode.STATE_CHAIN_MISMATCH,
                        "M8 action root terminal state differs from influence chain",
                        fact_sha256=root.fact_sha256,
                        dependency_sha256=root_influences[-1].fact_sha256,
                    )
                discontinuous_influence = next(
                    (
                        current
                        for previous, current in zip(
                            root_influences,
                            root_influences[1:],
                            strict=False,
                        )
                        if previous.state_after_sha256 != current.state_before_sha256
                    ),
                    None,
                )
                if discontinuous_influence is not None:
                    _raise_structural_error(
                        _M8StructuralErrorCode.STATE_CHAIN_MISMATCH,
                        "M8 action root influence state chain is discontinuous",
                        fact_sha256=root.fact_sha256,
                        dependency_sha256=discontinuous_influence.fact_sha256,
                    )
            elif root.initial_state_after_sha256 != root.final_state_sha256:
                _raise_structural_error(
                    _M8StructuralErrorCode.STATE_CHAIN_MISMATCH,
                    "empty M8 action root must preserve its post-initial terminal state",
                    fact_sha256=root.fact_sha256,
                )
            self._require_dependency_context(root, (*root_commons, *root_influences))
            if root.suffix_sha256 != self.provenance.suffix_sha256:
                _raise_structural_error(
                    _M8StructuralErrorCode.REPLAY_CONTEXT_MISMATCH,
                    "M8 action root suffix differs from bundle provenance",
                    fact_sha256=root.fact_sha256,
                )
            reachable.add(root.fact_sha256)
            for common in root_commons:
                visit_common(common.fact_sha256)
            for influence in root_influences:
                reachable.add(influence.fact_sha256)
                reachable.update(
                    reference
                    for evidence in influence.rejection_evidence
                    for reference in (
                        (evidence.candidate_scalar_ref,)
                        if isinstance(evidence, M8RejectionEvidenceV2)
                        else evidence.candidate_scalar_refs
                    )
                )
                reachable.update(
                    search.translation_batch_ref for search in influence.search_evidence
                )

        serialized = set(all_hashes)
        unused = serialized - reachable
        if unused:
            first_unused = next(
                item.fact_sha256 for item in all_entries if item.fact_sha256 in unused
            )
            _raise_structural_error(
                _M8StructuralErrorCode.UNUSED_FACT,
                "M8 bundle contains unused fixed-layer facts",
                fact_sha256=first_unused,
            )
        first_root = self.action_roots[0]
        first_root_context = (
            first_root.baseline_action_id,
            first_root.baseline_catalog_action_id,
            first_root.start_event_position,
            first_root.stop_event_position,
            first_root.suffix_sha256,
            first_root.start_state_sha256,
            first_root.common_lemma_refs,
        )
        conflicting_root = next(
            (
                root
                for root in self.action_roots[1:]
                if (
                    root.baseline_action_id,
                    root.baseline_catalog_action_id,
                    root.start_event_position,
                    root.stop_event_position,
                    root.suffix_sha256,
                    root.start_state_sha256,
                    root.common_lemma_refs,
                )
                != first_root_context
            ),
            None,
        )
        if conflicting_root is not None:
            _raise_structural_error(
                _M8StructuralErrorCode.ROOT_CONTEXT_MISMATCH,
                "M8 action roots differ from one root suffix/baseline context",
                fact_sha256=conflicting_root.fact_sha256,
                dependency_sha256=first_root.fact_sha256,
            )
        seen_catalog_ids: dict[str, M8ActionRootV2] = {}
        duplicate_catalog_root = None
        first_catalog_root = None
        for root in self.action_roots:
            if root.catalog_action_id in seen_catalog_ids:
                duplicate_catalog_root = root
                first_catalog_root = seen_catalog_ids[root.catalog_action_id]
                break
            seen_catalog_ids[root.catalog_action_id] = root
        if duplicate_catalog_root is not None and first_catalog_root is not None:
            _raise_structural_error(
                _M8StructuralErrorCode.DUPLICATE_IDENTITY,
                "M8 action roots contain duplicate catalog action IDs",
                fact_sha256=duplicate_catalog_root.fact_sha256,
                dependency_sha256=first_catalog_root.fact_sha256,
            )
        return self

    @staticmethod
    def _require_dependency_context(
        owner: _M8FactV2,
        dependencies: tuple[_M8FactV2, ...],
    ) -> None:
        expected = (owner.semantic_runtime_sha256, owner.stream_id)
        mismatched_dependency = next(
            (
                item
                for item in dependencies
                if (item.semantic_runtime_sha256, item.stream_id) != expected
            ),
            None,
        )
        if mismatched_dependency is not None:
            _raise_structural_error(
                _M8StructuralErrorCode.CONTEXT_MISMATCH,
                "M8 fact dependency has cross-runtime or cross-stream context",
                fact_sha256=owner.fact_sha256,
                dependency_sha256=mismatched_dependency.fact_sha256,
            )


__all__ = [
    "M8ActionRootV2",
    "M8BundleProvenanceV2",
    "M8CandidateScalarGroupEvidenceV2",
    "M8CandidateScalarFactV2",
    "M8CanonicalF64",
    "M8CanonicalUtc",
    "M8CommonTransitionLemmaV2",
    "M8CompetitorEvidenceV2",
    "M8DominanceEvidenceV2",
    "M8FrontierFactV2",
    "M8InfluenceFactV2",
    "M8InventoryDeltaV2",
    "M8PortableTranslationBatch",
    "M8RejectionEvidenceClaimV2",
    "M8RejectionEvidenceV2",
    "M8SearchEvidenceV2",
    "M8Sha256",
    "M8StandardCandidateFactV2",
    "M8TranslationPointV2",
    "M8UncheckedFactBundleV2",
    "canonical_semantic_json",
    "decode_canonical_f64",
    "decode_canonical_utc",
    "encode_canonical_f64",
    "encode_canonical_utc",
    "m8_bundle_sha256",
    "m8_fact_sha256",
]
