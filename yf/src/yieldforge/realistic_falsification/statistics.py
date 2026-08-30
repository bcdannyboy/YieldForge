"""Frozen Gate 1 statistics for the M11 optimistic-ceiling decision."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal, NamedTuple, Self

import numpy as np
from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from yieldforge.experiments.contracts import FrozenExperimentModel, semantic_sha256

GATE1_BOOTSTRAP_RESAMPLES = 10_000
GATE1_BOOTSTRAP_SEED = 0
GATE1_CONFIDENCE_LEVEL = 0.95
GATE1_QUANTILE_METHOD = "linear"
GATE1_SAVINGS_THRESHOLD_PERCENT = 1.5
GATE1_UNKNOWN_THRESHOLD_POINTS = 0.5
GATE1_WILSON_Z = 1.959963984540054
_DECIMAL_PRECISION = 50

Gate1StatisticsGroup = Literal[
    "lectra-m3-m4",
    "loco-2dics",
    "equal-corpus-pool",
]


@dataclass(frozen=True, slots=True)
class Gate1CellMetricPair:
    """One stream's unquantized Decimal S/U pair."""

    savings_percent: Decimal
    unknown_contribution_points: Decimal

    def __post_init__(self) -> None:
        if (
            type(self.savings_percent) is not Decimal
            or type(self.unknown_contribution_points) is not Decimal
            or not self.savings_percent.is_finite()
            or not self.unknown_contribution_points.is_finite()
        ):
            raise TypeError("Gate 1 metric pairs require finite Decimal values")


class Gate1GroupBootstrapSummary(FrozenExperimentModel):
    """Observed means, type-7 intervals, and adverse margin for one group."""

    group: Gate1StatisticsGroup
    stream_count: StrictInt = Field(gt=0)
    mean_savings_percent: StrictFloat
    savings_mean_ci_lower: StrictFloat
    savings_mean_ci_upper: StrictFloat
    mean_unknown_contribution_points: StrictFloat
    unknown_mean_ci_lower: StrictFloat
    unknown_mean_ci_upper: StrictFloat
    adverse_margin: StrictFloat

    @model_validator(mode="after")
    def require_finite_ordered_statistics(self) -> Self:
        numeric = (
            self.mean_savings_percent,
            self.savings_mean_ci_lower,
            self.savings_mean_ci_upper,
            self.mean_unknown_contribution_points,
            self.unknown_mean_ci_lower,
            self.unknown_mean_ci_upper,
            self.adverse_margin,
        )
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("Gate 1 group statistics must be finite")
        if not (
            self.savings_mean_ci_lower <= self.mean_savings_percent <= self.savings_mean_ci_upper
            and self.unknown_mean_ci_lower
            <= self.mean_unknown_contribution_points
            <= self.unknown_mean_ci_upper
        ):
            raise ValueError("Gate 1 group mean lies outside its bootstrap interval")
        expected_margin = min(
            self.mean_savings_percent - GATE1_SAVINGS_THRESHOLD_PERCENT,
            self.mean_unknown_contribution_points - GATE1_UNKNOWN_THRESHOLD_POINTS,
        )
        if self.adverse_margin != expected_margin:
            raise ValueError("Gate 1 group adverse margin does not reconcile")
        return self


class Gate1BootstrapSummary(FrozenExperimentModel):
    """Content-addressed frozen bootstrap output for the three decision groups."""

    schema_version: Literal["yieldforge.m11-gate1-bootstrap-summary.v1"] = (
        "yieldforge.m11-gate1-bootstrap-summary.v1"
    )
    summary_id: StrictStr = Field(pattern=r"^yfm11g1s-[0-9a-f]{24}$")
    content_sha256: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
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
    groups: tuple[Gate1GroupBootstrapSummary, ...] = Field(min_length=3, max_length=3)
    joint_upper_adverse_margin: StrictFloat
    falsifies_optimistic_ceiling: StrictBool

    @model_validator(mode="after")
    def require_frozen_decision_and_identity(self) -> Self:
        if tuple(item.group for item in self.groups) != (
            "lectra-m3-m4",
            "loco-2dics",
            "equal-corpus-pool",
        ):
            raise ValueError("Gate 1 bootstrap groups differ from the frozen order")
        if not math.isfinite(self.joint_upper_adverse_margin):
            raise ValueError("Gate 1 joint upper margin must be finite")
        if self.falsifies_optimistic_ceiling is not (self.joint_upper_adverse_margin < 0.0):
            raise ValueError("Gate 1 falsification flag differs from the strict boundary")
        digest = semantic_sha256(self, excluded_fields={"summary_id", "content_sha256"})
        if self.summary_id != f"yfm11g1s-{digest[:24]}" or self.content_sha256 != (
            f"sha256:{digest}"
        ):
            raise ValueError("Gate 1 bootstrap summary identity does not match semantic content")
        return self


