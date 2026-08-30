"""Fail-closed Gate 1 evaluation for the M11 optimistic-ceiling test."""

from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from yieldforge.experiments.contracts import FrozenExperimentModel, semantic_sha256
from yieldforge.realistic_falsification.bounds import (
    Gate1BaselineSelectionEvidence,
    Gate1EvidenceError,
    Gate1SourceContext,
    Gate1StreamCell,
    Gate1TinyAudit,
    _open_official_gate1_session,
    audit_tiny_gate1_bounds,
    build_gate1_demand_record,
    build_gate1_feasible_opening,
    build_gate1_feasible_policy_cost,
    build_gate1_stream_cell_from_evidence,
    build_preregistered_gate1_tiny_problem,
    calculate_relaxed_lower_bound,
    verify_gate1_tiny_audit,
)
from yieldforge.realistic_falsification.contracts import (
    M11EvidenceState,
    M11ExperimentContract,
    M11InvalidReason,
    M11InvalidReasonCategory,
    M11InvalidReasonCode,
    M11VerdictResult,
    build_m11_verdict,
)
from yieldforge.realistic_falsification.pack import M11Regime, M11Stream
from yieldforge.realistic_falsification.statistics import (
    GATE1_BOOTSTRAP_RESAMPLES,
    GATE1_BOOTSTRAP_SEED,
    GATE1_CONFIDENCE_LEVEL,
    GATE1_SAVINGS_THRESHOLD_PERCENT,
    GATE1_UNKNOWN_THRESHOLD_POINTS,
    Gate1BootstrapSummary,
    Gate1CellMetricPair,
    _bootstrap_gate1_statistics,
    calculate_gate1_cell_metrics,
)

_CONTRACT_ID = "yfm11c-e956019aeef85350f2ffa9d3"
_CONTRACT_SHA256 = "sha256:e956019aeef85350f2ffa9d351ab15539d1b23137d7566118f73c5f29882143b"
_POPULATION_ID = "yfm11pop-a26084179d2e8f776630f8ac"
_POPULATION_SHA256 = "sha256:a26084179d2e8f776630f8ac272d5651069500a5df996869d20df9893ca0bc56"
_SOURCE_MANIFEST_ID = "yfm11sm-54426d56dcccc07b667da56f"
_SOURCE_MANIFEST_SHA256 = "sha256:54426d56dcccc07b667da56fd1106f2e463baf3aaf57358a50f47e76fc073605"
_CORPUS_ORDER = ("lectra-m3-m4", "loco-2dics")
_REGIME_ORDER = ("recurrent", "mixed", "high_mix", "regime_shift")
_OFFICIAL_CONFIRMATION_BINDING_SHA256 = (
    "639dc66fe1e3feedc9f7d68563f72398c219e708741caa19ac25873b6b894977"
)

Gate1EvaluationStatus = Literal[
    "invalid_test",
    "falsified_by_optimistic_ceiling",
    "gate_1_survived",
]


class Gate1EvaluationError(ValueError):
    """Gate 1 evaluation cannot safely authenticate or classify the submitted evidence."""


class Gate1EvaluationConfig(FrozenExperimentModel):
    """The complete frozen statistical and aggregation configuration for Gate 1."""

    schema_version: Literal["yieldforge.m11-gate1-evaluation-config.v1"] = (
        "yieldforge.m11-gate1-evaluation-config.v1"
    )
    config_id: StrictStr = Field(pattern=r"^yfm11g1c-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    corpus_order: tuple[Literal["lectra-m3-m4"], Literal["loco-2dics"]] = _CORPUS_ORDER
    confirmation_cells_per_corpus: Literal[20] = 20
    streams_per_regime: Literal[5] = 5
    bootstrap_generator: Literal["numpy.Generator(PCG64(0))"] = "numpy.Generator(PCG64(0))"
    bootstrap_resamples: Literal[10000] = GATE1_BOOTSTRAP_RESAMPLES
    bootstrap_seed: Literal[0] = GATE1_BOOTSTRAP_SEED
    quantile_method: Literal["linear_type_7"] = "linear_type_7"
    confidence_level: Literal[0.95] = GATE1_CONFIDENCE_LEVEL
    resampling_unit: Literal["complete_paired_stream_s_u_vector"] = (
        "complete_paired_stream_s_u_vector"
    )
    aggregation: Literal["equal_stream_within_corpus_then_equal_corpus_pool"] = (
        "equal_stream_within_corpus_then_equal_corpus_pool"
    )
    savings_threshold_percent: Literal[1.5] = GATE1_SAVINGS_THRESHOLD_PERCENT
    unknown_threshold_points: Literal[0.5] = GATE1_UNKNOWN_THRESHOLD_POINTS
    falsification_boundary: Literal["joint_type_7_q975_strictly_below_zero"] = (
        "joint_type_7_q975_strictly_below_zero"
    )

    @model_validator(mode="after")
    def require_identity(self) -> Self:
        digest = semantic_sha256(self, excluded_fields={"config_id", "content_sha256"})
        if self.config_id != f"yfm11g1c-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 1 evaluation config identity does not match semantic content")
        return self


