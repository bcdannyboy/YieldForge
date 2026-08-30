"""Compact authenticated sidecars for complete Gate 3 validity evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zlib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from gzip import GzipFile
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    canonical_pretty_json_bytes,
    semantic_sha256,
)
from yieldforge.oracle.artifact_publisher import (
    M8ArtifactPublicationError,
    publish_immutable_artifact,
)
from yieldforge.realistic_falsification.confirmation import (
    Gate3BaselineCalibrationFreeze,
    Gate3BaselinePolicyId,
    Gate3CorpusId,
    Gate3HardNullKind,
    Gate3NoSignalSummary,
    Gate3RootBinding,
    Gate3ValidityReceipt,
)

_COMPRESSION = "gzip-level-6-mtime-0-flags-0"
_MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
_MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
_COST_PATTERN = r"^(?:0|[1-9][0-9]*)\.[0-9]{6}$"
_SIGNED_COST_PATTERN = r"^-?(?:0|[1-9][0-9]*)\.[0-9]{6}$"
_METRIC_PATTERN = r"^-?(?:0|[1-9][0-9]*)\.[0-9]{12}$"
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_CORPUS_ORDER: tuple[Gate3CorpusId, Gate3CorpusId] = (
    "lectra-m3-m4",
    "loco-2dics",
)


class Gate3ValidityEvidenceError(ValueError):
    """Gate 3 validity evidence failed a bounded fail-closed check."""


class Gate3ValidityBaselineFreezeBinding(FrozenExperimentModel):
    corpus_id: Gate3CorpusId
    freeze_id: StrictStr = Field(pattern=r"^yfm11g3bf-[0-9a-f]{24}$")
    freeze_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    selected_policy_id: Gate3BaselinePolicyId


class Gate3ValidityHardNullRow(FrozenExperimentModel):
    registration_id: StrictStr = Field(pattern=r"^yfm11null-[0-9a-f]{24}$")
    registration_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    control_trace_id: StrictStr = Field(pattern=r"^yfm11g3hn-[0-9a-f]{24}$")
    control_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    corpus_id: Gate3CorpusId
    null_kind: Gate3HardNullKind
    maximum_absolute_cost_difference: StrictStr = Field(
        max_length=64,
        pattern=_COST_PATTERN,
    )
    passes: StrictBool

    @model_validator(mode="after")
    def require_frozen_tolerance_decision(self) -> Self:
        expected = Decimal(self.maximum_absolute_cost_difference) <= Decimal("0.000001")
        if self.passes is not expected:
            raise ValueError("Gate 3 compact hard-null tolerance decision differs")
        return self


class Gate3ValidityTwinControlRow(FrozenExperimentModel):
    """Compact twin metric row; validity is decided only by corpus summaries."""

    control_id: StrictStr = Field(pattern=r"^yfm11g3twin-[0-9a-f]{24}$")
    control_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    source_stream_id: StrictStr = Field(min_length=1, max_length=128)
    twin_stream_id: StrictStr = Field(min_length=1, max_length=128)
    twin_cell_id: StrictStr = Field(pattern=r"^yfm11g3cell-[0-9a-f]{24}$")
    twin_cell_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    corpus_id: Gate3CorpusId
    baseline_cost: StrictStr = Field(max_length=64, pattern=_SIGNED_COST_PATTERN)
    full_future_cost: StrictStr = Field(max_length=64, pattern=_SIGNED_COST_PATTERN)
    known_only_cost: StrictStr = Field(max_length=64, pattern=_SIGNED_COST_PATTERN)
    no_signal_savings_percent: StrictStr = Field(max_length=64, pattern=_METRIC_PATTERN)

    @model_validator(mode="after")
    def require_recomputed_metric(self) -> Self:
        baseline = Decimal(self.baseline_cost)
        if baseline <= 0:
            raise ValueError("Gate 3 compact twin requires a positive baseline")
        with localcontext() as context:
            context.prec = 50
            expected = Decimal(100) * (baseline - Decimal(self.full_future_cost)) / baseline
        formatted = format(
            expected.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP),
            ".12f",
        )
        if self.no_signal_savings_percent != formatted:
            raise ValueError("Gate 3 compact twin savings does not reconcile")
        return self


class Gate3ValidityExactAuditRow(FrozenExperimentModel):
    registration_id: StrictStr = Field(pattern=r"^yfm11audit-[0-9a-f]{24}$")
    registration_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    trace_id: StrictStr = Field(pattern=r"^yfm11g3audit-[0-9a-f]{24}$")
    trace_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    corpus_id: Gate3CorpusId
    audit_ordinal: StrictInt = Field(ge=0, le=5)
    economic_arm: Literal["central", "adverse", "null"]
    selected_action_id: StrictStr = Field(min_length=1, max_length=512)
    selected_is_exact_optimal: StrictBool
    passes: StrictBool

    @model_validator(mode="after")
    def require_exact_decision(self) -> Self:
        if self.passes is not self.selected_is_exact_optimal:
            raise ValueError("Gate 3 compact exact-audit decision differs")
        return self


class Gate3ValidityEvidenceReceipt(FrozenExperimentModel):
    """Compact authenticated index for one full validity receipt sidecar."""

    schema_version: Literal["yieldforge.m11-gate3-validity-evidence.v1"] = (
        "yieldforge.m11-gate3-validity-evidence.v1"
    )
    evidence_id: StrictStr = Field(pattern=r"^yfm11g3valrcpt-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    roots: Gate3RootBinding
    baseline_freezes: tuple[
        Gate3ValidityBaselineFreezeBinding,
        Gate3ValidityBaselineFreezeBinding,
    ]
    validity_receipt_id: StrictStr = Field(pattern=r"^yfm11g3valid-[0-9a-f]{24}$")
    validity_receipt_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    hard_nulls: tuple[
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
    ]
    twin_controls: tuple[Gate3ValidityTwinControlRow, ...] = Field(
        min_length=40,
        max_length=40,
    )
    exact_audits: tuple[Gate3ValidityExactAuditRow, ...] = Field(
        min_length=12,
        max_length=12,
    )
    no_signal_summaries: tuple[Gate3NoSignalSummary, Gate3NoSignalSummary]
    failure_codes: tuple[Annotated[StrictStr, Field(min_length=1, max_length=128)], ...] = Field(
        max_length=20
    )
    diagnosis_codes: tuple[Annotated[StrictStr, Field(min_length=1, max_length=128)], ...] = Field(
        max_length=2
    )
    status: Literal["valid", "diagnosis_required", "invalid"]
    exact_control_census: Literal[True] = True
    raw_controls_revalidated: Literal[True] = True
    source_lineage: Literal["repaired_runtime"] = "repaired_runtime"
    sidecar_name: StrictStr = Field(
        pattern=(
            r"^m11-gate3-validity-receipt-"
            r"[0-9a-f]{64}-[0-9a-f]{64}\.json\.gz$"
        )
    )
    compressed_raw_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    compressed_byte_count: StrictInt = Field(gt=0, le=_MAX_COMPRESSED_BYTES)
    uncompressed_byte_count: StrictInt = Field(gt=0, le=_MAX_UNCOMPRESSED_BYTES)
    compression: Literal["gzip-level-6-mtime-0-flags-0"] = _COMPRESSION

    @model_validator(mode="after")
    def require_exact_census_decision_and_identity(self) -> Self:
        if tuple(item.corpus_id for item in self.baseline_freezes) != _CORPUS_ORDER:
            raise ValueError("Gate 3 validity freeze binding order differs")
        hard_null_corpora = ("lectra-m3-m4",) * 3 + ("loco-2dics",) * 3
        hard_null_kinds = (
            "single_action",
            "unique_materials_single_action",
            "all_work_known_single_action",
        ) * 2
        if (
            tuple(item.corpus_id for item in self.hard_nulls) != hard_null_corpora
            or tuple(item.null_kind for item in self.hard_nulls) != hard_null_kinds
        ):
            raise ValueError("Gate 3 compact hard-null census differs")
        if tuple(item.corpus_id for item in self.twin_controls) != (
            ("lectra-m3-m4",) * 20 + ("loco-2dics",) * 20
        ):
            raise ValueError("Gate 3 compact twin census differs")
        if tuple(item.corpus_id for item in self.exact_audits) != (
            ("lectra-m3-m4",) * 6 + ("loco-2dics",) * 6
        ):
            raise ValueError("Gate 3 compact exact-audit corpus census differs")
        if tuple(item.audit_ordinal for item in self.exact_audits) != tuple(range(6)) * 2:
            raise ValueError("Gate 3 compact exact-audit ordinal census differs")
        if (
            tuple(item.economic_arm for item in self.exact_audits)
            != (
                "central",
                "central",
                "adverse",
                "adverse",
                "null",
                "null",
            )
            * 2
        ):
            raise ValueError("Gate 3 compact exact-audit arm census differs")
        uniqueness_groups = (
            tuple(item.freeze_id for item in self.baseline_freezes),
            tuple(item.freeze_content_sha256 for item in self.baseline_freezes),
            tuple(item.registration_id for item in self.hard_nulls),
            tuple(item.registration_content_sha256 for item in self.hard_nulls),
            tuple(item.control_trace_id for item in self.hard_nulls),
            tuple(item.control_content_sha256 for item in self.hard_nulls),
            tuple(item.control_id for item in self.twin_controls),
            tuple(item.control_content_sha256 for item in self.twin_controls),
            tuple(item.source_stream_id for item in self.twin_controls),
            tuple(item.twin_stream_id for item in self.twin_controls),
            tuple(item.twin_cell_id for item in self.twin_controls),
            tuple(item.twin_cell_content_sha256 for item in self.twin_controls),
            tuple(item.registration_id for item in self.exact_audits),
            tuple(item.registration_content_sha256 for item in self.exact_audits),
            tuple(item.trace_id for item in self.exact_audits),
            tuple(item.trace_content_sha256 for item in self.exact_audits),
        )
        if any(len(set(values)) != len(values) for values in uniqueness_groups):
            raise ValueError("Gate 3 compact validity identities must be unique")
        for twin in self.twin_controls:
            twin_semantic = {
                "schema_version": "yieldforge.m11-gate3-twin-control.v1",
                "roots": self.roots.model_dump(mode="json"),
                "source_stream_id": twin.source_stream_id,
                "twin_stream_id": twin.twin_stream_id,
                "corpus_id": twin.corpus_id,
                "twin_cell_id": twin.twin_cell_id,
                "twin_cell_content_sha256": twin.twin_cell_content_sha256,
                "baseline_cost": twin.baseline_cost,
                "full_future_cost": twin.full_future_cost,
                "known_only_cost": twin.known_only_cost,
                "no_signal_savings_percent": twin.no_signal_savings_percent,
            }
            twin_digest = semantic_sha256(twin_semantic)
            if twin.control_id != f"yfm11g3twin-{twin_digest[:24]}" or (
                twin.control_content_sha256 != f"sha256:{twin_digest}"
            ):
                raise ValueError("Gate 3 compact twin identity differs")
        expected_summaries = []
        for corpus_id, rows, summary in zip(
            _CORPUS_ORDER,
            (self.twin_controls[:20], self.twin_controls[20:]),
            self.no_signal_summaries,
            strict=True,
        ):
            with localcontext() as context:
                context.prec = 50
                mean = sum(
                    (Decimal(item.no_signal_savings_percent) for item in rows),
                    Decimal(0),
                ) / Decimal(20)
            formatted = format(
                mean.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP),
                ".12f",
            )
            classification = (
                "invalid"
                if mean > Decimal("0.5")
                else "diagnosis_required"
                if mean >= Decimal("0.3")
                else "clean"
            )
            if (
                summary.corpus_id != corpus_id
                or summary.control_ids != tuple(item.control_id for item in rows)
                or summary.control_content_sha256s
                != tuple(item.control_content_sha256 for item in rows)
                or summary.mean_no_signal_savings_percent != formatted
                or summary.classification != classification
            ):
                raise ValueError("Gate 3 compact no-signal summary differs from twin rows")
            expected_summaries.append(summary)
        failures = tuple(
            sorted(
                (
                    *(
                        f"hard_null:{item.registration_id}"
                        for item in self.hard_nulls
                        if not item.passes
                    ),
                    *(
                        f"exact_audit:{item.registration_id}"
                        for item in self.exact_audits
                        if not item.passes
                    ),
                    *(
                        f"no_signal:{item.corpus_id}"
                        for item in expected_summaries
                        if item.classification == "invalid"
                    ),
                )
            )
        )
        diagnoses = tuple(
            sorted(
                f"no_signal:{item.corpus_id}"
                for item in expected_summaries
                if item.classification == "diagnosis_required"
            )
        )
        status = "invalid" if failures else "diagnosis_required" if diagnoses else "valid"
        if (
            self.failure_codes != failures
            or self.diagnosis_codes != diagnoses
            or self.status != status
        ):
            raise ValueError("Gate 3 compact validity decision differs")
        validity_hash = self.validity_receipt_content_sha256.removeprefix("sha256:")
        compressed_hash = self.compressed_raw_sha256.removeprefix("sha256:")
        if self.validity_receipt_id != f"yfm11g3valid-{validity_hash[:24]}":
            raise ValueError("Gate 3 compact validity receipt binding differs")
        if self.sidecar_name != (
            f"m11-gate3-validity-receipt-{validity_hash}-{compressed_hash}.json.gz"
        ):
            raise ValueError("Gate 3 compact validity sidecar binding differs")
        digest = semantic_sha256(self, excluded_fields={"evidence_id", "content_sha256"})
        if self.evidence_id != f"yfm11g3valrcpt-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 compact validity evidence identity differs")
        return self


def deterministic_gzip(data: bytes) -> bytes:
    """Return a reproducible gzip transport with frozen header fields."""

    output = BytesIO()
    with GzipFile(
        filename="",
        mode="wb",
        compresslevel=6,
        fileobj=output,
        mtime=0,
    ) as archive:
        archive.write(data)
    return output.getvalue()


def _iter_canonical_json_bytes(value: Gate3ValidityReceipt):
    """Yield the canonical JSON encoding without retaining a second raw copy."""

    payload = value.model_dump(mode="json")
    encoder = json.JSONEncoder(
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    for chunk in encoder.iterencode(payload):
        yield chunk.encode("utf-8")
    yield b"\n"


def _stream_canonical_gzip(
    validity: Gate3ValidityReceipt,
) -> tuple[bytes, int]:
    """Compress canonical chunks while enforcing the raw bound incrementally."""

    output = BytesIO()
    uncompressed_byte_count = 0
    with GzipFile(
        filename="",
        mode="wb",
        compresslevel=6,
        fileobj=output,
        mtime=0,
    ) as archive:
        for chunk in _iter_canonical_json_bytes(validity):
            uncompressed_byte_count += len(chunk)
            if uncompressed_byte_count > _MAX_UNCOMPRESSED_BYTES:
                raise Gate3ValidityEvidenceError(
                    "Gate 3 validity sidecar exceeds the uncompressed byte bound"
                )
            archive.write(chunk)
    compressed = output.getvalue()
    _validate_gzip_header(compressed)
    if len(compressed) > _MAX_COMPRESSED_BYTES:
        raise Gate3ValidityEvidenceError(
            "Gate 3 validity sidecar exceeds the compressed byte bound"
        )
    return compressed, uncompressed_byte_count


def _strict_validity_receipt(receipt: Gate3ValidityReceipt) -> Gate3ValidityReceipt:
    try:
        return Gate3ValidityReceipt.model_validate(
            receipt.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3ValidityEvidenceError(
            "Gate 3 validity receipt failed strict validation"
        ) from error


def _strict_baseline_freezes(
    baseline_freezes: tuple[
        Gate3BaselineCalibrationFreeze,
        Gate3BaselineCalibrationFreeze,
    ],
    *,
    expected_roots: Gate3RootBinding,
) -> tuple[Gate3BaselineCalibrationFreeze, Gate3BaselineCalibrationFreeze]:
    try:
        if type(baseline_freezes) is not tuple or len(baseline_freezes) != 2:
            raise TypeError("two freezes required")
        strict = tuple(
            Gate3BaselineCalibrationFreeze.model_validate(
                item.model_dump(mode="python", round_trip=True),
                strict=True,
            )
            for item in baseline_freezes
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3ValidityEvidenceError(
            "Gate 3 validity baseline freezes failed strict validation"
        ) from error
    if tuple(item.corpus_id for item in strict) != _CORPUS_ORDER or any(
        item.roots != expected_roots for item in strict
    ):
        raise Gate3ValidityEvidenceError(
            "Gate 3 validity baseline freezes differ from roots or corpus order"
        )
    return strict  # type: ignore[return-value]


def _freeze_bindings(
    freezes: tuple[Gate3BaselineCalibrationFreeze, Gate3BaselineCalibrationFreeze],
) -> tuple[
    Gate3ValidityBaselineFreezeBinding,
    Gate3ValidityBaselineFreezeBinding,
]:
    bindings = tuple(
        Gate3ValidityBaselineFreezeBinding(
            corpus_id=item.corpus_id,
            freeze_id=item.freeze_id,
            freeze_content_sha256=item.content_sha256,
            selected_policy_id=item.selected_policy_id,
        )
        for item in freezes
    )
    return bindings  # type: ignore[return-value]


def _compact_hard_nulls(receipt: Gate3ValidityReceipt):  # type: ignore[no-untyped-def]
    return tuple(
        Gate3ValidityHardNullRow(
            registration_id=item.registration.null_id,
            registration_content_sha256=item.registration.content_sha256,
            control_trace_id=item.control_trace_id,
            control_content_sha256=item.content_sha256,
            corpus_id=item.corpus_id,
            null_kind=item.null_kind,
            maximum_absolute_cost_difference=item.maximum_absolute_cost_difference,
            passes=item.passes,
        )
        for item in receipt.hard_nulls
    )


def _compact_twins(receipt: Gate3ValidityReceipt) -> tuple[Gate3ValidityTwinControlRow, ...]:
    return tuple(
        Gate3ValidityTwinControlRow(
            control_id=item.control_id,
            control_content_sha256=item.content_sha256,
            source_stream_id=item.source_stream_id,
            twin_stream_id=item.twin_stream_id,
            twin_cell_id=item.twin_cell_id,
            twin_cell_content_sha256=item.twin_cell_content_sha256,
            corpus_id=item.corpus_id,
            baseline_cost=item.baseline_cost,
            full_future_cost=item.full_future_cost,
            known_only_cost=item.known_only_cost,
            no_signal_savings_percent=item.no_signal_savings_percent,
        )
        for item in receipt.twin_controls
    )


def _compact_audits(receipt: Gate3ValidityReceipt) -> tuple[Gate3ValidityExactAuditRow, ...]:
    return tuple(
        Gate3ValidityExactAuditRow(
            registration_id=item.registration.audit_id,
            registration_content_sha256=item.registration.content_sha256,
            trace_id=item.trace_id,
            trace_content_sha256=item.content_sha256,
            corpus_id=item.corpus_id,
            audit_ordinal=item.registration.audit_ordinal,
            economic_arm=item.economic_arm,
            selected_action_id=item.selected_action_id,
            selected_is_exact_optimal=item.selected_is_exact_optimal,
            passes=item.passes,
        )
        for item in receipt.exact_audits
    )


def _validate_receipt_freeze_bindings(
    receipt: Gate3ValidityReceipt,
    freezes: tuple[Gate3BaselineCalibrationFreeze, Gate3BaselineCalibrationFreeze],
) -> None:
    by_corpus = {item.corpus_id: item for item in freezes}
    if any(
        item.baseline_freeze != by_corpus[item.corpus_id]
        for item in (*receipt.hard_nulls, *receipt.exact_audits)
    ):
        raise Gate3ValidityEvidenceError(
            "Gate 3 validity controls differ from the supplied baseline freezes"
        )


def canonical_gate3_validity_receipt_bytes(receipt: Gate3ValidityReceipt) -> bytes:
    """Strictly detach and canonically serialize one complete validity receipt."""

    return canonical_pretty_json_bytes(_strict_validity_receipt(receipt))


@dataclass(frozen=True, slots=True)
class _PreparedValidityEvidence:
    compressed_bytes: bytes
    evidence_receipt: Gate3ValidityEvidenceReceipt


def _build_evidence_receipt(
    validity: Gate3ValidityReceipt,
    freezes: tuple[Gate3BaselineCalibrationFreeze, Gate3BaselineCalibrationFreeze],
    *,
    uncompressed_byte_count: int,
    compressed_bytes: bytes,
) -> Gate3ValidityEvidenceReceipt:
    validity_hash = validity.content_sha256.removeprefix("sha256:")
    compressed_hash = hashlib.sha256(compressed_bytes).hexdigest()
    freeze_bindings = _freeze_bindings(freezes)
    hard_nulls = _compact_hard_nulls(validity)
    twin_controls = _compact_twins(validity)
    exact_audits = _compact_audits(validity)
    semantic = {
        "schema_version": "yieldforge.m11-gate3-validity-evidence.v1",
        "roots": validity.roots.model_dump(mode="json"),
        "baseline_freezes": [item.model_dump(mode="json") for item in freeze_bindings],
        "validity_receipt_id": validity.receipt_id,
        "validity_receipt_content_sha256": validity.content_sha256,
        "hard_nulls": [item.model_dump(mode="json") for item in hard_nulls],
        "twin_controls": [item.model_dump(mode="json") for item in twin_controls],
        "exact_audits": [item.model_dump(mode="json") for item in exact_audits],
        "no_signal_summaries": [
            item.model_dump(mode="json") for item in validity.no_signal_summaries
        ],
        "failure_codes": validity.failure_codes,
        "diagnosis_codes": validity.diagnosis_codes,
        "status": validity.status,
        "exact_control_census": validity.exact_control_census,
        "raw_controls_revalidated": validity.raw_controls_revalidated,
        "source_lineage": "repaired_runtime",
        "sidecar_name": (f"m11-gate3-validity-receipt-{validity_hash}-{compressed_hash}.json.gz"),
        "compressed_raw_sha256": f"sha256:{compressed_hash}",
        "compressed_byte_count": len(compressed_bytes),
        "uncompressed_byte_count": uncompressed_byte_count,
        "compression": _COMPRESSION,
    }
    digest = semantic_sha256(semantic)
    return Gate3ValidityEvidenceReceipt(
        evidence_id=f"yfm11g3valrcpt-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        roots=validity.roots,
        baseline_freezes=freeze_bindings,
        validity_receipt_id=validity.receipt_id,
        validity_receipt_content_sha256=validity.content_sha256,
        hard_nulls=hard_nulls,  # type: ignore[arg-type]
        twin_controls=twin_controls,
        exact_audits=exact_audits,
        no_signal_summaries=validity.no_signal_summaries,
        failure_codes=validity.failure_codes,
        diagnosis_codes=validity.diagnosis_codes,
        status=validity.status,
        exact_control_census=validity.exact_control_census,
        raw_controls_revalidated=validity.raw_controls_revalidated,
        source_lineage="repaired_runtime",
        sidecar_name=semantic["sidecar_name"],
        compressed_raw_sha256=semantic["compressed_raw_sha256"],
        compressed_byte_count=len(compressed_bytes),
        uncompressed_byte_count=uncompressed_byte_count,
    )


def _prepare_validity_evidence(
    validity_receipt: Gate3ValidityReceipt,
    *,
    baseline_freezes: tuple[
        Gate3BaselineCalibrationFreeze,
        Gate3BaselineCalibrationFreeze,
    ],
) -> _PreparedValidityEvidence:
    strict = _strict_validity_receipt(validity_receipt)
    freezes = _strict_baseline_freezes(baseline_freezes, expected_roots=strict.roots)
    _validate_receipt_freeze_bindings(strict, freezes)
    compressed, uncompressed_byte_count = _stream_canonical_gzip(strict)
    evidence_receipt = _build_evidence_receipt(
        strict,
        freezes,
        uncompressed_byte_count=uncompressed_byte_count,
        compressed_bytes=compressed,
    )
    return _PreparedValidityEvidence(
        compressed_bytes=compressed,
        evidence_receipt=evidence_receipt,
    )


def build_gate3_validity_evidence_receipt(
    validity_receipt: Gate3ValidityReceipt,
    *,
    baseline_freezes: tuple[
        Gate3BaselineCalibrationFreeze,
        Gate3BaselineCalibrationFreeze,
    ],
) -> Gate3ValidityEvidenceReceipt:
    """Build a compact receipt from one complete full validity receipt."""

    return _prepare_validity_evidence(
        validity_receipt,
        baseline_freezes=baseline_freezes,
    ).evidence_receipt


def _strict_evidence_receipt(
    receipt: Gate3ValidityEvidenceReceipt,
) -> Gate3ValidityEvidenceReceipt:
    try:
        return Gate3ValidityEvidenceReceipt.model_validate(
            receipt.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3ValidityEvidenceError(
            "Gate 3 validity evidence receipt failed strict validation"
        ) from error


def _read_bounded_regular_file(path: Path) -> bytes:
    candidate = Path(path)
    try:
        before = candidate.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > _MAX_COMPRESSED_BYTES
        ):
            raise Gate3ValidityEvidenceError(
                "Gate 3 validity sidecar must be a bounded regular file"
            )
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                before.st_dev,
                before.st_ino,
            ):
                raise Gate3ValidityEvidenceError(
                    "Gate 3 validity sidecar must be a bounded regular file"
                )
            chunks: list[bytes] = []
            remaining = _MAX_COMPRESSED_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            during = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = candidate.lstat()
    except Gate3ValidityEvidenceError:
        raise
    except OSError as error:
        raise Gate3ValidityEvidenceError(
            "Gate 3 validity sidecar could not be read safely"
        ) from error
    before_fingerprint = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if (
        len(raw) > _MAX_COMPRESSED_BYTES
        or len(raw) != before.st_size
        or before_fingerprint
        != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        or before_fingerprint
        != (
            during.st_dev,
            during.st_ino,
            during.st_size,
            during.st_mtime_ns,
            during.st_ctime_ns,
        )
        or before_fingerprint
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
    ):
        raise Gate3ValidityEvidenceError("Gate 3 validity sidecar changed during read-back")
    return raw


def _validate_gzip_header(data: bytes) -> None:
    if (
        type(data) is not bytes
        or len(data) < 10
        or data[:3] != b"\x1f\x8b\x08"
        or data[3] != 0
        or data[4:8] != b"\x00\x00\x00\x00"
    ):
        raise Gate3ValidityEvidenceError(
            "Gate 3 validity gzip header differs from the frozen contract"
        )


def _bounded_gzip_decompress(data: bytes) -> bytearray:
    try:
        with GzipFile(fileobj=BytesIO(data), mode="rb") as archive:
            raw = bytearray()
            while True:
                remaining = _MAX_UNCOMPRESSED_BYTES + 1 - len(raw)
                if remaining <= 0:
                    raise Gate3ValidityEvidenceError(
                        "Gate 3 validity sidecar exceeds the uncompressed byte bound"
                    )
                chunk = archive.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > _MAX_UNCOMPRESSED_BYTES:
                    raise Gate3ValidityEvidenceError(
                        "Gate 3 validity sidecar exceeds the uncompressed byte bound"
                    )
    except Gate3ValidityEvidenceError:
        raise
    except (EOFError, OSError, zlib.error) as error:
        raise Gate3ValidityEvidenceError("Gate 3 validity sidecar is not valid gzip") from error
    return raw


def _json_object_without_duplicates(pairs):  # type: ignore[no-untyped-def]
    output = {}
    for key, value in pairs:
        if key in output:
            raise Gate3ValidityEvidenceError("Gate 3 validity JSON contains a duplicate object key")
        output[key] = value
    return output


def _reject_nonfinite_json(value: str):
    raise Gate3ValidityEvidenceError(f"Gate 3 validity JSON contains non-finite value {value}")


def _parse_strict_validity_json(raw: bytes | bytearray) -> Gate3ValidityReceipt:
    try:
        json.loads(
            raw,
            object_pairs_hook=_json_object_without_duplicates,
            parse_constant=_reject_nonfinite_json,
        )
        return Gate3ValidityReceipt.model_validate_json(raw, strict=True)
    except Gate3ValidityEvidenceError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise Gate3ValidityEvidenceError(
            "Gate 3 validity sidecar failed strict semantic validation"
        ) from error


def _has_canonical_encoding(
    validity: Gate3ValidityReceipt,
    raw: bytes | bytearray,
) -> bool:
    view = memoryview(raw)
    offset = 0
    for chunk in _iter_canonical_json_bytes(validity):
        stop = offset + len(chunk)
        if stop > len(view) or view[offset:stop] != chunk:
            return False
        offset = stop
    return offset == len(view)


def _validate_sidecar_bytes(
    data: bytes,
    *,
    evidence_receipt: Gate3ValidityEvidenceReceipt,
    baseline_freezes: tuple[
        Gate3BaselineCalibrationFreeze,
        Gate3BaselineCalibrationFreeze,
    ],
) -> Gate3ValidityReceipt:
    if type(data) is not bytes:
        raise Gate3ValidityEvidenceError("Gate 3 validity sidecar requires exact bytes")
    _validate_gzip_header(data)
    if len(data) > _MAX_COMPRESSED_BYTES:
        raise Gate3ValidityEvidenceError(
            "Gate 3 validity sidecar exceeds the compressed byte bound"
        )
    if len(data) != evidence_receipt.compressed_byte_count:
        raise Gate3ValidityEvidenceError("Gate 3 validity compressed byte count differs")
    compressed_sha = f"sha256:{hashlib.sha256(data).hexdigest()}"
    if compressed_sha != evidence_receipt.compressed_raw_sha256:
        raise Gate3ValidityEvidenceError("Gate 3 validity compressed raw hash differs")
    raw = _bounded_gzip_decompress(data)
    if len(raw) != evidence_receipt.uncompressed_byte_count:
        raise Gate3ValidityEvidenceError("Gate 3 validity uncompressed byte count differs")
    validity = _parse_strict_validity_json(raw)
    if not _has_canonical_encoding(validity, raw):
        raise Gate3ValidityEvidenceError("Gate 3 validity receipt encoding is not canonical")
    _validate_receipt_freeze_bindings(validity, baseline_freezes)
    rederived = _build_evidence_receipt(
        validity,
        baseline_freezes,
        uncompressed_byte_count=len(raw),
        compressed_bytes=data,
    )
    if rederived != evidence_receipt:
        raise Gate3ValidityEvidenceError("Gate 3 validity sidecar differs from its compact receipt")
    return validity


def _strict_compact_tuple(values, model, expected_length: int, label: str):  # type: ignore[no-untyped-def]
    try:
        if type(values) is not tuple or len(values) != expected_length:
            raise TypeError("exact tuple required")
        return tuple(
            model.model_validate(
                item.model_dump(mode="python", round_trip=True),
                strict=True,
            )
            for item in values
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3ValidityEvidenceError(
            f"Gate 3 validity expected {label} are malformed"
        ) from error


def _strict_expected_values(
    *,
    expected_roots: Gate3RootBinding,
    expected_baseline_freezes: tuple[
        Gate3BaselineCalibrationFreeze,
        Gate3BaselineCalibrationFreeze,
    ],
    expected_validity_receipt_id: str,
    expected_validity_receipt_content_sha256: str,
    expected_hard_nulls: tuple[
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
    ],
    expected_twin_controls: tuple[Gate3ValidityTwinControlRow, ...],
    expected_exact_audits: tuple[Gate3ValidityExactAuditRow, ...],
    expected_no_signal_summaries: tuple[Gate3NoSignalSummary, Gate3NoSignalSummary],
    expected_failure_codes: tuple[str, ...],
    expected_diagnosis_codes: tuple[str, ...],
    expected_status: Literal["valid", "diagnosis_required", "invalid"],
    expected_exact_control_census: bool,
    expected_raw_controls_revalidated: bool,
    expected_source_lineage: Literal["repaired_runtime"],
):  # type: ignore[no-untyped-def]
    try:
        strict_roots = Gate3RootBinding.model_validate(
            expected_roots.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3ValidityEvidenceError("Gate 3 validity expected roots are malformed") from error
    freezes = _strict_baseline_freezes(
        expected_baseline_freezes,
        expected_roots=strict_roots,
    )
    hard_nulls = _strict_compact_tuple(
        expected_hard_nulls,
        Gate3ValidityHardNullRow,
        6,
        "hard-null rows",
    )
    twins = _strict_compact_tuple(
        expected_twin_controls,
        Gate3ValidityTwinControlRow,
        40,
        "twin rows",
    )
    audits = _strict_compact_tuple(
        expected_exact_audits,
        Gate3ValidityExactAuditRow,
        12,
        "exact-audit rows",
    )
    summaries = _strict_compact_tuple(
        expected_no_signal_summaries,
        Gate3NoSignalSummary,
        2,
        "no-signal summaries",
    )
    if (
        type(expected_validity_receipt_id) is not str
        or re.fullmatch(r"yfm11g3valid-[0-9a-f]{24}", expected_validity_receipt_id) is None
        or type(expected_validity_receipt_content_sha256) is not str
        or re.fullmatch(_SHA256_PATTERN, expected_validity_receipt_content_sha256) is None
        or type(expected_failure_codes) is not tuple
        or any(type(item) is not str for item in expected_failure_codes)
        or type(expected_diagnosis_codes) is not tuple
        or any(type(item) is not str for item in expected_diagnosis_codes)
        or type(expected_status) is not str
        or expected_status not in ("valid", "diagnosis_required", "invalid")
        or expected_exact_control_census is not True
        or expected_raw_controls_revalidated is not True
        or type(expected_source_lineage) is not str
        or expected_source_lineage != "repaired_runtime"
    ):
        raise Gate3ValidityEvidenceError("Gate 3 validity expected compact values are malformed")
    return (
        strict_roots,
        freezes,
        expected_validity_receipt_id,
        expected_validity_receipt_content_sha256,
        hard_nulls,
        twins,
        audits,
        summaries,
        expected_failure_codes,
        expected_diagnosis_codes,
        expected_status,
    )


def publish_gate3_validity_evidence(
    output_directory: Path,
    validity_receipt: Gate3ValidityReceipt,
    *,
    baseline_freezes: tuple[
        Gate3BaselineCalibrationFreeze,
        Gate3BaselineCalibrationFreeze,
    ],
) -> tuple[Path, Gate3ValidityEvidenceReceipt]:
    """Prepare once and publish one immutable full validity sidecar."""

    prepared = _prepare_validity_evidence(
        validity_receipt,
        baseline_freezes=baseline_freezes,
    )
    path = Path(output_directory) / prepared.evidence_receipt.sidecar_name
    try:
        published = publish_immutable_artifact(
            path,
            prepared.compressed_bytes,
            validate=(lambda data: data if data == prepared.compressed_bytes else b""),
            label="M11 Gate 3 validity evidence",
        )
    except M8ArtifactPublicationError as error:
        raise Gate3ValidityEvidenceError("Gate 3 validity immutable publication failed") from error
    return published, prepared.evidence_receipt


def load_gate3_validity_evidence(
    path: Path,
    *,
    evidence_receipt: Gate3ValidityEvidenceReceipt,
    expected_roots: Gate3RootBinding,
    expected_baseline_freezes: tuple[
        Gate3BaselineCalibrationFreeze,
        Gate3BaselineCalibrationFreeze,
    ],
    expected_validity_receipt_id: str,
    expected_validity_receipt_content_sha256: str,
    expected_hard_nulls: tuple[
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
        Gate3ValidityHardNullRow,
    ],
    expected_twin_controls: tuple[Gate3ValidityTwinControlRow, ...],
    expected_exact_audits: tuple[Gate3ValidityExactAuditRow, ...],
    expected_no_signal_summaries: tuple[Gate3NoSignalSummary, Gate3NoSignalSummary],
    expected_failure_codes: tuple[str, ...],
    expected_diagnosis_codes: tuple[str, ...],
    expected_status: Literal["valid", "diagnosis_required", "invalid"],
    expected_exact_control_census: bool,
    expected_raw_controls_revalidated: bool,
    expected_source_lineage: Literal["repaired_runtime"],
) -> Gate3ValidityReceipt:
    """Load a full receipt only under exact compact and caller bindings."""

    strict_evidence = _strict_evidence_receipt(evidence_receipt)
    expected = _strict_expected_values(
        expected_roots=expected_roots,
        expected_baseline_freezes=expected_baseline_freezes,
        expected_validity_receipt_id=expected_validity_receipt_id,
        expected_validity_receipt_content_sha256=(expected_validity_receipt_content_sha256),
        expected_hard_nulls=expected_hard_nulls,
        expected_twin_controls=expected_twin_controls,
        expected_exact_audits=expected_exact_audits,
        expected_no_signal_summaries=expected_no_signal_summaries,
        expected_failure_codes=expected_failure_codes,
        expected_diagnosis_codes=expected_diagnosis_codes,
        expected_status=expected_status,
        expected_exact_control_census=expected_exact_control_census,
        expected_raw_controls_revalidated=expected_raw_controls_revalidated,
        expected_source_lineage=expected_source_lineage,
    )
    (
        strict_roots,
        strict_freezes,
        strict_validity_id,
        strict_validity_sha,
        strict_hard_nulls,
        strict_twins,
        strict_audits,
        strict_summaries,
        strict_failures,
        strict_diagnoses,
        strict_status,
    ) = expected
    if (
        strict_evidence.roots != strict_roots
        or strict_evidence.baseline_freezes != _freeze_bindings(strict_freezes)
        or strict_evidence.validity_receipt_id != strict_validity_id
        or strict_evidence.validity_receipt_content_sha256 != strict_validity_sha
        or strict_evidence.hard_nulls != strict_hard_nulls
        or strict_evidence.twin_controls != strict_twins
        or strict_evidence.exact_audits != strict_audits
        or strict_evidence.no_signal_summaries != strict_summaries
        or strict_evidence.failure_codes != strict_failures
        or strict_evidence.diagnosis_codes != strict_diagnoses
        or strict_evidence.status != strict_status
        or strict_evidence.exact_control_census is not True
        or strict_evidence.raw_controls_revalidated is not True
        or strict_evidence.source_lineage != "repaired_runtime"
    ):
        raise Gate3ValidityEvidenceError(
            "Gate 3 validity receipt differs from expected compact values"
        )
    candidate = Path(path)
    if candidate.name != strict_evidence.sidecar_name:
        raise Gate3ValidityEvidenceError("Gate 3 validity sidecar filename differs")
    data = _read_bounded_regular_file(candidate)
    return _validate_sidecar_bytes(
        data,
        evidence_receipt=strict_evidence,
        baseline_freezes=strict_freezes,
    )