class _Gate1BootstrapArrays(NamedTuple):
    lectra_group_means: np.ndarray
    loco_group_means: np.ndarray
    pool_group_means: np.ndarray
    lectra_adverse_margin: np.ndarray
    loco_adverse_margin: np.ndarray
    pool_adverse_margin: np.ndarray
    joint_adverse_margin: np.ndarray


def calculate_gate1_cell_metrics(
    *, baseline: Decimal, known_only: Decimal, lower_bound: Decimal
) -> Gate1CellMetricPair:
    """Compute S/U from exact Decimal inputs without quantizing either percentage."""

    values = (baseline, known_only, lower_bound)
    if any(type(value) is not Decimal for value in values):
        raise TypeError("Gate 1 B, K, and L inputs must be Decimal")
    if not all(value.is_finite() for value in values):
        raise ValueError("Gate 1 B, K, and L inputs must be finite")
    if baseline <= 0 or lower_bound > baseline or lower_bound > known_only:
        raise ValueError("Gate 1 requires B > 0 and L <= B/K")
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        savings = Decimal(100) * (baseline - lower_bound) / baseline
        unknown = Decimal(100) * (known_only - lower_bound) / baseline
    return Gate1CellMetricPair(savings, unknown)


def _decimal_values(values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    if not values:
        raise ValueError("Gate 1 corpus aggregation requires at least one stream")
    if any(type(value) is not Decimal or not value.is_finite() for value in values):
        raise TypeError("Gate 1 corpus aggregation requires finite Decimal values")
    return values


def equal_corpus_mean(
    lectra_values: tuple[Decimal, ...], loco_values: tuple[Decimal, ...]
) -> Decimal:
    """Give each corpus one half of the pooled weight regardless of row counts."""

    lectra = _decimal_values(tuple(lectra_values))
    loco = _decimal_values(tuple(loco_values))
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        lectra_mean = sum(lectra, start=Decimal(0)) / Decimal(len(lectra))
        loco_mean = sum(loco, start=Decimal(0)) / Decimal(len(loco))
        return (lectra_mean + loco_mean) / Decimal(2)


def linear_quantile(values, probability: float) -> float:
    """Return NumPy's frozen type-7 linear quantile for one finite vector."""

    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        raise TypeError("quantile probability must be numeric")
    if not math.isfinite(float(probability)) or not 0 <= float(probability) <= 1:
        raise ValueError("quantile probability must lie in [0, 1]")
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or array.size == 0 or not np.isfinite(array).all():
        raise ValueError("quantile input must be one nonempty finite vector")
    return float(np.quantile(array, float(probability), method=GATE1_QUANTILE_METHOD))


def draw_gate1_bootstrap_indices(lectra_size: int, loco_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Draw the complete Lectra matrix first, then LOCo, from PCG64(0)."""

    if (
        type(lectra_size) is not int
        or type(loco_size) is not int
        or lectra_size <= 0
        or loco_size <= 0
    ):
        raise ValueError("Gate 1 bootstrap corpus sizes must be positive strict integers")
    generator = np.random.Generator(np.random.PCG64(GATE1_BOOTSTRAP_SEED))
    lectra = generator.integers(
        0,
        lectra_size,
        size=(GATE1_BOOTSTRAP_RESAMPLES, lectra_size),
    )
    loco = generator.integers(
        0,
        loco_size,
        size=(GATE1_BOOTSTRAP_RESAMPLES, loco_size),
    )
    return lectra, loco


def _metric_matrix(values: tuple[Gate1CellMetricPair, ...]) -> np.ndarray:
    if not values or any(type(item) is not Gate1CellMetricPair for item in values):
        raise TypeError("Gate 1 bootstrap requires nonempty strict metric pairs")
    matrix = np.asarray(
        [[float(item.savings_percent), float(item.unknown_contribution_points)] for item in values],
        dtype=np.float64,
    )
    if not np.isfinite(matrix).all():
        raise ValueError("Gate 1 bootstrap metric matrix must be finite")
    return matrix


def _bootstrap_gate1_arrays(
    lectra_metrics: tuple[Gate1CellMetricPair, ...],
    loco_metrics: tuple[Gate1CellMetricPair, ...],
) -> _Gate1BootstrapArrays:
    """Return deterministic paired bootstrap arrays for contract tests and summaries."""

    lectra = _metric_matrix(tuple(lectra_metrics))
    loco = _metric_matrix(tuple(loco_metrics))
    lectra_indices, loco_indices = draw_gate1_bootstrap_indices(len(lectra), len(loco))
    lectra_means = lectra[lectra_indices].mean(axis=1)
    loco_means = loco[loco_indices].mean(axis=1)
    pool_means = (lectra_means + loco_means) / 2.0
    lectra_margin = np.minimum(
        lectra_means[:, 0] - GATE1_SAVINGS_THRESHOLD_PERCENT,
        lectra_means[:, 1] - GATE1_UNKNOWN_THRESHOLD_POINTS,
    )
    loco_margin = np.minimum(
        loco_means[:, 0] - GATE1_SAVINGS_THRESHOLD_PERCENT,
        loco_means[:, 1] - GATE1_UNKNOWN_THRESHOLD_POINTS,
    )
    pool_margin = np.minimum(
        pool_means[:, 0] - GATE1_SAVINGS_THRESHOLD_PERCENT,
        pool_means[:, 1] - GATE1_UNKNOWN_THRESHOLD_POINTS,
    )
    joint_margin = np.maximum.reduce((lectra_margin, loco_margin, pool_margin))
    return _Gate1BootstrapArrays(
        lectra_means,
        loco_means,
        pool_means,
        lectra_margin,
        loco_margin,
        pool_margin,
        joint_margin,
    )


def _group_summary(
    *,
    group: Gate1StatisticsGroup,
    stream_count: int,
    observed_savings: Decimal,
    observed_unknown: Decimal,
    bootstrap_means: np.ndarray,
) -> Gate1GroupBootstrapSummary:
    mean_savings = float(observed_savings)
    mean_unknown = float(observed_unknown)
    return Gate1GroupBootstrapSummary(
        group=group,
        stream_count=stream_count,
        mean_savings_percent=mean_savings,
        savings_mean_ci_lower=linear_quantile(bootstrap_means[:, 0], 0.025),
        savings_mean_ci_upper=linear_quantile(bootstrap_means[:, 0], 0.975),
        mean_unknown_contribution_points=mean_unknown,
        unknown_mean_ci_lower=linear_quantile(bootstrap_means[:, 1], 0.025),
        unknown_mean_ci_upper=linear_quantile(bootstrap_means[:, 1], 0.975),
        adverse_margin=min(
            mean_savings - GATE1_SAVINGS_THRESHOLD_PERCENT,
            mean_unknown - GATE1_UNKNOWN_THRESHOLD_POINTS,
        ),
    )


def optimistic_ceiling_falsified(joint_upper_adverse_margin: float) -> bool:
    """Apply the strict registered boundary; equality survives Gate 1."""

    if isinstance(joint_upper_adverse_margin, bool) or not isinstance(
        joint_upper_adverse_margin, (int, float)
    ):
        raise TypeError("joint upper adverse margin must be numeric")
    if not math.isfinite(float(joint_upper_adverse_margin)):
        raise ValueError("joint upper adverse margin must be finite")
    return float(joint_upper_adverse_margin) < 0.0


def bootstrap_gate1_statistics(
    lectra_metrics: tuple[Gate1CellMetricPair, ...],
    loco_metrics: tuple[Gate1CellMetricPair, ...],
) -> Gate1BootstrapSummary:
    """Compute the sole registered equal-corpus paired bootstrap."""

    lectra = tuple(lectra_metrics)
    loco = tuple(loco_metrics)
    arrays = _bootstrap_gate1_arrays(lectra, loco)
    lectra_s = tuple(item.savings_percent for item in lectra)
    lectra_u = tuple(item.unknown_contribution_points for item in lectra)
    loco_s = tuple(item.savings_percent for item in loco)
    loco_u = tuple(item.unknown_contribution_points for item in loco)
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        lectra_mean_s = sum(lectra_s, Decimal(0)) / Decimal(len(lectra_s))
        lectra_mean_u = sum(lectra_u, Decimal(0)) / Decimal(len(lectra_u))
        loco_mean_s = sum(loco_s, Decimal(0)) / Decimal(len(loco_s))
        loco_mean_u = sum(loco_u, Decimal(0)) / Decimal(len(loco_u))
    groups = (
        _group_summary(
            group="lectra-m3-m4",
            stream_count=len(lectra),
            observed_savings=lectra_mean_s,
            observed_unknown=lectra_mean_u,
            bootstrap_means=arrays.lectra_group_means,
        ),
        _group_summary(
            group="loco-2dics",
            stream_count=len(loco),
            observed_savings=loco_mean_s,
            observed_unknown=loco_mean_u,
            bootstrap_means=arrays.loco_group_means,
        ),
        _group_summary(
            group="equal-corpus-pool",
            stream_count=len(lectra) + len(loco),
            observed_savings=equal_corpus_mean(lectra_s, loco_s),
            observed_unknown=equal_corpus_mean(lectra_u, loco_u),
            bootstrap_means=arrays.pool_group_means,
        ),
    )
    joint_upper = linear_quantile(arrays.joint_adverse_margin, 0.975)
    semantic: dict[str, object] = {
        "schema_version": "yieldforge.m11-gate1-bootstrap-summary.v1",
        "bootstrap_generator": "numpy.Generator(PCG64(0))",
        "bootstrap_resamples": GATE1_BOOTSTRAP_RESAMPLES,
        "bootstrap_seed": GATE1_BOOTSTRAP_SEED,
        "quantile_method": "linear_type_7",
        "confidence_level": GATE1_CONFIDENCE_LEVEL,
        "resampling_unit": "complete_paired_stream_s_u_vector",
        "aggregation": "equal_stream_within_corpus_then_equal_corpus_pool",
        "groups": [item.model_dump(mode="json") for item in groups],
        "joint_upper_adverse_margin": joint_upper,
        "falsifies_optimistic_ceiling": optimistic_ceiling_falsified(joint_upper),
    }
    digest = semantic_sha256(semantic)
    return Gate1BootstrapSummary(
        summary_id=f"yfm11g1s-{digest[:24]}",
        content_sha256=f"sha256:{digest}",
        groups=groups,
        joint_upper_adverse_margin=joint_upper,
        falsifies_optimistic_ceiling=optimistic_ceiling_falsified(joint_upper),
    )


def wilson_interval_percent(successes: int, total: int) -> tuple[float, float]:
    """Return the diagnostic-only 95% Wilson interval in percentage units."""

    if type(successes) is not int or type(total) is not int:
        raise TypeError("Wilson counts must be strict integers")
    if total <= 0 or successes < 0 or successes > total:
        raise ValueError("Wilson counts require n > 0 and 0 <= k <= n")
    proportion = successes / total
    z_squared = GATE1_WILSON_Z * GATE1_WILSON_Z
    denominator = 1.0 + z_squared / total
    center = (proportion + z_squared / (2.0 * total)) / denominator
    half_width = (
        GATE1_WILSON_Z
        * math.sqrt(proportion * (1.0 - proportion) / total + z_squared / (4.0 * total * total))
        / denominator
    )
    return max(0.0, center - half_width) * 100.0, min(1.0, center + half_width) * 100.0


__all__ = [
    "GATE1_BOOTSTRAP_RESAMPLES",
    "GATE1_BOOTSTRAP_SEED",
    "GATE1_CONFIDENCE_LEVEL",
    "GATE1_QUANTILE_METHOD",
    "GATE1_SAVINGS_THRESHOLD_PERCENT",
    "GATE1_UNKNOWN_THRESHOLD_POINTS",
    "GATE1_WILSON_Z",
    "Gate1BootstrapSummary",
    "Gate1CellMetricPair",
    "Gate1GroupBootstrapSummary",
    "bootstrap_gate1_statistics",
    "calculate_gate1_cell_metrics",
    "draw_gate1_bootstrap_indices",
    "equal_corpus_mean",
    "linear_quantile",
    "optimistic_ceiling_falsified",
    "wilson_interval_percent",
]