class Gate1AuditReceipt(FrozenExperimentModel):
    """Complete content-addressed evidence envelope for a valid Gate 1 result."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m11-gate1-audit-receipt.v2"] = (
        "yieldforge.m11-gate1-audit-receipt.v2"
    )
    receipt_id: StrictStr = Field(pattern=r"^yfm11g1a-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    confirmation_cells: tuple[Gate1StreamCell, ...] = Field(min_length=40, max_length=40)
    confirmation_regimes: tuple[M11Regime, ...] = Field(min_length=40, max_length=40)
    baseline_selections: tuple[Gate1BaselineSelectionEvidence, ...] = Field(
        min_length=2,
        max_length=2,
    )
    tiny_audit: Gate1TinyAudit
    roots_authenticated: Literal[True] = True
    exact_confirmation_census: Literal[True] = True
    regime_census: Literal[True] = True
    feasibility_revalidated: Literal[True] = True
    lower_bounds_revalidated: Literal[True] = True
    accounting_reconciled: Literal[True] = True
    selection_revalidated: Literal[True] = True
    tiny_audit_revalidated: Literal[True] = True

    @model_validator(mode="after")
    def require_complete_unique_receipt(self) -> Self:
        canonical_cells = tuple(
            Gate1StreamCell.model_validate(
                item.model_dump(mode="python", round_trip=True), strict=True
            )
            for item in self.confirmation_cells
        )
        canonical_selections = tuple(
            Gate1BaselineSelectionEvidence.model_validate(
                item.model_dump(mode="python", round_trip=True), strict=True
            )
            for item in self.baseline_selections
        )
        canonical_tiny = verify_gate1_tiny_audit(self.tiny_audit)
        if (
            canonical_cells != self.confirmation_cells
            or canonical_selections != self.baseline_selections
            or canonical_tiny != self.tiny_audit
        ):
            raise ValueError("Gate 1 receipt evidence differs after strict revalidation")
        if len(set(self.cell_ids)) != 40 or len(set(self.cell_content_sha256s)) != 40:
            raise ValueError("Gate 1 receipt cell IDs must be unique")
        if len(set(self.selection_ids)) != 2 or len(set(self.selection_content_sha256s)) != 2:
            raise ValueError("Gate 1 receipt selection IDs must be unique")
        if tuple(item.corpus_id for item in self.baseline_selections) != _CORPUS_ORDER:
            raise ValueError("Gate 1 receipt selections differ from the frozen corpus order")
        if any(
            item.contract_id != _CONTRACT_ID
            or item.contract_content_sha256 != _CONTRACT_SHA256
            or item.population_id != _POPULATION_ID
            or item.population_content_sha256 != _POPULATION_SHA256
            for item in self.baseline_selections
        ):
            raise ValueError("Gate 1 receipt selection roots differ from the official bundle")
        selection_by_corpus = {item.corpus_id: item for item in self.baseline_selections}
        if any(
            (selection := selection_by_corpus.get(cell.corpus_id)) is None
            or cell.baseline.calibration_selection_id != selection.selection_id
            or cell.baseline.registered_policy_id != selection.selected_policy_id
            for cell in self.confirmation_cells
        ):
            raise ValueError("Gate 1 receipt cell selection and policy binding differs")
        binding_digest = semantic_sha256(
            {
                "bindings": [
                    [cell.stream_id, cell.corpus_id, regime]
                    for cell, regime in zip(
                        self.confirmation_cells,
                        self.confirmation_regimes,
                        strict=True,
                    )
                ]
            }
        )
        if binding_digest != _OFFICIAL_CONFIRMATION_BINDING_SHA256:
            raise ValueError("Gate 1 receipt stream/corpus/regime binding differs from official")
        digest = semantic_sha256(self, excluded_fields={"receipt_id", "content_sha256"})
        if self.receipt_id != f"yfm11g1a-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 1 audit receipt identity does not match semantic content")
        return self

    @property
    def cell_ids(self) -> tuple[str, ...]:
        return tuple(item.cell_id for item in self.confirmation_cells)

    @property
    def cell_content_sha256s(self) -> tuple[str, ...]:
        return tuple(item.content_sha256 for item in self.confirmation_cells)

    @property
    def selection_ids(self) -> tuple[str, ...]:
        return tuple(item.selection_id for item in self.baseline_selections)

    @property
    def selection_content_sha256s(self) -> tuple[str, ...]:
        return tuple(item.content_sha256 for item in self.baseline_selections)

    @property
    def tiny_audit_id(self) -> str:
        return self.tiny_audit.audit_id

    @property
    def tiny_audit_content_sha256(self) -> str:
        return self.tiny_audit.content_sha256


class Gate1EvaluationResult(FrozenExperimentModel):
    """A closed Gate 1 branch result bound to the exact official evidence roots."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m11-gate1-evaluation-result.v2"] = (
        "yieldforge.m11-gate1-evaluation-result.v2"
    )
    result_id: StrictStr = Field(pattern=r"^yfm11g1r-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Gate1EvaluationStatus
    contract_id: Literal["yfm11c-e956019aeef85350f2ffa9d3"] = _CONTRACT_ID
    contract_content_sha256: Literal[
        "sha256:e956019aeef85350f2ffa9d351ab15539d1b23137d7566118f73c5f29882143b"
    ] = _CONTRACT_SHA256
    population_id: Literal["yfm11pop-a26084179d2e8f776630f8ac"] = _POPULATION_ID
    population_content_sha256: Literal[
        "sha256:a26084179d2e8f776630f8ac272d5651069500a5df996869d20df9893ca0bc56"
    ] = _POPULATION_SHA256
    source_manifest_id: Literal["yfm11sm-54426d56dcccc07b667da56f"] = _SOURCE_MANIFEST_ID
    source_manifest_content_sha256: Literal[
        "sha256:54426d56dcccc07b667da56fd1106f2e463baf3aaf57358a50f47e76fc073605"
    ] = _SOURCE_MANIFEST_SHA256
    contract: M11ExperimentContract
    observed_cell_ids: tuple[StrictStr, ...]
    config: Gate1EvaluationConfig
    audit_receipt: Gate1AuditReceipt | None
    statistics: Gate1BootstrapSummary | None
    verdict: M11VerdictResult | None
    repair_count: StrictInt
    terminal: StrictBool
    opens_gate_2: StrictBool
    retention_authorized: Literal[False] = False
    productization_authorized: Literal[False] = False

    @model_validator(mode="after")
    def require_closed_branch_and_identity(self) -> Self:
        canonical_contract = M11ExperimentContract.model_validate(
            self.contract.model_dump(mode="python", round_trip=True), strict=True
        )
        if (
            canonical_contract != self.contract
            or self.contract.contract_id != self.contract_id
            or self.contract.content_sha256 != self.contract_content_sha256
        ):
            raise ValueError("Gate 1 result contract differs from the official root binding")
        if self.config != _build_config():
            raise ValueError("Gate 1 result config differs from the registered configuration")
        if self.repair_count not in (0, 1):
            raise ValueError("Gate 1 repair count must be 0 or 1")
        if self.status == "invalid_test":
            if not (
                self.terminal
                and not self.opens_gate_2
                and self.audit_receipt is None
                and self.statistics is None
                and self.verdict is not None
                and self.verdict.evidence_state is M11EvidenceState.INVALID_TEST
                and self.verdict.repair_count == self.repair_count
            ):
                raise ValueError("Gate 1 invalid result branch is internally inconsistent")
            expected_verdict = build_m11_verdict(
                contract=self.contract,
                evidence_state=M11EvidenceState.INVALID_TEST,
                repair_count=self.repair_count,
                invalid_reason=self.verdict.invalid_reason,
            )
            if self.verdict != expected_verdict:
                raise ValueError("Gate 1 invalid verdict differs from its exact evidence")
        else:
            if self.audit_receipt is None or self.statistics is None:
                raise ValueError("Gate 1 valid result requires complete persisted evidence")
            canonical_receipt = Gate1AuditReceipt.model_validate(
                self.audit_receipt.model_dump(mode="python", round_trip=True), strict=True
            )
            if canonical_receipt != self.audit_receipt:
                raise ValueError("Gate 1 result evidence differs after strict revalidation")
            expected_stream_ids = tuple(
                stream_id
                for corpus in self.contract.corpora
                for stream_id in corpus.confirmation_stream_ids
            )
            if (
                tuple(item.stream_id for item in self.audit_receipt.confirmation_cells)
                != expected_stream_ids
                or self.observed_cell_ids != self.audit_receipt.cell_ids
            ):
                raise ValueError("Gate 1 result evidence differs from contract confirmation order")
            metrics = tuple(
                _cell_metric_pair(item) for item in self.audit_receipt.confirmation_cells
            )
            recomputed = _bootstrap_gate1_statistics(metrics[:20], metrics[20:])
            if self.statistics != recomputed:
                raise ValueError("Gate 1 statistics differ from recomputed cell evidence")
            falsifies = recomputed.falsifies_optimistic_ceiling
            expected_status: Gate1EvaluationStatus = (
                "falsified_by_optimistic_ceiling" if falsifies else "gate_1_survived"
            )
            expected_verdict = (
                build_m11_verdict(
                    contract=self.contract,
                    evidence_state=M11EvidenceState.FALSIFIED_BY_OPTIMISTIC_CEILING,
                    repair_count=self.repair_count,
                )
                if falsifies
                else None
            )
            if (
                self.status != expected_status
                or self.terminal is not falsifies
                or self.opens_gate_2 is falsifies
                or self.verdict != expected_verdict
            ):
                raise ValueError("Gate 1 branch differs from recomputed cell evidence")
        digest = semantic_sha256(self, excluded_fields={"result_id", "content_sha256"})
        if self.result_id != f"yfm11g1r-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 1 evaluation result identity does not match semantic content")
        return self


