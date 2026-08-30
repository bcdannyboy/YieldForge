"""Authenticated resumable state for the M11 central economic screen."""

from __future__ import annotations

import json
import os
import re
import stat
from decimal import ROUND_HALF_EVEN, Decimal, DecimalException, localcontext
from pathlib import Path
from typing import Literal, Self, cast

import numpy as np
from pydantic import Field, StrictBool, StrictInt, StrictStr, ValidationError, model_validator

from yieldforge.experiments.contracts import (
    FrozenExperimentModel,
    canonical_pretty_json_bytes,
    semantic_sha256,
)
from yieldforge.oracle.artifact_publisher import (
    M8ArtifactPublicationError,
    publish_immutable_artifact,
)
from yieldforge.realistic_falsification.central_evidence_store import (
    Gate3CentralCellReceipt,
)
from yieldforge.realistic_falsification.confirmation import (
    Gate3BaselineCalibrationFreeze,
    Gate3CorpusId,
    Gate3RootBinding,
)
from yieldforge.realistic_falsification.economic_decision import (
    EconomicCrossSegmentDecision,
    EconomicDecisionAddendum,
    EconomicSegmentDecision,
    build_economic_decision_addendum,
    build_economic_segment_decision,
    reduce_economic_segment_decisions,
)
from yieldforge.realistic_falsification.economic_resolution import (
    Gate3CalibrationManifest,
    build_economic_resolution_protocol,
)
from yieldforge.realistic_falsification.economic_validity import (
    Gate3ValidityStageManifest,
)

_CORPUS_ORDER: tuple[Gate3CorpusId, Gate3CorpusId] = (
    "loco-2dics",
    "lectra-m3-m4",
)
_METRIC_PATTERN = r"^-?(?:0|[1-9][0-9]{0,6})\.[0-9]{12}$"
_METRIC_QUANTUM = Decimal("0.000000000001")
_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
_MAX_FAILURE_ARTIFACT_BYTES = 16 * 1024
_MAX_DISCOVERY_ENTRIES = 4096
_FAILURE_PREFIX = "m11-economic-central-cell-failure-"
_CHECKPOINT_PREFIX = "m11-economic-central-cell-checkpoint-"
_SUMMARY_PREFIX = "m11-economic-central-segment-"
_MANIFEST_PREFIX = "m11-economic-central-manifest-"

CentralSegmentNextAction = Literal[
    "CONTINUE_ADVERSE_LOCO",
    "CONTINUE_FORECAST_LOCO",
    "CONTINUE_LECTRA_SCREEN",
    "CONTINUE_ADVERSE_LECTRA",
    "CONTINUE_FORECAST_LECTRA",
]


class Gate3EconomicCentralEvidenceError(ValueError):
    """Central-stage evidence failed an authenticated fail-closed check."""


