"""Fail-closed Gate 1 evaluation for the M11 optimistic-ceiling test."""

from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal
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
    build_gate1_stream_cell,
    load_official_gate1_context,
    select_gate1_baseline_policy,
    verify_gate1_tiny_audit,
)
from yieldforge.realistic_falsification.contracts import (
    M11EvidenceState,
    M11InvalidReason,
    M11InvalidReasonCategory,
    M11InvalidReasonCode,
    M11VerdictResult,
    build_m11_verdict,
)
from yieldforge.realistic_falsification.pack import M11Stream
from yieldforge.realistic_falsification.statistics import (
    GATE1_BOOTSTRAP_RESAMPLES,
    GATE1_BOOTSTRAP_SEED,
    GATE1_CONFIDENCE_LEVEL,
    GATE1_SAVINGS_THRESHOLD_PERCENT,
    GATE1_UNKNOWN_THRESHOLD_POINTS,
    Gate1BootstrapSummary,
    Gate1CellMetricPair,
    bootstrap_gate1_statistics,
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

Gate1EvaluationStatus = Literal[
    "invalid_test",
    "falsified_by_optimistic_ceiling",
    "gate_1_survived",
]


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
    """Content-addressed receipt proving every prerequisite audit completed."""

    schema_version: Literal["yieldforge.m11-gate1-audit-receipt.v1"] = (
        "yieldforge.m11-gate1-audit-receipt.v1"
    )
    receipt_id: StrictStr = Field(pattern=r"^yfm11g1a-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    cell_ids: tuple[StrictStr, ...] = Field(min_length=40, max_length=40)
    selection_ids: tuple[StrictStr, ...] = Field(min_length=2, max_length=2)
    tiny_audit_id: StrictStr = Field(pattern=r"^yfm11ta-[0-9a-f]{24}$")
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
        if len(set(self.cell_ids)) != 40:
            raise ValueError("Gate 1 receipt cell IDs must be unique")
        if len(set(self.selection_ids)) != 2:
            raise ValueError("Gate 1 receipt selection IDs must be unique")
        digest = semantic_sha256(self, excluded_fields={"receipt_id", "content_sha256"})
        if self.receipt_id != f"yfm11g1a-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 1 audit receipt identity does not match semantic content")
        return self


class Gate1EvaluationResult(FrozenExperimentModel):
    """A closed Gate 1 branch result bound to the exact official evidence roots."""

    model_config = ConfigDict(revalidate_instances="always")

    schema_version: Literal["yieldforge.m11-gate1-evaluation-result.v1"] = (
        "yieldforge.m11-gate1-evaluation-result.v1"
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
        if self.repair_count not in (0, 1):
            raise ValueError("Gate 1 repair count must be 0 or 1")
        if self.statistics is not None and tuple(
            item.stream_count for item in self.statistics.groups
        ) != (20, 20, 40):
            raise ValueError("Gate 1 statistics stream census must be 20/20/40")
        if self.verdict is not None and (
            self.verdict.repair_count != self.repair_count
            or self.verdict.contract.contract_id != self.contract_id
            or self.verdict.contract.content_sha256 != self.contract_content_sha256
        ):
            raise ValueError("Gate 1 verdict binding differs from the evaluation result")
        if self.status == "invalid_test":
            valid_shape = (
                self.terminal
                and not self.opens_gate_2
                and self.audit_receipt is None
                and self.statistics is None
                and self.verdict is not None
                and self.verdict.evidence_state is M11EvidenceState.INVALID_TEST
            )
        elif self.status == "falsified_by_optimistic_ceiling":
            valid_shape = (
                self.terminal
                and not self.opens_gate_2
                and self.audit_receipt is not None
                and self.statistics is not None
                and self.statistics.falsifies_optimistic_ceiling
                and self.verdict is not None
                and self.verdict.evidence_state is M11EvidenceState.FALSIFIED_BY_OPTIMISTIC_CEILING
                and self.audit_receipt.cell_ids == self.observed_cell_ids
            )
        else:
            valid_shape = (
                not self.terminal
                and self.opens_gate_2
                and self.audit_receipt is not None
                and self.statistics is not None
                and not self.statistics.falsifies_optimistic_ceiling
                and self.verdict is None
                and self.audit_receipt.cell_ids == self.observed_cell_ids
            )
        if not valid_shape:
            raise ValueError("Gate 1 evaluation result branch is internally inconsistent")
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
    cell_ids: tuple[str, ...],
    selection_ids: tuple[str, ...],
    tiny_audit_id: str,
) -> Gate1AuditReceipt:
    """Seal the successful prerequisite audits into one immutable receipt."""

    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-audit-receipt.v1",
        "cell_ids": list(cell_ids),
        "selection_ids": list(selection_ids),
        "tiny_audit_id": tiny_audit_id,
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
        cell_ids=cell_ids,
        selection_ids=selection_ids,
        tiny_audit_id=tiny_audit_id,
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
) -> dict[str, object]:
    return {
        "schema_version": "yieldforge.m11-gate1-evaluation-result.v1",
        "status": status,
        "contract_id": _CONTRACT_ID,
        "contract_content_sha256": _CONTRACT_SHA256,
        "population_id": _POPULATION_ID,
        "population_content_sha256": _POPULATION_SHA256,
        "source_manifest_id": _SOURCE_MANIFEST_ID,
        "source_manifest_content_sha256": _SOURCE_MANIFEST_SHA256,
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
    for field in ("config", "audit_receipt", "statistics", "verdict"):
        value = hashable[field]
        hashable[field] = None if value is None else value.model_dump(mode="json")
    return semantic_sha256(hashable)


def _build_gate1_valid_result(
    *,
    context: Gate1SourceContext,
    cell_ids: tuple[str, ...],
    audit_receipt: Gate1AuditReceipt,
    statistics: Gate1BootstrapSummary,
    repair_count: Literal[0, 1],
) -> Gate1EvaluationResult:
    """Build either valid Gate 1 terminal/survival branch from audited statistics."""

    _require_official_root_values(context)
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
        observed_cell_ids=cell_ids,
        config=_build_config(),
        audit_receipt=audit_receipt,
        statistics=statistics,
        verdict=verdict,
        repair_count=repair_count,
        terminal=terminal,
        opens_gate_2=not terminal,
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

    cell_tuple = tuple(cells)
    observed_ids = _observed_ids(cell_tuple)
    canonical_repair_count: Literal[0, 1] = repair_count if repair_count in (0, 1) else 1
    try:
        canonical_context = load_official_gate1_context(context.repository_root)
        if context != canonical_context:
            raise Gate1EvidenceError("caller context differs from official pinned sources")
        _require_official_root_values(canonical_context)
    except (AttributeError, OSError, TypeError, ValueError, ValidationError):
        try:
            canonical_context = load_official_gate1_context(context.repository_root)
        except (AttributeError, OSError, TypeError, ValueError, ValidationError):
            raise Gate1EvidenceError(
                "Gate 1 cannot emit a root-bound result without readable official sources"
            ) from None
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
        canonical_selections = tuple(
            select_gate1_baseline_policy(canonical_context, corpus_id)
            for corpus_id in _CORPUS_ORDER
        )
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

    selection_by_corpus = {item.corpus_id: item for item in canonical_selections}
    metrics: list[Gate1CellMetricPair] = []
    canonical_cells: list[Gate1StreamCell] = []
    try:
        for supplied, stream in zip(cell_tuple, streams, strict=True):
            rebuilt_supplied = Gate1StreamCell.model_validate(
                supplied.model_dump(mode="python", round_trip=True), strict=True
            )
            expected = build_gate1_stream_cell(
                canonical_context,
                stream,
                baseline_selection=selection_by_corpus[stream.corpus_id],
            )
            if rebuilt_supplied != expected:
                raise Gate1EvidenceError(
                    "Gate 1 cell differs from its reconstructed feasible/lower-bound evidence"
                )
            canonical_cells.append(rebuilt_supplied)
            metrics.append(_cell_metric_pair(rebuilt_supplied))
    except (AttributeError, TypeError, ValueError, ValidationError):
        return _invalid(
            canonical_context,
            observed_ids,
            reason_code="accounting_reconciliation_failure",
            repair_count=canonical_repair_count,
        )

    receipt = build_gate1_audit_receipt(
        cell_ids=tuple(item.cell_id for item in canonical_cells),
        selection_ids=tuple(item.selection_id for item in canonical_selections),
        tiny_audit_id=canonical_tiny_audit.audit_id,
    )
    statistics = bootstrap_gate1_statistics(tuple(metrics[:20]), tuple(metrics[20:]))
    return _build_gate1_valid_result(
        context=canonical_context,
        cell_ids=receipt.cell_ids,
        audit_receipt=receipt,
        statistics=statistics,
        repair_count=canonical_repair_count,
    )


__all__ = [
    "Gate1AuditReceipt",
    "Gate1EvaluationConfig",
    "Gate1EvaluationResult",
    "Gate1EvaluationStatus",
    "build_gate1_audit_receipt",
    "evaluate_gate1_confirmation",
]