def _build_config() -> Gate1EvaluationConfig:
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-evaluation-config.v1",
        "corpus_order": list(_CORPUS_ORDER),
        "confirmation_cells_per_corpus": 20,
        "streams_per_regime": 5,
        "bootstrap_generator": "numpy.Generator(PCG64(0))",
        "bootstrap_resamples": GATE1_BOOTSTRAP_RESAMPLES,
        "bootstrap_seed": GATE1_BOOTSTRAP_SEED,
        "quantile_method": "linear_type_7",
        "confidence_level": GATE1_CONFIDENCE_LEVEL,
        "resampling_unit": "complete_paired_stream_s_u_vector",
        "aggregation": "equal_stream_within_corpus_then_equal_corpus_pool",
        "savings_threshold_percent": GATE1_SAVINGS_THRESHOLD_PERCENT,
        "unknown_threshold_points": GATE1_UNKNOWN_THRESHOLD_POINTS,
        "falsification_boundary": "joint_type_7_q975_strictly_below_zero",
    }
    digest = semantic_sha256(semantic)
    return Gate1EvaluationConfig(
        config_id=f"yfm11g1c-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
    )


def build_gate1_audit_receipt(
    *,
    confirmation_cells: tuple[Gate1StreamCell, ...],
    confirmation_regimes: tuple[M11Regime, ...],
    baseline_selections: tuple[Gate1BaselineSelectionEvidence, ...],
    tiny_audit: Gate1TinyAudit,
) -> Gate1AuditReceipt:
    """Seal the successful prerequisite audits into one immutable receipt."""

    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-audit-receipt.v2",
        "confirmation_cells": [item.model_dump(mode="json") for item in confirmation_cells],
        "confirmation_regimes": list(confirmation_regimes),
        "baseline_selections": [item.model_dump(mode="json") for item in baseline_selections],
        "tiny_audit": tiny_audit.model_dump(mode="json"),
        "roots_authenticated": True,
        "exact_confirmation_census": True,
        "regime_census": True,
        "feasibility_revalidated": True,
        "lower_bounds_revalidated": True,
        "accounting_reconciled": True,
        "selection_revalidated": True,
        "tiny_audit_revalidated": True,
    }
    digest = semantic_sha256(semantic)
    return Gate1AuditReceipt(
        receipt_id=f"yfm11g1a-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        confirmation_cells=confirmation_cells,
        confirmation_regimes=confirmation_regimes,
        baseline_selections=baseline_selections,
        tiny_audit=tiny_audit,
    )