def _json_value(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[union-attr]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _metric(value: Decimal | float) -> str:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
        if not parsed.is_finite():
            raise Gate3EconomicCentralEvidenceError("central metric must be finite")
        quantized = parsed.quantize(_METRIC_QUANTUM, rounding=ROUND_HALF_EVEN)
    except DecimalException as error:
        raise Gate3EconomicCentralEvidenceError("central metric exceeds decimal bounds") from error
    if quantized == 0:
        quantized = Decimal(0)
    return format(quantized, ".12f")


def _median(values: tuple[Decimal, ...]) -> Decimal:
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _bootstrap_indices(corpus_id: Gate3CorpusId) -> np.ndarray:
    """Preserve the frozen shared RNG stream even for LOCo-first execution."""

    generator = np.random.Generator(np.random.PCG64(0))
    lectra = generator.integers(0, 20, size=(10_000, 20), dtype=np.int64)
    loco = generator.integers(0, 20, size=(10_000, 20), dtype=np.int64)
    return loco if corpus_id == "loco-2dics" else lectra


def _strict_upstream(
    *,
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> tuple[
    Gate3CalibrationManifest,
    Gate3ValidityStageManifest,
    EconomicDecisionAddendum,
]:
    try:
        calibration = Gate3CalibrationManifest.model_validate(
            calibration_manifest.model_dump(mode="python", round_trip=True), strict=True
        )
        validity = Gate3ValidityStageManifest.model_validate(
            validity_manifest.model_dump(mode="python", round_trip=True), strict=True
        )
        addendum = EconomicDecisionAddendum.model_validate(
            decision_addendum.model_dump(mode="python", round_trip=True), strict=True
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicCentralEvidenceError(
            "central upstream evidence failed strict validation"
        ) from error
    protocol = build_economic_resolution_protocol()
    current_addendum = build_economic_decision_addendum(base_protocol=protocol)
    if (
        calibration.protocol != protocol
        or calibration.status != "complete_valid"
        or calibration.success_count != 96
        or calibration.failure_count != 0
        or len(calibration.checkpoints) != 96
        or len(calibration.baseline_freezes) != 2
        or tuple(item.corpus_id for item in calibration.baseline_freezes)
        != ("lectra-m3-m4", "loco-2dics")
        or not calibration.complete
    ):
        raise Gate3EconomicCentralEvidenceError(
            "central stage requires exact complete-valid calibration"
        )
    if (
        validity.protocol != protocol
        or validity.roots != calibration.roots
        or validity.calibration_manifest_id != calibration.manifest_id
        or validity.calibration_manifest_content_sha256 != calibration.content_sha256
        or validity.baseline_freezes != calibration.baseline_freezes
        or validity.status != "valid"
        or validity.central_authorized is not True
        or not validity.complete
    ):
        raise Gate3EconomicCentralEvidenceError(
            "central stage requires an exact valid authorizing validity manifest"
        )
    if addendum != current_addendum:
        raise Gate3EconomicCentralEvidenceError(
            "central stage decision addendum differs from the frozen addendum"
        )
    return calibration, validity, addendum


def _freeze_for(
    calibration: Gate3CalibrationManifest,
    corpus_id: Gate3CorpusId,
) -> Gate3BaselineCalibrationFreeze:
    freezes = tuple(item for item in calibration.baseline_freezes if item.corpus_id == corpus_id)
    if len(freezes) != 1:
        raise Gate3EconomicCentralEvidenceError("central corpus has no unique baseline freeze")
    return freezes[0]


def _strict_receipts(
    receipts: tuple[Gate3CentralCellReceipt, ...],
    *,
    canonical_stream_ids: tuple[str, ...],
    roots: Gate3RootBinding,
    freeze: Gate3BaselineCalibrationFreeze,
    addendum: EconomicDecisionAddendum,
) -> tuple[Gate3CentralCellReceipt, ...]:
    if (
        type(receipts) is not tuple
        or len(receipts) != 20
        or type(canonical_stream_ids) is not tuple
        or len(canonical_stream_ids) != 20
        or len(set(canonical_stream_ids)) != 20
    ):
        raise Gate3EconomicCentralEvidenceError(
            "central segment summary requires exactly twenty canonical receipts"
        )
    try:
        strict = tuple(
            Gate3CentralCellReceipt.model_validate(
                item.model_dump(mode="python", round_trip=True), strict=True
            )
            for item in receipts
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicCentralEvidenceError(
            "central segment receipt failed strict validation"
        ) from error
    corpus_id = strict[0].corpus_id
    if (
        tuple(item.stream_id for item in strict) != canonical_stream_ids
        or len({item.receipt_id for item in strict}) != 20
        or len({item.cell_id for item in strict}) != 20
        or any(
            item.roots != roots
            or item.corpus_id != corpus_id
            or item.baseline_freeze_id != freeze.freeze_id
            or item.baseline_freeze_content_sha256 != freeze.content_sha256
            or item.selected_policy_id != freeze.selected_policy_id
            or item.decision_addendum_id != addendum.addendum_id
            or item.decision_addendum_content_sha256 != addendum.content_sha256
            or item.base_protocol_id != addendum.base_protocol_id
            or item.base_protocol_content_sha256 != addendum.base_protocol_content_sha256
            for item in strict
        )
    ):
        raise Gate3EconomicCentralEvidenceError(
            "central segment receipts differ from canonical upstream bindings"
        )
    return strict


def _statistics(
    receipts: tuple[Gate3CentralCellReceipt, ...],
) -> dict[str, str | int]:
    corpus_id = receipts[0].corpus_id
    indices = _bootstrap_indices(corpus_id)
    f_values = tuple(Decimal(item.full_future_savings_percent) for item in receipts)
    k_values = tuple(Decimal(item.known_only_causal_savings_percent) for item in receipts)
    unknown_values = tuple(Decimal(item.unknown_future_contribution_points) for item in receipts)
    with localcontext() as context:
        context.prec = 50
        f_mean = sum(f_values, Decimal(0)) / Decimal(20)
        k_mean = sum(k_values, Decimal(0)) / Decimal(20)
        unknown_mean = sum(unknown_values, Decimal(0)) / Decimal(20)
        f_fraction = Decimal(sum(item > 0 for item in f_values)) / Decimal(20)
        k_fraction = Decimal(sum(item > 0 for item in k_values)) / Decimal(20)
    f_array = np.asarray([float(item) for item in f_values], dtype=np.float64)
    k_array = np.asarray([float(item) for item in k_values], dtype=np.float64)
    f_bootstrap = f_array[indices].mean(axis=1)
    k_bootstrap = k_array[indices].mean(axis=1)
    f_bounds = np.quantile(f_bootstrap, (0.025, 0.975), method="linear")
    k_bounds = np.quantile(k_bootstrap, (0.025, 0.975), method="linear")
    return {
        "f_mean_savings_percent": _metric(f_mean),
        "f_mean_ci_lower_percent": _metric(float(f_bounds[0])),
        "f_mean_ci_upper_percent": _metric(float(f_bounds[1])),
        "f_median_savings_percent": _metric(_median(f_values)),
        "f_positive_stream_count": sum(item > 0 for item in f_values),
        "f_positive_stream_fraction": _metric(f_fraction),
        "unknown_headroom_mean_percentage_points": _metric(unknown_mean),
        "k_mean_savings_percent": _metric(k_mean),
        "k_mean_ci_lower_percent": _metric(float(k_bounds[0])),
        "k_mean_ci_upper_percent": _metric(float(k_bounds[1])),
        "k_median_savings_percent": _metric(_median(k_values)),
        "k_positive_stream_count": sum(item > 0 for item in k_values),
        "k_positive_stream_fraction": _metric(k_fraction),
    }


def _segment_next_action(
    corpus_id: Gate3CorpusId,
    decision: EconomicSegmentDecision,
) -> CentralSegmentNextAction | None:
    if corpus_id == "loco-2dics":
        return cast(CentralSegmentNextAction, decision.loco_next_step)
    if decision.candidate_classification == "causal_candidate":
        return "CONTINUE_ADVERSE_LECTRA"
    if decision.candidate_classification == "forecast_candidate":
        return "CONTINUE_FORECAST_LECTRA"
    return None


class Gate3EconomicSegmentSummary(FrozenExperimentModel):
    """Authenticated compact statistics and decision for one 20-stream segment."""

    schema_version: Literal["yieldforge.m11-economic-central-segment.v1"] = (
        "yieldforge.m11-economic-central-segment.v1"
    )
    summary_id: StrictStr = Field(pattern=r"^yfm11econsegsummary-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_metrics_id: StrictStr = Field(pattern=r"^yfm11econmetrics-[0-9a-f]{24}$")
    source_metrics_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_protocol_id: StrictStr = Field(pattern=r"^yfm11econp-[0-9a-f]{24}$")
    base_protocol_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision_addendum_id: StrictStr = Field(pattern=r"^yfm11econdec-[0-9a-f]{24}$")
    decision_addendum_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    calibration_manifest_id: StrictStr = Field(pattern=r"^yfm11econcalman-[0-9a-f]{24}$")
    calibration_manifest_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    validity_manifest_id: StrictStr = Field(pattern=r"^yfm11econvalman-[0-9a-f]{24}$")
    validity_manifest_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    validity_evidence_id: StrictStr = Field(pattern=r"^yfm11g3valrcpt-[0-9a-f]{24}$")
    validity_evidence_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_id: Gate3CorpusId
    baseline_freeze_id: StrictStr = Field(pattern=r"^yfm11g3bf-[0-9a-f]{24}$")
    baseline_freeze_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selected_policy_id: StrictStr
    canonical_stream_ids: tuple[StrictStr, ...] = Field(min_length=20, max_length=20)
    receipts: tuple[Gate3CentralCellReceipt, ...] = Field(min_length=20, max_length=20)
    receipt_ids: tuple[StrictStr, ...] = Field(min_length=20, max_length=20)
    receipt_content_sha256s: tuple[StrictStr, ...] = Field(min_length=20, max_length=20)
    cell_ids: tuple[StrictStr, ...] = Field(min_length=20, max_length=20)
    cell_content_sha256s: tuple[StrictStr, ...] = Field(min_length=20, max_length=20)
    stream_count: Literal[20] = 20
    bootstrap_generator: Literal["numpy.Generator(PCG64(0))"] = "numpy.Generator(PCG64(0))"
    bootstrap_resamples: Literal[10000] = 10_000
    bootstrap_resampling_unit: Literal["paired_stream"] = "paired_stream"
    bootstrap_quantile_method: Literal["linear_type_7"] = "linear_type_7"
    bootstrap_corpus_draw_order: Literal["lectra_then_loco"] = "lectra_then_loco"
    metric_rounding: Literal["twelve_decimal_half_even"] = "twelve_decimal_half_even"
    f_mean_savings_percent: StrictStr = Field(pattern=_METRIC_PATTERN)
    f_mean_ci_lower_percent: StrictStr = Field(pattern=_METRIC_PATTERN)
    f_mean_ci_upper_percent: StrictStr = Field(pattern=_METRIC_PATTERN)
    f_median_savings_percent: StrictStr = Field(pattern=_METRIC_PATTERN)
    f_positive_stream_count: StrictInt = Field(ge=0, le=20)
    f_positive_stream_fraction: StrictStr = Field(pattern=_METRIC_PATTERN)
    unknown_headroom_mean_percentage_points: StrictStr = Field(pattern=_METRIC_PATTERN)
    k_mean_savings_percent: StrictStr = Field(pattern=_METRIC_PATTERN)
    k_mean_ci_lower_percent: StrictStr = Field(pattern=_METRIC_PATTERN)
    k_mean_ci_upper_percent: StrictStr = Field(pattern=_METRIC_PATTERN)
    k_median_savings_percent: StrictStr = Field(pattern=_METRIC_PATTERN)
    k_positive_stream_count: StrictInt = Field(ge=0, le=20)
    k_positive_stream_fraction: StrictStr = Field(pattern=_METRIC_PATTERN)
    decision: EconomicSegmentDecision
    next_action: CentralSegmentNextAction | None
    complete: Literal[True] = True
    productization_authorized: Literal[False] = False
    bounded_pilot_authorized: Literal[False] = False

    @model_validator(mode="after")
    def require_complete_recomputation_and_identity(self) -> Self:
        protocol = build_economic_resolution_protocol()
        addendum = build_economic_decision_addendum(base_protocol=protocol)
        if (
            (self.base_protocol_id, self.base_protocol_content_sha256)
            != (protocol.protocol_id, protocol.content_sha256)
            or (self.decision_addendum_id, self.decision_addendum_content_sha256)
            != (addendum.addendum_id, addendum.content_sha256)
            or self.roots.content_sha256 != protocol.legacy_root_content_sha256
            or tuple(item.stream_id for item in self.receipts) != self.canonical_stream_ids
            or self.receipt_ids != tuple(item.receipt_id for item in self.receipts)
            or self.receipt_content_sha256s != tuple(item.content_sha256 for item in self.receipts)
            or self.cell_ids != tuple(item.cell_id for item in self.receipts)
            or self.cell_content_sha256s
            != tuple(item.cell_content_sha256 for item in self.receipts)
            or len(set(self.canonical_stream_ids)) != 20
            or len(set(self.receipt_ids)) != 20
            or len(set(self.cell_ids)) != 20
            or any(
                item.roots != self.roots
                or item.corpus_id != self.corpus_id
                or item.baseline_freeze_id != self.baseline_freeze_id
                or item.baseline_freeze_content_sha256 != self.baseline_freeze_content_sha256
                or item.selected_policy_id != self.selected_policy_id
                for item in self.receipts
            )
        ):
            raise ValueError("central segment summary source bindings do not reconcile")
        calculated = _statistics(self.receipts)
        actual = {key: getattr(self, key) for key in calculated}
        if actual != calculated:
            raise ValueError("central segment summary metrics do not reconcile")
        metrics_semantic = self.model_dump(
            mode="json",
            exclude={
                "summary_id",
                "content_sha256",
                "source_metrics_id",
                "source_metrics_content_sha256",
                "decision",
                "next_action",
                "complete",
                "productization_authorized",
                "bounded_pilot_authorized",
            },
        )
        metrics_digest = semantic_sha256(metrics_semantic)
        if (
            self.source_metrics_id != f"yfm11econmetrics-{metrics_digest[:24]}"
            or self.source_metrics_content_sha256 != f"sha256:{metrics_digest}"
        ):
            raise ValueError("central segment source-metrics identity does not reconcile")
        decision = build_economic_segment_decision(
            addendum=addendum,
            corpus_id=self.corpus_id,
            source_summary_id=self.source_metrics_id,
            source_summary_content_sha256=self.source_metrics_content_sha256,
            f_mean_savings_percent=self.f_mean_savings_percent,
            f_mean_ci_lower_percent=self.f_mean_ci_lower_percent,
            f_median_savings_percent=self.f_median_savings_percent,
            f_positive_stream_fraction=self.f_positive_stream_fraction,
            unknown_headroom_mean_percentage_points=(self.unknown_headroom_mean_percentage_points),
            k_mean_savings_percent=self.k_mean_savings_percent,
            k_mean_ci_lower_percent=self.k_mean_ci_lower_percent,
            k_median_savings_percent=self.k_median_savings_percent,
            k_positive_stream_fraction=self.k_positive_stream_fraction,
        )
        if self.decision != decision or self.next_action != _segment_next_action(
            self.corpus_id, decision
        ):
            raise ValueError("central segment decision does not reconcile")
        digest = semantic_sha256(self, excluded_fields={"summary_id", "content_sha256"})
        if self.summary_id != f"yfm11econsegsummary-{digest[:24]}" or (
            self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("central segment summary identity does not reconcile")
        return self


def build_gate3_economic_segment_summary(
    receipts: tuple[Gate3CentralCellReceipt, ...],
    *,
    canonical_stream_ids: tuple[str, ...],
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> Gate3EconomicSegmentSummary:
    """Recompute one exact 20-stream F/K screen from compact cell receipts."""

    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    if not receipts:
        raise Gate3EconomicCentralEvidenceError(
            "central segment summary requires exactly twenty canonical receipts"
        )
    corpus_id = receipts[0].corpus_id
    if corpus_id not in _CORPUS_ORDER:
        raise Gate3EconomicCentralEvidenceError("central segment corpus is not registered")
    freeze = _freeze_for(calibration, corpus_id)
    strict_receipts = _strict_receipts(
        receipts,
        canonical_stream_ids=canonical_stream_ids,
        roots=calibration.roots,
        freeze=freeze,
        addendum=addendum,
    )
    statistics = _statistics(strict_receipts)
    common = {
        "schema_version": "yieldforge.m11-economic-central-segment.v1",
        "base_protocol_id": calibration.protocol.protocol_id,
        "base_protocol_content_sha256": calibration.protocol.content_sha256,
        "decision_addendum_id": addendum.addendum_id,
        "decision_addendum_content_sha256": addendum.content_sha256,
        "roots": calibration.roots,
        "calibration_manifest_id": calibration.manifest_id,
        "calibration_manifest_content_sha256": calibration.content_sha256,
        "validity_manifest_id": validity.manifest_id,
        "validity_manifest_content_sha256": validity.content_sha256,
        "validity_evidence_id": validity.validity_evidence.evidence_id,
        "validity_evidence_content_sha256": validity.validity_evidence.content_sha256,
        "corpus_id": corpus_id,
        "baseline_freeze_id": freeze.freeze_id,
        "baseline_freeze_content_sha256": freeze.content_sha256,
        "selected_policy_id": freeze.selected_policy_id,
        "canonical_stream_ids": canonical_stream_ids,
        "receipts": strict_receipts,
        "receipt_ids": tuple(item.receipt_id for item in strict_receipts),
        "receipt_content_sha256s": tuple(item.content_sha256 for item in strict_receipts),
        "cell_ids": tuple(item.cell_id for item in strict_receipts),
        "cell_content_sha256s": tuple(item.cell_content_sha256 for item in strict_receipts),
        "stream_count": 20,
        "bootstrap_generator": "numpy.Generator(PCG64(0))",
        "bootstrap_resamples": 10_000,
        "bootstrap_resampling_unit": "paired_stream",
        "bootstrap_quantile_method": "linear_type_7",
        "bootstrap_corpus_draw_order": "lectra_then_loco",
        "metric_rounding": "twelve_decimal_half_even",
        **statistics,
    }
    metrics_digest = semantic_sha256(
        {
            key: (
                value.model_dump(mode="json")
                if hasattr(value, "model_dump")
                else [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                    for item in value
                ]
                if isinstance(value, tuple)
                else value
            )
            for key, value in common.items()
        }
    )
    source_metrics_id = f"yfm11econmetrics-{metrics_digest[:24]}"
    source_metrics_sha = f"sha256:{metrics_digest}"
    decision = build_economic_segment_decision(
        addendum=addendum,
        corpus_id=corpus_id,
        source_summary_id=source_metrics_id,
        source_summary_content_sha256=source_metrics_sha,
        f_mean_savings_percent=cast(str, statistics["f_mean_savings_percent"]),
        f_mean_ci_lower_percent=cast(str, statistics["f_mean_ci_lower_percent"]),
        f_median_savings_percent=cast(str, statistics["f_median_savings_percent"]),
        f_positive_stream_fraction=cast(str, statistics["f_positive_stream_fraction"]),
        unknown_headroom_mean_percentage_points=cast(
            str, statistics["unknown_headroom_mean_percentage_points"]
        ),
        k_mean_savings_percent=cast(str, statistics["k_mean_savings_percent"]),
        k_mean_ci_lower_percent=cast(str, statistics["k_mean_ci_lower_percent"]),
        k_median_savings_percent=cast(str, statistics["k_median_savings_percent"]),
        k_positive_stream_fraction=cast(str, statistics["k_positive_stream_fraction"]),
    )
    semantic = {
        **common,
        "source_metrics_id": source_metrics_id,
        "source_metrics_content_sha256": source_metrics_sha,
        "decision": decision,
        "next_action": _segment_next_action(corpus_id, decision),
        "complete": True,
        "productization_authorized": False,
        "bounded_pilot_authorized": False,
    }
    digest = semantic_sha256(
        {
            key: (
                value.model_dump(mode="json")
                if hasattr(value, "model_dump")
                else [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                    for item in value
                ]
                if isinstance(value, tuple)
                else value
            )
            for key, value in semantic.items()
        }
    )
    try:
        return Gate3EconomicSegmentSummary(
            summary_id=f"yfm11econsegsummary-{digest[:24]}",
            content_sha256=f"sha256:{digest}",
            **semantic,
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicCentralEvidenceError(str(error)) from error


class Gate3CentralCellFailureReceipt(FrozenExperimentModel):
    """Bounded authenticated record of one failed central-cell attempt."""

    schema_version: Literal["yieldforge.m11-economic-central-cell-failure.v1"] = (
        "yieldforge.m11-economic-central-cell-failure.v1"
    )
    failure_receipt_id: StrictStr = Field(pattern=r"^yfm11econcellfail-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_protocol_id: StrictStr = Field(pattern=r"^yfm11econp-[0-9a-f]{24}$")
    base_protocol_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision_addendum_id: StrictStr = Field(pattern=r"^yfm11econdec-[0-9a-f]{24}$")
    decision_addendum_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    calibration_manifest_id: StrictStr = Field(pattern=r"^yfm11econcalman-[0-9a-f]{24}$")
    calibration_manifest_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    validity_manifest_id: StrictStr = Field(pattern=r"^yfm11econvalman-[0-9a-f]{24}$")
    validity_manifest_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    validity_evidence_id: StrictStr = Field(pattern=r"^yfm11g3valrcpt-[0-9a-f]{24}$")
    validity_evidence_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_position: StrictInt = Field(ge=0, le=39)
    attempt_number: StrictInt = Field(ge=1, le=999)
    corpus_id: Gate3CorpusId
    stream_id: StrictStr = Field(min_length=1, max_length=128)
    baseline_freeze_id: StrictStr = Field(pattern=r"^yfm11g3bf-[0-9a-f]{24}$")
    baseline_freeze_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    completed_checkpoint_count: StrictInt = Field(ge=0, le=39)
    completed_checkpoint_prefix_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    previous_failure_receipt_id: StrictStr | None = Field(
        default=None, pattern=r"^yfm11econcellfail-[0-9a-f]{24}$"
    )
    previous_failure_receipt_content_sha256: StrictStr | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    exception_type: StrictStr = Field(min_length=1, max_length=240)
    failure_detail: StrictStr = Field(min_length=1, max_length=1000)
    status: Literal["infrastructure_failure"] = "infrastructure_failure"
    economic_value_resolved: Literal[False] = False
    productization_authorized: Literal[False] = False
    bounded_pilot_authorized: Literal[False] = False
    complete: Literal[True] = True

    @model_validator(mode="after")
    def require_exact_failure_bindings_and_identity(self) -> Self:
        protocol = build_economic_resolution_protocol()
        addendum = build_economic_decision_addendum(base_protocol=protocol)
        expected_range = range(0, 20) if self.corpus_id == "loco-2dics" else range(20, 40)
        prior = (
            self.previous_failure_receipt_id,
            self.previous_failure_receipt_content_sha256,
        )
        if (
            (self.base_protocol_id, self.base_protocol_content_sha256)
            != (protocol.protocol_id, protocol.content_sha256)
            or (self.decision_addendum_id, self.decision_addendum_content_sha256)
            != (addendum.addendum_id, addendum.content_sha256)
            or self.roots.content_sha256 != protocol.legacy_root_content_sha256
            or self.execution_position not in expected_range
            or self.completed_checkpoint_count != self.execution_position
            or (self.attempt_number == 1 and prior != (None, None))
            or (self.attempt_number > 1 and None in prior)
        ):
            raise ValueError("central failure receipt bindings do not reconcile")
        digest = semantic_sha256(self, excluded_fields={"failure_receipt_id", "content_sha256"})
        if self.failure_receipt_id != f"yfm11econcellfail-{digest[:24]}" or (
            self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("central failure receipt identity does not reconcile")
        return self


def build_gate3_central_cell_failure_receipt(
    *,
    execution_position: int,
    corpus_id: Gate3CorpusId,
    stream_id: str,
    completed_checkpoints: tuple[Gate3CentralCellCheckpoint, ...],
    previous_failure_receipt: Gate3CentralCellFailureReceipt | None,
    exception_type: str,
    failure_detail: str,
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> Gate3CentralCellFailureReceipt:
    """Bind one bounded failed attempt to its exact upstream and retry prefix."""

    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    if type(completed_checkpoints) is not tuple:
        raise Gate3EconomicCentralEvidenceError(
            "central failure receipt checkpoint prefix must be a tuple"
        )
    try:
        checkpoints = tuple(
            Gate3CentralCellCheckpoint.model_validate(
                item.model_dump(mode="python", round_trip=True), strict=True
            )
            for item in completed_checkpoints
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicCentralEvidenceError(
            "central failure receipt checkpoint prefix is malformed"
        ) from error
    if (
        type(execution_position) is not int
        or execution_position not in range(40)
        or len(checkpoints) != execution_position
        or tuple(item.execution_position for item in checkpoints)
        != tuple(range(execution_position))
        or any(
            item.roots != calibration.roots
            or item.calibration_manifest_id != calibration.manifest_id
            or item.calibration_manifest_content_sha256 != calibration.content_sha256
            or item.validity_manifest_id != validity.manifest_id
            or item.validity_manifest_content_sha256 != validity.content_sha256
            or item.decision_addendum_id != addendum.addendum_id
            or item.decision_addendum_content_sha256 != addendum.content_sha256
            for item in checkpoints
        )
    ):
        raise Gate3EconomicCentralEvidenceError(
            "central failure receipt checkpoint prefix differs from stage state"
        )
    expected_corpus: Gate3CorpusId = "loco-2dics" if execution_position < 20 else "lectra-m3-m4"
    if corpus_id != expected_corpus or type(stream_id) is not str or not stream_id:
        raise Gate3EconomicCentralEvidenceError(
            "central failure receipt stream differs from canonical position"
        )
    freeze = _freeze_for(calibration, corpus_id)
    checkpoint_prefix_sha = "sha256:" + semantic_sha256(
        tuple((item.checkpoint_id, item.content_sha256) for item in checkpoints)
    )
    previous: Gate3CentralCellFailureReceipt | None
    if previous_failure_receipt is None:
        previous = None
        attempt_number = 1
    else:
        try:
            previous = Gate3CentralCellFailureReceipt.model_validate(
                previous_failure_receipt.model_dump(mode="python", round_trip=True),
                strict=True,
            )
        except (AttributeError, TypeError, ValidationError, ValueError) as error:
            raise Gate3EconomicCentralEvidenceError(
                "central prior failure receipt is malformed"
            ) from error
        _require_artifact_upstream(
            previous,
            calibration=calibration,
            validity=validity,
            addendum=addendum,
        )
        if (
            previous.execution_position != execution_position
            or previous.corpus_id != corpus_id
            or previous.stream_id != stream_id
            or previous.roots != calibration.roots
            or previous.calibration_manifest_id != calibration.manifest_id
            or previous.validity_manifest_id != validity.manifest_id
            or previous.decision_addendum_id != addendum.addendum_id
            or previous.completed_checkpoint_count != len(checkpoints)
            or previous.completed_checkpoint_prefix_content_sha256 != checkpoint_prefix_sha
            or previous.attempt_number >= 999
        ):
            raise Gate3EconomicCentralEvidenceError(
                "central prior failure receipt differs from the exact retry prefix"
            )
        attempt_number = previous.attempt_number + 1
    if type(exception_type) is not str or type(failure_detail) is not str:
        raise Gate3EconomicCentralEvidenceError(
            "central failure receipt requires textual bounded failure fields"
        )
    bounded_type = exception_type.strip()[:240] or "builtins.Exception"
    bounded_detail = failure_detail.strip()[:1000] or "exception carried no detail"
    semantic = {
        "schema_version": "yieldforge.m11-economic-central-cell-failure.v1",
        "base_protocol_id": calibration.protocol.protocol_id,
        "base_protocol_content_sha256": calibration.protocol.content_sha256,
        "decision_addendum_id": addendum.addendum_id,
        "decision_addendum_content_sha256": addendum.content_sha256,
        "roots": calibration.roots,
        "calibration_manifest_id": calibration.manifest_id,
        "calibration_manifest_content_sha256": calibration.content_sha256,
        "validity_manifest_id": validity.manifest_id,
        "validity_manifest_content_sha256": validity.content_sha256,
        "validity_evidence_id": validity.validity_evidence.evidence_id,
        "validity_evidence_content_sha256": validity.validity_evidence.content_sha256,
        "execution_position": execution_position,
        "attempt_number": attempt_number,
        "corpus_id": corpus_id,
        "stream_id": stream_id,
        "baseline_freeze_id": freeze.freeze_id,
        "baseline_freeze_content_sha256": freeze.content_sha256,
        "completed_checkpoint_count": len(checkpoints),
        "completed_checkpoint_prefix_content_sha256": checkpoint_prefix_sha,
        "previous_failure_receipt_id": (
            previous.failure_receipt_id if previous is not None else None
        ),
        "previous_failure_receipt_content_sha256": (
            previous.content_sha256 if previous is not None else None
        ),
        "exception_type": bounded_type,
        "failure_detail": bounded_detail,
        "status": "infrastructure_failure",
        "economic_value_resolved": False,
        "productization_authorized": False,
        "bounded_pilot_authorized": False,
        "complete": True,
    }
    digest = semantic_sha256({key: _json_value(value) for key, value in semantic.items()})
    try:
        return Gate3CentralCellFailureReceipt(
            failure_receipt_id=f"yfm11econcellfail-{digest[:24]}",
            content_sha256=f"sha256:{digest}",
            **semantic,
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicCentralEvidenceError(str(error)) from error


class Gate3CentralCellCheckpoint(FrozenExperimentModel):
    """Immediate durable binding from one compact sidecar receipt to the stage."""

    schema_version: Literal["yieldforge.m11-economic-central-cell-checkpoint.v2"] = (
        "yieldforge.m11-economic-central-cell-checkpoint.v2"
    )
    checkpoint_id: StrictStr = Field(pattern=r"^yfm11econcellcp-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_protocol_id: StrictStr = Field(pattern=r"^yfm11econp-[0-9a-f]{24}$")
    base_protocol_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision_addendum_id: StrictStr = Field(pattern=r"^yfm11econdec-[0-9a-f]{24}$")
    decision_addendum_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    calibration_manifest_id: StrictStr = Field(pattern=r"^yfm11econcalman-[0-9a-f]{24}$")
    calibration_manifest_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    validity_manifest_id: StrictStr = Field(pattern=r"^yfm11econvalman-[0-9a-f]{24}$")
    validity_manifest_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    validity_evidence_id: StrictStr = Field(pattern=r"^yfm11g3valrcpt-[0-9a-f]{24}$")
    validity_evidence_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_position: StrictInt = Field(ge=0, le=39)
    corpus_id: Gate3CorpusId
    stream_id: StrictStr = Field(min_length=1, max_length=128)
    baseline_freeze_id: StrictStr = Field(pattern=r"^yfm11g3bf-[0-9a-f]{24}$")
    baseline_freeze_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    receipt_id: StrictStr = Field(pattern=r"^yfm11g3cellrcpt-[0-9a-f]{24}$")
    receipt_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    receipt: Gate3CentralCellReceipt
    prior_failure_attempt_count: StrictInt = Field(ge=0, le=999)
    prior_failure_receipt_id: StrictStr | None = Field(
        default=None, pattern=r"^yfm11econcellfail-[0-9a-f]{24}$"
    )
    prior_failure_receipt_content_sha256: StrictStr | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    complete: Literal[True] = True

    @model_validator(mode="after")
    def require_exact_receipt_position_and_identity(self) -> Self:
        protocol = build_economic_resolution_protocol()
        addendum = build_economic_decision_addendum(base_protocol=protocol)
        expected_range = range(0, 20) if self.corpus_id == "loco-2dics" else range(20, 40)
        if (
            (self.base_protocol_id, self.base_protocol_content_sha256)
            != (protocol.protocol_id, protocol.content_sha256)
            or (self.decision_addendum_id, self.decision_addendum_content_sha256)
            != (addendum.addendum_id, addendum.content_sha256)
            or self.roots.content_sha256 != protocol.legacy_root_content_sha256
            or self.execution_position not in expected_range
            or self.receipt.roots != self.roots
            or self.receipt.corpus_id != self.corpus_id
            or self.receipt.stream_id != self.stream_id
            or self.receipt.baseline_freeze_id != self.baseline_freeze_id
            or self.receipt.baseline_freeze_content_sha256 != self.baseline_freeze_content_sha256
            or self.receipt_id != self.receipt.receipt_id
            or self.receipt_content_sha256 != self.receipt.content_sha256
            or (
                self.prior_failure_attempt_count == 0
                and (
                    self.prior_failure_receipt_id,
                    self.prior_failure_receipt_content_sha256,
                )
                != (None, None)
            )
            or (
                self.prior_failure_attempt_count > 0
                and None
                in (
                    self.prior_failure_receipt_id,
                    self.prior_failure_receipt_content_sha256,
                )
            )
        ):
            raise ValueError("central cell checkpoint bindings do not reconcile")
        digest = semantic_sha256(self, excluded_fields={"checkpoint_id", "content_sha256"})
        if self.checkpoint_id != f"yfm11econcellcp-{digest[:24]}" or (
            self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("central cell checkpoint identity does not reconcile")
        return self


def build_gate3_central_cell_checkpoint(
    receipt: Gate3CentralCellReceipt,
    *,
    execution_position: int,
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
    prior_failure_receipts: tuple[Gate3CentralCellFailureReceipt, ...] = (),
) -> Gate3CentralCellCheckpoint:
    """Bind one persisted cell receipt to the exact authorizing parents."""

    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    try:
        strict_receipt = Gate3CentralCellReceipt.model_validate(
            receipt.model_dump(mode="python", round_trip=True), strict=True
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicCentralEvidenceError(
            "central checkpoint receipt failed strict validation"
        ) from error
    freeze = _freeze_for(calibration, strict_receipt.corpus_id)
    if (
        strict_receipt.roots != calibration.roots
        or strict_receipt.baseline_freeze_id != freeze.freeze_id
        or strict_receipt.baseline_freeze_content_sha256 != freeze.content_sha256
        or strict_receipt.selected_policy_id != freeze.selected_policy_id
        or strict_receipt.decision_addendum_id != addendum.addendum_id
        or strict_receipt.decision_addendum_content_sha256 != addendum.content_sha256
    ):
        raise Gate3EconomicCentralEvidenceError(
            "central checkpoint receipt differs from authorizing parents"
        )
    if type(prior_failure_receipts) is not tuple:
        raise Gate3EconomicCentralEvidenceError(
            "central checkpoint prior-failure chain must be a tuple"
        )
    try:
        failures = tuple(
            Gate3CentralCellFailureReceipt.model_validate(
                item.model_dump(mode="python", round_trip=True), strict=True
            )
            for item in prior_failure_receipts
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicCentralEvidenceError(
            "central checkpoint prior-failure chain is malformed"
        ) from error
    for failure in failures:
        _require_artifact_upstream(
            failure,
            calibration=calibration,
            validity=validity,
            addendum=addendum,
        )
    if (
        len(failures) > 999
        or tuple(item.attempt_number for item in failures) != tuple(range(1, len(failures) + 1))
        or any(
            item.execution_position != execution_position
            or item.corpus_id != strict_receipt.corpus_id
            or item.stream_id != strict_receipt.stream_id
            or item.roots != calibration.roots
            or item.calibration_manifest_id != calibration.manifest_id
            or item.validity_manifest_id != validity.manifest_id
            or item.decision_addendum_id != addendum.addendum_id
            or item.baseline_freeze_id != freeze.freeze_id
            or item.baseline_freeze_content_sha256 != freeze.content_sha256
            or (
                index > 0
                and (
                    item.previous_failure_receipt_id,
                    item.previous_failure_receipt_content_sha256,
                )
                != (
                    failures[index - 1].failure_receipt_id,
                    failures[index - 1].content_sha256,
                )
            )
            for index, item in enumerate(failures)
        )
    ):
        raise Gate3EconomicCentralEvidenceError(
            "central checkpoint prior-failure chain differs from the exact cell"
        )
    failure_head = failures[-1] if failures else None
    semantic = {
        "schema_version": "yieldforge.m11-economic-central-cell-checkpoint.v2",
        "base_protocol_id": calibration.protocol.protocol_id,
        "base_protocol_content_sha256": calibration.protocol.content_sha256,
        "decision_addendum_id": addendum.addendum_id,
        "decision_addendum_content_sha256": addendum.content_sha256,
        "roots": calibration.roots,
        "calibration_manifest_id": calibration.manifest_id,
        "calibration_manifest_content_sha256": calibration.content_sha256,
        "validity_manifest_id": validity.manifest_id,
        "validity_manifest_content_sha256": validity.content_sha256,
        "validity_evidence_id": validity.validity_evidence.evidence_id,
        "validity_evidence_content_sha256": validity.validity_evidence.content_sha256,
        "execution_position": execution_position,
        "corpus_id": strict_receipt.corpus_id,
        "stream_id": strict_receipt.stream_id,
        "baseline_freeze_id": freeze.freeze_id,
        "baseline_freeze_content_sha256": freeze.content_sha256,
        "receipt_id": strict_receipt.receipt_id,
        "receipt_content_sha256": strict_receipt.content_sha256,
        "receipt": strict_receipt,
        "prior_failure_attempt_count": len(failures),
        "prior_failure_receipt_id": (
            failure_head.failure_receipt_id if failure_head is not None else None
        ),
        "prior_failure_receipt_content_sha256": (
            failure_head.content_sha256 if failure_head is not None else None
        ),
        "complete": True,
    }
    digest = semantic_sha256({key: _json_value(value) for key, value in semantic.items()})
    try:
        return Gate3CentralCellCheckpoint(
            checkpoint_id=f"yfm11econcellcp-{digest[:24]}",
            content_sha256=f"sha256:{digest}",
            **semantic,
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicCentralEvidenceError(str(error)) from error


CentralManifestStatus = Literal[
    "adverse_confirmation_required",
    "forecast_confirmation_required",
    "insufficient_current_modeled_value",
]
CentralGlobalDisposition = Literal[
    "CONTINUE_ADVERSE_SEGMENT_CONFIRMATION",
    "CONTINUE_FORECAST_SEGMENT_CONFIRMATION",
    "INSUFFICIENT_CURRENT_MODELED_VALUE",
]


class Gate3EconomicCentralManifest(FrozenExperimentModel):
    """Terminal central-stage branch without any productization authorization."""

    schema_version: Literal["yieldforge.m11-economic-central-manifest.v1"] = (
        "yieldforge.m11-economic-central-manifest.v1"
    )
    manifest_id: StrictStr = Field(pattern=r"^yfm11econcentral-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_protocol_id: StrictStr = Field(pattern=r"^yfm11econp-[0-9a-f]{24}$")
    base_protocol_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision_addendum_id: StrictStr = Field(pattern=r"^yfm11econdec-[0-9a-f]{24}$")
    decision_addendum_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    roots: Gate3RootBinding
    calibration_manifest_id: StrictStr = Field(pattern=r"^yfm11econcalman-[0-9a-f]{24}$")
    calibration_manifest_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    validity_manifest_id: StrictStr = Field(pattern=r"^yfm11econvalman-[0-9a-f]{24}$")
    validity_manifest_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    validity_evidence_id: StrictStr = Field(pattern=r"^yfm11g3valrcpt-[0-9a-f]{24}$")
    validity_evidence_content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    checkpoints: tuple[Gate3CentralCellCheckpoint, ...] = Field(min_length=20, max_length=40)
    checkpoint_ids: tuple[StrictStr, ...] = Field(min_length=20, max_length=40)
    checkpoint_content_sha256s: tuple[StrictStr, ...] = Field(min_length=20, max_length=40)
    segment_summaries: tuple[Gate3EconomicSegmentSummary, ...] = Field(min_length=1, max_length=2)
    total_cell_count: Literal[20, 40]
    cross_segment_decision: EconomicCrossSegmentDecision | None
    status: CentralManifestStatus
    global_disposition: CentralGlobalDisposition
    next_actions: tuple[StrictStr, ...] = Field(max_length=2)
    central_stage_complete: Literal[True] = True
    economic_value_resolved: StrictBool
    resolution_scope: Literal["current_modeled_value_not_proof_no_possible_algorithm_can_work"] = (
        "current_modeled_value_not_proof_no_possible_algorithm_can_work"
    )
    productization_authorized: Literal[False] = False
    bounded_pilot_authorized: Literal[False] = False

    @model_validator(mode="after")
    def require_branch_reduction_and_identity(self) -> Self:
        protocol = build_economic_resolution_protocol()
        addendum = build_economic_decision_addendum(base_protocol=protocol)
        checkpoints = self.checkpoints
        summaries = self.segment_summaries
        if (
            (self.base_protocol_id, self.base_protocol_content_sha256)
            != (protocol.protocol_id, protocol.content_sha256)
            or (self.decision_addendum_id, self.decision_addendum_content_sha256)
            != (addendum.addendum_id, addendum.content_sha256)
            or self.roots.content_sha256 != protocol.legacy_root_content_sha256
            or self.checkpoint_ids != tuple(item.checkpoint_id for item in checkpoints)
            or self.checkpoint_content_sha256s != tuple(item.content_sha256 for item in checkpoints)
            or tuple(item.execution_position for item in checkpoints)
            != tuple(range(len(checkpoints)))
            or len(checkpoints) != self.total_cell_count
            or tuple(item.corpus_id for item in checkpoints[:20]) != ("loco-2dics",) * 20
            or (
                len(checkpoints) == 40
                and tuple(item.corpus_id for item in checkpoints[20:]) != ("lectra-m3-m4",) * 20
            )
            or tuple(item.corpus_id for item in summaries)
            != (("loco-2dics",) if len(summaries) == 1 else ("loco-2dics", "lectra-m3-m4"))
            or any(
                item.roots != self.roots
                or item.calibration_manifest_id != self.calibration_manifest_id
                or item.calibration_manifest_content_sha256
                != self.calibration_manifest_content_sha256
                or item.validity_manifest_id != self.validity_manifest_id
                or item.validity_manifest_content_sha256 != self.validity_manifest_content_sha256
                or item.validity_evidence_id != self.validity_evidence_id
                or item.validity_evidence_content_sha256 != self.validity_evidence_content_sha256
                for item in (*checkpoints, *summaries)
            )
        ):
            raise ValueError("central manifest source bindings do not reconcile")
        for index, summary in enumerate(summaries):
            start = index * 20
            relevant = checkpoints[start : start + 20]
            if summary.receipts != tuple(item.receipt for item in relevant):
                raise ValueError("central manifest summaries differ from checkpoints")
        if len(summaries) == 1:
            decision = summaries[0].decision
            if decision.candidate_classification == "current_segment_red" or len(checkpoints) != 20:
                raise ValueError("central manifest LOCo red requires a Lectra screen")
            expected_cross = None
            expected_disposition: CentralGlobalDisposition = (
                "CONTINUE_ADVERSE_SEGMENT_CONFIRMATION"
                if decision.candidate_classification == "causal_candidate"
                else "CONTINUE_FORECAST_SEGMENT_CONFIRMATION"
            )
            expected_actions = (cast(str, summaries[0].next_action),)
            expected_status: CentralManifestStatus = (
                "adverse_confirmation_required"
                if decision.candidate_classification == "causal_candidate"
                else "forecast_confirmation_required"
            )
            resolved = False
        else:
            if summaries[0].decision.candidate_classification != "current_segment_red" or (
                len(checkpoints) != 40
            ):
                raise ValueError("central manifest Lectra screen requires LOCo red")
            expected_cross = reduce_economic_segment_decisions(
                addendum=addendum,
                loco_decision=summaries[0].decision,
                lectra_decision=summaries[1].decision,
            )
            expected_disposition = cast(CentralGlobalDisposition, expected_cross.global_disposition)
            expected_actions = tuple(expected_cross.next_actions)
            expected_status = {
                "CONTINUE_ADVERSE_SEGMENT_CONFIRMATION": "adverse_confirmation_required",
                "CONTINUE_FORECAST_SEGMENT_CONFIRMATION": "forecast_confirmation_required",
                "INSUFFICIENT_CURRENT_MODELED_VALUE": "insufficient_current_modeled_value",
            }[expected_disposition]  # type: ignore[assignment]
            resolved = expected_disposition == "INSUFFICIENT_CURRENT_MODELED_VALUE"
        if (
            self.cross_segment_decision != expected_cross
            or self.global_disposition != expected_disposition
            or self.next_actions != expected_actions
            or self.status != expected_status
            or self.economic_value_resolved is not resolved
        ):
            raise ValueError("central manifest branch decision does not reconcile")
        digest = semantic_sha256(self, excluded_fields={"manifest_id", "content_sha256"})
        if self.manifest_id != f"yfm11econcentral-{digest[:24]}" or (
            self.content_sha256 != f"sha256:{digest}"
        ):
            raise ValueError("central manifest identity does not reconcile")
        return self


def build_gate3_economic_central_manifest(
    checkpoints: tuple[Gate3CentralCellCheckpoint, ...],
    *,
    segment_summaries: tuple[Gate3EconomicSegmentSummary, ...],
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> Gate3EconomicCentralManifest:
    """Reduce the completed LOCo-first central branch to its exact next action."""

    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    try:
        strict_checkpoints = tuple(
            Gate3CentralCellCheckpoint.model_validate(
                item.model_dump(mode="python", round_trip=True), strict=True
            )
            for item in checkpoints
        )
        strict_summaries = tuple(
            Gate3EconomicSegmentSummary.model_validate(
                item.model_dump(mode="python", round_trip=True), strict=True
            )
            for item in segment_summaries
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicCentralEvidenceError(
            "central manifest inputs failed strict validation"
        ) from error
    if not strict_summaries:
        raise Gate3EconomicCentralEvidenceError("central manifest requires a LOCo summary")
    loco = strict_summaries[0]
    if len(strict_summaries) == 1:
        if loco.corpus_id != "loco-2dics" or (
            loco.decision.candidate_classification == "current_segment_red"
        ):
            raise Gate3EconomicCentralEvidenceError(
                "central manifest requires Lectra after a LOCo current-segment red"
            )
        cross = None
        disposition: CentralGlobalDisposition = (
            "CONTINUE_ADVERSE_SEGMENT_CONFIRMATION"
            if loco.decision.candidate_classification == "causal_candidate"
            else "CONTINUE_FORECAST_SEGMENT_CONFIRMATION"
        )
        actions = (cast(str, loco.next_action),)
    elif len(strict_summaries) == 2:
        if (
            tuple(item.corpus_id for item in strict_summaries) != ("loco-2dics", "lectra-m3-m4")
            or loco.decision.candidate_classification != "current_segment_red"
        ):
            raise Gate3EconomicCentralEvidenceError(
                "central manifest Lectra branch requires LOCo current-segment red"
            )
        cross = reduce_economic_segment_decisions(
            addendum=addendum,
            loco_decision=loco.decision,
            lectra_decision=strict_summaries[1].decision,
        )
        disposition = cast(CentralGlobalDisposition, cross.global_disposition)
        actions = tuple(cross.next_actions)
    else:
        raise Gate3EconomicCentralEvidenceError("central manifest has too many segments")
    status: CentralManifestStatus = {
        "CONTINUE_ADVERSE_SEGMENT_CONFIRMATION": "adverse_confirmation_required",
        "CONTINUE_FORECAST_SEGMENT_CONFIRMATION": "forecast_confirmation_required",
        "INSUFFICIENT_CURRENT_MODELED_VALUE": "insufficient_current_modeled_value",
    }[disposition]  # type: ignore[assignment]
    resolved = disposition == "INSUFFICIENT_CURRENT_MODELED_VALUE"
    semantic = {
        "schema_version": "yieldforge.m11-economic-central-manifest.v1",
        "base_protocol_id": calibration.protocol.protocol_id,
        "base_protocol_content_sha256": calibration.protocol.content_sha256,
        "decision_addendum_id": addendum.addendum_id,
        "decision_addendum_content_sha256": addendum.content_sha256,
        "roots": calibration.roots,
        "calibration_manifest_id": calibration.manifest_id,
        "calibration_manifest_content_sha256": calibration.content_sha256,
        "validity_manifest_id": validity.manifest_id,
        "validity_manifest_content_sha256": validity.content_sha256,
        "validity_evidence_id": validity.validity_evidence.evidence_id,
        "validity_evidence_content_sha256": validity.validity_evidence.content_sha256,
        "checkpoints": strict_checkpoints,
        "checkpoint_ids": tuple(item.checkpoint_id for item in strict_checkpoints),
        "checkpoint_content_sha256s": tuple(item.content_sha256 for item in strict_checkpoints),
        "segment_summaries": strict_summaries,
        "total_cell_count": len(strict_checkpoints),
        "cross_segment_decision": cross,
        "status": status,
        "global_disposition": disposition,
        "next_actions": actions,
        "central_stage_complete": True,
        "economic_value_resolved": resolved,
        "resolution_scope": "current_modeled_value_not_proof_no_possible_algorithm_can_work",
        "productization_authorized": False,
        "bounded_pilot_authorized": False,
    }
    digest = semantic_sha256({key: _json_value(value) for key, value in semantic.items()})
    try:
        return Gate3EconomicCentralManifest(
            manifest_id=f"yfm11econcentral-{digest[:24]}",
            content_sha256=f"sha256:{digest}",
            **semantic,
        )
    except (TypeError, ValidationError, ValueError) as error:
        detail = str(error)
        if "LOCo red" in detail or "Lectra" in detail:
            detail = "central manifest requires Lectra after LOCo red"
        raise Gate3EconomicCentralEvidenceError(detail) from error


def _reject_duplicate_keys(pairs):  # type: ignore[no-untyped-def]
    result = {}
    for key, value in pairs:
        if key in result:
            raise Gate3EconomicCentralEvidenceError("central artifact repeats a JSON key")
        result[key] = value
    return result


def _read_bounded_regular_file(path: Path) -> bytes:
    candidate = Path(path)
    fingerprint = lambda item: (  # noqa: E731
        item.st_dev,
        item.st_ino,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    descriptor: int | None = None
    try:
        before = candidate.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > _MAX_ARTIFACT_BYTES
        ):
            raise Gate3EconomicCentralEvidenceError(
                "central artifact must be a bounded regular non-symlink file"
            )
        before_fingerprint = fingerprint(before)
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or fingerprint(opened) != before_fingerprint:
            raise Gate3EconomicCentralEvidenceError("central artifact changed during open")
        chunks: list[bytes] = []
        remaining = _MAX_ARTIFACT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        during = os.fstat(descriptor)
    except Gate3EconomicCentralEvidenceError:
        raise
    except OSError as error:
        raise Gate3EconomicCentralEvidenceError("central artifact read failed safely") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = candidate.lstat()
    except OSError as error:
        raise Gate3EconomicCentralEvidenceError(
            "central artifact could not be re-inspected"
        ) from error
    if (
        len(raw) > _MAX_ARTIFACT_BYTES
        or len(raw) != before.st_size
        or fingerprint(during) != before_fingerprint
        or fingerprint(after) != before_fingerprint
    ):
        raise Gate3EconomicCentralEvidenceError("central artifact changed during read")
    return raw


def _parse_artifact(path: Path, model_type):  # type: ignore[no-untyped-def]
    raw = _read_bounded_regular_file(path)
    try:
        json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                Gate3EconomicCentralEvidenceError("central artifact contains nonfinite JSON")
            ),
        )
        model = model_type.model_validate_json(raw, strict=True)
    except Gate3EconomicCentralEvidenceError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise Gate3EconomicCentralEvidenceError(
            "central artifact failed strict validation"
        ) from error
    if canonical_pretty_json_bytes(model) != raw:
        raise Gate3EconomicCentralEvidenceError("central artifact encoding is not canonical")
    return model


def _publish(path: Path, model: FrozenExperimentModel, *, label: str) -> Path:
    raw = canonical_pretty_json_bytes(model)
    if len(raw) > _MAX_ARTIFACT_BYTES:
        raise Gate3EconomicCentralEvidenceError("central artifact exceeds its byte bound")
    try:
        return publish_immutable_artifact(
            path,
            raw,
            validate=lambda data: data if data == raw else b"",
            label=label,
        )
    except M8ArtifactPublicationError as error:
        raise Gate3EconomicCentralEvidenceError(
            "central immutable artifact publication failed"
        ) from error


def _discover_paths(
    output_directory: Path, *, prefix: str, pattern: re.Pattern[str]
) -> tuple[Path, ...]:
    directory = Path(output_directory)
    try:
        before = directory.lstat()
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise Gate3EconomicCentralEvidenceError("central discovery directory failed") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise Gate3EconomicCentralEvidenceError(
            "central discovery requires a non-symlink directory"
        )
    paths: list[Path] = []
    try:
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > _MAX_DISCOVERY_ENTRIES:
                    raise Gate3EconomicCentralEvidenceError(
                        "central discovery exceeded its entry bound"
                    )
                if not entry.name.startswith(prefix):
                    continue
                if pattern.fullmatch(entry.name) is None:
                    raise Gate3EconomicCentralEvidenceError(
                        "central discovery found a malformed prefixed artifact"
                    )
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                    raise Gate3EconomicCentralEvidenceError(
                        "central discovered artifact must be a regular file"
                    )
                paths.append(directory / entry.name)
    except Gate3EconomicCentralEvidenceError:
        raise
    except OSError as error:
        raise Gate3EconomicCentralEvidenceError("central discovery failed safely") from error
    try:
        after = directory.lstat()
    except OSError as error:
        raise Gate3EconomicCentralEvidenceError(
            "central discovery directory changed after scan"
        ) from error
    fingerprint = lambda item: (  # noqa: E731
        item.st_dev,
        item.st_ino,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if fingerprint(before) != fingerprint(after):
        raise Gate3EconomicCentralEvidenceError("central discovery directory changed during scan")
    return tuple(sorted(paths))


def _require_artifact_upstream(
    value: object,
    *,
    calibration: Gate3CalibrationManifest,
    validity: Gate3ValidityStageManifest,
    addendum: EconomicDecisionAddendum,
) -> None:
    if (
        getattr(value, "base_protocol_id", None) != calibration.protocol.protocol_id
        or getattr(value, "base_protocol_content_sha256", None)
        != calibration.protocol.content_sha256
        or getattr(value, "decision_addendum_id", None) != addendum.addendum_id
        or getattr(value, "decision_addendum_content_sha256", None) != addendum.content_sha256
        or getattr(value, "roots", None) != calibration.roots
        or getattr(value, "calibration_manifest_id", None) != calibration.manifest_id
        or getattr(value, "calibration_manifest_content_sha256", None) != calibration.content_sha256
        or getattr(value, "validity_manifest_id", None) != validity.manifest_id
        or getattr(value, "validity_manifest_content_sha256", None) != validity.content_sha256
        or getattr(value, "validity_evidence_id", None) != validity.validity_evidence.evidence_id
        or getattr(value, "validity_evidence_content_sha256", None)
        != validity.validity_evidence.content_sha256
    ):
        raise Gate3EconomicCentralEvidenceError(
            "central artifact differs from exact upstream bindings"
        )
    if isinstance(value, Gate3CentralCellFailureReceipt):
        freeze = _freeze_for(calibration, value.corpus_id)
        if (
            value.baseline_freeze_id != freeze.freeze_id
            or value.baseline_freeze_content_sha256 != freeze.content_sha256
        ):
            raise Gate3EconomicCentralEvidenceError(
                "central failure receipt differs from the exact baseline freeze"
            )


def _failure_filename(receipt: Gate3CentralCellFailureReceipt) -> str:
    return f"{_FAILURE_PREFIX}{receipt.execution_position:02d}-{receipt.attempt_number:03d}.json"


def publish_gate3_central_cell_failure_receipt(
    output_directory: Path,
    receipt: Gate3CentralCellFailureReceipt,
    *,
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> Path:
    """Immutably append one authenticated failed-attempt receipt."""

    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    try:
        strict = Gate3CentralCellFailureReceipt.model_validate(
            receipt.model_dump(mode="python", round_trip=True), strict=True
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicCentralEvidenceError("central failure receipt is malformed") from error
    _require_artifact_upstream(
        strict, calibration=calibration, validity=validity, addendum=addendum
    )
    existing = discover_gate3_central_cell_failure_receipts(
        output_directory,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    same_attempt = tuple(
        item
        for item in existing
        if (item[1].execution_position, item[1].attempt_number)
        == (strict.execution_position, strict.attempt_number)
    )
    if same_attempt:
        if len(same_attempt) != 1 or same_attempt[0][1] != strict:
            raise Gate3EconomicCentralEvidenceError(
                "central failure receipt publication found a competitor"
            )
        return same_attempt[0][0]
    chain = tuple(
        item for _path, item in existing if item.execution_position == strict.execution_position
    )
    tail = chain[-1] if chain else None
    if strict.attempt_number != len(chain) + 1 or (
        strict.previous_failure_receipt_id,
        strict.previous_failure_receipt_content_sha256,
    ) != (
        tail.failure_receipt_id if tail is not None else None,
        tail.content_sha256 if tail is not None else None,
    ):
        raise Gate3EconomicCentralEvidenceError(
            "central failure receipt is not the exact append-only tail"
        )
    if len(canonical_pretty_json_bytes(strict)) > _MAX_FAILURE_ARTIFACT_BYTES:
        raise Gate3EconomicCentralEvidenceError(
            "central failure receipt exceeds its dedicated byte bound"
        )
    return _publish(
        Path(output_directory) / _failure_filename(strict),
        strict,
        label="M11 economic central-cell failure receipt",
    )


def discover_gate3_central_cell_failure_receipts(
    output_directory: Path,
    *,
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> tuple[tuple[Path, Gate3CentralCellFailureReceipt], ...]:
    """Discover and verify every append-only failed-attempt chain."""

    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    paths = _discover_paths(
        output_directory,
        prefix=_FAILURE_PREFIX,
        pattern=re.compile(rf"{re.escape(_FAILURE_PREFIX)}[0-3][0-9]-[0-9]{{3}}\.json"),
    )

    def fingerprints(candidates: tuple[Path, ...]) -> dict[str, tuple[int, int, int, int, int]]:
        result: dict[str, tuple[int, int, int, int, int]] = {}
        for candidate in candidates:
            try:
                metadata = candidate.lstat()
            except OSError as error:
                raise Gate3EconomicCentralEvidenceError(
                    "central failure receipt changed during discovery"
                ) from error
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > _MAX_FAILURE_ARTIFACT_BYTES
            ):
                raise Gate3EconomicCentralEvidenceError(
                    "central failure receipt changed during discovery"
                )
            result[candidate.name] = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
        return result

    before_fingerprints = fingerprints(paths)
    found: list[tuple[Path, Gate3CentralCellFailureReceipt]] = []
    for path in paths:
        value = cast(
            Gate3CentralCellFailureReceipt,
            _parse_artifact(path, Gate3CentralCellFailureReceipt),
        )
        _require_artifact_upstream(
            value, calibration=calibration, validity=validity, addendum=addendum
        )
        if path.name != _failure_filename(value):
            raise Gate3EconomicCentralEvidenceError(
                "central failure receipt filename binding differs"
            )
        found.append((path, value))
    found.sort(key=lambda item: (item[1].execution_position, item[1].attempt_number))
    keys = tuple((item.execution_position, item.attempt_number) for _, item in found)
    if len(set(keys)) != len(keys):
        raise Gate3EconomicCentralEvidenceError(
            "central failure receipt discovery found competitors"
        )
    for position in sorted({item.execution_position for _, item in found}):
        chain = tuple(item for _, item in found if item.execution_position == position)
        if tuple(item.attempt_number for item in chain) != tuple(range(1, len(chain) + 1)):
            raise Gate3EconomicCentralEvidenceError(
                "central failure receipt chain is not contiguous"
            )
        for index, item in enumerate(chain):
            prior = chain[index - 1] if index else None
            if (
                (item.previous_failure_receipt_id, item.previous_failure_receipt_content_sha256)
                != (
                    prior.failure_receipt_id if prior is not None else None,
                    prior.content_sha256 if prior is not None else None,
                )
                or item.corpus_id != chain[0].corpus_id
                or item.stream_id != chain[0].stream_id
                or item.baseline_freeze_id != chain[0].baseline_freeze_id
                or item.baseline_freeze_content_sha256 != chain[0].baseline_freeze_content_sha256
                or item.completed_checkpoint_count != chain[0].completed_checkpoint_count
                or item.completed_checkpoint_prefix_content_sha256
                != chain[0].completed_checkpoint_prefix_content_sha256
            ):
                raise Gate3EconomicCentralEvidenceError(
                    "central failure receipt retry chain does not reconcile"
                )
    after_paths = _discover_paths(
        output_directory,
        prefix=_FAILURE_PREFIX,
        pattern=re.compile(rf"{re.escape(_FAILURE_PREFIX)}[0-3][0-9]-[0-9]{{3}}\.json"),
    )
    if after_paths != paths or fingerprints(after_paths) != before_fingerprints:
        raise Gate3EconomicCentralEvidenceError(
            "central failure receipt set changed during discovery"
        )
    return tuple(found)


def publish_gate3_central_cell_checkpoint(
    output_directory: Path,
    checkpoint: Gate3CentralCellCheckpoint,
    *,
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> Path:
    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    try:
        strict = Gate3CentralCellCheckpoint.model_validate(
            checkpoint.model_dump(mode="python", round_trip=True), strict=True
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise Gate3EconomicCentralEvidenceError("central checkpoint is malformed") from error
    _require_artifact_upstream(
        strict, calibration=calibration, validity=validity, addendum=addendum
    )
    filename = (
        f"{_CHECKPOINT_PREFIX}{strict.execution_position:02d}-"
        f"{strict.content_sha256.removeprefix('sha256:')}.json"
    )
    return _publish(
        Path(output_directory) / filename,
        strict,
        label="M11 economic central-cell checkpoint",
    )


def load_gate3_central_cell_checkpoint(
    path: Path,
    *,
    expected_checkpoint_id: str,
    expected_content_sha256: str,
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> Gate3CentralCellCheckpoint:
    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    candidate = Path(path)
    match = re.fullmatch(
        rf"{re.escape(_CHECKPOINT_PREFIX)}([0-3][0-9])-([0-9a-f]{{64}})\.json",
        candidate.name,
    )
    if match is None or expected_content_sha256 != f"sha256:{match.group(2)}":
        raise Gate3EconomicCentralEvidenceError("central checkpoint path binding differs")
    value = cast(Gate3CentralCellCheckpoint, _parse_artifact(candidate, Gate3CentralCellCheckpoint))
    _require_artifact_upstream(value, calibration=calibration, validity=validity, addendum=addendum)
    if (
        value.execution_position != int(match.group(1))
        or value.checkpoint_id != expected_checkpoint_id
        or value.content_sha256 != expected_content_sha256
    ):
        raise Gate3EconomicCentralEvidenceError("central checkpoint expected binding differs")
    return value


def discover_gate3_central_cell_checkpoints(
    output_directory: Path,
    *,
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> tuple[tuple[Path, Gate3CentralCellCheckpoint], ...]:
    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    paths = _discover_paths(
        output_directory,
        prefix=_CHECKPOINT_PREFIX,
        pattern=re.compile(rf"{re.escape(_CHECKPOINT_PREFIX)}[0-3][0-9]-[0-9a-f]{{64}}\.json"),
    )
    found: list[tuple[Path, Gate3CentralCellCheckpoint]] = []
    for path in paths:
        digest = path.stem.rsplit("-", 1)[-1]
        value = cast(Gate3CentralCellCheckpoint, _parse_artifact(path, Gate3CentralCellCheckpoint))
        loaded = load_gate3_central_cell_checkpoint(
            path,
            expected_checkpoint_id=value.checkpoint_id,
            expected_content_sha256=f"sha256:{digest}",
            calibration_manifest=calibration,
            validity_manifest=validity,
            decision_addendum=addendum,
        )
        found.append((path, loaded))
    positions = tuple(item.execution_position for _, item in found)
    if len(set(positions)) != len(positions):
        raise Gate3EconomicCentralEvidenceError("central discovery found competing checkpoints")
    return tuple(sorted(found, key=lambda item: item[1].execution_position))


def _summary_filename(summary: Gate3EconomicSegmentSummary) -> str:
    return (
        f"{_SUMMARY_PREFIX}{summary.corpus_id}-"
        f"{summary.content_sha256.removeprefix('sha256:')}.json"
    )


def publish_gate3_economic_segment_summary(
    output_directory: Path,
    summary: Gate3EconomicSegmentSummary,
    *,
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> Path:
    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    strict = Gate3EconomicSegmentSummary.model_validate(
        summary.model_dump(mode="python", round_trip=True), strict=True
    )
    _require_artifact_upstream(
        strict, calibration=calibration, validity=validity, addendum=addendum
    )
    existing = discover_gate3_economic_segment_summaries(
        output_directory,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    same_corpus = tuple(item for item in existing if item[1].corpus_id == strict.corpus_id)
    if same_corpus:
        if len(same_corpus) != 1 or same_corpus[0][1] != strict:
            raise Gate3EconomicCentralEvidenceError(
                "central summary publication found a competitor"
            )
        return same_corpus[0][0]
    return _publish(
        Path(output_directory) / _summary_filename(strict),
        strict,
        label="M11 economic central segment summary",
    )


def discover_gate3_economic_segment_summaries(
    output_directory: Path,
    *,
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> tuple[tuple[Path, Gate3EconomicSegmentSummary], ...]:
    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    paths = _discover_paths(
        output_directory,
        prefix=_SUMMARY_PREFIX,
        pattern=re.compile(
            rf"{re.escape(_SUMMARY_PREFIX)}(loco-2dics|lectra-m3-m4)-[0-9a-f]{{64}}\.json"
        ),
    )
    found: list[tuple[Path, Gate3EconomicSegmentSummary]] = []
    for path in paths:
        value = cast(
            Gate3EconomicSegmentSummary, _parse_artifact(path, Gate3EconomicSegmentSummary)
        )
        _require_artifact_upstream(
            value, calibration=calibration, validity=validity, addendum=addendum
        )
        if path.name != _summary_filename(value):
            raise Gate3EconomicCentralEvidenceError("central summary filename binding differs")
        found.append((path, value))
    corpora = tuple(item.corpus_id for _, item in found)
    if len(set(corpora)) != len(corpora):
        raise Gate3EconomicCentralEvidenceError("central discovery found competing summaries")
    return tuple(sorted(found, key=lambda item: _CORPUS_ORDER.index(item[1].corpus_id)))


def publish_gate3_economic_central_manifest(
    output_directory: Path,
    manifest: Gate3EconomicCentralManifest,
    *,
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> Path:
    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    strict = Gate3EconomicCentralManifest.model_validate(
        manifest.model_dump(mode="python", round_trip=True), strict=True
    )
    _require_artifact_upstream(
        strict, calibration=calibration, validity=validity, addendum=addendum
    )
    existing = discover_gate3_economic_central_manifest(
        output_directory,
        calibration_manifest=calibration,
        validity_manifest=validity,
        decision_addendum=addendum,
    )
    if existing is not None:
        if existing[1] != strict:
            raise Gate3EconomicCentralEvidenceError(
                "central manifest publication found a competing manifest"
            )
        return existing[0]
    filename = f"{_MANIFEST_PREFIX}{strict.content_sha256.removeprefix('sha256:')}.json"
    return _publish(
        Path(output_directory) / filename,
        strict,
        label="M11 economic central manifest",
    )


def discover_gate3_economic_central_manifest(
    output_directory: Path,
    *,
    calibration_manifest: Gate3CalibrationManifest,
    validity_manifest: Gate3ValidityStageManifest,
    decision_addendum: EconomicDecisionAddendum,
) -> tuple[Path, Gate3EconomicCentralManifest] | None:
    calibration, validity, addendum = _strict_upstream(
        calibration_manifest=calibration_manifest,
        validity_manifest=validity_manifest,
        decision_addendum=decision_addendum,
    )
    paths = _discover_paths(
        output_directory,
        prefix=_MANIFEST_PREFIX,
        pattern=re.compile(rf"{re.escape(_MANIFEST_PREFIX)}[0-9a-f]{{64}}\.json"),
    )
    if len(paths) > 1:
        raise Gate3EconomicCentralEvidenceError("central discovery found competing manifests")
    if not paths:
        return None
    path = paths[0]
    value = cast(Gate3EconomicCentralManifest, _parse_artifact(path, Gate3EconomicCentralManifest))
    _require_artifact_upstream(value, calibration=calibration, validity=validity, addendum=addendum)
    if path.name != f"{_MANIFEST_PREFIX}{value.content_sha256.removeprefix('sha256:')}.json":
        raise Gate3EconomicCentralEvidenceError("central manifest filename binding differs")
    return path, value


__all__ = [
    "CentralGlobalDisposition",
    "CentralManifestStatus",
    "CentralSegmentNextAction",
    "Gate3CentralCellCheckpoint",
    "Gate3CentralCellFailureReceipt",
    "Gate3EconomicCentralEvidenceError",
    "Gate3EconomicCentralManifest",
    "Gate3EconomicSegmentSummary",
    "build_gate3_central_cell_checkpoint",
    "build_gate3_central_cell_failure_receipt",
    "build_gate3_economic_central_manifest",
    "build_gate3_economic_segment_summary",
    "discover_gate3_central_cell_checkpoints",
    "discover_gate3_central_cell_failure_receipts",
    "discover_gate3_economic_central_manifest",
    "discover_gate3_economic_segment_summaries",
    "load_gate3_central_cell_checkpoint",
    "publish_gate3_central_cell_checkpoint",
    "publish_gate3_central_cell_failure_receipt",
    "publish_gate3_economic_central_manifest",
    "publish_gate3_economic_segment_summary",
]
