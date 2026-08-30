"""Compact authenticated sidecars for complete Gate 3 central cells."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, DecimalException, localcontext
from enum import Enum
from gzip import GzipFile
from io import BytesIO
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import to_json

from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    semantic_sha256,
)
from yieldforge.oracle.artifact_publisher import (
    M8ArtifactPublicationError,
    publish_immutable_artifact,
)
from yieldforge.realistic_falsification.confirmation import (
    GATE3_BASELINE_POLICY_IDS,
    Gate3BaselineCalibrationFreeze,
    Gate3BaselinePolicyId,
    Gate3CorpusId,
    Gate3CostLedger,
    Gate3RootBinding,
    Gate3StreamCell,
    Gate3Visibility,
)
from yieldforge.realistic_falsification.economic_decision import (
    EconomicDecisionAddendum,
    build_economic_decision_addendum,
)
from yieldforge.realistic_falsification.economic_resolution import (
    build_economic_resolution_protocol,
)

_COMPRESSION = "gzip-level-6-mtime-0-flags-0"
_MAX_COMPRESSED_BYTES = 64 * 1024 * 1024
_MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_STRICT_CELL_BYTES = 256 * 1024 * 1024
_SIGNED_COST_PATTERN = r"^-?(?:0|[1-9][0-9]*)\.[0-9]{6}$"
_METRIC_PATTERN = r"^-?(?:0|[1-9][0-9]*)\.[0-9]{12}$"
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_METRIC_QUANTUM = Decimal("0.000000000001")
_MAX_IMPORTED_STRING_LENGTH = 4096


class Gate3CentralEvidenceError(ValueError):
    """Gate 3 central evidence failed a bounded fail-closed check."""


def _validate_imported_string_bounds(value: object) -> None:
    """Bound strings before inherited validators perform decimal or hash work."""

    pending = [value]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if len(current) > _MAX_IMPORTED_STRING_LENGTH:
                raise ValueError("Gate 3 central imported string exceeds its bound")
            continue
        if current is None or type(current) in (int, float, bool, bytes):
            continue
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(current, BaseModel):
            pending.extend(
                getattr(current, field_name) for field_name in type(current).model_fields
            )
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)


def _format_metric(value: Decimal) -> str:
    if not value.is_finite():
        raise Gate3CentralEvidenceError("Gate 3 central metric must be finite")
    try:
        return format(value.quantize(_METRIC_QUANTUM, rounding=ROUND_HALF_EVEN), ".12f")
    except DecimalException as error:
        raise Gate3CentralEvidenceError("Gate 3 central metric exceeds decimal bounds") from error


def _economic_metrics(
    *,
    baseline_cost: str,
    full_future_cost: str,
    known_only_cost: str,
) -> tuple[str, str, str]:
    try:
        baseline = Decimal(baseline_cost)
        full_future = Decimal(full_future_cost)
        known_only = Decimal(known_only_cost)
    except (DecimalException, TypeError, ValueError) as error:  # pragma: no cover
        raise Gate3CentralEvidenceError("Gate 3 central costs are malformed") from error
    if not all(item.is_finite() for item in (baseline, full_future, known_only)):
        raise Gate3CentralEvidenceError("Gate 3 central costs must be finite")
    if baseline <= 0:
        raise Gate3CentralEvidenceError("Gate 3 central baseline cost must be positive")
    try:
        with localcontext() as context:
            context.prec = 50
            full_future_savings = Decimal(100) * (baseline - full_future) / baseline
            unknown_contribution = Decimal(100) * (known_only - full_future) / baseline
            known_only_savings = Decimal(100) * (baseline - known_only) / baseline
    except DecimalException as error:
        raise Gate3CentralEvidenceError("Gate 3 central costs exceed decimal bounds") from error
    return (
        _format_metric(full_future_savings),
        _format_metric(unknown_contribution),
        _format_metric(known_only_savings),
    )


def _current_addendum() -> EconomicDecisionAddendum:
    return build_economic_decision_addendum(base_protocol=build_economic_resolution_protocol())


class Gate3CentralCellReceipt(FrozenExperimentModel):
    """Compact, content-addressed index for one full central B/F/K cell."""

    schema_version: Literal["yieldforge.m11-gate3-central-cell-receipt.v1"] = (
        "yieldforge.m11-gate3-central-cell-receipt.v1"
    )
    receipt_id: StrictStr = Field(pattern=r"^yfm11g3cellrcpt-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    decision_addendum_id: StrictStr = Field(pattern=r"^yfm11econdec-[0-9a-f]{24}$")
    decision_addendum_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    base_protocol_id: StrictStr = Field(pattern=r"^yfm11econp-[0-9a-f]{24}$")
    base_protocol_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    roots: Gate3RootBinding
    corpus_id: Gate3CorpusId
    stream_id: StrictStr = Field(min_length=1, max_length=128)
    regime: Literal["recurrent", "mixed", "high_mix", "regime_shift"]
    baseline_freeze_id: StrictStr = Field(pattern=r"^yfm11g3bf-[0-9a-f]{24}$")
    baseline_freeze_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    selected_policy_id: Gate3BaselinePolicyId
    cell_id: StrictStr = Field(pattern=r"^yfm11g3cell-[0-9a-f]{24}$")
    cell_content_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    baseline_costs: Gate3CostLedger
    full_future_costs: Gate3CostLedger
    known_only_costs: Gate3CostLedger
    baseline_cost: StrictStr = Field(max_length=64, pattern=_SIGNED_COST_PATTERN)
    full_future_cost: StrictStr = Field(max_length=64, pattern=_SIGNED_COST_PATTERN)
    known_only_cost: StrictStr = Field(max_length=64, pattern=_SIGNED_COST_PATTERN)
    full_future_savings_percent: StrictStr = Field(max_length=64, pattern=_METRIC_PATTERN)
    unknown_future_contribution_points: StrictStr = Field(
        max_length=64,
        pattern=_METRIC_PATTERN,
    )
    known_only_causal_savings_percent: StrictStr = Field(
        max_length=64,
        pattern=_METRIC_PATTERN,
    )
    full_future_savings_formula: Literal["100 * (B_i - F_i) / B_i"] = "100 * (B_i - F_i) / B_i"
    unknown_future_contribution_formula: Literal["100 * (K_i - F_i) / B_i"] = (
        "100 * (K_i - F_i) / B_i"
    )
    known_only_causal_savings_formula: Literal["100 * (B_i - K_i) / B_i"] = (
        "100 * (B_i - K_i) / B_i"
    )
    metric_rounding: Literal["twelve_decimal_half_even"] = "twelve_decimal_half_even"
    baseline_visibility: Gate3Visibility
    full_future_visibility: Literal["full_future"] = "full_future"
    known_only_visibility: Literal["known_only"] = "known_only"
    arm_event_counts: tuple[StrictInt, StrictInt, StrictInt]
    arm_material_shard_counts: tuple[StrictInt, StrictInt, StrictInt]
    all_arm_exact_event_censuses: Literal[True] = True
    candidate_action_parity_revalidated: Literal[True] = True
    common_compute_and_tie_revalidated: Literal[True] = True
    source_lineage: Literal["repaired_runtime"] = "repaired_runtime"
    sidecar_name: StrictStr = Field(
        pattern=(
            r"^m11-gate3-central-cell-"
            r"[0-9a-f]{64}-[0-9a-f]{64}\.json\.gz$"
        )
    )
    compressed_raw_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    compressed_byte_count: StrictInt = Field(gt=0, le=_MAX_COMPRESSED_BYTES)
    uncompressed_byte_count: StrictInt = Field(gt=0, le=_MAX_UNCOMPRESSED_BYTES)
    compression: Literal["gzip-level-6-mtime-0-flags-0"] = _COMPRESSION

    @field_validator(
        "baseline_costs",
        "full_future_costs",
        "known_only_costs",
        mode="before",
    )
    @classmethod
    def require_bounded_imported_ledger_strings(cls, value: object) -> object:
        for field_name in Gate3CostLedger.model_fields:
            candidate = (
                getattr(value, field_name, None)
                if isinstance(value, BaseModel)
                else value.get(field_name)
                if isinstance(value, dict)
                else None
            )
            if isinstance(candidate, str) and len(candidate) > 80:
                raise ValueError(f"Gate 3 central ledger {field_name} exceeds its string bound")
        return value

    @model_validator(mode="after")
    def require_all_bindings_metrics_and_identity(self) -> Self:
        addendum = _current_addendum()
        if (
            self.decision_addendum_id,
            self.decision_addendum_content_sha256,
            self.base_protocol_id,
            self.base_protocol_content_sha256,
        ) != (
            addendum.addendum_id,
            addendum.content_sha256,
            addendum.base_protocol_id,
            addendum.base_protocol_content_sha256,
        ):
            raise ValueError("Gate 3 central decision addendum binding differs")
        freeze_hash = self.baseline_freeze_content_sha256.removeprefix("sha256:")
        cell_hash = self.cell_content_sha256.removeprefix("sha256:")
        compressed_hash = self.compressed_raw_sha256.removeprefix("sha256:")
        if self.baseline_freeze_id != f"yfm11g3bf-{freeze_hash[:24]}":
            raise ValueError("Gate 3 central baseline freeze identity differs")
        if self.cell_id != f"yfm11g3cell-{cell_hash[:24]}":
            raise ValueError("Gate 3 central full cell identity differs")
        if self.selected_policy_id not in GATE3_BASELINE_POLICY_IDS:
            raise ValueError("Gate 3 central selected policy is not registered")
        if (
            self.baseline_cost,
            self.full_future_cost,
            self.known_only_cost,
        ) != (
            self.baseline_costs.net_cost,
            self.full_future_costs.net_cost,
            self.known_only_costs.net_cost,
        ):
            raise ValueError("Gate 3 central compact costs differ from complete ledgers")
        metrics = _economic_metrics(
            baseline_cost=self.baseline_cost,
            full_future_cost=self.full_future_cost,
            known_only_cost=self.known_only_cost,
        )
        if (
            self.full_future_savings_percent,
            self.unknown_future_contribution_points,
            self.known_only_causal_savings_percent,
        ) != metrics:
            raise ValueError("Gate 3 central compact metrics do not reconcile")
        expected_baseline_visibility = (
            "known_only"
            if self.selected_policy_id == "known_only_m9_two_ply_scrap"
            else "released_only"
        )
        if self.baseline_visibility != expected_baseline_visibility:
            raise ValueError("Gate 3 central baseline visibility differs")
        if self.arm_event_counts != (24, 24, 24):
            raise ValueError("Gate 3 central compact event census differs")
        if any(
            type(value) is not int or not 1 <= value <= 24
            for value in self.arm_material_shard_counts
        ):
            raise ValueError("Gate 3 central compact material census differs")
        if self.sidecar_name != (f"m11-gate3-central-cell-{cell_hash}-{compressed_hash}.json.gz"):
            raise ValueError("Gate 3 central sidecar binding differs")
        digest = semantic_sha256(self, excluded_fields={"receipt_id", "content_sha256"})
        if self.receipt_id != f"yfm11g3cellrcpt-{digest[:24]}" or (
            self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("Gate 3 central receipt identity differs from semantic content")
        return self


def deterministic_gzip(data: bytes) -> bytes:
    """Return reproducible gzip transport bytes with frozen header fields."""

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


class _CanonicalSizeCounter:
    """Count canonical bytes and fail before any cell-sized allocation."""

    __slots__ = ("maximum_bytes", "total")

    def __init__(self, maximum_bytes: int) -> None:
        self.maximum_bytes = maximum_bytes
        self.total = 0

    @property
    def remaining(self) -> int:
        return self.maximum_bytes - self.total

    def add(self, byte_count: int) -> None:
        if byte_count < 0 or byte_count > self.remaining:
            raise Gate3CentralEvidenceError(
                "Gate 3 central cell exceeds the strict cell byte bound"
            )
        self.total += byte_count


def _count_json_string(counter: _CanonicalSizeCounter, value: str) -> None:
    counter.add(2)
    if len(value) > counter.remaining:
        counter.add(len(value))
    encoded_size = 0
    for character in value:
        codepoint = ord(character)
        if character in ('"', "\\", "\b", "\f", "\n", "\r", "\t"):
            encoded_size += 2
        elif codepoint < 0x20 or codepoint <= 0xFFFF and codepoint > 0x7F:
            encoded_size += 6
        elif codepoint > 0xFFFF:
            encoded_size += 12
        else:
            encoded_size += 1
        if encoded_size > counter.remaining:
            counter.add(encoded_size)
    counter.add(encoded_size)


def _count_canonical_json_value(
    counter: _CanonicalSizeCounter,
    value: object,
    *,
    depth: int,
) -> None:
    if isinstance(value, BaseModel):
        fields = type(value).model_fields
        if not fields:
            counter.add(2)
            return
        counter.add(2)
        for index, field_name in enumerate(fields):
            counter.add(2 * (depth + 1))
            _count_json_string(counter, field_name)
            counter.add(2)
            _count_canonical_json_value(
                counter,
                getattr(value, field_name),
                depth=depth + 1,
            )
            counter.add(2 if index + 1 < len(fields) else 1)
        counter.add(2 * depth + 1)
        return
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise Gate3CentralEvidenceError(
                "Gate 3 central canonical JSON requires string object keys"
            )
        if not value:
            counter.add(2)
            return
        counter.add(2)
        for index, (key, item) in enumerate(value.items()):
            counter.add(2 * (depth + 1))
            _count_json_string(counter, key)
            counter.add(2)
            _count_canonical_json_value(counter, item, depth=depth + 1)
            counter.add(2 if index + 1 < len(value) else 1)
        counter.add(2 * depth + 1)
        return
    if isinstance(value, (list, tuple)):
        if not value:
            counter.add(2)
            return
        counter.add(2)
        for index, item in enumerate(value):
            counter.add(2 * (depth + 1))
            _count_canonical_json_value(counter, item, depth=depth + 1)
            counter.add(2 if index + 1 < len(value) else 1)
        counter.add(2 * depth + 1)
        return
    if isinstance(value, Enum):
        _count_canonical_json_value(counter, value.value, depth=depth)
        return
    if type(value) in (date, datetime, time, timedelta):
        counter.add(len(to_json(value)))
        return
    if type(value) is str:
        _count_json_string(counter, value)
        return
    if value is None:
        counter.add(4)
        return
    if type(value) is bool:
        counter.add(4 if value else 5)
        return
    if type(value) is int:
        digit_lower_bound = max(
            1,
            (value.bit_length() - 1) * 30103 // 100000 + 1,
        )
        if value < 0:
            digit_lower_bound += 1
        if digit_lower_bound > counter.remaining:
            counter.add(digit_lower_bound)
        try:
            counter.add(len(str(value)))
        except ValueError as error:
            raise Gate3CentralEvidenceError(
                "Gate 3 central integer exceeds the strict cell byte bound"
            ) from error
        return
    if type(value) is float:
        try:
            counter.add(len(json.dumps(value, allow_nan=False)))
        except (TypeError, ValueError) as error:
            raise Gate3CentralEvidenceError(
                "Gate 3 central cell contains a non-canonical JSON scalar"
            ) from error
        return
    raise Gate3CentralEvidenceError(
        f"Gate 3 central cell contains unsupported JSON type {type(value).__name__}"
    )


def _bounded_existing_canonical_size(
    value: Gate3StreamCell,
    *,
    maximum_bytes: int,
) -> int:
    """Return exact canonical size without constructing serialized cell data."""

    if type(maximum_bytes) is not int or maximum_bytes <= 0:
        raise Gate3CentralEvidenceError("Gate 3 central strict cell bound is malformed")
    counter = _CanonicalSizeCounter(maximum_bytes)
    try:
        _count_canonical_json_value(counter, value, depth=0)
        counter.add(1)
    except RecursionError as error:
        raise Gate3CentralEvidenceError(
            "Gate 3 central cell nesting exceeds the strict validation bound"
        ) from error
    return counter.total


def _iter_pretty_json_bytes(value: object, *, depth: int) -> Iterator[bytes]:
    """Traverse an existing Pydantic graph using the canonical JSON layout."""

    if isinstance(value, BaseModel):
        fields = type(value).model_fields
        items = tuple((name, getattr(value, name)) for name in sorted(fields))
        yield from _iter_pretty_json_object(items, depth=depth)
        return
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise Gate3CentralEvidenceError(
                "Gate 3 central canonical JSON requires string object keys"
            )
        yield from _iter_pretty_json_object(
            tuple((key, value[key]) for key in sorted(value)),
            depth=depth,
        )
        return
    if isinstance(value, (list, tuple)):
        if not value:
            yield b"[]"
            return
        yield b"[\n"
        for index, item in enumerate(value):
            yield b" " * (2 * (depth + 1))
            yield from _iter_pretty_json_bytes(item, depth=depth + 1)
            yield b",\n" if index + 1 < len(value) else b"\n"
        yield b" " * (2 * depth)
        yield b"]"
        return
    if isinstance(value, Enum):
        yield from _iter_pretty_json_bytes(value.value, depth=depth)
        return
    if type(value) in (date, datetime, time, timedelta):
        yield to_json(value)
        return
    if value is None or type(value) in (str, int, float, bool):
        try:
            yield json.dumps(value, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise Gate3CentralEvidenceError(
                "Gate 3 central cell contains a non-canonical JSON scalar"
            ) from error
        return
    raise Gate3CentralEvidenceError(
        f"Gate 3 central cell contains unsupported JSON type {type(value).__name__}"
    )


def _iter_pretty_json_object(
    items: tuple[tuple[str, object], ...],
    *,
    depth: int,
) -> Iterator[bytes]:
    if not items:
        yield b"{}"
        return
    yield b"{\n"
    for index, (key, value) in enumerate(items):
        yield b" " * (2 * (depth + 1))
        yield json.dumps(key).encode("utf-8")
        yield b": "
        yield from _iter_pretty_json_bytes(value, depth=depth + 1)
        yield b",\n" if index + 1 < len(items) else b"\n"
    yield b" " * (2 * depth)
    yield b"}"


def _iter_canonical_json_bytes(cell: Gate3StreamCell) -> Iterator[bytes]:
    """Yield canonical cell JSON without retaining a second full graph."""

    yield from _iter_pretty_json_bytes(cell, depth=0)
    yield b"\n"


class _BoundedCompressedSink(BytesIO):
    """Abort before the in-memory compressed transport can exceed its cap."""

    def __init__(self, maximum_bytes: int) -> None:
        super().__init__()
        self._maximum_bytes = maximum_bytes

    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > self._maximum_bytes:
            raise Gate3CentralEvidenceError(
                "Gate 3 central sidecar exceeds the compressed byte bound"
            )
        return super().write(data)


def _validate_gzip_header(data: bytes) -> None:
    if (
        type(data) is not bytes
        or len(data) < 10
        or data[:3] != b"\x1f\x8b\x08"
        or data[3] != 0
        or data[4:8] != b"\x00\x00\x00\x00"
    ):
        raise Gate3CentralEvidenceError(
            "Gate 3 central gzip header differs from the frozen contract"
        )


def _stream_canonical_gzip(cell: Gate3StreamCell) -> tuple[bytes, int]:
    """Compress canonical chunks with incremental raw and transport caps."""

    output = _BoundedCompressedSink(_MAX_COMPRESSED_BYTES)
    uncompressed_byte_count = 0
    with GzipFile(
        filename="",
        mode="wb",
        compresslevel=6,
        fileobj=output,
        mtime=0,
    ) as archive:
        for chunk in _iter_canonical_json_bytes(cell):
            uncompressed_byte_count += len(chunk)
            if uncompressed_byte_count > _MAX_UNCOMPRESSED_BYTES:
                raise Gate3CentralEvidenceError(
                    "Gate 3 central sidecar exceeds the uncompressed byte bound"
                )
            archive.write(chunk)
    compressed = output.getvalue()
    _validate_gzip_header(compressed)
    return compressed, uncompressed_byte_count


def _compress_bounded_canonical_bytes(canonical: bytearray) -> bytes:
    if len(canonical) > _MAX_UNCOMPRESSED_BYTES:
        raise Gate3CentralEvidenceError(
            "Gate 3 central sidecar exceeds the uncompressed byte bound"
        )
    output = _BoundedCompressedSink(_MAX_COMPRESSED_BYTES)
    with GzipFile(
        filename="",
        mode="wb",
        compresslevel=6,
        fileobj=output,
        mtime=0,
    ) as archive:
        view = memoryview(canonical)
        for offset in range(0, len(view), 1024 * 1024):
            archive.write(view[offset : offset + 1024 * 1024])
    compressed = output.getvalue()
    _validate_gzip_header(compressed)
    return compressed


def _strict_cell_and_canonical(
    cell: Gate3StreamCell,
) -> tuple[Gate3StreamCell, bytearray]:
    try:
        expected_size = _bounded_existing_canonical_size(
            cell,
            maximum_bytes=_MAX_STRICT_CELL_BYTES,
        )
        _validate_imported_string_bounds(cell)
        canonical = bytearray()
        for chunk in _iter_canonical_json_bytes(cell):
            if len(chunk) > _MAX_UNCOMPRESSED_BYTES - len(canonical):
                raise Gate3CentralEvidenceError(
                    "Gate 3 central sidecar exceeds the uncompressed byte bound"
                )
            if len(chunk) > expected_size - len(canonical):
                raise Gate3CentralEvidenceError(
                    "Gate 3 central cell changed during strict serialization"
                )
            canonical.extend(chunk)
        if len(canonical) != expected_size:
            raise Gate3CentralEvidenceError(
                "Gate 3 central cell changed during strict serialization"
            )
        strict = Gate3StreamCell.model_validate_json(canonical, strict=True)
        return strict, canonical
    except Gate3CentralEvidenceError:
        raise
    except (
        AttributeError,
        DecimalException,
        RecursionError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise Gate3CentralEvidenceError(
            "Gate 3 central full cell failed strict validation"
        ) from error


def _strict_cell(cell: Gate3StreamCell) -> Gate3StreamCell:
    strict, _ = _strict_cell_and_canonical(cell)
    return strict


def _strict_addendum(addendum: EconomicDecisionAddendum) -> EconomicDecisionAddendum:
    try:
        _validate_imported_string_bounds(addendum)
        strict = EconomicDecisionAddendum.model_validate(
            addendum.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (
        AttributeError,
        DecimalException,
        RecursionError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise Gate3CentralEvidenceError(
            "Gate 3 central decision addendum failed strict validation"
        ) from error
    if strict != _current_addendum():
        raise Gate3CentralEvidenceError("Gate 3 central decision addendum differs")
    return strict


def _build_receipt(
    cell: Gate3StreamCell,
    addendum: EconomicDecisionAddendum,
    *,
    uncompressed_byte_count: int,
    compressed_bytes: bytes,
) -> Gate3CentralCellReceipt:
    cell_hash = cell.content_sha256.removeprefix("sha256:")
    compressed_hash = hashlib.sha256(compressed_bytes).hexdigest()
    metrics = _economic_metrics(
        baseline_cost=cell.baseline_cost,
        full_future_cost=cell.full_future_cost,
        known_only_cost=cell.known_only_cost,
    )
    if metrics[:2] != (
        cell.full_future_savings_percent,
        cell.unknown_future_contribution_points,
    ):
        raise Gate3CentralEvidenceError(
            "Gate 3 central full cell metrics differ from HALF_EVEN recomputation"
        )
    semantic = {
        "schema_version": "yieldforge.m11-gate3-central-cell-receipt.v1",
        "decision_addendum_id": addendum.addendum_id,
        "decision_addendum_content_sha256": addendum.content_sha256,
        "base_protocol_id": addendum.base_protocol_id,
        "base_protocol_content_sha256": addendum.base_protocol_content_sha256,
        "roots": cell.roots.model_dump(mode="json"),
        "corpus_id": cell.corpus_id,
        "stream_id": cell.stream_id,
        "regime": cell.regime,
        "baseline_freeze_id": cell.baseline_freeze_id,
        "baseline_freeze_content_sha256": cell.baseline_freeze_content_sha256,
        "selected_policy_id": cell.baseline_freeze.selected_policy_id,
        "cell_id": cell.cell_id,
        "cell_content_sha256": cell.content_sha256,
        "baseline_costs": cell.baseline.final_costs.model_dump(mode="json"),
        "full_future_costs": cell.full_future.final_costs.model_dump(mode="json"),
        "known_only_costs": cell.known_only.final_costs.model_dump(mode="json"),
        "baseline_cost": cell.baseline_cost,
        "full_future_cost": cell.full_future_cost,
        "known_only_cost": cell.known_only_cost,
        "full_future_savings_percent": metrics[0],
        "unknown_future_contribution_points": metrics[1],
        "known_only_causal_savings_percent": metrics[2],
        "full_future_savings_formula": "100 * (B_i - F_i) / B_i",
        "unknown_future_contribution_formula": "100 * (K_i - F_i) / B_i",
        "known_only_causal_savings_formula": "100 * (B_i - K_i) / B_i",
        "metric_rounding": "twelve_decimal_half_even",
        "baseline_visibility": cell.baseline.visibility,
        "full_future_visibility": cell.full_future.visibility,
        "known_only_visibility": cell.known_only.visibility,
        "arm_event_counts": tuple(
            len(item.decisions) for item in (cell.baseline, cell.full_future, cell.known_only)
        ),
        "arm_material_shard_counts": tuple(
            item.material_shard_count for item in (cell.baseline, cell.full_future, cell.known_only)
        ),
        "all_arm_exact_event_censuses": all(
            item.exact_event_census for item in (cell.baseline, cell.full_future, cell.known_only)
        ),
        "candidate_action_parity_revalidated": cell.candidate_action_parity_revalidated,
        "common_compute_and_tie_revalidated": cell.common_compute_and_tie_revalidated,
        "source_lineage": "repaired_runtime",
        "sidecar_name": f"m11-gate3-central-cell-{cell_hash}-{compressed_hash}.json.gz",
        "compressed_raw_sha256": f"sha256:{compressed_hash}",
        "compressed_byte_count": len(compressed_bytes),
        "uncompressed_byte_count": uncompressed_byte_count,
        "compression": _COMPRESSION,
    }
    digest = semantic_sha256(semantic)
    try:
        return Gate3CentralCellReceipt(
            receipt_id=f"yfm11g3cellrcpt-{digest[:24]}",
            content_sha256=f"sha256:{digest}",
            **semantic,
        )
    except (DecimalException, TypeError, ValidationError, ValueError) as error:
        raise Gate3CentralEvidenceError(
            "Gate 3 compact central receipt failed validation"
        ) from error


@dataclass(frozen=True, slots=True)
class _PreparedCentralCellEvidence:
    compressed_bytes: bytes
    receipt: Gate3CentralCellReceipt


def _prepare_central_cell_evidence(
    cell: Gate3StreamCell,
    *,
    decision_addendum: EconomicDecisionAddendum,
) -> _PreparedCentralCellEvidence:
    """Validate, stream, compress, and compact one full central cell once."""

    strict_cell, canonical = _strict_cell_and_canonical(cell)
    strict_addendum = _strict_addendum(decision_addendum)
    compressed = _compress_bounded_canonical_bytes(canonical)
    uncompressed_byte_count = len(canonical)
    receipt = _build_receipt(
        strict_cell,
        strict_addendum,
        uncompressed_byte_count=uncompressed_byte_count,
        compressed_bytes=compressed,
    )
    return _PreparedCentralCellEvidence(
        compressed_bytes=compressed,
        receipt=receipt,
    )


def build_gate3_central_cell_receipt(
    cell: Gate3StreamCell,
    *,
    decision_addendum: EconomicDecisionAddendum,
) -> Gate3CentralCellReceipt:
    """Build a compact receipt from one complete authenticated central cell."""

    return _prepare_central_cell_evidence(
        cell,
        decision_addendum=decision_addendum,
    ).receipt


def canonical_gate3_central_cell_bytes(cell: Gate3StreamCell) -> bytes:
    """Strictly detach and canonically serialize one full central cell."""

    _, canonical = _strict_cell_and_canonical(cell)
    return bytes(canonical)


def _strict_receipt(receipt: Gate3CentralCellReceipt) -> Gate3CentralCellReceipt:
    try:
        _validate_imported_string_bounds(receipt)
        return Gate3CentralCellReceipt.model_validate(
            receipt.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (
        AttributeError,
        DecimalException,
        RecursionError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise Gate3CentralEvidenceError(
            "Gate 3 central receipt failed strict validation"
        ) from error


def _strict_roots(roots: Gate3RootBinding) -> Gate3RootBinding:
    try:
        _validate_imported_string_bounds(roots)
        return Gate3RootBinding.model_validate(
            roots.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (
        AttributeError,
        DecimalException,
        RecursionError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise Gate3CentralEvidenceError(
            "Gate 3 central expected roots failed strict validation"
        ) from error


def _strict_freeze(
    freeze: Gate3BaselineCalibrationFreeze,
    *,
    expected_roots: Gate3RootBinding,
    expected_corpus_id: Gate3CorpusId,
) -> Gate3BaselineCalibrationFreeze:
    try:
        _validate_imported_string_bounds(freeze)
        strict = Gate3BaselineCalibrationFreeze.model_validate(
            freeze.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (
        AttributeError,
        DecimalException,
        RecursionError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise Gate3CentralEvidenceError(
            "Gate 3 central expected baseline freeze failed strict validation"
        ) from error
    if strict.roots != expected_roots or strict.corpus_id != expected_corpus_id:
        raise Gate3CentralEvidenceError("Gate 3 central expected baseline freeze binding differs")
    return strict


def _strict_ledger(ledger: Gate3CostLedger, *, label: str) -> Gate3CostLedger:
    try:
        _validate_imported_string_bounds(ledger)
        return Gate3CostLedger.model_validate(
            ledger.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (
        AttributeError,
        DecimalException,
        RecursionError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise Gate3CentralEvidenceError(
            f"Gate 3 central expected {label} ledger failed strict validation"
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
            raise Gate3CentralEvidenceError("Gate 3 central sidecar must be a bounded regular file")
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
                raise Gate3CentralEvidenceError(
                    "Gate 3 central sidecar must be a bounded regular file"
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
    except Gate3CentralEvidenceError:
        raise
    except OSError as error:
        raise Gate3CentralEvidenceError(
            "Gate 3 central sidecar could not be read safely"
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
        raise Gate3CentralEvidenceError("Gate 3 central sidecar changed during read-back")
    return raw


def _bounded_gzip_decompress(data: bytes) -> bytearray:
    strict_bound_active = _MAX_STRICT_CELL_BYTES <= _MAX_UNCOMPRESSED_BYTES
    maximum_bytes = min(_MAX_STRICT_CELL_BYTES, _MAX_UNCOMPRESSED_BYTES)

    def raise_bound_error() -> None:
        if strict_bound_active:
            raise Gate3CentralEvidenceError(
                "Gate 3 central cell exceeds the strict cell byte bound"
            )
        raise Gate3CentralEvidenceError(
            "Gate 3 central sidecar exceeds the uncompressed byte bound"
        )

    try:
        with GzipFile(fileobj=BytesIO(data), mode="rb") as archive:
            raw = bytearray()
            while True:
                remaining = maximum_bytes + 1 - len(raw)
                if remaining <= 0:
                    raise_bound_error()
                chunk = archive.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > maximum_bytes:
                    raise_bound_error()
    except Gate3CentralEvidenceError:
        raise
    except (EOFError, OSError, zlib.error) as error:
        raise Gate3CentralEvidenceError("Gate 3 central sidecar is not valid gzip") from error
    return raw


def _json_object_without_duplicates(pairs):  # type: ignore[no-untyped-def]
    output = {}
    for key, value in pairs:
        if key in output:
            raise Gate3CentralEvidenceError("Gate 3 central JSON contains a duplicate object key")
        output[key] = value
    return output


def _reject_nonfinite_json(value: str):  # type: ignore[no-untyped-def]
    raise Gate3CentralEvidenceError(f"Gate 3 central JSON contains non-finite value {value}")


def _parse_strict_cell_json(raw: bytes | bytearray) -> Gate3StreamCell:
    if len(raw) > _MAX_STRICT_CELL_BYTES:
        raise Gate3CentralEvidenceError("Gate 3 central cell exceeds the strict cell byte bound")
    try:
        json.loads(
            raw,
            object_pairs_hook=_json_object_without_duplicates,
            parse_constant=_reject_nonfinite_json,
        )
        return Gate3StreamCell.model_validate_json(raw, strict=True)
    except Gate3CentralEvidenceError:
        raise
    except (
        UnicodeDecodeError,
        DecimalException,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise Gate3CentralEvidenceError(
            "Gate 3 central sidecar failed strict semantic validation"
        ) from error


def _has_canonical_encoding(cell: Gate3StreamCell, raw: bytes | bytearray) -> bool:
    view = memoryview(raw)
    offset = 0
    for chunk in _iter_canonical_json_bytes(cell):
        stop = offset + len(chunk)
        if stop > len(view) or view[offset:stop] != chunk:
            return False
        offset = stop
    return offset == len(view)


def _validate_cell_expected_binding(
    cell: Gate3StreamCell,
    *,
    roots: Gate3RootBinding,
    corpus_id: Gate3CorpusId,
    stream_id: str,
    regime: Literal["recurrent", "mixed", "high_mix", "regime_shift"],
    baseline_freeze: Gate3BaselineCalibrationFreeze,
) -> None:
    if (
        cell.roots != roots
        or cell.corpus_id != corpus_id
        or cell.stream_id != stream_id
        or cell.regime != regime
        or cell.baseline_freeze != baseline_freeze
    ):
        raise Gate3CentralEvidenceError("Gate 3 central full cell differs from expected bindings")


def _validate_sidecar_bytes(
    data: bytes,
    *,
    receipt: Gate3CentralCellReceipt,
    decision_addendum: EconomicDecisionAddendum,
    roots: Gate3RootBinding,
    corpus_id: Gate3CorpusId,
    stream_id: str,
    regime: Literal["recurrent", "mixed", "high_mix", "regime_shift"],
    baseline_freeze: Gate3BaselineCalibrationFreeze,
) -> Gate3StreamCell:
    if type(data) is not bytes:
        raise Gate3CentralEvidenceError("Gate 3 central sidecar requires exact bytes")
    _validate_gzip_header(data)
    if len(data) > _MAX_COMPRESSED_BYTES:
        raise Gate3CentralEvidenceError("Gate 3 central sidecar exceeds the compressed byte bound")
    if len(data) != receipt.compressed_byte_count:
        raise Gate3CentralEvidenceError("Gate 3 central compressed byte count differs")
    compressed_sha = f"sha256:{hashlib.sha256(data).hexdigest()}"
    if compressed_sha != receipt.compressed_raw_sha256:
        raise Gate3CentralEvidenceError("Gate 3 central compressed raw hash differs")
    raw = _bounded_gzip_decompress(data)
    if len(raw) != receipt.uncompressed_byte_count:
        raise Gate3CentralEvidenceError("Gate 3 central uncompressed byte count differs")
    cell = _parse_strict_cell_json(raw)
    if not _has_canonical_encoding(cell, raw):
        raise Gate3CentralEvidenceError("Gate 3 central full cell encoding is not canonical")
    _validate_cell_expected_binding(
        cell,
        roots=roots,
        corpus_id=corpus_id,
        stream_id=stream_id,
        regime=regime,
        baseline_freeze=baseline_freeze,
    )
    rederived = _build_receipt(
        cell,
        decision_addendum,
        uncompressed_byte_count=len(raw),
        compressed_bytes=data,
    )
    if rederived != receipt:
        raise Gate3CentralEvidenceError("Gate 3 central sidecar differs from its compact receipt")
    return cell


def _strict_expected_values(
    *,
    decision_addendum: EconomicDecisionAddendum,
    expected_roots: Gate3RootBinding,
    expected_corpus_id: Gate3CorpusId,
    expected_stream_id: str,
    expected_regime: Literal["recurrent", "mixed", "high_mix", "regime_shift"],
    expected_baseline_freeze: Gate3BaselineCalibrationFreeze,
    expected_cell_id: str,
    expected_cell_content_sha256: str,
    expected_baseline_costs: Gate3CostLedger,
    expected_full_future_costs: Gate3CostLedger,
    expected_known_only_costs: Gate3CostLedger,
    expected_baseline_cost: str,
    expected_full_future_cost: str,
    expected_known_only_cost: str,
    expected_full_future_savings_percent: str,
    expected_unknown_future_contribution_points: str,
    expected_known_only_causal_savings_percent: str,
    expected_baseline_visibility: Gate3Visibility,
    expected_full_future_visibility: Gate3Visibility,
    expected_known_only_visibility: Gate3Visibility,
    expected_arm_event_counts: tuple[int, int, int],
    expected_arm_material_shard_counts: tuple[int, int, int],
    expected_all_arm_exact_event_censuses: bool,
    expected_candidate_action_parity_revalidated: bool,
    expected_common_compute_and_tie_revalidated: bool,
    expected_source_lineage: Literal["repaired_runtime"],
) -> dict[str, object]:
    addendum = _strict_addendum(decision_addendum)
    roots = _strict_roots(expected_roots)
    if expected_corpus_id not in ("lectra-m3-m4", "loco-2dics"):
        raise Gate3CentralEvidenceError("Gate 3 central expected corpus is malformed")
    corpus_id = expected_corpus_id
    freeze = _strict_freeze(
        expected_baseline_freeze,
        expected_roots=roots,
        expected_corpus_id=corpus_id,
    )
    ledgers = (
        _strict_ledger(expected_baseline_costs, label="baseline"),
        _strict_ledger(expected_full_future_costs, label="full-future"),
        _strict_ledger(expected_known_only_costs, label="known-only"),
    )
    costs = (
        expected_baseline_cost,
        expected_full_future_cost,
        expected_known_only_cost,
    )
    metrics = (
        expected_full_future_savings_percent,
        expected_unknown_future_contribution_points,
        expected_known_only_causal_savings_percent,
    )
    if (
        type(expected_stream_id) is not str
        or not 1 <= len(expected_stream_id) <= 128
        or type(expected_regime) is not str
        or expected_regime not in ("recurrent", "mixed", "high_mix", "regime_shift")
        or type(expected_cell_id) is not str
        or re.fullmatch(r"yfm11g3cell-[0-9a-f]{24}", expected_cell_id) is None
        or type(expected_cell_content_sha256) is not str
        or re.fullmatch(_SHA256_PATTERN, expected_cell_content_sha256) is None
        or expected_cell_id
        != ("yfm11g3cell-" + expected_cell_content_sha256.removeprefix("sha256:")[:24])
        or any(
            type(value) is not str or re.fullmatch(_SIGNED_COST_PATTERN, value) is None
            for value in costs
        )
        or costs != tuple(item.net_cost for item in ledgers)
        or any(
            type(value) is not str or re.fullmatch(_METRIC_PATTERN, value) is None
            for value in metrics
        )
        or metrics
        != _economic_metrics(
            baseline_cost=expected_baseline_cost,
            full_future_cost=expected_full_future_cost,
            known_only_cost=expected_known_only_cost,
        )
        or expected_baseline_visibility
        != (
            "known_only"
            if freeze.selected_policy_id == "known_only_m9_two_ply_scrap"
            else "released_only"
        )
        or expected_full_future_visibility != "full_future"
        or expected_known_only_visibility != "known_only"
        or type(expected_arm_event_counts) is not tuple
        or expected_arm_event_counts != (24, 24, 24)
        or type(expected_arm_material_shard_counts) is not tuple
        or any(
            type(value) is not int or not 1 <= value <= 24
            for value in expected_arm_material_shard_counts
        )
        or expected_all_arm_exact_event_censuses is not True
        or expected_candidate_action_parity_revalidated is not True
        or expected_common_compute_and_tie_revalidated is not True
        or expected_source_lineage != "repaired_runtime"
    ):
        raise Gate3CentralEvidenceError("Gate 3 central expected compact values are malformed")
    return {
        "addendum": addendum,
        "roots": roots,
        "corpus_id": corpus_id,
        "stream_id": expected_stream_id,
        "regime": expected_regime,
        "freeze": freeze,
        "cell_id": expected_cell_id,
        "cell_sha": expected_cell_content_sha256,
        "ledgers": ledgers,
        "costs": costs,
        "metrics": metrics,
        "visibilities": (
            expected_baseline_visibility,
            expected_full_future_visibility,
            expected_known_only_visibility,
        ),
        "event_counts": expected_arm_event_counts,
        "material_counts": expected_arm_material_shard_counts,
    }


def publish_gate3_central_cell_evidence(
    output_directory: Path,
    cell: Gate3StreamCell,
    *,
    decision_addendum: EconomicDecisionAddendum,
) -> tuple[Path, Gate3CentralCellReceipt]:
    """Prepare once and immutably publish one complete central-cell sidecar."""

    prepared = _prepare_central_cell_evidence(
        cell,
        decision_addendum=decision_addendum,
    )
    path = Path(output_directory) / prepared.receipt.sidecar_name
    try:
        published = publish_immutable_artifact(
            path,
            prepared.compressed_bytes,
            validate=(lambda data: data if data == prepared.compressed_bytes else b""),
            label="M11 Gate 3 central-cell evidence",
        )
    except M8ArtifactPublicationError as error:
        raise Gate3CentralEvidenceError("Gate 3 central immutable publication failed") from error
    return published, prepared.receipt


def recover_gate3_central_cell_receipt(
    path: Path,
    *,
    decision_addendum: EconomicDecisionAddendum,
    expected_roots: Gate3RootBinding,
    expected_corpus_id: Gate3CorpusId,
    expected_stream_id: str,
    expected_regime: Literal["recurrent", "mixed", "high_mix", "regime_shift"],
    expected_baseline_freeze: Gate3BaselineCalibrationFreeze,
) -> Gate3CentralCellReceipt:
    """Authenticate an orphan sidecar and recover its actual transport receipt."""

    addendum = _strict_addendum(decision_addendum)
    roots = _strict_roots(expected_roots)
    if (
        expected_corpus_id not in ("lectra-m3-m4", "loco-2dics")
        or type(expected_stream_id) is not str
        or not 1 <= len(expected_stream_id) <= 128
        or expected_regime not in ("recurrent", "mixed", "high_mix", "regime_shift")
    ):
        raise Gate3CentralEvidenceError("Gate 3 central recovery bindings are malformed")
    freeze = _strict_freeze(
        expected_baseline_freeze,
        expected_roots=roots,
        expected_corpus_id=expected_corpus_id,
    )
    candidate = Path(path)
    data = _read_bounded_regular_file(candidate)
    _validate_gzip_header(data)
    raw = _bounded_gzip_decompress(data)
    cell = _parse_strict_cell_json(raw)
    if not _has_canonical_encoding(cell, raw):
        raise Gate3CentralEvidenceError("Gate 3 central full cell encoding is not canonical")
    _validate_cell_expected_binding(
        cell,
        roots=roots,
        corpus_id=expected_corpus_id,
        stream_id=expected_stream_id,
        regime=expected_regime,
        baseline_freeze=freeze,
    )
    recovered = _build_receipt(
        cell,
        addendum,
        uncompressed_byte_count=len(raw),
        compressed_bytes=data,
    )
    if candidate.name != recovered.sidecar_name:
        raise Gate3CentralEvidenceError("Gate 3 central sidecar filename differs")
    return recovered


def load_gate3_central_cell_evidence(
    path: Path,
    *,
    receipt: Gate3CentralCellReceipt,
    decision_addendum: EconomicDecisionAddendum,
    expected_roots: Gate3RootBinding,
    expected_corpus_id: Gate3CorpusId,
    expected_stream_id: str,
    expected_regime: Literal["recurrent", "mixed", "high_mix", "regime_shift"],
    expected_baseline_freeze: Gate3BaselineCalibrationFreeze,
    expected_cell_id: str,
    expected_cell_content_sha256: str,
    expected_baseline_costs: Gate3CostLedger,
    expected_full_future_costs: Gate3CostLedger,
    expected_known_only_costs: Gate3CostLedger,
    expected_baseline_cost: str,
    expected_full_future_cost: str,
    expected_known_only_cost: str,
    expected_full_future_savings_percent: str,
    expected_unknown_future_contribution_points: str,
    expected_known_only_causal_savings_percent: str,
    expected_baseline_visibility: Gate3Visibility,
    expected_full_future_visibility: Gate3Visibility,
    expected_known_only_visibility: Gate3Visibility,
    expected_arm_event_counts: tuple[int, int, int],
    expected_arm_material_shard_counts: tuple[int, int, int],
    expected_all_arm_exact_event_censuses: bool,
    expected_candidate_action_parity_revalidated: bool,
    expected_common_compute_and_tie_revalidated: bool,
    expected_source_lineage: Literal["repaired_runtime"],
) -> Gate3StreamCell:
    """Load a full cell only under exact receipt and caller-bound semantics."""

    strict_receipt = _strict_receipt(receipt)
    expected = _strict_expected_values(
        decision_addendum=decision_addendum,
        expected_roots=expected_roots,
        expected_corpus_id=expected_corpus_id,
        expected_stream_id=expected_stream_id,
        expected_regime=expected_regime,
        expected_baseline_freeze=expected_baseline_freeze,
        expected_cell_id=expected_cell_id,
        expected_cell_content_sha256=expected_cell_content_sha256,
        expected_baseline_costs=expected_baseline_costs,
        expected_full_future_costs=expected_full_future_costs,
        expected_known_only_costs=expected_known_only_costs,
        expected_baseline_cost=expected_baseline_cost,
        expected_full_future_cost=expected_full_future_cost,
        expected_known_only_cost=expected_known_only_cost,
        expected_full_future_savings_percent=expected_full_future_savings_percent,
        expected_unknown_future_contribution_points=(expected_unknown_future_contribution_points),
        expected_known_only_causal_savings_percent=(expected_known_only_causal_savings_percent),
        expected_baseline_visibility=expected_baseline_visibility,
        expected_full_future_visibility=expected_full_future_visibility,
        expected_known_only_visibility=expected_known_only_visibility,
        expected_arm_event_counts=expected_arm_event_counts,
        expected_arm_material_shard_counts=expected_arm_material_shard_counts,
        expected_all_arm_exact_event_censuses=(expected_all_arm_exact_event_censuses),
        expected_candidate_action_parity_revalidated=(expected_candidate_action_parity_revalidated),
        expected_common_compute_and_tie_revalidated=(expected_common_compute_and_tie_revalidated),
        expected_source_lineage=expected_source_lineage,
    )
    addendum = expected["addendum"]
    roots = expected["roots"]
    freeze = expected["freeze"]
    ledgers = expected["ledgers"]
    costs = expected["costs"]
    metrics = expected["metrics"]
    visibilities = expected["visibilities"]
    if (
        strict_receipt.decision_addendum_id != addendum.addendum_id
        or strict_receipt.decision_addendum_content_sha256 != addendum.content_sha256
        or strict_receipt.base_protocol_id != addendum.base_protocol_id
        or strict_receipt.base_protocol_content_sha256 != addendum.base_protocol_content_sha256
        or strict_receipt.roots != roots
        or strict_receipt.corpus_id != expected["corpus_id"]
        or strict_receipt.stream_id != expected["stream_id"]
        or strict_receipt.regime != expected["regime"]
        or strict_receipt.baseline_freeze_id != freeze.freeze_id
        or strict_receipt.baseline_freeze_content_sha256 != freeze.content_sha256
        or strict_receipt.selected_policy_id != freeze.selected_policy_id
        or strict_receipt.cell_id != expected["cell_id"]
        or strict_receipt.cell_content_sha256 != expected["cell_sha"]
        or (
            strict_receipt.baseline_costs,
            strict_receipt.full_future_costs,
            strict_receipt.known_only_costs,
        )
        != ledgers
        or (
            strict_receipt.baseline_cost,
            strict_receipt.full_future_cost,
            strict_receipt.known_only_cost,
        )
        != costs
        or (
            strict_receipt.full_future_savings_percent,
            strict_receipt.unknown_future_contribution_points,
            strict_receipt.known_only_causal_savings_percent,
        )
        != metrics
        or (
            strict_receipt.baseline_visibility,
            strict_receipt.full_future_visibility,
            strict_receipt.known_only_visibility,
        )
        != visibilities
        or strict_receipt.arm_event_counts != expected["event_counts"]
        or strict_receipt.arm_material_shard_counts != expected["material_counts"]
        or strict_receipt.all_arm_exact_event_censuses is not True
        or strict_receipt.candidate_action_parity_revalidated is not True
        or strict_receipt.common_compute_and_tie_revalidated is not True
        or strict_receipt.source_lineage != "repaired_runtime"
    ):
        raise Gate3CentralEvidenceError(
            "Gate 3 central receipt differs from expected compact values"
        )
    candidate = Path(path)
    if candidate.name != strict_receipt.sidecar_name:
        raise Gate3CentralEvidenceError("Gate 3 central sidecar filename differs")
    data = _read_bounded_regular_file(candidate)
    return _validate_sidecar_bytes(
        data,
        receipt=strict_receipt,
        decision_addendum=addendum,  # type: ignore[arg-type]
        roots=roots,  # type: ignore[arg-type]
        corpus_id=expected["corpus_id"],  # type: ignore[arg-type]
        stream_id=expected["stream_id"],  # type: ignore[arg-type]
        regime=expected["regime"],  # type: ignore[arg-type]
        baseline_freeze=freeze,  # type: ignore[arg-type]
    )