def _result_semantic(
    *,
    status: Gate1EvaluationStatus,
    observed_cell_ids: tuple[str, ...],
    config: Gate1EvaluationConfig,
    audit_receipt: Gate1AuditReceipt | None,
    statistics: Gate1BootstrapSummary | None,
    verdict: M11VerdictResult | None,
    repair_count: int,
    terminal: bool,
    opens_gate_2: bool,
    contract: M11ExperimentContract,
) -> dict[str, object]:
    return {
        "schema_version": "yieldforge.m11-gate1-evaluation-result.v2",
        "status": status,
        "contract_id": _CONTRACT_ID,
        "contract_content_sha256": _CONTRACT_SHA256,
        "population_id": _POPULATION_ID,
        "population_content_sha256": _POPULATION_SHA256,
        "source_manifest_id": _SOURCE_MANIFEST_ID,
        "source_manifest_content_sha256": _SOURCE_MANIFEST_SHA256,
        "contract": contract,
        "observed_cell_ids": observed_cell_ids,
        "config": config,
        "audit_receipt": audit_receipt,
        "statistics": statistics,
        "verdict": verdict,
        "repair_count": repair_count,
        "terminal": terminal,
        "opens_gate_2": opens_gate_2,
        "retention_authorized": False,
        "productization_authorized": False,
    }


def _result_digest(semantic: dict[str, object]) -> str:
    hashable = dict(semantic)
    for field in ("contract", "config", "audit_receipt", "statistics", "verdict"):
        value = hashable[field]
        hashable[field] = None if value is None else value.model_dump(mode="json")
    return semantic_sha256(hashable)


def _build_gate1_valid_result(
    *,
    context: Gate1SourceContext,
    cells: tuple[Gate1StreamCell, ...],
    baseline_selections: tuple[Gate1BaselineSelectionEvidence, ...],
    tiny_audit: Gate1TinyAudit,
    repair_count: Literal[0, 1],
) -> Gate1EvaluationResult:
    """Derive every valid-result field from complete persisted Gate 1 evidence."""

    _require_official_root_values(context)
    canonical_cells = tuple(
        Gate1StreamCell.model_validate(item.model_dump(mode="python", round_trip=True), strict=True)
        for item in tuple(cells)
    )
    canonical_selections = tuple(
        Gate1BaselineSelectionEvidence.model_validate(
            item.model_dump(mode="python", round_trip=True), strict=True
        )
        for item in tuple(baseline_selections)
    )
    canonical_tiny = verify_gate1_tiny_audit(tiny_audit)
    streams = _require_confirmation_cell_census(context, canonical_cells)
    metrics = tuple(_cell_metric_pair(item) for item in canonical_cells)
    statistics = _bootstrap_gate1_statistics(metrics[:20], metrics[20:])
    audit_receipt = build_gate1_audit_receipt(
        confirmation_cells=canonical_cells,
        confirmation_regimes=tuple(item.regime for item in streams),
        baseline_selections=canonical_selections,
        tiny_audit=canonical_tiny,
    )
    status: Gate1EvaluationStatus = (
        "falsified_by_optimistic_ceiling"
        if statistics.falsifies_optimistic_ceiling
        else "gate_1_survived"
    )
    verdict = (
        build_m11_verdict(
            contract=context.bundle.contract,
            evidence_state=M11EvidenceState.FALSIFIED_BY_OPTIMISTIC_CEILING,
            repair_count=repair_count,
        )
        if statistics.falsifies_optimistic_ceiling
        else None
    )
    terminal = statistics.falsifies_optimistic_ceiling
    semantic = _result_semantic(
        status=status,
        observed_cell_ids=audit_receipt.cell_ids,
        config=_build_config(),
        audit_receipt=audit_receipt,
        statistics=statistics,
        verdict=verdict,
        repair_count=repair_count,
        terminal=terminal,
        opens_gate_2=not terminal,
        contract=context.bundle.contract,
    )
    digest = _result_digest(semantic)
    return Gate1EvaluationResult(
        result_id=f"yfm11g1r-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _build_gate1_invalid_result(
    *,
    context: Gate1SourceContext,
    observed_cell_ids: tuple[str, ...],
    category: M11InvalidReasonCategory,
    reason_code: M11InvalidReasonCode,
    repair_count: Literal[0, 1],
) -> Gate1EvaluationResult:
    """Build an explicit invalid-test branch with no numeric bootstrap."""

    _require_official_root_values(context)
    invalid_reason = M11InvalidReason(
        category=category,
        reason_code=reason_code,
        repair_eligible=(
            category is M11InvalidReasonCategory.PREREGISTERED_INTEGRITY_OR_SOFTWARE_DEFECT
        ),
    )
    verdict = build_m11_verdict(
        contract=context.bundle.contract,
        evidence_state=M11EvidenceState.INVALID_TEST,
        repair_count=repair_count,
        invalid_reason=invalid_reason,
    )
    semantic = _result_semantic(
        status="invalid_test",
        observed_cell_ids=observed_cell_ids,
        config=_build_config(),
        audit_receipt=None,
        statistics=None,
        verdict=verdict,
        repair_count=repair_count,
        terminal=True,
        opens_gate_2=False,
        contract=context.bundle.contract,
    )
    digest = _result_digest(semantic)
    return Gate1EvaluationResult(
        result_id=f"yfm11g1r-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        **semantic,
    )


def _require_official_root_values(context: Gate1SourceContext) -> None:
    if (
        context.bundle.contract.contract_id != _CONTRACT_ID
        or context.bundle.contract.content_sha256 != _CONTRACT_SHA256
        or context.bundle.population.population_id != _POPULATION_ID
        or context.bundle.population.content_sha256 != _POPULATION_SHA256
        or context.source_manifest.source_manifest_id != _SOURCE_MANIFEST_ID
        or context.source_manifest.content_sha256 != _SOURCE_MANIFEST_SHA256
    ):
        raise ValueError("Gate 1 source roots differ from the frozen official bundle")


def _require_confirmation_cell_census(
    context: Gate1SourceContext,
    cells: tuple[Gate1StreamCell, ...],
) -> tuple[M11Stream, ...]:
    """Require exactly the forty primary confirmation streams in contract order."""

    cell_tuple = tuple(cells)
    expected_ids = tuple(
        stream_id
        for corpus in context.bundle.contract.corpora
        for stream_id in corpus.confirmation_stream_ids
    )
    actual_ids = tuple(getattr(cell, "stream_id", None) for cell in cell_tuple)
    cell_ids = tuple(getattr(cell, "cell_id", None) for cell in cell_tuple)
    if (
        len(cell_tuple) != 40
        or actual_ids != expected_ids
        or len(set(actual_ids)) != 40
        or len(set(cell_ids)) != 40
    ):
        raise ValueError("Gate 1 confirmation cell census/order is not exact")

    by_id = {item.stream_id: item for item in context.bundle.population.streams}
    try:
        streams = tuple(by_id[stream_id] for stream_id in actual_ids)
    except KeyError as error:
        raise ValueError("Gate 1 confirmation census contains an unknown stream") from error
    if any(
        stream.partition != "confirmation"
        or stream.stream_kind != "primary"
        or cell.corpus_id != stream.corpus_id
        for cell, stream in zip(cell_tuple, streams, strict=True)
    ):
        raise ValueError("Gate 1 confirmation census contains a non-primary stream")
    regime_counts = Counter((item.corpus_id, item.regime) for item in streams)
    if regime_counts != Counter(
        {(corpus_id, regime): 5 for corpus_id in _CORPUS_ORDER for regime in _REGIME_ORDER}
    ):
        raise ValueError("Gate 1 confirmation regime census must be five per regime")
    return streams


def _canonical_cost(value: object, *, label: str) -> Decimal:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError(f"Gate 1 {label} must be a finite canonical six-place float")
    decimal = Decimal(str(value))
    if decimal.as_tuple().exponent < -6:
        raise ValueError(f"Gate 1 {label} must use at most six decimal places")
    return decimal


def _cell_metric_pair(cell: Gate1StreamCell) -> Gate1CellMetricPair:
    """Extract canonical six-place B/K/L and compute an unquantized S/U pair."""

    try:
        baseline = _canonical_cost(cell.baseline_feasible_cost, label="B")
        known_only = _canonical_cost(cell.known_only_feasible_cost, label="K")
        lower_bound = _canonical_cost(cell.lower_bound.lower_bound_cost, label="L")
    except AttributeError as error:
        raise ValueError("Gate 1 cell is missing finite canonical six-place B/K/L") from error
    try:
        return calculate_gate1_cell_metrics(
            baseline=baseline,
            known_only=known_only,
            lower_bound=lower_bound,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Gate 1 requires B > 0 and L <= B/K") from error


def _observed_ids(cells: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(
        value if isinstance(value := getattr(cell, "cell_id", None), str) else repr(value)
        for cell in cells
    )


def _invalid(
    context: Gate1SourceContext,
    observed_cell_ids: tuple[str, ...],
    *,
    reason_code: M11InvalidReasonCode,
    repair_count: Literal[0, 1],
) -> Gate1EvaluationResult:
    return _build_gate1_invalid_result(
        context=context,
        observed_cell_ids=observed_cell_ids,
        category=M11InvalidReasonCategory.OTHER_VALIDITY_FAILURE,
        reason_code=reason_code,
        repair_count=repair_count,
    )


def evaluate_gate1_confirmation(
    *,
    context: Gate1SourceContext,
    cells: tuple[Gate1StreamCell, ...],
    baseline_selections: tuple[Gate1BaselineSelectionEvidence, ...],
    tiny_audit: Gate1TinyAudit | None,
    repair_count: Literal[0, 1],
) -> Gate1EvaluationResult:
    """Authenticate all Gate 1 evidence before computing the sole registered bootstrap."""

    if type(repair_count) is not int or repair_count not in (0, 1):
        raise Gate1EvaluationError("Gate 1 repair_count must be the strict integer 0 or 1")
    cell_tuple = tuple(cells)
    observed_ids = _observed_ids(cell_tuple)
    canonical_repair_count: Literal[0, 1] = repair_count
    try:
        session = _open_official_gate1_session(context.repository_root)
        canonical_context = session.context
        _require_official_root_values(canonical_context)
    except (AttributeError, OSError, TypeError, ValueError, ValidationError):
        raise Gate1EvaluationError(
            "Gate 1 cannot emit a root-bound result without readable official sources"
        ) from None
    if context != canonical_context:
        return _invalid(
            canonical_context,
            observed_ids,
            reason_code="source_lineage_failure",
            repair_count=canonical_repair_count,
        )

    try:
        streams = _require_confirmation_cell_census(canonical_context, cell_tuple)
    except (AttributeError, TypeError, ValueError):
        return _invalid(
            canonical_context,
            observed_ids,
            reason_code="accounting_reconciliation_failure",
            repair_count=canonical_repair_count,
        )

    try:
        passed_selections = tuple(
            Gate1BaselineSelectionEvidence.model_validate(
                item.model_dump(mode="python", round_trip=True), strict=True
            )
            for item in tuple(baseline_selections)
        )
        canonical_selections = session.baseline_selections
        if passed_selections != canonical_selections:
            raise Gate1EvidenceError("baseline selections differ from calibration-only evidence")
        if tiny_audit is None:
            raise Gate1EvidenceError("the preregistered tiny audit is missing")
        canonical_tiny_audit = verify_gate1_tiny_audit(tiny_audit)
    except (AttributeError, TypeError, ValueError, ValidationError):
        return _invalid(
            canonical_context,
            observed_ids,
            reason_code="control_failure",
            repair_count=canonical_repair_count,
        )

    canonical_cells: list[Gate1StreamCell] = []
    try:
        for supplied, stream in zip(cell_tuple, streams, strict=True):
            rebuilt_supplied = Gate1StreamCell.model_validate(
                supplied.model_dump(mode="python", round_trip=True), strict=True
            )
            expected = session.build_stream_cell(stream.stream_id)
            if rebuilt_supplied != expected:
                raise Gate1EvidenceError(
                    "Gate 1 cell differs from its reconstructed feasible/lower-bound evidence"
                )
            canonical_cells.append(rebuilt_supplied)
    except (AttributeError, TypeError, ValueError, ValidationError):
        return _invalid(
            canonical_context,
            observed_ids,
            reason_code="accounting_reconciliation_failure",
            repair_count=canonical_repair_count,
        )

    return _build_gate1_valid_result(
        context=canonical_context,
        cells=tuple(canonical_cells),
        baseline_selections=canonical_selections,
        tiny_audit=canonical_tiny_audit,
        repair_count=canonical_repair_count,
    )


def _build_registered_tiny_audit() -> Gate1TinyAudit:
    """Build the exact preregistered tiny certificate without supplied evidence."""

    demand = build_gate1_demand_record(
        event_position=0,
        event_id="tiny-event-0",
        geometry_reference_id="tiny-shape-0",
        geometry_sha256="a" * 64,
        source_binding_sha256="sha256:" + "b" * 64,
        source_kind="tiny",
        source_instance=None,
        material_group="material-a",
        reference_area_key="material-a",
        unit_area=Fraction(6),
        quantity=1,
        reference_area=Fraction(10),
    )
    lower_bound = calculate_relaxed_lower_bound(
        stream_id="tiny-stream",
        demands=(demand,),
    )
    opening = build_gate1_feasible_opening(
        event_position=0,
        event_id="tiny-event-0",
        payload_id="tiny-payload-0",
        material_group="material-a",
        reference_area_key="material-a",
        source_kind="tiny",
        candidate_options=(("tiny-candidate-0", "sha256:" + "c" * 64),),
        selected_candidate_id="tiny-candidate-0",
        selection_rule="exhaustive_tiny_case",
        verification_kind="exhaustive_tiny_case",
        geometry_witness_sha256="sha256:" + "d" * 64,
        known_positions_at_release=(0,),
        stock_area=Fraction(11, 10),
        reference_area=Fraction(1),
    )
    baseline, known_only = tuple(
        build_gate1_feasible_policy_cost(
            stream_id="tiny-stream",
            policy_kind=policy_kind,
            openings=(opening,),
        )
        for policy_kind in ("baseline_as_of", "known_only")
    )
    cell = build_gate1_stream_cell_from_evidence(
        stream_id="tiny-stream",
        corpus_id="tiny",
        lower_bound=lower_bound,
        baseline=baseline,
        known_only=known_only,
    )
    return audit_tiny_gate1_bounds(
        cell,
        problem=build_preregistered_gate1_tiny_problem(),
    )


def authenticate_official_gate1_evaluation(
    result: Gate1EvaluationResult,
    *,
    repository_root: Path,
) -> Gate1EvaluationResult:
    """Authoritatively reload and reconstruct one complete official Gate 1 result."""

    try:
        supplied = Gate1EvaluationResult.model_validate(
            result.model_dump(mode="python", round_trip=True),
            strict=True,
        )
    except (AttributeError, TypeError, ValueError, ValidationError) as error:
        raise Gate1EvaluationError(
            "supplied Gate 1 result is not a valid semantic artifact"
        ) from error
    if supplied.audit_receipt is None:
        raise Gate1EvaluationError(
            "official Gate 1 authentication requires a complete persisted audit receipt"
        )

    try:
        session = _open_official_gate1_session(repository_root)
        canonical_context = session.context
        _require_official_root_values(canonical_context)
        stream_ids = tuple(
            stream_id
            for corpus in canonical_context.bundle.contract.corpora
            for stream_id in corpus.confirmation_stream_ids
        )
        if len(stream_ids) != 40 or len(set(stream_ids)) != 40:
            raise Gate1EvidenceError("official Gate 1 confirmation census is not exact")
        canonical_cells = tuple(session.build_stream_cell(stream_id) for stream_id in stream_ids)
        canonical_result = _build_gate1_valid_result(
            context=canonical_context,
            cells=canonical_cells,
            baseline_selections=session.baseline_selections,
            tiny_audit=_build_registered_tiny_audit(),
            repair_count=supplied.repair_count,
        )
    except (AttributeError, OSError, TypeError, ValueError, ValidationError) as error:
        raise Gate1EvaluationError(
            "official Gate 1 sources could not reconstruct the canonical result"
        ) from error

    if supplied != canonical_result:
        raise Gate1EvaluationError(
            "supplied Gate 1 result differs from freshly reconstructed official evidence"
        )
    return canonical_result


__all__ = [
    "Gate1EvaluationError",
    "authenticate_official_gate1_evaluation",
]
